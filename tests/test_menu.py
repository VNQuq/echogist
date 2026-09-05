"""Menu-loop tests (T9), migrated to the UI seam (v1.1 T6).

The menu orchestrates the one network stage but is itself offline: the UI is a scripted
:class:`~echogist.ui.StubUI` (no TTY, no rich, no network) and every heavy seam is
injected, so these run with no model, no key, no network (killswitch). Coverage: loop
navigation + clean exit, each source×action path returning cleanly, the cost/threshold
flow (cheap proceeds with no gate / above-threshold confirm), settings edit + persistence,
the §12 return-to-menu failure modes (F1 bad path, F3 missing key, F6 overflow, F13
render-after-pay, plus a SummarizeError surface), and the killswitch (no network import
at module top level).

Arrow-key surfaces are now ``select``: the main menu, the action, the transcript pick,
and the settings enums return a value the operator chose from a list, so an invalid
choice is structurally impossible (no reprompt test). ``StubUI`` pops queued answers in
order — a string for ``select``/``text``, a bool for ``confirm`` — and EOFs (clean exit)
when the queue empties.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from echogist import config, extract, folder, menu, naming, scan, summarize
from echogist.extract import ExtractError
from echogist.render import RenderError
from echogist.summarize import SummarizeError, SummarizeResult, Summary, SynthesisSection
from echogist.transcribe import Segment, TranscribeError, Transcript
from echogist.ui import (
    REVEAL_AUDIO,
    REVEAL_SUMMARY,
    REVEAL_TRANSCRIPT,
    Choice,
    RichQuestionaryUI,
    StubUI,
)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _summary() -> Summary:
    return Summary(
        title="Test Summary",
        core_idea="c",
        decisions=(),
        action_items=(),
        language="ru",
        synthesis=(SynthesisSection("H", "prose", ("[00:00:00]",)),),
        main_themes=("t",),
    )


def _transcript() -> Transcript:
    return Transcript(language="ru", duration=1.0, segments=(Segment(0.0, 1.0, "hello"),))


def _make_deps(
    tmp_path: Path,
    answers: list[Any],
    *,
    api_key: str | None = "sk-test",
    render_error: bool = False,
    summarize_error: str | None = None,
    extract_error: bool = False,
    extract_oserror: bool = False,
    convert_many: Any = None,
) -> tuple[menu.Deps, StubUI, dict[str, int]]:
    calls: dict[str, int] = {"extract": 0, "transcribe": 0, "summarize": 0, "render": 0}

    def extract_audio(source: Path, out_dir: Path, *, log: Any = print, **_kw: Any) -> Path:
        calls["extract"] += 1
        if extract_oserror:
            # extract_audio raises bare OSError (mkdir / os.replace into a locked dir), not
            # only ExtractError — the degrade path must treat both the same (red-team finding).
            raise OSError("output/audio is locked")
        if extract_error:
            raise ExtractError("ffmpeg fell over")
        # Write a real file so a test can assert the MP3 actually lands in output/audio
        # (TD-12: MP3 is the baseline kept on every video branch).
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        mp3 = out / "out.mp3"
        mp3.write_bytes(b"id3")
        return mp3

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
        notice: Any = print,
        **_kw: Any,
    ) -> SummarizeResult:
        calls["summarize"] += 1
        if summarize_error is not None:
            raise SummarizeError(summarize_error)
        # The real stage narrates each phase through `log`; mirror that so a test can see
        # what the operator would have been told while the call was in flight. ``notice``
        # is taken EXPLICITLY, not through **_kw: SummarizeFn is Callable[..., ...], so an
        # unwired channel would be invisible to mypy and swallowed by the catch-all — the
        # suite would stay green while every finding printed to a dead end.
        log("Synthesizing phase 1/2 (00:00:00-00:30:00)...")
        log("Synthesizing phase 2/2 (00:30:00-01:00:00)...")
        notice("Validated anchors: 8 exact, 0 snapped, 2 dropped.")
        return SummarizeResult(summary=_summary(), input_tokens=1234, output_tokens=567)

    def render(
        summary: Any,
        out_dir: Path,
        fmt: str,
        *,
        base: str | None = None,
        log: Any = print,
        notice: Any = print,
        **_kw: Any,
    ) -> Path:
        calls["render"] += 1
        if render_error:
            raise RenderError("layout blew up")
        # TD-28: the real stage announces a character the PDF font cannot draw here.
        # ``notice`` is taken EXPLICITLY for the same reason as in the summarize stub —
        # RenderFn is Callable[..., Path], so an unwired channel would pass mypy and be
        # swallowed by **_kw, and the suite would stay green while the warning went
        # nowhere. Emitted unconditionally so a test can see WHERE it landed.
        notice("The PDF font cannot draw 1 character ('催').")
        return Path(out_dir) / f"{base}.{fmt}"

    stub = StubUI(answers)
    deps = menu.Deps(
        ui=stub,
        extract_audio=extract_audio,
        convert_many=convert_many or folder.convert_many,
        transcribe=transcribe,
        summarize=summarize,
        render=render,
        get_api_key=lambda: api_key,
        base=tmp_path,
        settings_path=tmp_path / "settings.json",
    )
    return deps, stub, calls


def _write_settings(tmp_path: Path, **overrides: Any) -> None:
    settings = config.default_settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    config.save_settings(settings, tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def isolate_last_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    """Keep the TD-10 picker's state IO off the real ``config/state.json`` in unit
    tests, and record every ``save_last_dir`` call. The menu calls these via the
    module (``config.save_last_dir`` etc.), so patching the module attrs is enough.
    Returns the list of saved directories for assertions."""
    saved: list[Path] = []
    monkeypatch.setattr(config, "load_last_dir", lambda *a, **k: None)
    monkeypatch.setattr(config, "resolve_initial_dir", lambda last: tmp_path)
    monkeypatch.setattr(config, "save_last_dir", lambda d, *a, **k: saved.append(d))
    return saved


# --------------------------------------------------------------------------- #
# Loop + navigation
# --------------------------------------------------------------------------- #
def test_exit_returns_zero(tmp_path: Path) -> None:
    deps, stub, _ = _make_deps(tmp_path, ["exit"])
    assert menu.run_menu(deps) == 0
    assert "Goodbye." in stub.log_text


def test_the_menu_drains_typed_ahead_input_after_every_flow(tmp_path: Path) -> None:
    """A flow can hold the console for an hour with no prompt on screen. Anything typed
    into that silence must be dropped before the menu re-opens, or the menu answers itself
    with it — the 2026-09-04 folder run finished seven summaries and then launched an
    unasked-for folder picker off a buffered keystroke. Drained after the flow RETURNS, so
    type-ahead into a prompt the operator can actually see still works.
    """
    deps, stub, _ = _make_deps(tmp_path, ["settings", "back", "exit"])
    assert menu.run_menu(deps) == 0
    drains = [i for i, (level, _) in enumerate(stub.messages) if level == "drain"]
    selects = [i for i, (level, _) in enumerate(stub.messages) if level == "select"]
    assert drains, "a finished flow must drain the input buffer before re-asking"
    # The drain lands between the flow's last prompt and the menu's next one.
    assert any(selects[0] < d < selects[-1] for d in drains)


def test_eof_exits_cleanly(tmp_path: Path) -> None:
    # No answers at all -> the first menu select EOFs -> clean exit, no crash.
    deps, _, _ = _make_deps(tmp_path, [])
    assert menu.run_menu(deps) == 0


# --------------------------------------------------------------------------- #
# Source 1 — local file × actions
# --------------------------------------------------------------------------- #
def test_local_file_summary_runs_full_pipeline(tmp_path: Path) -> None:
    # TD-12 regression: a video Summary now ALSO extracts + keeps the MP3 (the baseline),
    # on top of transcribe → summarize → render.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "summary", True, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1
    assert calls["render"] == 1
    assert calls["extract"] == 1  # keep-MP3 confirmed (default), so the MP3 is saved
    assert "Done — summary written to" in stub.log_text
    # The MP3 baseline landed in output/audio, and the transcript checkpoint was saved.
    assert list((tmp_path / "output" / "audio").glob("*.mp3"))
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))


def test_local_file_mp3_only_skips_transcribe(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "mp3", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0
    assert "Saved MP3:" in stub.log_text


def test_local_file_transcript_keeps_mp3_and_skips_summarize(tmp_path: Path) -> None:
    # TD-12: a video Transcript run extracts + keeps the MP3 (baseline), transcribes and
    # saves the checkpoint, and STOPS before the paid summary (the new gap-closing path).
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "transcript", True, "exit"]
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1  # keep-MP3 confirmed (default), so the MP3 is saved
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 0  # stops before paying for a summary
    assert list((tmp_path / "output" / "audio").glob("*.mp3"))
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))
    # The video Transcript branch reveals the transcripts folder (the deliverable here).
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("transcripts")


def test_mp3_source_summary(tmp_path: Path) -> None:
    # TD-12: an mp3 input is offered a trimmed menu (no extract step); picking Summary
    # runs the full transcribe → summarize pipeline without ever producing an mp3.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "summary", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0  # already an mp3 — nothing to extract
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1
    assert "already an MP3" in stub.log_text
    # The trimmed action menu WAS shown (mp3 still chooses transcript vs summary).
    assert ("select", "What should EchoGist produce?") in stub.messages


def test_mp3_source_transcript_only(tmp_path: Path) -> None:
    # TD-12: picking "Transcript only" for an mp3 saves the checkpoint and stops — no
    # extract, no network summarize call.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["single", "file", str(src), "transcript", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 0  # transcript-only never touches the network
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))


def test_mp3_source_reencode(tmp_path: Path) -> None:
    # Operator request (reverses TD-12): an mp3 source can be re-encoded to a smaller VBR mp3.
    # It runs the SAME extraction path as a video MP3-only, never transcribes/summarizes, and
    # pops the audio folder. The progress label distinguishes a re-encode from a conversion.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "mp3", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1  # the mp3 is re-encoded
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0
    assert ("progress", "Re-encoding MP3") in stub.messages  # not "Converting to MP3"
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1 and reveals[0][1].endswith("audio")


def test_mp3_source_reencode_failure_is_fatal(tmp_path: Path) -> None:
    # Re-encode IS the deliverable, so a failed extract is fatal — it aborts to the menu
    # (like a video MP3-only), never silently degrades.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "mp3", "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 0
    assert "Returning to the main menu" in stub.log_text  # bubbled to the loop handler


def test_local_file_action_back_returns_to_menu(tmp_path: Path) -> None:
    # TD-13: "← Back" from the action menu does nothing and returns to the main menu.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["single", "file", str(src), "__back__", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0


def test_video_summary_extract_failure_degrades(tmp_path: Path) -> None:
    # TD-12 Issue 1: on a video Summary, a failed MP3 extract DEGRADES — warn + continue
    # to transcribe + summarize (the paid deliverable outranks the audio artifact).
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "summary", True, "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1  # attempted
    assert calls["transcribe"] == 1  # but the run continued
    assert calls["summarize"] == 1
    assert "Couldn't save the MP3" in stub.log_text
    assert "Returning to the main menu" not in stub.log_text  # degraded, did not abort


def test_video_summary_extract_oserror_degrades(tmp_path: Path) -> None:
    # Red-team finding: extract_audio raises bare OSError (mkdir / os.replace into a locked
    # output/audio — realistic on Windows), not only ExtractError. Both are in _RECOVERABLE,
    # so the degrade path must catch OSError too or it would abort the whole run before
    # transcription, destroying the primary deliverable the DEGRADE contract must preserve.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "summary", True, "exit"], extract_oserror=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1  # attempted
    assert calls["transcribe"] == 1  # degraded + continued, did NOT abort to menu
    assert calls["summarize"] == 1
    assert "Couldn't save the MP3" in stub.log_text
    assert "Returning to the main menu" not in stub.log_text


def test_video_transcript_extract_failure_degrades(tmp_path: Path) -> None:
    # TD-12 Issue 1: the Transcript branch shares the degrade block — a failed MP3 extract
    # warns and continues to transcribe + reveal transcripts, never aborts.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "transcript", True, "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 1  # continued
    assert calls["summarize"] == 0  # still transcript-only
    assert "Couldn't save the MP3" in stub.log_text
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1 and reveals[0][1].endswith("transcripts")


def test_video_mp3_only_extract_failure_is_fatal(tmp_path: Path) -> None:
    # TD-12 Issue 1: MP3-only stays FATAL — extraction is the deliverable, so a failure
    # aborts to the menu (nothing else to produce), never silently degrades.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "mp3", "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 0  # nothing else ran
    assert "Returning to the main menu" in stub.log_text  # bubbled to the loop handler


def test_video_summary_keep_mp3_declined_skips_extract(tmp_path: Path) -> None:
    # Operator request #3: the keep-MP3 confirm is checked by default; declining it (False)
    # skips the conversion entirely on a Summary run — no extract call, no MP3 on disk — while
    # the primary summary pipeline still runs.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "summary", False, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0  # opted out of keeping the MP3
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1
    assert not list((tmp_path / "output" / "audio").glob("*.mp3"))  # no MP3 kept
    assert ("confirm", "Also save the converted MP3?") in stub.messages


def test_video_transcript_keep_mp3_declined_skips_extract(tmp_path: Path) -> None:
    # The same opt-out applies to a Transcript run: decline → no conversion, transcript still
    # produced.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["single", "file", str(src), "transcript", False, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 0
    assert not list((tmp_path / "output" / "audio").glob("*.mp3"))


def test_mp3_source_summary_never_asks_keep_mp3(tmp_path: Path) -> None:
    # An mp3 source has nothing to extract, so the keep-MP3 confirm must NOT appear (it would
    # be a meaningless prompt). The trimmed flow goes straight to transcribe → summarize.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(src), "summary", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["summarize"] == 1
    assert ("confirm", "Also save the converted MP3?") not in stub.messages


def test_flow_clears_screen_on_entry(tmp_path: Path) -> None:
    # TD-11: each flow clears the console on entry so prior menu chrome doesn't pile up.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(src), "summary", True, "exit"])
    assert menu.run_menu(deps) == 0
    assert ("clear", "") in stub.messages


def test_summary_reveals_summaries_folder_once(tmp_path: Path) -> None:
    # TD-14 (reopened): a Summary run reveals the SUMMARIES folder (never transcripts),
    # and only once per launch even across two summary flows.
    a = tmp_path / "a.wav"
    a.write_bytes(b"x")
    b = tmp_path / "b.wav"
    b.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path,
        [
            "single",
            "file",
            str(a),
            "summary",
            True,
            "single",
            "file",
            str(b),
            "summary",
            True,
            "exit",
        ],
    )
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1  # once per launch, not once per summary
    assert reveals[0][1].endswith("summaries")
    assert not any(r[1].endswith("transcripts") for r in reveals)  # summary never pops transcripts


def test_saved_transcript_resummarize_reveals_summaries(tmp_path: Path) -> None:
    # TD-14 (reopened): re-summarizing an existing transcript produces a summary, so the
    # SUMMARIES folder pops (the deliverable) — the transcripts folder never does.
    _seed_transcript(tmp_path)
    deps, stub, _ = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("summaries")


def test_transcript_only_reveals_transcripts(tmp_path: Path) -> None:
    # TD-12 (Issue 2, reverses TD-14): a transcript-only run NOW pops the transcripts
    # folder — the transcript is the deliverable here, so it is the reveal target.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(src), "transcript", "exit"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("transcripts")


def test_mp3_conversion_drives_progress_bar(tmp_path: Path) -> None:
    # Bug #1: the video→MP3 conversion runs behind a %/ETA bar (was a frozen log line).
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(src), "mp3", "exit"])
    assert menu.run_menu(deps) == 0
    assert ("progress", "Converting to MP3") in stub.messages


def test_successful_stage_completes_progress_bar(tmp_path: Path) -> None:
    # TD-17 baseline: a clean run completes its bar and never records a failure.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(src), "mp3", "exit"])
    assert menu.run_menu(deps) == 0
    assert "fail" not in stub.progress_events
    assert "done" in stub.progress_events


def test_failed_stage_fails_progress_bar_not_completes_it(tmp_path: Path) -> None:
    # TD-17 (fatal path): when the conversion stage raises, its bar is FAILED (stopped at its
    # last fraction), never snapped to a false 100% just before the error panel. MP3-only is
    # fatal, so exactly one bar runs and it fails.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path, ["single", "file", str(src), "mp3", "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert stub.progress_events == ["fail"]  # failed, never "done"
    assert "Returning to the main menu" in stub.log_text  # still aborted to the menu


def test_degraded_stage_fails_its_bar_then_next_bar_completes(tmp_path: Path) -> None:
    # TD-17 (degrade path, shared handle): on a video Summary a failed MP3 extract DEGRADES —
    # its conversion bar fails, then the transcription bar that follows completes normally. The
    # fix is per-bar, so one failure never taints the next bar. (Default auto-accept → no beat.)
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "file", str(src), "summary", "exit"], extract_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1  # degraded + continued
    assert stub.progress_events[0] == "fail"  # the conversion bar failed
    assert "fail" not in stub.progress_events[1:]  # no later bar failed
    assert "done" in stub.progress_events  # the transcription bar completed


def test_mp3_only_reveals_audio_folder(tmp_path: Path) -> None:
    # Bug #2: an MP3-only run pops the audio folder (the chosen flow's output), not the
    # transcripts folder, and only once per launch.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(src), "mp3", "exit"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("audio")


def test_summary_reveal_supersedes_earlier_audio(tmp_path: Path) -> None:
    # TD-14 (reopened): an MP3-only run pops audio; a later Summary run in the same launch
    # still pops summaries (REVEAL_SUMMARY outranks the once-per-launch audio guard).
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    wav = tmp_path / "b.wav"
    wav.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path, ["single", "file", str(wav), "mp3", "single", "file", str(mp3), "summary", "exit"]
    )
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 2
    assert reveals[0].endswith("audio")
    assert reveals[1].endswith("summaries")


def test_summary_reveal_supersedes_earlier_transcript(tmp_path: Path) -> None:
    # TD-12 priority: a transcript-only run pops transcripts (priority 2); a later Summary
    # still pops summaries (REVEAL_SUMMARY=3 outranks REVEAL_TRANSCRIPT=2 for the launch).
    a = tmp_path / "a.mp3"
    a.write_bytes(b"x")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path,
        ["single", "file", str(a), "transcript", "single", "file", str(b), "summary", "exit"],
    )
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 2
    assert reveals[0].endswith("transcripts")
    assert reveals[1].endswith("summaries")


def test_transcript_reveal_supersedes_earlier_audio(tmp_path: Path) -> None:
    # TD-12 priority: an MP3-only run pops audio (priority 1); a later transcript-only run
    # still pops transcripts (REVEAL_TRANSCRIPT=2 outranks REVEAL_AUDIO=1 for the launch).
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    mp3 = tmp_path / "b.mp3"
    mp3.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path,
        ["single", "file", str(wav), "mp3", "single", "file", str(mp3), "transcript", "exit"],
    )
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 2
    assert reveals[0].endswith("audio")
    assert reveals[1].endswith("transcripts")


def test_audio_not_revealed_after_summary(tmp_path: Path) -> None:
    # TD-14 (reopened): once summaries has popped, a later MP3-only run does not pop audio
    # (summaries outranks audio for the whole launch).
    wav_a = tmp_path / "a.wav"
    wav_a.write_bytes(b"x")
    wav_b = tmp_path / "b.wav"
    wav_b.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path,
        [
            "single",
            "file",
            str(wav_a),
            "summary",
            True,
            "single",
            "file",
            str(wav_b),
            "mp3",
            "exit",
        ],
    )
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0].endswith("summaries")


def test_transcript_not_revealed_after_summary(tmp_path: Path) -> None:
    # TD-12 priority (reverse of supersede): once summaries popped (3), a later
    # transcript-only run (2) does NOT pop transcripts — summaries outranks it for the launch.
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    mp3 = tmp_path / "b.mp3"
    mp3.write_bytes(b"x")
    answers = [
        "single",
        "file",
        str(wav),
        "summary",
        True,
        "single",
        "file",
        str(mp3),
        "transcript",
        "exit",
    ]
    deps, stub, _ = _make_deps(tmp_path, answers)
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0].endswith("summaries")


def test_audio_not_revealed_after_transcript(tmp_path: Path) -> None:
    # TD-12 priority (reverse of supersede): once transcripts popped (2), a later MP3-only
    # run (1) does NOT pop audio — transcripts outranks it for the launch.
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    wav = tmp_path / "b.wav"
    wav.write_bytes(b"x")
    deps, stub, _ = _make_deps(
        tmp_path,
        ["single", "file", str(mp3), "transcript", "single", "file", str(wav), "mp3", "exit"],
    )
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0].endswith("transcripts")


def test_action_choice_keys_match_flow_branches(tmp_path: Path) -> None:
    # Regression guard: StubUI.select returns the queued answer verbatim, so a key/branch
    # drift (renaming a Choice key without updating _flow_local_file, or vice versa) would
    # not surface in a behavior test. Pin the menu Choice keys to the keys the flow handles.
    # Video menu: extraction (mp3), summary, transcript, plus the back control.
    assert {k for k, _ in menu._ACTION_CHOICES} == {"mp3", "summary", "transcript", "__back__"}
    # mp3 menu now offers the "mp3" key too — an mp3 source can be re-encoded to a smaller VBR
    # file (operator request, reversing TD-12). It shares the "mp3" branch with the video menu.
    assert {k for k, _ in menu._MP3_ACTION_CHOICES} == {
        "summary",
        "transcript",
        "mp3",
        "__back__",
    }


def test_production_reveal_dir_guard_is_monotone_by_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reveal guard exists in two places: StubUI (mirrored, tested above) and the real
    # RichQuestionaryUI. TD-12 made the production compare load-bearing for THREE levels, so
    # exercise it directly — a regression ('<' instead of '<=', or a dropped assignment)
    # would pass every StubUI-based test. Force the non-Windows early return so we test only
    # the priority arithmetic, and bypass the TTY-required __init__.
    import os

    monkeypatch.setattr(os, "name", "posix")  # echogist.ui reads os.name (same module object)
    ui = object.__new__(RichQuestionaryUI)
    ui._revealed_priority = 0
    ui.reveal_dir(tmp_path, priority=REVEAL_AUDIO)
    assert ui._revealed_priority == REVEAL_AUDIO
    ui.reveal_dir(tmp_path, priority=REVEAL_TRANSCRIPT)  # higher supersedes
    assert ui._revealed_priority == REVEAL_TRANSCRIPT
    ui.reveal_dir(tmp_path, priority=REVEAL_AUDIO)  # lower is a no-op
    assert ui._revealed_priority == REVEAL_TRANSCRIPT
    ui.reveal_dir(tmp_path, priority=REVEAL_SUMMARY)  # highest supersedes
    assert ui._revealed_priority == REVEAL_SUMMARY


def test_local_file_bad_path_returns_to_menu(tmp_path: Path, isolate_last_dir: list[Path]) -> None:
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", str(tmp_path / "nope.wav"), "exit"])
    assert menu.run_menu(deps) == 0
    assert "File not found" in stub.log_text
    assert calls["transcribe"] == 0
    assert isolate_last_dir == []  # F1 → nothing remembered


def test_local_file_cancel_returns_to_menu(tmp_path: Path, isolate_last_dir: list[Path]) -> None:
    # A soft cancel from the picker is a queued None (dialog Cancel / blank entry).
    deps, stub, calls = _make_deps(tmp_path, ["single", "file", None, "exit"])
    assert menu.run_menu(deps) == 0
    assert "No file selected" in stub.log_text
    assert calls["transcribe"] == 0
    assert isolate_last_dir == []  # cancel → nothing transcribed, nothing remembered


def test_local_file_picker_remembers_directory(
    tmp_path: Path, isolate_last_dir: list[Path]
) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["single", "file", str(src), "summary", True, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1
    assert isolate_last_dir == [src.parent]  # the picked file's parent is saved


# --------------------------------------------------------------------------- #
# Source 2 — batch videos -> MP3 (offline, no cost, no transcript)
# --------------------------------------------------------------------------- #
def _videos(tmp_path: Path, *names: str) -> list[Path]:
    folder = tmp_path / "inbox"
    folder.mkdir(exist_ok=True)
    made = []
    for name in names:
        path = folder / name
        path.write_bytes(b"x" * 1024)
        made.append(path)
    return made


def _spy_batch(report: folder.Report | None = None, *, raises: Exception | None = None) -> Any:
    """A convert_many stand-in that records its call and returns a canned report."""
    seen: dict[str, Any] = {}

    def convert(sources: Any, out_dir: Path, **kwargs: Any) -> folder.Report:
        seen["sources"] = list(sources)
        seen["out_dir"] = out_dir
        seen.update(kwargs)
        if raises is not None:
            raise raises
        result = report or folder.Report(
            items=tuple(
                folder.Item(source, "done", output=out_dir / f"{source.stem}.mp3")
                for source in sources
            )
            + tuple(kwargs.get("extra", ()))
        )
        on_item = kwargs.get("on_item")
        for item in result.items:
            # Only pool-converted files tick the bar; caller-supplied ``extra`` skips
            # never entered the pool, so the real runner does not tick for them either.
            if on_item is not None and item.status == "done":
                on_item(item)
        return result

    convert.seen = seen  # type: ignore[attr-defined]
    return convert


def test_batch_converts_every_picked_video(tmp_path: Path) -> None:
    videos = _videos(tmp_path, "a.mp4", "b.mkv", "c.mov")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["sources"] == videos
    assert spy.seen["out_dir"] == tmp_path / "output" / "audio"
    assert "Converted 3 of 3" in stub.log_text
    # MP3 is the whole deliverable, so the audio folder is what pops (TD-14).
    assert ("reveal_dir", str(tmp_path / "output" / "audio")) in stub.messages


def test_selection_bytes_ignores_an_unreadable_entry(tmp_path: Path) -> None:
    # The size line is a courtesy, never a gate: a file that vanished between the picker
    # and the stat must contribute 0, not abort a batch the operator already committed to.
    good = tmp_path / "a.mp4"
    good.write_bytes(b"x" * 10)

    assert menu._selection_bytes([good, tmp_path / "gone.mp4"]) == 10


def test_batch_routes_extraction_through_the_injected_seam(tmp_path: Path) -> None:
    # Without this the batch falls back to folder.convert_many's own module-level default
    # and silently forks from menu #1: a test stubbing deps.extract_audio would spawn real
    # ffmpeg, and any future extraction option would apply to one flow only.
    _videos(tmp_path, "a.mp4")
    spy = _spy_batch()
    deps, _, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["extract_fn"] is deps.extract_audio


def test_batch_remembers_the_picked_directory(tmp_path: Path, isolate_last_dir: list[Path]) -> None:
    videos = _videos(tmp_path, "a.mp4", "b.mkv")
    deps, _, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=_spy_batch()
    )

    assert menu.run_menu(deps) == 0

    assert isolate_last_dir == [videos[0].parent]


def test_batch_typed_directory_is_remembered_as_itself(
    tmp_path: Path, isolate_last_dir: list[Path]
) -> None:
    # A typed folder IS the working directory; remembering its parent would reopen the
    # picker one level too high on the next run.
    _videos(tmp_path, "a.mp4")
    deps, _, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=_spy_batch()
    )

    assert menu.run_menu(deps) == 0

    assert isolate_last_dir == [tmp_path / "inbox"]


def test_batch_uses_the_configured_worker_count(tmp_path: Path) -> None:
    _write_settings(tmp_path, batch_workers=3)
    _videos(tmp_path, "a.mp4")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["workers"] == 3
    assert "3 at a time" in stub.log_text


def test_batch_worker_count_of_one_is_announced_as_sequential(tmp_path: Path) -> None:
    _write_settings(tmp_path, batch_workers=1)
    _videos(tmp_path, "a.mp4")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["workers"] == 1
    assert "one at a time" in stub.log_text


def test_batch_cancel_at_the_picker_returns_to_the_menu(tmp_path: Path) -> None:
    spy = _spy_batch()
    deps, stub, _ = _make_deps(tmp_path, ["folder", None, "exit"], convert_many=spy)

    assert menu.run_menu(deps) == 0

    assert spy.seen == {}, "a soft cancel must never reach the converter"
    assert "No folder selected" in stub.log_text


def test_batch_asks_once_before_re_encoding_mp3s_and_defaults_to_skipping(tmp_path: Path) -> None:
    # The operator's rule: a Shift-range that swept up an mp3 must not quietly re-encode
    # it (lossy -> lossy). Declining moves them to the report as skipped, untouched.
    _videos(tmp_path, "a.mp4", "old.mp3", "b.mkv")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", False, "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert [p.name for p in spy.seen["sources"]] == ["a.mp4", "b.mkv"]
    assert [i.source.name for i in spy.seen["extra"]] == ["old.mp3"]
    assert "1 skipped" in stub.log_text
    assert any("already MP3" in text for _level, text in stub.messages)


def test_batch_re_encodes_mp3s_when_the_operator_says_yes(tmp_path: Path) -> None:
    _videos(tmp_path, "a.mp4", "old.mp3")
    spy = _spy_batch()
    deps, _, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", True, "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert [p.name for p in spy.seen["sources"]] == ["a.mp4", "old.mp3"]
    assert spy.seen["extra"] == ()


def test_batch_never_asks_about_mp3s_when_the_selection_has_none(tmp_path: Path) -> None:
    # The question exists only when the choice is real; a pure-video batch must not stop
    # for it. The answer queue holds no bool, so a stray confirm would EOF the run.
    _videos(tmp_path, "a.mp4", "b.mkv")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert not any(level == "confirm" for level, _text in stub.messages)


def test_batch_all_mp3_and_declined_converts_nothing(tmp_path: Path) -> None:
    _videos(tmp_path, "one.mp3", "two.mp3")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", False, "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen == {}, "nothing left to convert — the pool is never started"
    assert "already an MP3" in stub.log_text


def test_batch_reports_failures_in_a_table_and_still_reveals(tmp_path: Path) -> None:
    videos = _videos(tmp_path, "good.mp4", "bad.mp4")
    audio = tmp_path / "output" / "audio"
    report = folder.Report(
        items=(
            folder.Item(videos[0], "done", output=audio / "good.mp3"),
            folder.Item(videos[1], "failed", detail="ffmpeg exit 1: moov atom not found"),
        )
    )
    spy = _spy_batch(report)
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert "Converted 1 of 2" in stub.log_text
    assert "1 failed" in stub.log_text
    assert ("table", "Not converted") in stub.messages
    assert any("moov atom not found" in text for _level, text in stub.messages)
    # One bad file must not cost the operator the folder pop for the file that worked.
    assert ("reveal_dir", str(audio)) in stub.messages


def test_batch_ctrl_c_shows_the_partial_report_and_stays_in_the_app(tmp_path: Path) -> None:
    # The deliberate local exception to the Ctrl-C-exits contract: the batch stops, the
    # partial report is shown, and run_menu keeps looping (it exits on the queued "exit").
    videos = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
    audio = tmp_path / "output" / "audio"
    partial = folder.Report(
        items=(
            folder.Item(videos[0], "done", output=audio / "a.mp3"),
            folder.Item(videos[1], "cancelled"),
            folder.Item(videos[2], "cancelled"),
        )
    )
    spy = _spy_batch(raises=folder.Cancelled(partial))
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0  # did NOT exit through the interrupt

    assert "Batch stopped" in stub.log_text
    assert "Converted 1 of 3" in stub.log_text
    assert "2 not started" in stub.log_text
    # TD-17: a cancelled batch freezes its bar instead of snapping to a false 100%.
    assert stub.progress_events == ["fail"]
    assert ("reveal_dir", str(audio)) in stub.messages  # the one converted file still pops


def test_batch_cancelled_files_do_not_advance_the_bar(tmp_path: Path) -> None:
    # The post-Ctrl-C drain reports every outstanding file so the report is complete, but
    # those never ran: ticking them would walk the bar to a full 100% right before it
    # freezes, which is the false completion TD-17 forbids.
    videos = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4", "d.mp4")
    audio = tmp_path / "output" / "audio"
    partial = folder.Report(
        items=(folder.Item(videos[0], "done", output=audio / "a.mp3"),)
        + tuple(folder.Item(v, "cancelled") for v in videos[1:])
    )

    def convert(sources: Any, out_dir: Path, **kwargs: Any) -> folder.Report:
        for item in partial.items:  # the real runner ticks for cancelled items too
            kwargs["on_item"](item)
        raise folder.Cancelled(partial)

    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=convert
    )

    assert menu.run_menu(deps) == 0

    assert stub.progress_values == [1.0], "only the one converted file advanced the bar"
    assert stub.progress_events == ["fail"]


def test_batch_bad_typed_path_returns_to_menu(tmp_path: Path) -> None:
    # F1 via the no-tkinter console fallback: a stale path must fail loud, not silently
    # convert an empty selection.
    spy = _spy_batch()
    deps, stub, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "nope.mp4"), "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen == {}
    # The folder module validates the path before any action runs, so a stale entry is
    # refused once, at the picker, rather than reaching a converter with nothing to convert.
    assert "Not a folder" in stub.log_text


def test_batch_expands_a_typed_directory(tmp_path: Path) -> None:
    # The console fallback can only take one path, so a directory is accepted there and
    # expanded by the flow (the native multi-select never yields one).
    videos = _videos(tmp_path, "a.mp4", "b.mkv")
    (tmp_path / "inbox" / "notes.txt").write_text("not media", encoding="utf-8")
    spy = _spy_batch()
    deps, _, _ = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["sources"] == videos


def test_batch_empty_directory_returns_to_menu(tmp_path: Path) -> None:
    # Caught one step earlier since the preview became automatic: the walk says the folder
    # is empty and the action is never asked for, so the converter is never reached.
    empty = tmp_path / "empty"
    empty.mkdir()
    spy = _spy_batch()
    deps, stub, _ = _make_deps(tmp_path, ["folder", str(empty), "exit"], convert_many=spy)

    assert menu.run_menu(deps) == 0

    assert spy.seen == {}
    assert "No media files found" in stub.log_text


def test_batch_never_transcribes_or_summarizes(tmp_path: Path) -> None:
    # Scope guard: this flow is offline and free. A regression that wired it into the
    # paid pipeline would be caught here, not on the operator's bill.
    _videos(tmp_path, "a.mp4")
    spy = _spy_batch()
    deps, _, calls = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0
    assert calls["render"] == 0


# --------------------------------------------------------------------------- #
# Source 3 — saved transcript (the recovery path)
# --------------------------------------------------------------------------- #
def _seed_transcript(tmp_path: Path, text: str = "hello world") -> Path:
    tdir = tmp_path / "output" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / "2026-06-16-clip.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_saved_transcript_pick_by_arrow(tmp_path: Path) -> None:
    _seed_transcript(tmp_path)
    # The first saved transcript is select value "0" (the list index).
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 0  # re-summarize, never re-transcribe
    assert calls["summarize"] == 1
    assert "Done — summary written to" in stub.log_text


def test_saved_transcript_cancel_returns_to_menu(tmp_path: Path) -> None:
    _seed_transcript(tmp_path)
    deps, _, calls = _make_deps(tmp_path, ["single", "transcript", "__cancel__", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 0


def test_saved_transcript_empty_file(tmp_path: Path) -> None:
    _seed_transcript(tmp_path, text="   ")
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert "nothing to summarize" in stub.log_text
    assert calls["summarize"] == 0


# --------------------------------------------------------------------------- #
# Cost / threshold flow (T8 wired through the menu)
# --------------------------------------------------------------------------- #
def test_over_threshold_decline_skips_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)  # any cost needs explicit confirm
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", False, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 0
    assert "cancelled" in stub.log_text.lower()


def test_over_threshold_yes_makes_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", True, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert "Estimated cost" in stub.log_text
    assert "Actual cost" in stub.log_text


def test_below_threshold_proceeds_without_confirm(tmp_path: Path) -> None:
    # A cheap call must NOT consume a confirm answer — the shown estimate is the
    # acknowledgment (v1.1 §5). Auto-accept is on by default, so the below-threshold path also
    # shows no "press Enter" beat: the queue has no confirm bool AND no text beat before exit.
    _write_settings(tmp_path, confirm_threshold_usd=100.0)  # everything is "cheap"
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert not [m for m in stub.messages if m[0] == "text" and "Press Enter" in m[1]]


def test_below_threshold_shows_beat_when_auto_accept_off(tmp_path: Path) -> None:
    # TD-9: with auto-accept turned OFF, the cheap path gains a non-decision "press Enter"
    # acknowledge beat before the paid call — a last chance to Ctrl-C out. The queued "" is
    # that Enter; the beat is non-blocking, so the call still runs.
    _write_settings(tmp_path, confirm_threshold_usd=100.0, auto_accept_under_threshold=False)
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    beats = [m for m in stub.messages if m[0] == "text" and "Press Enter to summarize" in m[1]]
    assert len(beats) == 1  # fired exactly once, on the below-threshold path


def test_above_threshold_confirms_even_with_auto_accept_on(tmp_path: Path) -> None:
    # Auto-accept only governs the cheap path — above the threshold the explicit y/N gate
    # ALWAYS applies (and no beat), even with auto-accept on (the default).
    _write_settings(tmp_path, confirm_threshold_usd=0.0, auto_accept_under_threshold=True)
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", True, "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert any(m[0] == "confirm" for m in stub.messages)  # explicit gate fired
    assert not [m for m in stub.messages if m[0] == "text" and "Press Enter" in m[1]]  # no beat


def test_unverified_prices_warns_but_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tier flagged prices_unverified (a generation bump whose prices no human has
    # re-checked) prints a one-time non-blocking notice near the estimate. It never
    # moves the gate: this cheap, auto-accept-on run still reaches the wire.
    from dataclasses import replace

    _write_settings(tmp_path, confirm_threshold_usd=100.0)
    _seed_transcript(tmp_path)
    real = config.load_model_config

    def patched(*a: Any, **k: Any) -> config.ModelConfig:
        cfg = real(*a, **k)
        flagged = replace(cfg.tiers["economy"], prices_unverified=True)
        return replace(cfg, tiers={**cfg.tiers, "economy": flagged})

    monkeypatch.setattr(config, "load_model_config", patched)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1  # not blocked
    notices = [m for m in stub.messages if m[0] == "warn" and "prices unconfirmed" in m[1]]
    assert len(notices) == 1


def test_no_unverified_notice_when_prices_confirmed(tmp_path: Path) -> None:
    # The default economy tier is not flagged, so the notice must stay silent.
    _write_settings(tmp_path, confirm_threshold_usd=100.0)
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert not [m for m in stub.messages if m[0] == "warn" and "prices unconfirmed" in m[1]]


# --------------------------------------------------------------------------- #
# §12 return-to-menu failure modes
# --------------------------------------------------------------------------- #
def test_missing_api_key_guides_and_skips_call(tmp_path: Path) -> None:  # F3
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"], api_key=None)
    assert menu.run_menu(deps) == 0
    assert "No Anthropic API key found" in stub.log_text
    assert "config/secrets.toml" in stub.log_text  # F3 guides to both key sources
    assert calls["summarize"] == 0


def test_long_transcript_synthesizes_in_phases(tmp_path: Path) -> None:  # TD-16 v2
    # A long transcript no longer hits the old "too long" refusal — it phase-splits and
    # synthesizes directly (K>1, plus a reconcile pass). The menu makes ONE seam call
    # (summarize_auto phase-splits internally); the phase count + message live in the menu.
    _write_settings(tmp_path, model_tier="economy")
    big = "\n".join(f"[00:{m:02d}:00] " + "слово " * 400 for m in range(50))  # ~70K tok, timecoded
    _seed_transcript(tmp_path, text=big)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert "phases (+1 reconcile)" in stub.log_text  # multi-phase synthesis copy
    assert "map-reduce" not in stub.log_text  # the old map-reduce path is gone
    assert "too long" not in stub.log_text  # the old refusal is gone
    assert calls["summarize"] == 1  # reached the wire (phase-split happens inside the seam)


def test_short_transcript_still_pays_for_the_reconcile_call(tmp_path: Path) -> None:
    # Short material collapses to K=1, but the reconcile call is no longer skipped — it
    # writes the essence block the document opens with. The menu must quote TWO cloud
    # calls, not one, so the operator is never surprised by a second charge.
    _seed_transcript(tmp_path, text="[00:00:00] короткая расшифровка")
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert "in 1 phase (+1 reconcile)" in stub.log_text  # singular, not "1 phases"
    assert "2 cloud calls" in stub.log_text
    assert calls["summarize"] == 1  # still ONE seam call; the split happens inside it


def test_phase_path_still_guards_an_oversize_phase(tmp_path: Path) -> None:  # TD-16 v2 / F6
    # Phase-split lowers the per-call input but does NOT repeal the overflow guard. A single
    # un-splittable block (plan_phases never cuts mid-block, K is clamped to len(blocks))
    # that exceeds the tier context is caught locally, before any paid call — not sent to
    # the wire to fail mid-run after partial spend.
    _write_settings(tmp_path, model_tier="economy")  # safe_budget = 200000 * 0.8 = 160000
    one_huge_block = "[00:00:00] " + "слово" * 60_000  # ~181K est tokens in ONE block
    _seed_transcript(tmp_path, text=one_huge_block)
    deps, stub, calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert "too large" in stub.log_text  # the F6-style oversize-phase guard fired
    assert "safe budget" in stub.log_text
    assert calls["summarize"] == 0  # never reached the wire


def test_resume_partial_loaded_passed_to_seam_then_cleared(tmp_path: Path) -> None:  # TD-16 v2
    # Artifact-resume: a within-run partial that is a strict prefix of the K-phase plan is
    # reloaded and handed to the seam as resume_from (so a re-run skips done phases), then
    # deleted once the durable .json is written.
    _write_settings(tmp_path, model_tier="economy")
    big = "\n".join(f"[00:{m:02d}:00] " + "слово " * 400 for m in range(50))  # K>1
    src = _seed_transcript(tmp_path, text=big)
    # Keyed on the resolved source path, plus the language and tier the phases were
    # written under: reloading Russian phases into an English run would splice two
    # languages into one document that reconcile never re-reads.
    key = menu._resume_key(src, language="ru", tier="economy")
    resume_path = tmp_path / "output" / "summaries" / "raw" / ".resume" / f"{key}.json"
    partial = summarize._running_summary(
        [SynthesisSection("Done phase", "prior prose", ("[00:00:00]",))], [], [], "ru"
    )
    summarize.write_summary_json(partial, resume_path)
    assert resume_path.is_file()

    captured: dict[str, Any] = {}

    def summarize_fn(
        text: str, tier: Any, cfg: Any, *, resume_from: Any = None, **_kw: Any
    ) -> SummarizeResult:
        captured["resume_from"] = resume_from
        return SummarizeResult(summary=_summary(), input_tokens=1, output_tokens=1)

    def render_fn(summary: Any, out_dir: Path, fmt: str, *, base: str, **_kw: Any) -> Path:
        return Path(out_dir) / f"{base}.{fmt}"

    deps = menu.Deps(
        ui=StubUI(["single", "transcript", "0", "exit"]),
        summarize=summarize_fn,
        render=render_fn,
        get_api_key=lambda: "sk-test",
        base=tmp_path,
        settings_path=tmp_path / "settings.json",
    )
    assert menu.run_menu(deps) == 0
    rf = captured["resume_from"]
    assert rf is not None and len(rf.synthesis) == 1  # the prefix partial was loaded
    assert not resume_path.exists()  # cleared once the durable .json exists


def test_resume_partial_is_keyed_per_source_not_per_stem(tmp_path: Path) -> None:
    # The live bug (dec-b3a3181c). Two different recordings can share a stem — the same talk
    # saved twice, or "lecture.txt" in two folders. Under the old stem key, source A dying
    # mid-summary left a partial that source B then RESUMED, so B's PAID summary carried A's
    # prose and A's timecodes. The anchor validator accepted it, because those timecodes are
    # real, just from the wrong recording: silently wrong output, paid for.
    _write_settings(tmp_path, model_tier="economy")

    def _lecture(folder: str, word: str) -> Path:
        d = tmp_path / folder
        d.mkdir(parents=True, exist_ok=True)
        path = d / "lecture.txt"  # SAME stem, different source
        body = "\n".join(f"[00:{m:02d}:00] " + f"{word} " * 400 for m in range(50))  # K>1
        path.write_text(body, encoding="utf-8")
        return path

    src_a, src_b = _lecture("A", "альфа"), _lecture("B", "бета")
    seen_resume: list[Any] = []

    def summarize_fn(
        text: str,
        tier: Any,
        cfg: Any,
        *,
        on_phase: Any = None,
        resume_from: Any = None,
        **_kw: Any,
    ) -> SummarizeResult:
        seen_resume.append(resume_from)
        on_phase(  # phase 1 lands on disk under this source's key
            summarize._running_summary(
                [SynthesisSection("A phase", "PROSE FROM A", ("[00:00:00]",))], [], [], "ru"
            )
        )
        if len(seen_resume) == 1:  # ...and then A's run dies, so its partial survives
            raise SummarizeError("died mid-summary")
        return SummarizeResult(summary=_summary(), input_tokens=1, output_tokens=1)

    def render_fn(summary: Any, out_dir: Path, fmt: str, *, base: str, **_kw: Any) -> Path:
        return Path(out_dir) / f"{base}.{fmt}"

    deps = menu.Deps(
        ui=StubUI(["single", "transcript", str(src_a), "single", "transcript", str(src_b), "exit"]),
        summarize=summarize_fn,
        render=render_fn,
        get_api_key=lambda: "sk-test",
        base=tmp_path,
        settings_path=tmp_path / "settings.json",
    )
    assert menu.run_menu(deps) == 0

    assert seen_resume == [None, None]  # B started FRESH — it never saw A's phases
    resume_dir = tmp_path / "output" / "summaries" / "raw" / ".resume"
    settings = config.load_settings(tmp_path / "settings.json")
    key = partial(menu._resume_key, language=settings.summary_language, tier=settings.model_tier)
    assert (resume_dir / f"{key(src_a)}.json").is_file()  # A's partial kept
    assert not (resume_dir / f"{key(src_b)}.json").exists()  # B's was cleared
    assert key(src_a) != key(src_b)  # the keys actually differ


def test_resume_key_is_case_insensitive(tmp_path: Path) -> None:
    # Path.resolve() does not normalize case on Windows, so the same file picked once as
    # "Lecture.txt" and once as "lecture.txt" would key to two partials and silently lose
    # the resume. casefold() is what makes the two spellings one identity.
    path = tmp_path / "Lecture.txt"
    path.write_text("x", encoding="utf-8")
    assert menu._resume_key(path) == menu._resume_key(tmp_path / "lecture.txt")


def test_stale_stem_keyed_resume_files_are_swept(tmp_path: Path) -> None:
    # Partials written by the pre-hash stem key are unreachable now. Swept by SHAPE (a stem
    # that is not 16 hex chars), so the sweep needs no date and no migration table.
    _seed_transcript(tmp_path)
    resume_dir = tmp_path / "output" / "summaries" / "raw" / ".resume"
    resume_dir.mkdir(parents=True)
    stale = resume_dir / "2026-06-16-clip.json"  # old stem key
    keep = resume_dir / "a1b2c3d4e5f60718.json"  # already hash-keyed: left alone
    for f in (stale, keep):
        f.write_text("{}", encoding="utf-8")

    deps, stub, _calls = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert not stale.exists()
    assert keep.is_file()
    assert "left by an older version" in stub.log_text


def test_render_failure_after_paid_call_keeps_json(tmp_path: Path) -> None:  # F13
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "transcript", "0", "exit"], render_error=True
    )
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1  # the call was paid
    assert "re-render it later" in stub.log_text
    # The raw result was persisted BEFORE render, so no re-pay is needed.
    assert list((tmp_path / "output" / "summaries" / "raw").glob("*.json"))


def test_render_notice_reaches_the_loud_channel(tmp_path: Path) -> None:  # TD-28
    # The render stage gets its own `notice`, wired to ui.warn — the same channel as a
    # dropped anchor. Asserted at the SEAM because RenderFn is Callable[..., Path]: if the
    # menu stopped passing it, the stage would fall back to bare print() and the operator
    # would never see that the document has a blank where a character should be.
    _seed_transcript(tmp_path)
    deps, stub, _ = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])
    assert menu.run_menu(deps) == 0
    assert [m for m in stub.messages if m[0] == "warn" and "cannot draw" in m[1]]


def test_summarize_error_returns_to_menu(tmp_path: Path) -> None:  # F2/F4/F5
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(
        tmp_path, ["single", "transcript", "0", "exit"], summarize_error="no internet"
    )
    assert menu.run_menu(deps) == 0
    assert "no internet" in stub.log_text
    assert calls["render"] == 0


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_change_language_persists(tmp_path: Path) -> None:
    # menu -> settings -> field "language" -> value "en".
    deps, stub, _ = _make_deps(tmp_path, ["settings", "1", "en", "exit"])
    assert menu.run_menu(deps) == 0
    assert "Saved." in stub.log_text
    assert config.load_settings(tmp_path / "settings.json").summary_language == "en"


def test_settings_back_makes_no_change(tmp_path: Path) -> None:
    deps, stub, _ = _make_deps(tmp_path, ["settings", "__back__", "exit"])
    assert menu.run_menu(deps) == 0
    assert "Saved." not in stub.log_text


def test_settings_bad_threshold_unchanged(tmp_path: Path) -> None:
    # menu -> settings -> field "threshold" -> a non-numeric text value.
    deps, stub, _ = _make_deps(tmp_path, ["settings", "4", "abc", "exit"])
    assert menu.run_menu(deps) == 0
    assert "isn't a number" in stub.log_text


def test_settings_toggle_auto_accept_persists(tmp_path: Path) -> None:
    # menu -> settings -> field "5" (auto-accept) -> confirm False. Default is True, so this
    # flips it off and persists. The confirm answer is popped as a bool by StubUI.
    deps, stub, _ = _make_deps(tmp_path, ["settings", "5", False, "exit"])
    assert menu.run_menu(deps) == 0
    assert "Saved." in stub.log_text
    assert config.load_settings(tmp_path / "settings.json").auto_accept_under_threshold is False


def test_settings_batch_workers_persists(tmp_path: Path) -> None:
    deps, stub, _ = _make_deps(tmp_path, ["settings", "6", "2", "exit"])
    assert menu.run_menu(deps) == 0
    assert "Saved." in stub.log_text
    assert config.load_settings(tmp_path / "settings.json").batch_workers == 2


@pytest.mark.parametrize("bad", ["abc", "0", "99"])
def test_settings_bad_batch_workers_unchanged(tmp_path: Path, bad: str) -> None:
    # Same fail-soft shape as the threshold field: warn, leave the setting alone, and
    # return to the menu — never persist a value the pool would choke on.
    before = config.default_settings().batch_workers
    deps, stub, _ = _make_deps(tmp_path, ["settings", "6", bad, "exit"])
    assert menu.run_menu(deps) == 0
    assert "unchanged" in stub.log_text
    assert config.load_settings(tmp_path / "settings.json").batch_workers == before


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


# --------------------------------------------------------------------------- #
# Scan flow (bulk v3, increment 1)
# --------------------------------------------------------------------------- #
_SCAN_STDERR = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':
  Duration: 01:00:00.00, start: 0.000000, bitrate: 1502 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(tv), 1920x1080, 1350 kb/s, 30 fps
  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s
"""


