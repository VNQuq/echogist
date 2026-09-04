"""T8 — cost (plan §3 "Cost flow"). Local, offline, killswitch-safe.

Two numbers bracket the paid SUMMARIZE call(s) (TD-16 v2: K phase calls + 1 reconcile,
the reconcile always — it writes the essence block, so K=1 pays for it too):

* **Before** the call(s) — a cost *estimate* (:func:`estimate_cost_synthesis`) from the
  local, language-aware per-phase token guess (:func:`echogist.guard.estimate_input_tokens`)
  on the input side, and a PROPORTIONAL output projection on the output side: each call's
  output is ``tier.output_per_input_ratio × that call's input``, clamped to the API's
  ``max_tokens`` cap (TD-24). Not the cap itself (TD-21: the cap-based ceiling ran ~2.4×
  the bill), and no longer a flat per-call constant (TD-24: flat made the quote depend on
  the phase split, and it undershot the first real folder run by 7%). The input side keeps
  its Cyrillic-high bias, so the quote sits above the bill with a modest margin. Always shown.
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

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import ModelTier
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


def estimate_cost_synthesis(
    phase_input_tokens: Sequence[int],
    tier: ModelTier,
    *,
    output_cap: int,
    reconcile_floor: int,
) -> CostEstimate:
    """Pre-call cost estimate for the v2 direct-synthesis path (TD-16): K phases + reconcile.

    ``phase_input_tokens`` is the per-phase input estimate (one per synthesis call, each
    already including the prompt overhead via :func:`echogist.guard.estimate_input_tokens`).
    ``output_cap`` is ``[summarize].max_output_tokens`` — the API ``max_tokens`` ceiling a
    single reply physically cannot exceed, used here to clamp, never to project.
    ``reconcile_floor`` is ``[summarize].reconcile_output_floor_tokens``, the smallest
    reply the reconcile prompt can produce.

    **The output side is proportional, not flat (TD-24).** Each call is projected at
    ``tier.output_per_input_ratio × its own input``: the model writes prose in proportion
    to the material it is given, so a call over twice the transcript emits roughly twice
    the words. Two properties follow, and both are the point:

    * The quote no longer moves with K. ``sum(ratio × phase_in)`` is ``ratio × sum(phase_in)``
      however the transcript is split, so halving the phase size no longer looks like it
      doubles the output bill. The flat model made the same lecture quote $0.22 at K=4 and
      $0.31 at K=7 for prose that is the same size either way.
    * It stops undershooting. The flat 4,600/call quoted the first real folder run at
      $2.2394 against a $2.4003 bill — 0.93x, the one direction CLAUDE.md forbids. At the
      measured ratio the same run quotes 1.07x.

    A single reconcile call ALWAYS follows the K phase calls — including at K=1, where it
    writes the essence block the document opens with. Its INPUT is the phase prose fed to
    it (≈ the K projected phase outputs) and its own output is the ratio applied to that,
    but never less than ``reconcile_floor``: the reconcile emits a title, an essence block
    and the themes whatever the file's size, so it is the one call with a real FIXED cost.
    Without that floor a purely proportional model has no per-call cost at all, and a
    folder of fifty short clips quotes the same as one long file — the exact under-quote
    :func:`echogist.bulk.folder_estimate` exists to prevent. Above the floor the ratio
    dominates and this term stops mattering.

    The reconcile projection is the loosest part of the model — it prices a header as if it
    were a second synthesis, high, then saturates at ``output_cap``. Deliberate: it errs in
    the safe direction off ONE measured knob. Look here first if a quote runs far above a bill.
    """
    ratio = tier.output_per_input_ratio
    phase_outputs = [min(math.ceil(tokens * ratio), output_cap) for tokens in phase_input_tokens]
    reconcile_input = sum(phase_outputs)
    reconcile_output = min(max(math.ceil(reconcile_input * ratio), reconcile_floor), output_cap)
    return CostEstimate(
        input_tokens=sum(phase_input_tokens) + reconcile_input,
        output_tokens=reconcile_input + reconcile_output,
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
