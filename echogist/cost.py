"""T8 — cost (plan §3 "Cost flow"). Local, offline, killswitch-safe.

Two numbers bracket the one paid SUMMARIZE call:

* **Before** the call — a cost *estimate* from the local, language-aware token
  guess (:func:`echogist.guard.estimate_input_tokens`, already computed by the
  GUARD) on the input side, and a **fixed absolute** output projection
  (``GuardConfig.output_tokens_estimate``) on the output side. Output is modelled
  as an absolute, not a fraction of input — but the summary contract is now
  no-upper-limit (all concepts, per-section bullets), so the estimate is sized for
  a dense long summary rather than a short fixed one. Always shown.
* **After** the call — the *actual* cost from the audited ``response.usage``
  counts carried on :class:`echogist.summarize.SummarizeResult`. The exact,
  billable number.

**Threshold friction (plan §3 / v1.1 §5).** Cheap material proceeds with no gate
(the menu shows the estimate as the acknowledgment); only an estimate above the
operator's ``confirm_threshold_usd`` demands an explicit yes. :func:`confirm_proceed`
owns that policy via an injectable ``confirm(prompt, default) -> bool`` callable, so
``cost.py`` stays decoupled from the full :class:`~echogist.ui.UI` Protocol and both
sides are unit-testable without a TTY (the menu wires ``ui.confirm``).

**Killswitch (CLAUDE.md):** this is a LOCAL stage. It computes cost from prices
in the config and token counts handed to it — no ``count_tokens``, no network,
no ``anthropic`` import. The module must import cleanly offline; T8 never reaches
the wire. Pure functions over plain values, so the whole stage is unit-testable
with no model, no key, no network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import GuardConfig, ModelTier
from .summarize import SummarizeResult

# A confirm widget: takes a prompt + a default and returns the operator's yes/no.
# Injectable so the confirmation policy is driven offline in tests; the menu wires
# the UI's arrow-key/confirm prompt. Narrow on purpose — cost stays decoupled from
# the full UI Protocol (v1.1 §5).
Confirm = Callable[[str, bool], bool]

_USD_PER_MTOK = 1_000_000  # prices are quoted per million tokens (plan §3)


@dataclass(frozen=True)
class CostEstimate:
    """A cost breakdown for one SUMMARIZE call — used for both the pre-call
    *estimate* (guessed tokens) and the post-call *actual* (audited usage). The
    prices are the tier's, carried along so the message can name the tier's rate.
    """

    input_tokens: int
    output_tokens: int
    price_in_per_mtok: float
    price_out_per_mtok: float

    @property
    def input_cost_usd(self) -> float:
        """Input-side cost: ``input_tokens`` at the tier's per-MTok input price."""
        return self.input_tokens / _USD_PER_MTOK * self.price_in_per_mtok

    @property
    def output_cost_usd(self) -> float:
        """Output-side cost: ``output_tokens`` at the tier's per-MTok output price."""
        return self.output_tokens / _USD_PER_MTOK * self.price_out_per_mtok

    @property
    def total_usd(self) -> float:
        """Total USD for the call (input + output)."""
        return self.input_cost_usd + self.output_cost_usd


def estimate_cost(
    est_input_tokens: int,
    tier: ModelTier,
    guard: GuardConfig,
) -> CostEstimate:
    """Pre-call cost estimate (plan §3).

    ``est_input_tokens`` is the GUARD's local language-aware estimate (reused, not
    recomputed — :class:`echogist.guard.GuardResult.est_input_tokens`). Output is
    the fixed ``guard.output_tokens_estimate`` absolute. Both priced at the tier's
    rates. Biased high on the input side because the GUARD estimate already is.

    SELF-AUDIT-FIX (FIX-7): the system prompt + tool schema are ALREADY represented
    in ``est_input_tokens`` via the GUARD's flat ``_PROMPT_OVERHEAD_TOKENS`` (1000),
    so the cost estimate already accounts for the prompt. Do NOT add the prompt
    length here again — that would double-count it. (The measured prompt+schema is
    ~1270 tok; the flat overhead's small under-shoot is dwarfed by the +20% body
    bias — see the constant's note in guard.py for why that is harmless.)
    """
    return CostEstimate(
        input_tokens=est_input_tokens,
        output_tokens=guard.output_tokens_estimate,
        price_in_per_mtok=tier.price_in_per_mtok,
        price_out_per_mtok=tier.price_out_per_mtok,
    )


