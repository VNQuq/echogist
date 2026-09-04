"""T2-T7 — the folder scanner (bulk v3, increment 1).

Point EchoGist at a folder and get a picture of it before spending anything: per-folder
file counts, total hours, total bytes, how many candidate transcripts already exist, an
upper-bound dollar figure for summarizing all of it, which files share a name, and which
ones ffmpeg cannot read.

**Read-only, offline, free.** The one thing this module writes is its own probe cache
(``output/.scan-cache.json``). No conversion, no transcription, no network — the
killswitch is not merely respected here, there is no paid stage to gate.

This is also the project's ONE tree walker: :func:`walk` is what
:func:`echogist.folder.expand_selection` delegates to at ``recursive=False``, so the
suffix filter, the resolved-path dedup and the junction guard exist once.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import cost, guard, naming
from .config import ModelConfig, ModelTier
from .cost import CostEstimate
from .extract import ExtractError, Runner, _default_probe_runner, probe_media
from .ui import Choice, human_size

# Extensions a directory expansion will feed to ffmpeg. Moved here from the folder runner
# because this module owns the walker; ``folder`` has no copy and no re-export, so there is exactly
# one list. Deliberately a
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

# EchoGist's own artifact tree. Never counted as a source, wherever it turns up under the
# scan root: without this, increment 2 would re-process its own extracted mp3s.
_OUTPUT_DIR_NAME = "output"

# The probe cache. Bumping the schema discards every entry rather than migrating it —
# these are re-derivable in one ffmpeg spawn, so a migration would cost more than a
# re-probe.
CACHE_FILENAME = ".scan-cache.json"
_CACHE_SCHEMA = 1

# Windows file attributes marking a cloud placeholder: the file is listed in the
# directory but its bytes live in the cloud. FILE_ATTRIBUTE_OFFLINE (0x1000) and
# FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (0x400000). ``ffmpeg -i`` on one triggers a full
# hydration download, which turns an offline two-minute scan into hours of transfer.
_PLACEHOLDER_ATTRS = 0x1000 | 0x400000

# A saved transcript is ``<YYYY-MM-DD>-<sanitized stem>.txt``, optionally with a ``-N``
# dedup suffix. Parsing the name BACK to its stem is what makes transcript detection one
# pass over the directory instead of one regex build per (source, transcript) pair.
_TRANSCRIPT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<stem>.+?)(?:-\d+)?$")


class ScanCancelled(Exception):
    """Ctrl-C during a scan. Carries the partial result so the probing is not lost.

    Same contract as :class:`echogist.folder.Cancelled`: not a stage failure, so it
    is deliberately outside the menu's ``_RECOVERABLE`` tuple and the flow that started
    the scan is the one that catches it. Unlike a batch there is nothing to terminate —
    ``ffmpeg -i`` exits in milliseconds — so the cancel only needs to flush the cache.
    """

    def __init__(self, result: ScanResult) -> None:
        super().__init__("Scan cancelled by the operator.")
        self.result = result


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MediaFile:
    """One media file the scan found and probed.

    ``duration is None`` means the probe produced no ``Duration:`` line — such a file is
    reported as unreadable instead, so a ``MediaFile`` in a :class:`ScanResult` always
    carries a duration. ``audio_kbps`` and ``has_video`` are NOT consumed by this
    increment; they are probed and cached now because the probe is the expensive part and
    the separate mp3-re-encode rule would otherwise have to re-walk the whole library to
    get them.
    """

    path: Path
    rel: Path
    size: int
    duration: float
    audio_kbps: float | None
    has_video: bool


@dataclass(frozen=True)
class ScanResult:
    """Everything one scan learned about a tree.

    The three tuples are disjoint and together account for every candidate the walk
    found: nothing is dropped silently, which is the whole point of running a scan
    before spending money.

    ``unreadable`` and ``placeholders`` carry ABSOLUTE paths, unlike ``MediaFile.rel``:
    the operator may need to go find one of those files, and by then the scan root is not
    on screen any more. The render helpers below relativize them for display.
    """

    root: Path
    files: tuple[MediaFile, ...]
    unreadable: tuple[tuple[Path, str], ...]  # path, reason
    placeholders: tuple[Path, ...]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def total_seconds(self) -> float:
        return sum(f.duration for f in self.files)


# --------------------------------------------------------------------------- #
# Walk
# --------------------------------------------------------------------------- #
def walk(
    root: Path,
    *,
    recursive: bool = True,
    on_error: Callable[[OSError], None] | None = None,
    exclude: Path | None = None,
) -> list[Path]:
    """Media files under ``root``, sorted, deduped by resolved path.

    The project's only tree walker. ``recursive=False`` reproduces the one-level
    expansion :func:`echogist.folder.expand_selection` has always done, so the batch flow
    is unchanged and its existing tests are this function's regression harness.

    ``on_error`` is handed straight to :func:`os.walk` and is called once per directory
    that cannot be LISTED. It is not optional decoration: ``os.walk``'s default swallows
    a ``PermissionError`` from ``scandir`` and simply yields nothing for that directory,
    so without this every media file inside an ACL-locked or cloud-sync-locked folder
    disappears from the scan with no trace in any bucket. A scanner whose whole job is to
    show the operator what is there must never lose a folder in silence.

    Two directories are pruned in place, during the walk rather than filtered after it,
    so their subtrees are never even listed:

    * any directory named ``output``, or — when ``exclude`` is given — any directory whose
      RESOLVED path IS that directory. EchoGist's own artifacts must never be counted as
      sources, or a bulk run would re-process what it just produced. The name rule alone
      is not enough (TD-25): an NTFS junction called anything else but pointing AT the
      real ``output`` tree walks straight through it, and cloud-sync clients create such
      junctions routinely. Callers that know where the artifacts live pass ``exclude``
      and get the authoritative check; the name rule stays as the floor for callers that
      do not (``folder.expand_selection``);
    * any directory whose RESOLVED path has already been visited. ``os.walk`` does not
      follow symlinks by default, but that says nothing about NTFS directory junctions,
      which cloud-sync clients create routinely in a media library and which would
      otherwise send the walk around a loop forever.
    """
    found: list[Path] = []
    seen_files: set[Path] = set()
    seen_dirs: set[Path] = set()
    excluded = _norm(_resolve(exclude)) if exclude is not None else None
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        here = Path(dirpath)
        if not recursive:
            dirnames[:] = []
        else:
            dirnames[:] = [name for name in dirnames if _keep_dir(here / name, seen_dirs, excluded)]
        for name in sorted(filenames):
            path = here / name
            if path.suffix.lower() not in CONVERTIBLE_SUFFIXES:
                continue
            resolved = _resolve(path)
            if resolved in seen_files:  # the same file reachable twice
                continue
            seen_files.add(resolved)
            found.append(path)
    return found


def _keep_dir(path: Path, seen: set[Path], excluded: str | None = None) -> bool:
    """Whether to descend into ``path``, recording it as visited when we do."""
    if path.name.lower() == _OUTPUT_DIR_NAME:
        return False
    resolved = _resolve(path)
    if excluded is not None and _norm(resolved) == excluded:  # TD-25: a junction to output/
        return False
    if resolved in seen:  # a junction pointing back at an ancestor
        return False
    seen.add(resolved)
    return True


def _norm(path: Path) -> str:
    """The comparison key for "is this the same directory".

    ``os.path.normcase`` is a no-op on POSIX and lowercases plus normalizes separators on
    Windows, which is exactly what the ``output/`` self-exclusion needs there: ``resolve``
    does not normalize case on Windows, so a junction and the real tree can come back
    spelled differently and compare unequal. Used only for the directory identity check —
    the FILE dedup key deliberately does not fold case (see :func:`_resolve`).
    """
    return os.path.normcase(str(path))


def _resolve(path: Path) -> Path:
    """The scan's dedup key for a source file — :func:`naming.resolve_source`.

    Kept as a local alias because the rule is shared with the summary back-link (TD-22)
    and must stay one definition; see that function for why it does NOT casefold.
    """
    return naming.resolve_source(path)


# --------------------------------------------------------------------------- #
# Probe cache
# --------------------------------------------------------------------------- #
def _cache_key(path: Path, size: int, mtime_ns: int) -> str:
    """``<resolved path>|<size>|<mtime_ns>`` — an edit invalidates the entry by itself.

    ``|`` is illegal in a Windows filename, so it cannot appear inside the path part and
    the join is unambiguous.
    """
    return f"{_resolve(path)}|{size}|{mtime_ns}"


def load_cache(path: Path) -> dict[str, dict[str, object]]:
    """The cached probes, or an empty dict.

    A cache problem NEVER fails a scan: a missing file, unreadable bytes, invalid JSON,
    the wrong shape, or a different ``schema`` all discard the whole file and fall back
    to re-probing. The cost of being wrong here is one extra ffmpeg spawn per file.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema") != _CACHE_SCHEMA:
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {str(k): v for k, v in entries.items() if isinstance(v, dict)}


