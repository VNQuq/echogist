"""T8 — cost (plan §3 "Cost flow"). Local, offline, killswitch-safe.

Two numbers bracket the one paid SUMMARIZE call:

* **Before** the call — a cost *estimate* from the local, language-aware token
  guess (:func:`echogist.guard.estimate_input_tokens`, already computed by the
  GUARD) on the input side, and a small **fixed absolute** output projection
  (``GuardConfig.output_tokens_estimate`` ≈ 2K) on the output side. Output is a
  constant, not a fraction of input: a summary is roughly the same length
  regardless of transcript size (plan §3, outside-voice #3). Always shown.
* **After** the call — the *actual* cost from the audited ``response.usage``
  counts carried on :class:`echogist.summarize.SummarizeResult`. The exact,
  billable number.

**Threshold friction (plan §3).** Cheap material proceeds on a bare Enter; only
an estimate above the operator's ``confirm_threshold_usd`` demands an explicit
y/n. :func:`confirm_proceed` owns that policy with an injectable ``reader`` so
both sides are unit-testable without stdin (same seam idiom as the summarize
``caller`` / extract ``runner``).

**Killswitch (CLAUDE.md):** this is a LOCAL stage. It computes cost from prices
in the config and token counts handed to it — no ``count_tokens``, no network,
no ``anthropic`` import. The module must import cleanly offline; T8 never reaches
the wire. Pure functions over plain values, so the whole stage is unit-testable
with no model, no key, no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .config import GuardConfig, ModelTier
from .summarize import SummarizeResult

# A reader takes a prompt and returns the operator's typed line. Injectable so
# the confirmation policy is driven offline in tests. Default: the builtin input.
Reader = Callable[[str], str]

_USD_PER_MTOK = 1_000_000  # prices are quoted per million tokens (plan §3)

# Affirmative replies past the threshold. Anything else (incl. a bare Enter)
# means "no" — above the threshold the safe default is NOT to spend.
_YES = frozenset({"y", "yes"})


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
    """
    return CostEstimate(
        input_tokens=est_input_tokens,
        output_tokens=guard.output_tokens_estimate,
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
    reader: Reader = input,
) -> bool:
    """Decide whether to make the paid call, applying the §3 threshold friction.

    Cheap material (estimate ≤ threshold): acknowledge with a bare Enter and
    proceed. Above the threshold: an explicit ``y``/``yes`` is required; anything
    else (including a bare Enter) declines — above budget the safe default is not
    to spend. The transcript is already saved, so declining loses nothing.

    ``reader`` is injectable for tests; it defaults to the builtin ``input``.
    """
    if not requires_explicit_confirmation(estimate, threshold_usd):
        reader(f"Press Enter to summarize ({_format_usd(estimate.total_usd)})... ")
        return True
    answer = reader(
        f"Estimated cost {_format_usd(estimate.total_usd)} exceeds your "
        f"{_format_usd(threshold_usd)} threshold. Summarize anyway? [y/N] "
    )
    return answer.strip().lower() in _YES
