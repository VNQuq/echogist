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
def test_render_transcript_timecoded_lines() -> None:
    t = _transcript(
        Segment(0.0, 2.5, "First line"),
        Segment(2.5, 65.0, "Second line"),
    )
    assert render_lines(t) == [
        "[00:00:00] First line",
        "[00:00:02] Second line",
    ]


def render_lines(t: Transcript) -> list[str]:
    rendered = transcribe.render_transcript(t)
    return rendered.split("\n") if rendered else []


def test_render_transcript_empty() -> None:
    assert transcribe.render_transcript(_transcript()) == ""


# --------------------------------------------------------------------------- #
# save_transcript
# --------------------------------------------------------------------------- #
def test_save_transcript_writes_dated_file(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "Привет мир"))
    path = transcribe.save_transcript(t, tmp_path, "lecture", today=date(2026, 6, 15))
    assert path == tmp_path / "2026-06-15-lecture.txt"
    assert path.read_text(encoding="utf-8") == "[00:00:00] Привет мир\n"


def test_save_transcript_creates_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "output" / "transcripts"
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(t, out, "clip", today=date(2026, 6, 15))
    assert path.is_file()


def test_save_transcript_dedups(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    p1 = transcribe.save_transcript(t, tmp_path, "talk", today=date(2026, 6, 15))
    p2 = transcribe.save_transcript(t, tmp_path, "talk", today=date(2026, 6, 15))
    p3 = transcribe.save_transcript(t, tmp_path, "talk", today=date(2026, 6, 15))
    assert p1.name == "2026-06-15-talk.txt"
    assert p2.name == "2026-06-15-talk-2.txt"
    assert p3.name == "2026-06-15-talk-3.txt"


def test_save_transcript_sanitizes_illegal_chars(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(t, tmp_path, 'a/b:c*?"<>|d', today=date(2026, 6, 15))
    assert ":" not in path.name and "/" not in path.name and "*" not in path.name
    assert path.is_file()


def test_save_transcript_blank_stem_falls_back(tmp_path: Path) -> None:
    t = _transcript(Segment(0.0, 1.0, "x"))
    path = transcribe.save_transcript(t, tmp_path, "///", today=date(2026, 6, 15))
    assert path.name == "2026-06-15-transcript.txt"


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
