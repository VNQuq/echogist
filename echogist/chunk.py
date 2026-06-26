"""T5b — chunk planner for map-reduce summarization (TD-5). Local, offline, killswitch-safe.

When a transcript is long or dense enough that a single SUMMARIZE pass would lose
fidelity in the middle ("lost in the middle"), it is split into balanced, slightly
overlapping chunks that are each summarized (MAP) and then merged (REDUCE). This
module owns only the LOCAL, deterministic planning half — deciding *whether* to
chunk and *where* to cut. No model, no network, no Anthropic import: the whole
planner is pure functions over the saved transcript text, so it is unit-testable
with no key (CLAUDE.md killswitch).

**Two budgets.** Chunking triggers on the **QualityBudget** (:func:`needs_chunking`):
a single pass is trusted only while the transcript is under BOTH the token and the
duration limit. This sits below the tier's ContextBudget (the overflow guard) on
purpose — a single pass degrades well before the context window is full.

**Where to cut.** The saved transcript is already a sequence of ``[HH:MM:SS] text``
blocks (one per ~60s, see :mod:`echogist.transcribe`). Chunks are packed out of whole
blocks — never mid-sentence — into ``K = ceil(total_tokens / target_chunk_tokens)``
balanced bins (no disproportionate tail), with a time-based overlap carried between
adjacent chunks so an idea straddling a cut survives in both (the duplicate is removed
by the reduce step's conservative dedup).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from .config import ChunkConfig
from .guard import estimate_input_tokens

# A block line opens with its timecode, e.g. "[01:23:45] some text". Hours are
# uncapped (matches transcribe.format_timecode). A line without a leading timecode
# (defensive: a hand-edited transcript) is treated as a continuation of the prior
# block so it never floats free of a start time.
_TIMECODE_RE = re.compile(r"^\[(\d+):(\d{2}):(\d{2})\]")

# Token estimator for a single block body (no prompt overhead — that scaffolding is
# added once per CALL, not per block). Injectable so tests can drive deterministic
# sizes; defaults to the guard's biased-high per-script estimate for consistency.
TokenEstimator = Callable[[str], int]


def _body_tokens(text: str) -> int:
    return estimate_input_tokens(text, prompt_overhead=0)


def _hms(seconds: float) -> str:
    """Seconds -> ``HH:MM:SS`` (zero-padded, hours uncapped). Mirrors transcribe."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _block_start_seconds(line: str) -> float | None:
    """Parse the leading ``[HH:MM:SS]`` of a block line to seconds, or None."""
    m = _TIMECODE_RE.match(line)
    if not m:
        return None
    h, mm, ss = (int(g) for g in m.groups())
    return float(h * 3600 + mm * 60 + ss)


@dataclass(frozen=True)
class _Block:
    """One transcript line: its start time and the raw ``[HH:MM:SS] text`` line."""

    start: float
    line: str


def _blocks(text: str) -> list[_Block]:
    """Split the transcript into timecoded blocks (one per non-blank line).

    A line with no parseable timecode inherits the previous block's start (defensive:
    a wrapped or hand-edited line never floats free of a time), and the very first
    such line is anchored at 0.0.
    """
    out: list[_Block] = []
    last = 0.0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        start = _block_start_seconds(raw)
        if start is None:
            start = last
        last = start
        out.append(_Block(start=start, line=raw))
    return out


def block_timecodes(text: str) -> tuple[str, ...]:
    """The canonical ``[HH:MM:SS]`` timecodes that ACTUALLY APPEAR in ``text``, in order.

    The ground-truth set of real anchors a synthesis pass may cite (TD-16 v2): built
    from the same block parse the planner uses, so the anchor validator
    (:func:`echogist.summarize.validate_anchors`) can snap a rounded timecode to the
    nearest real block or drop a hallucinated one. Only lines that carry their OWN
    leading timecode count — a continuation line that merely inherited the previous
    block's start is not a citeable anchor. Distinct and order-preserving; a transcript
    with no parseable timecodes yields an empty tuple.
    """
    seen: set[float] = set()
    out: list[str] = []
    for b in _blocks(text):
        if _block_start_seconds(b.line) is None:  # inherited continuation, not a real anchor
            continue
        if b.start in seen:
            continue
        seen.add(b.start)
        out.append(f"[{_hms(b.start)}]")
    return tuple(out)