def save_cache(path: Path, entries: dict[str, dict[str, object]]) -> None:
    """Publish the cache atomically: write ``.part``, then ``os.replace``.

    The same scheme ``extract_audio`` uses for an mp3, and load-bearing for the same
    reason twice over. A direct overwrite is truncatable by the very Ctrl-C this flush
    exists to survive, and a truncated file fails the schema check on the next run — so
    the crash would discard every entry and force a full re-probe at the worst possible
    moment. Failing to write the cache is not a scan failure: the results are already in
    hand and the only loss is next run's speed.
    """
    payload = {"schema": _CACHE_SCHEMA, "entries": entries}
    part = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(part, path)
    except OSError:
        with contextlib.suppress(OSError):
            part.unlink()  # sweep our own leftover rather than leave it for the next run


def _is_placeholder(st: os.stat_result) -> bool:
    """True for a cloud "files on demand" placeholder whose bytes are not local.

    ``st_file_attributes`` exists only on Windows, hence the ``getattr`` default: off
    Windows every file reads as present, which is correct there.
    """
    return bool(getattr(st, "st_file_attributes", 0) & _PLACEHOLDER_ATTRS)


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #
def scan_tree(
    root: Path,
    *,
    cache_path: Path,
    exe: str,
    runner: Runner = _default_probe_runner,
    exclude: Path | None = None,
) -> ScanResult:
    """Walk ``root``, probe every media file, and return what is there.

    Reads only, apart from the cache flush. Every file lands in exactly one of the three
    result buckets; nothing is dropped:

    * **placeholder** — a cloud file whose bytes are not local. Never probed, because the
      probe would trigger a full hydration download of the whole library.
    * **unreadable** — ``stat`` raised, the probe raised (timeout on a dead share), or the
      probe produced no ``Duration:`` line. Exit status is deliberately not consulted:
      ``ffmpeg -i`` with no output always exits non-zero, by design.
    * **file** — everything else.

    A cached negative (``duration: null``) is honoured, not re-probed: the file was
    already asked and answered, and the cache key changes the moment the file does.

    ``exclude`` is EchoGist's own ``output`` directory; see :func:`walk` for why the name
    it happens to be reachable under is not enough to keep the artifacts out (TD-25).

    Ctrl-C raises :class:`ScanCancelled` carrying the partial result, and the cache is
    flushed first either way, so an interrupted cold scan keeps the spawns it paid for.
    """
    files: list[MediaFile] = []
    unreadable: list[tuple[Path, str]] = []
    placeholders: list[Path] = []
    cache = load_cache(cache_path)
    fresh: dict[str, dict[str, object]] = {}

    def result() -> ScanResult:
        return ScanResult(
            root=root,
            files=tuple(files),
            unreadable=tuple(unreadable),
            placeholders=tuple(placeholders),
        )

    def note_unlistable(exc: OSError) -> None:
        """A directory ``os.walk`` could not list. Its CONTENTS are unknowable — that is
        what "cannot list" means — so the folder itself is what gets reported, and the
        operator learns that part of the tree was not counted."""
        where = Path(exc.filename) if exc.filename else root
        unreadable.append((where, f"cannot list this folder: {exc.strerror or exc}"))

    try:
        for path in walk(root, on_error=note_unlistable, exclude=exclude):
            try:
                st = path.stat()
            except OSError as exc:
                unreadable.append((path, f"cannot read the file: {exc.strerror or exc}"))
                continue
            if _is_placeholder(st):
                placeholders.append(path)
                continue

            key = _cache_key(path, st.st_size, st.st_mtime_ns)
            entry = cache.get(key)
            if entry is None:
                try:
                    probe = probe_media(path, exe, runner)
                except ExtractError as exc:
                    unreadable.append((path, str(exc)))
                    continue
                entry = {
                    "duration": probe.duration,
                    "audio_kbps": probe.audio_kbps,
                    "has_video": probe.has_video,
                }
            fresh[key] = entry

            duration = _valid_duration(entry.get("duration"))
            if duration is None:  # cached or fresh negative, or a corrupt cache value
                unreadable.append((path, "ffmpeg reported no duration for this file"))
                continue
            files.append(
                MediaFile(
                    path=path,
                    rel=_relative(path, root),
                    size=st.st_size,
                    duration=duration,
                    audio_kbps=_valid_kbps(entry.get("audio_kbps")),
                    has_video=bool(entry.get("has_video")),
                )
            )
    except KeyboardInterrupt as exc:
        # MERGED, not replaced. ``fresh`` holds only what this run reached; the entries
        # past the interruption point are still valid and were paid for by an earlier
        # run. Writing ``fresh`` alone would delete them, so Ctrl-C at file 5 of 500
        # would cost 495 re-probes on the next run — the exact opposite of what stopping
        # early is supposed to save.
        save_cache(cache_path, {**cache, **fresh})
        raise ScanCancelled(result()) from exc
    # A COMPLETE run rewrites the file from live results, which is the whole eviction
    # policy: an entry whose key no longer matches any file on disk simply falls out.
    # Consequence, accepted: the cache holds ONE scan root at a time, so alternating
    # between two folders re-probes each time. The console scans one library.
    save_cache(cache_path, fresh)
    return result()


