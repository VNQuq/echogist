"""Whisper model provisioning (TD-1, plan §5.4 / F14).

The model is fetched once from **Hugging Face** (``hf_repo``, the vanilla CT2
large-v3 stored float16), OR pre-placed in the local dir as a drop-in escape
hatch (``local_dir/model.bin`` present → no fetch, fully offline). The download
is one-time provisioning, not a pipeline stage, so the killswitch is unaffected.

The HF download is decoupled from the CUDA stack (no ctranslate2 import) so the
dedicated GPU preflight owns those diagnostics.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .config import ModelAsset

# faster-whisper / CT2 model dirs always contain this file — use it as the
# "model is present" sentinel for both downloaded and pre-placed dirs.
_SENTINEL = "model.bin"

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


def fetch_from_hf(repo_id: str, local_dir: Path) -> None:
    """Download the CT2 model files for ``repo_id`` from Hugging Face into ``local_dir``.

    Uses ``huggingface_hub.snapshot_download`` (a runtime dep, imported lazily so the
    pre-placed/offline path needs neither it nor the CUDA stack). Any hub/network
    failure raises a recoverable ``ProvisionError`` pointing at the pre-place escape
    hatch (CLAUDE.md: fail loud, return to menu).

    Xet is forced OFF first. huggingface_hub 1.x routes large-file transfers through
    the Xet CAS hosts (``*.xethub.hf.co``) when ``hf_xet`` is installed; those hosts
    stall on some routes while plain ``huggingface.co`` works (observed: the small
    files download, then ``model.bin`` hangs). ``HF_HUB_DISABLE_XET`` falls back to
    the classic LFS path, which is the path a browser download takes. Set BEFORE the
    import so huggingface_hub reads it with Xet already disabled. ``setdefault`` keeps
    it overridable: an operator on a Xet-reachable route can re-enable dedup with
    ``HF_HUB_DISABLE_XET=0``. Refs: huggingface/xet-core#446, huggingface_hub#3440.
    """
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
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
