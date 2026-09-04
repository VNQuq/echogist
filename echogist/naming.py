"""Shared artifact naming — Windows-safe stems + dated dedup (F9).

Every kept artifact is ``output/<kind>/<date>-<title>.<ext>``: the transcript
checkpoint (T3), the extracted mp3 (T4), and later the summary (T7). They all
share one naming rule — strip the Windows-illegal characters ``\\ / : * ? " < > |``
(spec §7), prefix the ISO date, and resolve a collision with a ``-2``/``-3``
suffix. That rule lives here, once, so a fix lands in every stage at the same time.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path

# Windows-illegal filename characters (spec §7). A stem usually comes from a real
# filename and is already legal, but a typed/odd source path — or an LLM-generated
# summary title — could carry one of these, so strip them before building the name.
_ILLEGAL_CHARS = '\\/:*?"<>|'
# ASCII control chars (0x00–0x1F) are also illegal in Windows filenames; an LLM
# title could in principle carry a stray tab/newline. Strip them like the above.
_CONTROL_CHARS = "".join(chr(c) for c in range(0x20))

# Reserved DOS device names. A file named any of these (case-insensitive, with or
# without an extension) is special on Windows and cannot be created normally. The
# dated artifacts (T3/T4) carry a date prefix so their stem is never bare, but the
# summary triplet (T6/T7) has NO date prefix — an LLM title of "CON" would crash
# the .json write AFTER the paid call, defeating the F13 never-re-pay guarantee.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_stem(stem: str, *, fallback: str) -> str:
    """Make ``stem`` a safe Windows filename base; fall back when nothing remains.

    Strips Windows-illegal punctuation and ASCII control chars, trims padding AND
    the dashes left by leading/trailing illegal chars (so ``"///"`` -> ``fallback``,
    not ``"----"``), and removes trailing dots/spaces — Windows silently drops
    those from filenames, which would otherwise desync the on-disk name from our
    dedup existence check. Finally, a reserved device name is prefixed with ``_``
    so it can be written at all.
    """
    cleaned = "".join("-" if ch in _ILLEGAL_CHARS or ch in _CONTROL_CHARS else ch for ch in stem)
    cleaned = cleaned.strip().strip("-").rstrip(". ").strip()
    if not cleaned:
        return fallback
    if cleaned.split(".", 1)[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


# Max length of a summary artifact stem (before extension / dedup suffix). The
# title comes from the model, so a runaway one would blow Windows' ~260-char
# MAX_PATH once the output dir + ".json"/".pdf"/".md" are appended. The .json
# (T6) and the .pdf/.md (T7) all derive their name from this, so capping here
# keeps the whole triplet inside the limit and grouped under one base.
_MAX_SUMMARY_STEM = 100


def summary_stem(title: str, *, fallback: str) -> str:
    """Windows-safe, length-capped stem for the summary triplet (.json/.pdf/.md).

    Sanitizes (F9 illegal-char strip) then truncates to ``_MAX_SUMMARY_STEM``.
    Prefers to cut on the last word boundary inside the cap (so the name stays
    readable) and re-strips trailing dashes/spaces so the cut never leaves a
    dangling separator. An empty/all-illegal title still yields ``fallback``.
    """
    stem = sanitize_stem(title, fallback=fallback)
    if len(stem) <= _MAX_SUMMARY_STEM:
        return stem
    cut = stem[:_MAX_SUMMARY_STEM]
    pivot = cut.rfind(" ")
    if pivot >= _MAX_SUMMARY_STEM // 2:  # break on a space only if it isn't too early
        cut = cut[:pivot]
    # Re-sanitize the cut: truncation can re-expose a trailing dot or land on a
    # reserved name, both of which sanitize_stem handles in one place.
    return sanitize_stem(cut, fallback=fallback)


def dedup_path(
    directory: Path, base: str, suffix: str, *, taken: AbstractSet[Path] = frozenset()
) -> Path:
    """``base+suffix`` if free, else ``base-2``, ``base-3`` ... (F9).

    ``taken`` holds names a caller has already handed out in this run but has not written
    yet. A batch resolves N names before any file exists, so on-disk existence alone would
    give two colliding stems the same path; an in-memory claim closes that without creating
    placeholder files. Note this cannot use the ``.part`` sibling as its marker: ``.part``
    is deliberately invisible here so a half-written file never occupies the artifact
    namespace, which is the exact opposite of what a reservation needs.
    """
    candidate = directory / f"{base}{suffix}"
    counter = 2
    while candidate.exists() or candidate in taken:
        candidate = directory / f"{base}-{counter}{suffix}"
        counter += 1
    return candidate


def dated_artifact_path(
    directory: Path,
    stem: str,
    suffix: str,
    *,
    fallback: str,
    today: date | None = None,
    taken: AbstractSet[Path] = frozenset(),
) -> Path:
    """Resolve ``directory/<date>-<sanitized-stem><suffix>``, deduped.

    Selects (does not create) the path — the writing stage owns creation, so the
    name is only reserved against files that already exist, plus any ``taken`` the
    caller has handed out but not yet written (the single-user TOCTOU is accepted,
    same as T3).
    """
    stamp = (today or date.today()).isoformat()
    base = f"{stamp}-{sanitize_stem(stem, fallback=fallback)}"
    return dedup_path(directory, base, suffix, taken=taken)


def resolve_source(path: Path) -> Path:
    """``path.resolve()``, or the path itself when it cannot be resolved (a broken
    junction) rather than aborting the caller.

    The identity of a SOURCE file, shared by every stage that has to decide whether two
    paths are the same recording: the scan's dedup key across a tree, and the summary's
    ``source_path`` back-link (TD-22). Both sides must agree character for character or
    the join silently misses and a paid summary is re-paid, so the rule lives here once.

    Deliberately NOT casefolded, unlike ``menu._resume_key``. There the key identifies one
    file the operator picked twice, and folding case only ever merges two spellings of the
    same thing. Here the value is a dedup key across a whole tree, and on a case-SENSITIVE
    filesystem ``Lecture.mp4`` and ``lecture.mp4`` are two different recordings: folding
    them would drop one from the scan silently and, worse, hand it the other's cached
    duration. The cost of not folding is the opposite and much cheaper — on Windows,
    where ``resolve`` does not normalize case, one file reachable under two spellings can
    probe twice. A redundant spawn is visible and harmless; a missing lecture is not.
    """
    try:
        return path.resolve()
    except OSError:
        return path
