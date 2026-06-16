"""T10 — summarization quality scorer (plan §8 EVAL lane). Pure, offline.

The eval gate asks one question of a produced :class:`~echogist.summarize.Summary`:
is it a *usable* summary of *this* transcript? The plan's acceptance criteria
(plan §8) are mechanical enough to check without a human:

* **structure present** — every field a reader needs is filled (overview, at least
  one key takeaway, at least one section timecode, at least one recurring theme, a
  core idea, a title);
* **timecodes plausible** — every ``[HH:MM:SS]`` the model attached to a section
  ACTUALLY appears in the transcript. This is the one check that catches a
  hallucinated navigation aid — the prompt forbids inventing timecodes (T6), and
  this is where that promise is verified against the source;
* **language plausible** — a Russian summary is actually written in Cyrillic and an
  English one is not, so a wrong-language reply (the prompt's ``{language}`` ignored)
  fails loud instead of shipping.

This module is the shared bar for both halves of the eval suite: the offline run
(golden fixtures + a stubbed call) and the operator's live run (the real API call,
gated). It is a pure scorer over plain values — no model, no key, no network, so it
imports and runs under the killswitch like every stage left of SUMMARIZE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from echogist.summarize import Summary

# Any Cyrillic letter. Used to sanity-check that an `ru` summary is really written
# in Russian and an `en` one is not — a coarse but reliable language plausibility
# read without pulling a language-detection dependency.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


@dataclass(frozen=True)
class QualityReport:
    """The per-check outcome of evaluating one summary against its transcript.

    ``checks`` maps a human-readable criterion name to pass/fail. ``passed`` is the
    gate: the summary clears the bar only when every check holds. ``failures`` names
    the criteria that did not, so a failing eval prints what was wrong, not just that
    something was.
    """

    checks: dict[str, bool]

    @property
    def passed(self) -> bool:
        """True only if every quality check held."""
        return all(self.checks.values())

    @property
    def failures(self) -> tuple[str, ...]:
        """The names of the checks that failed (empty when :attr:`passed`)."""
        return tuple(name for name, ok in self.checks.items() if not ok)


def _language_plausible(summary: Summary) -> bool:
    """A coarse language read: ru text must contain Cyrillic, en must not.

    Checks the prose fields the model writes freely (title/overview/core_idea), not
    the timecodes (digits, language-agnostic). An unknown language code is not
    constrained — it passes — since settings validation already limits the code to
    ru/en upstream and a hand-edited file should not crash the scorer.
    """
    prose = " ".join((summary.title, summary.overview, summary.core_idea))
    has_cyrillic = bool(_CYRILLIC.search(prose))
    if summary.language == "ru":
        return has_cyrillic
    if summary.language == "en":
        return not has_cyrillic
    return True


def _timecodes_plausible(summary: Summary, transcript: str) -> bool:
    """Every section timecode must appear verbatim in the transcript (no hallucination).

    Requires at least one section AND that each section's timecode is a substring of
    the transcript text — the transcript writes them as ``[HH:MM:SS]`` (T3) and the
    model is told to copy, never invent (T6). A section whose timecode is absent from
    the source is exactly the failure this gate exists to catch.
    """
    if not summary.section_timecodes:
        return False
    return all(marker.timecode in transcript for marker in summary.section_timecodes)


def evaluate_summary(summary: Summary, transcript: str) -> QualityReport:
    """Score ``summary`` against its source ``transcript`` (plan §8 EVAL criteria).

    Pure: returns a :class:`QualityReport` of named checks. The caller decides what
    to do with a failure (the eval suite asserts :attr:`QualityReport.passed`).
    """
    return QualityReport(
        checks={
            "title_present": bool(summary.title.strip()),
            "overview_present": bool(summary.overview.strip()),
            "key_takeaways_present": len(summary.key_takeaways) >= 1,
            "section_timecodes_present": len(summary.section_timecodes) >= 1,
            "recurring_themes_present": len(summary.recurring_themes) >= 1,
            "core_idea_present": bool(summary.core_idea.strip()),
            "timecodes_plausible": _timecodes_plausible(summary, transcript),
            "language_plausible": _language_plausible(summary),
        }
    )
