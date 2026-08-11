"""Batch MP3 conversion tests.

No real ffmpeg and no media fixture: the ``extract_fn`` seam stands in for the whole
conversion, so every orchestration branch — up-front name planning, the pool, the
per-file failure isolation, cancellation, and the report shape — is unit-tested
off-process and offline (killswitch). :class:`Cancellation`'s process handling is
exercised against a real short-lived subprocess, which needs no ffmpeg.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from echogist import batch, extract, naming
from echogist.extract import ExtractError

TODAY = date(2026, 8, 11)


def _source(tmp_path: Path, name: str, *, size: int = 8) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _fake_extract(
    *, fail_on: set[str] | None = None, error: type[Exception] = ExtractError
) -> batch.ExtractFn:
    """A stand-in for ``extract_audio``: writes the reserved out_path, or raises."""

    def extract_fn(source: Path, out_dir: Path, **kwargs: object) -> Path:
        out_path = kwargs["out_path"]
        assert isinstance(out_path, Path)  # batch always plans and passes one
        if fail_on and source.name in fail_on:
            raise error(f"ffmpeg failed on {source.name}")
        out_path.write_bytes(b"ID3")
        return out_path

    return extract_fn


# --------------------------------------------------------------------------- #
# expand_selection
# --------------------------------------------------------------------------- #
def test_expand_selection_expands_a_directory_without_recursion(tmp_path: Path) -> None:
    _source(tmp_path, "a.mp4")
    _source(tmp_path, "b.mkv")
    _source(tmp_path, "notes.txt")  # not convertible
    nested = tmp_path / "sub"
    nested.mkdir()
    _source(nested, "deep.mp4")  # one level only — must NOT appear

    found = batch.expand_selection([tmp_path])

    assert [p.name for p in found] == ["a.mp4", "b.mkv"]


def test_expand_selection_keeps_an_explicit_file_whatever_its_extension(tmp_path: Path) -> None:
    # Naming a file explicitly IS the intent, so an exotic container is never dropped —
    # only directory EXPANSION is filtered.
    odd = _source(tmp_path, "capture.mts")

    assert batch.expand_selection([odd]) == [odd]


def test_expand_selection_includes_mp3_so_the_flow_can_ask(tmp_path: Path) -> None:
    # An mp3 in an expanded directory must reach the caller, not be dropped here: the flow
    # asks before re-encoding one, and a silent drop would make a typed directory behave
    # differently from the same files picked by hand.
    _source(tmp_path, "video.mp4")
    _source(tmp_path, "podcast.mp3")
    _source(tmp_path, "cover.jpg")

    found = batch.expand_selection([tmp_path])

    assert [p.name for p in found] == ["podcast.mp3", "video.mp4"]


def test_expand_selection_is_case_insensitive_about_extensions(tmp_path: Path) -> None:
    # Cameras and phones write .MP4/.MOV on Windows. A case-sensitive filter would answer
    # "Nothing convertible in that selection" for a whole folder of real videos.
    _source(tmp_path, "A.MP4")
    _source(tmp_path, "B.MkV")
    _source(tmp_path, "notes.TXT")

    assert [p.name for p in batch.expand_selection([tmp_path])] == ["A.MP4", "B.MkV"]


def test_expand_selection_dedupes_a_file_reachable_twice(tmp_path: Path) -> None:
    explicit = _source(tmp_path, "a.mp4")

    found = batch.expand_selection([tmp_path, explicit])

    assert found == [explicit]


# --------------------------------------------------------------------------- #
# convert_many — happy path + naming
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("workers", [1, 4])
def test_convert_many_converts_every_source(tmp_path: Path, workers: int) -> None:
    # workers=1 is the sequential path and workers>1 the pool; both are the same code,
    # so both must produce the same complete result.
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(5)]
    out_dir = tmp_path / "audio"

    report = batch.convert_many(
        sources, out_dir, workers=workers, today=TODAY, extract_fn=_fake_extract()
    )

    assert report.converted == 5
    assert report.failed == 0
    assert report.failures == ()
    assert sorted(p.name for p in out_dir.iterdir()) == [
        f"2026-08-11-clip{i}.mp3" for i in range(5)
    ]


def test_colliding_stems_get_distinct_files_in_parallel(tmp_path: Path) -> None:
    # The bug this whole reservation scheme exists for: two sources whose sanitized stems
    # collide resolve to the same dated name, and without an up-front claim one worker's
    # mp3 silently overwrites the other's. Five identical stems, five distinct outputs.
    sources = [
        _source(tmp_path, "lecture.mp4"),
        _source(tmp_path, "lecture.mkv"),
        _source(tmp_path, "lecture.mov"),
        _source(tmp_path, "lecture.webm"),
        _source(tmp_path, "lecture.avi"),
    ]
    out_dir = tmp_path / "audio"

    report = batch.convert_many(
        sources, out_dir, workers=5, today=TODAY, extract_fn=_fake_extract()
    )

    produced = [item.output for item in report.items]
    assert len(set(produced)) == 5, "each source must own a distinct output path"
    assert report.converted == 5
    assert len(list(out_dir.iterdir())) == 5


def test_real_extract_audio_honours_the_reserved_paths(tmp_path: Path) -> None:
    # The seam every other test here stubs out: the REAL extract_audio, driven by a fake
    # ffmpeg. Proves the in-memory claim survives the round trip — two colliding stems land
    # as two distinct dated files, published by extract_audio's .part -> os.replace.
    sources = [_source(tmp_path, "lecture.mp4"), _source(tmp_path, "lecture.mkv")]
    out_dir = tmp_path / "audio"

    def fake_ffmpeg(argv: list[str]) -> tuple[int, str]:
        Path(argv[-1]).write_bytes(b"ID3" * 10)  # ffmpeg writes the .part
        return 0, ""

    def extract_fn(source: Path, target_dir: Path, **kwargs: Any) -> Path:
        kwargs.pop("runner", None)  # swap the cancel-tracking runner for the fake ffmpeg
        return extract.extract_audio(
            source, target_dir, ffmpeg_exe="/fake/ffmpeg", runner=fake_ffmpeg, **kwargs
        )

    report = batch.convert_many(sources, out_dir, workers=2, today=TODAY, extract_fn=extract_fn)

    assert report.converted == 2
    assert {p.name for p in out_dir.iterdir()} == {
        "2026-08-11-lecture.mp3",
        "2026-08-11-lecture-2.mp3",
    }
    assert all(p.stat().st_size > 0 for p in out_dir.iterdir()), "no empty stub left behind"


def test_convert_many_hands_workers_the_cancellation_runner(tmp_path: Path) -> None:
    # The wire that makes Ctrl-C actually reach ffmpeg. Without it the batch still
    # converts and every other test stays green, but a cancel would wait out a two-hour
    # lecture instead of terminating it.
    seen: dict[str, Any] = {}
    cancel = batch.Cancellation()

    def extract_fn(source: Path, out_dir: Path, **kwargs: Any) -> Path:
        seen["runner"] = kwargs.get("runner")
        out_path = kwargs["out_path"]
        assert isinstance(out_path, Path)
        out_path.write_bytes(b"ID3")
        return out_path

    batch.convert_many(
        [_source(tmp_path, "a.mp4")],
        tmp_path / "audio",
        today=TODAY,
        extract_fn=extract_fn,
        cancel=cancel,
    )

    assert seen["runner"] == cancel.runner


def test_workers_actually_run_in_parallel(tmp_path: Path) -> None:
    # A barrier of 2 can only clear if two conversions are genuinely in flight at once.
    # Asserting the final report cannot tell a pool from a sequential loop, so the whole
    # batch_workers setting could regress to workers=1 with no signal.
    barrier = threading.Barrier(2, timeout=10)

    def extract_fn(source: Path, out_dir: Path, **kwargs: Any) -> Path:
        barrier.wait()
        out_path = kwargs["out_path"]
        assert isinstance(out_path, Path)
        out_path.write_bytes(b"ID3")
        return out_path

    sources = [_source(tmp_path, "a.mp4"), _source(tmp_path, "b.mp4")]

    report = batch.convert_many(
        sources, tmp_path / "audio", workers=2, today=TODAY, extract_fn=extract_fn
    )

    assert report.converted == 2, "a pool collapsed to sequential would break the barrier"


def test_report_keeps_the_operators_selection_order(tmp_path: Path) -> None:
    # Completion order in a pool is arbitrary; the report is read by a human against the
    # list they picked, so it is ordered by selection, not by who finished first.
    sources = [_source(tmp_path, f"{i:02d}-clip.mp4") for i in range(8)]

    report = batch.convert_many(
        sources, tmp_path / "audio", workers=4, today=TODAY, extract_fn=_fake_extract()
    )

    assert [item.source.name for item in report.items] == [p.name for p in sources]


# --------------------------------------------------------------------------- #
# convert_many — failure isolation
# --------------------------------------------------------------------------- #
def test_one_bad_file_does_not_kill_the_batch(tmp_path: Path) -> None:
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(4)]

    report = batch.convert_many(
        sources,
        tmp_path / "audio",
        workers=2,
        today=TODAY,
        extract_fn=_fake_extract(fail_on={"clip2.mp4"}),
    )

    assert report.converted == 3
    assert report.failed == 1
    failure = next(item for item in report.items if item.status == "failed")
    assert failure.source.name == "clip2.mp4"
    assert "ffmpeg failed" in failure.detail


def test_a_failed_file_leaves_nothing_behind(tmp_path: Path) -> None:
    # A failed file must leave the output folder holding only real artifacts — no stub, and
    # no leftover .part (extract_audio removes its own).
    out_dir = tmp_path / "audio"
    sources = [_source(tmp_path, "good.mp4"), _source(tmp_path, "bad.mp4")]

    batch.convert_many(
        sources,
        out_dir,
        workers=1,
        today=TODAY,
        extract_fn=_fake_extract(fail_on={"bad.mp4"}),
    )

    assert [p.name for p in out_dir.iterdir()] == ["2026-08-11-good.mp3"]


def test_oserror_is_caught_per_file_like_an_extract_error(tmp_path: Path) -> None:
    # A locked output dir on Windows surfaces as OSError, not ExtractError; it must
    # degrade the same way rather than bubbling out and destroying the whole batch.
    sources = [_source(tmp_path, "a.mp4"), _source(tmp_path, "b.mp4")]

    report = batch.convert_many(
        sources,
        tmp_path / "audio",
        workers=1,
        today=TODAY,
        extract_fn=_fake_extract(fail_on={"a.mp4"}, error=OSError),
    )

    assert report.failed == 1
    assert report.converted == 1


def test_an_unexpected_exception_type_still_only_costs_that_one_file(tmp_path: Path) -> None:
    # The promise is "one bad file never kills the batch", not "one bad file that fails in
    # a way we predicted". A stage raising something other than ExtractError/OSError must
    # still land in the report rather than taking the other conversions down with it.
    sources = [_source(tmp_path, "a.mp4"), _source(tmp_path, "weird.mp4")]

    report = batch.convert_many(
        sources,
        tmp_path / "audio",
        workers=1,
        today=TODAY,
        extract_fn=_fake_extract(fail_on={"weird.mp4"}, error=RuntimeError),
    )

    assert report.converted == 1
    assert report.failed == 1
    assert "weird.mp4" in report.failures[0].detail


def test_a_silent_exception_still_reports_its_type(tmp_path: Path) -> None:
    # An empty str(exc) would leave a blank cell in the failure table; fall back to the
    # class name so every failed row says something the operator can act on.
    class Silent(Exception):
        pass

    def extract_fn(source: Path, out_dir: Path, **kwargs: Any) -> Path:
        raise Silent  # no message at all -> str(exc) == ""

    report = batch.convert_many(
        [_source(tmp_path, "a.mp4")],
        tmp_path / "audio",
        today=TODAY,
        extract_fn=extract_fn,
    )

    assert report.failures[0].detail == "Silent"


def test_extra_items_are_folded_into_the_report(tmp_path: Path) -> None:
    # Skips decided by the caller (an already-mp3 the operator declined to re-encode)
    # ride along so the report covers the whole selection, not just what the pool saw.
    skipped = (batch.BatchItem(tmp_path / "old.mp3", "skipped", detail="already an MP3"),)

    report = batch.convert_many(
        [_source(tmp_path, "a.mp4")],
        tmp_path / "audio",
        today=TODAY,
        extract_fn=_fake_extract(),
        extra=skipped,
    )

    assert report.skipped == 1
    assert report.converted == 1
    assert len(report.items) == 2
    assert report.failures == (), "a skip the operator asked for is not a failure"
    assert [i.detail for i in report.items if i.status == "skipped"] == ["already an MP3"]


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_a_pre_cancelled_run_converts_nothing_and_writes_nothing(tmp_path: Path) -> None:
    # No KeyboardInterrupt here, so convert_many RETURNS (it does not raise): the flag
    # alone just makes every worker short-circuit, and nothing reaches the disk.
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(3)]
    cancel = batch.Cancellation()
    cancel.cancel()

    report = batch.convert_many(
        sources,
        tmp_path / "audio",
        workers=2,
        today=TODAY,
        extract_fn=_fake_extract(),
        cancel=cancel,
    )

    assert report.cancelled == 3
    assert report.converted == 0
    assert list((tmp_path / "audio").iterdir()) == []


def test_ctrl_c_mid_batch_raises_with_a_partial_report(tmp_path: Path) -> None:
    # The operator interrupts after the second file: the already-converted mp3s must
    # survive, the rest come back as cancelled, and the partial report rides the exception
    # rather than being thrown away with the batch.
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(6)]
    seen = {"n": 0}

    def on_item(_item: batch.BatchItem) -> None:
        seen["n"] += 1
        if seen["n"] == 2:
            raise KeyboardInterrupt  # delivered on the main thread, as a real Ctrl-C is

    with pytest.raises(batch.BatchCancelled) as excinfo:
        batch.convert_many(
            sources,
            tmp_path / "audio",
            workers=1,
            today=TODAY,
            extract_fn=_fake_extract(),
            on_item=on_item,
        )

    report = excinfo.value.report
    assert len(report.items) == 6, "every source is accounted for, not just the finished ones"
    assert report.converted >= 2
    assert report.converted + report.cancelled == 6
    assert report.failed == 0, "a cancel must never be reported as a broken file"


def test_a_file_killed_by_the_cancel_is_cancelled_not_failed(tmp_path: Path) -> None:
    # A terminated ffmpeg exits non-zero, which surfaces as ExtractError. That is the
    # operator's Ctrl-C, not a broken video — tabling it under "Not converted" with an
    # ffmpeg error would tell them their files are corrupt when they simply stopped.
    cancel = batch.Cancellation()

    def extract_fn(source: Path, out_dir: Path, **kwargs: Any) -> Path:
        cancel.cancel()  # the terminate lands while this conversion is in flight
        raise ExtractError("ffmpeg failed (exit 255)")

    report = batch.convert_many(
        [_source(tmp_path, "a.mp4")],
        tmp_path / "audio",
        workers=1,
        today=TODAY,
        extract_fn=extract_fn,
        cancel=cancel,
    )

    assert report.cancelled == 1
    assert report.failed == 0
    assert report.failures == ()
    assert list((tmp_path / "audio").iterdir()) == []  # nothing was ever written


def test_ctrl_c_while_planning_names_returns_to_the_menu_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Interrupt window 1: before the pool exists. This used to escape as a raw
    # KeyboardInterrupt — a BaseException, so run_menu's `except Exception` backstop never
    # saw it and the app died with a traceback instead of returning to the menu.
    real = naming.dated_artifact_path
    calls = {"n": 0}

    def boom(*args: Any, **kwargs: Any) -> Path:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        result: Path = real(*args, **kwargs)
        return result

    monkeypatch.setattr(naming, "dated_artifact_path", boom)
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(4)]
    out_dir = tmp_path / "audio"

    with pytest.raises(batch.BatchCancelled) as excinfo:
        batch.convert_many(sources, out_dir, workers=2, today=TODAY, extract_fn=_fake_extract())

    assert excinfo.value.report.converted == 0
    assert list(out_dir.iterdir()) == [], "planning must never leave a file behind"


def test_ctrl_c_while_dispatching_returns_to_the_menu_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Interrupt window 2: mid-submit. The dangerous one — cancel never fired, so the
    # executor's shutdown(wait=True) joined workers whose ffmpeg nobody had killed, and
    # the console froze for the length of the longest lecture.
    class _InterruptingPool(ThreadPoolExecutor):
        submitted = 0

        def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
            type(self).submitted += 1
            if type(self).submitted == 3:
                raise KeyboardInterrupt
            return super().submit(fn, *args, **kwargs)

    monkeypatch.setattr(batch, "ThreadPoolExecutor", _InterruptingPool)
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(6)]
    out_dir = tmp_path / "audio"

    with pytest.raises(batch.BatchCancelled) as excinfo:
        batch.convert_many(sources, out_dir, workers=2, today=TODAY, extract_fn=_fake_extract())

    report = excinfo.value.report
    # The two files that were dispatched still report; the four never submitted simply are
    # not in the report. What matters is that this is a clean BatchCancelled, not a crash.
    assert report.failed == 0
    assert all(item.status in {"converted", "cancelled"} for item in report.items)


def test_a_second_ctrl_c_during_the_drain_keeps_the_partial_report(tmp_path: Path) -> None:
    # An impatient operator hits Ctrl-C again because the drain prints nothing. That must
    # not throw away what already converted.
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(4)]
    seen = {"n": 0}

    def on_item(_item: batch.BatchItem) -> None:
        seen["n"] += 1
        if seen["n"] in (2, 3):  # the first interrupt, then one during the drain
            raise KeyboardInterrupt

    with pytest.raises(batch.BatchCancelled) as excinfo:
        batch.convert_many(
            sources,
            tmp_path / "audio",
            workers=1,
            today=TODAY,
            extract_fn=_fake_extract(),
            on_item=on_item,
        )

    report = excinfo.value.report
    assert len(report.items) == 4, "every source is still accounted for"
    assert report.converted >= 1


def test_a_third_ctrl_c_tears_everything_down(tmp_path: Path) -> None:
    # The hard escape: an interrupt must never become un-interruptible. The third one
    # propagates as a real KeyboardInterrupt instead of being absorbed again.
    sources = [_source(tmp_path, f"clip{i}.mp4") for i in range(5)]

    def on_item(_item: batch.BatchItem) -> None:
        raise KeyboardInterrupt  # every collect is interrupted

    with pytest.raises(KeyboardInterrupt):
        batch.convert_many(
            sources,
            tmp_path / "audio",
            workers=1,
            today=TODAY,
            extract_fn=_fake_extract(),
            on_item=on_item,
        )


def test_planning_creates_no_files_at_all(tmp_path: Path) -> None:
    # The invariant that makes a kill safe: names are claimed in memory, so there is
    # nothing on disk to orphan. Publishing stays extract_audio's .part -> os.replace.
    sources = [_source(tmp_path, "lecture.mp4"), _source(tmp_path, "lecture.mkv")]
    out_dir = tmp_path / "audio"

    planned = batch._plan_output_paths(sources, out_dir, TODAY)

    assert [p.name for p in planned] == ["2026-08-11-lecture.mp3", "2026-08-11-lecture-2.mp3"]
    assert list(out_dir.iterdir()) == [], "a claim is in-memory; nothing is written"


def test_cancellation_terminates_a_running_process() -> None:
    # The half of cancel that makes Ctrl-C feel immediate: a conversion already in flight
    # is killed, not waited out. A sleeping Python stands in for a long ffmpeg.
    cancel = batch.Cancellation()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    result: dict[str, tuple[int, str]] = {}

    worker = threading.Thread(target=lambda: result.update(run=cancel.runner(argv)))
    worker.start()
    for _ in range(100):  # wait for the process to actually be registered
        if cancel._live:  # noqa: SLF001 - asserting the registry did its job
            break
        threading.Event().wait(0.05)
    cancel.cancel()
    worker.join(timeout=15)

    assert not worker.is_alive(), "terminate() must end the tracked process"
    assert result["run"][0] != 0, "a terminated process reports a non-zero exit"
    assert cancel.cancelled


def test_cancel_escalates_to_a_kill_when_sigterm_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # cancel() only asks politely. A child that ignores the request would otherwise keep
    # communicate() blocked forever, hanging the batch past every Ctrl-C handler — a
    # console no keystroke can rescue. After the grace window it gets killed outright.
    monkeypatch.setattr(batch, "_TERMINATE_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(batch, "_CANCEL_POLL_SECONDS", 0.1)
    cancel = batch.Cancellation()
    deaf = [
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
    ]
    result: dict[str, tuple[int, str]] = {}
    worker = threading.Thread(target=lambda: result.update(run=cancel.runner(deaf)))
    worker.start()
    for _ in range(100):  # wait until the process is registered
        if cancel._live:  # noqa: SLF001 - asserting the registry did its job
            break
        threading.Event().wait(0.05)

    cancel.cancel()
    worker.join(timeout=20)

    assert not worker.is_alive(), "a SIGTERM-deaf child must not hang the batch"
    assert result["run"][0] != 0


def test_runner_kills_a_process_spawned_during_a_cancel() -> None:
    # The race the registration guard exists for: cancel lands between Popen and the
    # registry insert. The runner must re-check under the lock and kill it anyway.
    cancel = batch.Cancellation()
    cancel.cancel()

    code, _out = cancel.runner([sys.executable, "-c", "import time; time.sleep(30)"])

    assert code != 0


def test_runner_returns_output_like_the_default_runner() -> None:
    # Contract parity with extract._default_runner: (returncode, merged text).
    cancel = batch.Cancellation()

    code, out = cancel.runner([sys.executable, "-c", "import sys; sys.stderr.write('boom')"])

    assert code == 0
    assert "boom" in out


def test_runner_survives_a_terminate_on_an_already_finished_process() -> None:
    # cancel() runs over a snapshot of live processes; one may exit in between. That must
    # not raise, or a cancel would fail loudly for a purely cosmetic reason.
    cancel = batch.Cancellation()
    proc = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE, text=True)
    proc.wait()
    cancel._live.add(proc)  # noqa: SLF001 - simulating the exit-during-cancel window

    cancel.cancel()  # must not raise

    assert cancel.cancelled
