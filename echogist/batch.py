"""Batch MP3 conversion — many sources through one bounded worker pool.

The single-file flow (menu #1) converts one source behind a %/ETA bar. This module
is the *many* case: the operator multi-selects N videos and every one of them becomes
an ``output/audio/<date>-<title>.mp3``. It adds no encoding logic — every file still
goes through :func:`echogist.extract.extract_audio` (same ``-vn``, same libmp3lame VBR
~q2, same atomic ``.part`` publish), so a batch-produced mp3 is byte-for-byte the file
the single-file flow would have produced.

**Names are reserved up front, single-threaded.** ``naming.dated_artifact_path``
*selects* a free name but does not create it, and the real file only appears at the
closing ``os.replace``. Two workers converting ``лекция.mp4`` and ``лекция.mkv`` would
therefore both resolve to ``<date>-лекция.mp3`` and one would silently overwrite the
other. :func:`_reserve_paths` closes that window: it walks the sources in order, claims
each name with a zero-byte placeholder (so the next lookup dedups past it), and hands
each worker its own reserved ``out_path``. A file that fails, or never starts, has its
placeholder removed again.

**One bad file never kills the batch.** Every per-file failure is caught and recorded as
a :class:`BatchItem`; the pool keeps going and the caller renders the failures at the end.
That is what "fail loud" means for a batch — nothing is skipped silently, but a corrupt
30th file does not discard the 29 conversions that already succeeded.

**Cancellation is cooperative and real.** :class:`Cancellation` both stops new files from
starting and terminates the ffmpeg processes already running, so Ctrl-C mid-batch stops in
seconds instead of waiting out a two-hour lecture. The partial result is not lost: it is
carried out on :class:`BatchCancelled` so the caller can still show what got converted.

**Killswitch (CLAUDE.md).** Nothing here touches the network — it spawns the bundled
ffmpeg and nothing else. The ``extract_fn`` seam keeps the whole module unit-testable
with no ffmpeg and no media fixture.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from . import extract, naming

# The extract seam, mirroring ``menu.ExtractFn``: ``...`` because the real function takes
# keyword-only arguments a bare Callable cannot spell out. Tests inject a fake.
ExtractFn = Callable[..., Path]

# What happened to one source. ``skipped`` is decided by the caller (an already-mp3 source
# the operator chose not to re-encode) and passed through the report unchanged.
ItemStatus = Literal["converted", "skipped", "failed", "cancelled"]

# Extensions the batch flow will feed to ffmpeg when expanding a directory. Deliberately a
# closed list rather than "everything that is not an mp3": a folder of lectures also holds
# .txt/.srt/.jpg, and handing those to ffmpeg would fill the failure table with noise the
# operator cannot act on. Config-shaped data, so an exotic container is a one-line addition.
CONVERTIBLE_SUFFIXES = frozenset(
    {
        # video containers
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".ts",
        ".avi",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".wmv",
        ".flv",
        # Audio. ``.mp3`` is here on purpose: the flow ASKS before re-encoding an mp3, so
        # including it turns a silent drop into a visible question. Filtering it out here
        # would make a typed directory behave differently from the same files picked by
        # hand, which is the kind of quiet divergence "fail loud" exists to prevent.
        ".mp3",
        ".m4a",
        ".wav",
        ".flac",
        ".aac",
        ".ogg",
        ".opus",
        ".wma",
    }
)


@dataclass(frozen=True)
class BatchItem:
    """The outcome for one source file."""

    source: Path
    status: ItemStatus
    output: Path | None = None
    #: Human-readable reason — the ffmpeg tail for ``failed``, the skip reason for
    #: ``skipped``. Empty for a clean conversion.
    detail: str = ""


@dataclass(frozen=True)
class BatchReport:
    """Every source's outcome, in the operator's original selection order."""

    items: tuple[BatchItem, ...]

    def _count(self, status: ItemStatus) -> int:
        return sum(1 for item in self.items if item.status == status)

    @property
    def converted(self) -> int:
        return self._count("converted")

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
    def failures(self) -> tuple[BatchItem, ...]:
        """The files that broke — the only rows worth a table at the end.

        Deliberately NOT "everything that is not converted": a skip the operator asked
        for, and a cancel they triggered, are already accounted for in the headline
        counts. Listing them would bury the one file with a real ffmpeg error under
        fifty rows the operator already knows about.
        """
        return tuple(item for item in self.items if item.status == "failed")


class BatchCancelled(Exception):
    """Ctrl-C during a batch. Carries the partial report so nothing is lost.

    Deliberately NOT in the menu's ``_RECOVERABLE`` tuple: a batch cancel is not a stage
    failure, it is the operator steering, and the flow that started the batch is the one
    that must catch it and render the partial result.
    """

    def __init__(self, report: BatchReport) -> None:
        super().__init__("Batch cancelled by the operator.")
        self.report = report


class Cancellation:
    """Cooperative cancel shared by every worker in the pool.

    Two jobs, both needed for Ctrl-C to feel immediate: no *new* file starts once
    :meth:`cancel` fires, and every ffmpeg already running is terminated. Without the
    second, a cancel would still wait out whatever conversion was in flight — on a
    two-hour lecture that is minutes of a console that looks hung.

    :meth:`runner` is an :data:`echogist.extract.Runner`, injected into ``extract_audio``,
    so the process handles this class needs are captured without ``extract`` having to
    know that batches or cancellation exist.
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
            # A process that already exited raises here on some platforms; a terminate
            # that fails must never mask the cancel itself.
            with contextlib.suppress(OSError, ValueError):
                proc.terminate()

    def runner(self, argv: list[str]) -> tuple[int, str]:
        """Spawn ffmpeg, tracked, so :meth:`cancel` can reach it.

        Mirrors ``extract._default_runner``'s contract — ``(returncode, text)`` with
        stderr merged into stdout, utf-8/replace so a Windows OEM codepage never raises
        inside the spawn. Registration happens under the lock and re-checks the flag, so
        a cancel landing between the spawn and the registration still kills the process
        instead of leaking it.
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
            with contextlib.suppress(OSError, ValueError):
                proc.terminate()
        try:
            out, _ = proc.communicate()
        finally:
            with self._lock:
                self._live.discard(proc)
        return proc.returncode, out


def expand_selection(selected: Iterable[Path]) -> list[Path]:
    """A picker result → the concrete files to convert, in order, deduped.

    A directory expands to its :data:`CONVERTIBLE_SUFFIXES` members (one level — no
    recursion; the native multi-select is the operator's precision tool and a directory
    only ever arrives from the no-tkinter console fallback). A file is taken as-is,
    whatever its extension: naming it explicitly IS the intent, so an odd container is
    never silently dropped from a hand-picked selection.
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for entry in selected:
        candidates: list[Path]
        if entry.is_dir():
            candidates = sorted(
                child
                for child in entry.iterdir()
                if child.is_file() and child.suffix.lower() in CONVERTIBLE_SUFFIXES
            )
        else:
            candidates = [entry]
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:  # the same file reachable twice (dir + explicit pick)
                continue
            seen.add(resolved)
            files.append(path)
    return files