def _valid_duration(value: object) -> float | None:
    """A usable duration in seconds, or None for anything the cache should not be trusted on.

    The cache is plaintext JSON that a later run READS BACK and turns into the dollar
    figure the operator decides on, so its numbers are input, not internal state. A
    type check alone is not enough: a negative duration quietly SHORTENS the total and
    UNDER-prices the folder, which is the one direction CLAUDE.md forbids, and it looks
    entirely plausible on screen. NaN and infinity are worse in a different way — they
    reach ``math.ceil`` in the projection and abort the report half-rendered.

    ``ffmpeg``'s ``Duration:`` line cannot parse to a negative number, so a live probe
    never produces one; this guards the cache file, which anything with write access to
    ``output/`` can edit. Bad values are reported as unreadable, exactly like a file with
    no ``Duration:`` line at all.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None  # bool is an int subclass; True would otherwise read as 1 second
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0 or seconds > _MAX_PLAUSIBLE_DURATION_S:
        return None
    return seconds


def _valid_kbps(value: object) -> float | None:
    """A usable audio bitrate, on the same terms as :func:`_valid_duration`.

    Nothing in this increment reads it, but the mp3 re-encode rule will, and a negative
    or infinite bitrate there decides whether a file is re-encoded. None already means
    "never re-encode", so a bad value degrades to the safe answer.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    kbps = float(value)
    return kbps if math.isfinite(kbps) and kbps >= 0.0 else None


