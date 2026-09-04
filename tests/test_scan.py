"""Scanner tests (bulk v3, increment 1).

The scanner is read-only, offline and free, and so is this suite: no ffmpeg spawns (the
``runner`` seam takes captured stderr), no model, no key, no network. Every assertion is
about a decision the operator will act on — what got counted, what got flagged, and what
the folder would cost.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from echogist import config, cost, extract, guard, scan
from echogist.extract import ExtractError

_STDERR = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':
  Duration: 01:00:00.00, start: 0.000000, bitrate: 1502 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(tv), 1920x1080, 1350 kb/s, 30 fps
  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s
"""
_STDERR_NO_DURATION = "broken.mp4: Invalid data found when processing input\n"


def _runner(text: str = _STDERR) -> extract.Runner:
    def runner(_argv: list[str]) -> tuple[int, str]:
        return 1, text  # ffmpeg -i always exits non-zero; only stderr carries meaning

    return runner


def _media(directory: Path, name: str, size: int = 1024) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    return path


def _scan(root: Path, tmp_path: Path, *, runner: extract.Runner | None = None) -> scan.ScanResult:
    return scan.scan_tree(
        root,
        cache_path=tmp_path / "output" / scan.CACHE_FILENAME,
        exe="/fake/ffmpeg",
        runner=runner or _runner(),
    )


# --------------------------------------------------------------------------- #
# walk
# --------------------------------------------------------------------------- #
def test_walk_recurses_and_honours_the_suffix_filter(tmp_path: Path) -> None:
    _media(tmp_path, "a.mp4")
    _media(tmp_path, "notes.txt")  # not convertible
    _media(tmp_path / "sub" / "deep", "b.MKV")  # nested, and shouting

    assert [p.name for p in scan.walk(tmp_path)] == ["a.mp4", "b.MKV"]


def test_walk_skips_echogists_own_output_tree(tmp_path: Path) -> None:
    # Without this a bulk run re-processes the mp3s it just extracted, paying twice for
    # the same lecture and filling the collision report with its own artifacts.
    _media(tmp_path, "lecture.mp4")
    _media(tmp_path / "output" / "audio", "2026-01-01-lecture.mp3")
    _media(tmp_path / "Course" / "output", "stray.mp4")

    assert [p.name for p in scan.walk(tmp_path)] == ["lecture.mp4"]


def test_walk_terminates_on_a_directory_loop(tmp_path: Path) -> None:
    # A symlink here stands in for an NTFS junction, which is what a cloud-sync client
    # actually creates in a media library. followlinks=False does not cover junctions, so
    # the resolved-path ``seen`` set is the real guard; without it the walk never returns.
    _media(tmp_path / "course", "a.mp4")
    (tmp_path / "course" / "loop").symlink_to(tmp_path / "course", target_is_directory=True)

    found = scan.walk(tmp_path)

    assert [p.name for p in found] == ["a.mp4"]


def test_walk_keeps_two_files_that_differ_only_in_case(tmp_path: Path) -> None:
    # On a case-SENSITIVE filesystem these are two different recordings. Casefolding the
    # dedup key drops one from the scan with no message, and hands it the other's cached
    # duration — a scanner whose whole contract is "nothing is dropped silently".
    _media(tmp_path, "Lecture.mp4")
    _media(tmp_path, "lecture.mp4", size=2048)

    found = scan.walk(tmp_path)

    if len({p.resolve() for p in tmp_path.iterdir()}) == 2:  # skip on a case-folding FS
        assert sorted(p.name for p in found) == ["Lecture.mp4", "lecture.mp4"]
        keys = {scan._cache_key(p, p.stat().st_size, 0) for p in found}
        assert len(keys) == 2, "two files must never share one cache entry"


def test_walk_without_recursion_is_one_level(tmp_path: Path) -> None:
    # The contract batch.expand_selection rides on; its five existing tests are the rest
    # of this regression harness.
    _media(tmp_path, "a.mp4")
    _media(tmp_path / "sub", "deep.mp4")

    assert [p.name for p in scan.walk(tmp_path, recursive=False)] == ["a.mp4"]


