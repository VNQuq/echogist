"""GPU provisioning — the one OS-specific shim (plan §12.1, TD-3).

Two responsibilities, both kept import-light so the pure stages and CI import
this module without a GPU or the heavy wheels present:

* :func:`register_cuda_libraries` — make the bundled cuDNN/cuBLAS DLLs loadable
  **before** ``import faster_whisper``. Windows-only; on WSL/Linux the linker
  finds the ``nvidia-*-cu12`` wheels via RPATH/``LD_LIBRARY_PATH``, so it is a
  no-op there. This is the single ``win32`` platform shim — every other stage
  imports identically across WSL and Windows.
* :func:`preflight` — load the GPU backend and probe for a CUDA device, turning
  the usual cryptic DLL/driver crash into a specific, actionable diagnostic (F8).

``ctranslate2`` is imported lazily inside :func:`preflight` so this module stays
importable with no GPU stack installed.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

# The bundled NVIDIA wheels lay their libraries under
# site-packages/nvidia/<lib>/bin (Windows DLLs) or .../lib (Linux .so).
_NVIDIA_LIBS: tuple[str, ...] = ("cudnn", "cublas")


def _site_packages() -> Path:
    return Path(sysconfig.get_paths()["purelib"])


def _nvidia_lib_dirs(site_packages: Path, subdir: str) -> list[Path]:
    """Existing ``nvidia/<lib>/<subdir>`` dirs under ``site_packages``."""
    found: list[Path] = []
    for lib in _NVIDIA_LIBS:
        candidate = site_packages / "nvidia" / lib / subdir
        if candidate.is_dir():
            found.append(candidate)
    return found


def register_cuda_libraries(site_packages: Path | None = None) -> list[Path]:
    """Register the bundled cuDNN/cuBLAS DLL dirs so faster-whisper can load them.

    MUST be called before importing ``faster_whisper`` on Windows. Returns the
    dirs registered — empty off Windows (the linker handles it on WSL/Linux) or
    when the wheels are absent.
    """
    if sys.platform != "win32":
        return []
    base = site_packages or _site_packages()
    registered: list[Path] = []
    for lib_dir in _nvidia_lib_dirs(base, "bin"):
        os.add_dll_directory(str(lib_dir))
        registered.append(lib_dir)
    return registered


def diagnose_import_error(exc: BaseException) -> str:
    """Map a CUDA backend load failure to a specific, copy-pasteable message (F8).

    Version skew between ctranslate2 and the cuDNN/cuBLAS wheels is the #1 silent
    first-run failure (TD-3, outside-voice #4) — name it instead of surfacing a
    raw DLL crash.
    """
    text = str(exc).lower()
    if "cudnn" in text:
        return (
            "cuDNN failed to load. The nvidia-cudnn-cu12 wheel is missing or its "
            "major version does not match the ctranslate2 build. Re-run run.bat to "
            "reinstall the pinned dependency set; never 'pip install -U ctranslate2' "
            "on its own (that is the version-skew trap)."
        )
    if "cublas" in text:
        return (
            "cuBLAS failed to load. nvidia-cublas-cu12 is missing or mismatched. "
            "Re-run run.bat to reinstall the pinned dependency set."
        )
    if "driver" in text:
        return (
            "The NVIDIA driver looks too old or missing. Update your GPU driver "
            "(a manual prerequisite EchoGist does not auto-install), then re-run."
        )
    return f"GPU preflight failed: {exc}"


def preflight() -> tuple[bool, str]:
    """Register DLLs, load the CUDA backend, and confirm a GPU is visible.

    Returns ``(ok, message)``. A real transcription op is left to T3 / the manual
    F8 test; this is the fast, model-free gate that catches a broken GPU stack
    before the user picks a file.
    """
    try:
        register_cuda_libraries()
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return (
                False,
                "No CUDA GPU detected. EchoGist transcription needs an NVIDIA GPU. "
                "Check `nvidia-smi` and your driver, then re-run.",
            )
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly diagnostic
        return (False, diagnose_import_error(exc))
    return (True, "GPU preflight OK.")