def _offline_scan(monkeypatch: pytest.MonkeyPatch, *, cancel_after: int | None = None) -> None:
    """Point the scan flow at a fake ffmpeg: no binary resolved, no process spawned."""
    monkeypatch.setattr(extract, "default_ffmpeg_exe", lambda: "/fake/ffmpeg")
    seen: list[str] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        if cancel_after is not None and len(seen) >= cancel_after:
            raise KeyboardInterrupt
        seen.append(argv[-1])
        return 1, _SCAN_STDERR

    real_scan_tree = scan.scan_tree
    monkeypatch.setattr(
        scan,
        "scan_tree",
        lambda root, **kw: real_scan_tree(root, **{**kw, "runner": runner}),
    )


def _lecture(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    # Distinct bytes per name, same length. Identity is CONTENT since TD-31, so two files
    # written with identical bytes are genuinely ONE recording and every join collapses
    # them — correct behaviour, but not what a fixture standing in for two lectures wants.
    path.write_bytes((name.encode("utf-8") * 4096)[:4096])
    return path


def test_scan_flow_reports_folders_totals_and_costs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library / "Course-1", "one.mp4")
    _lecture(library / "Course-1", "two.mp4")
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])

    assert menu.run_menu(deps) == 0

    assert "prices the summaries before you spend anything" in stub.log_text
    assert ("table", "Folders") in stub.messages
    assert ("table", "Totals") in stub.messages
    assert "Course-1/" in stub.log_text
    assert "2h 00m" in stub.log_text
    assert "PROJECTION from duration, biased high" in stub.log_text
    # Read-only: not one paid or heavy stage ran.
    assert calls == {"extract": 0, "transcribe": 0, "summarize": 0, "render": 0}