# --------------------------------------------------------------------------- #
# scan_tree — the three buckets
# --------------------------------------------------------------------------- #
def test_scan_probes_every_file_and_fills_the_media_bucket(tmp_path: Path) -> None:
    _media(tmp_path / "course", "one.mp4", size=2048)
    _media(tmp_path / "course", "two.mp4", size=4096)

    result = _scan(tmp_path, tmp_path)

    assert len(result.files) == 2
    assert result.total_bytes == 6144
    assert result.total_seconds == pytest.approx(7200.0)
    assert result.files[0].rel == Path("course/one.mp4")
    # Probed and cached now although nothing in this increment reads them: the spawn is
    # the expensive part, and the mp3 re-encode rule would otherwise re-walk the library.
    assert result.files[0].audio_kbps == 128.0
    assert result.files[0].has_video is True


def test_a_file_with_no_duration_line_is_unreadable_not_dropped(tmp_path: Path) -> None:
    # Exit status carries no signal here — ffmpeg -i is non-zero by design — so the
    # absence of a Duration line IS the definition. A silent drop would let a corrupt
    # lecture disappear from a report the operator is using to decide what to spend.
    _media(tmp_path, "broken.mp4")

    result = _scan(tmp_path, tmp_path, runner=_runner(_STDERR_NO_DURATION))

    assert result.files == ()
    assert [p.name for p, _reason in result.unreadable] == ["broken.mp4"]
    assert "no duration" in result.unreadable[0][1]


def test_a_probe_that_times_out_is_unreadable_and_the_scan_continues(tmp_path: Path) -> None:
    _media(tmp_path, "a.mp4")
    _media(tmp_path, "gone.mp4")

    def runner(argv: list[str]) -> tuple[int, str]:
        if argv[-1].endswith("gone.mp4"):
            raise ExtractError("ffmpeg did not respond within 30s")
        return 1, _STDERR

    result = _scan(tmp_path, tmp_path, runner=runner)

    assert [f.path.name for f in result.files] == ["a.mp4"]
    assert [p.name for p, _r in result.unreadable] == ["gone.mp4"]


def test_an_unstattable_file_is_unreadable_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _media(tmp_path, "a.mp4")
    real_stat = Path.stat

    def boom(self: Path, **kwargs: object) -> os.stat_result:
        if self.name == "a.mp4":
            raise OSError(5, "Input/output error")
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", boom)
    result = _scan(tmp_path, tmp_path)

    assert result.files == ()
    assert [p.name for p, _r in result.unreadable] == ["a.mp4"]


def test_a_cloud_placeholder_is_listed_and_never_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only reachable on Windows, so the attribute is faked. Probing one triggers a full
    # hydration download: an offline two-minute scan becomes hours of network transfer.
    _media(tmp_path, "cloud.mp4")
    monkeypatch.setattr(scan, "_is_placeholder", lambda _st: True)
    spawned: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        spawned.append(argv)
        return 1, _STDERR

    result = _scan(tmp_path, tmp_path, runner=runner)

    assert [p.name for p in result.placeholders] == ["cloud.mp4"]
    assert result.files == ()
    assert spawned == []  # the whole point: no hydration


def test_placeholder_detection_is_a_no_op_off_windows(tmp_path: Path) -> None:
    # st_file_attributes does not exist here, and the getattr default must read as
    # "present" rather than flagging every file in the tree.
    st = _media(tmp_path, "a.mp4").stat()

    assert scan._is_placeholder(st) is False


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_a_second_scan_reuses_the_cache_and_spawns_nothing(tmp_path: Path) -> None:
    _media(tmp_path, "a.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())

    spawned: list[list[str]] = []

    def counting(argv: list[str]) -> tuple[int, str]:
        spawned.append(argv)
        return 1, _STDERR

    warm = scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=counting)

    assert spawned == []
    assert len(warm.files) == 1
    assert warm.files[0].duration == pytest.approx(3600.0)


def test_editing_a_file_invalidates_its_cache_entry(tmp_path: Path) -> None:
    # The key carries size and mtime_ns, so a changed file re-probes without any eviction
    # policy: the old key is simply never hit again.
    path = _media(tmp_path, "a.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())

    path.write_bytes(b"y" * 9999)
    spawned: list[list[str]] = []

    def counting(argv: list[str]) -> tuple[int, str]:
        spawned.append(argv)
        return 1, _STDERR

    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=counting)

    assert len(spawned) == 1


