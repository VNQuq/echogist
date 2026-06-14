"""Whisper model provisioning (TD-1, plan §5.4 / F14).

The model is fetched once from **Hugging Face** (``hf_repo``, the vanilla CT2
large-v3 stored float16), OR pre-placed in the local dir as a drop-in escape
hatch (``local_dir/model.bin`` present → no fetch, fully offline). The download
is one-time provisioning, not a pipeline stage, so the killswitch is unaffected.

The HF download is decoupled from the CUDA stack (no ctranslate2 import) so the
dedicated GPU preflight owns those diagnostics. The zip-from-URL helpers below
are the **dormant** self-host path, kept until the HF route is gate-verified on a
cold Windows run, then removed with the matching config fields (TD-1).
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

# CT2 model files to pull from the HF repo (mirrors faster-whisper's own set).
_HF_ALLOW_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)

Logger = Callable[[str], object]


class ProvisionError(Exception):
    """A recoverable provisioning failure. Print it, return to the launcher."""


def model_present(local_dir: Path) -> bool:
    """True if ``local_dir`` already holds a usable model (downloaded or pre-placed)."""
    return (local_dir / _SENTINEL).is_file()


# --------------------------------------------------------------------------- #
# DORMANT self-host path (zip-from-URL + checksum). Superseded by fetch_from_hf;
# kept until the HF route is gate-verified on a cold Windows run, then removed
# with [model_asset].source_url / sha256 (TD-1). Not wired into ensure_model.
# --------------------------------------------------------------------------- #
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


def fetch_from_hf(repo_id: str, local_dir: Path) -> None:
    """Download the CT2 model files for ``repo_id`` from Hugging Face into ``local_dir``.

    Uses ``huggingface_hub.snapshot_download`` (a runtime dep, imported lazily so the
    pre-placed/offline path needs neither it nor the CUDA stack). Any hub/network
    failure raises a recoverable ``ProvisionError`` pointing at the pre-place escape
    hatch (CLAUDE.md: fail loud, return to menu).
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ProvisionError(
            "huggingface_hub is not installed — run.bat installs it from "
            "requirements.lock. Or pre-place the model in the local dir."
        ) from exc

    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(local_dir),
            allow_patterns=list(_HF_ALLOW_PATTERNS),
        )
    except Exception as exc:  # any hub/network failure -> loud + recoverable
        raise ProvisionError(
            f"Hugging Face download of {repo_id} failed: {exc}. "
            f"Check your network, or pre-place the model in {local_dir} "
            f"({_SENTINEL} + config.json + tokenizer.json + vocabulary + "
            "preprocessor_config.json)."
        ) from exc


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
    """Guarantee the model exists locally, fetching it from HF if needed. Returns its dir."""
    local_dir = _resolve_local_dir(asset, app_root)
    if model_present(local_dir):
        log(f"Model present at {local_dir}; using it (no fetch).")
        return local_dir

    log(f"Model not found at {local_dir}; fetching {asset.hf_repo} from Hugging Face ...")
    fetch_from_hf(asset.hf_repo, local_dir)

    if not model_present(local_dir):
        raise ProvisionError(
            f"Hugging Face fetch of {asset.hf_repo} left no {local_dir / _SENTINEL}. "
            "Check the repo id, or pre-place the model in the local dir."
        )
    log(f"Model ready at {local_dir}.")
    return local_dir