def total_duration_seconds(text: str) -> float:
    """Transcript duration proxy: the start time of the last timecoded block (or 0).

    Uses the last block's start (we do not carry block END times); for a long
    recording this under-counts by at most one block (~60s), which is harmless for a
    coarse QualityBudget trigger.
    """
    blocks = _blocks(text)
    return blocks[-1].start if blocks else 0.0


def needs_chunking(est_tokens: int, duration_seconds: float, cfg: ChunkConfig) -> bool:
    """True if the transcript crosses the QualityBudget on EITHER tokens or duration.

    Either axis tripping is enough (long OR dense), erring toward chunking — which
    errs toward completeness, the whole point of map-reduce.
    """
    return est_tokens > cfg.quality_budget_tokens or duration_seconds > cfg.quality_budget_seconds


@dataclass(frozen=True)
class Chunk:
    """One planned chunk: its text, position, and time span (for the map note).

    ``text`` is whole ``[HH:MM:SS] text`` lines joined by newlines (same shape as the
    transcript), including any leading overlap carried from the previous chunk.
    ``index`` is 1-based; ``span`` formats the covered time range for the map prompt.
    """

    text: str
    index: int
    total: int
    start_seconds: float
    end_seconds: float

    @property
    def span(self) -> str:
        return f"{_hms(self.start_seconds)}–{_hms(self.end_seconds)}"


def _bin_count(total_tokens: int, target_tokens: int, n_blocks: int) -> int:
    """``K = ceil(total / target)``, at least 1, never more bins than blocks.

    Shared by :func:`plan_chunks` (map) and :func:`plan_phases` (synthesis): K is
    COMPUTED from the chosen target, so it scales with transcript length rather than
    being a fixed number of pieces. Clamped to ``n_blocks`` so a tiny target never asks
    for more bins than there are whole blocks to fill (we never split mid-block).
    """
    k = max(1, math.ceil(total_tokens / max(1, target_tokens)))
    return min(k, n_blocks)


