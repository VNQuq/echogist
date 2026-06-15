"""Shared artifact naming — Windows-safe stems + dated dedup (F9).

Every kept artifact is ``output/<kind>/<date>-<title>.<ext>``: the transcript
checkpoint (T3), the extracted mp3 (T4), and later the summary (T7). They all
share one naming rule — strip the Windows-illegal characters ``\\ / : * ? " < > |``
(spec §7), prefix the ISO date, and resolve a collision with a ``-2``/``-3``
suffix. That rule lives here, once, so a fix lands in every stage at the same time.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# Windows-illegal filename characters (spec §7). A stem usually comes from a real
# filename and is already legal, but a typed/odd source path could carry one of
# these, so strip them before building the artifact name.
_ILLEGAL_CHARS = '\\/:*?"<>|'


def sanitize_stem(stem: str, *, fallback: str) -> str:
    """Strip Windows-illegal chars; fall back when nothing legal remains.

    Trims padding AND the dashes left by leading/trailing illegal chars, so an
    all-illegal stem (``"///"``) returns ``fallback`` rather than ``"----"``.
    """
    cleaned = "".join("-" if ch in _ILLEGAL_CHARS else ch for ch in stem)
    cleaned = cleaned.strip().strip("-").strip()
    return cleaned or fallback


def dedup_path(directory: Path, base: str, suffix: str) -> Path:
    """``base+suffix`` if free, else ``base-2``, ``base-3`` ... (F9)."""
    candidate = directory / f"{base}{suffix}"
    counter = 2
    while candidate.exists():
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
) -> Path:
    """Resolve ``directory/<date>-<sanitized-stem><suffix>``, deduped.

    Selects (does not create) the path — the writing stage owns creation, so the
    name is only reserved against files that already exist (the single-user TOCTOU
    is accepted, same as T3).
    """
    stamp = (today or date.today()).isoformat()
    base = f"{stamp}-{sanitize_stem(stem, fallback=fallback)}"
    return dedup_path(directory, base, suffix)
