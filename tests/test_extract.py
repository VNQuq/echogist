"""Extract-stage tests (T4).

No real ffmpeg runs and no media fixture: the binary is reached through the
``ffmpeg_exe`` + ``runner`` seams, so every orchestration branch — argv assembly,
dated/deduped naming, the F11 missing-binary guard, and the fail-loud non-zero /
empty-output paths — is unit-tested off-process. The actual mp3 conversion is the
§12.3 smoke.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from echogist import extract
from echogist.extract import ExtractError


def _recorder() -> tuple[list[list[str]], extract.Runner]:
    """A runner that records argv and reports success, writing the output file."""
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        Path(argv[-1]).write_bytes(b"ID3")  # pretend ffmpeg wrote the mp3
        return 0, ""

    return calls, runner


def test_explicit_out_path_overrides_the_computed_name(tmp_path: Path) -> None:
    # The batch runner reserves every name up front (so two colliding stems cannot race
    # for one file) and hands each worker its own path. Omitting it must keep the
    # original dated/deduped behaviour, which every other test here already covers.
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"fake video")
    out_dir = tmp_path / "audio"
    out_dir.mkdir()
    reserved = out_dir / "reserved-name.mp3"
    _calls, runner = _recorder()

    path = extract.extract_audio(
        source,
        out_dir,
        today=date(2026, 8, 11),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
        out_path=reserved,
    )

    assert path == reserved
    assert not (out_dir / "2026-08-11-lecture.mp3").exists()


# --------------------------------------------------------------------------- #
# is_mp3
# --------------------------------------------------------------------------- #
def test_is_mp3_true_any_case() -> None:
    assert extract.is_mp3(Path("a.mp3"))
    assert extract.is_mp3(Path("A.MP3"))


def test_is_mp3_false_for_video_and_other_audio() -> None:
    assert not extract.is_mp3(Path("clip.mp4"))
    assert not extract.is_mp3(Path("voice.wav"))
    assert not extract.is_mp3(Path("noext"))


# --------------------------------------------------------------------------- #
# extract_audio — happy path
# --------------------------------------------------------------------------- #
def test_extract_audio_writes_dated_mp3(tmp_path: Path) -> None:
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"fake video")
    out_dir = tmp_path / "audio"
    calls, runner = _recorder()

    path = extract.extract_audio(
        source,
        out_dir,
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )

    assert path == out_dir / "2026-06-15-lecture.mp3"
    assert path.is_file()


def test_extract_audio_creates_out_dir(tmp_path: Path) -> None:
    source = tmp_path / "v.mkv"
    source.write_bytes(b"x")
    out_dir = tmp_path / "nested" / "audio"
    _, runner = _recorder()
    path = extract.extract_audio(
        source,
        out_dir,
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )
    assert path.is_file()


def test_extract_audio_argv_drops_video_and_targets_mp3(tmp_path: Path) -> None:
    source = tmp_path / "talk.mov"
    source.write_bytes(b"x")
    calls, runner = _recorder()
    path = extract.extract_audio(
        source,
        tmp_path / "out",
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )
    argv = calls[0]
    assert argv[0] == "/fake/ffmpeg"
    assert "-nostdin" in argv  # never block on stdin
    assert "-vn" in argv  # video track dropped
    assert argv[argv.index("-acodec") + 1] == "libmp3lame"
    assert argv[argv.index("-q:a") + 1] == "2"  # VBR quality is load-bearing
    assert argv[argv.index("-f") + 1] == "mp3"  # explicit muxer (the .part hides the ext)
    assert str(source) in argv
    # ffmpeg writes a sibling .part; os.replace publishes it to the final name.
    assert argv[-1] == str(path) + ".part"


def test_extract_audio_dedups(tmp_path: Path) -> None:
    source = tmp_path / "talk.wav"
    source.write_bytes(b"x")
    out_dir = tmp_path / "out"
    _, runner = _recorder()
    p1 = extract.extract_audio(
        source,
        out_dir,
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )
    p2 = extract.extract_audio(
        source,
        out_dir,
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )
    assert p1.name == "2026-06-15-talk.mp3"
    assert p2.name == "2026-06-15-talk-2.mp3"


# --------------------------------------------------------------------------- #
# extract_audio — failure modes (fail loud, return to menu)
# --------------------------------------------------------------------------- #
def test_extract_audio_missing_input_fails_loud(tmp_path: Path) -> None:
    _, runner = _recorder()
    with pytest.raises(ExtractError, match="not found"):
        extract.extract_audio(
            tmp_path / "nope.mp4",
            tmp_path / "out",
            ffmpeg_exe="/fake/ffmpeg",
            runner=runner,
        )


def test_extract_audio_nonzero_exit_fails_loud(tmp_path: Path) -> None:
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"x")

    def runner(argv: list[str]) -> tuple[int, str]:
        return 1, "Invalid data found when processing input"

    with pytest.raises(ExtractError, match="ffmpeg failed"):
        extract.extract_audio(
            source,
            tmp_path / "out",
            log=lambda _m: None,
            ffmpeg_exe="/fake/ffmpeg",
            runner=runner,
        )


def test_extract_audio_failure_leaves_no_partial(tmp_path: Path) -> None:
    # A real interrupted ffmpeg writes some bytes then exits non-zero; the .part
    # must be cleaned up so no half-written mp3 survives in output/audio/.
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"x")
    out_dir = tmp_path / "out"

    def runner(argv: list[str]) -> tuple[int, str]:
        Path(argv[-1]).write_bytes(b"half an mp3")  # partial written to the .part
        return 1, "interrupted"

    with pytest.raises(ExtractError, match="ffmpeg failed"):
        extract.extract_audio(
            source, out_dir, log=lambda _m: None, ffmpeg_exe="/fake/ffmpeg", runner=runner
        )
    assert list(out_dir.iterdir()) == []  # neither the .part nor a final mp3 remains


def test_extract_audio_silent_no_output_fails_loud(tmp_path: Path) -> None:
    source = tmp_path / "v.mp4"
    source.write_bytes(b"x")

    def runner(argv: list[str]) -> tuple[int, str]:
        return 0, ""  # success exit but never wrote the file

    with pytest.raises(ExtractError, match="produced no output"):
        extract.extract_audio(
            source,
            tmp_path / "out",
            log=lambda _m: None,
            ffmpeg_exe="/fake/ffmpeg",
            runner=runner,
        )


# --------------------------------------------------------------------------- #
# Duration probe + progress parsing (pure helpers)
# --------------------------------------------------------------------------- #
def test_parse_duration_reads_ffmpeg_line() -> None:
    stderr = "  Input #0, mov\n  Duration: 01:02:03.50, start: 0.000000, bitrate: 128 kb/s\n"
    assert extract._parse_duration(stderr) == 3723.5


def test_parse_duration_none_when_absent_or_na() -> None:
    assert extract._parse_duration("no duration here") is None
    assert extract._parse_duration("Duration: N/A, bitrate: N/A") is None


def test_parse_out_time_us_reads_microseconds() -> None:
    assert extract._parse_out_time_us("out_time_us=2500000\n") == 2_500_000.0
    assert extract._parse_out_time_us("out_time_us=N/A\n") is None  # early, pre-first-frame
    assert extract._parse_out_time_us("progress=continue\n") is None


# --------------------------------------------------------------------------- #
# extract_audio — live progress path (probe duration → stream → fraction)
# --------------------------------------------------------------------------- #
def test_extract_audio_streams_progress_fractions(tmp_path: Path) -> None:
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"fake video")
    seen: list[float] = []
    stream_argv: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:  # the duration probe
        return 1, "Duration: 00:00:10.00, start: 0.0\nAt least one output file"

    def stream_runner(argv: list[str], on_line: object) -> tuple[int, str]:
        assert callable(on_line)
        stream_argv.append(argv)
        on_line("out_time_us=2500000\n")  # 2.5s / 10s
        on_line("progress=continue\n")  # ignored
        on_line("out_time_us=5000000\n")  # 5s / 10s
        on_line("out_time_us=20000000\n")  # past end → clamps to 1.0
        on_line("progress=end\n")
        Path(argv[-1]).write_bytes(b"ID3")
        return 0, ""

    path = extract.extract_audio(
        source,
        tmp_path / "audio",
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
        stream_runner=stream_runner,
        progress=seen.append,
    )

    assert path.is_file()
    assert seen == [0.25, 0.5, 1.0]  # clamped, no out-of-range fraction
    assert "-progress" in stream_argv[0]  # streaming argv asks ffmpeg for progress
    assert "pipe:1" in stream_argv[0]


def test_extract_audio_unknown_duration_falls_back_to_plain_runner(tmp_path: Path) -> None:
    # A probe that can't read a duration must NOT stream (no honest fraction to show);
    # extraction still succeeds via the plain capture-at-end runner.
    source = tmp_path / "clip.mkv"
    source.write_bytes(b"x")
    seen: list[float] = []
    used_stream = False

    def runner(argv: list[str]) -> tuple[int, str]:
        # First call = probe (no Duration); second = the conversion (writes the file).
        if "-f" in argv:  # the conversion argv (probe argv has no muxer flag)
            Path(argv[-1]).write_bytes(b"ID3")
            return 0, ""
        return 1, "Duration: N/A"

    def stream_runner(argv: list[str], on_line: object) -> tuple[int, str]:
        nonlocal used_stream
        used_stream = True
        return 0, ""

    path = extract.extract_audio(
        source,
        tmp_path / "audio",
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
        stream_runner=stream_runner,
        progress=seen.append,
    )

    assert path.is_file()
    assert not used_stream  # unknown duration → no streaming
    assert seen == []  # nothing fraudulent reported


def test_extract_audio_no_progress_skips_probe_and_streaming(tmp_path: Path) -> None:
    # The MP3-without-bar callers (and the rest of the suite) pass no progress: there
    # must be no probe call and a single plain conversion, argv unchanged (no -progress).
    source = tmp_path / "talk.wav"
    source.write_bytes(b"x")
    calls, runner = _recorder()

    path = extract.extract_audio(
        source,
        tmp_path / "audio",
        today=date(2026, 6, 15),
        log=lambda _m: None,
        ffmpeg_exe="/fake/ffmpeg",
        runner=runner,
    )

    assert path.is_file()
    assert len(calls) == 1  # no separate probe call
    assert "-progress" not in calls[0]


# --------------------------------------------------------------------------- #
# default_ffmpeg_exe — F11 missing-binary guard
# --------------------------------------------------------------------------- #
def test_default_ffmpeg_exe_missing_package_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    with pytest.raises(ExtractError, match="imageio-ffmpeg is not installed"):
        extract.default_ffmpeg_exe()


def test_default_ffmpeg_exe_corrupt_binary_fails_loud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys
    import types

    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: str(tmp_path / "ghost-ffmpeg")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    with pytest.raises(ExtractError, match="missing or corrupt"):
        extract.default_ffmpeg_exe()


# --------------------------------------------------------------------------- #
# probe_media — one ffmpeg -i, three facts (bulk v3 increment 1, T2)
# --------------------------------------------------------------------------- #
# Real ``ffmpeg -i`` stderr, trimmed to the lines the parser reads.
_STDERR_VIDEO_128 = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'lecture.mp4':
  Duration: 02:58:57.42, start: 0.000000, bitrate: 1502 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(tv), 1920x1080, 1350 kb/s, 30 fps
  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s
"""
_STDERR_MP3_320 = """\
Input #0, mp3, from 'talk.mp3':
  Duration: 00:45:10.03, start: 0.025057, bitrate: 320 kb/s
  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, stereo, fltp, 320 kb/s
"""
_STDERR_VBR_NO_KBPS = """\
Input #0, matroska,webm, from 'seminar.mkv':
  Duration: 01:02:03.40, start: 0.000000, bitrate: 96 kb/s
  Stream #0:0: Audio: opus, 48000 Hz, stereo, fltp
"""
_STDERR_MP3_WITH_COVER = """\
Input #0, mp3, from 'tagged.mp3':
  Duration: 01:00:00.00, start: 0.000000, bitrate: 192 kb/s
  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, stereo, fltp, 192 kb/s
  Stream #0:1: Video: mjpeg (Baseline), yuvj420p(pc), 500x500, 90k tbr (attached pic)
"""
_STDERR_SILENT_VIDEO = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'screencast.mp4':
  Duration: 00:10:00.00, start: 0.000000, bitrate: 900 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(tv), 1280x720, 900 kb/s, 30 fps