def test_a_cached_negative_is_not_reprobed(tmp_path: Path) -> None:
    # "Probed, and ffmpeg had no duration for it" is an ANSWER, not a gap. Re-probing it
    # every run would pay the spawn cost forever for a file that will never be readable.
    _media(tmp_path, "broken.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    scan.scan_tree(
        tmp_path, cache_path=cache_path, exe="/fake", runner=_runner(_STDERR_NO_DURATION)
    )

    spawned: list[list[str]] = []

    def counting(argv: list[str]) -> tuple[int, str]:
        spawned.append(argv)
        return 1, _STDERR

    warm = scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=counting)

    assert spawned == []
    assert [p.name for p, _r in warm.unreadable] == ["broken.mp4"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json at all",  # truncated by a Ctrl-C on an older, non-atomic write
        '{"entries": {}}',  # no schema field
        '{"schema": 999, "entries": {}}',  # a future schema
        '{"schema": 1, "entries": []}',  # right schema, wrong shape
        "[]",  # not an object
    ],
)
def test_a_broken_cache_is_discarded_never_fatal(tmp_path: Path, content: str) -> None:
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(content, encoding="utf-8")

    assert scan.load_cache(cache_path) == {}


def test_a_missing_cache_file_is_just_an_empty_cache(tmp_path: Path) -> None:
    assert scan.load_cache(tmp_path / "nope.json") == {}


def test_the_cache_is_published_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # .part -> os.replace, the same scheme extract_audio uses. A direct overwrite is
    # truncatable by the very Ctrl-C the flush exists to survive, and a truncated file
    # fails the schema check — discarding every entry at the worst possible moment.
    _media(tmp_path, "a.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        replaced.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())

    assert replaced == [(str(cache_path) + ".part", str(cache_path))]
    assert not cache_path.with_name(cache_path.name + ".part").exists()
    assert json.loads(cache_path.read_text())["schema"] == 1


def test_ctrl_c_carries_the_partial_result_and_still_flushes(tmp_path: Path) -> None:
    # A cold scan is minutes of spawns. Losing both the report and the cache for one
    # keystroke is the mistake the batch flow already refuses to make.
    _media(tmp_path, "a.mp4")
    _media(tmp_path, "b.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    seen: list[str] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        if seen:
            raise KeyboardInterrupt
        seen.append(argv[-1])
        return 1, _STDERR

    with pytest.raises(scan.ScanCancelled) as caught:
        scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=runner)

    assert [f.path.name for f in caught.value.result.files] == ["a.mp4"]
    assert len(scan.load_cache(cache_path)) == 1  # the spawn that was paid for is kept


def test_ctrl_c_keeps_cache_entries_the_run_never_reached(tmp_path: Path) -> None:
    # Stopping early must SAVE work, not destroy it. Writing only what this run probed
    # deletes every still-valid entry past the interruption point, so Ctrl-C at file 5 of
    # 500 costs 495 re-probes on the next run.
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _media(tmp_path, name)
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())
    assert len(scan.load_cache(cache_path)) == 3

    def interrupt(_argv: list[str]) -> tuple[int, str]:
        raise KeyboardInterrupt

    (tmp_path / "a.mp4").write_bytes(b"y" * 4096)  # only this one needs re-probing
    with pytest.raises(scan.ScanCancelled):
        scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=interrupt)

    spawned: list[list[str]] = []

    def counting(argv: list[str]) -> tuple[int, str]:
        spawned.append(argv)
        return 1, _STDERR

    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=counting)

    assert len(spawned) == 1, "b.mp4 and c.mp4 were still valid and must not be re-probed"


def test_a_completed_scan_evicts_entries_for_files_that_are_gone(tmp_path: Path) -> None:
    # The other half of the same rule: a COMPLETE run rewrites the cache from live
    # results, so an entry whose file no longer exists falls out on its own.
    _media(tmp_path, "a.mp4")
    gone = _media(tmp_path, "gone.mp4")
    cache_path = tmp_path / "output" / scan.CACHE_FILENAME
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())

    gone.unlink()
    scan.scan_tree(tmp_path, cache_path=cache_path, exe="/fake", runner=_runner())

    assert len(scan.load_cache(cache_path)) == 1


