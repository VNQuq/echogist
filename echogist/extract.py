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
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import naming

Logger = Callable[[str], object]
# A runner takes the full ffmpeg argv and returns ``(returncode, stderr_text)``.
# Injectable so tests drive success/failure without spawning a process.
Runner = Callable[[list[str]], tuple[int, str]]
# A progress sink: a 0.0..1.0 completion fraction the UI turns into a %/ETA bar.
Progress = Callable[[float], None]
# A streaming runner spawns ffmpeg and feeds each stdout line to ``on_line`` as it
# arrives, then returns ``(returncode, captured_text)``. Injectable so tests drive
# the live-progress path without spawning a process (mirrors the transcribe seam).
StreamRunner = Callable[[list[str], Callable[[str], None]], tuple[int, str]]

# ffmpeg prints ``Duration: HH:MM:SS.ss`` to stderr while probing an input; ``N/A``
# (unseekable / corrupt header) simply won't match, so the probe returns None.
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")
# ``-progress pipe:1`` emits ``out_time_us=<microseconds>`` lines as it encodes.
_OUT_TIME_PREFIX = "out_time_us="


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


def _default_stream_runner(argv: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
    # Live-progress spawn. stderr is merged into stdout (single pipe → no
    # fill-the-buffer deadlock while we read line-by-line), and every line is both
    # fed to ``on_line`` (which ignores non-progress lines) and captured so the
    # fail-loud path still has an ffmpeg diagnostic tail. ``-nostats`` keeps the
    # merged stream to the banner + the ``-progress`` key=value lines.
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    captured: list[str] = []
    assert proc.stdout is not None  # PIPE above guarantees it
    for line in proc.stdout:
        on_line(line)
        captured.append(line)
    proc.wait()
    return proc.returncode, "".join(captured)


def _parse_duration(text: str) -> float | None:
    """Seconds parsed from ffmpeg's ``Duration:`` stderr line, or None if absent."""
    match = _DURATION_RE.search(text)
    if not match:
        return None
    hours, minutes, seconds, frac = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + float(f"0.{frac}")


def _parse_out_time_us(line: str) -> float | None:
    """Microseconds from an ``out_time_us=`` progress line, or None for any other
    line (including the early ``out_time_us=N/A`` before the first frame)."""
    line = line.strip()
    if not line.startswith(_OUT_TIME_PREFIX):
        return None
    try:
        return float(line[len(_OUT_TIME_PREFIX) :])
    except ValueError:
        return None


def _probe_duration(source: Path, exe: str, runner: Runner) -> float | None:
    """Total media duration in seconds, or None when unknowable.

    Runs ``ffmpeg -i <source>`` with no output: ffmpeg prints the ``Duration:`` line
    to stderr and then exits non-zero ("At least one output file must be specified").
    The non-zero exit is expected here, not a failure — we only want the stderr. Uses
    the all-at-once ``runner`` seam so the probe is testable off-process. ``imageio-
    ffmpeg`` ships ffmpeg but no ffprobe, so this stderr parse is the only local route."""
    _code, stderr = runner([exe, "-nostdin", "-i", str(source)])
    return _parse_duration(stderr)


def _conversion_argv(exe: str, source: Path, tmp_path: Path, *, stream: bool) -> list[str]:
    """The ffmpeg conversion argv. With ``stream`` it adds ``-progress pipe:1 -nostats``
    so ffmpeg emits machine-readable progress to stdout; without it the argv is the
    plain (capture-at-end) form."""
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
    ]
    if stream:
        # Machine-readable progress to stdout; suppress the per-frame stderr stats so
        # the merged stream stays small (see _default_stream_runner).
        argv += ["-progress", "pipe:1", "-nostats"]
    argv += [
        "-f",
        "mp3",  # explicit muxer: the .part temp name hides the extension ffmpeg
        str(tmp_path),  # would otherwise infer the format from
    ]
    return argv


def extract_audio(
    source: Path,
    out_dir: Path,
    *,
    today: date | None = None,
    log: Logger = print,
    ffmpeg_exe: str | None = None,
    runner: Runner = _default_runner,
    stream_runner: StreamRunner = _default_stream_runner,
    progress: Progress | None = None,
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
    log(f"Extracting audio -> {out_path.name}")

    # Live progress needs a known total: probe the duration first, then stream
    # ffmpeg's ``out_time_us`` against it. Without a callback (MP3-only callers,
    # the unit suite) or when the duration is unknowable, fall back to the plain
    # capture-at-end runner — same behaviour as before, just no bar.
    duration = _probe_duration(source, exe, runner) if progress is not None else None
    if progress is not None and duration is not None and duration > 0.0:
        total = duration  # narrowed to float for the closure below
        sink = progress

        def _on_line(line: str) -> None:
            micros = _parse_out_time_us(line)
            if micros is not None:
                sink(min(micros / 1_000_000.0 / total, 1.0))

        argv = _conversion_argv(exe, source, tmp_path, stream=True)
        code, stderr = stream_runner(argv, _on_line)
    else:
        argv = _conversion_argv(exe, source, tmp_path, stream=False)
        code, stderr = runner(argv)

    if code != 0:
        tmp_path.unlink(missing_ok=True)  # don't leave a partial .part behind
        tail = stderr.strip()[-500:] or "no ffmpeg output"
        raise ExtractError(f"ffmpeg failed (exit {code}): {tail}.")
    if not tmp_path.is_file():
        raise ExtractError("ffmpeg reported success but produced no output file.")

    os.replace(tmp_path, out_path)  # atomic publish into the deduped final name
    return out_path
