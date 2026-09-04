"""Two-phase folder run — every lecture in a scanned tree, one gate (bulk v3 increment 2).

**Why two phases and not one pass per file.** Transcription is local, free and slow;
summarization is the one paid stage. Running the whole folder through TRANSCRIBE first
means the cost gate is reached with the REAL transcript text of every file in hand, so
the quote is the same arithmetic the single-file flow uses, summed — no duration-to-token
projection (TD-23's two unmeasured constants are not on this path at all) and no
per-file confirm the operator has to sit and answer seven times. The cost is that the
first summary lands after the last transcription rather than after the first.

**What the scan bought.** The real folder came back with zero stem collisions, so the
naming machinery increment 2 was sized for has nothing to defend against here. Rather
than build it, this module refuses the one operation that would be wrong under a
collision: reusing a saved transcript whose stem is ambiguous (see :func:`plan_run`).
Correct by construction, and it stays correct on a folder that does collide.

**Recovery is the artifacts, not a job engine** (CLAUDE.md). A saved transcript is the
phase-1 checkpoint and a saved summary is the phase-2 one, so an interrupted run is
resumed by starting it again: what is already on disk is skipped. Nothing else persists.

The stages here take injected callables and never touch the UI, so the whole run is
unit-testable with TRANSCRIBE and SUMMARIZE stubbed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import cost, naming, scan
from .config import ModelTier
from .cost import CostEstimate

#: What happened to one source. ``skipped`` is never a failure: it is a source that
#: already had the artifact this phase produces, which is the resume path working.
ItemStatus = Literal["done", "skipped", "failed"]


@dataclass(frozen=True)
class BulkItem:
    """The outcome for one source in one phase."""

    source: Path
    status: ItemStatus
    output: Path | None = None
    #: Human-readable reason — the stage error for ``failed``, the skip reason for
    #: ``skipped``. Empty for a clean run.
    detail: str = ""


@dataclass(frozen=True)
class BulkReport:
    """Every source's outcome, in the order the run reached them."""

    items: tuple[BulkItem, ...]

    def _count(self, status: ItemStatus) -> int:
        return sum(1 for item in self.items if item.status == status)

    @property
    def done(self) -> int:
        return self._count("done")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def failed(self) -> int:
        return self._count("failed")

    @property
    def failures(self) -> tuple[BulkItem, ...]:
        """The sources that broke — the only rows worth a table at the end.

        A skip is already accounted for in the headline counts and is the resume path
        doing its job, so listing skips here would bury a real failure under the six
        files that were fine.
        """
        return tuple(item for item in self.items if item.status == "failed")


class BulkCancelled(Exception):
    """Ctrl-C during a folder run. Carries the partial report so nothing is lost.

    Mirrors :class:`echogist.batch.BatchCancelled`, and for the same reason: a cancel is
    the operator steering, not a stage failure, so the flow that started the run is the
    one that catches it and renders what did finish. Every artifact produced before the
    cancel is already on disk, so re-running skips it.
    """

    def __init__(self, report: BulkReport) -> None:
        super().__init__("Folder run cancelled by the operator.")
        self.report = report


@dataclass(frozen=True)
class Plan:
    """What a folder run will actually do, decided before anything runs.

    Three buckets, and every source lands in exactly one: ``summarized`` is finished
    work, ``ready`` needs only the paid phase, ``to_transcribe`` needs both phases.
    """

    #: Already has a summary on disk (the TD-22 back-link). Nothing to do, nothing to pay.
    summarized: tuple[Path, ...]
    #: Has exactly one unambiguous saved transcript — phase 1 is skipped for these.
    ready: tuple[tuple[Path, Path], ...]
    #: Needs transcribing first.
    to_transcribe: tuple[Path, ...]

    @property
    def sources(self) -> tuple[Path, ...]:
        """Every source the plan covers, in bucket order."""
        return self.summarized + tuple(src for src, _ in self.ready) + self.to_transcribe


