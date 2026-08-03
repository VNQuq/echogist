"""Unit tests for scripts/check-models.py — the model-currency maintenance tool.

Offline by construction (killswitch): these exercise the PURE parsing / diff /
rewrite logic against a RECORDED JSON fixture (``tests/fixtures/models_list.json``),
never a live ``GET /v1/models`` call. The script itself is standalone and hyphen-named,
so it is loaded here by file path via importlib rather than a normal import.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-models.py"
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "models_list.json"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("check_models", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves the module's (future-)stringized
    # annotations via sys.modules[__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cm = _load_module()


@pytest.fixture
def api_models() -> Any:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))["data"]
    return cm.parse_models_response(raw)


# --------------------------------------------------------------------------- #
# parse_models_response
# --------------------------------------------------------------------------- #
def test_parse_reads_ids_names_and_limits(api_models: dict[str, Any]) -> None:
    haiku = api_models["claude-haiku-4-5"]
    assert haiku.display_name == "Claude Haiku 4.5"
    assert haiku.max_input_tokens == 200000
    assert haiku.max_output_tokens == 64000


def test_parse_skips_entry_without_id(api_models: dict[str, Any]) -> None:
    # The malformed fixture entry (no "id") must not appear.
    assert all(m.id for m in api_models.values())
    assert "Malformed" not in {m.display_name for m in api_models.values()}


def test_parse_tolerates_missing_token_limits(api_models: dict[str, Any]) -> None:
    opus = api_models["claude-opus-4-8-20260101"]
    assert opus.max_input_tokens is None
    assert opus.max_output_tokens is None


# --------------------------------------------------------------------------- #
# match_model
# --------------------------------------------------------------------------- #
def test_match_exact_alias(api_models: dict[str, Any]) -> None:
    assert cm.match_model("claude-haiku-4-5", api_models).id == "claude-haiku-4-5"


def test_match_picks_newest_snapshot_of_alias(api_models: dict[str, Any]) -> None:
    # claude-sonnet-5 is not listed directly; the newest dated snapshot wins.
    assert cm.match_model("claude-sonnet-5", api_models).id == "claude-sonnet-5-20250930"


def test_match_returns_none_for_retired(api_models: dict[str, Any]) -> None:
    assert cm.match_model("claude-sonnet-9", api_models) is None


def test_match_never_crosses_a_generation_boundary() -> None:
    """A snapshot is strictly ``<alias>-YYYYMMDD``; a nested next-gen id is NOT one.

    Anthropic's ids nest ('claude-sonnet-4' -> 'claude-sonnet-4-5-…'), and the nested
    one even sorts last. A prefix match would report a retired tier as [ok] against a
    different model and derive ITS context_window.
    """
    models = cm.parse_models_response(
        [
            {"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5"},
            {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
        ]
    )
    # Sonnet 4 is gone: no exact id, and 4.5 is a different generation, not a snapshot.
    assert cm.match_model("claude-sonnet-4", models) is None
    # The 4.5 alias itself still resolves (exact), and to its own snapshot when absent.
    assert cm.match_model("claude-sonnet-4-5", models).id == "claude-sonnet-4-5"
    del models["claude-sonnet-4-5"]
    assert cm.match_model("claude-sonnet-4-5", models).id == "claude-sonnet-4-5-20250929"


def test_zero_token_limit_is_unknown_not_a_real_limit() -> None:
    """A 0 from the API must read as "unknown", never as a limit worth writing.

    Writing ``context_window = 0`` would make echogist/config.py reject models.toml
    ('must be > 0') on every subsequent start — the tool bricking its own app.
    """
    models = cm.parse_models_response(
        [{"id": "claude-x-1", "display_name": "X", "max_input_tokens": 0, "max_tokens": 0}]
    )
    assert models["claude-x-1"].max_input_tokens is None
    assert models["claude-x-1"].max_output_tokens is None
    tiers = {"economy": {"model_id": "claude-x-1", "context_window": 200000}}
    findings = cm.build_report(tiers, models, {})
    assert findings[0].context_drift is False
    assert cm.plan_writes(findings) == {}


# --------------------------------------------------------------------------- #
# build_report / plan_writes / plan_cache
# --------------------------------------------------------------------------- #
def _tiers() -> dict[str, dict[str, Any]]:
    return {
        "economy": {"model_id": "claude-haiku-4-5", "context_window": 200000},
        "balanced": {"model_id": "claude-sonnet-5", "context_window": 900000},
        "flagship": {"model_id": "claude-opus-4-8", "context_window": 1000000},
        "dead": {"model_id": "claude-sonnet-9", "context_window": 1000000},
    }


def test_report_flags_retired_tier(api_models: dict[str, Any]) -> None:
    findings = {f.name: f for f in cm.build_report(_tiers(), api_models, {})}
    assert findings["dead"].status == "retired"
    assert findings["economy"].status == "valid"


def test_report_detects_context_drift(api_models: dict[str, Any]) -> None:
    findings = {f.name: f for f in cm.build_report(_tiers(), api_models, {})}
    # balanced config says 900k, live snapshot says 1,000,000 -> drift.
    assert findings["balanced"].context_drift is True
    assert findings["balanced"].api_input_tokens == 1000000
    # economy matches exactly -> no drift.
    assert findings["economy"].context_drift is False


def test_report_no_drift_when_api_limit_unknown(api_models: dict[str, Any]) -> None:
    # flagship matched a snapshot with no max_input_tokens -> cannot claim drift.
    findings = {f.name: f for f in cm.build_report(_tiers(), api_models, {})}
    assert findings["flagship"].context_drift is False
    assert findings["flagship"].api_input_tokens is None


def test_report_generation_bump_needs_prior_cache(api_models: dict[str, Any]) -> None:
    # No cache -> first sighting, not a bump.
    fresh = {f.name: f for f in cm.build_report(_tiers(), api_models, {})}
    assert fresh["balanced"].generation_bump is False
    # Cached under the OLD display_name -> bump.
    cache = {"claude-sonnet-5": "Claude Sonnet 4.6"}
    bumped = {f.name: f for f in cm.build_report(_tiers(), api_models, cache)}
    assert bumped["balanced"].generation_bump is True
    # Cached under the SAME display_name -> no bump.
    same = {"claude-sonnet-5": "Claude Sonnet 5"}
    steady = {f.name: f for f in cm.build_report(_tiers(), api_models, same)}
    assert steady["balanced"].generation_bump is False


def test_new_alias_is_reported_but_never_written(api_models: dict[str, Any]) -> None:
    """A hand-edited model_id is a first sighting, so the display_name bump check
    cannot see it. Report it (prices likely moved) but never flip the flag — only a
    human can confirm prices. An EMPTY cache is a first run: nothing is 'new' there.
    """
    # Cache knows the other tiers but not balanced's alias -> a human swapped it.
    cache = {"claude-haiku-4-5": "Claude Haiku 4.5"}
    findings = {f.name: f for f in cm.build_report(_tiers(), api_models, cache)}
    assert findings["balanced"].new_alias is True
    assert findings["balanced"].generation_bump is False
    assert findings["economy"].new_alias is False  # known alias
    # Report says so; the write plan does not.
    report = cm.format_report(list(findings.values()), {})
    assert any("NEW alias 'claude-sonnet-5'" in line for line in report.lines)
    # balanced still gets its context_window write (real drift), but NOT the flag.
    assert cm.plan_writes(list(findings.values()))["balanced"].set_prices_unverified is False
    # First run (empty cache): everything is a first sighting, nothing is "new".
    fresh = {f.name: f for f in cm.build_report(_tiers(), api_models, {})}
    assert all(not f.new_alias for f in fresh.values())


def test_plan_writes_context_and_flag_but_never_prices(api_models: dict[str, Any]) -> None:
    cache = {"claude-sonnet-5": "Claude Sonnet 4.6"}
    findings = cm.build_report(_tiers(), api_models, cache)
    writes = cm.plan_writes(findings)
    assert writes["balanced"].context_window == 1000000
    assert writes["balanced"].set_prices_unverified is True
    # economy is unchanged -> no write entry at all.
    assert "economy" not in writes
    # retired tier is never written.
    assert "dead" not in writes


def test_plan_cache_refreshes_valid_keeps_retired(api_models: dict[str, Any]) -> None:
    cache = {"claude-sonnet-5": "Claude Sonnet 4.6", "claude-sonnet-9": "Old Retired"}
    findings = cm.build_report(_tiers(), api_models, cache)
    new_cache = cm.plan_cache(findings, cache)
    assert new_cache["claude-sonnet-5"] == "Claude Sonnet 5"  # refreshed
    assert new_cache["claude-sonnet-9"] == "Old Retired"  # retired entry kept


# --------------------------------------------------------------------------- #
# rewrite_toml (pure string transform, comments preserved)
# --------------------------------------------------------------------------- #
_SAMPLE_TOML = """\
# header comment
[tiers.economy]
model_id = "claude-haiku-4-5"
context_window = 200000  # inline comment kept
price_in_per_mtok = 1.0

