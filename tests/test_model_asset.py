"""Model provisioning tests (TD-1). Downloads run via file:// URLs — no network."""

from __future__ import annotations

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


def _asset(source: Path, *, sha256: str = "", local_dir: str = "models/m") -> ModelAsset:
    return ModelAsset(name="m", source_url=source.as_uri(), sha256=sha256, local_dir=local_dir)


# --------------------------------------------------------------------------- #
# presence + checksum
# --------------------------------------------------------------------------- #
def test_model_present(tmp_path: Path) -> None:
    assert not model_asset.model_present(tmp_path)
    (tmp_path / "model.bin").write_bytes(b"x")
    assert model_asset.model_present(tmp_path)


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


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #
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
    # The corrupt partial must be removed so the next run resumes clean (F7).
    assert not dest.with_name(dest.name + ".part").exists()
    assert not dest.exists()


def test_download_unreachable_guides_to_fallback(tmp_path: Path) -> None:
    missing = (tmp_path / "nope.zip").as_uri()
    with pytest.raises(ProvisionError, match="mirror"):
        model_asset.download_resumable(missing, tmp_path / "o.zip")


# --------------------------------------------------------------------------- #
# ensure_model
# --------------------------------------------------------------------------- #
def test_ensure_model_downloads_and_extracts(tmp_path: Path) -> None:
    src = _make_model_zip(tmp_path / "asset.zip")
    out = model_asset.ensure_model(_asset(src), tmp_path, log=lambda *_: None)
    assert out == tmp_path / "models" / "m"
    assert (out / "model.bin").is_file()


def test_ensure_model_flattens_top_dir(tmp_path: Path) -> None:
    src = _make_model_zip(tmp_path / "asset.zip", top="large-v3-int8_float16")
    out = model_asset.ensure_model(_asset(src), tmp_path, log=lambda *_: None)
    assert (out / "model.bin").is_file()


def test_ensure_model_preplaced_skips_download(tmp_path: Path) -> None:
    # Pre-place the model; point source at a non-existent URL to prove no fetch.
    local = tmp_path / "models" / "m"
    local.mkdir(parents=True)
    (local / "model.bin").write_bytes(b"pre-placed")
    asset = ModelAsset(
        name="m", source_url="file:///does/not/exist.zip", sha256="", local_dir="models/m"
    )
    out = model_asset.ensure_model(asset, tmp_path, log=lambda *_: None)
    assert out == local
    assert (local / "model.bin").read_bytes() == b"pre-placed"


def test_ensure_model_rejects_non_zip_asset(tmp_path: Path) -> None:
    # Source returns an HTML error page (not a zip) -> loud ProvisionError, not a crash.
    bogus = tmp_path / "asset.zip"
    bogus.write_text("<html>403 Forbidden</html>")
    with pytest.raises(ProvisionError, match="not a valid zip"):
        model_asset.ensure_model(_asset(bogus), tmp_path, log=lambda *_: None)


def test_ensure_model_absolute_local_dir(tmp_path: Path) -> None:
    src = _make_model_zip(tmp_path / "asset.zip")
    target = tmp_path / "abs" / "model_dir"
    asset = _asset(src, local_dir=str(target))
    out = model_asset.ensure_model(asset, tmp_path, log=lambda *_: None)
    assert out == target
    assert (target / "model.bin").is_file()