def _bin_blocks(sizes: list[int], k: int) -> list[list[int]]:
    """Assign block indices 0..n-1 into ``k`` token-balanced, CONTIGUOUS bins.

    Each block lands in the bin its cumulative-token MIDPOINT falls into, so the bins
    are near-even runs of consecutive blocks with no disproportionate tail. Empty bins
    (a rounding edge with a skewed size distribution) are dropped, so the result has 1
    to ``k`` non-empty bins that together cover every index exactly once, in order.
    Pure and overlap-free — overlap, if any, is the caller's concern (map adds it,
    phases do not). Self-defensive: empty ``sizes`` or a non-positive ``k`` returns no
    bins rather than dividing by zero (callers already guard this, but the helper is
    shared and must not blow up on a degenerate call).
    """
    total_tokens = sum(sizes)
    if not sizes or k <= 0:
        return []
    target = total_tokens / k  # ideal tokens per bin (balanced — no leftover tail)
    bins: list[list[int]] = [[] for _ in range(k)]
    cumulative = 0
    for i, size in enumerate(sizes):
        midpoint = cumulative + size / 2
        idx = min(k - 1, int(midpoint // target))
        bins[idx].append(i)
        cumulative += size
    return [b for b in bins if b]  # drop any empty bin (defensive; rounding edge)


def plan_chunks(
    text: str, cfg: ChunkConfig, *, estimate: TokenEstimator = _body_tokens
) -> list[Chunk]:
    """Split ``text`` into balanced, overlapping chunks on block boundaries.

    ``K = ceil(total_tokens / target_chunk_tokens)`` bins, each block assigned by the
    midpoint of its cumulative-token position so the bins are near-even (no tiny
    tail). Then each chunk after the first re-includes the trailing blocks of its
    predecessor that fall within ``overlap_seconds`` of its own first block, so an
    idea spanning the cut appears in both chunks. Always returns at least one chunk;
    a transcript with no parseable blocks returns a single chunk of the whole text.
    """
    blocks = _blocks(text)
    if not blocks:
        return [Chunk(text=text, index=1, total=1, start_seconds=0.0, end_seconds=0.0)]

    sizes = [max(1, estimate(b.line)) for b in blocks]  # >=1 so empty-ish blocks still count
    k = _bin_count(sum(sizes), cfg.target_chunk_tokens, len(blocks))
    bins = _bin_blocks(sizes, k)

    chunks: list[Chunk] = []
    total = len(bins)
    for pos, members in enumerate(bins):
        first_idx = members[0]
        first_start = blocks[first_idx].start
        overlap_idx: list[int] = []
        if pos > 0 and cfg.overlap_seconds > 0:
            # Walk backward through the previous bin, re-including blocks within the
            # overlap window of THIS chunk's first block.
            j = first_idx - 1
            while j >= 0 and first_start - blocks[j].start <= cfg.overlap_seconds:
                overlap_idx.append(j)
                j -= 1
            overlap_idx.reverse()
        line_idx = overlap_idx + members
        lines = [blocks[i].line for i in line_idx]
        chunks.append(
            Chunk(
                text="\n".join(lines),
                index=pos + 1,
                total=total,
                start_seconds=blocks[line_idx[0]].start,
                end_seconds=blocks[members[-1]].start,
            )
        )
    return chunks


@dataclass(frozen=True)
class Phase:
    """One synthesis phase (TD-16 v2): a CONTIGUOUS, non-overlapping span of blocks.

    Same shape as :class:`Chunk` (text / 1-based index / total / time span, with the
    ``span`` property for the "phase N of M (HH:MM:SS–HH:MM:SS)" coherence note), but a
    STANDALONE type — deliberately not a subclass of :class:`Chunk`. Phases are produced
    differently (no overlap between neighbours: synthesis has no mechanical dedup, so any
    overlap would surface as duplicated prose at the seam — coherence is carried forward
    as prior-phase context, see the synthesis prompt's PRIOR CONTEXT section, not by
    re-including text). Standalone so the map-reduce ``Chunk`` can be deleted (TD-16 v2
    retirement) without touching the phase model.
    """

    text: str
    index: int
    total: int
    start_seconds: float
    end_seconds: float

    @property
    def span(self) -> str:
        return f"{_hms(self.start_seconds)}–{_hms(self.end_seconds)}"


def plan_phases(
    text: str, cfg: ChunkConfig, *, estimate: TokenEstimator = _body_tokens
) -> list[Phase]:
    """Split ``text`` into balanced, CONTIGUOUS, non-overlapping phases for synthesis.

    The same midpoint token-binning as :func:`plan_chunks`, but ``K`` is computed from
    ``cfg.phase_target_tokens`` (coarser than the map target, so a 3h lecture lands at
    ~3-4 phases, a 6h one at ~6-8 — K SCALES, it is not capped), and NO overlap is added:
    the phases partition the blocks, each block in exactly one phase. Short material
    under one phase-target collapses to a single phase (K=1, the whole transcript);
    a transcript with no parseable blocks returns one phase of the whole text.
    """
    blocks = _blocks(text)
    if not blocks:
        return [Phase(text=text, index=1, total=1, start_seconds=0.0, end_seconds=0.0)]

    sizes = [max(1, estimate(b.line)) for b in blocks]  # >=1 so empty-ish blocks still count
    k = _bin_count(sum(sizes), cfg.phase_target_tokens, len(blocks))
    bins = _bin_blocks(sizes, k)

    phases: list[Phase] = []
    total = len(bins)
    for pos, members in enumerate(bins):
        lines = [blocks[i].line for i in members]  # contiguous run — no overlap carried
        phases.append(
            Phase(
                text="\n".join(lines),
                index=pos + 1,
                total=total,
                start_seconds=blocks[members[0]].start,
                end_seconds=blocks[members[-1]].start,
            )
        )
    return phases
