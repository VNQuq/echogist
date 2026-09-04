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

[summarize]
max_output_tokens = 4096

[model_asset]
name = "large-v3-int8_float16"
hf_repo = "Systran/faster-whisper-large-v3"
local_dir = "models/large-v3-int8_float16"
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


def test_tier_temperature_is_none_when_absent(tmp_path: Path) -> None:
    # Current 4.x models deprecate temperature; absence -> omit the param at call time.
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.tier("economy").temperature is None


def test_tier_temperature_is_read_when_present(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace(
        'model_id = "claude-haiku-4-5"', 'model_id = "claude-haiku-4-5"\ntemperature = 0'
    )
    cfg = load_model_config(_write(tmp_path / "models.toml", text))
    assert cfg.tier("economy").temperature == 0.0


def test_negative_temperature_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace(
        'model_id = "claude-haiku-4-5"', 'model_id = "claude-haiku-4-5"\ntemperature = -1'
    )
    with pytest.raises(ConfigError, match="temperature.*must be >= 0"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_prices_unverified_defaults_false(tmp_path: Path) -> None:
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.tier("economy").prices_unverified is False


def test_prices_unverified_true_loads(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace(
        'model_id = "claude-haiku-4-5"',
        'model_id = "claude-haiku-4-5"\nprices_unverified = true',
    )
    cfg = load_model_config(_write(tmp_path / "models.toml", text))
    assert cfg.tier("economy").prices_unverified is True


def test_prices_unverified_non_bool_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace(
        'model_id = "claude-haiku-4-5"',
        'model_id = "claude-haiku-4-5"\nprices_unverified = "yes"',
    )
    with pytest.raises(ConfigError, match="prices_unverified.*must be true or false"):
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


def test_missing_summarize_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML.replace("[summarize]", "[summarizex]")
    with pytest.raises(ConfigError, match=r"\[summarize\]"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_shipped_summarize_config_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert cfg.summarize.max_output_tokens == 12_288
    assert "{language}" in cfg.summarize.synthesis_system_prompt


# --------------------------------------------------------------------------- #
# [chunk] — phase-split config (TD-16 v2): optional, defaulted, validated
# --------------------------------------------------------------------------- #
def test_shipped_chunk_config_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert cfg.chunk.phase_target_tokens == 12_000  # TD-16 v2 phase-split target
    # TD-26 hygiene floors, measured on a real RU lecture (speech never below 0.56 / 15).
    assert cfg.chunk.min_unique_word_ratio == 0.55
    assert cfg.chunk.min_block_words == 15
    # TD-16 v2 synthesis/reconcile prompts present, with their substitution tokens.
    assert "{language}" in cfg.summarize.synthesis_system_prompt
    assert "{interpretation}" in cfg.summarize.synthesis_system_prompt
    assert "{language}" in cfg.summarize.reconcile_system_prompt


def test_missing_chunk_table_uses_defaults(tmp_path: Path) -> None:
    # VALID_MODELS_TOML has no [chunk] / no synthesis_system_prompt -> code defaults apply.
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    # The CODE default must track the shipped models.toml: a missing [chunk] table used to
    # silently restore 24_000, i.e. the phase size that overflowed the output cap on a real
    # lecture. Tie them together so the fallback path cannot reintroduce the bug.
    assert cfg.chunk.phase_target_tokens == 12_000
    assert cfg.chunk.min_unique_word_ratio == 0.55
    assert cfg.chunk.min_block_words == 15
    # TD-16 v2 prompts default in when the keys are absent (not crashed).
    assert "{interpretation}" in cfg.summarize.synthesis_system_prompt
    assert "{language}" in cfg.summarize.reconcile_system_prompt


def test_phase_target_override_parses(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML + "\n[chunk]\nphase_target_tokens = 30000\n"
    cfg = load_model_config(_write(tmp_path / "models.toml", text))
    assert cfg.chunk.phase_target_tokens == 30000


def test_phase_target_invalid_fails_loud(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML + "\n[chunk]\nphase_target_tokens = 0\n"
    with pytest.raises(ConfigError, match="must be > 0"):
        load_model_config(_write(tmp_path / "models.toml", text))


# --------------------------------------------------------------------------- #
# [scan] — the scanner's speech-rate constants: optional, defaulted, validated
# --------------------------------------------------------------------------- #
def test_tier_output_ratio_loads_and_defaults_high() -> None:
    """The cost model's output side is a per-tier ratio (TD-24). A tier that declares one
    gets it; a hand-added tier that does not is priced as the most verbose model measured,
    never as a cheap one — an under-quote is the failure that spends unagreed money."""
    cfg = load_model_config(REPO_MODELS)
    assert cfg.tier("economy").output_per_input_ratio == 0.39  # measured, seven-lecture run
    assert cfg.tier("balanced").output_per_input_ratio < cfg.tier("economy").output_per_input_ratio


def test_shipped_reconcile_floor_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert cfg.summarize.reconcile_output_floor_tokens == 2500


def test_tier_without_a_ratio_falls_back_to_the_high_default(tmp_path: Path) -> None:
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.tier("economy").output_per_input_ratio == 0.40
    assert cfg.summarize.reconcile_output_floor_tokens == 2500


def test_shipped_scan_config_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert cfg.scan.words_per_minute == 135.0
    assert cfg.scan.chars_per_word == 6.5


def test_missing_scan_table_uses_defaults(tmp_path: Path) -> None:
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.scan.words_per_minute == 135.0
    assert cfg.scan.chars_per_word == 6.5


def test_scan_constants_override_parses(tmp_path: Path) -> None:
    # The whole point of TD-23: recalibrating these must be a models.toml edit, with no
    # code change. If the override does not land, that plan silently does nothing.
    text = VALID_MODELS_TOML + "\n[scan]\nwords_per_minute = 120\nchars_per_word = 6\n"
    cfg = load_model_config(_write(tmp_path / "models.toml", text))
    assert cfg.scan.words_per_minute == 120.0
    assert cfg.scan.chars_per_word == 6.0


@pytest.mark.parametrize("value", ["0", "-5"])
def test_scan_constant_invalid_fails_loud(tmp_path: Path, value: str) -> None:
    text = VALID_MODELS_TOML + f"\n[scan]\nwords_per_minute = {value}\n"
    with pytest.raises(ConfigError, match="must be > 0"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_scan_table_of_the_wrong_shape_fails_loud(tmp_path: Path) -> None:
    # PREPENDED, not appended: a bare key after the last [table] header would be parsed
    # as a member of that table, land nowhere near the top level, and the test would pass
    # for the wrong reason.
    text = 'scan = "not a table"\n' + VALID_MODELS_TOML
    with pytest.raises(ConfigError, match=r"\[scan\] must be a table"):
        load_model_config(_write(tmp_path / "models.toml", text))


def test_blank_synthesis_prompt_fails_loud(tmp_path: Path) -> None:
    # A hand-edited blank prompt is a loud ConfigError (caught, not silently defaulted).
    text = VALID_MODELS_TOML.replace(
        "max_output_tokens = 4096",
        'max_output_tokens = 4096\nsynthesis_system_prompt = "   "',
    )
    with pytest.raises(ConfigError, match="must be a non-empty string"):
        load_model_config(_write(tmp_path / "models.toml", text))


# --------------------------------------------------------------------------- #
# Model config — [transcript] block_seconds (optional, defaults to 60)
# --------------------------------------------------------------------------- #
def test_shipped_transcript_block_seconds_loads() -> None:
    cfg = load_model_config(REPO_MODELS)
    assert cfg.transcript.block_seconds == 60.0


def test_missing_transcript_table_defaults(tmp_path: Path) -> None:
    # VALID_MODELS_TOML has no [transcript] table -> falls back to the default.
    cfg = load_model_config(_write(tmp_path / "models.toml", VALID_MODELS_TOML))
    assert cfg.transcript.block_seconds == 60.0


def test_transcript_block_seconds_parsed(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML + "\n[transcript]\nblock_seconds = 90\n"
    cfg = load_model_config(_write(tmp_path / "models.toml", text))
    assert cfg.transcript.block_seconds == 90.0


def test_transcript_block_seconds_non_positive_errors(tmp_path: Path) -> None:
    text = VALID_MODELS_TOML + "\n[transcript]\nblock_seconds = 0\n"
    with pytest.raises(ConfigError, match="must be > 0"):
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


def test_auto_accept_defaults_true() -> None:
    # New field: defaults to auto-accepting the below-threshold (cheap) path.
    assert default_settings().auto_accept_under_threshold is True


def test_auto_accept_absent_in_old_file_defaults_true(tmp_path: Path) -> None:
    # Backward-compat: a settings.json written before the field existed (the original four keys
    # only) still loads — the missing flag defaults to True, not a "missing setting" error.
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf",'
        '"model_tier":"balanced","confirm_threshold_usd":0.5}',
    )
    assert load_settings(tmp_path / "settings.json").auto_accept_under_threshold is True


def test_auto_accept_explicit_false_loads(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf","model_tier":"balanced",'
        '"confirm_threshold_usd":0.5,"auto_accept_under_threshold":false}',
    )
    assert load_settings(tmp_path / "settings.json").auto_accept_under_threshold is False


def test_auto_accept_non_bool_errors(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf","model_tier":"balanced",'
        '"confirm_threshold_usd":0.5,"auto_accept_under_threshold":"yes"}',
    )
    with pytest.raises(ConfigError, match="auto_accept_under_threshold"):
        load_settings(tmp_path / "settings.json")


def test_batch_workers_defaults_to_at_most_four(tmp_path: Path) -> None:
    # Seeded from the core count so a 2-core box does not thrash, capped at 4 because a
    # batch waits on the disk past that. Also the backfill for a file written before the
    # field existed — an old settings.json must keep loading.
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf",'
        '"model_tier":"balanced","confirm_threshold_usd":0.5}',
    )
    loaded = load_settings(tmp_path / "settings.json")

    assert loaded.batch_workers == default_settings().batch_workers
    assert 1 <= loaded.batch_workers <= 4


def test_batch_workers_explicit_value_loads(tmp_path: Path) -> None:
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf","model_tier":"balanced",'
        '"confirm_threshold_usd":0.5,"batch_workers":1}',
    )
    assert load_settings(tmp_path / "settings.json").batch_workers == 1


@pytest.mark.parametrize("bad", ["0", "99", '"4"', "2.5", "true"])
def test_batch_workers_out_of_range_or_wrong_type_errors(tmp_path: Path, bad: str) -> None:
    # ``true`` is called out explicitly: bool is an int in Python, so without the isinstance
    # guard it would sail through as "1 worker" instead of failing loud.
    _write(
        tmp_path / "settings.json",
        '{"summary_language":"ru","output_format":"pdf","model_tier":"balanced",'
        f'"confirm_threshold_usd":0.5,"batch_workers":{bad}}}',
    )
    with pytest.raises(ConfigError, match="batch_workers"):
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
# API key — env wins, then config/secrets.toml (fail-soft)
# --------------------------------------------------------------------------- #
def test_get_api_key_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-abc  ")
    assert config.get_api_key(secrets_path=tmp_path / "none.toml") == "sk-abc"


def test_get_api_key_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.get_api_key(secrets_path=tmp_path / "none.toml") is None


def test_get_api_key_blank_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert config.get_api_key(secrets_path=tmp_path / "none.toml") is None


def _write_secrets(tmp_path: Path, body: str) -> Path:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text(body, encoding="utf-8")
    return secrets


def test_env_wins_over_secrets_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    secrets = _write_secrets(tmp_path, 'anthropic_api_key = "sk-file"\n')
    assert config.get_api_key(secrets_path=secrets) == "sk-env"


def test_secrets_file_used_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = _write_secrets(tmp_path, 'anthropic_api_key = "  sk-file  "\n')
    assert config.get_api_key(secrets_path=secrets) == "sk-file"  # stripped


def test_secrets_file_used_when_env_blank(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")  # blank env = not set → fall through
    secrets = _write_secrets(tmp_path, 'anthropic_api_key = "sk-file"\n')
    assert config.get_api_key(secrets_path=secrets) == "sk-file"


def test_secrets_file_missing_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.get_api_key(secrets_path=tmp_path / "absent.toml") is None


def test_secrets_file_corrupt_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = _write_secrets(tmp_path, "this is not = valid toml [[[\n")
    assert config.get_api_key(secrets_path=secrets) is None


def test_secrets_file_missing_key_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = _write_secrets(tmp_path, 'other_key = "x"\n')
    assert config.get_api_key(secrets_path=secrets) is None


def test_secrets_file_blank_value_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = _write_secrets(tmp_path, 'anthropic_api_key = "   "\n')
    assert config.get_api_key(secrets_path=secrets) is None


def test_secrets_file_non_string_value_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = _write_secrets(tmp_path, "anthropic_api_key = 12345\n")
    assert config.get_api_key(secrets_path=secrets) is None


# --------------------------------------------------------------------------- #
# Picker state (TD-10) — last-used dir IO (fail-soft) + the initialdir ladder
# --------------------------------------------------------------------------- #
def test_load_last_dir_absent_is_none(tmp_path: Path) -> None:
    assert config.load_last_dir(tmp_path / "state.json") is None


def test_save_then_load_last_dir_round_trips(tmp_path: Path) -> None:
    state = tmp_path / "sub" / "state.json"  # parent does not exist yet
    config.save_last_dir(Path("/media/clips"), state)
    assert state.is_file()  # save created the dir
    assert config.load_last_dir(state) == Path("/media/clips")


def test_load_last_dir_corrupt_json_is_none(tmp_path: Path) -> None:
    state = _write(tmp_path / "state.json", "{not json")
    assert config.load_last_dir(state) is None  # fail-soft, no raise


def test_load_last_dir_wrong_shape_is_none(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", '["not", "a", "dict"]')
    _write(tmp_path / "b.json", '{"last_input_dir": 42}')
    _write(tmp_path / "c.json", '{"last_input_dir": ""}')
    assert config.load_last_dir(tmp_path / "a.json") is None
    assert config.load_last_dir(tmp_path / "b.json") is None
    assert config.load_last_dir(tmp_path / "c.json") is None


def test_save_last_dir_swallows_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A read-only config dir must not surface after a completed run.
    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", _boom)
    config.save_last_dir(Path("/media/clips"), tmp_path / "state.json")  # no raise


def test_resolve_initial_dir_prefers_existing_last(tmp_path: Path) -> None:
    assert config.resolve_initial_dir(tmp_path) == tmp_path


def test_resolve_initial_dir_falls_to_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    # last is None / gone → Downloads (exists)
    assert config.resolve_initial_dir(None) == downloads
    assert config.resolve_initial_dir(tmp_path / "gone") == downloads


def test_resolve_initial_dir_falls_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()  # no Downloads under it
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    assert config.resolve_initial_dir(None) == home


def test_resolve_initial_dir_guards_oserror_on_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dead UNC / network path can raise on is_dir() — must fall through, not crash.
    def _raise(_self: Path) -> bool:
        raise OSError("network path is unreachable")

    monkeypatch.setattr(Path, "is_dir", _raise)
    result = config.resolve_initial_dir(Path("//dead-host/share"))
    assert result == Path.home()  # fell through to the floor
