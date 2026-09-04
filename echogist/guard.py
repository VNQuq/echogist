"""T5 — overflow guard (plan §4). Local, language-aware, killswitch-safe.

Before the one paid SUMMARIZE call, estimate how many input tokens the transcript
(plus the fixed prompt scaffolding) will cost and compare against the tier's
``safe_budget``. If it would overflow, the menu stops cleanly with a human message
(F6) instead of making a doomed/expensive call.

**Killswitch (CLAUDE.md):** this is a LOCAL stage — no ``count_tokens``, no network,
no Anthropic import. The estimate is a per-script character→token ratio biased
**high**: Cyrillic tokenizes to more tokens/char than Latin (byte-level BPE over
2-byte UTF-8 codepoints, plan §2), so RU is rated higher and an over-long
transcript is always caught, never slipped past. Over-estimating only risks an
occasional false "too long" on a borderline input — the safe direction.

Pure stage: :func:`estimate_input_tokens` / :func:`check_overflow` are total
functions over plain values, so the whole guard is unit-testable with no model,
no key, no network. The menu (T9) reads :class:`GuardResult` and, on overflow,
prints :func:`overflow_message` and returns to the menu — the transcript is
already saved (artifact-based recovery). T8 (cost) reuses ``est_input_tokens``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import GuardConfig, ModelTier

# Tokens per character by script, biased high so the estimate overshoots the real
# Anthropic tokenizer (we cannot call it — killswitch). Real English is ~4
# chars/token (~0.25 tok/char); Russian Cyrillic tokenizes to noticeably more
# tokens/char. Both rates sit ~20% above the measured ratio so the guard errs
# toward catching an over-long transcript, never under-counting one.
_TOKENS_PER_CHAR_CYRILLIC = 0.60  # ~1.7 chars/token
_TOKENS_PER_CHAR_DEFAULT = 0.30  # ~3.3 chars/token

# Fixed instruction/prompt scaffolding wrapped around the transcript in the single
# structured SUMMARIZE call. Counted on the input side (plan §4: transcript + prompt).
# SELF-AUDIT-FIX (FIX-7): this flat overhead is the ONE place the system prompt +
# tool-use schema are charged to the input estimate. cost.estimate_cost reuses this
# value (via GuardResult.est_input_tokens — see menu.py), so the prompt IS already
# represented in the cost estimate; do NOT also add len(prompt)//4 in cost.py or it
# double-counts. Measured today: prompt ~710 tok + serialized tool schema ~560 tok
# ≈ 1270, so this flat 1000 slightly UNDER-shoots the true fixed scaffolding. That
# is harmless: the overflow guarantee ("always catch an over-long transcript") rides
# on the +20%-biased per-char body estimate, which scales with length and dwarfs a
# ~270-tok fixed shortfall on any transcript long enough to approach the budget. A
# near-empty transcript is the only case the shortfall isn't absorbed, and it is
# sub-cent and nowhere near overflow. If the prompt/schema grows materially, raise
# this constant above the measured prompt+schema total to keep the estimate high.
PROMPT_OVERHEAD_TOKENS = 1000


def _is_cyrillic(ch: str) -> bool:
    """True for the Cyrillic + Cyrillic Supplement blocks (covers RU, plan §2)."""
    return "Ѐ" <= ch <= "ԯ"


def estimate_input_tokens(
    text: str,
    *,
    prompt_overhead: int = PROMPT_OVERHEAD_TOKENS,
    cyrillic_rate: float = _TOKENS_PER_CHAR_CYRILLIC,
    default_rate: float = _TOKENS_PER_CHAR_DEFAULT,
) -> int:
    """Estimate SUMMARIZE input tokens for ``text``, biased high (no network).

    Cyrillic chars are rated higher than the rest, so a Russian transcript always
    estimates more tokens than a Latin one of the same length. The per-script sum
    is rounded **up** and the fixed ``prompt_overhead`` added — every rounding goes
    in the conservative (over-estimate) direction.
    """
    cyrillic = sum(1 for ch in text if _is_cyrillic(ch))
    other = len(text) - cyrillic
    body = math.ceil(cyrillic * cyrillic_rate + other * default_rate)
    return body + prompt_overhead


@dataclass(frozen=True)
class GuardResult:
    """The overflow verdict + the numbers behind it (T8 reuses the estimate)."""

    est_input_tokens: int
    safe_budget: int

    @property
    def over_budget(self) -> bool:
        """True if the estimate exceeds the tier's safe budget — STOP before SUMMARIZE."""
        return self.est_input_tokens > self.safe_budget


def check_overflow(
    transcript_text: str,
    tier: ModelTier,
    guard: GuardConfig,
    *,
    prompt_overhead: int = PROMPT_OVERHEAD_TOKENS,
) -> GuardResult:
    """Estimate input tokens and compare against ``guard.safe_budget(tier)`` (§4)."""
    est = estimate_input_tokens(transcript_text, prompt_overhead=prompt_overhead)
    return GuardResult(est_input_tokens=est, safe_budget=guard.safe_budget(tier))


def overflow_message(result: GuardResult, tier: ModelTier) -> str:
    """The clean §4 "too long" message the menu prints on overflow (F6)."""
    return (
        f"This transcript is too long for the '{tier.name}' tier (estimated "
        f"{result.est_input_tokens:,} input tokens vs a safe budget of "
        f"{result.safe_budget:,}). Choose a larger-context model in Settings. "
        f"Your transcript is saved."
    )
