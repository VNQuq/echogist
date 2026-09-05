"""One-time TD-31 migration: pre-TD-31 artifacts get their recording's identity.

The script rewrites artifacts that were PAID FOR, so the cost of a wrong move is a lecture
re-bought or a transcript orphaned. These pin the two things that matter: it never guesses
which recording an artifact belongs to, and running it twice changes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import backfill_td31  # noqa: E402

from echogist import naming  # noqa: E402


def _recording(directory: Path, name: str, filler: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(filler * 4096)
    return path


def _summary(raw_dir: Path, title: str, source: Path | None, **extra: object) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"title": title, "core_idea": "x", **extra}
    if source is not None:
        data["source_path"] = str(source)
    path = raw_dir / f"{title}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    (out / "summaries" / "raw").mkdir(parents=True)
    (out / "transcripts").mkdir(parents=True)
    return out


def test_a_summary_is_stamped_with_its_recordings_fingerprint(tmp_path: Path) -> None:
    out = _output(tmp_path)
    src = _recording(tmp_path / "course", "Лекция 1.mp4", b"a")
    entry = _summary(out / "summaries" / "raw", "Лекция 1", src)

    assert backfill_td31.main(["--output-dir", str(out), "--apply"]) == 0

    data = json.loads(entry.read_text(encoding="utf-8"))
    assert data["source_fingerprint"] == naming.source_fingerprint(src)
    # Replaced, not kept alongside: a fresh run writes only the fingerprint, and a migrated
    # artifact carrying both would be a second shape to reason about.
    assert "source_path" not in data


def test_a_transcript_is_renamed_to_carry_the_fingerprint(tmp_path: Path) -> None:
    out = _output(tmp_path)
    src = _recording(tmp_path / "course", "Лекция 1.mp4", b"a")
    _summary(out / "summaries" / "raw", "Лекция 1", src)
    old = out / "transcripts" / "2026-09-05-Лекция 1.txt"
    old.write_text("[00:00:00] текст\n", encoding="utf-8")

    backfill_td31.main(["--output-dir", str(out), "--apply"])

    fingerprint = naming.source_fingerprint(src)
    new = out / "transcripts" / f"2026-09-05-Лекция 1-{fingerprint}.txt"
    assert new.is_file() and not old.exists()
    assert new.read_text(encoding="utf-8") == "[00:00:00] текст\n"


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    out = _output(tmp_path)
    src = _recording(tmp_path / "course", "Лекция 1.mp4", b"a")
    entry = _summary(out / "summaries" / "raw", "Лекция 1", src)
    old = out / "transcripts" / "2026-09-05-Лекция 1.txt"
    old.write_text("x", encoding="utf-8")
    before = entry.read_text(encoding="utf-8")

    assert backfill_td31.main(["--output-dir", str(out)]) == 0

    assert entry.read_text(encoding="utf-8") == before
    assert old.is_file()


def test_running_twice_changes_nothing(tmp_path: Path) -> None:
    out = _output(tmp_path)
    src = _recording(tmp_path / "course", "Лекция 1.mp4", b"a")
    entry = _summary(out / "summaries" / "raw", "Лекция 1", src)
    (out / "transcripts" / "2026-09-05-Лекция 1.txt").write_text("x", encoding="utf-8")

    backfill_td31.main(["--output-dir", str(out), "--apply"])
    after_first = (
        entry.read_text(encoding="utf-8"),
        sorted(p.name for p in (out / "transcripts").iterdir()),
    )
    backfill_td31.main(["--output-dir", str(out), "--apply"])

    assert (
        entry.read_text(encoding="utf-8"),
        sorted(p.name for p in (out / "transcripts").iterdir()),
    ) == after_first
    assert backfill_td31.build_plan(out).done == 2


def test_two_recordings_sharing_a_stem_leave_the_transcript_alone(tmp_path: Path) -> None:
    """The TD-31 bug itself, in the one place a stem is still allowed to decide anything.

    Two courses both containing ``Лекция 1.mp4`` claim the same transcript name. Guessing
    would hand one course's transcript to the other — paid and silent. Refusing costs a
    re-transcription: free, local, visible.
    """
    out = _output(tmp_path)
    a = _recording(tmp_path / "2025", "Лекция 1.mp4", b"a")
    b = _recording(tmp_path / "2026", "Лекция 1.mp4", b"b")
    _summary(out / "summaries" / "raw", "A", a)
    _summary(out / "summaries" / "raw", "B", b)
    orphan = out / "transcripts" / "2026-09-05-Лекция 1.txt"
    orphan.write_text("x", encoding="utf-8")

    plan = backfill_td31.build_plan(out)

    assert plan.transcripts == {}
    assert "stem" in plan.skipped[orphan]
    # Both summaries still get stamped: they name their sources by PATH, which is exact.
    assert len(plan.summaries) == 2
    assert set(plan.summaries.values()) == {
        naming.source_fingerprint(a),
        naming.source_fingerprint(b),
    }


def test_a_summary_whose_recording_is_gone_is_reported_not_guessed(tmp_path: Path) -> None:
    out = _output(tmp_path)
    entry = _summary(out / "summaries" / "raw", "Лекция 1", tmp_path / "deleted.mp4")

    plan = backfill_td31.build_plan(out)

    assert plan.summaries == {}
    assert "gone or unreadable" in plan.skipped[entry]


def test_a_summary_with_no_back_link_at_all_is_reported(tmp_path: Path) -> None:
    out = _output(tmp_path)
    entry = _summary(out / "summaries" / "raw", "Лекция 1", None)

    plan = backfill_td31.build_plan(out)

    assert plan.summaries == {}
    assert "no source_path" in plan.skipped[entry]


def test_unreadable_json_is_skipped_rather_than_aborting_the_run(tmp_path: Path) -> None:
    """One corrupt artifact must not strand the other twelve."""
    out = _output(tmp_path)
    broken = out / "summaries" / "raw" / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    src = _recording(tmp_path / "course", "Лекция 1.mp4", b"a")
    good = _summary(out / "summaries" / "raw", "Лекция 1", src)

    plan = backfill_td31.build_plan(out)

    assert set(plan.summaries) == {good}
    assert "unreadable JSON" in plan.skipped[broken]


def test_a_transcript_with_no_matching_summary_is_left_alone(tmp_path: Path) -> None:
    out = _output(tmp_path)
    orphan = out / "transcripts" / "2026-09-05-Никому не нужная.txt"
    orphan.write_text("x", encoding="utf-8")

    plan = backfill_td31.build_plan(out)

    assert plan.transcripts == {}
    assert orphan in plan.skipped


def test_a_windows_source_path_resolves_from_wsl() -> None:
    """Summaries were written on the Windows box; the operator runs this from either side."""
    got = backfill_td31.windows_path_to_local(r"C:\Users\operator\Documents\echogist\Лекция 1.mp4")
    if sys.platform == "win32":
        assert got == Path(r"C:\Users\operator\Documents\echogist\Лекция 1.mp4")
    else:
        assert got == Path("/mnt/c/Users/operator/Documents/echogist/Лекция 1.mp4")


def test_a_posix_source_path_is_left_alone() -> None:
    assert backfill_td31.windows_path_to_local("/mnt/c/x/y.mp4") == Path("/mnt/c/x/y.mp4")


def test_a_missing_output_directory_fails_loud(tmp_path: Path) -> None:
    assert backfill_td31.main(["--output-dir", str(tmp_path / "nope")]) == 1
