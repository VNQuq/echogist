"""Everything a folder run does — the free MP3 pool and the two-phase transcribe/summarize
run, in one module because they are one feature.

The Folder module of the menu asks a single question ("what should EchoGist produce for
this folder?") and answers it with the code here. Which engine runs depends only on how
far down the pipeline the operator asked to go:

* **MP3 only** → :func:`convert_many`. Local, free, and the one thing worth doing in
  PARALLEL: N independent ffmpeg processes saturate the machine, and nothing downstream
  waits on the result. Hence the worker pool, and hence :class:`Cancellation` — with the
  work off the main thread, Ctrl-C reaches ffmpeg only if something goes and terminates it.
* **Transcript / Summary** → :func:`plan_run`, then :func:`run_phase` twice. Strictly
  SEQUENTIAL: the GPU takes one Whisper job at a time, and the paid calls must reach the
  cost gate with a whole folder's real transcripts in hand rather than racing each other
  past it.

They were two modules (``batch`` / ``bulk``) and the names were synonyms, so nothing about
either name said which one spent money. Merged 2026-09-04 by operator call. The two engines
stay two functions — the pool's cancellation machinery is dead weight in a sequential run,
and forcing the sequential run through a pool would drop the parallelism the conversion
exists for — but they now share one vocabulary: one :class:`Item`, one :class:`Report`, one
:class:`Cancelled`, so a caller reads a folder run's outcome the same way whichever engine
produced it.

**Why the summary run is two phases and not one pass per file.** Transcription is local,
free and slow; summarization is the one paid stage. Running the whole folder through
TRANSCRIBE first means the cost gate is reached with the REAL transcript text of every file
in hand, so the quote is the same arithmetic the single-file flow uses, summed — no
duration-to-token projection (the scan's speech-rate constants are not on this path at all)
and no per-file confirm the operator has to sit and answer seven times. The cost is that the
first summary lands after the last transcription rather than after the first.

**What the scan bought.** The real folder came back with zero stem collisions, so the naming
machinery increment 2 was sized for has nothing to defend against here. Rather than build
it, this module refuses the one operation that would be wrong under a collision: reusing a
saved transcript whose stem is ambiguous (see :func:`plan_run`).

**Recovery is the artifacts, not a job engine** (CLAUDE.md). A saved transcript is the
phase-1 checkpoint and a saved summary is the phase-2 one, so an interrupted run is resumed
by starting it again: what is already on disk is skipped. Nothing else persists.

**One bad file never kills the run.** Every per-file failure is caught and recorded as an
:class:`Item`; the run keeps going and the caller renders the failures at the end. That is
what "fail loud" means over many files — nothing is skipped silently, but a corrupt 30th
file does not discard the 29 that already succeeded.

**Killswitch (CLAUDE.md).** Nothing here touches the network: the pool spawns the bundled
ffmpeg and nothing else, and :func:`folder_estimate` is pure arithmetic over local token
counts. Every stage takes an injected callable, so the whole module is unit-testable with
ffmpeg, TRANSCRIBE and SUMMARIZE stubbed.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from . import cost, extract, naming, scan
from .config import ModelTier
from .cost import CostEstimate

# The extract seam, mirroring ``menu.ExtractFn``: ``...`` because the real function takes
# keyword-only arguments a bare Callable cannot spell out. Tests inject a fake.
ExtractFn = Callable[..., Path]

#: What happened to one source. ``skipped`` is never a failure — either the caller decided
#: it (an already-mp3 source the operator chose not to re-encode) or the source already had
#: the artifact this phase produces, which is the resume path working. ``cancelled`` is a
#: file the pool never started; only the parallel conversion produces it.
ItemStatus = Literal["done", "skipped", "failed", "cancelled"]


@dataclass(frozen=True)
class Item:
    """The outcome for one source in one phase."""

    source: Path
    status: ItemStatus
    output: Path | None = None
    #: Human-readable reason — the stage error for ``failed``, the skip reason for
    #: ``skipped``. Empty for a clean run.
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """Every source's outcome, in the order the run reached them.

    On a pool run the caller-supplied items come first, then the pool's own results in the
    operator's selection order (see :func:`convert_many`'s ``extra``).
    """

    items: tuple[Item, ...]

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
    def cancelled(self) -> int:
        return self._count("cancelled")

    @property
    def failures(self) -> tuple[Item, ...]:
        """The files that broke — the only rows worth a table at the end.

        Deliberately NOT "everything that is not done": a skip is the resume path (or the
        operator's own choice) working, and a cancel is the operator steering; both are
        already accounted for in the headline counts. Listing them here would bury the one
        file with a real error under the fifty rows the operator already knows about.
        """
        return tuple(item for item in self.items if item.status == "failed")


class Cancelled(Exception):
    """Ctrl-C during a folder run. Carries the partial report so nothing is lost.

    Deliberately NOT in the menu's ``_RECOVERABLE`` tuple: a cancel is not a stage failure,
    it is the operator steering, and the flow that started the run is the one that must
    catch it and render what did finish. Every artifact produced before the cancel is
    already on disk, so re-running skips it.
    """

    def __init__(self, report: Report) -> None:
        super().__init__("Folder run cancelled by the operator.")
        self.report = report


# --------------------------------------------------------------------------- #
# MP3 conversion — many sources through one bounded worker pool
# --------------------------------------------------------------------------- #
# **Names are planned up front, single-threaded, in memory.** ``naming.dated_artifact_path``
# *selects* a free name but does not create it, and the real file only appears at the
# closing ``os.replace``. Two workers converting ``лекция.mp4`` and ``лекция.mkv`` would
# therefore both resolve to ``<date>-лекция.mp3`` and one would silently overwrite the
# other. :func:`_plan_output_paths` closes that window by walking the sources in order and
# passing each choice to the next lookup as ``taken``, then handing every worker its own
# ``out_path``. It writes **nothing**: a claim that existed on disk would survive a
# ``kill -9`` as a file indistinguishable from a real track, permanently pushing later
# conversions of the same title onto ``-2``. Publishing stays the one ``.part`` →
# ``os.replace`` scheme inside ``extract_audio`` — note ``.part`` could not double as the
# claim marker, since ``dedup_path`` deliberately cannot see it.
#
# **Cancellation is cooperative, real, and bounded.** :class:`Cancellation` both stops new
# files from starting and terminates the ffmpeg processes already running, so Ctrl-C
# mid-run stops in seconds instead of waiting out a two-hour lecture; a process that
# ignores the polite signal is killed outright once the grace window closes, so a cancel can
# never hang the console waiting on a child that will not leave. The partial result is not
# lost: it is carried out on :class:`Cancelled` so the caller can still show what got
# converted. A second Ctrl-C during that hand-off is absorbed rather than throwing the
# report away; a third tears everything down, because an interrupt must never become
# un-interruptible.

# How long a cancelled ffmpeg gets to exit on its own before it is killed outright, and
# how often a waiting worker checks. Both only matter on the cancel path: a healthy
# conversion just runs to completion between polls.
_TERMINATE_GRACE_SECONDS = 5.0
_CANCEL_POLL_SECONDS = 1.0


def _end(proc: subprocess.Popen[str], *, hard: bool) -> None:
    """Ask a process to stop (``hard=False``) or make it stop (``hard=True``).

    Both calls are no-ops on an already-reaped process, and a failure here must never
    mask the cancel itself — losing the race to a process that just exited is success.
    """
    with contextlib.suppress(OSError, ValueError):
        if hard:
            proc.kill()
        else:
            proc.terminate()


class Cancellation:
    """Cooperative cancel shared by every worker in the pool.

    Two jobs, both needed for Ctrl-C to feel immediate: no *new* file starts once
    :meth:`cancel` fires, and every ffmpeg already running is terminated. Without the
    second, a cancel would still wait out whatever conversion was in flight — on a
    two-hour lecture that is minutes of a console that looks hung.

    :meth:`runner` is an :data:`echogist.extract.Runner`, injected into ``extract_audio``,
    so the process handles this class needs are captured without ``extract`` having to
    know that folder runs or cancellation exist.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: set[subprocess.Popen[str]] = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        """Stop new work and terminate what is already running. Idempotent."""
        with self._lock:
            self._cancelled = True
            live = list(self._live)
        for proc in live:
            # Polite first. If SIGTERM is ignored, the worker waiting on this process
            # escalates to a hard kill once the grace window closes (see ``runner``).
            _end(proc, hard=False)

    def runner(self, argv: list[str]) -> tuple[int, str]:
        """Spawn ffmpeg, tracked, so :meth:`cancel` can reach it.

        Mirrors ``extract._default_runner``'s contract — ``(returncode, text)`` with
        stderr merged into stdout, utf-8/replace so a Windows OEM codepage never raises
        inside the spawn. Registration happens under the lock and re-checks the flag, so
        a cancel landing between the spawn and the registration still kills the process
        instead of leaking it.

        **The wait is bounded once a cancel is in play.** ``cancel`` only asks politely
        (SIGTERM / TerminateProcess); a plain ``communicate()`` would then block forever on
        a child that ignores it, hanging the run past every Ctrl-C handler — a console
        no keystroke can rescue. So after the grace window the process is killed outright.
        The poll is idle work only in the sense that it wakes once a second during a
        conversion that already takes minutes.
        """
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with self._lock:
            already_cancelled = self._cancelled
            self._live.add(proc)
        if already_cancelled:
            _end(proc, hard=False)

        deadline: float | None = None
        try:
            while True:
                try:
                    out, _ = proc.communicate(timeout=_CANCEL_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    if not self.cancelled:
                        continue  # a normal long conversion; keep waiting
                    now = time.monotonic()
                    if deadline is None:
                        deadline = now + _TERMINATE_GRACE_SECONDS
                    elif now >= deadline:
                        _end(proc, hard=True)  # SIGTERM was ignored — stop asking
                        deadline = now + _TERMINATE_GRACE_SECONDS
        finally:
            with self._lock:
                self._live.discard(proc)
        return proc.returncode, out


def expand_selection(selected: Iterable[Path]) -> list[Path]:
    """A picker result → the concrete files to convert, in order, deduped.

    A directory expands to its :data:`echogist.scan.CONVERTIBLE_SUFFIXES` members (one
    level — no
    recursion; the native multi-select is the operator's precision tool and a directory
    only ever arrives from the no-tkinter console fallback). A file is taken as-is,
    whatever its extension: naming it explicitly IS the intent, so an odd container is
    never silently dropped from a hand-picked selection.
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for entry in selected:
        # One walker for the whole project: the suffix filter, the sort and the junction
        # guard live in ``scan.walk``, and ``recursive=False`` is what keeps this the
        # one-level expansion the flow has always done.
        candidates = scan.walk(entry, recursive=False) if entry.is_dir() else [entry]
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:  # the same file reachable twice (dir + explicit pick)
                continue
            seen.add(resolved)
            files.append(path)
    return files


def _plan_output_paths(sources: Sequence[Path], out_dir: Path, today: date | None) -> list[Path]:
    """One output path per source, resolved single-threaded before any worker starts.

    **Creates nothing.** The collision this guards against lives entirely inside one run
    (``лекция.mp4`` and ``лекция.mkv`` in the same selection resolve to one dated name), and
    this loop is already sequential — so an in-memory ``claimed`` set makes each choice
    visible to the next without touching the disk.

    An earlier version claimed names by touching a 0-byte ``.mp3``. That worked, but a
    process killed mid-run left files indistinguishable from real artifacts, each one
    permanently pushing future conversions of the same title onto ``-2``. Nothing is
    created here now, so nothing can be orphaned — not by a failure, not by Ctrl-C, not by
    ``kill -9``. Publishing stays the single ``.part`` → ``os.replace`` scheme inside
    :func:`echogist.extract.extract_audio`; note ``.part`` could NOT have served as the
    claim marker, since ``naming.dedup_path`` deliberately cannot see it.
    """
    # The directory IS created — it is a container, not an artifact: an empty output/audio
    # is the normal resting state, nothing mistakes it for a converted track, and
    # extract_audio would create it anyway. Only the per-file placeholders are gone.
    out_dir.mkdir(parents=True, exist_ok=True)
    claimed: set[Path] = set()
    planned: list[Path] = []
    for source in sources:
        path = naming.dated_artifact_path(
            out_dir, source.stem, ".mp3", fallback="audio", today=today, taken=claimed
        )
        claimed.add(path)
        planned.append(path)
    return planned


def convert_many(
    sources: Sequence[Path],
    out_dir: Path,
    *,
    workers: int = 1,
    today: date | None = None,
    extract_fn: ExtractFn = extract.extract_audio,
    cancel: Cancellation | None = None,
    on_item: Callable[[Item], None] | None = None,
    extra: Sequence[Item] = (),
) -> Report:
    """Convert every source to mp3 through a pool of ``workers``, and report per file.

    ``workers=1`` is a genuinely sequential run (one worker, one ffmpeg at a time) — the
    same code path, not a special case, so there is no second implementation to keep in
    step. ``extra`` is folded into the report untouched: it carries the items the caller
    decided before the pool ran, such as mp3 sources the operator chose not to re-encode.

    Raises :class:`Cancelled` (carrying the partial report) on Ctrl-C. Every other
    per-file failure is recorded, never raised — see the module docstring.
    """
    cancel = cancel or Cancellation()

    def _one(source: Path, out_path: Path) -> Item:
        if cancel.cancelled:  # cancelled before this worker picked the job up
            return Item(source, "cancelled")
        try:
            produced = extract_fn(
                source,
                out_dir,
                out_path=out_path,
                today=today,
                runner=cancel.runner,
                log=lambda _message: None,  # per-file chatter would shred the aggregate bar
            )
        except Exception as exc:  # noqa: BLE001 - see below; nothing is swallowed
            # Deliberately broad. ExtractError and OSError are the expected two, but the
            # module's promise is that ONE bad file never costs the operator the other
            # twenty-nine — and a promise that holds only for the exception types we
            # predicted is not a promise. Nothing is hidden: the message lands in the
            # report's failure table under this file's name. KeyboardInterrupt is a
            # BaseException, so the Ctrl-C path below is unaffected. No cleanup is needed
            # either: extract_audio removes its own ``.part`` and nothing else was created.
            if cancel.cancelled:
                # A terminated ffmpeg exits non-zero, which surfaces as an ExtractError.
                # That is the cancel, not a bad file — reporting it as "failed" would tell
                # the operator their videos are broken when they simply pressed Ctrl-C.
                return Item(source, "cancelled")
            return Item(source, "failed", detail=str(exc) or type(exc).__name__)
        return Item(source, "done", output=produced)

    results: dict[int, Item] = {}
    interrupts = 0

    def _collect(index: int, item: Item) -> None:
        results[index] = item
        if on_item is not None:
            on_item(item)

    def _drain(futures: dict[Future[Item], int]) -> int:
        """Collect every outstanding future so the partial report is whole, not truncated.

        They return in seconds — terminated or never started. A SECOND Ctrl-C here (an
        impatient operator, since the drain prints nothing) must not throw the report away,
        so it is absorbed and re-fires the cancel. A THIRD tears everything down
        unconditionally: an interrupt must never become un-interruptible.
        """
        extra_interrupts = 0
        for future, index in futures.items():
            while index not in results:
                try:
                    _collect(index, future.result())
                except KeyboardInterrupt:
                    extra_interrupts += 1
                    if extra_interrupts >= 2:  # third overall
                        raise
                    cancel.cancel()
        return extra_interrupts

    futures: dict[Future[Item], int] = {}
    # Not a ``with`` block: its __exit__ is shutdown(wait=True), which on an interrupt
    # would join workers whose ffmpeg nobody had cancelled yet — a console frozen for the
    # length of the longest lecture. Cancellation and shutdown are sequenced by hand below.
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        # Name planning and submission live INSIDE the guard: a Ctrl-C in either window
        # used to escape as a raw KeyboardInterrupt (a BaseException, so run_menu's
        # ``except Exception`` backstop never saw it), crashing the app with a traceback
        # instead of returning to the menu — the opposite of what CLAUDE.md and USAGE.md
        # both promise.
        planned = _plan_output_paths(sources, out_dir, today)
        futures = {
            pool.submit(_one, source, out_path): index
            for index, (source, out_path) in enumerate(zip(sources, planned, strict=True))
        }
        for future in as_completed(futures):
            _collect(futures[future], future.result())
    except KeyboardInterrupt:
        # Caught explicitly rather than via BaseException: swallowing SystemExit here would
        # make the process hard to kill.
        interrupts = 1
    finally:
        try:
            if interrupts:
                cancel.cancel()  # in ``finally`` so it holds on every exit path
                interrupts += _drain(futures)
        finally:
            # wait=False: every future is resolved by now on both paths, and on the
            # third-interrupt teardown we must not block.
            pool.shutdown(wait=False)

    ordered = tuple(results[index] for index in sorted(results))
    report = Report(items=tuple(extra) + ordered)
    if interrupts:
        raise Cancelled(report)
    return report


# ------------------------------------------------------------------------- #
# The two-phase run — plan, price, then one phase at a time
# ------------------------------------------------------------------------- #


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
    fingerprints: Mapping[Path, str],
    summarized: set[str],
    transcripts: Mapping[str, Path],
) -> Plan:
    """Sort every source into finished / ready-to-summarize / needs-transcribing.

    Both joins are on the TD-31 source fingerprint — the identity of the recording, taken
    from its content — so neither depends on a filename. ``fingerprints`` maps each source
    to the value :mod:`echogist.scan` computed once during the walk; ``summarized`` is
    :func:`echogist.summarize.summary_index` (already paid for, TD-22); ``transcripts`` is
    :func:`echogist.scan.transcript_sources`.

    This replaced a stem match. Two courses that both number their lectures produced the
    same stem, and the transcript of course A's lecture 1 was handed to course B's — a
    PAID summary of the wrong recording, silent because every anchor still validated
    against real timecodes. A name says what a file is called; only its content says which
    recording it is.

    A source whose fingerprint is missing from ``fingerprints`` transcribes again rather
    than matching anything. Free, local and visible beats paid and silent, always.

    Nothing here recomputes a fingerprint. That is the seam the increment 1b re-encode
    depends on: a re-encoded file carries its PARENT's value in ``fingerprints``, so it
    reads as the same recording and is not re-bought. Recomputing would change every byte
    and silently re-buy the summary.
    """
    finished: list[Path] = []
    ready: list[tuple[Path, Path]] = []
    todo: list[Path] = []
    for source in sources:
        fingerprint = fingerprints.get(source)
        if fingerprint is None:
            todo.append(source)
            continue
        if fingerprint in summarized:
            finished.append(source)
            continue
        saved = transcripts.get(fingerprint)
        if saved is not None:
            ready.append((source, saved))
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
    output_cap: int,
    reconcile_floor: int,
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
            phase_inputs, tier, output_cap=output_cap, reconcile_floor=reconcile_floor
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
    on_result: Callable[[Item], None] = lambda item: None,
    recoverable: tuple[type[Exception], ...],
) -> Report:
    """Run ``step`` over ``sources`` in order, one at a time, collecting every outcome.

    A source that raises one of ``recoverable`` is recorded as ``failed`` and the run
    CONTINUES. That is deliberate and is not the "fail loud, return to menu" rule being
    bent: these are independent lectures, so letting one bad file abandon the other six
    would throw away local work already done and, in the paid phase, spend money and then
    discard the summaries it bought. Nothing is silent — every failure is carried in the
    report and shown at the end.

    ``KeyboardInterrupt`` is NOT caught here. It propagates as :class:`Cancelled`
    carrying the partial report, so the caller can show what finished; everything that
    finished is already on disk.
    """
    items: list[Item] = []
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        # The WHOLE iteration is guarded, not just ``step``. ``KeyboardInterrupt`` is a
        # BaseException, so one raised while ``on_start`` prints the next file's header or
        # while ``on_result`` renders the last one's row escapes run_menu's backstop
        # entirely and crashes the app — taking the partial report with it, and on phase 2
        # the "Actually spent" reconciliation for money already billed. The gap between
        # two files is exactly where an operator aims Ctrl-C.
        try:
            on_start(index, total, source)
            try:
                output = step(source)
            except recoverable as exc:
                item = Item(source=source, status="failed", detail=str(exc))
            else:
                item = Item(source=source, status="done", output=output)
            items.append(item)
            on_result(item)
        except KeyboardInterrupt:
            raise Cancelled(Report(tuple(items))) from None
    return Report(tuple(items))
