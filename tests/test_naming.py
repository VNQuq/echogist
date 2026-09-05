"""Shared artifact-naming tests (F9) — the rule T3, T4 and T7 all reuse.

These exercise :mod:`echogist.naming` directly. The transcribe/extract suites
assert that their public save paths route through it; this file owns the
illegal-char-strip, fallback, and ``-2``/``-3`` dedup edge cases once.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from echogist import naming


# --------------------------------------------------------------------------- #
# sanitize_stem
# --------------------------------------------------------------------------- #
def test_sanitize_stem_strips_illegal_chars() -> None:
    cleaned = naming.sanitize_stem('a/b:c*?"<>|d', fallback="x")
    for ch in '\\/:*?"<>|':
        assert ch not in cleaned


def test_sanitize_stem_keeps_legal_text() -> None:
    assert naming.sanitize_stem("Лекция 1", fallback="x") == "Лекция 1"


def test_sanitize_stem_all_illegal_falls_back() -> None:
    assert naming.sanitize_stem("///", fallback="audio") == "audio"


def test_sanitize_stem_blank_falls_back() -> None:
    assert naming.sanitize_stem("   ", fallback="transcript") == "transcript"


def test_sanitize_stem_trims_dashes_from_edges() -> None:
    # Leading/trailing illegal chars become dashes, then get trimmed off.
    assert naming.sanitize_stem(":talk:", fallback="x") == "talk"


def test_sanitize_stem_strips_control_chars() -> None:
    # Tab/newline are illegal in Windows filenames; treated like illegal punctuation.
    cleaned = naming.sanitize_stem("a\tb\nc", fallback="x")
    assert "\t" not in cleaned and "\n" not in cleaned


def test_sanitize_stem_strips_trailing_dot_and_space() -> None:
    # Windows silently drops trailing dots/spaces — strip them so the on-disk name
    # matches our dedup existence check.
    assert naming.sanitize_stem("Quarterly Review.", fallback="x") == "Quarterly Review"
    assert naming.sanitize_stem("Report .", fallback="x") == "Report"


def test_sanitize_stem_all_dots_falls_back() -> None:
    assert naming.sanitize_stem("...", fallback="summary") == "summary"


def test_sanitize_stem_prefixes_reserved_device_names() -> None:
    # "CON"/"NUL"/"COM1" etc. cannot be created on Windows; prefix to make them safe.
    assert naming.sanitize_stem("CON", fallback="x") == "_CON"
    assert naming.sanitize_stem("nul", fallback="x") == "_nul"
    assert naming.sanitize_stem("Com1.txt", fallback="x") == "_Com1.txt"
    # A name merely containing a reserved word is fine — only exact matches.
    assert naming.sanitize_stem("console", fallback="x") == "console"


# --------------------------------------------------------------------------- #
# summary_stem — sanitize + Windows MAX_PATH truncation (T6/T7 triplet base)
# --------------------------------------------------------------------------- #
def test_summary_stem_hard_cuts_a_single_long_word() -> None:
    # No space to break on -> exact hard cut at the cap.
    assert naming.summary_stem("x" * 300, fallback="summary") == "x" * 100


def test_summary_stem_ignores_a_too_early_space() -> None:
    # The only space is at index 2 (< cap//2), so it is NOT used as a break point.
    stem = naming.summary_stem("ab " + "c" * 300, fallback="summary")
    assert len(stem) == 100


def test_summary_stem_reserved_and_trailing_dot_survive_truncation() -> None:
    assert naming.summary_stem("CON", fallback="summary") == "_CON"
    assert naming.summary_stem("Talk.", fallback="summary") == "Talk"


# --------------------------------------------------------------------------- #
# dedup_path
# --------------------------------------------------------------------------- #
def test_dedup_path_free_name(tmp_path: Path) -> None:
    assert naming.dedup_path(tmp_path, "clip", ".mp3") == tmp_path / "clip.mp3"


def test_dedup_path_collides_increments(tmp_path: Path) -> None:
    (tmp_path / "clip.mp3").write_bytes(b"")
    assert naming.dedup_path(tmp_path, "clip", ".mp3") == tmp_path / "clip-2.mp3"
    (tmp_path / "clip-2.mp3").write_bytes(b"")
    assert naming.dedup_path(tmp_path, "clip", ".mp3") == tmp_path / "clip-3.mp3"


def test_dedup_path_respects_suffix(tmp_path: Path) -> None:
    # A .txt collision must not push the .mp3 name; suffixes are independent.
    (tmp_path / "clip.txt").write_bytes(b"")
    assert naming.dedup_path(tmp_path, "clip", ".mp3") == tmp_path / "clip.mp3"


# --------------------------------------------------------------------------- #
# dated_artifact_path
# --------------------------------------------------------------------------- #
def test_dated_artifact_path_composes_date_stem_suffix(tmp_path: Path) -> None:
    path = naming.dated_artifact_path(
        tmp_path, "lecture", ".mp3", fallback="audio", today=date(2026, 6, 15)
    )
    assert path == tmp_path / "2026-06-15-lecture.mp3"


def test_dated_artifact_path_dedups(tmp_path: Path) -> None:
    (tmp_path / "2026-06-15-talk.mp3").write_bytes(b"")
    path = naming.dated_artifact_path(
        tmp_path, "talk", ".mp3", fallback="audio", today=date(2026, 6, 15)
    )
    assert path == tmp_path / "2026-06-15-talk-2.mp3"


def test_dated_artifact_path_uses_fallback_for_illegal_stem(tmp_path: Path) -> None:
    path = naming.dated_artifact_path(
        tmp_path, "///", ".mp3", fallback="audio", today=date(2026, 6, 15)
    )
    assert path == tmp_path / "2026-06-15-audio.mp3"


# --------------------------------------------------------------------------- #
# TD-31 — source identity is CONTENT, and it lives in the transcript's NAME
# --------------------------------------------------------------------------- #
def _big(path: Path, filler: bytes = b"a", size: int = 3 << 20) -> Path:
    """A file past the 2 MiB sample window, so head and tail are sampled separately."""
    path.write_bytes(filler * size)
    return path


def test_the_fingerprint_is_stable_across_reads(tmp_path: Path) -> None:
    src = _big(tmp_path / "lecture.mp4")
    assert naming.source_fingerprint(src) == naming.source_fingerprint(src)


def test_the_fingerprint_has_the_shape_the_transcript_name_parser_expects(
    tmp_path: Path,
) -> None:
    fingerprint = naming.source_fingerprint(_big(tmp_path / "lecture.mp4"))
    assert len(fingerprint) == naming.FINGERPRINT_HEX
    assert all(ch in "0123456789abcdef" for ch in fingerprint)


def test_moving_or_renaming_a_recording_does_not_change_its_identity(tmp_path: Path) -> None:
    """The whole reason identity is content and not a path.

    A path dies on a tree move, a rename, and a WSL-vs-Windows read of the same disk. The
    recording is the same recording through all three.
    """
    src = _big(tmp_path / "Лекция 1.mp4")
    before = naming.source_fingerprint(src)
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    dst = moved / "Совсем другое имя.mp4"
    src.rename(dst)
    assert naming.source_fingerprint(dst) == before


def test_a_changed_byte_changes_the_fingerprint(tmp_path: Path) -> None:
    src = _big(tmp_path / "lecture.mp4")
    before = naming.source_fingerprint(src)
    data = bytearray(src.read_bytes())
    data[0] = ord("z")
    src.write_bytes(bytes(data))
    assert naming.source_fingerprint(src) != before


def test_a_change_in_the_tail_changes_the_fingerprint(tmp_path: Path) -> None:
    """The tail is sampled precisely because a container's head is often boilerplate."""
    src = _big(tmp_path / "lecture.mp4")
    before = naming.source_fingerprint(src)
    data = bytearray(src.read_bytes())
    data[-1] = ord("z")
    src.write_bytes(bytes(data))
    assert naming.source_fingerprint(src) != before


