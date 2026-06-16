"""GPU shim tests — the win32-gated parts that are verifiable off Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

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


# Separator-free fake dirs so the assertions hold on Linux CI (where os.pathsep
# is ':'); the real Windows paths use ';' and drive-letter colons are fine there.
def test_prepend_to_path_adds_dirs_in_front(monkeypatch: pytest.MonkeyPatch) -> None:
    # ctranslate2's bare-name cuBLAS load honors PATH but not add_dll_directory,
    # so the shim must put the wheel dirs on PATH (TD-3).
    monkeypatch.setenv("PATH", f"existing{os.pathsep}more")
    cublas = Path("/venv/nvidia/cublas/bin")
    cudnn = Path("/venv/nvidia/cudnn/bin")
    gpu._prepend_to_path([cublas, cudnn])
    parts = os.environ["PATH"].split(os.pathsep)
    # Both wheel dirs land ahead of the pre-existing PATH entries (relative order
    # between the two doesn't matter — both must beat System32/PATH defaults).
    assert {str(cublas), str(cudnn)} == set(parts[:2])
    assert parts.index(str(cublas)) < parts.index("existing")
    assert parts.index(str(cudnn)) < parts.index("more")


def test_prepend_to_path_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "existing")
    cublas = Path("/venv/nvidia/cublas/bin")
    gpu._prepend_to_path([cublas])
    gpu._prepend_to_path([cublas])  # second call must not duplicate the entry
    assert os.environ["PATH"].split(os.pathsep).count(str(cublas)) == 1


def test_prepend_to_path_handles_empty_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cublas = Path("/venv/nvidia/cublas/bin")
    gpu._prepend_to_path([cublas])
    assert os.environ["PATH"] == str(cublas)


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
