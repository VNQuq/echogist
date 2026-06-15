"""T4 — extract stage (imageio-ffmpeg, plan §3 / §8).

Turns a local video or non-mp3 audio source into the kept
``output/audio/<date>-<title>.mp3`` artifact, using the **bundled** ffmpeg static
binary from ``imageio-ffmpeg`` (plan §6 — no PATH ffmpeg, no winget step; it just
works on first run). Video loses its picture track (``-vn``); audio is re-encoded
to mp3. Naming is the shared F9 rule in :mod:`echogist.naming`.

The ffmpeg binary is reached through two injectable seams (``ffmpeg_exe`` +
``runner``) so the orchestration — argv assembly, the F11 missing-binary guard,
the fail-loud non-zero-exit path — is fully unit-tested without spawning ffmpeg or
shipping a media fixture. The real conversion is exercised by the §12.3 smoke.

``is_mp3`` is the predicate the menu uses to skip extraction for a source that is
already an mp3 (the audio flow's "convert only if not mp3", plan §3).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import naming

Logger = Callable[[str], object]
# A runner takes the full ffmpeg argv and returns ``(returncode, stderr_text)``.
# Injectable so tests drive success/failure without spawning a process.
Runner = Callable[[list[str]], tuple[int, str]]


class ExtractError(Exception):
    """A recoverable extraction failure. Print it, return to the menu."""


def is_mp3(path: Path) -> bool:
    """True if ``path`` is already an mp3 — the menu skips extraction then (§3)."""
    return path.suffix.lower() == ".mp3"


def _default_ffmpeg_exe() -> str:
    """Locate the bundled ffmpeg, or raise the F11 diagnostic."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # installed by run.bat from requirements.lock
        raise ExtractError(
            "imageio-ffmpeg is not installed — run.bat installs it from requirements.lock."
        ) from exc

    exe: str = imageio_ffmpeg.get_ffmpeg_exe()
    if not exe or not Path(exe).is_file():  # F11: present-and-usable check
        raise ExtractError(
            "The bundled ffmpeg binary is missing or corrupt; reinstall via run.bat."
        )
    return exe


def _default_runner(argv: list[str]) -> tuple[int, str]:
    # argv is ours (no user-built shell string, shell=False) — safe to spawn.
    # encoding/errors are explicit: on Windows ffmpeg writes stderr in the OEM
    # console codepage, and the default locale-strict decode would raise
    # UnicodeDecodeError *inside* subprocess.run — crashing the very fail-loud
    # path that is meant to surface a friendly ExtractError. utf-8 + replace
    # keeps the diagnostic readable and never throws.
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stderr


def extract_audio(
    source: Path,
    out_dir: Path,
    *,
    today: date | None = None,
    log: Logger = print,
    ffmpeg_exe: str | None = None,
    runner: Runner = _default_runner,
) -> Path:
    """Extract/convert ``source`` to ``out_dir/<date>-<stem>.mp3`` (deduped).

    Works for any input the bundled ffmpeg can demux: a video track is dropped
    (``-vn``) and the audio re-encoded to mp3 VBR ~q2; a non-mp3 audio file is
    converted. ``is_mp3`` sources are normally filtered out by the caller (§3) — if
    one reaches here it is still re-encoded, which is harmless but lossy, so the
    menu should prefer to skip it. Any failure (missing input, missing ffmpeg,
    non-zero exit, empty output) becomes a recoverable :class:`ExtractError`.

    The conversion is **atomic**: ffmpeg writes a sibling ``.part`` file that is
    ``os.replace``-d into the final name only on success, so an interrupted or
    failed run never leaves a half-written mp3 in ``output/audio/`` for the user to
    mistake for the real artifact (the F12-analog for audio).
    """
    if not source.is_file():
        raise ExtractError(f"Input file not found: {source}.")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = naming.dated_artifact_path(
        out_dir, source.stem, ".mp3", fallback="audio", today=today
    )
    tmp_path = out_path.with_name(out_path.name + ".part")
    exe = ffmpeg_exe or _default_ffmpeg_exe()

    argv = [
        exe,
        "-nostdin",  # never block waiting on stdin — a malformed input can't hang us
        "-y",  # overwrite a stale .part from a prior aborted run, no interactive stall
        "-i",
        str(source),
        "-vn",  # drop any video stream — audio artifact only
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",  # VBR ~190 kbps; transparent for speech, smaller than CBR 320
        "-f",
        "mp3",  # explicit muxer: the .part temp name hides the extension ffmpeg
        str(tmp_path),  # would otherwise infer the format from
    ]
    log(f"Extracting audio -> {out_path.name}")

    code, stderr = runner(argv)
    if code != 0:
        tmp_path.unlink(missing_ok=True)  # don't leave a partial .part behind
        tail = stderr.strip()[-500:] or "no ffmpeg output"
        raise ExtractError(f"ffmpeg failed (exit {code}): {tail}.")
    if not tmp_path.is_file():
        raise ExtractError("ffmpeg reported success but produced no output file.")

    os.replace(tmp_path, out_path)  # atomic publish into the deduped final name
    return out_path