def _relative(path: Path, root: Path) -> Path:
    """``path`` relative to the scan root, or the path itself when it is not under it
    (a junction can land the walk outside the tree it started in)."""
    try:
        return path.relative_to(root)
    except ValueError:
        return path


# --------------------------------------------------------------------------- #
# Transcript detection
# --------------------------------------------------------------------------- #
def transcript_index(transcripts_dir: Path) -> Counter[str]:
    """How many saved transcripts exist per sanitized source stem.

    Built by listing the directory ONCE and parsing each transcript filename back to its
    stem, rather than testing each source against each transcript. The pairwise version
    is ``O(files x transcripts)`` with a regex compiled in the inner loop — 150k
    compilations on a 500-file library with 300 transcripts, inside a 30-second budget.

    The count is a CANDIDATE count, not an answer: two sources with the same sanitized
    stem share one bucket. Disambiguating them is exactly what the collision report is
    for, which is why the column is labelled "candidate".
    """
    return Counter({stem: len(paths) for stem, paths in transcript_files(transcripts_dir).items()})


def transcript_files(transcripts_dir: Path) -> dict[str, tuple[Path, ...]]:
    """The saved transcripts on disk, grouped by the sanitized source stem they were
    named from — the path-carrying form of :func:`transcript_index`.

    A bulk run needs the PATH to reuse a transcript instead of re-transcribing hours of
    audio, and both callers must agree on which filenames count as a transcript, so the
    name-parsing rule lives here once and the counting form is derived from this one.
    Values are sorted for a stable pick, and a stem with more than one file is
    deliberately left ambiguous for the caller to refuse: nothing on disk says which
    recording a second ``-2`` transcript belongs to.
    """
    groups: dict[str, list[Path]] = {}
    if not transcripts_dir.is_dir():
        return {}
    # No try/except: Path.glob yields nothing for a missing or unreadable directory
    # rather than raising, so a handler here would be dead code (verified, and the
    # is_dir guard above already covers the missing case).
    for path in sorted(transcripts_dir.glob("*.txt")):
        match = _TRANSCRIPT_NAME.match(path.stem)
        if match:
            groups.setdefault(match["stem"], []).append(path)
    return {stem: tuple(paths) for stem, paths in groups.items()}


