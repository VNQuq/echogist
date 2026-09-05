"""First-run provisioning orchestrator (plan §5, TD-3).

Invoked by ``run.bat`` as ``python -m echogist.provision`` after the host steps
(Python check, venv, hash-pinned deps). Runs the Python-side, idempotent steps:
create output dirs, ensure the Whisper model (TD-1), and GPU preflight (F8).

Each failure prints a human-readable diagnostic and returns a distinct exit code
so the launcher can stop cleanly (CLAUDE.md: fail loud, never a cryptic crash).
The API key is intentionally NOT checked here — MP3-only extraction and the local
GUARD must work with no key; the key is validated only before a summarize action.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from . import gpu, model_asset
from .config import ConfigError, load_model_config

# ``summaries/raw`` holds the F13 recovery .json out of the operator's eye-line;
# the readable .pdf/.md stay directly under ``summaries``.
OUTPUT_SUBDIRS: tuple[str, ...] = (
    "audio",
    "transcripts",
    "summaries",
    "summaries/raw",
    "logs",  # per-launch session transcripts (ui.RunLog)
)

Logger = Callable[[str], object]

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_MODEL = 3
EXIT_GPU = 4


def app_root() -> Path:
    """Repo/app root (the dir that holds ``config/``, ``output/``, ``run.bat``)."""
    return Path(__file__).resolve().parent.parent


def ensure_output_dirs(base: Path) -> list[Path]:
    """Create every directory in ``OUTPUT_SUBDIRS`` — the recovery artifacts plus ``logs``."""
    dirs = [base / "output" / sub for sub in OUTPUT_SUBDIRS]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def provision(base: Path | None = None, *, log: Logger = print) -> int:
    """Run all provisioning steps. Returns an exit code (0 = ready)."""
    base = base or app_root()

    try:
        model_config = load_model_config()
    except ConfigError as exc:
        log(f"Config error: {exc}")
        return EXIT_CONFIG

    ensure_output_dirs(base)
    log("Output dirs ready.")

    try:
        model_asset.ensure_model(model_config.asset, base, log=log)
    except model_asset.ProvisionError as exc:
        log(f"Model provisioning failed: {exc}")
        return EXIT_MODEL

    ok, message = gpu.preflight()
    log(message)
    if not ok:
        return EXIT_GPU

    log("Provisioning complete.")
    return EXIT_OK


def main() -> int:
    return provision()


if __name__ == "__main__":
    sys.exit(main())