def test_scan_flow_surfaces_duplicate_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one thing that silently corrupts a paid run later, so it gets its own warning
    # rather than a quiet row in a table.
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library / "A", "lecture.mp4")
    _lecture(library / "B", "lecture.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])

    menu.run_menu(deps)

    assert "shared by more than one file" in stub.log_text
    assert ("table", "Duplicate names") in stub.messages
    # Rendered RELATIVE to the scan root. Three absolute paths per group wrap over several
    # lines and bury the one part that tells the files apart: which folder each is in.
    assert ("table-row", "lecture: A/lecture.mp4, B/lecture.mp4") in stub.messages


def test_scan_flow_never_counts_echogists_own_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    _lecture(tmp_path / "output" / "audio", "2026-01-01-lecture.mp3")
    _lecture(tmp_path, "lecture.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(tmp_path), "__back__", "exit"])

    menu.run_menu(deps)

    assert "Media files: 1" in stub.log_text


def test_scan_flow_cancelled_still_reports_what_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ctrl-C returns to the MENU with the partial report, it does not exit the app: a cold
    # scan is minutes of spawns and the operator should not lose both the numbers and the
    # cache for one keystroke.
    _offline_scan(monkeypatch, cancel_after=1)
    library = tmp_path / "library"
    _lecture(library, "a.mp4")
    _lecture(library, "b.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])

    assert menu.run_menu(deps) == 0  # back to the menu, then a normal exit

    assert "Scan cancelled" in stub.log_text
    assert "Media files: 1" in stub.log_text


def test_scan_flow_returns_to_the_menu_on_a_bad_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(tmp_path / "ghost"), "exit"])

    assert menu.run_menu(deps) == 0

    assert "Not a folder" in stub.log_text


def test_scan_flow_soft_cancels_when_no_folder_is_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    deps, stub, _calls = _make_deps(tmp_path, ["folder", None, "exit"])

    assert menu.run_menu(deps) == 0

    assert "No folder selected" in stub.log_text


def test_a_missing_ffmpeg_fails_once_not_five_hundred_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resolved before the walk on purpose. Resolving it per file would mark every file in
    # the library "unreadable" and bury the one real cause.
    def boom() -> str:
        raise ExtractError("The bundled ffmpeg binary is missing or corrupt")

    monkeypatch.setattr(extract, "default_ffmpeg_exe", boom)
    _lecture(tmp_path / "library", "a.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", "exit"])

    assert menu.run_menu(deps) == 0

    assert "missing or corrupt" in stub.log_text
    assert not [m for m in stub.messages if m[0] == "pick_dir"]


