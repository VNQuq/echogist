"""Phase planner for direct transcript synthesis (TD-16 v2). Local, offline, killswitch-safe.

A long transcript is split into a computed number of balanced, CONTIGUOUS phases that are
each synthesized into faithful prose (see :func:`echogist.summarize.synthesize_summary`).
This module owns the LOCAL, deterministic planning half — deciding *where* to cut. No
model, no network, no Anthropic import: the whole planner is pure functions over the saved
transcript text, so it is unit-testable with no key (CLAUDE.md killswitch).

**Where to cut.** The saved transcript is already a sequence of ``[HH:MM:SS] text`` blocks
(one per ~60s, see :mod:`echogist.transcribe`). Phases are packed out of whole blocks —
never mid-sentence — into ``K = ceil(total_tokens / phase_target_tokens)`` balanced bins
(no disproportionate tail). Phases are contiguous and NON-overlapping: synthesis has no
mechanical dedup, so coherence is carried forward as prior-phase context rather than by
re-including text (see :func:`plan_phases`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
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


# Below this a block is too short for the unique-word ratio to mean anything: a genuine
# "Да. Да." is 50% unique and would look exactly like a loop. Blocks this small are judged
# by Rule B (adjacency) instead, never by ratio.
_MIN_WORDS_FOR_RATIO = 4

# Trailing punctuation stripped before comparing words, so "канал!" and "канал" are one word.
_WORD_PUNCT = ".,!?…:;—-\"'()[]«»"


def _block_body(line: str) -> str:
    """The spoken text of a block, without its leading ``[HH:MM:SS]`` timecode."""
    return _TIMECODE_RE.sub("", line, count=1).strip() if _TIMECODE_RE.match(line) else line.strip()


def _unique_word_ratio(words: Sequence[str]) -> float:
    """Distinct words / total words, case- and punctuation-insensitive. 0.0 for no words.

    The signature of a Whisper repetition loop: the decoder emits one phrase over and over
    across a stretch of silence or music, so the block is long on words and short on
    distinct ones. Real speech does not do this — measured floor 0.56 on a real RU lecture.
    """
    if not words:
        return 0.0
    distinct = {w.lower().strip(_WORD_PUNCT) for w in words}
    return len(distinct) / len(words)


def drop_degenerate_blocks(text: str, cfg: ChunkConfig) -> tuple[str, tuple[str, ...]]:
    """Drop whole non-speech blocks from the SYNTHESIS INPUT. Returns (kept text, dropped).

    TD-26. Whisper emits training-set subtitle boilerplate over silence, music or an intro
    screen — on the operator's real lectures, minutes of ``Добро пожаловать на наш канал!``
    and ``Субтитры создавал <name>`` before the speaker starts. The synthesis prompt treats
    the transcript as ground truth and is told to cover everything and drop nothing, so
    without this the model faithfully summarizes a YouTube greeting as lecture content:
    fabrication that enters UPSTREAM of the model, where the groundedness contract and the
    anchor validator cannot see it (the timecodes are real — only the words are invented).

    Two content-agnostic rules, no phrase list (a phrase list only ever catches the
    boilerplate someone already saw):

    * **Rule A — the loop.** A block with enough words to judge whose unique-word ratio is
      below ``cfg.min_unique_word_ratio`` is a repetition loop, not speech.
    * **Rule B — the shoulder.** A block too short to hold a minute of talk
      (``cfg.min_block_words``) that TOUCHES a Rule-A block is part of the same artifact,
      applied outward until it stops spreading. This is what catches the single unique
      credit line that opens the run (``Субтитры создавал ...``, 8 words, ratio 1.0) without
      touching a legitimately quiet moment surrounded by real speech.

    WHOLE blocks only — never an edit inside one. A block that is mostly real speech with a
    stray credit glued to its front keeps all of it: losing a real sentence is worse than
    keeping a stray phrase, and the operator's manual re-check is the backstop (CLAUDE.md
    fidelity gate). Dropping every block is refused — a transcript with no speech at all is
    returned untouched rather than emptied, so the caller never gets a silent empty input.

    The SAVED transcript is not modified. It stays verbatim ground truth on disk for the
    operator to check against the recording; only what is handed to the model is cleaned.
    Callers must REPORT what came back in ``dropped`` — CLAUDE.md forbids a silent skip.
    """
    blocks = _blocks(text)
    if not blocks:
        return text, ()

    words = [_block_body(b.line).split() for b in blocks]
    drop = [
        len(w) >= _MIN_WORDS_FOR_RATIO and _unique_word_ratio(w) < cfg.min_unique_word_ratio
        for w in words
    ]

    # Rule B spreads outward from the Rule-A runs until nothing more is adjacent to a drop.
    spreading = True
    while spreading:
        spreading = False
        for i, w in enumerate(words):
            if drop[i] or len(w) >= cfg.min_block_words:
                continue
            before = i > 0 and drop[i - 1]
            after = i + 1 < len(drop) and drop[i + 1]
            if before or after:
                drop[i] = True
                spreading = True

    kept = [b.line for i, b in enumerate(blocks) if not drop[i]]
    if not kept:  # a transcript that is ALL boilerplate: hand it back rather than empty it
        return text, ()
    return "\n".join(kept), tuple(b.line for i, b in enumerate(blocks) if drop[i])


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


@dataclass(frozen=True)
class Phase:
    """One synthesis phase (TD-16 v2): a CONTIGUOUS, non-overlapping span of blocks.

    Carries text / 1-based index / total / time span, with the ``span`` property for the
    "phase N of M (HH:MM:SS–HH:MM:SS)" coherence note. Phases have NO overlap between
    neighbours: synthesis has no mechanical dedup, so any overlap would surface as
    duplicated prose at the seam — coherence is carried forward as prior-phase context
    (see the synthesis prompt's PRIOR CONTEXT section), not by re-including text.
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
