"""Folder-run tests (bulk v3, increment 2).

Offline and free like the scanner suite: TRANSCRIBE and SUMMARIZE are plain callables
here, so the whole two-phase run is exercised with no GPU, no key and no network. Every
assertion is about something that costs the operator money or a lecture if it is wrong —
what gets skipped, what gets re-done, and what the folder is quoted at.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from echogist import bulk, config, cost
from echogist.bulk import BulkCancelled, BulkItem


def _tier() -> config.ModelTier:
    return config.ModelTier(
        name="stub",
        model_id="stub",
        context_window=200_000,
        price_in_per_mtok=1.0,
        price_out_per_mtok=5.0,
    )


# --------------------------------------------------------------------------- #
# plan_run — what the folder run will actually do, decided before anything runs
# --------------------------------------------------------------------------- #
def test_a_source_with_a_summary_on_disk_is_not_re_paid_for(tmp_path: Path) -> None:
    """TD-22 in anger: the whole reason the back-link exists."""
    src = tmp_path / "lecture.mp4"
    src.write_bytes(b"x")
    plan = bulk.plan_run([src], summarized={str(src.resolve())}, transcripts={})
    assert plan.summarized == (src,)
    assert plan.to_transcribe == ()
    assert plan.ready == ()


def test_the_summary_skip_joins_on_the_resolved_path_not_the_name(tmp_path: Path) -> None:
    nested = tmp_path / "f"
    nested.mkdir()
    src = nested / "lecture.mp4"
    src.write_bytes(b"x")
    indirect = nested / ".." / "f" / "lecture.mp4"
    plan = bulk.plan_run([indirect], summarized={str(src.resolve())}, transcripts={})
    assert plan.summarized == (indirect,)


def test_an_unambiguous_saved_transcript_skips_the_transcribe_phase(tmp_path: Path) -> None:
    src = tmp_path / "lecture.mp4"
    src.write_bytes(b"x")
    saved = tmp_path / "2026-09-04-lecture.txt"
    saved.write_text("t", encoding="utf-8")
    plan = bulk.plan_run([src], summarized=set(), transcripts={"lecture": (saved,)})
    assert plan.ready == ((src, saved),)
    assert plan.to_transcribe == ()


def test_two_transcripts_for_one_stem_are_refused_and_it_transcribes_again(
    tmp_path: Path,
) -> None:
    """Nothing on disk says which recording the ``-2`` file belongs to."""
    src = tmp_path / "lecture.mp4"
    src.write_bytes(b"x")
    a = tmp_path / "2026-09-04-lecture.txt"
    b = tmp_path / "2026-09-05-lecture-2.txt"
    plan = bulk.plan_run([src], summarized=set(), transcripts={"lecture": (a, b)})
    assert plan.to_transcribe == (src,)
    assert plan.ready == ()


def test_two_sources_sharing_a_stem_never_reuse_one_transcript(tmp_path: Path) -> None:
    """The paid, silent failure this pipeline exists to prevent: summarizing one
    lecture from the other's transcript because their names happen to match."""
    one = tmp_path / "a" / "Лекция.mp4"
    two = tmp_path / "b" / "Лекция.mp4"
    for p in (one, two):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
    saved = tmp_path / "2026-09-04-Лекция.txt"
    saved.write_text("t", encoding="utf-8")
    plan = bulk.plan_run([one, two], summarized=set(), transcripts={"Лекция": (saved,)})
    assert plan.ready == ()
    assert plan.to_transcribe == (one, two)


def test_every_source_lands_in_exactly_one_bucket(tmp_path: Path) -> None:
    done, ready, fresh = (tmp_path / f"{n}.mp4" for n in ("done", "ready", "fresh"))
    for p in (done, ready, fresh):
        p.write_bytes(b"x")
    saved = tmp_path / "2026-09-04-ready.txt"
    saved.write_text("t", encoding="utf-8")
    plan = bulk.plan_run(
        [done, ready, fresh],
        summarized={str(done.resolve())},
        transcripts={"ready": (saved,)},
    )
    assert plan.summarized == (done,)
    assert plan.ready == ((ready, saved),)
    assert plan.to_transcribe == (fresh,)
    assert sorted(plan.sources) == sorted([done, ready, fresh])