def test_an_empty_folder_says_so_instead_of_drawing_an_empty_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty Folders table and a $0.00 total reads as "the scan broke". Say it plainly.
    _offline_scan(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(empty), "exit"])

    assert menu.run_menu(deps) == 0

    assert "No media files found" in stub.log_text
    assert ("table", "Folders") not in stub.messages
    assert ("table", "Totals") not in stub.messages


def test_the_other_menu_rows_still_dispatch_after_the_renumber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inserting a row shifts Settings and Exit by one POSITIONAL key. The semantic keys
    # must be untouched, or the operator's next Settings visit lands somewhere else.
    _offline_scan(monkeypatch)
    deps, stub, _calls = _make_deps(tmp_path, ["settings", "__back__", "exit"])

    assert menu.run_menu(deps) == 0

    assert ("table", "Current settings") in stub.messages
    assert "Goodbye." in stub.log_text


def test_the_sweep_shape_check_tracks_the_digest_size() -> None:
    # If these two ever drift, _sweep_stale_resumes deletes every VALID partial as
    # "stale" — silently, and only on the re-run that was meant to save the work.
    key = menu._resume_key(Path("/any/source.mp4"))

    assert len(key) == menu._RESUME_KEY_DIGEST_SIZE * 2
    assert menu._RESUME_KEY_RE.fullmatch(key)


