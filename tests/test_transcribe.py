"""Transcribe-stage tests (T3).

Only the pure half is unit-tested — the in-memory model, the ``[HH:MM:SS]``
formatting, and the artifact save. The GPU adapter (:func:`transcribe`) needs the
CUDA stack + a real model and is verified by the §12.3 real-GPU smoke, not here.
The one thing we DO assert about it off-GPU: the missing-file guard fails loud.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from echogist import transcribe
from echogist.transcribe import Segment, TranscribeError, Transcript

#: A stand-in source fingerprint: 16 hex characters, the shape naming.FINGERPRINT_HEX
#: produces. The value is arbitrary; only its shape and its stability matter here.
_FP = "0123456789abcdef"


def _transcript(*segments: Segment, language: str = "ru", duration: float = 0.0) -> Transcript:
    return Transcript(language=language, duration=duration, segments=tuple(segments))


# --------------------------------------------------------------------------- #
# format_timecode
# --------------------------------------------------------------------------- #
def test_format_timecode_basic() -> None:
    assert transcribe.format_timecode(0) == "00:00:00"
    assert transcribe.format_timecode(5) == "00:00:05"
    assert transcribe.format_timecode(65) == "00:01:05"
    assert transcribe.format_timecode(3661) == "01:01:01"


def test_format_timecode_uncapped_hours() -> None:
    # A 1.5-2.5h file (TD-4) must not wrap the hour field.
    assert transcribe.format_timecode(9000) == "02:30:00"


def test_format_timecode_truncates_and_clamps() -> None:
    assert transcribe.format_timecode(1.9) == "00:00:01"
    assert transcribe.format_timecode(-3) == "00:00:00"


# --------------------------------------------------------------------------- #
# Transcript.text
# --------------------------------------------------------------------------- #
def test_transcript_text_joins_segments() -> None:
    t = _transcript(Segment(0.0, 1.0, "Привет"), Segment(1.0, 2.0, "мир"))
    assert t.text == "Привет мир"


def test_transcript_text_empty() -> None:
    assert _transcript().text == ""


# --------------------------------------------------------------------------- #
# render_transcript
# --------------------------------------------------------------------------- #
def render_lines(t: Transcript, block_seconds: float = 60.0) -> list[str]:
    rendered = transcribe.render_transcript(t, block_seconds)
    return rendered.split("\n") if rendered else []


def test_render_transcript_groups_within_window() -> None:
    # Two segments < block_seconds apart collapse into one block; the block's
    # timecode is the FIRST segment's start, the text is space-joined.
    t = _transcript(
        Segment(0.0, 2.5, "First line"),
        Segment(2.5, 65.0, "Second line"),
    )
    assert render_lines(t, 60.0) == ["[00:00:00] First line Second line"]


def test_render_transcript_opens_new_block_past_window() -> None:
    # A segment starting >= block_seconds after the block start opens a new block.
    t = _transcript(
        Segment(0.0, 30.0, "alpha"),
        Segment(45.0, 50.0, "beta"),  # 45 < 60 from block start (0) -> same block
        Segment(70.0, 75.0, "gamma"),  # 70 >= 60 -> new block, timecode = 70
    )
    assert render_lines(t, 60.0) == [
        "[00:00:00] alpha beta",
        "[00:01:10] gamma",
    ]


def test_render_transcript_block_start_is_first_segment_start() -> None:
    # The block timecode anchors to the first member's start (here 12s, not 0); the
    # next segment starts 600s after -> new block at its own start.
    t = _transcript(
        Segment(12.0, 14.0, "one"),
        Segment(612.0, 615.0, "two"),  # 612 - 12 = 600 >= 60 -> new block at 612
    )
    assert render_lines(t, 60.0) == [
        "[00:00:12] one",
        "[00:10:12] two",
    ]


def test_render_transcript_single_segment() -> None:
    t = _transcript(Segment(5.0, 9.0, "solo"))
    assert render_lines(t, 60.0) == ["[00:00:05] solo"]


def test_render_transcript_zero_block_seconds_is_per_segment() -> None:
    # Degrade path: block_seconds <= 0 keeps the legacy one-line-per-segment render.
    t = _transcript(
        Segment(0.0, 2.5, "First line"),
        Segment(2.5, 65.0, "Second line"),
    )
    assert render_lines(t, 0.0) == [
        "[00:00:00] First line",
        "[00:00:02] Second line",
    ]


def test_render_transcript_empty() -> None:
    assert transcribe.render_transcript(_transcript()) == ""
    assert transcribe.render_transcript(_transcript(), 0.0) == ""


# --------------------------------------------------------------------------- #
# save_transcript
# --------------------------------------------------------------------------- #
def test_save_transcript_writes_a_dated_fingerprinted_file(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "Привет мир"))
    path = transcribe.save_transcript(
        t, tmp_path, "lecture", fingerprint=_FP, today=date(2026, 6, 15)
    )
    assert path == tmp_path / f"2026-06-15-lecture-{_FP}.txt"
    assert path.read_text(encoding="utf-8") == "[00:00:00] Привет мир\n"


def test_the_saved_body_carries_no_metadata_of_our_own(tmp_path: Path) -> None:
    """TD-31's load-bearing property: identity is in the NAME, never in the file.

    The body is the exact text the summarizer reads. Any line we add becomes block #1 at
    ``[00:00:00]`` (``chunk._blocks`` anchors a timecode-less line at the previous start,
    the first one at zero), and the model could quote our own metadata back with an anchor
    that PASSES validation. Pin it: every line is a real transcript block.
    """
    t = _transcript(Segment(0.0, 1.0, "Привет"), Segment(70.0, 71.0, "мир"))
    path = transcribe.save_transcript(
        t, tmp_path, "lecture", fingerprint=_FP, today=date(2026, 6, 15)
    )
    body = path.read_text(encoding="utf-8")
    assert body == transcribe.render_transcript(t) + "\n"
    assert all(line.startswith("[") for line in body.splitlines() if line.strip())
    assert _FP not in body


def test_save_transcript_groups_by_block_seconds(tmp_path: Path) -> None:
    # block_seconds threads through to render: two close segments share one block.
    t = _transcript(Segment(0.0, 2.0, "a"), Segment(3.0, 5.0, "b"))
    path = transcribe.save_transcript(
        t, tmp_path, "talk", fingerprint=_FP, block_seconds=60.0, today=date(2026, 6, 15)
    )
    assert path.read_text(encoding="utf-8") == "[00:00:00] a b\n"


def test_save_transcript_creates_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "output" / "transcripts"
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(t, out, "clip", fingerprint=_FP, today=date(2026, 6, 15))
    assert path.is_file()


def test_re_transcribing_one_recording_overwrites_rather_than_dedups(tmp_path: Path) -> None:
    """No ``-2``/``-3`` suffix any more, and that is the point (TD-31).

    Two different recordings have different fingerprints and cannot collide, so a repeat
    name means the SAME recording transcribed twice on one day. Landing on one file is
    correct and idempotent; a ``-2`` would leave two files claiming one recording, which
    is what the old dedup-suffix ambiguity was made of.
    """
    t = _transcript(Segment(0.0, 1.0, "x"))
    p1 = transcribe.save_transcript(t, tmp_path, "talk", fingerprint=_FP, today=date(2026, 6, 15))
    p2 = transcribe.save_transcript(t, tmp_path, "talk", fingerprint=_FP, today=date(2026, 6, 15))
    assert p1 == p2 == tmp_path / f"2026-06-15-talk-{_FP}.txt"
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_two_recordings_sharing_a_stem_get_different_files(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    a = transcribe.save_transcript(t, tmp_path, "talk", fingerprint=_FP, today=date(2026, 6, 15))
    b = transcribe.save_transcript(
        t, tmp_path, "talk", fingerprint="fedcba9876543210", today=date(2026, 6, 15)
    )
    assert a != b
    assert len(list(tmp_path.glob("*.txt"))) == 2


def test_save_transcript_sanitizes_illegal_chars(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(
        t, tmp_path, 'a/b:c*?"<>|d', fingerprint=_FP, today=date(2026, 6, 15)
    )
    assert ":" not in path.name and "/" not in path.name and "*" not in path.name
    assert path.is_file()


def test_save_transcript_blank_stem_falls_back(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(t, tmp_path, "///", fingerprint=_FP, today=date(2026, 6, 15))
    assert path.name == f"2026-06-15-transcript-{_FP}.txt"


# --------------------------------------------------------------------------- #
# _collect_segments — pure stream → Segment assembly + progress (T1)
# --------------------------------------------------------------------------- #
class _RawSeg:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


def test_collect_segments_emits_fractions_when_total_known() -> None:
    stream = [_RawSeg(0.0, 2.5, " a "), _RawSeg(2.5, 5.0, "b"), _RawSeg(5.0, 10.0, "c")]
    seen: list[float] = []
    segs = transcribe._collect_segments(stream, total=10.0, progress=seen.append)
    # text is stripped, order + timecodes preserved.
    assert [s.text for s in segs] == ["a", "b", "c"]
    # progress is the running completion fraction raw.end / total.
    assert seen == [0.25, 0.5, 1.0]


def test_collect_segments_clamps_fraction_at_one() -> None:
    # A segment ending past the probed duration must not report > 100%.
    stream = [_RawSeg(0.0, 12.0, "x")]
    seen: list[float] = []
    transcribe._collect_segments(stream, total=10.0, progress=seen.append)
    assert seen == [1.0]


def test_collect_segments_total_zero_reports_segment_count() -> None:
    # Zero/unprobeable duration: no fraction is knowable, so the running segment count
    # is reported instead (a no-ETA readout) — never a div-by-zero, never a fake %.
    stream = [_RawSeg(0.0, 0.0, "a"), _RawSeg(0.0, 0.0, "b"), _RawSeg(0.0, 0.0, "c")]
    seen: list[float] = []
    segs = transcribe._collect_segments(stream, total=0.0, progress=seen.append)
    assert len(segs) == 3
    assert seen == [1.0, 2.0, 3.0]


def test_collect_segments_empty_stream() -> None:
    seen: list[float] = []
    segs = transcribe._collect_segments([], total=10.0, progress=seen.append)
    assert segs == ()
    assert seen == []


def test_collect_segments_no_progress_callback_is_fine() -> None:
    # progress is optional — assembling the tuple must not require a reporter.
    segs = transcribe._collect_segments([_RawSeg(0.0, 1.0, "a")], total=10.0)
    assert [s.text for s in segs] == ["a"]


# --------------------------------------------------------------------------- #
# transcribe() — the off-GPU guard
# --------------------------------------------------------------------------- #
def test_transcribe_missing_audio_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(TranscribeError, match="not found"):
        transcribe.transcribe(tmp_path / "nope.mp3", tmp_path / "model")


class _FakeRawSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    language = "ru"
    duration = 12.5


class _FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel — no GPU, no model bytes."""

    def __init__(self, model_dir: str, device: str, compute_type: str) -> None:
        self.args = (model_dir, device, compute_type)

    def transcribe(
        self, audio_path: str, language: str | None = None
    ) -> tuple[Iterator[_FakeRawSegment], _FakeInfo]:
        segments = iter(
            [
                _FakeRawSegment(0.0, 2.0, "  Привет  "),
                _FakeRawSegment(2.0, 4.0, "мир"),
            ]
        )
        return segments, _FakeInfo()


