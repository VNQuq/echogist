"""Shared artifact-naming tests (F9) — the rule T3, T4 and T7 all reuse.

These exercise :mod:`echogist.naming` directly. The transcribe/extract suites
assert that their public save paths route through it; this file owns the
illegal-char-strip, fallback, and ``-2``/``-3`` dedup edge cases once.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