def test_main_menu_is_two_modules_then_settings_and_exit() -> None:
    # Number keys are POSITIONAL, so where a row lands changes the operator's fingers.
    # The work rows come first, in input order (one file, then many), and the two
    # housekeeping rows close the list.
    assert [key for key, _label in menu._MAIN_MENU] == ["single", "folder", "settings", "exit"]


def test_the_preview_runs_on_the_pick_not_on_a_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator 2026-09-05: the count and the price are what the action choice is FOR, so
    they have to be on screen before it is asked, not one row inside it."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library / "Course-1", "one.mp4")
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])

    assert menu.run_menu(deps) == 0

    kinds = [(kind, text) for kind, text in stub.messages if kind in {"table", "select"}]
    totals = kinds.index(("table", "Totals"))
    action = kinds.index(("select", "What should EchoGist produce for 'library'?"))
    assert totals < action  # the report, THEN the question it informs
    # The operator never asked for a preview and never spent anything to get one.
    assert "Preview only" not in stub.log_text
    assert calls == {"extract": 0, "transcribe": 0, "summarize": 0, "render": 0}


def test_mp3_for_a_folder_converts_every_file_the_preview_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview walks the whole tree; the MP3 action used to walk one level of it.

    Two numbers, one folder: the operator reads "Media files: 3" and a per-folder table
    listing the course subfolders, picks MP3 only, and gets the top-level file with a
    "Converted 1 of 1" report that reads as a complete success. The three actions come off
    ONE choice list, so they have to mean the same thing by "this folder".
    """
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "intro.mp4")
    _lecture(library / "Week-1", "one.mp4")
    _lecture(library / "Week-1" / "Day-2", "two.mp4")
    spy = _spy_batch()
    deps, stub, _calls = _make_deps(
        tmp_path, ["folder", str(library), "mp3", True, "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert "Media files: 3" in stub.log_text
    assert sorted(p.name for p in spy.seen["sources"]) == ["intro.mp4", "one.mp4", "two.mp4"]


def test_a_cancelled_preview_does_not_claim_the_folder_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C on the very first probe reads nothing, so it can say nothing about the tree.

    The partial result is empty, and the empty-folder early return used to fire on it: a
    keystroke meant to skip a slow scan printed "No media files found" about a full course
    and threw the operator out of the folder module.
    """
    _offline_scan(monkeypatch, cancel_after=0)
    library = tmp_path / "library"
    _lecture(library, "a.mp4")
    _lecture(library, "b.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])

    assert menu.run_menu(deps) == 0

    assert "No media files found" not in stub.log_text
    assert "Scan stopped before it read anything" in stub.log_text
    # ...and the module is still usable: the action question was still asked.
    assert [m for m in stub.messages if m[0] == "select" and "produce for" in m[1]]


def test_an_unreadable_folder_is_not_reported_as_already_summarized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unhydrated OneDrive course plans to nothing, exactly like a finished one does.

    "Every file in this folder already has a summary" is a claim about work done. Said
    over a folder whose files were never read, it tells the operator the course is
    finished when not one second of it reached the GPU.
    """
    monkeypatch.setattr(extract, "default_ffmpeg_exe", lambda: "/fake/ffmpeg")
    # A probe that returns no Duration line: the file is real, its length is unknowable.
    monkeypatch.setattr(scan, "scan_tree", lambda root, **kw: _scan_all_unreadable(root))
    library = tmp_path / "library"
    _lecture(library, "a.mp4")
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "summary", "exit"])

    assert menu.run_menu(deps) == 0

    assert "already has a summary" not in stub.log_text
    assert "Nothing readable in this folder" in stub.log_text
    assert calls["transcribe"] == 0 and calls["summarize"] == 0


def _scan_all_unreadable(root: Path) -> scan.ScanResult:
    """A scan whose every candidate failed to probe — the OneDrive-placeholder shape."""
    found = sorted(p for p in root.rglob("*.mp4"))
    return scan.ScanResult(
        root=root,
        files=(),
        unreadable=tuple((p, "no duration in probe output") for p in found),
        placeholders=(),
    )


def test_an_empty_folder_never_reaches_the_action_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three ways to produce nothing out of nothing is not a question worth asking."""
    _offline_scan(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(empty), "exit"])

    assert menu.run_menu(deps) == 0

    assert not [m for m in stub.messages if m[0] == "select" and "produce for" in m[1]]