def test_transcribe_assembles_transcript_with_fake_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = _FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    tr = transcribe.transcribe(audio, tmp_path / "model", log=lambda _m: None)

    assert tr.language == "ru"
    assert tr.duration == 12.5
    # raw text is stripped at construction; segments preserve order + timecodes.
    assert [s.text for s in tr.segments] == ["Привет", "мир"]
    assert tr.segments[0].start == 0.0
    assert tr.segments[0].end == 2.0


def test_transcribe_missing_backend_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    # None in sys.modules makes `from faster_whisper import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(TranscribeError, match="faster-whisper is not installed"):
        transcribe.transcribe(audio, tmp_path / "model")


def _install_fake_whisper(monkeypatch: pytest.MonkeyPatch, model_cls: type) -> None:
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = model_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)


def test_transcribe_model_load_failure_is_diagnosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cuDNN/cuBLAS load error must surface gpu.diagnose_import_error's F8 message,
    # not a raw exception — this is the #1 silent first-run failure (TD-3).
    class _CudnnFailModel:
        def __init__(self, model_dir: str, device: str, compute_type: str) -> None:
            raise RuntimeError("Unable to load libcudnn_ops.so")

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    _install_fake_whisper(monkeypatch, _CudnnFailModel)
    with pytest.raises(TranscribeError, match="cuDNN failed to load"):
        transcribe.transcribe(audio, tmp_path / "model", log=lambda _m: None)