# --------------------------------------------------------------------------- #
# Transcript detection
# --------------------------------------------------------------------------- #
def test_transcript_index_parses_names_back_to_stems(tmp_path: Path) -> None:
    directory = tmp_path / "transcripts"
    directory.mkdir()
    for name in ("2026-01-01-Лекция 1.txt", "2026-02-02-Лекция 1-2.txt", "2026-01-01-Другая.txt"):
        (directory / name).write_text("x", encoding="utf-8")
    (directory / "notes.txt").write_text("x", encoding="utf-8")  # no date prefix

    index = scan.transcript_index(directory)

    assert index == Counter({"Лекция 1": 2, "Другая": 1})


def test_a_stem_is_not_matched_as_a_prefix_of_a_longer_one(tmp_path: Path) -> None:
    # The bug a naive startswith would ship: "Лекция 1" must not claim the transcript
    # belonging to "Лекция 10", or the scan reports work as done that was never done.
    directory = tmp_path / "transcripts"
    directory.mkdir()
    (directory / "2026-01-01-Лекция 10.txt").write_text("x", encoding="utf-8")

    index = scan.transcript_index(directory)
    short = scan.MediaFile(Path("Лекция 1.mp4"), Path("Лекция 1.mp4"), 1, 1.0, None, True)
    long = scan.MediaFile(Path("Лекция 10.mp4"), Path("Лекция 10.mp4"), 1, 1.0, None, True)

    assert scan.candidate_transcripts([short], index) == 0
    assert scan.candidate_transcripts([long], index) == 1


def test_a_missing_transcripts_directory_is_an_empty_index(tmp_path: Path) -> None:
    assert scan.transcript_index(tmp_path / "never-created") == Counter()


# --------------------------------------------------------------------------- #
# Collisions
# --------------------------------------------------------------------------- #
def _file(path: Path) -> scan.MediaFile:
    return scan.MediaFile(path, path, 1, 60.0, None, True)


def test_collisions_group_files_that_would_share_an_artifact_name() -> None:
    files = [
        _file(Path("/A/lecture.mp4")),
        _file(Path("/B/lecture.mkv")),
        _file(Path("/A/unique.mp4")),
    ]

    groups = scan.collisions(files)

    assert groups == [("lecture", (Path("/A/lecture.mp4"), Path("/B/lecture.mkv")))]


def test_collisions_see_stems_that_only_collide_after_sanitizing() -> None:
    # The names differ on disk but not in the artifact tree: sanitize_stem turns ':' into
    # '-', so both would be summarized to the same filename. Grouping on the raw stem
    # would report "no duplicates" for exactly the pair that corrupts a paid run.
    files = [_file(Path("/A/Лекция: 1.mp4")), _file(Path("/B/Лекция- 1.mp4"))]

    assert len(scan.collisions(files)) == 1


def test_no_duplicates_is_an_empty_report() -> None:
    assert scan.collisions([_file(Path("/A/a.mp4")), _file(Path("/A/b.mp4"))]) == []


# --------------------------------------------------------------------------- #
# Cost projection
# --------------------------------------------------------------------------- #
def _model_config() -> config.ModelConfig:
    return config.load_model_config()