def estimate_cost_chunked(
    map_input_tokens: Sequence[int],
    tier: ModelTier,
    *,
    output_cap: int,
) -> CostEstimate:
    """Pre-call cost estimate for the map-reduce path (TD-5): N map calls + 1 reduce.

    ``map_input_tokens`` is the per-chunk input estimate (one per map call, each already
    including the prompt overhead via :func:`echogist.guard.estimate_input_tokens`).

    A true CEILING, not an expected value — the operator approves this number, so it must
    never sit below the bill (the project's "estimate high" rule, applied to the expensive
    output side). Every one of the N+1 calls can emit up to ``output_cap`` (the request's
    ``max_tokens``); on a no-upper-limit summary contract a dense chunk really can approach
    it, and output is priced ~5× input. So each call's output is projected at the full cap,
    and the reduce call's INPUT (the merged map outputs fed to synthesis) is bounded by the
    N map outputs, i.e. ``N × output_cap`` — both at the ceiling rather than the smaller
    ``output_tokens_estimate``. The exact cost still comes from the summed ``response.usage``
    after the calls; this only governs the pre-call quote the operator confirms against.
    """
    n = len(map_input_tokens)
    reduce_input = n * output_cap  # merged map outputs fed to synthesis, at the ceiling
    total_input = sum(map_input_tokens) + reduce_input
    total_output = (n + 1) * output_cap  # N maps + 1 reduce, each capped at output_cap
    return CostEstimate(
        input_tokens=total_input,
        output_tokens=total_output,
        price_in_per_mtok=tier.price_in_per_mtok,
        price_out_per_mtok=tier.price_out_per_mtok,
    )


def actual_cost(result: SummarizeResult, tier: ModelTier) -> CostEstimate:
    """Post-call actual cost from the audited ``response.usage`` counts (plan §3).

    The exact billable number: ``result.input_tokens`` / ``result.output_tokens``
    are the real usage the SDK returned, priced at the same tier rates.
    """
    return CostEstimate(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        price_in_per_mtok=tier.price_in_per_mtok,
        price_out_per_mtok=tier.price_out_per_mtok,
    )


def _format_usd(amount: float) -> str:
    """USD to 4 decimals — sub-cent calls (a short, cheap transcript) still show a
    non-zero figure instead of rounding to ``$0.00``."""
    return f"${amount:,.4f}"


def estimate_message(estimate: CostEstimate, tier: ModelTier) -> str:
    """The always-shown pre-call line: estimated total + the token breakdown."""
    return (
        f"Estimated cost: {_format_usd(estimate.total_usd)} on tier '{tier.name}' "
        f"(~{estimate.input_tokens:,} input + ~{estimate.output_tokens:,} output tokens). "
        f"This is a high estimate; the actual cost is shown after the call."
    )


def actual_message(actual: CostEstimate) -> str:
    """The post-call line: the exact billed cost from ``response.usage``."""
    return (
        f"Actual cost: {_format_usd(actual.total_usd)} "
        f"({actual.input_tokens:,} input + {actual.output_tokens:,} output tokens)."
    )


def requires_explicit_confirmation(estimate: CostEstimate, threshold_usd: float) -> bool:
    """True if the estimate is over ``threshold_usd`` — the menu must ask y/n.

    At or below the threshold the call is "cheap material": a bare Enter proceeds
    (plan §3). Pure predicate, so both threshold sides are testable in isolation.
    """
    return estimate.total_usd > threshold_usd


def confirm_proceed(
    estimate: CostEstimate,
    threshold_usd: float,
    *,
    confirm: Confirm,
) -> bool:
    """Decide whether to make the paid call, applying the §3 threshold friction.

    Cheap material (estimate ≤ threshold): proceeds with no gate — the estimate the
    menu already showed is the acknowledgment, faithful to today's unconditional
    proceed (v1.1 §5; a y/N widget here would let the operator decline a cheap call
    that always ran). Above the threshold: ``confirm`` is asked with ``default=False``,
    so a bare Enter declines — above budget the safe default is not to spend. The
    transcript is already saved, so declining loses nothing.

    ``confirm`` is injectable for tests; the menu wires the UI's confirm prompt.
    """
    if not requires_explicit_confirmation(estimate, threshold_usd):
        return True
    return confirm(
        f"Estimated cost {_format_usd(estimate.total_usd)} exceeds your "
        f"{_format_usd(threshold_usd)} threshold. Summarize anyway?",
        False,
    )
