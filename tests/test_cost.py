"""Cost-stage tests (T8).

The cost stage is a pure, offline stage — these run with no model, no key, no
network (killswitch). Coverage: the input/output/total arithmetic at the tier's
per-MTok prices, estimate-vs-actual sourcing (fixed output projection vs audited
usage), both threshold sides (Enter for cheap material, explicit y/N above the
threshold), message formatting (sub-cent shown, not rounded to zero), and the
killswitch (no network import at module top level).
"""

from __future__ import annotations

import ast
from pathlib import Path

from echogist import cost
from echogist.config import ModelTier
from echogist.summarize import SummarizeResult, Summary


def _tier(price_in: float = 3.0, price_out: float = 15.0) -> ModelTier:
    return ModelTier(
        name="balanced",
        model_id="test-model",
        context_window=1_000_000,
        price_in_per_mtok=price_in,
        price_out_per_mtok=price_out,
    )


def _result(input_tokens: int, output_tokens: int) -> SummarizeResult:
    summary = Summary(
        title="t",
        core_idea="c",
        decisions=(),
        action_items=(),
        language="en",
    )
    return SummarizeResult(
        summary=summary,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# --------------------------------------------------------------------------- #
# CostEstimate arithmetic
# --------------------------------------------------------------------------- #
def test_cost_breakdown_at_tier_prices() -> None:
    est = cost.CostEstimate(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        price_in_per_mtok=3.0,
        price_out_per_mtok=15.0,
    )
    assert est.input_cost_usd == 3.0
    assert est.output_cost_usd == 15.0
    assert est.total_usd == 18.0


def test_cost_scales_with_tokens() -> None:
    est = cost.CostEstimate(
        input_tokens=100_000,  # 0.1 MTok * $3 = $0.30
        output_tokens=2_000,  # 0.002 MTok * $15 = $0.03
        price_in_per_mtok=3.0,
        price_out_per_mtok=15.0,
    )
    assert round(est.input_cost_usd, 6) == 0.30
    assert round(est.output_cost_usd, 6) == 0.03
    assert round(est.total_usd, 6) == 0.33


# --------------------------------------------------------------------------- #
# estimate_cost_synthesis (TD-16 v2 / TD-21) — tight per-call projection + margin
# --------------------------------------------------------------------------- #
def test_estimate_cost_synthesis_projects_phases_and_reconcile_per_call() -> None:
    # K>1: each of the K phases + 1 reconcile projected at the realistic PER-CALL output
    # figure; reconcile INPUT is the K phase outputs fed to it (K * per_call), not the cap.
    est = cost.estimate_cost_synthesis([1000, 2000], _tier(), per_call_output_tokens=2800)
    assert est.input_tokens == 3000 + 2 * 2800  # sum(phase inputs) + reconcile input (K*per_call)
    assert est.output_tokens == 3 * 2800  # K phases + 1 reconcile, each at the per-call figure
    assert est.price_out_per_mtok == 15.0  # tier prices carried through


def test_estimate_cost_synthesis_single_phase_has_no_reconcile() -> None:
    # K=1 is degenerate: one phase, its heading is the title, NO reconcile call — so no
    # reconcile input and only one per-call output.
    est = cost.estimate_cost_synthesis([1000], _tier(), per_call_output_tokens=2800)
    assert est.input_tokens == 1000  # one phase input, no reconcile input added
    assert est.output_tokens == 2800  # one per-call output only


def test_estimate_cost_synthesis_is_tighter_than_the_old_cap_ceiling() -> None:
    # TD-21: the quote is a TIGHT estimate + margin, not the worst-case cap ceiling. The
    # same K priced at a realistic per-call figure must land well under the cap-based one.
    phases = [5000, 5000, 5000]
    tight = cost.estimate_cost_synthesis(phases, _tier(), per_call_output_tokens=2800)
    ceiling = cost.estimate_cost_synthesis(phases, _tier(), per_call_output_tokens=8192)
    assert tight.total_usd < ceiling.total_usd  # tighter than projecting every call at the cap
    assert tight.output_tokens == 4 * 2800  # 3 phases + 1 reconcile at the per-call figure


# --------------------------------------------------------------------------- #
# actual_cost — from the audited response.usage counts
# --------------------------------------------------------------------------- #
def test_actual_cost_from_usage() -> None:
    actual = cost.actual_cost(_result(input_tokens=123_456, output_tokens=789), _tier())
    assert actual.input_tokens == 123_456
    assert actual.output_tokens == 789
    assert actual.total_usd == 123_456 / 1_000_000 * 3.0 + 789 / 1_000_000 * 15.0


def test_actual_differs_from_estimate() -> None:
    tier = _tier()
    estimate = cost.CostEstimate(50_000, 2_000, 3.0, 15.0)  # a high pre-call quote
    actual = cost.actual_cost(_result(input_tokens=41_000, output_tokens=1_500), tier)
    # The estimate is biased high; the real call usually comes in under it.
    assert actual.total_usd < estimate.total_usd


# --------------------------------------------------------------------------- #
# Threshold friction — both sides
# --------------------------------------------------------------------------- #
def test_requires_confirmation_below_at_and_above_threshold() -> None:
    cheap = cost.CostEstimate(10_000, 2_000, 3.0, 15.0)  # $0.06
    pricey = cost.CostEstimate(1_000_000, 2_000, 3.0, 15.0)  # ~$3.03
    assert cost.requires_explicit_confirmation(cheap, 0.50) is False
    assert cost.requires_explicit_confirmation(pricey, 0.50) is True
    # Exactly at the threshold is NOT over — cheap material proceeds on Enter.
    at = cost.CostEstimate(0, 0, 3.0, 15.0)
    assert cost.requires_explicit_confirmation(at, 0.0) is False


def test_confirm_cheap_proceeds_without_gate() -> None:
    cheap = cost.CostEstimate(10_000, 2_000, 3.0, 15.0)  # $0.06 < $0.50
    calls: list[tuple[str, bool]] = []

    def confirm(prompt: str, default: bool) -> bool:
        calls.append((prompt, default))
        return False  # would decline if asked — but cheap material must never ask

    assert cost.confirm_proceed(cheap, 0.50, confirm=confirm) is True
    assert calls == []  # no gate on a cheap call; the shown estimate is the acknowledgment


def test_confirm_above_threshold_asks_with_default_false() -> None:
    pricey = cost.CostEstimate(1_000_000, 2_000, 3.0, 15.0)  # ~$3.03 > $0.50
    seen: list[tuple[str, bool]] = []

    def confirm(prompt: str, default: bool) -> bool:
        seen.append((prompt, default))
        return True

    assert cost.confirm_proceed(pricey, 0.50, confirm=confirm) is True
    # The gate is asked exactly once, defaulting to No (safe-not-to-spend above budget).
    assert len(seen) == 1
    assert seen[0][1] is False
    assert "exceeds your" in seen[0][0]


def test_confirm_above_threshold_declines_returns_callable_result() -> None:
    pricey = cost.CostEstimate(1_000_000, 2_000, 3.0, 15.0)
    # Above the threshold, the operator's decline flows straight through.
    assert cost.confirm_proceed(pricey, 0.50, confirm=lambda _p, _d: False) is False
    assert cost.confirm_proceed(pricey, 0.50, confirm=lambda _p, _d: True) is True


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def test_estimate_message_names_tier_and_tokens() -> None:
    est = cost.CostEstimate(50_000, 2_000, 3.0, 15.0)
    msg = cost.estimate_message(est, _tier())
    assert "balanced" in msg
    assert "50,000" in msg
    assert "2,000" in msg
    assert "$" in msg


def test_actual_message_shows_billed_total() -> None:
    actual = cost.actual_cost(_result(41_000, 1_500), _tier())
    msg = cost.actual_message(actual)
    assert "41,000" in msg
    assert "1,500" in msg
    assert "Actual cost" in msg


def test_subcent_cost_not_rounded_to_zero() -> None:
    tiny = cost.CostEstimate(1_000, 100, 3.0, 15.0)  # ~$0.0045
    msg = cost.estimate_message(tiny, _tier())
    assert "$0.0000" not in msg  # 4 decimals keep a sub-cent figure visible


# --------------------------------------------------------------------------- #
# Killswitch — the module must import offline, no network packages at top level
# --------------------------------------------------------------------------- #
def test_module_imports_nothing_network_at_top_level() -> None:
    src = Path(cost.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"anthropic", "httpx", "requests", "urllib", "http", "socket", "ssl"}
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"network import at module top: {imported & banned}"
