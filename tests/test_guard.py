"""Overflow-guard tests (T5).

The guard is a pure, offline stage — these run with no model, no key, no network
(killswitch). Coverage: the language-aware estimate is biased high and rates RU
above EN, the per-script + ceil + overhead arithmetic, and the F6 under/over
verdict against a tier's safe budget.
"""

from __future__ import annotations

from echogist import guard
from echogist.config import GuardConfig, ModelTier


def _tier(context_window: int) -> ModelTier:
    return ModelTier(
        name="test",
        model_id="test-model",
        context_window=context_window,
        price_in_per_mtok=1.0,
        price_out_per_mtok=5.0,
    )


def _guard(fraction: float = 0.8) -> GuardConfig:
    return GuardConfig(safe_budget_fraction=fraction)


# --------------------------------------------------------------------------- #
# estimate_input_tokens
# --------------------------------------------------------------------------- #
def test_estimate_empty_is_just_overhead() -> None:
    assert guard.estimate_input_tokens("", prompt_overhead=0) == 0
    assert guard.estimate_input_tokens("", prompt_overhead=1000) == 1000


def test_estimate_latin_rate() -> None:
    # 100 latin chars * 0.30 = 30, + 0 overhead.
    assert guard.estimate_input_tokens("a" * 100, prompt_overhead=0) == 30


def test_estimate_cyrillic_rated_higher_than_latin() -> None:
    # Same length, Cyrillic must estimate MORE tokens (plan §2 — estimate RU high).
    ru = guard.estimate_input_tokens("я" * 100, prompt_overhead=0)
    en = guard.estimate_input_tokens("a" * 100, prompt_overhead=0)
    assert ru > en
    assert ru == 60  # 100 * 0.60


def test_estimate_mixed_script_sums_per_script() -> None:
    # 50 cyrillic (*0.60=30) + 50 latin (*0.30=15) = 45.
    text = "я" * 50 + "a" * 50
    assert guard.estimate_input_tokens(text, prompt_overhead=0) == 45


def test_estimate_rounds_up_conservative() -> None:
    # 101 * 0.30 = 30.3 -> ceil 31 (never round down past the budget).
    assert guard.estimate_input_tokens("a" * 101, prompt_overhead=0) == 31


def test_estimate_adds_prompt_overhead() -> None:
    assert guard.estimate_input_tokens("a" * 100, prompt_overhead=1000) == 1030


def test_estimate_default_overhead_is_applied() -> None:
    # Default prompt_overhead is non-zero, so even an empty transcript costs tokens.
    assert guard.estimate_input_tokens("") >= 1000


# --------------------------------------------------------------------------- #
# GuardResult / check_overflow
# --------------------------------------------------------------------------- #
def test_under_budget_is_not_over() -> None:
    result = guard.check_overflow("a" * 100, _tier(200_000), _guard())
    assert not result.over_budget
    assert result.safe_budget == 160_000


def test_over_budget_trips_the_guard() -> None:
    # Tiny context window forces overflow on a short transcript (F6).
    result = guard.check_overflow("a" * 1000, _tier(100), _guard())
    assert result.over_budget
    assert result.safe_budget == 80
    assert result.est_input_tokens > 80


def test_check_overflow_uses_estimate_and_budget() -> None:
    result = guard.check_overflow("a" * 100, _tier(1000), _guard(0.5))
    # est = 100*0.30 + 1000 overhead = 1030; budget = 1000*0.5 = 500.
    assert result.est_input_tokens == 1030
    assert result.safe_budget == 500
    assert result.over_budget


def test_cyrillic_can_overflow_where_latin_fits() -> None:
    # Same length transcript: the guard catches the RU one but passes the EN one,
    # proving the conservative-high RU bias actually changes the verdict.
    # EN est = 100*0.30+1000 = 1030; RU est = 100*0.60+1000 = 1060.
    # cw 1307 -> safe_budget int(1307*0.8) = 1045, which sits between the two.
    tier = _tier(context_window=1307)
    cfg = _guard()
    en = guard.check_overflow("a" * 100, tier, cfg)
    ru = guard.check_overflow("я" * 100, tier, cfg)
    assert not en.over_budget
    assert ru.over_budget


# --------------------------------------------------------------------------- #
# overflow_message
# --------------------------------------------------------------------------- #
def test_overflow_message_is_human_and_names_numbers() -> None:
    tier = _tier(100)
    result = guard.check_overflow("a" * 1000, tier, _guard())
    msg = guard.overflow_message(result, tier)
    assert "too long" in msg.lower()
    assert "test" in msg  # tier name
    assert f"{result.est_input_tokens:,}" in msg
    assert f"{result.safe_budget:,}" in msg
    assert "saved" in msg.lower()  # reassures the artifact is kept


def test_every_non_latin_script_is_rated_above_latin() -> None:
    """The split was Cyrillic vs everything-else-is-Latin, and Whisper auto-detects.

    Greek, Devanagari, Arabic and Hangul were all rated at the LATIN rate, a 2-3x
    under-count on the scripts that tokenize worst — in the one direction CLAUDE.md
    forbids, where the guard lets an over-long transcript through and the gate quotes low.
    """
    latin = guard.estimate_input_tokens("a" * 10_000, prompt_overhead=0)

    for text in ("я" * 10_000, "α" * 10_000, "क" * 10_000, "ع" * 10_000, "한" * 10_000):
        assert guard.estimate_input_tokens(text, prompt_overhead=0) > latin, text
    # CJK is the dense end and is rated above Cyrillic again.
    assert guard.estimate_input_tokens(
        "字" * 10_000, prompt_overhead=0
    ) > guard.estimate_input_tokens("я" * 10_000, prompt_overhead=0)
