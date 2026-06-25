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
    total_tokens = sum(sizes)
    k = max(1, math.ceil(total_tokens / max(1, cfg.target_chunk_tokens)))
    k = min(k, len(blocks))  # never more bins than blocks
    target = total_tokens / k  # ideal tokens per bin (balanced — no leftover tail)

    # Assign each block to a bin by the midpoint of its cumulative-token span. Contiguous
    # by construction (cumulative is monotonic), so bins are runs of consecutive blocks.
    bins: list[list[int]] = [[] for _ in range(k)]
    cumulative = 0
    for i, size in enumerate(sizes):
        midpoint = cumulative + size / 2
        idx = min(k - 1, int(midpoint // target))
        bins[idx].append(i)
        cumulative += size
    bins = [b for b in bins if b]  # drop any empty bin (defensive; rounding edge)

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
