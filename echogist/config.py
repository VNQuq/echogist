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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VALID_LANGUAGES: tuple[str, ...] = ("ru", "en")
VALID_FORMATS: tuple[str, ...] = ("pdf", "md")

# Batch MP3 concurrency. The seeded value is capped at the machine's core count by
# ``default_batch_workers``; the ceiling is a guard on a hand-edited settings.json, since
# past a handful of concurrent ffmpegs the disk, not the CPU, is what the batch waits on.
_DEFAULT_BATCH_WORKERS = 4
MAX_BATCH_WORKERS = 16

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
# Phase-split target (TD-16 v2 direct synthesis). The transcript is cut into a few
# CONTIGUOUS, non-overlapping phases for sequential synthesis. K is computed from this
# target — ceil(total_tokens / phase_target_tokens) — so it SCALES with length (a 3h
# lecture ≈ 3-4 phases, 6h ≈ 6-8); it is NOT a fixed cap. Sized so the validated
# ~84k-token 3h lecture lands at ~4 phases.
_DEFAULT_PHASE_TARGET_TOKENS = 12_000
# Scan-time speech-rate constants (see ScanConfig). Seeded HIGH for Russian, per
# CLAUDE.md's estimate-Cyrillic-high rule: the scan quote must never undershoot.
# TD-26 transcript hygiene. Measured on the operator's real 3h22m RU lecture (198 blocks):
# genuine speech blocks never fell below a 0.56 unique-word ratio (median 0.71) and never
# below 15 words (median 133), while every Whisper boilerplate-loop block sat at 0.07-0.50.
# The floor is set just under the observed speech minimum so the two classes stay separated.
_DEFAULT_MAX_LOOP_COVERAGE = 0.50
_DEFAULT_MIN_BLOCK_WORDS = 15
# Scan-time speech-rate constants (see ScanConfig). MEASURED 2026-09-04 against the
# operator's seven-lecture RU course (23h43m): a real transcript runs ~122 wpm at ~6.2
# chars/word. Seeded ~10% above that, per CLAUDE.md's estimate-Cyrillic-high rule — the
# scan quote must stay above the bill, and the resulting margin is now a KNOWN ~1.4x
# rather than the unmeasured 150 x 7 (TD-23).
_DEFAULT_WORDS_PER_MINUTE = 135.0
_DEFAULT_CHARS_PER_WORD = 6.5
# The reconcile call always writes a title, the essence block (core_idea ~250-350 words,
# main_skill ~150-200, 3 test questions with 2-4 sentence answers), 5-8 themes and one
# heading per phase — roughly 700 Russian words even for the shortest material, and RU
# runs ~3.5 tokens/word at the guard's rate. 2,500 is that floor, rounded down so it stays
# a floor and not a projection. It is the only FIXED per-call cost in the cost model.
_DEFAULT_RECONCILE_OUTPUT_FLOOR_TOKENS = 2_500
# Output-per-input token ratio for a tier that does not declare one (see ModelTier).
# Set to the highest MEASURED tier ratio + margin, so an unknown model is projected
# as if it were the most verbose one we have numbers for.
_DEFAULT_OUTPUT_PER_INPUT_RATIO = 0.40