def stem_key(path: Path) -> str:
    """The sanitized stem an artifact for ``path`` would be named from. ``fallback`` is a
    REQUIRED keyword-only argument on ``sanitize_stem``; ``transcript`` matches what
    ``transcribe.save_transcript`` passes, so the index keys line up.

    Public because a bulk run has to key its plan the SAME way the transcript index and
    the collision report key theirs — three callers agreeing on one name rule."""
    return naming.sanitize_stem(path.stem, fallback="transcript")


def candidate_transcripts(files: Iterable[MediaFile], index: Counter[str]) -> int:
    """How many of ``files`` have at least one transcript candidate on disk."""
    return sum(1 for f in files if index.get(stem_key(f.path), 0) > 0)


# --------------------------------------------------------------------------- #
# Collisions
# --------------------------------------------------------------------------- #
def collisions(files: Sequence[MediaFile]) -> list[tuple[str, tuple[Path, ...]]]:
    """Groups of files whose artifacts would be named from the SAME stem, sorted.

    Free at scan time and the operator-visible evidence for the naming problem: two
    recordings that share a stem are the input that turns a latent naming assumption into
    a silently wrong paid summary. Grouping is on the SANITIZED stem, so ``Лекция: 1`` and
    ``Лекция- 1`` collide here exactly as they would on disk.
    """
    groups: dict[str, list[Path]] = {}
    for f in files:
        # ``rel``, not ``path``: an absolute path per member wraps over three lines in the
        # report and buries the one part that tells the two files apart — which folder
        # each is in. The operator picked the root seconds ago.
        groups.setdefault(stem_key(f.path), []).append(f.rel)
    return sorted((stem, tuple(sorted(paths))) for stem, paths in groups.items() if len(paths) > 1)


# --------------------------------------------------------------------------- #
# Cost projection
# --------------------------------------------------------------------------- #
# Characters of pure Cyrillic used to measure the guard's per-character token rate once.
# The rate is linear in length, so one sample scales to any transcript: feeding a real
# synthetic string per file would run guard's per-character generator over ~189k chars
# for a 3h lecture (~95M character tests across a 500-file library, on the order of ten
# seconds) for a number that is a multiplication.
_RATE_SAMPLE_CHARS = 1000

