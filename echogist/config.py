"""T1 — config layer.

Two kinds of config, both DATA not code (CLAUDE.md):

* **Model config** (``config/models.toml``) — tiers (id/context/prices), the
  overflow-guard budget, and the configurable Whisper model-asset source.
  Read-only at runtime; the operator edits the TOML to update IDs or prices.
* **Settings** (``config/settings.json``) — the operator's choices changed from
  the Settings menu: summary language, output format, model tier, and the
  cost-confirm threshold. Written by the app, so it is JSON (stdlib-writable).

Every recoverable problem raises :class:`ConfigError` with a human-readable
message. Callers print it and return to the menu — never a crash (CLAUDE.md
"fail loud, return to menu").
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VALID_LANGUAGES: tuple[str, ...] = ("ru", "en")
VALID_FORMATS: tuple[str, ...] = ("pdf", "md")

_ENV_API_KEY = "ANTHROPIC_API_KEY"

_ENV_CONFIG_DIR = "ECHOGIST_CONFIG_DIR"
_MODELS_FILENAME = "models.toml"
_SETTINGS_FILENAME = "settings.json"
_STATE_FILENAME = "state.json"
_LAST_DIR_KEY = "last_input_dir"
_SECRETS_FILENAME = "secrets.toml"
_SECRETS_API_KEY = "anthropic_api_key"

# Fallback timecode granularity when models.toml omits [transcript] block_seconds.
# Kept in sync with echogist.transcribe._DEFAULT_BLOCK_SECONDS (the pure-stage default).
_DEFAULT_BLOCK_SECONDS = 60.0

# Defaults for the optional [chunk] table (map-reduce summarization, TD-5). A
# missing table falls back to these so an existing models.toml keeps loading.
# QualityBudget (tokens OR minutes) sits BELOW the tier's ContextBudget: a single
# pass over a long transcript degrades ("lost in the middle") well before the
# context fills, so chunking triggers early, on quality not just on overflow.
_DEFAULT_QUALITY_BUDGET_TOKENS = 40_000
_DEFAULT_QUALITY_BUDGET_SECONDS = 3_600.0  # 60 min
_DEFAULT_TARGET_CHUNK_TOKENS = 12_000  # hyperparameter — calibrate on a real lecture
_DEFAULT_OVERLAP_SECONDS = 90.0  # time-based boundary overlap so straddling ideas survive
# Phase-split target (TD-16 v2 direct synthesis). The transcript is cut into a few
# CONTIGUOUS, non-overlapping phases (not map chunks) for sequential synthesis. K is
# computed from this target — ceil(total_tokens / phase_target_tokens) — so it SCALES
# with length (a 3h lecture ≈ 3-4 phases, 6h ≈ 6-8); it is NOT a fixed cap. Sized ~2x
# the map target so the validated ~84k-token 3h lecture lands at ~4 phases.
_DEFAULT_PHASE_TARGET_TOKENS = 24_000

# Default reduce/map prompt scaffolding. Prompt text is DATA (shipped in
# models.toml), but these code-level fallbacks keep a minimal [summarize] table (or
# a test fixture) loading when the keys are absent. {language} is substituted at
# call time; {n}/{total}/{span} are filled per chunk by the map step.
_DEFAULT_MAP_NOTE_TEMPLATE = (
    "This is segment {n} of {total} ({span}) of a LONGER transcript. Extract EVERY "
    "distinct idea, decision, and action in THIS segment — omit nothing, do not "
    "compress, do not skip the middle. Use only timecodes that appear in this segment."
)
_DEFAULT_REDUCE_SYSTEM_PROMPT = (
    "You are merging the extracted points of several segments of one transcript into "
    "a single coherent summary, written entirely in {language}. You are given the "
    "already-extracted points; do NOT drop or compress them. Produce only an overall "
    "title, a faithful overview, and the single core idea — as many sentences as the "
    "material needs, no upper limit. Call emit_synthesis exactly once."
)
# Grouping step (TD-15 Phase 2): organizes an already-extracted flat list into
# headings WITHOUT rewriting any point. The model returns headings + the 1-based
# INDICES of the points under each — never the point text — so reconstruction is
# verbatim and a point can never be dropped or reworded. {language} is substituted
# at call time. Code-level fallback so a minimal [summarize] table (or a fixture)
# without the key still loads.
_DEFAULT_GROUPING_SYSTEM_PROMPT = (
    "You organize already-extracted points into a readable hierarchy, in {language}. "
    "You are given numbered lists (takeaways, themes, sections). For each list, group "
    "its items under a small set of clear, specific headings (~10-15 for takeaways, "
    "fewer for themes; group sections into time-ordered macro-sections). Return ONLY "
    "the headings and the 1-based INDICES of the items under each — never the item "
    "text. Every index must appear under exactly ONE heading: do not drop, duplicate, "
    "merge, or reword any item. Write headings in {language}. Call emit_grouping once."
)
# Synthesis step (TD-16 v2): synthesize ONE phase of the transcript into faithful,
# readable prose — the transcript is ground truth, read directly (one hop). {language}
# is the target; {interpretation} is the per-language label for the inline marker that
# flags any bridge beyond what the author literally said. Code-level fallback so a
# minimal [summarize] table (or a fixture) without the key still loads.
_DEFAULT_SYNTHESIS_SYSTEM_PROMPT = (
    "You synthesize ONE phase of a longer timestamped transcript into faithful, "
    "readable prose in {language}. Ground every sentence in the transcript; do not "
    "invent, soften, invert, or merge distinct points, and keep the author's caveats. "
    "Copy only [HH:MM:SS] timecodes that actually appear as anchors for the passage and "
    "for each decision/action; never invent or round one. Mark any bridge beyond what "
    "the author says inline with [{interpretation}]:. Use a PRIOR CONTEXT section, if "
    "given, for continuity only — do not restate it. Call emit_phase exactly once."
)
# Reconcile step (TD-16 v2): the document-level header derived ONLY from the already-
# written phase passages (it never re-reads the transcript — that would be a second
# lossy hop). Emits title + core_idea + main_themes and flags cross-phase contradiction.
_DEFAULT_RECONCILE_SYSTEM_PROMPT = (
    "You are given the already-written phase passages of one transcript, in order. In "
    "{language}, produce only a short specific title, the single core_idea (no upper "
    "limit), and 5-8 main_themes as short noun phrases — based ONLY on the passages "
    "given, adding no new facts. If two phases state contradictory things about the same "
    "point, note the contradiction in core_idea. Call emit_reconcile exactly once."
)


class ConfigError(Exception):
    """A recoverable, human-readable config problem. Print it, return to menu."""


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelTier:
    """A summarization tier: which model to call and what it costs."""

    name: str
    model_id: str
    context_window: int
    price_in_per_mtok: float
    price_out_per_mtok: float
    # Sampling temperature for the summarize call, or None to OMIT the parameter.
    # The current Claude 4.x models deprecate `temperature` and reject the request
    # (400) if it is sent, so a tier omits it by default. An older model that still
    # honors sampling can set `temperature = 0` to pin the title (the filename stem)
    # for stable re-run overwrites. Config is data: this lives in models.toml.
    temperature: float | None = None


@dataclass(frozen=True)
class GuardConfig:
    """Inputs to the local overflow guard + cost estimate (plan §3, §4)."""

    safe_budget_fraction: float
    output_tokens_estimate: int

    def safe_budget(self, tier: ModelTier) -> int:
        """Max single-pass input tokens allowed for ``tier`` before the guard stops."""
        return int(tier.context_window * self.safe_budget_fraction)


@dataclass(frozen=True)
class SummarizeConfig:
    """The one network stage's tunables (plan §3, T6). DATA, not code.

    ``system_prompt`` is the prompt TEXT — editable data per "Config is data"
    (CLAUDE.md). It carries two substitution tokens replaced at call time:
    ``{language}`` (the target summary language) and ``{unassigned}`` (the fixed
    no-owner label for that language). The tool-use SCHEMA (the title+sections
    field contract the parser depends on) lives in code, in
    :mod:`echogist.summarize`, so the prompt and the schema cannot drift apart.

    ``max_output_tokens`` is the API's hard ``max_tokens`` cap — deliberately
    SEPARATE from, and larger than, ``GuardConfig.output_tokens_estimate`` (the
    cost projection). The summary contract carries no upper limit on element counts
    (all concepts, full overview, per-section bullets), so a flush cap would
    truncate a dense RU summary into invalid tool-use JSON and waste the paid call;
    the cap carries real headroom for Cyrillic tokenization.
    """

    system_prompt: str
    max_output_tokens: int
    reduce_system_prompt: str = _DEFAULT_REDUCE_SYSTEM_PROMPT
    map_note_template: str = _DEFAULT_MAP_NOTE_TEMPLATE
    grouping_system_prompt: str = _DEFAULT_GROUPING_SYSTEM_PROMPT
    # TD-16 v2 direct synthesis: the per-phase synthesis prompt (carries {language} and
    # the {interpretation} marker label) and the document-header reconcile prompt. Both
    # DATA, defaulted so a minimal table keeps loading; the new tool SCHEMAs stay in code.
    synthesis_system_prompt: str = _DEFAULT_SYNTHESIS_SYSTEM_PROMPT
    reconcile_system_prompt: str = _DEFAULT_RECONCILE_SYSTEM_PROMPT


@dataclass(frozen=True)
class ChunkConfig:
    """Map-reduce chunking knobs (DATA, not code) — TD-5.

    Chunking triggers on the **QualityBudget**: a single pass is allowed only while
    the transcript stays under BOTH ``quality_budget_tokens`` and
    ``quality_budget_seconds``; crossing either one (long OR dense) flips to
    map-reduce. This sits below the tier's ContextBudget (``GuardConfig.safe_budget``)
    on purpose — a single pass loses fidelity in the middle of a long context well
    before that context is full.

    ``target_chunk_tokens`` is the per-chunk size target (a calibratable
    hyperparameter, not a hard limit); ``overlap_seconds`` is the time-based overlap
    carried between adjacent chunks so an idea straddling a cut is not lost (the
    duplicate it creates is removed by the reduce step's conservative dedup).
    """

    quality_budget_tokens: int = _DEFAULT_QUALITY_BUDGET_TOKENS
    quality_budget_seconds: float = _DEFAULT_QUALITY_BUDGET_SECONDS
    target_chunk_tokens: int = _DEFAULT_TARGET_CHUNK_TOKENS
    overlap_seconds: float = _DEFAULT_OVERLAP_SECONDS
    # TD-16 v2 direct synthesis: target tokens per CONTIGUOUS, non-overlapping phase.
    # K = ceil(total_tokens / phase_target_tokens), computed (scales with length), not a
    # cap. Larger than target_chunk_tokens — phases are coarser than map chunks.
    phase_target_tokens: int = _DEFAULT_PHASE_TARGET_TOKENS


@dataclass(frozen=True)
class TranscriptConfig:
    """Transcript rendering tunables (DATA, not code).

    ``block_seconds`` is the timecode granularity: Whisper segments are coalesced
    into ~this-many-second blocks so the saved transcript stays readable and carries
    a tractable number of real, citeable timecodes (see
    :func:`echogist.transcribe.render_transcript`). Optional in ``models.toml`` — a
    missing ``[transcript]`` table falls back to the shipped default.
    """

    block_seconds: float


@dataclass(frozen=True)
class ModelAsset:
    """Whisper model distribution (TD-1). Fetched from Hugging Face by repo id; a
    pre-placed ``local_dir`` is the offline escape hatch."""

    name: str
    hf_repo: str
    local_dir: str


@dataclass(frozen=True)
class ModelConfig:
    """The whole of ``models.toml``, parsed and validated."""

    tiers: dict[str, ModelTier]
    guard: GuardConfig
    summarize: SummarizeConfig
    asset: ModelAsset
    transcript: TranscriptConfig
    # Defaulted (it is the last field): the loader always sets it from the optional
    # [chunk] table; the default keeps minimal hand-built fixtures constructing.
    chunk: ChunkConfig = ChunkConfig()

    def tier(self, name: str) -> ModelTier:
        """Resolve a tier by name. Unknown/deprecated -> guided ConfigError (F5)."""
        try:
            return self.tiers[name]
        except KeyError:
            available = ", ".join(sorted(self.tiers)) or "(none configured)"
            raise ConfigError(
                f"Model tier '{name}' is not in {_MODELS_FILENAME}. "
                f"Open Settings and pick one of: {available}."
            ) from None


@dataclass
class Settings:
    """Operator choices, persisted to ``settings.json``."""

    summary_language: str
    output_format: str
    model_tier: str
    confirm_threshold_usd: float


def default_settings() -> Settings:
    """The seeded defaults used when no ``settings.json`` exists yet.

    PDF + RU by default (the operator's primary language); ``economy`` is the
    cheapest tier (Haiku) — the operator's chosen default for everyday use, with
    ``balanced``/``flagship`` available via Settings; the threshold is high enough
    that typical cheap material runs on a bare Enter and only unusually
    long/costly inputs prompt for y/n.
    """
    return Settings(
        summary_language="ru",
        output_format="pdf",
        model_tier="economy",
        confirm_threshold_usd=0.50,
    )


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def config_dir() -> Path:
    """Where config lives. ``$ECHOGIST_CONFIG_DIR`` wins (tests/Windows), else
    the repo's ``config/`` next to this package."""
    override = os.environ.get(_ENV_CONFIG_DIR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config"


def _api_key_from_secrets(path: Path | None = None) -> str | None:
    """The Anthropic key from ``config/secrets.toml`` (key ``anthropic_api_key``), or
    None. Fail-soft like the other local state: a missing, unreadable, or malformed
    file — or a missing/blank/non-string value — degrades to None (→ F3 at the menu),
    never a crash. The file is gitignored; see ``config/secrets.toml.example``."""
    secrets = path or (config_dir() / _SECRETS_FILENAME)
    try:
        data = tomllib.loads(secrets.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return None
    raw = data.get(_SECRETS_API_KEY)
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def get_api_key(secrets_path: Path | None = None) -> str | None:
    """The Anthropic key: ``ANTHROPIC_API_KEY`` env first, then ``config/secrets.toml``.

    The environment ALWAYS wins — an operator (or CI) can override the file ad hoc,
    and a stray secrets file can never shadow an explicit env key. Validated only
    before a summarization action (plan §5.7); MP3-only extraction and the local GUARD
    run key-free. Never read from code; never logged; the file is gitignored.
    """
    env = os.environ.get(_ENV_API_KEY, "").strip()
    if env:
        return env
    return _api_key_from_secrets(secrets_path)


# --------------------------------------------------------------------------- #
# Model config
# --------------------------------------------------------------------------- #
def _require(table: dict[str, Any], key: str, where: str) -> Any:
    if key not in table:
        raise ConfigError(f"{_MODELS_FILENAME}: missing '{key}' in {where}.")
    return table[key]


def _as_positive_number(value: Any, key: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in {where} must be a number.")
    if value <= 0:
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in {where} must be > 0.")
    return float(value)


def _optional_nonempty_str(table: dict[str, Any], key: str, default: str) -> str:
    """Return ``table[key]`` if present and a non-empty string, else ``default``.

    Used for optional prompt-scaffolding keys: absent -> code default; present but
    blank/non-string -> loud ConfigError (a hand-edited typo is caught, not ignored).
    """
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in [summarize] must be a non-empty string.")
    return value


def _optional_positive(table: dict[str, Any], key: str, where: str, default: float) -> float:
    """Return ``table[key]`` validated as a positive number, else ``default``."""
    if key not in table:
        return default
    return _as_positive_number(table[key], key, where)


def _optional_nonnegative(table: dict[str, Any], key: str, where: str, default: float) -> float:
    """Return ``table[key]`` validated as a number >= 0, else ``default`` (0 allowed)."""
    if key not in table:
        return default
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in {where} must be a number.")
    if value < 0:
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in {where} must be >= 0.")
    return float(value)


def _optional_temperature(table: dict[str, Any], where: str) -> float | None:
    """Return ``table['temperature']`` validated as a number >= 0, or None if absent.

    Optional and omittable on purpose: the current Claude 4.x models deprecate the
    temperature parameter and reject the request (400) if it is sent, so a tier omits
    it by default. Present-but-malformed is a loud ConfigError (a hand-edited typo is
    caught, not silently ignored).
    """
    if "temperature" not in table:
        return None
    value = table["temperature"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{_MODELS_FILENAME}: 'temperature' in {where} must be a number.")
    if value < 0:
        raise ConfigError(f"{_MODELS_FILENAME}: 'temperature' in {where} must be >= 0.")
    return float(value)


def _parse_tier(name: str, table: Any) -> ModelTier:
    where = f"[tiers.{name}]"
    if not isinstance(table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: {where} must be a table.")
    model_id = _require(table, "model_id", where)
    if not isinstance(model_id, str) or not model_id.strip():
        raise ConfigError(f"{_MODELS_FILENAME}: 'model_id' in {where} must be a non-empty string.")
    context_window = _as_positive_number(
        _require(table, "context_window", where), "context_window", where
    )
    return ModelTier(
        name=name,
        model_id=model_id,
        context_window=int(context_window),
        price_in_per_mtok=_as_positive_number(
            _require(table, "price_in_per_mtok", where), "price_in_per_mtok", where
        ),
        price_out_per_mtok=_as_positive_number(
            _require(table, "price_out_per_mtok", where), "price_out_per_mtok", where
        ),
        temperature=_optional_temperature(table, where),
    )


def load_model_config(path: Path | None = None) -> ModelConfig:
    """Parse + validate ``models.toml``. Any malformed field -> ConfigError."""
    path = path or (config_dir() / _MODELS_FILENAME)
    if not path.is_file():
        raise ConfigError(
            f"Model config not found at {path}. EchoGist ships a default "
            f"{_MODELS_FILENAME}; restore it or set ${_ENV_CONFIG_DIR}."
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    tiers_table = raw.get("tiers")
    if not isinstance(tiers_table, dict) or not tiers_table:
        raise ConfigError(f"{_MODELS_FILENAME}: needs at least one [tiers.<name>] table.")
    tiers = {name: _parse_tier(name, tbl) for name, tbl in tiers_table.items()}

    guard_table = raw.get("guard")
    if not isinstance(guard_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: missing [guard] table.")
    fraction = _as_positive_number(
        _require(guard_table, "safe_budget_fraction", "[guard]"), "safe_budget_fraction", "[guard]"
    )
    if fraction > 1:
        raise ConfigError(f"{_MODELS_FILENAME}: 'safe_budget_fraction' must be <= 1.")
    guard = GuardConfig(
        safe_budget_fraction=fraction,
        output_tokens_estimate=int(
            _as_positive_number(
                _require(guard_table, "output_tokens_estimate", "[guard]"),
                "output_tokens_estimate",
                "[guard]",
            )
        ),
    )

    summarize_table = raw.get("summarize")
    if not isinstance(summarize_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: missing [summarize] table.")
    system_prompt = _require(summarize_table, "system_prompt", "[summarize]")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ConfigError(
            f"{_MODELS_FILENAME}: 'system_prompt' in [summarize] must be a non-empty string."
        )
    summarize = SummarizeConfig(
        system_prompt=system_prompt,
        max_output_tokens=int(
            _as_positive_number(
                _require(summarize_table, "max_output_tokens", "[summarize]"),
                "max_output_tokens",
                "[summarize]",
            )
        ),
        # Optional, defaulted: map-reduce prompt scaffolding. A minimal [summarize]
        # table (or fixture) without these keeps the code-level fallbacks.
        reduce_system_prompt=_optional_nonempty_str(
            summarize_table, "reduce_system_prompt", _DEFAULT_REDUCE_SYSTEM_PROMPT
        ),
        map_note_template=_optional_nonempty_str(
            summarize_table, "map_note_template", _DEFAULT_MAP_NOTE_TEMPLATE
        ),
        grouping_system_prompt=_optional_nonempty_str(
            summarize_table, "grouping_system_prompt", _DEFAULT_GROUPING_SYSTEM_PROMPT
        ),
        synthesis_system_prompt=_optional_nonempty_str(
            summarize_table, "synthesis_system_prompt", _DEFAULT_SYNTHESIS_SYSTEM_PROMPT
        ),
        reconcile_system_prompt=_optional_nonempty_str(
            summarize_table, "reconcile_system_prompt", _DEFAULT_RECONCILE_SYSTEM_PROMPT
        ),
    )

    asset_table = raw.get("model_asset")
    if not isinstance(asset_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: missing [model_asset] table.")
    asset = ModelAsset(
        name=str(_require(asset_table, "name", "[model_asset]")),
        hf_repo=str(_require(asset_table, "hf_repo", "[model_asset]")),
        local_dir=str(_require(asset_table, "local_dir", "[model_asset]")),
    )

    # [transcript] is OPTIONAL: a missing table (or missing key) falls back to the
    # default so an existing models.toml keeps loading. A present-but-invalid value
    # still fails loud (F5).
    transcript_table = raw.get("transcript")
    if transcript_table is None:
        transcript = TranscriptConfig(block_seconds=_DEFAULT_BLOCK_SECONDS)
    elif not isinstance(transcript_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: [transcript] must be a table.")
    elif "block_seconds" not in transcript_table:
        transcript = TranscriptConfig(block_seconds=_DEFAULT_BLOCK_SECONDS)
    else:
        transcript = TranscriptConfig(
            block_seconds=_as_positive_number(
                transcript_table["block_seconds"], "block_seconds", "[transcript]"
            )
        )

    # [chunk] is OPTIONAL like [transcript]: a missing table (or any missing key)
    # falls back to the shipped defaults; a present-but-invalid value fails loud.
    chunk_table = raw.get("chunk")
    if chunk_table is None:
        chunk = ChunkConfig()
    elif not isinstance(chunk_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: [chunk] must be a table.")
    else:
        chunk = ChunkConfig(
            quality_budget_tokens=int(
                _optional_positive(
                    chunk_table, "quality_budget_tokens", "[chunk]", _DEFAULT_QUALITY_BUDGET_TOKENS
                )
            ),
            quality_budget_seconds=_optional_positive(
                chunk_table, "quality_budget_seconds", "[chunk]", _DEFAULT_QUALITY_BUDGET_SECONDS
            ),
            target_chunk_tokens=int(
                _optional_positive(
                    chunk_table, "target_chunk_tokens", "[chunk]", _DEFAULT_TARGET_CHUNK_TOKENS
                )
            ),
            # overlap may legitimately be 0 (no overlap), so it is not "positive-only".
            overlap_seconds=_optional_nonnegative(
                chunk_table, "overlap_seconds", "[chunk]", _DEFAULT_OVERLAP_SECONDS
            ),
            phase_target_tokens=int(
                _optional_positive(
                    chunk_table, "phase_target_tokens", "[chunk]", _DEFAULT_PHASE_TARGET_TOKENS
                )
            ),
        )

    return ModelConfig(
        tiers=tiers,
        guard=guard,
        summarize=summarize,
        asset=asset,
        transcript=transcript,
        chunk=chunk,
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _validate_settings(data: dict[str, Any], source: str) -> Settings:
    try:
        language = str(data["summary_language"])
        output_format = str(data["output_format"])
        model_tier = str(data["model_tier"])
        threshold = data["confirm_threshold_usd"]
    except KeyError as exc:
        raise ConfigError(
            f"{source}: missing setting {exc}. Delete it to restore defaults."
        ) from None

    if language not in VALID_LANGUAGES:
        raise ConfigError(
            f"{source}: summary_language '{language}' invalid; "
            f"use one of {', '.join(VALID_LANGUAGES)}."
        )
    if output_format not in VALID_FORMATS:
        raise ConfigError(
            f"{source}: output_format '{output_format}' invalid; "
            f"use one of {', '.join(VALID_FORMATS)}."
        )
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or threshold < 0:
        raise ConfigError(f"{source}: confirm_threshold_usd must be a number >= 0.")

    return Settings(
        summary_language=language,
        output_format=output_format,
        model_tier=model_tier,
        confirm_threshold_usd=float(threshold),
    )


def load_settings(path: Path | None = None) -> Settings:
    """Load ``settings.json``; return seeded defaults if it does not exist yet.

    A present-but-corrupt file raises ConfigError (loud), rather than silently
    falling back — so a typo in a hand-edited file is caught, not ignored.
    """
    path = path or (config_dir() / _SETTINGS_FILENAME)
    if not path.is_file():
        return default_settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}. Delete it to restore defaults.") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a JSON object. Delete it to restore defaults.")
    return _validate_settings(data, str(path))


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Persist settings as pretty JSON, creating the config dir if needed."""
    path = path or (config_dir() / _SETTINGS_FILENAME)
    # Validate before writing so we never persist an invalid state.
    _validate_settings(asdict(settings), "settings")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Picker state (TD-10) — the last-used input directory.
#
# Accumulated UX state, NOT an operator choice, so it lives in its own
# ``state.json`` beside ``settings.json`` (and is gitignored) rather than in
# Settings. Unlike settings, state is fail-SOFT: a missing or corrupt file
# never raises — it just means "no remembered directory". The file picker is a
# convenience; a broken state file must not break the menu.
#
#   pick a file ──► save_last_dir(parent)         (best-effort; OSError swallowed)
#   open picker ──► resolve_initial_dir(load_last_dir())
#                       last_input_dir (if dir) ─► ~/Downloads (if dir) ─► ~ (home)
# --------------------------------------------------------------------------- #
def _state_path(path: Path | None = None) -> Path:
    return path or (config_dir() / _STATE_FILENAME)


def _dir_exists(path: Path) -> bool:
    """``is_dir`` guarded against OSError (a dead UNC / network path can raise
    rather than return False) so the ladder always falls through cleanly."""
    try:
        return path.is_dir()
    except OSError:
        return False


def load_last_dir(path: Path | None = None) -> Path | None:
    """The operator's last-used input directory, or None.

    Fail-soft by design (contrast :func:`load_settings`): a missing, unreadable,
    or corrupt ``state.json`` returns None so the picker just opens at its
    default. State is a UX convenience, not config — a bad file never raises.
    """
    state = _state_path(path)
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get(_LAST_DIR_KEY)
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw)


def save_last_dir(directory: Path, path: Path | None = None) -> None:
    """Remember the last-used input directory (best-effort).

    Swallows OSError: a state-write failure (e.g. a read-only config dir) must
    never mask an already-completed transcribe+summarize run, since ``OSError``
    is one of the menu's recoverable errors.
    """
    state = _state_path(path)
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({_LAST_DIR_KEY: str(directory)}, indent=2, ensure_ascii=False) + "\n"
        state.write_text(payload, encoding="utf-8")
    except OSError:
        pass


def resolve_initial_dir(last: Path | None) -> Path:
    """Where the file picker should open: the last-used dir if it still exists,
    else ``~/Downloads`` if it exists, else home. Never cwd. Resolved at call
    time so a directory that vanished since last run falls through cleanly."""
    for candidate in (last, Path.home() / "Downloads"):
        if candidate is not None and _dir_exists(candidate):
            return candidate
    return Path.home()