def test_the_folder_module_offers_exactly_the_single_file_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the two-module menu: the folder module is not a reduced one, and now
    not an extended one either. Whatever EchoGist can produce from one recording it can
    produce from a folder of them, off the SAME list — one object, so they cannot drift."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    deps, stub, _calls = _make_deps(tmp_path, ["folder", str(library), "__back__", "exit"])
    offered: dict[str, tuple[Choice, ...]] = {}
    inner = stub.select

    def record(prompt: str, choices: Sequence[Choice]) -> str:
        offered[prompt] = tuple(choices)
        return inner(prompt, choices)

    monkeypatch.setattr(stub, "select", record)

    assert menu.run_menu(deps) == 0

    assert offered["What should EchoGist produce for 'library'?"] == menu._ACTION_CHOICES


# --------------------------------------------------------------------------- #
# Folder run (bulk v3 increment 2) — two phases, one gate
# --------------------------------------------------------------------------- #
def test_bulk_flow_transcribes_everything_then_asks_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: one confirm for the folder, not one per file.

    The threshold is pinned to 0 so EVERY file would individually trip the single-file
    gate. That is what makes this test able to fail: with a default threshold the stub
    transcripts are too cheap to gate, and a per-file gate would sail through unnoticed.
    """
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    for name in ("one.mp4", "two.mp4", "three.mp4"):
        _lecture(library, name)
    deps, stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", False, True, "exit"]
    )

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 3
    assert calls["summarize"] == 3
    # Exactly one confirm was consumed for the whole folder: "exit" is still queued and
    # was reached. A per-file gate would have eaten it and ended the session early.
    assert stub.answers == []
    assert "3 file(s) ready to summarize" in stub.log_text


def test_the_folder_report_prices_every_file_and_closes_the_gate_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After an hour away the operator's questions are per file, and the console history
    that would answer them has scrolled past. The report answers them: a row per file with
    what it cost, then the totals — including the quote the operator agreed to next to the
    bill, which is the only feedback the cost model ever gets."""
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    for name in ("one.mp4", "two.mp4"):
        _lecture(library, name)
    deps, stub, _ = _make_deps(tmp_path, ["folder", str(library), "summary", False, True, "exit"])

    assert menu.run_menu(deps) == 0

    assert ("table", "Per file") in stub.messages
    assert ("table", "Totals") in stub.messages
    assert "one.mp4" in stub.log_text and "two.mp4" in stub.log_text
    assert "Actually spent" in stub.log_text
    assert "Quoted before the run" in stub.log_text
    assert "Time" in stub.log_text