# A duration past which the value is not a recording but a corrupt cache entry. 1000 hours
# is ~41 days of continuous audio; the longest thing this tool has ever seen is a 3-hour
# lecture. The bound exists because the projection turns duration into a PHASE COUNT and
# then a list of that length, so an absurd number is an absurd allocation, not a big
# number: a cached 1e300 raises OverflowError mid-report rather than pricing anything.
_MAX_PLAUSIBLE_DURATION_S = 1000 * 3600.0


@lru_cache(maxsize=1)
def _cyrillic_tokens_per_char() -> float:
    """The guard's token rate for Russian text, measured ONCE rather than re-typed.

    Cached because it is a constant: the docstring below says "sampled once", and without
    the cache ``project_cost`` re-derived it per file — 97% of its runtime on a 500-file
    folder spent recomputing the same number. Still a function, not a module constant, so
    it has no import-time side effect and the tests can call it directly.

    The sample is Cyrillic on purpose. ``guard`` rates Cyrillic at roughly double its
    default rate, so a Latin sample would HALVE the headline figure — and CLAUDE.md
    requires the estimate to run high. ``prompt_overhead=0`` isolates the per-character
    body rate; the overhead is added back per phase in :func:`project_file`, after the
    split, which is where it belongs.
    """
    sample = "а" * _RATE_SAMPLE_CHARS
    return guard.estimate_input_tokens(sample, prompt_overhead=0) / _RATE_SAMPLE_CHARS


def project_file(
    duration_seconds: float, model_config: ModelConfig, tier: ModelTier
) -> CostEstimate:
    """Projected cost of summarizing ONE file of this duration, priced against ``tier``.

    Duration is all there is to go on at scan time — nothing has been transcribed — so
    the chain is seconds -> words -> characters -> tokens -> phases -> dollars, with every
    constant biased high (see :class:`echogist.config.ScanConfig`).

    **The prompt overhead is added per phase, AFTER the split.** ``guard.estimate_input_
    tokens`` folds its flat overhead in ONCE, for one call; ``cost.estimate_cost_
    synthesis`` contracts that EVERY element of ``phase_input_tokens`` already includes
    it, because each element is a separate API call that pays for its own prompt. Splitting
    one already-overhead-inclusive total across K phases therefore under-counts input by
    ``PROMPT_OVERHEAD_TOKENS * (K - 1)`` — an estimate biased LOW, the one direction
    CLAUDE.md forbids. This is the bug round-3 review caught in the first draft of the
    design, and the reason a test asserts the overhead appears K times, not once.

    ``chunk.plan_phases`` is deliberately bypassed: it bins REAL transcript blocks, and at
    scan time there is no transcript. K is derived arithmetically from the same
    ``phase_target_tokens`` knob instead, duplicating that formula on purpose.
    """
    scan_cfg = model_config.scan
    words = duration_seconds * scan_cfg.words_per_minute / 60.0
    chars = words * scan_cfg.chars_per_word
    body = math.ceil(_cyrillic_tokens_per_char() * chars)
    phases = max(1, math.ceil(body / model_config.chunk.phase_target_tokens))
    per_phase = math.ceil(body / phases) + guard.PROMPT_OVERHEAD_TOKENS
    return cost.estimate_cost_synthesis(
        [per_phase] * phases,
        tier,
        output_cap=model_config.summarize.max_output_tokens,
        reconcile_floor=model_config.summarize.reconcile_output_floor_tokens,
    )