[tiers.balanced]
model_id = "claude-sonnet-5"
context_window = 900000
prices_unverified = false

[guard]
safe_budget_fraction = 0.8
"""


def test_rewrite_updates_context_window_keeps_comment() -> None:
    out = cm.rewrite_toml(_SAMPLE_TOML, {"economy": cm.TierWrite(context_window=250000)})
    assert "context_window = 250000  # inline comment kept" in out
    # Other tiers untouched.
    assert "context_window = 900000" in out


def test_rewrite_flips_existing_flag() -> None:
    out = cm.rewrite_toml(_SAMPLE_TOML, {"balanced": cm.TierWrite(set_prices_unverified=True)})
    assert "prices_unverified = true" in out
    assert "prices_unverified = false" not in out


def test_rewrite_inserts_flag_after_model_id_when_absent() -> None:
    out = cm.rewrite_toml(_SAMPLE_TOML, {"economy": cm.TierWrite(set_prices_unverified=True)})
    lines = out.splitlines()
    econ = lines.index("[tiers.economy]")
    assert lines[econ + 1] == 'model_id = "claude-haiku-4-5"'
    assert lines[econ + 2] == "prices_unverified = true"
    # Exactly one flag line was added (no duplicate).
    assert out.count("prices_unverified = true") == 1


def test_rewrite_reparses_and_only_targets_named_tier() -> None:
    out = cm.rewrite_toml(
        _SAMPLE_TOML,
        {
            "balanced": cm.TierWrite(context_window=1000000, set_prices_unverified=True),
            "ghost": cm.TierWrite(context_window=1),  # not in the text -> ignored
        },
    )
    data = tomllib.loads(out)
    assert data["tiers"]["balanced"]["context_window"] == 1000000
    assert data["tiers"]["balanced"]["prices_unverified"] is True
    assert data["tiers"]["economy"]["context_window"] == 200000
    assert data["guard"]["safe_budget_fraction"] == 0.8


def test_rewrite_no_updates_is_identity() -> None:
    assert cm.rewrite_toml(_SAMPLE_TOML, {}) == _SAMPLE_TOML


def test_rewrite_ignores_bracket_lines_inside_a_prompt_heredoc() -> None:
    """models.toml holds the prompts as data, and that prose starts lines with '['.

    A naive header scan would split mid-string and could even name a fake tier
    segment, letting a write land inside the prompt text.
    """
    text = (
        _SAMPLE_TOML
        + '\n[summarize]\nsynthesis_system_prompt = """\n'
        + "[tiers.economy]\n"
        + "[интерпретация]: пометь мост за пределы текста\n"
        + '[HH:MM:SS] — якорь\n"""\n\n[chunk]\nmax_phases = 6\n'
    )
    out = cm.rewrite_toml(text, {"economy": cm.TierWrite(context_window=250000)})
    data = tomllib.loads(out)
    assert data["tiers"]["economy"]["context_window"] == 250000
    # The prompt is byte-identical: no key injected, no line reordered.
    assert "[tiers.economy]\n[интерпретация]: пометь мост за пределы текста" in out
    assert data["chunk"]["max_phases"] == 6
    assert out.count("[HH:MM:SS] — якорь") == 1


def test_rewrite_flag_insert_stays_inside_a_tier_without_model_id() -> None:
    """Defensive: no model_id in the block must not push the key above the header
    (i.e. into the PREVIOUS table). Unreachable via plan_writes, but the rewrite
    primitive must not corrupt the file on its own."""
    text = "[tiers.economy]\ncontext_window = 200000\n\n[guard]\nsafe_budget_fraction = 0.8\n"
    out = cm.rewrite_toml(text, {"economy": cm.TierWrite(set_prices_unverified=True)})
    data = tomllib.loads(out)
    assert data["tiers"]["economy"]["prices_unverified"] is True
    assert "prices_unverified" not in data["guard"]