def test_a_free_transcript_run_reports_no_per_file_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row per file earns its place by carrying a price or a failure. On the free phase
    it carries neither, and that phase ALSO reports mid-run, right in front of the cost
    gate — seven rows reading "done" there push the one number being answered off screen."""
    _write_settings(tmp_path)
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    deps, stub, _ = _make_deps(tmp_path, ["folder", str(library), "transcript", False, "exit"])

    assert menu.run_menu(deps) == 0

    assert ("table", "Totals") in stub.messages
    assert ("table", "Per file") not in stub.messages


def test_each_file_opens_its_own_section_and_the_phase_lines_are_muted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder run prints ~60 lines. The file headers are rules so the wall becomes
    sections the eye can skip through; the phase-by-phase lines are muted sub-steps so they
    cannot bury the result. Both are load-bearing at 7 files and meaningless at 1, which is
    why they are asserted here and not left to a look."""
    _write_settings(tmp_path)
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    for name in ("one.mp4", "two.mp4"):
        _lecture(library, name)
    deps, stub, _ = _make_deps(tmp_path, ["folder", str(library), "summary", False, True, "exit"])

    assert menu.run_menu(deps) == 0

    rules = [text for level, text in stub.messages if level == "rule"]
    assert any("[1/2] one.mp4" in text for text in rules)
    assert any("[2/2] two.mp4" in text for text in rules)
    # The synthesis progress went to the muted channel, not the loud one.
    assert any(level == "detail" for level, _text in stub.messages)


def test_bulk_flow_declined_gate_spends_nothing_but_keeps_the_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    deps, stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", False, False, "exit"]
    )

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 1
    assert calls["summarize"] == 0  # killswitch: declining reaches no wire
    assert "your transcripts are saved" in stub.log_text


def test_bulk_flow_skips_a_file_that_already_has_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-22 paying off: a second run over the folder does not re-pay for file one."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    done = _lecture(library, "done.mp4")
    _lecture(library, "todo.mp4")
    summarize.save_raw_result(
        _summary(),
        tmp_path / "output" / "summaries" / "raw",
        fingerprint=naming.source_fingerprint(done),
    )
    deps, stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", False, True, "exit"]
    )

    assert menu.run_menu(deps) == 0

    assert "Skipping 1 file(s) already summarized" in stub.log_text
    assert calls["transcribe"] == 1  # only todo.mp4
    assert calls["summarize"] == 1


def test_bulk_flow_with_nothing_left_to_do_never_reaches_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    done = _lecture(library, "done.mp4")
    summarize.save_raw_result(
        _summary(),
        tmp_path / "output" / "summaries" / "raw",
        fingerprint=naming.source_fingerprint(done),
    )
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "summary", False, "exit"])

    assert menu.run_menu(deps) == 0

    assert "already has a summary" in stub.log_text
    assert calls == {"extract": 0, "transcribe": 0, "summarize": 0, "render": 0}


def test_bulk_flow_one_broken_file_does_not_abandon_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six good lectures must not be lost to one bad one — especially after paying."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _lecture(library, name)
    deps, stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", False, True, "exit"]
    )
    real = deps.transcribe

    def flaky(source: Path, *a: Any, **kw: Any) -> Any:
        if source.name == "b.mp4":
            raise TranscribeError("model choked")
        return real(source, *a, **kw)

    deps = replace(deps, transcribe=flaky)

    assert menu.run_menu(deps) == 0

    assert calls["summarize"] == 2  # a and c still summarized
    assert "model choked" in stub.log_text
    assert "2 file(s) ready to summarize" in stub.log_text