def test_two_recordings_of_the_same_length_but_different_content_differ(
    tmp_path: Path,
) -> None:
    a = _big(tmp_path / "a.mp4", filler=b"a")
    b = _big(tmp_path / "b.mp4", filler=b"b")
    assert naming.source_fingerprint(a) != naming.source_fingerprint(b)


def test_a_file_smaller_than_the_sample_window_is_hashed_whole(tmp_path: Path) -> None:
    """Below 2 MiB the head and tail samples would overlap, so a short file must not be
    identified by a prefix it shares with a neighbour."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"x" * 1024 + b"tail-a")
    b.write_bytes(b"x" * 1024 + b"tail-b")
    assert naming.source_fingerprint(a) != naming.source_fingerprint(b)


def test_length_alone_separates_two_otherwise_identical_files(tmp_path: Path) -> None:
    a = _big(tmp_path / "a.mp4", size=3 << 20)
    b = _big(tmp_path / "b.mp4", size=(3 << 20) + 1)
    assert naming.source_fingerprint(a) != naming.source_fingerprint(b)


def test_an_unreadable_file_raises_rather_than_returning_a_value(tmp_path: Path) -> None:
    """A sentinel would join to every OTHER identity-less file and hand one recording's
    transcript to another — the exact failure this function exists to prevent."""
    with pytest.raises(OSError):
        naming.source_fingerprint(tmp_path / "never-existed.mp4")


def test_transcript_path_puts_the_identity_in_the_name(tmp_path: Path) -> None:
    path = naming.transcript_path(tmp_path, "Лекция 1", "0123456789abcdef", today=date(2026, 6, 15))
    assert path == tmp_path / "2026-06-15-Лекция 1-0123456789abcdef.txt"


def test_transcript_path_sanitizes_and_falls_back_like_every_other_artifact(
    tmp_path: Path,
) -> None:
    path = naming.transcript_path(tmp_path, "///", "0123456789abcdef", today=date(2026, 6, 15))
    assert path == tmp_path / "2026-06-15-transcript-0123456789abcdef.txt"


def test_transcript_path_does_not_dedup(tmp_path: Path) -> None:
    """Two different recordings cannot collide (different fingerprints), so a repeat name
    is the SAME recording and must land on one file. A ``-2`` would leave two files
    claiming one recording — what the old ambiguity was made of."""
    (tmp_path / "2026-06-15-talk-0123456789abcdef.txt").write_text("x", encoding="utf-8")
    path = naming.transcript_path(tmp_path, "talk", "0123456789abcdef", today=date(2026, 6, 15))
    assert path.name == "2026-06-15-talk-0123456789abcdef.txt"
