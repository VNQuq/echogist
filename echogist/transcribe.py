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

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from . import gpu, naming

Logger = Callable[[str], object]

# Default render granularity (plan §3 / TD: timecode density). One [HH:MM:SS] per
# ~this many seconds of audio instead of one per Whisper segment. faster-whisper
# emits a segment per VAD pause (~2-10s), which is far finer than a human reader or
# the summarizer needs; grouping into coarser blocks keeps real, citeable anchors
# while cutting ~85-90% of the timecode lines. Overridable via [transcript]
# block_seconds in models.toml (config is data, CLAUDE.md).
_DEFAULT_BLOCK_SECONDS = 60.0

# Progress reporter (v1.1 plan §5). Additive, killswitch-safe — the ONE pipeline-stage
# signature this overhaul touches. The value is a 0.0..1.0 completion *fraction* when the
# audio duration is known (drives the %/ETA bar); when the duration is unknown it is a
# running segment *count* (1.0, 2.0, …) which the UI renders as a no-ETA readout.
Progress = Callable[[float], None]


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


def _group_segments(
    segments: tuple[Segment, ...], block_seconds: float
) -> tuple[tuple[float, str], ...]:
    """Coalesce fine Whisper ``segments`` into coarser ``(start, text)`` blocks.

    A new block opens once a segment starts ``block_seconds`` or more after the
    current block's first segment; the block's timecode is that first ``start`` (so
    it is always a real, citeable point) and its text is the member segment texts
    space-joined. ``block_seconds <= 0`` degrades to the legacy one-block-per-segment
    behavior (no grouping). Empty input → ``()``. Pure + O(n) in one pass, so the
    boundary math and the degrade branch are unit-testable without a GPU.
    """
    if not segments:
        return ()
    if block_seconds <= 0:
        return tuple((seg.start, seg.text) for seg in segments)
    blocks: list[tuple[float, str]] = []
    block_start = segments[0].start
    buffer: list[str] = [segments[0].text]
    for seg in segments[1:]:
        if seg.start - block_start >= block_seconds:
            blocks.append((block_start, " ".join(buffer)))
            block_start = seg.start
            buffer = [seg.text]
        else:
            buffer.append(seg.text)
    blocks.append((block_start, " ".join(buffer)))
    return tuple(blocks)


def render_transcript(transcript: Transcript, block_seconds: float = _DEFAULT_BLOCK_SECONDS) -> str:
    """Render as ``[HH:MM:SS] text`` lines (one per *block*), newline-joined.

    Segments are grouped into ~``block_seconds`` blocks (see :func:`_group_segments`)
    so the saved transcript is human-readable and carries a tractable number of real
    timecodes. This is the on-disk checkpoint format and the exact text the summarizer
    reads, so every timecode it can quote actually appears (plan §3 — suppresses
    hallucinated timecodes).
    """
    blocks = _group_segments(transcript.segments, block_seconds)
    return "\n".join(f"[{format_timecode(start)}] {text}" for start, text in blocks)


def save_transcript(
    transcript: Transcript,
    out_dir: Path,
    source_stem: str,
    *,
    block_seconds: float = _DEFAULT_BLOCK_SECONDS,
    today: date | None = None,
) -> Path:
    """Write the rendered transcript to ``out_dir/<date>-<stem>.txt`` (deduped).

    The saved file is the recovery checkpoint: the menu's 'Saved transcript' entry
    re-summarizes it without re-transcribing. ``block_seconds`` sets the timecode granularity (see
    :func:`render_transcript`). Returns the path written. Naming (illegal-char strip +
    ``-2``/``-3`` dedup, F9) is the shared :mod:`echogist.naming` rule.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = naming.dated_artifact_path(
        out_dir, source_stem, ".txt", fallback="transcript", today=today
    )
    path.write_text(render_transcript(transcript, block_seconds) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Pure stream → Segment assembly + progress (T1; unit-tested without a GPU)
# --------------------------------------------------------------------------- #
class _RawSegment(Protocol):
    """The shape faster-whisper yields per segment — ``start``/``end`` seconds + ``text``.

    Structural so :func:`_collect_segments` is testable with a plain fake (no GPU, no
    faster-whisper import).
    """

    start: float
    end: float
    text: str


def _collect_segments(
    stream: Iterable[_RawSegment],
    total: float,
    progress: Progress | None = None,
) -> tuple[Segment, ...]:
    """Drain the raw segment ``stream`` into a pure :class:`Segment` tuple, emitting
    progress (plan §5). This is where the actual GPU decode happens (the stream is lazy).

    * ``total > 0`` → ``progress(min(raw.end / total, 1.0))`` per segment: a 0.0..1.0
      completion fraction the UI turns into a ``%/elapsed/ETA`` bar. Clamped so a segment
      ending past the probed duration never reports > 100%.
    * ``total == 0`` (zero-duration / unprobeable audio) → no fraction is knowable, so
      ``progress(float(count))`` reports the running segment count instead: an honest
      no-ETA readout, never a fake percentage and never a div-by-zero.
    * empty stream → no progress calls, returns ``()``.

    Keep it scoped to assembling the tuple + emitting progress — no model, no I/O — so
    the fraction math and the ``total == 0`` branch are unit-testable with fake segments.
    """
    segments: list[Segment] = []
    for raw in stream:
        segments.append(Segment(start=raw.start, end=raw.end, text=raw.text.strip()))
        if progress is not None:
            progress(min(raw.end / total, 1.0) if total > 0 else float(len(segments)))
    return tuple(segments)


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
    progress: Progress | None = None,
    log: Logger = print,
) -> Transcript:
    """Transcribe ``audio_path`` with the CT2 large-v3 model in ``model_dir``.

    Registers the bundled cuDNN/cuBLAS DLLs (``win32`` no-op elsewhere) BEFORE
    importing ``faster_whisper``, loads the model at ``compute_type`` (default
    ``int8_float16`` — int8 speed/VRAM on the full large-v3, plan §6 / TD-4), and
    streams segments into a pure :class:`Transcript`. ``language=None`` lets Whisper
    auto-detect (autolang RU/EN). The detected language + duration are logged once,
    before the loop; per-segment progress goes through the optional ``progress`` callback
    (plan §5 — the %/ETA bar replaces the old per-segment log spam). Any load/transcribe
    failure becomes a recoverable :class:`TranscribeError`.
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
        segments = _collect_segments(segment_stream, total, progress)
    except TranscribeError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail loud, return to menu
        raise TranscribeError(f"Transcription failed: {exc}.") from exc

    return Transcript(language=detected, duration=total, segments=segments)