def project_cost(
    files: Iterable[MediaFile], model_config: ModelConfig, tier: ModelTier
) -> CostEstimate:
    """Projected cost of summarizing every file, priced against ``tier``.

    Per file, then summed. K is a per-file quantity, so summing the durations first and
    computing one K for the total would be wrong in both directions: it would over-count
    the overhead for a folder of short clips and under-count the phase split for a folder
    of long lectures.
    """
    total_in = 0
    total_out = 0
    for f in files:
        estimate = project_file(f.duration, model_config, tier)
        total_in += estimate.input_tokens
        total_out += estimate.output_tokens
    return CostEstimate(
        input_tokens=total_in,
        output_tokens=total_out,
        price_in_per_mtok=tier.price_in_per_mtok,
        price_out_per_mtok=tier.price_out_per_mtok,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def plural(count: int, singular: str, plural: str | None = None) -> str:
    """``1 file`` / ``2 files``. The scan report is read by a person, and "1 transcript
    candidates" reads like a bug in the tool rather than a count of one."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count:,} {word}"


def human_hours(seconds: float) -> str:
    """``18h 04m`` — the unit the operator thinks in for a lecture library."""
    total_minutes = int(seconds // 60)
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def group_by_folder(result: ScanResult) -> list[tuple[Path, tuple[MediaFile, ...]]]:
    """The scan's files bucketed by containing folder, sorted by folder path.

    A function rather than a dataclass: per-folder statistics are a grouping of the files
    the scan already holds, not a separate entity with its own lifecycle.
    """
    groups: dict[Path, list[MediaFile]] = {}
    for f in result.files:
        groups.setdefault(f.rel.parent, []).append(f)
    return sorted((folder, tuple(items)) for folder, items in groups.items())


def folder_rows(result: ScanResult, index: Counter[str]) -> list[Choice]:
    """One ``ui.table`` row per folder: the folder is the key, the numbers are packed
    into the value.

    ``ui.table`` is a two-column key/value renderer, not a grid, and this increment
    deliberately does not add a new UI seam for one screen. Column alignment inside the
    packed value is what keeps it readable; if it reads badly against a real tree, a
    proper multi-column seam is the next increment's problem.
    """
    rows: list[Choice] = []
    for folder, items in group_by_folder(result):
        name = f"{folder}/" if str(folder) != "." else "./"
        with_transcript = candidate_transcripts(items, index)
        rows.append(
            (
                name,
                f"{plural(len(items), 'file'):>9}  "
                f"{human_hours(sum(f.duration for f in items)):>9}  "
                f"{human_size(sum(f.size for f in items)):>10}  "
                f"{plural(with_transcript, 'transcript candidate')}",
            )
        )
    return rows


def totals_rows(result: ScanResult, model_config: ModelConfig, tier: ModelTier) -> list[Choice]:
    """The block under the folder table: what was found, and what summarizing it costs.

    The dollar figure is a row, not a column, because it is a property of the whole
    selection and because it is the number the operator is actually deciding on.
    """
    estimate = project_cost(result.files, model_config, tier)
    duplicate_groups = collisions(result.files)
    rows: list[Choice] = [
        ("Media files", f"{len(result.files):,}"),
        ("Total duration", human_hours(result.total_seconds)),
        ("Total size", human_size(result.total_bytes)),
        (
            f"Summaries (projected, '{tier.name}')",
            f"${estimate.total_usd:,.2f}",
        ),
        ("Duplicate name groups", f"{len(duplicate_groups):,}"),
        ("Unreadable files", f"{len(result.unreadable):,}"),
    ]
    if result.placeholders:
        rows.append(("Cloud placeholders (not probed)", f"{len(result.placeholders):,}"))
    return rows


def collision_rows(result: ScanResult) -> list[Choice]:
    """One row per duplicated name: the stem, then every file that claims it."""
    return [(stem, ", ".join(str(p) for p in paths)) for stem, paths in collisions(result.files)]


def unreadable_rows(result: ScanResult) -> list[Choice]:
    """One row per file ffmpeg could not read, with why. Shown relative to the scan root
    for the same reason as :func:`collision_rows`."""
    return [(str(_relative(path, result.root)), reason) for path, reason in result.unreadable]


def placeholder_rows(result: ScanResult) -> list[Choice]:
    """One row per cloud placeholder. These were never probed, on purpose."""
    return [
        (str(_relative(path, result.root)), "bytes are not on this machine")
        for path in result.placeholders
    ]