def plan_run(
    sources: Sequence[Path],
    *,
    summarized: set[str],
    transcripts: dict[str, tuple[Path, ...]],
) -> Plan:
    """Sort every source into finished / ready-to-summarize / needs-transcribing.

    ``summarized`` is :func:`echogist.summarize.summary_index` — resolved source paths
    that already have a summary, so a second run over the same folder does not re-pay for
    a lecture already bought (TD-22). ``transcripts`` is
    :func:`echogist.scan.transcript_files`.

    A saved transcript is reused ONLY when the match is unambiguous in both directions:
    exactly one transcript file carries the stem, and exactly one source in this run maps
    to it. Anything else transcribes again. That is the deliberate trade for not building
    increment 2's naming machinery: re-doing free local work is visible and merely slow,
    whereas summarizing one lecture from another's transcript is a paid, silent fidelity
    violation — the failure this whole pipeline exists to prevent. Note the asymmetry with
    the summary skip above, which is safe at any stem because it joins on the RESOLVED
    SOURCE PATH rather than on a name.
    """
    stems = [scan.stem_key(source) for source in sources]
    stem_counts = Counter(stems)

    finished: list[Path] = []
    ready: list[tuple[Path, Path]] = []
    todo: list[Path] = []
    for source, stem in zip(sources, stems, strict=True):
        if str(naming.resolve_source(source)) in summarized:
            finished.append(source)
            continue
        saved = transcripts.get(stem, ())
        if len(saved) == 1 and stem_counts[stem] == 1:
            ready.append((source, saved[0]))
        else:
            todo.append(source)
    # Sorted, not walk order: os.walk's order is filesystem-dependent, so an unsorted plan
    # plays a seven-lecture course back as 4, 2, 1, 7 and cannot be reproduced between
    # runs. Sorting groups each folder and matches the order the scan report showed.
    return Plan(
        summarized=tuple(sorted(finished)),
        ready=tuple(sorted(ready)),
        to_transcribe=tuple(sorted(todo)),
    )


def folder_estimate(
    phase_inputs_per_file: Sequence[Sequence[int]],
    tier: ModelTier,
    *,
    per_call_output_tokens: int,
) -> CostEstimate:
    """One quote for the whole folder: the per-file estimates, summed.

    Each file is priced by :func:`echogist.cost.estimate_cost_synthesis` exactly as the
    single-file flow prices it — K phase calls plus its OWN reconcile — and the totals
    are added. Summing the token counts (rather than the dollars) keeps one
    :class:`CostEstimate` that still names the tier's rates, so the existing threshold
    gate and message formatting work unchanged.

    Do not be tempted to concatenate every file's phases into one list and call
    ``estimate_cost_synthesis`` once: that prices a single reconcile for the whole folder
    when the run actually makes one per file, and undershoots by (files - 1) reconcile
    calls. Under-quoting is the one failure mode that spends money the operator did not
    agree to.
    """
    total_input = 0
    total_output = 0
    for phase_inputs in phase_inputs_per_file:
        est = cost.estimate_cost_synthesis(
            phase_inputs, tier, per_call_output_tokens=per_call_output_tokens
        )
        total_input += est.input_tokens
        total_output += est.output_tokens
    return CostEstimate(
        input_tokens=total_input,
        output_tokens=total_output,
        price_in_per_mtok=tier.price_in_per_mtok,
        price_out_per_mtok=tier.price_out_per_mtok,
    )


def run_phase(
    sources: Sequence[Path],
    step: Callable[[Path], Path],
    *,
    on_start: Callable[[int, int, Path], None] = lambda i, n, p: None,
    on_result: Callable[[BulkItem], None] = lambda item: None,
    recoverable: tuple[type[Exception], ...],
) -> BulkReport:
    """Run ``step`` over ``sources`` in order, one at a time, collecting every outcome.

    A source that raises one of ``recoverable`` is recorded as ``failed`` and the run
    CONTINUES. That is deliberate and is not the "fail loud, return to menu" rule being
    bent: these are independent lectures, so letting one bad file abandon the other six
    would throw away local work already done and, in the paid phase, spend money and then
    discard the summaries it bought. Nothing is silent — every failure is carried in the
    report and shown at the end.

    ``KeyboardInterrupt`` is NOT caught here. It propagates as :class:`BulkCancelled`
    carrying the partial report, so the caller can show what finished; everything that
    finished is already on disk.
    """
    items: list[BulkItem] = []
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        on_start(index, total, source)
        try:
            output = step(source)
        except KeyboardInterrupt:
            raise BulkCancelled(BulkReport(tuple(items))) from None
        except recoverable as exc:
            item = BulkItem(source=source, status="failed", detail=str(exc))
        else:
            item = BulkItem(source=source, status="done", output=output)
        items.append(item)
        on_result(item)
    return BulkReport(tuple(items))