def test_transcribe_midstream_failure_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Decoding happens lazily inside the segment loop; a blow-up there must become a
    # recoverable TranscribeError (F12: the partial is discarded, re-transcribe).
    def _raising_stream() -> Iterator[_FakeRawSegment]:
        yield _FakeRawSegment(0.0, 1.0, "ok so far")
        raise RuntimeError("decoder blew up mid-file")

    class _MidStreamFailModel:
        def __init__(self, model_dir: str, device: str, compute_type: str) -> None:
            pass

        def transcribe(
            self, audio_path: str, language: str | None = None
        ) -> tuple[Iterator[_FakeRawSegment], _FakeInfo]:
            return _raising_stream(), _FakeInfo()

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    _install_fake_whisper(monkeypatch, _MidStreamFailModel)
    with pytest.raises(TranscribeError, match="Transcription failed"):
        transcribe.transcribe(audio, tmp_path / "model", log=lambda _m: None)


def test_transcribe_no_speech_empty_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No detected language + zero duration: language logs 'unknown', the per-segment
    # progress branch (total > 0) is skipped, and the result is a valid empty Transcript.
    class _EmptyInfo:
        language = ""
        duration = 0.0

    class _NoSpeechModel:
        def __init__(self, model_dir: str, device: str, compute_type: str) -> None:
            pass

        def transcribe(
            self, audio_path: str, language: str | None = None
        ) -> tuple[Iterator[_FakeRawSegment], _EmptyInfo]:
            return iter([]), _EmptyInfo()

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    _install_fake_whisper(monkeypatch, _NoSpeechModel)
    tr = transcribe.transcribe(audio, tmp_path / "model", log=lambda _m: None)
    assert tr.language == ""
    assert tr.duration == 0.0
    assert tr.segments == ()


def test_transcribe_uses_int8_float16_cuda_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lock the §6/TD-4 defaults so a regression to plain float16 (or cpu) is caught.
    captured: dict[str, tuple[str, str, str]] = {}

    class _CapturingModel(_FakeWhisperModel):
        def __init__(self, model_dir: str, device: str, compute_type: str) -> None:
            captured["args"] = (model_dir, device, compute_type)
            super().__init__(model_dir, device, compute_type)

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    _install_fake_whisper(monkeypatch, _CapturingModel)
    transcribe.transcribe(audio, tmp_path / "model", log=lambda _m: None)
    assert captured["args"][1] == "cuda"
    assert captured["args"][2] == "int8_float16"
