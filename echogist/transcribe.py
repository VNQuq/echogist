"""T3 — transcribe stage (faster-whisper GPU, plan §3 / §12.1).

Two halves, split along the purity boundary (CLAUDE.md "pure stages"):

* **Pure, in-memory model + formatting** — :class:`Segment`, :class:`Transcript`,
  :func:`format_timecode`, :func:`render_transcript`, :func:`save_transcript`.
  No GPU, no heavy wheels; this is what the rest of the pipeline (GUARD, SUMMARIZE)
  reads and what the unit suite exercises.
* **The GPU adapter** — :func:`transcribe` loads the CT2 large-v3 model with
  ``compute_type=int8_float16`` and turns faster-whisper's segment stream into a
  pure :class:`Transcript`. ``faster_whisper`` is imported lazily (after the
  ``win32`` DLL shim) so this module imports with no CUDA stack present; the real
  GPU run is the §12.3 smoke, not a unit test.

The saved ``output/transcripts/<date>-<title>.txt`` is the artifact-based recovery
checkpoint between TRANSCRIBE and SUMMARIZE — no job.json, no history layer.
``[HH:MM:SS]`` timecodes are carried from the Whisper segments so the summarizer
quotes real timecodes instead of hallucinating them (plan §3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import gpu, naming

Logger = Callable[[str], object]


class TranscribeError(Exception):
    """A recoverable transcription failure. Print it, return to the menu."""


# --------------------------------------------------------------------------- #
# Pure in-memory model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Segment:
    """One Whisper segment: ``start``/``end`` in seconds, ``text`` already trimmed."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    """The in-memory transcribe result every downstream stage reads.

    ``language`` is the detected (autolang) code, ``duration`` the audio length in
    seconds (for progress + the §12.3 realtime_factor), ``segments`` the ordered
    timecoded text.
    """

    language: str
    duration: float
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        """Plain joined text (no timecodes) — what the GUARD/token estimate measures."""
        return " ".join(seg.text for seg in self.segments).strip()


# --------------------------------------------------------------------------- #
# Pure formatting + artifact save
# --------------------------------------------------------------------------- #
def format_timecode(seconds: float) -> str:
    """Seconds -> ``HH:MM:SS`` (zero-padded, hours uncapped). Negatives clamp to 0."""
    total = int(seconds) if seconds > 0 else 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def render_transcript(transcript: Transcript) -> str:
    """Render as ``[HH:MM:SS] text`` lines (one per segment), newline-joined.

    This is the on-disk checkpoint format and the text the summarizer reads, so the
    timecodes it can quote are real (plan §3 — suppresses hallucinated timecodes).
    """
    return "\n".join(f"[{format_timecode(seg.start)}] {seg.text}" for seg in transcript.segments)


def save_transcript(
    transcript: Transcript,
    out_dir: Path,
    source_stem: str,
    *,
    today: date | None = None,
) -> Path:
    """Write the rendered transcript to ``out_dir/<date>-<stem>.txt`` (deduped).

    The saved file is the recovery checkpoint: option 2 of the menu re-summarizes
    it without re-transcribing. Returns the path written. Naming (illegal-char
    strip + ``-2``/``-3`` dedup, F9) is the shared :mod:`echogist.naming` rule.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = naming.dated_artifact_path(
        out_dir, source_stem, ".txt", fallback="transcript", today=today
    )
    path.write_text(render_transcript(transcript) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# GPU adapter (lazy import; covered by the §12.3 real-GPU smoke, not unit tests)
# --------------------------------------------------------------------------- #
def transcribe(
    audio_path: Path,
    model_dir: Path,
    *,
    device: str = "cuda",
    compute_type: str = "int8_float16",
    language: str | None = None,
    log: Logger = print,
) -> Transcript:
    """Transcribe ``audio_path`` with the CT2 large-v3 model in ``model_dir``.

    Registers the bundled cuDNN/cuBLAS DLLs (``win32`` no-op elsewhere) BEFORE
    importing ``faster_whisper``, loads the model at ``compute_type`` (default
    ``int8_float16`` — int8 speed/VRAM on the full large-v3, plan §6 / TD-4), and
    streams segments into a pure :class:`Transcript`. ``language=None`` lets Whisper
    auto-detect (autolang RU/EN). Progress is logged per segment against the audio
    duration. Any load/transcribe failure becomes a recoverable :class:`TranscribeError`.
    """
    if not audio_path.is_file():
        raise TranscribeError(f"Audio file not found: {audio_path}.")

    try:
        gpu.register_cuda_libraries()
    except Exception as exc:  # noqa: BLE001 - add_dll_directory can raise on a stale/odd env
        raise TranscribeError(gpu.diagnose_import_error(exc)) from exc

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # the GPU wheels are installed by run.bat
        raise TranscribeError(
            "faster-whisper is not installed — run.bat installs it from requirements.lock."
        ) from exc

    try:
        model = WhisperModel(str(model_dir), device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly diagnostic
        raise TranscribeError(gpu.diagnose_import_error(exc)) from exc

    try:
        segment_stream, info = model.transcribe(str(audio_path), language=language)
        total = float(getattr(info, "duration", 0.0) or 0.0)
        detected = str(getattr(info, "language", "") or "")
        log(f"Detected language: {detected or 'unknown'}; audio {format_timecode(total)}.")

        segments: list[Segment] = []
        for raw in segment_stream:  # lazily decoded — the actual GPU work happens here
            segments.append(Segment(start=raw.start, end=raw.end, text=raw.text.strip()))
            if total > 0:
                log(f"  [{format_timecode(raw.end)} / {format_timecode(total)}]")
    except TranscribeError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail loud, return to menu
        raise TranscribeError(f"Transcription failed: {exc}.") from exc

    return Transcript(language=detected, duration=total, segments=tuple(segments))
