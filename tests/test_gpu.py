"""GPU shim tests — the win32-gated parts that are verifiable off Windows."""

from __future__ import annotations

import sys
from pathlib import Path

from echogist import gpu


def test_register_is_noop_off_windows() -> None:
    # Dev/CI run on WSL/Linux; the linker handles the wheels, so register no-ops.
    if sys.platform == "win32":
        return
    assert gpu.register_cuda_libraries() == []


def test_nvidia_lib_dirs_discovers_present_dirs(tmp_path: Path) -> None:
    (tmp_path / "nvidia" / "cudnn" / "lib").mkdir(parents=True)
    (tmp_path / "nvidia" / "cublas" / "lib").mkdir(parents=True)
    found = gpu._nvidia_lib_dirs(tmp_path, "lib")
    assert tmp_path / "nvidia" / "cudnn" / "lib" in found
    assert tmp_path / "nvidia" / "cublas" / "lib" in found


def test_nvidia_lib_dirs_skips_absent(tmp_path: Path) -> None:
    (tmp_path / "nvidia" / "cudnn" / "lib").mkdir(parents=True)
    found = gpu._nvidia_lib_dirs(tmp_path, "lib")
    assert found == [tmp_path / "nvidia" / "cudnn" / "lib"]


def test_diagnose_cudnn() -> None:
    msg = gpu.diagnose_import_error(RuntimeError("could not load libcudnn_ops.so.9"))
    assert "cuDNN" in msg
    assert "ctranslate2" in msg


def test_diagnose_cublas() -> None:
    msg = gpu.diagnose_import_error(RuntimeError("libcublas.so missing"))
    assert "cuBLAS" in msg


def test_diagnose_driver() -> None:
    msg = gpu.diagnose_import_error(RuntimeError("CUDA driver version is insufficient"))
    assert "driver" in msg.lower()


def test_diagnose_fallback_includes_original() -> None:
    msg = gpu.diagnose_import_error(RuntimeError("totally unexpected boom"))
    assert "totally unexpected boom" in msg
