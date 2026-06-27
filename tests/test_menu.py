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
from pathlib import Path
from typing import Any

import pytest

from echogist import config, menu, naming, summarize
from echogist.render import RenderError
from echogist.summarize import SummarizeError, SummarizeResult, Summary, SynthesisSection
from echogist.transcribe import Segment, Transcript
from echogist.ui import StubUI


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
) -> tuple[menu.Deps, StubUI, dict[str, int]]:
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

    stub = StubUI(answers)
    deps = menu.Deps(
        ui=stub,
        extract_audio=extract_audio,
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
    deps, stub, _ = _make_deps(tmp_path, ["4"])
    assert menu.run_menu(deps) == 0
    assert "Goodbye." in stub.log_text


def test_eof_exits_cleanly(tmp_path: Path) -> None:
    # No answers at all -> the first menu select EOFs -> clean exit, no crash.
    deps, _, _ = _make_deps(tmp_path, [])
    assert menu.run_menu(deps) == 0


# --------------------------------------------------------------------------- #
# Source 1 — local file × actions
# --------------------------------------------------------------------------- #
def test_local_file_summary_runs_full_pipeline(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["1", str(src), "1", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1
    assert calls["render"] == 1
    assert calls["extract"] == 0  # summary-only never produced an mp3
    assert "Done — summary written to" in stub.log_text
    # The transcript checkpoint was saved (the recovery artifact).
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))


def test_local_file_mp3_only_skips_transcribe(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["1", str(src), "2", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0
    assert "Saved MP3:" in stub.log_text


def test_local_file_both_extracts_and_summarizes(tmp_path: Path) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["1", str(src), "3", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 1
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 1


def test_mp3_source_summary(tmp_path: Path) -> None:
    # TD-12: an mp3 input is offered a trimmed menu (no extract step); picking Summary
    # runs the full transcribe → summarize pipeline without ever producing an mp3.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, calls = _make_deps(tmp_path, ["1", str(src), "1", "4"])
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
    deps, _, calls = _make_deps(tmp_path, ["1", str(src), "transcript", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["transcribe"] == 1
    assert calls["summarize"] == 0  # transcript-only never touches the network
    assert list((tmp_path / "output" / "transcripts").glob("*.txt"))


def test_local_file_action_back_returns_to_menu(tmp_path: Path) -> None:
    # TD-13: "← Back" from the action menu does nothing and returns to the main menu.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["1", str(src), "__back__", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["extract"] == 0
    assert calls["transcribe"] == 0
    assert calls["summarize"] == 0


def test_flow_clears_screen_on_entry(tmp_path: Path) -> None:
    # TD-11: each flow clears the console on entry so prior menu chrome doesn't pile up.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(src), "1", "4"])
    assert menu.run_menu(deps) == 0
    assert ("clear", "") in stub.messages


def test_summary_reveals_summaries_folder_once(tmp_path: Path) -> None:
    # TD-14 (reopened): a Summary run reveals the SUMMARIES folder (never transcripts),
    # and only once per launch even across two summary flows.
    a = tmp_path / "a.wav"
    a.write_bytes(b"x")
    b = tmp_path / "b.wav"
    b.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(a), "1", "1", str(b), "1", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1  # once per launch, not once per summary
    assert reveals[0][1].endswith("summaries")
    assert not any(r[1].endswith("transcripts") for r in reveals)  # transcripts never pop


def test_saved_transcript_resummarize_reveals_summaries(tmp_path: Path) -> None:
    # TD-14 (reopened): re-summarizing an existing transcript produces a summary, so the
    # SUMMARIES folder pops (the deliverable) — the transcripts folder never does.
    _seed_transcript(tmp_path)
    deps, stub, _ = _make_deps(tmp_path, ["2", "0", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("summaries")


def test_transcript_only_reveals_nothing(tmp_path: Path) -> None:
    # TD-14 (reopened): a transcript-only run (mp3 input → "Transcript only") never pops
    # a folder — transcripts are intentionally not revealed.
    src = tmp_path / "clip.mp3"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(src), "transcript", "4"])
    assert menu.run_menu(deps) == 0
    assert not [m for m in stub.messages if m[0] == "reveal_dir"]


def test_mp3_conversion_drives_progress_bar(tmp_path: Path) -> None:
    # Bug #1: the video→MP3 conversion runs behind a %/ETA bar (was a frozen log line).
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(src), "2", "4"])
    assert menu.run_menu(deps) == 0
    assert ("progress", "Converting to MP3") in stub.messages


def test_mp3_only_reveals_audio_folder(tmp_path: Path) -> None:
    # Bug #2: an MP3-only run pops the audio folder (the chosen flow's output), not the
    # transcripts folder, and only once per launch.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(src), "2", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("audio")


def test_both_flow_reveals_summaries_not_audio(tmp_path: Path) -> None:
    # TD-14 (reopened): "Both" ends on a summary, so the SUMMARIES folder is the reveal
    # target — not audio (reserved for MP3-only) and never transcripts.
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(src), "3", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0][1].endswith("summaries")


def test_summary_reveal_supersedes_earlier_audio(tmp_path: Path) -> None:
    # TD-14 (reopened): an MP3-only run pops audio; a later Summary run in the same launch
    # still pops summaries (REVEAL_SUMMARY outranks the once-per-launch audio guard).
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    wav = tmp_path / "b.wav"
    wav.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(wav), "2", "1", str(mp3), "1", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 2
    assert reveals[0].endswith("audio")
    assert reveals[1].endswith("summaries")


def test_audio_not_revealed_after_summary(tmp_path: Path) -> None:
    # TD-14 (reopened): once summaries has popped, a later MP3-only run does not pop audio
    # (summaries outranks audio for the whole launch).
    wav_a = tmp_path / "a.wav"
    wav_a.write_bytes(b"x")
    wav_b = tmp_path / "b.wav"
    wav_b.write_bytes(b"x")
    deps, stub, _ = _make_deps(tmp_path, ["1", str(wav_a), "1", "1", str(wav_b), "2", "4"])
    assert menu.run_menu(deps) == 0
    reveals = [m[1] for m in stub.messages if m[0] == "reveal_dir"]
    assert len(reveals) == 1
    assert reveals[0].endswith("summaries")


def test_local_file_bad_path_returns_to_menu(tmp_path: Path, isolate_last_dir: list[Path]) -> None:
    deps, stub, calls = _make_deps(tmp_path, ["1", str(tmp_path / "nope.wav"), "4"])
    assert menu.run_menu(deps) == 0
    assert "File not found" in stub.log_text
    assert calls["transcribe"] == 0
    assert isolate_last_dir == []  # F1 → nothing remembered


def test_local_file_cancel_returns_to_menu(tmp_path: Path, isolate_last_dir: list[Path]) -> None:
    # A soft cancel from the picker is a queued None (dialog Cancel / blank entry).
    deps, stub, calls = _make_deps(tmp_path, ["1", None, "4"])
    assert menu.run_menu(deps) == 0
    assert "No file selected" in stub.log_text
    assert calls["transcribe"] == 0
    assert isolate_last_dir == []  # cancel → nothing transcribed, nothing remembered


def test_local_file_picker_remembers_directory(
    tmp_path: Path, isolate_last_dir: list[Path]
) -> None:
    src = tmp_path / "clip.wav"
    src.write_bytes(b"x")
    deps, _, calls = _make_deps(tmp_path, ["1", str(src), "1", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 1
    assert isolate_last_dir == [src.parent]  # the picked file's parent is saved


# --------------------------------------------------------------------------- #
# Source 2 — saved transcript (the recovery path)
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
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["transcribe"] == 0  # re-summarize, never re-transcribe
    assert calls["summarize"] == 1
    assert "Done — summary written to" in stub.log_text


def test_saved_transcript_cancel_returns_to_menu(tmp_path: Path) -> None:
    _seed_transcript(tmp_path)
    deps, _, calls = _make_deps(tmp_path, ["2", "__cancel__", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 0


def test_saved_transcript_empty_file(tmp_path: Path) -> None:
    _seed_transcript(tmp_path, text="   ")
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"])
    assert menu.run_menu(deps) == 0
    assert "nothing to summarize" in stub.log_text
    assert calls["summarize"] == 0


# --------------------------------------------------------------------------- #
# Cost / threshold flow (T8 wired through the menu)
# --------------------------------------------------------------------------- #
def test_over_threshold_decline_skips_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)  # any cost needs explicit confirm
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", False, "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 0
    assert "cancelled" in stub.log_text.lower()


def test_over_threshold_yes_makes_call(tmp_path: Path) -> None:
    _write_settings(tmp_path, confirm_threshold_usd=0.0)
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", True, "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1
    assert "Estimated cost" in stub.log_text
    assert "Actual cost" in stub.log_text


def test_below_threshold_proceeds_without_confirm(tmp_path: Path) -> None:
    # A cheap call must NOT consume a confirm answer — the shown estimate is the
    # acknowledgment (v1.1 §5). Queue has no confirm bool between pick and exit.
    _write_settings(tmp_path, confirm_threshold_usd=100.0)  # everything is "cheap"
    _seed_transcript(tmp_path)
    deps, _, calls = _make_deps(tmp_path, ["2", "0", "4"])
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1


# --------------------------------------------------------------------------- #
# §12 return-to-menu failure modes
# --------------------------------------------------------------------------- #
def test_missing_api_key_guides_and_skips_call(tmp_path: Path) -> None:  # F3
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"], api_key=None)
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
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"])
    assert menu.run_menu(deps) == 0
    assert "phases (+1 reconcile)" in stub.log_text  # multi-phase synthesis copy
    assert "map-reduce" not in stub.log_text  # the old map-reduce path is gone
    assert "too long" not in stub.log_text  # the old refusal is gone
    assert calls["summarize"] == 1  # reached the wire (phase-split happens inside the seam)


def test_phase_path_still_guards_an_oversize_phase(tmp_path: Path) -> None:  # TD-16 v2 / F6
    # Phase-split lowers the per-call input but does NOT repeal the overflow guard. A single
    # un-splittable block (plan_phases never cuts mid-block, K is clamped to len(blocks))
    # that exceeds the tier context is caught locally, before any paid call — not sent to
    # the wire to fail mid-run after partial spend.
    _write_settings(tmp_path, model_tier="economy")  # safe_budget = 200000 * 0.8 = 160000
    one_huge_block = "[00:00:00] " + "слово" * 60_000  # ~181K est tokens in ONE block
    _seed_transcript(tmp_path, text=one_huge_block)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"])
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
    stem = naming.summary_stem(src.stem, fallback="transcript")
    resume_path = tmp_path / "output" / "summaries" / "raw" / ".resume" / f"{stem}.json"
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
        ui=StubUI(["2", "0", "4"]),
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


def test_render_failure_after_paid_call_keeps_json(tmp_path: Path) -> None:  # F13
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"], render_error=True)
    assert menu.run_menu(deps) == 0
    assert calls["summarize"] == 1  # the call was paid
    assert "re-render it later" in stub.log_text
    # The raw result was persisted BEFORE render, so no re-pay is needed.
    assert list((tmp_path / "output" / "summaries" / "raw").glob("*.json"))


def test_summarize_error_returns_to_menu(tmp_path: Path) -> None:  # F2/F4/F5
    _seed_transcript(tmp_path)
    deps, stub, calls = _make_deps(tmp_path, ["2", "0", "4"], summarize_error="no internet")
    assert menu.run_menu(deps) == 0
    assert "no internet" in stub.log_text
    assert calls["render"] == 0


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_change_language_persists(tmp_path: Path) -> None:
    # menu -> settings -> field "language" -> value "en".
    deps, stub, _ = _make_deps(tmp_path, ["3", "1", "en", "4"])
    assert menu.run_menu(deps) == 0
    assert "Saved." in stub.log_text
    assert config.load_settings(tmp_path / "settings.json").summary_language == "en"


def test_settings_back_makes_no_change(tmp_path: Path) -> None:
    deps, stub, _ = _make_deps(tmp_path, ["3", "__back__", "4"])
    assert menu.run_menu(deps) == 0
    assert "Saved." not in stub.log_text


def test_settings_bad_threshold_unchanged(tmp_path: Path) -> None:
    # menu -> settings -> field "threshold" -> a non-numeric text value.
    deps, stub, _ = _make_deps(tmp_path, ["3", "4", "abc", "4"])
    assert menu.run_menu(deps) == 0
    assert "isn't a number" in stub.log_text


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
