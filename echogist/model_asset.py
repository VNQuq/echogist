"""Whisper model provisioning (TD-1, plan §5.4 / F14).

The model is fetched once from a **configurable** source URL with resume +
checksum verify, OR pre-placed in the local dir as a drop-in escape hatch. A
GitHub block from RU therefore has an automated fallback (point the config at a
mirror, or drop the files in) with no per-run manual step.

Stdlib-only (``urllib``) so provisioning does not depend on the runtime wheels
and stays offline except for this one-time fetch.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .config import ModelAsset

# faster-whisper / CT2 model dirs always contain this file — use it as the
# "model is present" sentinel for both downloaded and pre-placed dirs.
_SENTINEL = "model.bin"
_CHUNK = 1 << 20

Logger = Callable[[str], object]


class ProvisionError(Exception):
    """A recoverable provisioning failure. Print it, return to the launcher."""


def model_present(local_dir: Path) -> bool:
    """True if ``local_dir`` already holds a usable model (downloaded or pre-placed)."""
    return (local_dir / _SENTINEL).is_file()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum(path: Path, expected: str) -> None:
    """Raise if ``path``'s sha256 != ``expected``. Empty ``expected`` skips (caller warns)."""
    if not expected:
        return
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise ProvisionError(
            f"Checksum mismatch for {path.name}: expected {expected}, got {actual}. "
            "The download is corrupt or the configured sha256 is stale."
        )


def download_resumable(url: str, dest: Path, *, expected_sha: str = "") -> Path:
    """Download ``url`` to ``dest`` with HTTP-range resume + sha256 verify.

    A partial download lands in ``<dest>.part``; an interrupted run resumes from
    it. If the server ignores the Range request, the partial is discarded and the
    transfer restarts cleanly. On any network error, the message points at the
    F14 fallbacks (mirror / pre-placed dir).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.is_file() else 0

    request = urllib.request.Request(url)
    if existing:
        request.add_header("Range", f"bytes={existing}-")

    try:
        with urllib.request.urlopen(request) as response:
            resumed = bool(existing) and getattr(response, "status", None) == 206
            mode = "ab" if resumed else "wb"
            with part.open(mode) as out:
                while True:
                    block = response.read(_CHUNK)
                    if not block:
                        break
                    out.write(block)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ProvisionError(
            f"Model download failed from {url}: {exc}. "
            "Point [model_asset].source_url at a mirror you host, or drop the model "
            "files into the local model dir (both are supported, no per-run step)."
        ) from exc

    try:
        verify_checksum(part, expected_sha)
    except ProvisionError:
        # Don't let a corrupt partial wedge the next run's resume — start clean (F7).
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    return dest


def _resolve_local_dir(asset: ModelAsset, app_root: Path) -> Path:
    local = Path(asset.local_dir)
    return local if local.is_absolute() else (app_root / local)


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ProvisionError(
            f"Downloaded asset {zip_path.name} is not a valid zip "
            "(the source URL may have returned an error page instead of the model). "
            "Check [model_asset].source_url, or pre-place the model in the local dir."
        ) from exc
    # Flatten a single wrapping top-level dir (common in release zips).
    if not model_present(dest):
        subdirs = [p for p in dest.iterdir() if p.is_dir()]
        if len(subdirs) == 1 and model_present(subdirs[0]):
            for item in list(subdirs[0].iterdir()):
                item.rename(dest / item.name)
            subdirs[0].rmdir()


def ensure_model(asset: ModelAsset, app_root: Path, *, log: Logger = print) -> Path:
    """Guarantee the model exists locally, fetching it if needed. Returns its dir."""
    local_dir = _resolve_local_dir(asset, app_root)
    if model_present(local_dir):
        log(f"Model present at {local_dir}.")
        return local_dir

    log(f"Model not found at {local_dir}; fetching from {asset.source_url} ...")
    if not asset.sha256:
        log(
            "WARNING: no sha256 in [model_asset]; skipping integrity verify "
            "(set it before release)."
        )

    cache = app_root / ".cache" / Path(asset.source_url).name
    download_resumable(asset.source_url, cache, expected_sha=asset.sha256)
    log(f"Extracting {cache.name} -> {local_dir} ...")
    _extract_zip(cache, local_dir)

    if not model_present(local_dir):
        raise ProvisionError(
            f"Extraction did not produce {local_dir / _SENTINEL}. "
            "Check the asset contents at [model_asset].source_url."
        )
    log(f"Model ready at {local_dir}.")
    return local_dir
