"""T1 config tests — load, round-trip, and every bad-input path (F5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from echogist import config
from echogist.config import (
    ConfigError,
    ModelConfig,
    Settings,
    default_settings,
    load_model_config,
    load_settings,
    save_settings,
)

REPO_MODELS = Path(__file__).resolve().parent.parent / "config" / "models.toml"

VALID_MODELS_TOML = """
[tiers.economy]
model_id = "claude-haiku-4-5"
context_window = 200000
price_in_per_mtok = 1.0
price_out_per_mtok = 5.0

[guard]
safe_budget_fraction = 0.8
output_tokens_estimate = 2000

[model_asset]
name = "large-v3-int8_float16"
hf_repo = "Systran/faster-whisper-large-v3"
local_dir = "models/large-v3-int8_float16"
source_url = ""
sha256 = ""
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Model config — the shipped default
# --------------------------------------------------------------------------- #
def test_shipped_models_toml_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert isinstance(cfg, ModelConfig)
    assert set(cfg.tiers) == {"economy", "balanced", "flagship"}
    assert cfg.tiers["flagship"].model_id == "claude-opus-4-8"
    assert cfg.tiers["balanced"].context_window == 1_000_000
    assert cfg.guard.safe_budget_fraction == 0.8
    assert cfg.asset.name == "large-v3-int8_float16"


def test_safe_budget_uses_fraction() -> None:
    cfg = load_model_config(REPO_MODELS)
    # 200K * 0.8 = 160K for the economy tier.
    assert cfg.guard.safe_budget(cfg.tiers["economy"]) == 160_000


def test_tier_lookup_ok(tmp_path: Path) -> None:
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.tier("economy").model_id == "claude-haiku-4-5"


def test_unknown_tier_is_guided_error(tmp_path: Path) -> None:
    """F5: a deprecated/unknown tier guides re-select, never crashes."""
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    with pytest.raises(ConfigError) as exc:
        cfg.tier("turbo")
    assert "Settings" in str(exc.value)
    assert "economy" in str(exc.value)


# --------------------------------------------------------------------------- #
# Model config — malformed inputs
# --------------------------------------------------------------------------- #
def test_missing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_model_config(tmp_path / "nope.toml")


def test_malformed_toml_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Could not read"):
        load_model_config(_write(tmp_path / "models.toml", "this = = broken"))


def test_no_tiers_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace("[tiers.economy]", "[other.economy]")
    with pytest.raises(ConfigError, match="at least one"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_negative_price_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace("price_in_per_mtok = 1.0", "price_in_per_mtok = -1.0")
    with pytest.raises(ConfigError, match="must be > 0"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_fraction_over_one_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace("safe_budget_fraction = 0.8", "safe_budget_fraction = 1.5")
    with pytest.raises(ConfigError, match="<= 1"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_missing_guard_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace("[guard]", "[guardx]")
    with pytest.raises(ConfigError, match=r"\[guard\]"):
        load_model_config(_write(tmp_path / "models.toml", text))


# --------------------------------------------------------------------------- #
# Settings — defaults, round-trip, validation
# --------------------------------------------------------------------------- #
def test_missing_settings_returns_defaults(tmp_path: Path) -> None:
    s = load_settings(tmp_path / "settings.json")
    assert s == default_settings()
    assert s.output_format == "pdf"
    assert s.summary_language == "ru"


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings(
        summary_language="en",
        output_format="md",
        model_tier="flagship",
        confirm_threshold_usd=1.25,
    )
    save_settings(original, path)
    assert load_settings(path) == original


def test_save_creates_config_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    save_settings(default_settings(), path)
    assert path.is_file()


def test_invalid_language_errors(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"de","output_format":"pdf",'
        '"model_tier":"balanced","confirm_threshold_usd":0.5}',
    )
    with pytest.raises(ConfigError, match="summary_language"):
        load_settings(tmp_path / "settings.json")


def test_invalid_format_errors(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"docx",'
        '"model_tier":"balanced","confirm_threshold_usd":0.5}',
    )
    with pytest.raises(ConfigError, match="output_format"):
        load_settings(tmp_path / "settings.json")


def test_negative_threshold_errors(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf",'
        '"model_tier":"balanced","confirm_threshold_usd":-1}',
    )
    with pytest.raises(ConfigError, match="confirm_threshold_usd"):
        load_settings(tmp_path / "settings.json")


def test_missing_key_errors(tmp_path: Path) -> None:
    _write(tmp_path / "settings.json", '{"summary_language":"ru"}')
    with pytest.raises(ConfigError, match="missing setting"):
        load_settings(tmp_path / "settings.json")


def test_corrupt_settings_json_errors(tmp_path: Path) -> None:
    _write(tmp_path / "settings.json", "{not json")
    with pytest.raises(ConfigError, match="Could not read"):
        load_settings(tmp_path / "settings.json")


def test_save_rejects_invalid_settings(tmp_path: Path) -> None:
    bad = Settings(
        summary_language="xx",
        output_format="pdf",
        model_tier="balanced",
        confirm_threshold_usd=0.5,
    )
    with pytest.raises(ConfigError):
        save_settings(bad, tmp_path / "settings.json")
    assert not (tmp_path / "settings.json").exists()


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def test_config_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHOGIST_CONFIG_DIR", str(tmp_path))
    assert config.config_dir() == tmp_path


def test_config_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHOGIST_CONFIG_DIR", raising=False)
    assert config.config_dir().name == "config"


# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #
def test_get_api_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-abc  ")
    assert config.get_api_key() == "sk-abc"


def test_get_api_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.get_api_key() is None


def test_get_api_key_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert config.get_api_key() is None
