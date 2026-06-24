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
    cost projection). A flush cap truncates a long multi-section RU summary into
    invalid tool-use JSON and wastes the paid call, so the cap carries real
    headroom for Cyrillic tokenization.
    """

    system_prompt: str
    max_output_tokens: int


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
    )

    asset_table = raw.get("model_asset")
    if not isinstance(asset_table, dict):
        raise ConfigError(f"{_MODELS_FILENAME}: missing [model_asset] table.")
    asset = ModelAsset(
        name=str(_require(asset_table, "name", "[model_asset]")),
        hf_repo=str(_require(asset_table, "hf_repo", "[model_asset]")),
        local_dir=str(_require(asset_table, "local_dir", "[model_asset]")),
    )

    return ModelConfig(tiers=tiers, guard=guard, summarize=summarize, asset=asset)


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
