"""GPU provisioning — the one OS-specific shim (plan §12.1, TD-3).

Two responsibilities, both kept import-light so the pure stages and CI import
this module without a GPU or the heavy wheels present:

* :func:`register_cuda_libraries` — make the bundled cuDNN/cuBLAS DLLs loadable
  **before** ``import faster_whisper``. Windows-only; on WSL/Linux it is a no-op
  because ``ctranslate2`` dlopens the ``nvidia-*-cu12`` ``.so`` files via the
  dynamic linker — which means the wheel lib dirs
  (``site-packages/nvidia/{cublas,cudnn}/lib``) must be on ``LD_LIBRARY_PATH``
  at process start (the dev loop / measurement harness export them; ld.so reads
  the var only at launch, so a runtime ``os.environ`` set would be too late).
  This is the single ``win32`` platform shim — every other stage imports
  identically across WSL and Windows.
* :func:`preflight` — load the GPU backend and probe for a CUDA device, turning
  the usual cryptic DLL/driver crash into a specific, actionable diagnostic (F8).

``ctranslate2`` is imported lazily inside :func:`preflight` so this module stays
importable with no GPU stack installed.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from collections.abc import Iterable
from pathlib import Path

# The bundled NVIDIA wheels lay their libraries under
# site-packages/nvidia/<lib>/bin (Windows DLLs) or .../lib (Linux .so).
_NVIDIA_LIBS: tuple[str, ...] = ("cudnn", "cublas")

# The "entry" DLLs ctranslate2 lazily loads by bare name at the first GPU op.
# We pin them resident by absolute path (see register_cuda_libraries) so the
# later by-name load returns the already-loaded module. The glob deliberately
# matches the version-suffixed entry point only (cublas64_12.dll, cudnn64_9.dll)
# and NOT its same-dir helpers (cublasLt64_12.dll, cudnn_graph64_9.dll, ...),
# which Windows resolves from the entry DLL's own directory.
_ENTRY_DLL_GLOBS: tuple[str, ...] = ("cublas64_*.dll", "cudnn64_*.dll")


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


def _prepend_to_path(lib_dirs: Iterable[Path]) -> None:
    """Prepend each dir to ``PATH`` (idempotent), in front of any existing entry.

    ``os.add_dll_directory`` alone is not enough on Windows: ctranslate2 lazily
    loads cuBLAS/cuDNN by bare name at the first GPU op, and that search honors
    ``PATH`` but not the directories added via ``add_dll_directory`` (TD-3). Pure
    over ``os.environ`` so it is unit-testable off Windows.
    """
    for lib_dir in lib_dirs:
        entry = str(lib_dir)
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        if entry not in parts:
            os.environ["PATH"] = entry + os.pathsep + current if current else entry


def _preload_entry_dlls(lib_dirs: Iterable[Path]) -> list[str]:
    """Pin the cuBLAS/cuDNN entry DLLs resident by absolute path (Windows only).

    Once a module is loaded by absolute path, a later bare-name ``LoadLibrary``
    from ctranslate2 returns the already-loaded handle regardless of its search
    order — the deterministic belt to the PATH suspenders. Best-effort: a DLL
    that fails to preload here surfaces a friendly diagnostic at transcribe time
    via :func:`diagnose_import_error`, never a crash. Returns the names pinned.
    """
    import ctypes

    pinned: list[str] = []
    for lib_dir in lib_dirs:
        for pattern in _ENTRY_DLL_GLOBS:
            for dll in sorted(lib_dir.glob(pattern)):
                try:
                    ctypes.WinDLL(str(dll))  # type: ignore[attr-defined]  # win32-only
                    pinned.append(dll.name)
                except OSError:
                    pass
    return pinned


def register_cuda_libraries(site_packages: Path | None = None) -> list[Path]:
    """Register the bundled cuDNN/cuBLAS DLL dirs so faster-whisper can load them.

    MUST be called before importing ``faster_whisper`` on Windows. Three layers,
    because ctranslate2 lazily loads cuBLAS/cuDNN by bare name at the first GPU
    op and that search ignores ``add_dll_directory`` (TD-3): register the dirs,
    prepend them to ``PATH``, and pin the entry DLLs resident by absolute path.
    Returns the dirs registered — empty off Windows (the linker handles it on
    WSL/Linux) or when the wheels are absent.
    """
    if sys.platform != "win32":
        return []
    base = site_packages or _site_packages()
    registered: list[Path] = []
    for lib_dir in _nvidia_lib_dirs(base, "bin"):
        os.add_dll_directory(str(lib_dir))
        registered.append(lib_dir)
    _prepend_to_path(registered)
    _preload_entry_dlls(registered)
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
