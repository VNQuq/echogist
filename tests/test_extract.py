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
# _default_ffmpeg_exe — F11 missing-binary guard
# --------------------------------------------------------------------------- #
def test_default_ffmpeg_exe_missing_package_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    with pytest.raises(ExtractError, match="imageio-ffmpeg is not installed"):
        extract._default_ffmpeg_exe()


def test_default_ffmpeg_exe_corrupt_binary_fails_loud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys
    import types

    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: str(tmp_path / "ghost-ffmpeg")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    with pytest.raises(ExtractError, match="missing or corrupt"):
        extract._default_ffmpeg_exe()
