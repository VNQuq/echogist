"""T10 — summarization quality eval (plan §8 EVAL lane).

Two halves, one bar (:func:`tests.eval_quality.evaluate_summary`):

* **Offline (always runs, killswitch-safe).** Golden RU + EN reference summaries
  (``tests/fixtures/summary_*.json``) are scored against their transcripts and must
  clear the bar; the scorer is shown to *reject* a structurally-broken, a
  hallucinated-timecode, and a wrong-language summary so a green eval means the
  checks have teeth; and the real summarize parser is driven end-to-end through a
  stub caller (no key, no network) to prove a model-shaped reply parses into a
  passing summary. This is the CI gate.

* **Live (opt-in, the operator's eval run).** ``test_live_*`` make the ONE real
  Anthropic call on the RU and EN fixtures and assert the produced summary clears
  the same bar. Skipped unless ``ECHOGIST_LIVE_EVAL`` is set AND a key is present —
  that gate IS the killswitch (CLAUDE.md): the default suite never reaches the wire.
  This is where the first real API call lives (plan §8: "that eval is the gate").
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest

from echogist import config, render, summarize
from echogist.summarize import CallOutcome
from tests import eval_quality
from tests.eval_quality import evaluate_summary

_FIXTURES = Path(__file__).parent / "fixtures"

# (language code, transcript fixture, golden-summary fixture) for each reference.
_CASES = [
    ("ru", "transcript_ru.txt", "summary_ru.json"),
    ("en", "transcript_en.txt", "summary_en.json"),
]


def _transcript(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _golden_summary(name: str) -> summarize.Summary:
    """Load a golden reference summary via the production loader (render.load_summary)."""
    return render.load_summary(_FIXTURES / name)


def _tool_input(name: str) -> dict[str, Any]:
    """The golden summary as the forced tool's JSON input (language is passed apart)."""
    raw: dict[str, Any] = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    raw.pop("language", None)
    return raw


# --------------------------------------------------------------------------- #
# Offline — the golden references clear the bar
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("lang", "transcript_file", "summary_file"), _CASES)
def test_golden_reference_summary_passes(
    lang: str, transcript_file: str, summary_file: str
) -> None:
    report = evaluate_summary(_golden_summary(summary_file), _transcript(transcript_file))
    assert report.passed, f"{lang} golden summary failed checks: {report.failures}"


# --------------------------------------------------------------------------- #
# Offline — the scorer has teeth (it must REJECT bad summaries)
# --------------------------------------------------------------------------- #
def test_rejects_missing_structure() -> None:
    broken = summarize.Summary(
        title="t",
        overview="",  # empty overview
        key_takeaways=(),  # no takeaways
        section_timecodes=(summarize.SectionMarker("[00:00:00]", "Intro"),),
        recurring_themes=(),  # no themes
        core_idea="",  # no core idea
        language="en",
    )
    report = evaluate_summary(broken, _transcript("transcript_en.txt"))
    assert not report.passed
    assert "overview_present" in report.failures
    assert "key_takeaways_present" in report.failures
    assert "recurring_themes_present" in report.failures
    assert "core_idea_present" in report.failures


def test_rejects_hallucinated_timecode() -> None:
    base = _golden_summary("summary_en.json")
    # A timecode that does NOT appear in the transcript — the exact failure the
    # eval exists to catch (the prompt forbids inventing timecodes, T6).
    hallucinated = summarize.Summary(
        **{
            **base.__dict__,
            "section_timecodes": (summarize.SectionMarker("[09:99:99]", "Made up"),),
        }
    )
    report = evaluate_summary(hallucinated, _transcript("transcript_en.txt"))
    assert not report.passed
    assert "timecodes_plausible" in report.failures


def test_rejects_no_sections() -> None:
    base = _golden_summary("summary_en.json")
    no_sections = summarize.Summary(**{**base.__dict__, "section_timecodes": ()})
    report = evaluate_summary(no_sections, _transcript("transcript_en.txt"))
    assert not report.passed
    assert "section_timecodes_present" in report.failures
    assert "timecodes_plausible" in report.failures


def test_rejects_wrong_language() -> None:
    # An English-prose summary labelled `ru` — the {language} instruction was ignored.
    base = _golden_summary("summary_en.json")
    mislabelled = summarize.Summary(**{**base.__dict__, "language": "ru"})
    report = evaluate_summary(mislabelled, _transcript("transcript_en.txt"))
    assert not report.passed
    assert "language_plausible" in report.failures


# --------------------------------------------------------------------------- #
# Offline — a model-shaped reply parses end-to-end into a passing summary
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("lang", "transcript_file", "summary_file"), _CASES)
def test_summarize_with_stub_caller_produces_passing_summary(
    lang: str, transcript_file: str, summary_file: str
) -> None:
    """Drive the real summarize() pipeline with a stub caller that returns the golden
    tool input — exercises build_request + parsing + the F10 path, then scores the
    parsed Summary. Proves a well-formed model reply clears the bar, all offline."""
    tool_input = _tool_input(summary_file)

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        return CallOutcome(
            tool_input=tool_input, stop_reason="tool_use", input_tokens=2000, output_tokens=500
        )

    cfg = config.load_model_config()
    tier = cfg.tier("balanced")
    result = summarize.summarize(
        _transcript(transcript_file),
        tier,
        cfg.summarize,
        language=lang,
        source_stem=transcript_file,
        api_key="sk-test",
        caller=caller,
        log=lambda _m: None,
    )
    report = evaluate_summary(result.summary, _transcript(transcript_file))
    assert report.passed, f"{lang} parsed summary failed checks: {report.failures}"


# --------------------------------------------------------------------------- #
# Killswitch — the scorer imports nothing network at module top level
# --------------------------------------------------------------------------- #
def test_eval_quality_imports_nothing_network_at_top_level() -> None:
    src = Path(eval_quality.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"anthropic", "httpx", "requests", "urllib", "http", "socket", "ssl"}
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"network import at module top: {imported & banned}"


# --------------------------------------------------------------------------- #
# Live — the operator's eval run: the ONE real API call (opt-in, killswitch gate)
# --------------------------------------------------------------------------- #
_LIVE_REASON = (
    "live eval is opt-in: set ECHOGIST_LIVE_EVAL=1 and provide ANTHROPIC_API_KEY "
    "(the killswitch — the default suite never reaches the network)."
)


def _live_enabled() -> bool:
    return bool(os.getenv("ECHOGIST_LIVE_EVAL")) and bool(config.get_api_key())


@pytest.mark.live
@pytest.mark.skipif(not _live_enabled(), reason=_LIVE_REASON)
@pytest.mark.parametrize(("lang", "transcript_file", "summary_file"), _CASES)
def test_live_summary_quality(lang: str, transcript_file: str, summary_file: str) -> None:
    """Make the real structured call on a reference transcript and score the result.

    The actual prompt-quality gate (plan §8): exercises the live prompt + model and
    asserts the produced summary clears the same bar as the golden reference.
    """
    api_key = config.get_api_key()
    assert api_key is not None  # guaranteed by _live_enabled gate
    cfg = config.load_model_config()
    tier = cfg.tier("balanced")
    result = summarize.summarize(
        _transcript(transcript_file),
        tier,
        cfg.summarize,
        language=lang,
        source_stem=transcript_file,
        api_key=api_key,
    )
    report = evaluate_summary(result.summary, _transcript(transcript_file))
    assert report.passed, f"{lang} LIVE summary failed checks: {report.failures}"