def test_the_projection_adds_the_prompt_overhead_once_per_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRITICAL regression. The overhead is a PER-CALL cost, so it belongs in every
    element of ``phase_input_tokens``; folding it in once and splitting the total across
    K phases under-counts input by ``PROMPT_OVERHEAD_TOKENS * (K - 1)`` — an estimate
    biased LOW, the one direction CLAUDE.md forbids."""
    model_config = _model_config()
    tier = model_config.tier("economy")
    captured: list[list[int]] = []

    real = cost.estimate_cost_synthesis

    def spy(phase_input_tokens: list[int], *args: Any, **kwargs: Any) -> Any:
        captured.append(list(phase_input_tokens))
        return real(phase_input_tokens, *args, **kwargs)

    monkeypatch.setattr(cost, "estimate_cost_synthesis", spy)
    # Three hours: long enough that K is comfortably greater than 1, which is the only
    # regime where the two versions differ at all.
    scan.project_file(3 * 3600.0, model_config, tier)

    phases = captured[0]
    k = len(phases)
    assert k > 1, "a 3h lecture must split into several phases or this proves nothing"

    # The body estimate, derived independently of the code under test, so the assertion
    # is not the implementation restated. Subtracting the overhead from each phase and
    # checking it comes back would prove nothing at all.
    scan_cfg = model_config.scan
    chars = 3 * 3600.0 * scan_cfg.words_per_minute / 60.0 * scan_cfg.chars_per_word
    body = math.ceil(scan._cyrillic_tokens_per_char() * chars)

    # Each phase pays for its own prompt. The biased-LOW version divides ONE overhead
    # across K phases, which lands every element near ``body / k + 1000 / k`` instead.
    assert min(phases) >= body // k + guard.PROMPT_OVERHEAD_TOKENS
    assert sum(phases) >= body + guard.PROMPT_OVERHEAD_TOKENS * k


def test_the_projection_is_priced_in_cyrillic_not_latin() -> None:
    # guard rates Cyrillic at roughly double its default rate. Measuring the rate with a
    # Latin sample would HALVE the headline figure the operator decides on.
    assert scan._cyrillic_tokens_per_char() > 0.5


def test_a_longer_file_never_projects_cheaper() -> None:
    model_config = _model_config()
    tier = model_config.tier("economy")
    costs = [scan.project_file(s, model_config, tier).total_usd for s in (600, 3600, 10800, 36000)]

    assert costs == sorted(costs)
    assert costs[0] > 0


def test_project_cost_sums_per_file_rather_than_pooling_durations() -> None:
    # K is a per-file quantity. Summing durations first and computing one K would
    # over-count the overhead for a folder of clips and under-split a folder of lectures.
    model_config = _model_config()
    tier = model_config.tier("economy")
    files = [_file(Path("/a.mp4")), _file(Path("/b.mp4"))]
    files = [scan.MediaFile(f.path, f.rel, f.size, 3 * 3600.0, None, True) for f in files]

    total = scan.project_cost(files, model_config, tier)
    one = scan.project_file(3 * 3600.0, model_config, tier)

    assert total.input_tokens == 2 * one.input_tokens
    assert total.output_tokens == 2 * one.output_tokens


def test_an_empty_folder_projects_nothing() -> None:
    model_config = _model_config()
    estimate = scan.project_cost([], model_config, model_config.tier("economy"))

    assert estimate.total_usd == 0.0


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_folder_rows_are_one_row_per_folder_with_the_numbers_packed(tmp_path: Path) -> None:
    _media(tmp_path / "Course-1", "a.mp4")
    _media(tmp_path / "Course-1", "b.mp4")
    _media(tmp_path / "Course-1" / "bonus", "c.mp4")

    result = _scan(tmp_path, tmp_path)
    rows = scan.folder_rows(result, Counter())

    assert [key for key, _value in rows] == ["Course-1/", "Course-1/bonus/"]
    assert "2 files" in rows[0][1]
    assert "2h 00m" in rows[0][1]
    assert "0 transcript candidates" in rows[0][1]


def test_totals_name_the_tier_the_price_belongs_to(tmp_path: Path) -> None:
    # The figure moves several-fold between tiers, so an unlabelled dollar number is a
    # number the operator cannot act on.
    _media(tmp_path, "a.mp4")
    result = _scan(tmp_path, tmp_path)
    model_config = _model_config()
    tier = model_config.tier("economy")

    rows = dict(scan.totals_rows(result, model_config, tier))

    assert any("economy" in key for key in rows)
    assert rows["Media files"] == "1"
    assert rows["Total duration"] == "1h 00m"
    assert rows["Unreadable files"] == "0"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0h 00m"), (59, "0h 00m"), (60, "0h 01m"), (3599, "0h 59m"), (65037, "18h 03m")],
)
def test_human_hours(seconds: float, expected: str) -> None:
    assert scan.human_hours(seconds) == expected