"""
_STDERR_UNREADABLE = """\
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x55] moov atom not found
broken.mp4: Invalid data found when processing input
"""


def _stderr_runner(text: str) -> extract.Runner:
    def runner(argv: list[str]) -> tuple[int, str]:
        assert argv[-1] == "/media/file"  # the source is the last argv element
        return 1, text  # ffmpeg -i ALWAYS exits non-zero; the code carries no signal

    return runner


@pytest.mark.parametrize(
    ("stderr", "duration", "kbps", "has_video"),
    [
        (_STDERR_VIDEO_128, 10737.42, 128.0, True),
        (_STDERR_MP3_320, 2710.03, 320.0, False),
        # VBR opus: no ``kb/s`` on the Audio line. None, never a guess — the container
        # average on the Duration line is deliberately not consulted.
        (_STDERR_VBR_NO_KBPS, 3723.40, None, False),
        # Album art is not a picture track: an mp3 tagged with a cover must stay audio, or
        # the size-derived bitrate fallback vanishes for a whole tagged library.
        (_STDERR_MP3_WITH_COVER, 3600.0, 192.0, False),
        # A silent screencast: a real video stream, and no audio bitrate to report.
        (_STDERR_SILENT_VIDEO, 600.0, None, True),
        # Corrupt container: no Duration line at all — the definition of unreadable.
        (_STDERR_UNREADABLE, None, None, False),
    ],
)
def test_probe_media_reads_all_three_facts_from_one_run(
    stderr: str, duration: float | None, kbps: float | None, has_video: bool
) -> None:
    probe = extract.probe_media(Path("/media/file"), "/fake/ffmpeg", _stderr_runner(stderr))

    if duration is None:
        assert probe.duration is None
    else:
        assert probe.duration == pytest.approx(duration)
    assert probe.audio_kbps == kbps
    assert probe.has_video is has_video


def test_probe_media_takes_the_audio_stream_bitrate_not_the_container_average() -> None:
    # The regression that matters for the 1b re-encode rule: the Duration line says
    # 1502 kb/s (video-dominated) while the soundtrack is 128. Reading the wrong one
    # would trip a >= 320 kb/s trigger on every ordinary lecture video.
    probe = extract.probe_media(
        Path("/media/file"), "/fake/ffmpeg", _stderr_runner(_STDERR_VIDEO_128)
    )

    assert probe.audio_kbps == 128.0


def test_probe_media_uses_a_single_ffmpeg_run() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 1, _STDERR_VIDEO_128

    extract.probe_media(Path("/media/file"), "/fake/ffmpeg", runner)

    assert len(calls) == 1
    assert calls[0] == ["/fake/ffmpeg", "-nostdin", "-i", "/media/file"]


def test_probe_runner_turns_a_hang_into_a_loud_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A file on a dead network share: without the timeout this blocks forever, hanging a
    # whole folder scan and leaving the child alive past Ctrl-C.
    import subprocess

    def hang(*_args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=float(kwargs["timeout"]))  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(ExtractError, match="did not respond within"):
        extract._default_probe_runner(["/fake/ffmpeg", "-nostdin", "-i", "//share/gone.mp4"])


def test_the_conversion_runner_stays_unbounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # The timeout belongs to the PROBE only. A three-hour lecture legitimately spends
    # minutes inside ffmpeg, and a bound on the shared runner would kill it mid-convert.
    import subprocess
    import types

    seen: dict[str, object] = {}

    def fake_run(_argv: list[str], **kwargs: object) -> object:
        seen.update(kwargs)
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    extract._default_runner(["/fake/ffmpeg", "-i", "in.mp4", "out.mp3"])

    assert seen["timeout"] is None