def _reserve_paths(sources: Sequence[Path], out_dir: Path, today: date | None) -> list[Path]:
    """Claim one output path per source, single-threaded, before any worker starts.

    The zero-byte placeholder is what makes the claim visible: ``dated_artifact_path``
    dedups against files that exist, so creating it immediately forces the next source
    with a colliding stem onto ``-2``. ``extract_audio`` later ``os.replace``-s its
    ``.part`` over the placeholder, and :func:`_discard` removes any that went unused.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    reserved: list[Path] = []
    for source in sources:
        path = naming.dated_artifact_path(
            out_dir, source.stem, ".mp3", fallback="audio", today=today
        )
        path.touch()
        reserved.append(path)
    return reserved


def _discard(path: Path) -> None:
    """Remove an unused reservation (best-effort — never mask the real outcome)."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def convert_many(
    sources: Sequence[Path],
    out_dir: Path,
    *,
    workers: int = 1,
    today: date | None = None,
    extract_fn: ExtractFn = extract.extract_audio,
    cancel: Cancellation | None = None,
    on_item: Callable[[BatchItem], None] | None = None,
    extra: Sequence[BatchItem] = (),
) -> BatchReport:
    """Convert every source to mp3 through a pool of ``workers``, and report per file.

    ``workers=1`` is a genuinely sequential run (one worker, one ffmpeg at a time) — the
    same code path, not a special case, so there is no second implementation to keep in
    step. ``extra`` is folded into the report untouched: it carries the items the caller
    decided before the pool ran, such as mp3 sources the operator chose not to re-encode.

    Raises :class:`BatchCancelled` (carrying the partial report) on Ctrl-C. Every other
    per-file failure is recorded, never raised — see the module docstring.
    """
    cancel = cancel or Cancellation()
    reserved = _reserve_paths(sources, out_dir, today)

    def _one(source: Path, out_path: Path) -> BatchItem:
        if cancel.cancelled:  # cancelled before this worker picked the job up
            _discard(out_path)
            return BatchItem(source, "cancelled")
        try:
            produced = extract_fn(
                source,
                out_dir,
                out_path=out_path,
                today=today,
                runner=cancel.runner,
                log=lambda _message: None,  # per-file chatter would shred the batch bar
            )
        except Exception as exc:  # noqa: BLE001 - see below; nothing is swallowed
            # Deliberately broad. ExtractError and OSError are the expected two, but the
            # module's promise is that ONE bad file never costs the operator the other
            # twenty-nine — and a promise that holds only for the exception types we
            # predicted is not a promise. Nothing is hidden: the message lands in the
            # report's failure table under this file's name. KeyboardInterrupt is a
            # BaseException, so the Ctrl-C path below is unaffected.
            _discard(out_path)
            # A terminated ffmpeg exits non-zero, which surfaces as an ExtractError. That
            # is the cancel, not a bad file — reporting it as "failed" would tell the
            # operator their videos are broken when they simply pressed Ctrl-C.
            if cancel.cancelled:
                return BatchItem(source, "cancelled")
            return BatchItem(source, "failed", detail=str(exc) or type(exc).__name__)
        return BatchItem(source, "converted", output=produced)

    results: dict[int, BatchItem] = {}
    interrupted = False

    def _collect(index: int, item: BatchItem) -> None:
        results[index] = item
        if on_item is not None:
            on_item(item)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures: dict[Future[BatchItem], int] = {
            pool.submit(_one, source, out_path): index
            for index, (source, out_path) in enumerate(zip(sources, reserved, strict=True))
        }
        try:
            for future in as_completed(futures):
                _collect(futures[future], future.result())
        except KeyboardInterrupt:
            # Stop new work, kill what is running, then drain: the outstanding futures
            # return in seconds (terminated or never started) and each still reports its
            # own outcome, so the partial report below is complete rather than truncated.
            interrupted = True
            cancel.cancel()
            for future, index in futures.items():
                if index not in results:
                    _collect(index, future.result())

    ordered = tuple(results[index] for index in sorted(results))
    report = BatchReport(items=tuple(extra) + ordered)
    if interrupted:
        raise BatchCancelled(report)
    return report
