"""Model provisioning tests (TD-1).

The active fetch path (Hugging Face) is exercised via a monkeypatched seam and an
injected fake ``huggingface_hub`` — no network, no GPU. The dormant self-host zip
helpers keep their own direct tests until they're removed with the config fields.
"""

from __future__ import annotations

import sys
import types
import zipfile
from pathlib import Path

import pytest

from echogist import model_asset
from echogist.config import ModelAsset
from echogist.model_asset import ProvisionError


def _make_model_zip(zip_path: Path, *, top: str | None = None) -> Path:
    with zipfile.ZipFile(zip_path, "w") as archive:
        prefix = f"{top}/" if top else ""
        archive.writestr(f"{prefix}model.bin", b"weights")
        archive.writestr(f"{prefix}config.json", b"{}")
    return zip_path


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


# --------------------------------------------------------------------------- #
# DORMANT self-host helpers — checksum / resumable download / zip extract
# --------------------------------------------------------------------------- #
def test_verify_checksum_ok(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    model_asset.verify_checksum(f, model_asset.sha256_file(f))


def test_verify_checksum_empty_skips(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    model_asset.verify_checksum(f, "")  # no raise


def test_verify_checksum_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    with pytest.raises(ProvisionError, match="Checksum mismatch"):
        model_asset.verify_checksum(f, "deadbeef")


def test_download_resumable_ok(tmp_path: Path) -> None:
    src = tmp_path / "src.zip"
    _make_model_zip(src)
    dest = tmp_path / "out" / "src.zip"
    model_asset.download_resumable(src.as_uri(), dest, expected_sha=model_asset.sha256_file(src))
    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_resumable_bad_checksum_clears_partial(tmp_path: Path) -> None:
    src = tmp_path / "src.zip"
    _make_model_zip(src)
    dest = tmp_path / "o.zip"
    with pytest.raises(ProvisionError, match="Checksum mismatch"):
        model_asset.download_resumable(src.as_uri(), dest, expected_sha="00")
    assert not dest.with_name(dest.name + ".part").exists()
    assert not dest.exists()


def test_download_unreachable_guides_to_fallback(tmp_path: Path) -> None:
    missing = (tmp_path / "nope.zip").as_uri()
    with pytest.raises(ProvisionError, match="mirror"):
        model_asset.download_resumable(missing, tmp_path / "o.zip")


def test_extract_zip_flattens_top_dir(tmp_path: Path) -> None:
    src = _make_model_zip(tmp_path / "asset.zip", top="large-v3-int8_float16")
    dest = tmp_path / "models" / "m"
    model_asset._extract_zip(src, dest)
    assert (dest / "model.bin").is_file()


def test_extract_zip_rejects_non_zip(tmp_path: Path) -> None:
    bogus = tmp_path / "asset.zip"
    bogus.write_text("<html>403 Forbidden</html>")
    with pytest.raises(ProvisionError, match="not a valid zip"):
        model_asset._extract_zip(bogus, tmp_path / "m")
