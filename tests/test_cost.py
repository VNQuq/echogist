"""Cost-stage tests (T8).

The cost stage is a pure, offline stage — these run with no model, no key, no
network (killswitch). Coverage: the input/output/total arithmetic at the tier's
per-MTok prices, estimate-vs-actual sourcing (proportional output projection vs audited
usage), the calibration against the one run whose real bill we know, both threshold
sides (Enter for cheap material, explicit y/N above the threshold), message formatting
(sub-cent shown, not rounded to zero), and the
killswitch (no network import at module top level).
"""

from __future__ import annotations

import ast
from pathlib import Path

from echogist import cost
from echogist.config import ModelTier
from echogist.summarize import SummarizeResult, Summary


def _tier(price_in: float = 3.0, price_out: float = 15.0, ratio: float = 0.5) -> ModelTier:
    return ModelTier(
        name="balanced",
        model_id="test-model",
        context_window=1_000_000,
        price_in_per_mtok=price_in,
        price_out_per_mtok=price_out,
        output_per_input_ratio=ratio,
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
# estimate_cost_synthesis (TD-16 v2 / TD-21 / TD-24) — proportional per-call projection
# --------------------------------------------------------------------------- #
def test_estimate_cost_synthesis_projects_phases_and_reconcile_per_call() -> None:
    # K>1: each phase's output is ratio x ITS OWN input; the reconcile's input is the phase
    # prose fed to it (the sum of those outputs) and its output is ratio x that.
    est = cost.estimate_cost_synthesis(
        [1000, 2000], _tier(ratio=0.5), output_cap=100_000, reconcile_floor=0
    )
    assert est.input_tokens == 3000 + 1500  # sum(phase inputs) + reconcile input (500 + 1000)
    assert est.output_tokens == 1500 + 750  # phase outputs + the reconcile's own
    assert est.price_out_per_mtok == 15.0  # tier prices carried through


def test_estimate_cost_synthesis_single_phase_still_prices_the_reconcile() -> None:
    # K=1 pays for the reconcile call too — it writes the essence block the document opens
    # with, so it is not skipped for short material and the quote must include it.
    est = cost.estimate_cost_synthesis(
        [1000], _tier(ratio=0.5), output_cap=100_000, reconcile_floor=0
    )
    assert est.input_tokens == 1000 + 500  # phase input + the reconcile's input
    assert est.output_tokens == 500 + 250  # one phase + one reconcile


def test_estimate_cost_synthesis_does_not_move_with_the_phase_split() -> None:
    """TD-24 (b): the same material must quote the same output however it is split.

    The flat per-call model made splitting look like it multiplied the bill — the same
    lecture quoted $0.2204 at K=4 and $0.3062 at K=7 for prose that is the same size
    either way, which penalised exactly the split that keeps a phase under the output cap.
    """
    tier = _tier(ratio=0.4)
    coarse = cost.estimate_cost_synthesis(
        [12_000, 12_000], tier, output_cap=100_000, reconcile_floor=0
    )
    fine = cost.estimate_cost_synthesis([6_000] * 4, tier, output_cap=100_000, reconcile_floor=0)
    assert coarse.output_tokens == fine.output_tokens
    assert coarse.input_tokens == fine.input_tokens  # phases do not overlap


def test_estimate_cost_synthesis_clamps_each_call_at_the_api_cap() -> None:
    # A call cannot emit more than max_tokens, so the projection must not either — that is
    # the one real ceiling in the model, and the reason the cap is passed in at all.
    est = cost.estimate_cost_synthesis(
        [100_000], _tier(ratio=0.5), output_cap=8_000, reconcile_floor=0
    )
    assert est.output_tokens == 8_000 + 4_000  # phase clamped at the cap, reconcile off that


def test_reconcile_output_never_dips_under_its_floor() -> None:
    """The reconcile writes a title + essence block whatever the file's size, so it is the
    one call with a real fixed cost. Without the floor, a purely proportional model has no
    per-call cost at all and a folder of short clips quotes like a single long file."""
    est = cost.estimate_cost_synthesis(
        [100], _tier(ratio=0.5), output_cap=100_000, reconcile_floor=2_500
    )
    assert est.output_tokens == 50 + 2_500  # the phase, then the floor, not 50 + 25


def test_estimate_cost_synthesis_is_tighter_than_the_old_cap_ceiling() -> None:
    # TD-21: the quote is a TIGHT estimate + margin, not the worst-case cap ceiling.
    phases = [5000, 5000, 5000]
    tight = cost.estimate_cost_synthesis(
        phases, _tier(ratio=0.4), output_cap=8192, reconcile_floor=0
    )
    ceiling = 4 * 8192  # what projecting every call at the cap would have charged
    assert tight.output_tokens < ceiling


def test_quote_stays_above_the_one_run_whose_real_bill_we_know() -> None:
    """Calibration against the 2026-09-04 folder run — the fixture is the bill itself.

    Seven RU lectures, tier `economy` ($1/$5 per MTok), 52 phases + 7 reconciles = 59
    calls. The gate quoted $2.2394 (882,417 in + 271,400 out) and the run actually cost
    $2.4003 (841,122 in + 311,829 out): the flat model came in at 0.93x, UNDER the bill,
    which is the one direction CLAUDE.md forbids.

    The phase inputs are reconstructed from the gate's own line: the flat model priced
    reconcile input at 4,600 x 52 phases, so the estimator saw 882,417 - 239,200 = 643,217
    tokens of phase input, spread over the real per-file phase counts. Feed that back
    through the new model and the quote must land ABOVE the real bill, and not far above:
    a quote nobody believes gets clicked through as fast as one that undershoots.
    """
    phases_per_file = [6, 7, 6, 7, 8, 6, 12]
    per_phase = round(643_217 / sum(phases_per_file))
    economy = ModelTier(
        name="economy",
        model_id="claude-haiku-4-5",
        context_window=200_000,
        price_in_per_mtok=1.0,
        price_out_per_mtok=5.0,
        output_per_input_ratio=0.39,  # the shipped, measured seed
    )
    quoted = sum(
        cost.estimate_cost_synthesis(
            [per_phase] * k, economy, output_cap=12_288, reconcile_floor=2_500
        ).total_usd
        for k in phases_per_file
    )
    real_bill = 2.4003
    assert quoted > real_bill  # above the bill, unlike the flat model's $2.2394
    assert quoted < real_bill * 1.25  # and still a number the operator can act on


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