# --------------------------------------------------------------------------- #
# folder_estimate — the exact gate, and the under-quote trap it exists to avoid
# --------------------------------------------------------------------------- #
def test_folder_estimate_is_the_sum_of_the_per_file_quotes() -> None:
    tier = _tier()
    files = [[100, 200], [300]]
    total = bulk.folder_estimate(files, tier, per_call_output_tokens=50)
    parts = [cost.estimate_cost_synthesis(f, tier, per_call_output_tokens=50) for f in files]
    assert total.input_tokens == sum(p.input_tokens for p in parts)
    assert total.output_tokens == sum(p.output_tokens for p in parts)
    assert total.total_usd == pytest.approx(sum(p.total_usd for p in parts))


def test_folder_estimate_prices_one_reconcile_per_file_not_one_per_folder() -> None:
    """Flattening every file's phases into one call under-quotes by (files-1)
    reconcile calls, and an under-quote is the only failure that spends unagreed money."""
    tier = _tier()
    files = [[100], [100], [100]]
    total = bulk.folder_estimate(files, tier, per_call_output_tokens=50)
    flattened = cost.estimate_cost_synthesis(
        [t for f in files for t in f], tier, per_call_output_tokens=50
    )
    assert total.output_tokens > flattened.output_tokens
    # 3 files x (1 phase + 1 reconcile) = 6 output calls, not 3 phases + 1 reconcile.
    assert total.output_tokens == 6 * 50
    assert flattened.output_tokens == 4 * 50


def test_folder_estimate_of_nothing_is_zero_but_keeps_the_tier_rates() -> None:
    total = bulk.folder_estimate([], _tier(), per_call_output_tokens=50)
    assert total.total_usd == 0.0
    assert total.price_in_per_mtok == 1.0


# --------------------------------------------------------------------------- #
# run_phase — one bad lecture must not cost the other six
# --------------------------------------------------------------------------- #
class _Boom(Exception):
    pass


def test_a_failed_source_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    a, b, c = (tmp_path / f"{n}.mp4" for n in "abc")

    def step(src: Path) -> Path:
        if src == b:
            raise _Boom("ffmpeg said no")
        return src.with_suffix(".txt")

    report = bulk.run_phase([a, b, c], step, recoverable=(_Boom,))
    assert report.done == 2
    assert report.failed == 1
    assert [i.source for i in report.failures] == [b]
    assert report.failures[0].detail == "ffmpeg said no"


def test_an_unexpected_error_is_not_swallowed(tmp_path: Path) -> None:
    """Only the stage's own recoverable errors continue the run; a real bug fails loud."""

    def step(src: Path) -> Path:
        raise ValueError("a genuine bug")

    with pytest.raises(ValueError, match="a genuine bug"):
        bulk.run_phase([tmp_path / "a.mp4"], step, recoverable=(_Boom,))


def test_ctrl_c_carries_the_partial_report_so_finished_work_is_visible(
    tmp_path: Path,
) -> None:
    a, b, c = (tmp_path / f"{n}.mp4" for n in "abc")

    def step(src: Path) -> Path:
        if src == b:
            raise KeyboardInterrupt
        return src.with_suffix(".txt")

    with pytest.raises(BulkCancelled) as excinfo:
        bulk.run_phase([a, b, c], step, recoverable=(_Boom,))
    report = excinfo.value.report
    assert report.done == 1
    assert [i.source for i in report.items] == [a]


def test_progress_callbacks_see_every_source_in_order(tmp_path: Path) -> None:
    sources = [tmp_path / f"{n}.mp4" for n in "abc"]
    seen: list[tuple[int, int, Path]] = []
    results: list[BulkItem] = []
    bulk.run_phase(
        sources,
        lambda p: p.with_suffix(".txt"),
        on_start=lambda i, n, p: seen.append((i, n, p)),
        on_result=results.append,
        recoverable=(_Boom,),
    )
    assert seen == [(1, 3, sources[0]), (2, 3, sources[1]), (3, 3, sources[2])]
    assert [r.source for r in results] == sources
