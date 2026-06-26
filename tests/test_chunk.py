"""Chunk-planner tests (TD-5) — duration, QualityBudget trigger, balanced split, overlap.

All local/offline (killswitch): the planner is pure functions over the saved
transcript text, no model and no key.
"""

from __future__ import annotations

from echogist import chunk
from echogist.config import ChunkConfig


def _timecoded(n_blocks: int, *, step_min: int = 1, words: int = 5) -> str:
    """n_blocks lines of ``[00:MM:00] word word ...`` at ``step_min`` spacing."""
    return "\n".join(f"[00:{m * step_min:02d}:00] " + "word " * words for m in range(n_blocks))


# --------------------------------------------------------------------------- #
# Duration from timecodes
# --------------------------------------------------------------------------- #
def test_total_duration_reads_last_timecode() -> None:
    text = "[00:00:00] a\n[00:30:00] b\n[01:30:00] c"
    assert chunk.total_duration_seconds(text) == 5400.0  # 01:30:00


def test_total_duration_empty_is_zero() -> None:
    assert chunk.total_duration_seconds("") == 0.0
    assert chunk.total_duration_seconds("\n  \n") == 0.0


# --------------------------------------------------------------------------- #
# QualityBudget trigger — tokens OR duration, either is enough
# --------------------------------------------------------------------------- #
def test_needs_chunking_trips_on_tokens() -> None:
    cfg = ChunkConfig(quality_budget_tokens=40_000, quality_budget_seconds=3_600)
    assert chunk.needs_chunking(50_000, 600, cfg) is True  # over on tokens, under on time
    assert chunk.needs_chunking(40_000, 600, cfg) is False  # exactly at budget is not over


def test_needs_chunking_trips_on_duration() -> None:
    cfg = ChunkConfig(quality_budget_tokens=40_000, quality_budget_seconds=3_600)
    assert chunk.needs_chunking(1_000, 4_000, cfg) is True  # under tokens, over time
    assert chunk.needs_chunking(1_000, 600, cfg) is False  # under both -> single pass


# --------------------------------------------------------------------------- #
# Balanced split — K bins, no tiny tail, cuts on whole block boundaries
# --------------------------------------------------------------------------- #
def test_plan_chunks_balances_into_k_bins_no_tail() -> None:
    text = _timecoded(12)
    cfg = ChunkConfig(target_chunk_tokens=4_000, overlap_seconds=0)
    chunks = chunk.plan_chunks(text, cfg, estimate=lambda _line: 1_000)  # 12 * 1000 = 12000
    # K = ceil(12000 / 4000) = 3, balanced -> 4 / 4 / 4 (no disproportionate last bin).
    assert [c.index for c in chunks] == [1, 2, 3]
    assert all(c.total == 3 for c in chunks)
    assert [len(c.text.splitlines()) for c in chunks] == [4, 4, 4]


def test_plan_chunks_cuts_only_on_block_boundaries() -> None:
    text = _timecoded(9)
    cfg = ChunkConfig(target_chunk_tokens=3_000, overlap_seconds=0)
    chunks = chunk.plan_chunks(text, cfg, estimate=lambda _line: 1_000)
    for c in chunks:
        for line in c.text.splitlines():
            assert line.startswith("[")  # every line is a whole timecoded block, never mid-sentence


def test_plan_chunks_short_text_is_single_chunk() -> None:
    chunks = chunk.plan_chunks(_timecoded(3), ChunkConfig(target_chunk_tokens=999_999))
    assert len(chunks) == 1
    assert chunks[0].index == 1 and chunks[0].total == 1


def test_plan_chunks_no_timecodes_falls_back_to_one_chunk() -> None:
    chunks = chunk.plan_chunks("no timecodes here at all", ChunkConfig())
    assert len(chunks) == 1
    assert chunks[0].text == "no timecodes here at all"


def test_plan_chunks_single_block_clamps_k_to_one() -> None:
    # One block whose token size dwarfs target_chunk_tokens: K = ceil(huge/tiny) is large
    # but clamped to len(blocks) == 1 (never more bins than blocks, never split mid-block).
    cfg = ChunkConfig(target_chunk_tokens=1, overlap_seconds=0)
    chunks = chunk.plan_chunks("[00:00:00] one", cfg, estimate=lambda _l: 9_999)
    assert len(chunks) == 1
    assert chunks[0].index == 1 and chunks[0].total == 1


def test_plan_chunks_drops_empty_bins_and_renumbers_contiguously() -> None:
    # A skewed estimator (one huge leading block, then tiny ones) can leave a midpoint bin
    # empty; it is dropped and total is recomputed so index/total stay consistent.
    sizes = iter([100, 1, 1, 1, 1])
    cfg = ChunkConfig(target_chunk_tokens=10, overlap_seconds=0)
    chunks = chunk.plan_chunks(_timecoded(5), cfg, estimate=lambda _l: next(sizes))
    assert [c.index for c in chunks] == list(range(1, len(chunks) + 1))  # 1..N, no gaps
    assert all(c.total == len(chunks) for c in chunks)  # total matches the real count


