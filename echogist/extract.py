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
from dataclasses import dataclass
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
# ffmpeg prints one ``Stream #0:1: Audio: mp3, 44100 Hz, stereo, fltp, 128 kb/s`` line per
# audio stream. The trailing ``kb/s`` is the AUDIO track's own bitrate, unlike the
# ``bitrate:`` field on the Duration line, which is the container average and on a video
# file is dominated by the picture track.
_AUDIO_STREAM_RE = re.compile(r"^\s*Stream #\S+?:\s*Audio:\s*(?P<rest>.*)$", re.MULTILINE)
_AUDIO_KBPS_RE = re.compile(r"(?P<kbps>\d+(?:\.\d+)?)\s*kb/s")
# A real picture track. ``(attached pic)`` marks embedded cover art — an mp3 with album
# art carries a Video stream that is a single JPEG, and treating it as video would strip
# the size-derived bitrate fallback from every tagged mp3 in a library.
_VIDEO_STREAM_RE = re.compile(r"^\s*Stream #\S+?:\s*Video:\s*(?P<rest>.*)$", re.MULTILINE)
# How long a header probe may take before the file counts as unreachable.
_PROBE_TIMEOUT_SECONDS = 30.0


class ExtractError(Exception):
    """A recoverable extraction failure. Print it, return to the menu."""


def is_mp3(path: Path) -> bool:
    """True if ``path`` is already an mp3 — the menu skips extraction then (§3)."""
    return path.suffix.lower() == ".mp3"


def default_ffmpeg_exe() -> str:
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


def _default_runner(argv: list[str], *, timeout: float | None = None) -> tuple[int, str]:
    # argv is ours (no user-built shell string, shell=False) — safe to spawn.
    # encoding/errors are explicit: on Windows ffmpeg writes stderr in the OEM
    # console codepage, and the default locale-strict decode would raise
    # UnicodeDecodeError *inside* subprocess.run — crashing the very fail-loud
    # path that is meant to surface a friendly ExtractError. utf-8 + replace
    # keeps the diagnostic readable and never throws.
    #
    # ``timeout`` defaults to None because a real conversion legitimately runs for
    # minutes on a long lecture and must never be cut short. Only the PROBE passes a
    # bound (see ``_default_probe_runner``), where any wait past a second or two means
    # the file is not really reachable.
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractError(
            f"ffmpeg did not respond within {timeout:.0f}s for: {argv[-1]}. "
            "A file on an unreachable network share or an unresponsive cloud mount "
            "does this; check that the path is available."
        ) from exc
    return proc.returncode, proc.stderr


def _default_probe_runner(argv: list[str]) -> tuple[int, str]:
    """:data:`Runner` for ``ffmpeg -i`` probes: the default spawn, but BOUNDED.

    A probe reads a container header and exits in milliseconds. Without a bound, one
    file on a dead SMB share blocks ``subprocess.run`` forever, which hangs a whole
    folder scan and leaves the child alive after Ctrl-C. The timeout turns that file
    into one ``ExtractError`` the caller counts as unreadable, and the scan moves on.
    """
    return _default_runner(argv, timeout=_PROBE_TIMEOUT_SECONDS)


def _probe_runner_for(runner: Runner) -> Runner:
    """The probe variant of ``runner``: the shared default gains the timeout above; an
    injected runner is used unchanged (a test's fake cannot hang, and the batch pool's
    cancellable runner is already bounded by its own terminate/kill grace window)."""
    return _default_probe_runner if runner is _default_runner else runner


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


def _parse_audio_kbps(text: str) -> float | None:
    """The AUDIO track's bitrate in kb/s from ffmpeg's stream lines, or None.

    Reads the per-stream ``Audio:`` line, never the ``bitrate:`` field on the
    ``Duration:`` line: that one is the container average, so on a video file it reports
    the picture track and would read 1500-4000 kb/s for a 128 kb/s soundtrack.

    None when no audio stream carries a ``kb/s`` figure, which is normal for VBR in some
    containers. The caller decides whether a size-derived fallback is safe; None must
    never be treated as zero or as a licence to guess.
    """
    for match in _AUDIO_STREAM_RE.finditer(text):
        kbps = _AUDIO_KBPS_RE.search(match.group("rest"))
        if kbps:
            return float(kbps.group("kbps"))
    return None


def _parse_has_video(text: str) -> bool:
    """True when the file carries a real picture track.

    ``(attached pic)`` streams are excluded: an mp3 with embedded album art announces a
    Video stream that is one still JPEG, and counting it as video would suppress the
    size-derived bitrate fallback for every tagged mp3 in a music-managed library.
    """
    return any(
        "attached pic" not in match.group("rest") for match in _VIDEO_STREAM_RE.finditer(text)
    )


@dataclass(frozen=True)
class MediaProbe:
    """What one ``ffmpeg -i`` says about a file. Any field may be unknown.

    ``duration is None`` is the load-bearing one: it means ffmpeg printed no
    ``Duration:`` line, which is this project's definition of an unreadable file. The
    exit code is NOT that signal — ``ffmpeg -i`` with no output always exits non-zero
    ("At least one output file must be specified"), by design.
    """

    duration: float | None
    audio_kbps: float | None
    has_video: bool


def probe_media(source: Path, exe: str, runner: Runner = _default_probe_runner) -> MediaProbe:
    """Duration, audio bitrate and a picture-track flag from ONE ``ffmpeg -i`` run.

    ffmpeg prints all three to stderr while probing an input and then exits non-zero; the
    non-zero exit is expected, not a failure, and only the stderr is read. ``imageio-
    ffmpeg`` ships ffmpeg but no ffprobe, so this stderr parse is the only local route.

    One run for all three fields on purpose: spawning is the expensive part of a scan
    (roughly 0.1-0.3s per file, times a whole library), and re-walking the tree later to
    collect the bitrate would double that cost.

    The ``runner`` seam is what lets tests drive captured real stderr without spawning
    ffmpeg. Its default is bounded (see :func:`_default_probe_runner`).
    """
    _code, stderr = runner([exe, "-nostdin", "-i", str(source)])
    return MediaProbe(
        duration=_parse_duration(stderr),
        audio_kbps=_parse_audio_kbps(stderr),
        has_video=_parse_has_video(stderr),
    )


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
    out_path: Path | None = None,
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

    ``out_path`` overrides the computed destination. Only :mod:`echogist.folder` passes
    it: :func:`naming.dated_artifact_path` *selects* a free name but does not create it,
    so two concurrent conversions whose stems collide (``лекция.mp4`` + ``лекция.mkv``)
    would both resolve to the same file and one would silently overwrite the other. The
    folder runner resolves and claims every name up front, single-threaded, then hands each
    worker its own reserved path. Callers that omit it keep the original behaviour exactly.
    """
    if not source.is_file():
        raise ExtractError(f"Input file not found: {source}.")

    out_dir.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        out_path = naming.dated_artifact_path(
            out_dir, source.stem, ".mp3", fallback="audio", today=today
        )
    tmp_path = out_path.with_name(out_path.name + ".part")
    exe = ffmpeg_exe or default_ffmpeg_exe()
    log(f"Extracting audio -> {out_path.name}")

    # Live progress needs a known total: probe the duration first, then stream
    # ffmpeg's ``out_time_us`` against it. Without a callback (MP3-only callers,
    # the unit suite) or when the duration is unknowable, fall back to the plain
    # capture-at-end runner — same behaviour as before, just no bar.
    duration = (
        probe_media(source, exe, _probe_runner_for(runner)).duration
        if progress is not None
        else None
    )
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