# Synthesis step (TD-16 v2): synthesize ONE phase of the transcript into faithful,
# readable prose — the transcript is ground truth, read directly (one hop). {language}
# is the target; {interpretation} is the per-language label for the inline marker that
# flags any bridge beyond what the author literally said. Code-level fallback so a
# minimal [summarize] table (or a fixture) without the key still loads.
_DEFAULT_SYNTHESIS_SYSTEM_PROMPT = (
    "You synthesize ONE phase of a longer timestamped transcript into faithful, "
    "readable prose in {language}. Ground every sentence in the transcript; do not "
    "invent, soften, invert, or merge distinct points, and keep the author's caveats. "
    "Cover the ENTIRE span of THIS phase, proportional to its content. Weave [HH:MM:SS] "
    "timecodes that actually appear INLINE into the prose at the points they support (a "
    "handful, not a wall), and copy them to the anchors field and each decision/action; "
    "never invent or round one. Mark any bridge beyond what the author says inline with "
    "[{interpretation}]:. Use a PRIOR CONTEXT section, if given, for continuity only — do "
    "not restate it, but never skip points THIS phase makes. Call emit_phase exactly once."
)
# Reconcile step (TD-16 v2): the document-level header + the essence block, derived ONLY
# from the already-written phase passages (it never re-reads the transcript — that would be
# a second lossy hop). Emits title + the essence block (core_idea / main_skill /
# test_questions) + main_themes, and flags cross-phase contradiction.
_DEFAULT_RECONCILE_SYSTEM_PROMPT = (
    "You are given the already-written phase passages of one transcript, in order. In "
    "{language}, produce a short specific title; the essence block — core_idea (the "
    "central claim with its reasoning and caveats, ~250-350 words), main_skill (the one "
    "thing the material teaches the reader to DO, ~150-200 words, empty if it teaches "
    "none), and EXACTLY 3 test_questions that only someone who followed the content could "
    "answer, each with a 2-4 sentence reference answer that stands on its own (it is "
    "printed far from its question); 5-8 main_themes as short noun phrases; and "
    "phase_headings — the phase headings rewritten into one coherent outline, exactly one "
    "per phase and in the SAME order — based ONLY on the passages given, adding no new "
    "facts. If two phases state contradictory things about the same point, note the "
    "contradiction in core_idea. Call emit_reconcile exactly once."
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
    # True when this tier's prices have not been verified since the model's last
    # generation bump (scripts/check-models.py sets it on a display_name change; a
    # human clears it after checking the pricing page). Optional, defaults False so
    # an older models.toml and direct ``ModelTier(...)`` callers stay valid. Purely
    # advisory: the summarize flow prints a one-time notice when it is True and never
    # blocks or moves the cost gate on it.
    prices_unverified: bool = False
    # Projected OUTPUT tokens per INPUT token for this tier's model, under the v2
    # synthesis prompt (TD-24). Per-tier because it is a property of the model's
    # verbosity: Haiku writes ~1.7x the prose Sonnet does over the same material.
    # Measured from a finished run's own audited totals — output_tokens / input_tokens
    # off the "Actual cost" line — plus a small margin, so the quote stays above the
    # bill. Optional so a hand-added tier still loads; the default is biased high.
    output_per_input_ratio: float = _DEFAULT_OUTPUT_PER_INPUT_RATIO


@dataclass(frozen=True)
class GuardConfig:
    """Inputs to the local overflow guard (plan §4).

    The cost projection's output side no longer lives here: it is
    ``ModelTier.output_per_input_ratio``, because it differs per model and scales
    with each call's input rather than being one flat number for every call (TD-24).
    """

    safe_budget_fraction: float

    def safe_budget(self, tier: ModelTier) -> int:
        """Max single-pass input tokens allowed for ``tier`` before the guard stops."""
        return int(tier.context_window * self.safe_budget_fraction)


@dataclass(frozen=True)
class SummarizeConfig:
    """The one network stage's tunables (plan §3, T6 / TD-16 v2). DATA, not code.

    ``synthesis_system_prompt`` / ``reconcile_system_prompt`` are the prompt TEXT —
    editable data per "Config is data" (CLAUDE.md). The synthesis prompt carries two
    substitution tokens replaced at call time: ``{language}`` (the target summary
    language) and ``{interpretation}`` (the per-language inline marker label). The
    tool-use SCHEMAs (the emit_phase / emit_reconcile field contracts the parser depends
    on) live in code, in :mod:`echogist.summarize`, so prompt and schema cannot drift.

    ``max_output_tokens`` is the API's hard ``max_tokens`` cap. It is also the ceiling
    the cost projection clamps a call's output to (a call cannot emit more than the cap),
    but it is NOT the projection itself — see ``ModelTier.output_per_input_ratio``.
    A phase's prose carries no upper limit, so a flush cap would truncate a dense RU
    phase into invalid tool-use JSON and waste the paid call; the cap carries real
    headroom for Cyrillic tokenization.
    """

    max_output_tokens: int
    #: Smallest reply the reconcile call can produce, in tokens — the cost model's only
    #: fixed per-call cost (see :func:`echogist.cost.estimate_cost_synthesis`). Derived
    #: from the reconcile prompt's own word budget, so it moves when that prompt does.
    reconcile_output_floor_tokens: int = _DEFAULT_RECONCILE_OUTPUT_FLOOR_TOKENS
    synthesis_system_prompt: str = _DEFAULT_SYNTHESIS_SYSTEM_PROMPT
    reconcile_system_prompt: str = _DEFAULT_RECONCILE_SYSTEM_PROMPT


@dataclass(frozen=True)
class ChunkConfig:
    """Phase-split knob (DATA, not code) — TD-16 v2 direct synthesis.

    ``phase_target_tokens`` is the target tokens per CONTIGUOUS, non-overlapping phase.
    K = ceil(total_tokens / phase_target_tokens), computed (scales with length), not a
    cap. Sized so a phase's faithful prose fits ``[summarize].max_output_tokens`` with
    room to spare — see the rationale in ``models.toml``.

    The other two are TD-26 transcript hygiene, applied to the synthesis INPUT only
    (:func:`echogist.chunk.drop_degenerate_blocks`); the saved transcript on disk stays
    verbatim. ``max_loop_coverage`` is the share of a block its most repeated 3-gram must
    blanket for the block to be a repetition loop rather than speech; ``min_block_words``
    is the length under which a block is too short to carry a minute of talk, used only
    for blocks TOUCHING such a loop.
    """

    phase_target_tokens: int = _DEFAULT_PHASE_TARGET_TOKENS
    max_loop_coverage: float = _DEFAULT_MAX_LOOP_COVERAGE
    min_block_words: int = _DEFAULT_MIN_BLOCK_WORDS


@dataclass(frozen=True)
class ScanConfig:
    """Duration -> transcript-size constants for the scanner's cost projection (DATA).

    The scanner prices a folder before anything is transcribed, so it has no text to
    count: it turns each file's DURATION into a character count via these two numbers,
    then into tokens. Both are seeded ~10% above MEASURED Russian lecture speech (122 wpm,
    6.2 chars/word across the operator's seven-lecture course), because the projection is
    biased high on purpose — an under-quote is the one failure mode that costs the
    operator money they did not agree to.

    Recalibration stays a MANUAL step: scan a folder that already holds a transcript,
    compare the projected character count against the real one, and edit the two numbers
    in ``models.toml``. No code change is needed to do it.
    """

    words_per_minute: float = _DEFAULT_WORDS_PER_MINUTE
    chars_per_word: float = _DEFAULT_CHARS_PER_WORD


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
    scan: ScanConfig = ScanConfig()

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
    # When True (default), a summary whose estimate is at or below ``confirm_threshold_usd``
    # runs immediately — the shown estimate is the acknowledgment. When False, that cheap
    # path first shows a non-decision "press Enter" acknowledge beat so the operator can
    # Ctrl-C out (TD-9). Above the threshold an explicit y/N confirm always applies,
    # regardless of this flag. Has a default so an older settings.json (written before the
    # field existed) still loads, and direct ``Settings(...)`` callers stay valid.
    auto_accept_under_threshold: bool = True
    # How many files the batch MP3 flow converts at once. 1 = a genuinely sequential run.
    # Same story as the field above: defaulted so an older settings.json still loads. The
    # factory (not a flat literal) keeps the core-count cap true on EVERY path — a direct
    # ``Settings(...)`` used to get a flat 4 while a loaded one got min(4, cpu_count).
    batch_workers: int = field(default_factory=lambda: default_batch_workers())


def default_batch_workers() -> int:
    """The seeded worker count: four, or fewer on a small machine.

    ffmpeg extracting an audio track is more I/O than CPU, so a handful of concurrent
    conversions is where the wall-clock win lands; past that they queue on the disk
    instead. Capped at the core count so a 2-core box does not thrash.
    """
    return max(1, min(_DEFAULT_BATCH_WORKERS, os.cpu_count() or 1))


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
        auto_accept_under_threshold=True,
        batch_workers=default_batch_workers(),
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


def _optional_bool(table: dict[str, Any], key: str, where: str, default: bool) -> bool:
    """Return ``table[key]`` if a bool, ``default`` if absent, else a loud ConfigError.

    Present-but-not-a-bool is caught (a hand-edited typo fails loud, not silently
    ignored), mirroring the other optional-field validators.
    """
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{_MODELS_FILENAME}: '{key}' in {where} must be true or false.")
    return value


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
        prices_unverified=_optional_bool(table, "prices_unverified", where, False),
        output_per_input_ratio=_optional_positive(
            table, "output_per_input_ratio", where, _DEFAULT_OUTPUT_PER_INPUT_RATIO
        ),
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
    guard = GuardConfig(safe_budget_fraction=fraction)

    summarize_table = raw.get("summarize")
    if not isinstance(summarize_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: missing [summarize] table.")
    summarize = SummarizeConfig(
        max_output_tokens=int(
            _as_positive_number(
                _require(summarize_table, "max_output_tokens", "[summarize]"),
                "max_output_tokens",
                "[summarize]",
            )
        ),
        reconcile_output_floor_tokens=int(
            _optional_positive(
                summarize_table,
                "reconcile_output_floor_tokens",
                "[summarize]",
                _DEFAULT_RECONCILE_OUTPUT_FLOOR_TOKENS,
            )
        ),
        # Optional, defaulted (TD-16 v2): the synthesis + reconcile prompts. A minimal
        # [summarize] table (or fixture) without these keeps the code-level fallbacks.
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
            phase_target_tokens=int(
                _optional_positive(
                    chunk_table, "phase_target_tokens", "[chunk]", _DEFAULT_PHASE_TARGET_TOKENS
                )
            ),
            max_loop_coverage=_optional_positive(
                chunk_table,
                "max_loop_coverage",
                "[chunk]",
                _DEFAULT_MAX_LOOP_COVERAGE,
            ),
            min_block_words=int(
                _optional_positive(
                    chunk_table, "min_block_words", "[chunk]", _DEFAULT_MIN_BLOCK_WORDS
                )
            ),
        )

    # [scan] is OPTIONAL on the same terms as [chunk] above.
    scan_table = raw.get("scan")
    if scan_table is None:
        scan = ScanConfig()
    elif not isinstance(scan_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: [scan] must be a table.")
    else:
        scan = ScanConfig(
            words_per_minute=_optional_positive(
                scan_table, "words_per_minute", "[scan]", _DEFAULT_WORDS_PER_MINUTE
            ),
            chars_per_word=_optional_positive(
                scan_table, "chars_per_word", "[scan]", _DEFAULT_CHARS_PER_WORD
            ),
        )

    return ModelConfig(
        tiers=tiers,
        guard=guard,
        summarize=summarize,
        asset=asset,
        transcript=transcript,
        chunk=chunk,
        scan=scan,
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

    # Optional (added after the original four): absent in an older settings.json → default
    # True (auto-accept). Present-but-not-a-bool is a loud error, like the other fields.
    auto_accept = data.get("auto_accept_under_threshold", True)
    if not isinstance(auto_accept, bool):
        raise ConfigError(f"{source}: auto_accept_under_threshold must be true or false.")

    # Optional, same backfill story. A bool is rejected explicitly because ``True`` is an
    # int in Python and would otherwise sail through as "1 worker".
    workers = data.get("batch_workers", default_batch_workers())
    if isinstance(workers, bool) or not isinstance(workers, int):
        raise ConfigError(f"{source}: batch_workers must be a whole number.")
    if not 1 <= workers <= MAX_BATCH_WORKERS:
        raise ConfigError(f"{source}: batch_workers must be between 1 and {MAX_BATCH_WORKERS}.")

    return Settings(
        summary_language=language,
        output_format=output_format,
        model_tier=model_tier,
        confirm_threshold_usd=float(threshold),
        auto_accept_under_threshold=auto_accept,
        batch_workers=workers,
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