def test_folder_run_keeps_the_mp3_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reverses the old 'the folder run never extracts MP3s' rule (operator, 2026-09-04:
    the two modules must produce the same artifacts). The action label promises an MP3, so
    the run has to make one."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    _lecture(library, "two.mp4")
    deps, _stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", True, True, "exit"]
    )

    assert menu.run_menu(deps) == 0

    assert calls["extract"] == 2, "one MP3 per file, like the single-file flow"


def test_folder_run_skips_the_mp3_when_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second full pass over every file is the operator's call, asked ONCE per run."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    deps, _stub, calls = _make_deps(
        tmp_path, ["folder", str(library), "summary", False, True, "exit"]
    )

    assert menu.run_menu(deps) == 0

    assert calls["extract"] == 0
    assert calls["summarize"] == 1, "declining the MP3 must not touch the primary deliverable"


def test_folder_run_mp3_failure_degrades_and_keeps_the_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MP3 is SECONDARY here exactly as on the single-file flow: a broken ffmpeg warns
    and the free local work still lands, rather than failing the file outright."""
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    _lecture(library, "one.mp4")
    deps, stub, calls = _make_deps(
        tmp_path,
        ["folder", str(library), "transcript", True, "exit"],
        extract_error=True,
    )

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 1, "the transcript is the deliverable and must survive"
    assert "Couldn't save the MP3" in stub.log_text
    assert "Transcribed" in stub.log_text


# --------------------------------------------------------------------------- #
# The two modules — symmetry, and the folder actions that are new to it
# --------------------------------------------------------------------------- #
def test_folder_transcript_run_never_reaches_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transcript-only is the free half of the folder run, and it must stay free.

    The threshold is pinned to 0 so ANY paid step would have to stop and ask. Nothing is
    queued to answer with, so a gate here would EOF the session instead of returning 0 —
    which is what makes this able to fail rather than pass by luck.
    """
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    for name in ("one.mp4", "two.mp4"):
        _lecture(library, name)
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "transcript", False, "exit"])

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 2
    assert calls["summarize"] == 0, "the free half of the folder run must not reach the wire"
    assert calls["render"] == 0
    assert stub.answers == []  # "exit" was reached: no gate ate it
    assert "ready to summarize" not in stub.log_text
    # The transcripts ARE the deliverable here, so that is the folder that pops (TD-14).
    assert ("reveal_dir", str(tmp_path / "output" / "transcripts")) in stub.messages


def test_folder_transcript_run_does_not_skip_an_already_summarized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-22's summary skip protects money; it must not withhold a FREE transcript.

    A lecture summarized last week may have had its transcript deleted since. Asking for
    transcripts must transcribe it, not report it as already done.
    """
    _offline_scan(monkeypatch)
    library = tmp_path / "library"
    done = _lecture(library, "done.mp4")
    _lecture(library, "todo.mp4")
    summarize.save_raw_result(
        _summary(),
        tmp_path / "output" / "summaries" / "raw",
        fingerprint=naming.source_fingerprint(done),
    )
    deps, stub, calls = _make_deps(tmp_path, ["folder", str(library), "transcript", False, "exit"])

    assert menu.run_menu(deps) == 0

    assert calls["transcribe"] == 2, "the summarized file still needs its transcript"
    assert "Skipping" not in stub.log_text, "the money-skip must not withhold free work"


def test_folder_mp3_run_converts_the_whole_folder(tmp_path: Path) -> None:
    """The folder module hands the FOLDER to the converter; expand_selection walks it."""
    videos = _videos(tmp_path, "a.mp4", "b.mkv")
    spy = _spy_batch()
    deps, stub, calls = _make_deps(
        tmp_path, ["folder", str(tmp_path / "inbox"), "mp3", "exit"], convert_many=spy
    )

    assert menu.run_menu(deps) == 0

    assert spy.seen["sources"] == videos
    assert calls["summarize"] == 0  # MP3 is offline and free, on every path
    assert ("reveal_dir", str(tmp_path / "output" / "audio")) in stub.messages


def test_a_hand_picked_file_list_still_converts(tmp_path: Path) -> None:
    """The multi-select path outlived the menu row that used to reach it.

    `_flow_mp3` still takes a hand-picked list, and the folder module simply passes a
    one-element list holding the folder. Kept covered so the capability does not rot while
    it is unreachable from the menu — restoring it is a menu row, not a rewrite.
    """
    videos = _videos(tmp_path, "a.mp4", "b.mkv", "c.mov")
    spy = _spy_batch()
    deps, stub, _ = _make_deps(tmp_path, [], convert_many=spy)

    menu._flow_mp3(deps, [videos[0], videos[2]])

    assert spy.seen["sources"] == [videos[0], videos[2]], "a subset must stay a subset"
    assert "Converted" in stub.log_text


# --------------------------------------------------------------------------- #
# Execution visibility — a long wait must not look like a hung process
# --------------------------------------------------------------------------- #
def test_the_spinner_names_the_phase_in_flight(tmp_path: Path) -> None:
    """The 2026-09-04 failure mode: a motionless "Summarizing (7 cloud calls)" for minutes,
    and the operator closes a console that is working. Each phase the stage announces has
    to reach the spinner, not only the scrollback."""
    _seed_transcript(tmp_path)
    deps, stub, _ = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])

    assert menu.run_menu(deps) == 0

    assert stub.spinner_labels == [
        "Synthesizing phase 1/2 (00:00:00-00:30:00)",
        "Synthesizing phase 2/2 (00:30:00-01:00:00)",
    ]


def test_phase_progress_still_reaches_the_scrollback(tmp_path: Path) -> None:
    """The spinner is transient; the printed lines are the history of what covered what.
    Moving progress into the spinner must not take that away."""
    _seed_transcript(tmp_path)
    deps, stub, _ = _make_deps(tmp_path, ["single", "transcript", "0", "exit"])

    assert menu.run_menu(deps) == 0

    assert "Synthesizing phase 1/2" in stub.log_text
    assert "Synthesizing phase 2/2" in stub.log_text


def test_session_log_path_is_one_file_per_launch(tmp_path: Path) -> None:
    first = menu._session_log_path(tmp_path)

    assert first.parent == tmp_path / "output" / "logs"
    assert first.suffix == ".log"
    # Sortable, so the newest session is the last one in the folder listing.
    assert re.match(r"^\d{4}-\d\d-\d\d-\d{6}$", first.stem)


def test_a_finding_is_loud_while_the_phase_chatter_stays_muted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-28 / TD-29: the summarize stage has two channels and the menu must not merge
    them. The phase lines are sixty-per-run scenery; a dropped anchor or a foreign-script
    slip is a finding the operator has to act on. Routing both through ``ui.detail`` — what
    shipped in 91d4c77 — makes the finding dimmer than the noise it sits in."""
    _write_settings(tmp_path)
    video = _lecture(tmp_path / "inbox", "lecture.mp4")
    deps, stub, _ = _make_deps(tmp_path, ["single", "file", str(video), "summary", "exit"])

    assert menu.run_menu(deps) == 0

    muted = [text for level, text in stub.messages if level == "detail"]
    loud = [text for level, text in stub.messages if level == "warn"]
    assert any("Synthesizing phase" in text for text in muted)
    assert any("2 dropped" in text for text in loud), "a finding must not print as chatter"
    assert not any("2 dropped" in text for text in muted)


def test_a_partial_from_another_language_is_not_spliced_into_this_run(tmp_path: Path) -> None:
    """The partial holds finished prose and reconcile never re-reads the transcript.

    A RU run that dies at phase 2 of 4 and is resumed after switching to EN would reload
    two Russian phases, generate two English ones, and write one mixed-language document
    with nothing able to notice. Re-running the dead phases is the cheap side of that.
    """
    src = tmp_path / "lecture.txt"
    src.write_text("x", encoding="utf-8")

    ru = menu._resume_key(src, language="ru", tier="economy")
    en = menu._resume_key(src, language="en", tier="economy")
    flagship = menu._resume_key(src, language="ru", tier="flagship")

    assert len({ru, en, flagship}) == 3
    # ...and the identity is still case-insensitive on the path, which is why it is hashed.
    assert ru == menu._resume_key(tmp_path / "LECTURE.txt", language="ru", tier="economy")


# --------------------------------------------------------------------------- #
# TD-33 — what a dropped block's preview actually shows
# --------------------------------------------------------------------------- #
def test_a_dropped_block_is_previewed_head_and_tail() -> None:
    """The head is never why a block was dropped, so previewing only the head is misleading.

    ``chunk.drop_degenerate_blocks`` scores a WHOLE ~60-second block; on the operator's
    six-file run every dropped block's visible head measured 0.80-1.00 unique words, far
    above the 0.55 floor, so the repetition is always further in. The operator was shown
    ordinary speech and told it was filler.
    """
    line = "[00:39:38] " + "Real speech that opens the block. " * 3 + "loop loop loop loop"
    out = menu._dropped_excerpt(line)
    assert out.startswith("[00:39:38] Real speech")
    assert out.endswith("loop loop loop loop")
    assert " ... " in out
    assert len(out) < len(line)


def test_a_short_dropped_block_is_shown_whole() -> None:
    line = "[00:01:00] да да да да да да"
    assert menu._dropped_excerpt(line) == line


def test_the_dropped_block_report_claims_only_what_was_measured() -> None:
    """The rule measures repetition. It cannot know a block was silence, and saying so told
    the operator not to check the one thing worth checking (TD-33)."""
    ui = StubUI()
    menu._report_dropped_blocks(ui, ("[00:00:00] " + "a b " * 40,))
    text = ui.log_text
    assert "filler" not in text
    assert "silence" not in text
    assert "repeats itself" in text
