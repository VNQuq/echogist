"""Model provisioning tests (TD-1).

The fetch path (Hugging Face) is exercised via a monkeypatched seam and an
injected fake ``huggingface_hub`` — no network, no GPU.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from echogist import model_asset
from echogist.config import ModelAsset
from echogist.model_asset import ProvisionError

_REPO = "Systran/faster-whisper-large-v3"


def _asset(*, hf_repo: str = _REPO, local_dir: str = "models/m") -> ModelAsset:
    return ModelAsset(name="m", hf_repo=hf_repo, local_dir=local_dir)


# --------------------------------------------------------------------------- #
# presence
# --------------------------------------------------------------------------- #
def test_model_present(tmp_path: Path) -> None:
    assert not model_asset.model_present(tmp_path)
    (tmp_path / "model.bin").write_bytes(b"x")
    assert model_asset.model_present(tmp_path)


# --------------------------------------------------------------------------- #
# ensure_model — HF fetch via the monkeypatched seam
# --------------------------------------------------------------------------- #
def test_ensure_model_fetches_from_hf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(repo_id: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "model.bin").write_bytes(b"weights")

    monkeypatch.setattr(model_asset, "fetch_from_hf", fake_fetch)
    out = model_asset.ensure_model(_asset(), tmp_path, log=lambda *_: None)
    assert out == tmp_path / "models" / "m"
    assert (out / "model.bin").is_file()


def test_ensure_model_preplaced_skips_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "models" / "m"
    local.mkdir(parents=True)
    (local / "model.bin").write_bytes(b"pre-placed")

    def boom(repo_id: str, local_dir: Path) -> None:
        raise AssertionError("fetch_from_hf must not be called when the model is pre-placed")

    monkeypatch.setattr(model_asset, "fetch_from_hf", boom)
    out = model_asset.ensure_model(_asset(), tmp_path, log=lambda *_: None)
    assert out == local
    assert (local / "model.bin").read_bytes() == b"pre-placed"


def test_ensure_model_absolute_local_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "abs" / "model_dir"

    def fake_fetch(repo_id: str, local_dir: Path) -> None:
        assert local_dir == target
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "model.bin").write_bytes(b"w")

    monkeypatch.setattr(model_asset, "fetch_from_hf", fake_fetch)
    out = model_asset.ensure_model(_asset(local_dir=str(target)), tmp_path, log=lambda *_: None)
    assert out == target
    assert (target / "model.bin").is_file()


def test_ensure_model_fetch_left_no_model_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fetch "succeeds" but produces no model.bin -> loud ProvisionError, not a crash.
    monkeypatch.setattr(model_asset, "fetch_from_hf", lambda repo_id, local_dir: None)
    with pytest.raises(ProvisionError, match="left no"):
        model_asset.ensure_model(_asset(), tmp_path, log=lambda *_: None)


# --------------------------------------------------------------------------- #
# fetch_from_hf — with an injected fake huggingface_hub (no network)
# --------------------------------------------------------------------------- #
def _inject_hf(monkeypatch: pytest.MonkeyPatch, snapshot_download: object) -> None:
    fake = types.ModuleType("huggingface_hub")
    fake.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)


def test_fetch_from_hf_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_snapshot(*, repo_id: str, local_dir: str, allow_patterns: list[str]) -> str:
        seen["repo_id"] = repo_id
        seen["allow_patterns"] = allow_patterns
        (Path(local_dir) / "model.bin").write_bytes(b"weights")
        return local_dir

    _inject_hf(monkeypatch, fake_snapshot)
    target = tmp_path / "models" / "m"
    model_asset.fetch_from_hf("Systran/faster-whisper-large-v3", target)
    assert (target / "model.bin").is_file()
    assert seen["repo_id"] == "Systran/faster-whisper-large-v3"
    assert "model.bin" in seen["allow_patterns"]  # type: ignore[operator]


def test_fetch_from_hf_disables_xet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The Xet transport stalls on some routes; fetch must force the classic LFS path
    # by setting HF_HUB_DISABLE_XET before huggingface_hub is imported (xet-core#446).
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    seen: dict[str, str | None] = {}

    def fake_snapshot(*, repo_id: str, local_dir: str, allow_patterns: list[str]) -> str:
        # Captured at call time: proves the flag is set by the time the download runs.
        seen["flag"] = os.environ.get("HF_HUB_DISABLE_XET")
        (Path(local_dir) / "model.bin").write_bytes(b"w")
        return local_dir

    _inject_hf(monkeypatch, fake_snapshot)
    model_asset.fetch_from_hf(_REPO, tmp_path / "m")
    assert seen["flag"] == "1"


def test_fetch_from_hf_respects_explicit_xet_optin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator on a Xet-reachable route can re-enable it; setdefault must not clobber.
    monkeypatch.setenv("HF_HUB_DISABLE_XET", "0")
    seen: dict[str, str | None] = {}

    def fake_snapshot(*, repo_id: str, local_dir: str, allow_patterns: list[str]) -> str:
        seen["flag"] = os.environ.get("HF_HUB_DISABLE_XET")
        (Path(local_dir) / "model.bin").write_bytes(b"w")
        return local_dir

    _inject_hf(monkeypatch, fake_snapshot)
    model_asset.fetch_from_hf(_REPO, tmp_path / "m")
    assert seen["flag"] == "0"


def test_fetch_from_hf_download_error_guides_to_preplace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(**_: object) -> str:
        raise OSError("connection reset")

    _inject_hf(monkeypatch, fake_snapshot)
    with pytest.raises(ProvisionError, match="pre-place"):
        model_asset.fetch_from_hf("Systran/faster-whisper-large-v3", tmp_path / "m")


def test_fetch_from_hf_missing_dep_guides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # huggingface_hub absent -> loud ProvisionError naming the dep, not ImportError.
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(ProvisionError, match="huggingface_hub is not installed"):
        model_asset.fetch_from_hf("Systran/faster-whisper-large-v3", tmp_path / "m")