# --------------------------------------------------------------------------- #
# Overlap — adjacent chunks re-include the prior block within the time window
# --------------------------------------------------------------------------- #
def test_plan_chunks_carries_time_based_overlap() -> None:
    text = _timecoded(6)  # blocks at 00:00, 01:00, ... 05:00 (60s apart)
    cfg = ChunkConfig(target_chunk_tokens=2_000, overlap_seconds=90)
    chunks = chunk.plan_chunks(text, cfg, estimate=lambda _line: 1_000)  # bins [0,1][2,3][4,5]
    assert len(chunks) == 3
    # Chunk 2 starts at 02:00; the 01:00 block is within 90s, so it is carried in (overlap).
    assert chunks[1].text.startswith("[00:01:00]")
    assert "[00:02:00]" in chunks[1].text
    # The 00:00 block is 120s back -> outside the 90s window, not carried.
    assert "[00:00:00]" not in chunks[1].text
    # The chunk's reported span starts at the overlap block, ends at its last own block.
    assert chunks[1].start_seconds == 60.0
    assert chunks[1].end_seconds == 180.0


def test_plan_chunks_no_overlap_when_zero() -> None:
    text = _timecoded(6)
    cfg = ChunkConfig(target_chunk_tokens=2_000, overlap_seconds=0)
    chunks = chunk.plan_chunks(text, cfg, estimate=lambda _line: 1_000)
    # With no overlap the bins are disjoint: chunk 2 starts at its own first block (02:00).
    assert chunks[1].text.startswith("[00:02:00]")


# --------------------------------------------------------------------------- #
# plan_phases (TD-16 v2) — computed K, contiguous, NO overlap, full partition
# --------------------------------------------------------------------------- #
def test_plan_phases_balances_into_k_bins_no_tail() -> None:
    text = _timecoded(12)
    cfg = ChunkConfig(phase_target_tokens=4_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # 12 * 1000 = 12000
    # K = ceil(12000 / 4000) = 3, balanced -> 4 / 4 / 4 (same binning math as plan_chunks).
    assert [p.index for p in phases] == [1, 2, 3]
    assert all(p.total == 3 for p in phases)
    assert [len(p.text.splitlines()) for p in phases] == [4, 4, 4]


def test_plan_phases_are_contiguous_and_partition_every_block_once() -> None:
    text = _timecoded(9)
    cfg = ChunkConfig(phase_target_tokens=3_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)
    # No overlap: the phases concatenate back to exactly the original blocks, in order,
    # each block appearing in exactly ONE phase (a clean partition, not overlapping spans).
    rejoined = [line for p in phases for line in p.text.splitlines()]
    assert rejoined == text.splitlines()


def test_plan_phases_no_overlap_phase_two_starts_on_its_own_block() -> None:
    text = _timecoded(6)  # blocks 60s apart
    cfg = ChunkConfig(phase_target_tokens=2_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # bins [0,1][2,3][4,5]
    assert len(phases) == 3
    # Unlike a map chunk, phase 2 carries NO prior overlap block — starts at its own 02:00.
    assert phases[1].text.startswith("[00:02:00]")
    assert "[00:01:00]" not in phases[1].text
    assert phases[1].start_seconds == 120.0
    assert phases[1].end_seconds == 180.0


def test_plan_phases_k_scales_with_target_not_capped() -> None:
    # K is COMPUTED from phase_target_tokens, so a smaller target yields more phases —
    # it is not pinned to 3-4. Same 12-block transcript, three targets, three K values.
    text = _timecoded(12)
    est = lambda _line: 1_000  # noqa: E731 — 12 * 1000 = 12000 total
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=24_000), estimate=est)) == 1
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=6_000), estimate=est)) == 2
    assert len(chunk.plan_phases(text, ChunkConfig(phase_target_tokens=3_000), estimate=est)) == 4


def test_plan_phases_short_text_is_single_phase() -> None:
    phases = chunk.plan_phases(_timecoded(3), ChunkConfig(phase_target_tokens=999_999))
    assert len(phases) == 1
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_no_timecodes_falls_back_to_one_phase() -> None:
    phases = chunk.plan_phases("no timecodes here at all", ChunkConfig())
    assert len(phases) == 1
    assert phases[0].text == "no timecodes here at all"
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_single_block_clamps_k_to_one() -> None:
    # A tiny target would ask for many bins, but K is clamped to len(blocks) == 1.
    cfg = ChunkConfig(phase_target_tokens=1)
    phases = chunk.plan_phases("[00:00:00] one", cfg, estimate=lambda _l: 9_999)
    assert len(phases) == 1
    assert phases[0].index == 1 and phases[0].total == 1


def test_plan_phases_span_formats_time_range() -> None:
    text = _timecoded(4)  # 00:00 .. 00:03
    cfg = ChunkConfig(phase_target_tokens=2_000)
    phases = chunk.plan_phases(text, cfg, estimate=lambda _line: 1_000)  # bins [0,1][2,3]
    assert phases[0].span == "00:00:00–00:01:00"
    assert phases[1].span == "00:02:00–00:03:00"


def test_bin_blocks_is_self_defensive_on_degenerate_input() -> None:
    # Unreachable via the public planners (they guard empty blocks), but the shared
    # helper must not divide by zero on a degenerate direct call.
    assert chunk._bin_blocks([], 3) == []
    assert chunk._bin_blocks([1, 2, 3], 0) == []
