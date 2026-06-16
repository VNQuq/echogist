"""Menu-loop tests (T9).

The menu orchestrates the one network stage but is itself offline: every seam that
touches the GPU, the wire, or ffmpeg is injected, so these run with no model, no
key, no network (killswitch). Coverage: loop navigation + clean exit, each
source×action path returning cleanly, the cost/threshold flow (Enter vs y/N),
settings edit + persistence, the §12 return-to-menu failure modes (F1 bad path,
F3 missing key, F6 overflow, F13 render-after-pay, plus a SummarizeError surface),
and the killswitch (no network import at module top level).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

from echogist import config, menu
from echogist.render import RenderError
from echogist.summarize import SummarizeError, SummarizeResult, Summary
from echogist.transcribe import Segment, Transcript


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _reader(answers: list[str]) -> Callable[[str], str]:
    """A stdin stub: hands back queued answers, then EOFs (the loop exits on EOF)."""
    queue = list(answers)

    def read(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


def _summary() -> Summary:
    return Summary(
        title="Test Summary",
        overview="o",
        key_takeaways=("k",),
        section_timecodes=(),
        recurring_themes=("t",),
        core_idea="c",
        language="ru",
    )


def _transcript() -> Transcript:
    return Transcript(language="ru", duration=1.0, segments=(Segment(0.0, 1.0, "hello"),))


def _make_deps(
    tmp_path: Path,
    answers: list[str],
    *,
    api_key: str | None = "sk-test",
    render_error: bool = False,
    summarize_error: str | None = None,
) -> tuple[menu.Deps, list[str], dict[str, int]]:
    log: list[str] = []
    calls: dict[str, int] = {"extract": 0, "transcribe": 0, "summarize": 0, "render": 0}

    def extract_audio(source: Path, out_dir: Path, *, log: Any = print, **_kw: Any) -> Path:
        calls["extract"] += 1
        return Path(out_dir) / "out.mp3"

    def transcribe(source: Path, model_dir: Path, *, log: Any = print, **_kw: Any) -> Transcript:
        calls["transcribe"] += 1
        return _transcript()

    def summarize(
        text: str,
        tier: Any,
        cfg: Any,
        *,
        language: str,
        source_stem: str,
        api_key: str,
        log: Any = print,
        **_kw: Any,
    ) -> SummarizeResult:
        calls["summarize"] += 1
        if summarize_error is not None:
            raise SummarizeError(summarize_error)
        return SummarizeResult(summary=_summary(), input_tokens=1234, output_tokens=567)

    def render(
        summary: Any,
        out_dir: Path,
        fmt: str,
        *,
        base: str | None = None,
        log: Any = print,
        **_kw: Any,
    ) -> Path:
        calls["render"] += 1
        if render_error:
            raise RenderError("layout blew up")
        return Path(out_dir) / f"{base}.{fmt}"

    deps = menu.Deps(
        reader=_reader(answers),
        log=log.append,
        extract_audio=extract_audio,
        transcribe=transcribe,
        summarize=summarize,
        render=render,
        get_api_key=lambda: api_key,
        base=tmp_path,
        settings_path=tmp_path / "settings.json",
    )
    return deps, log, calls


def _log_text(log: list[str]) -> str:
    return "\n".join(log)


def _write_settings(tmp_path: Path, **overrides: Any) -> None:
    settings = config.default_settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    config.save_settings(settings, tmp_path / "settings.json")


# --------------------------------------------------------------------------- #
# Loop + navigation
# --------------------------------------------------------------------------- #
def test_exit_returns_zero(tmp_path: Path) -> None:
    deps, log, _ = _make_deps(tmp_path, ["4"])
    assert menu.run_menu(deps) == 0
    assert "Goodbye." in _log_text(log)


def test_invalid_choice_reprompts(tmp_path: Path) -> None:
    deps, log, _ = _make_deps(tmp_path, ["9", "4"])
    assert menu.run_menu(deps) == 0
    assert "Please enter 1, 2, 3, or 4." in _log_text(log)


def test_eof_exits_cleanly(tmp_path: Path) -> None:
    # No answers at all -> the first prompt EOFs -> clean exit, no crash.
    deps, _, _ = _make_deps(tmp_path, [])
    assert menu.run_menu(deps) == 0


# --------------------------------------------------------------------------- #
# Source 1 — local file × actions
# --------------------------------------------------------------------------- #
def test_local_file_summary_runs_full_pipeline(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, log, calls = _make_deps(tmp_path, ["1", str(src), "1", "", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1
    assert calls["render"] == 1
    assert calls["extract"] == 0  # summary-only never produced an mp3
    assert "Done — summary written to" in _log_text(log)
    # The transcript checkpoint was saved (the recovery artifact).
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))


def test_local_file_mp3_only_skips_transcribe(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, log, calls = _make_deps(tmp_path, ["1", str(src), "2", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0
    assert "Saved MP3:" in _log_text(log)


def test_local_file_both_extracts_and_summarizes(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["1", str(src), "3", "", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1


def test_mp3_source_not_re_extracted(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, log, calls = _make_deps(tmp_path, ["1", str(src), "2", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0  # already an mp3
    assert "already an MP3" in _log_text(log)


def test_local_file_bad_path_returns_to_menu(tmp_path: Path) -> None:
    deps, log, calls = _make_deps(tmp_path, ["1", str(tmp_path / "nope.wav"), "4"])
    assert menu.run_menu(deps) == 0
    assert "File not found" in _log_text(log)
    assert calls["transcribe"] == 0


# --------------------------------------------------------------------------- #
# Source 2 — saved transcript (the recovery path)
# --------------------------------------------------------------------------- #
def _seed_transcript(tmp_path: Path, text: str = "hello world") -> Path:
    tdir = tmp_path / "output" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / "2026-06-16-clip.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_saved_transcript_pick_by_number(tmp_path: Path) -> None:
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 0  # re-summarize, never re-transcribe
    assert calls["summarize"] == 1
    assert "Done — summary written to" in _log_text(log)


def test_saved_transcript_empty_file(tmp_path: Path) -> None:
    _seed_transcript(tmp_path, text="   ")
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "4"])
    assert menu.run_menu(deps) == 0
    assert "nothing to summarize" in _log_text(log)
    assert calls["summarize"] == 0


# --------------------------------------------------------------------------- #
# Cost / threshold flow (T8 wired through the menu)
# --------------------------------------------------------------------------- #
def test_over_threshold_decline_skips_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)  # any cost needs explicit y/N
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "n", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 0
    assert "cancelled" in _log_text(log).lower()


def test_over_threshold_yes_makes_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "y", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert "Estimated cost" in _log_text(log)
    assert "Actual cost" in _log_text(log)


# --------------------------------------------------------------------------- #
# §12 return-to-menu failure modes
# --------------------------------------------------------------------------- #
def test_missing_api_key_guides_and_skips_call(tmp_path: Path) -> None:  # F3
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "4"], api_key=None)
    assert menu.run_menu(deps) == 0
    assert "ANTHROPIC_API_KEY is not set" in _log_text(log)
    assert calls["summarize"] == 0


def test_overflow_guard_stops_before_call(tmp_path: Path) -> None:  # F6
    _write_settings(tmp_path, model_tier="economy")  # smallest context window
    big = "a" * 600_000  # est tokens > economy safe budget, all local
    _seed_transcript(tmp_path, text=big)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "4"])
    assert menu.run_menu(deps) == 0
    assert "too long" in _log_text(log)
    assert calls["summarize"] == 0  # never reached the wire


def test_render_failure_after_paid_call_keeps_json(tmp_path: Path) -> None:  # F13
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "", "4"], render_error=True)
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1  # the call was paid
    assert "re-render it later" in _log_text(log)
    # The raw result was persisted BEFORE render, so no re-pay is needed.
    assert list((tmp_path / "output" / "summaries" / "raw").glob("*.json"))


def test_summarize_error_returns_to_menu(tmp_path: Path) -> None:  # F2/F4/F5
    _seed_transcript(tmp_path)
    deps, log, calls = _make_deps(tmp_path, ["2", "1", "", "4"], summarize_error="no internet")
    assert menu.run_menu(deps) == 0
    assert "no internet" in _log_text(log)
    assert calls["render"] == 0


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_change_language_persists(tmp_path: Path) -> None:
    deps, log, _ = _make_deps(tmp_path, ["3", "1", "en", "4"])
    assert menu.run_menu(deps) == 0
    assert "Saved." in _log_text(log)
    assert config.load_settings(tmp_path / "settings.json").summary_language == "en"


def test_settings_invalid_value_unchanged(tmp_path: Path) -> None:
    deps, log, _ = _make_deps(tmp_path, ["3", "2", "docx", "4"])
    assert menu.run_menu(deps) == 0
    assert "Unknown format" in _log_text(log)
    # Nothing was written; load falls back to defaults (pdf).
    assert config.load_settings(tmp_path / "settings.json").output_format == "pdf"


def test_settings_bad_threshold_unchanged(tmp_path: Path) -> None:
    deps, log, _ = _make_deps(tmp_path, ["3", "4", "abc", "4"])
    assert menu.run_menu(deps) == 0
    assert "isn't a number" in _log_text(log)


# --------------------------------------------------------------------------- #
# Killswitch — offline at import, no network packages at top level
# --------------------------------------------------------------------------- #
def test_module_imports_nothing_network_at_top_level() -> None:
    src = Path(menu.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"anthropic", "httpx", "requests", "urllib", "http", "socket", "ssl"}
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"network import at module top: {imported & banned}"
