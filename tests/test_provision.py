"""Provisioning orchestrator tests — output dirs + step sequencing with stubs."""

from __future__ import annotations

from pathlib import Path

import pytest

from echogist import gpu, model_asset, provision


def test_ensure_output_dirs(tmp_path: Path) -> None:
    dirs = provision.ensure_output_dirs(tmp_path)
    output = tmp_path / "output"
    assert {d.relative_to(output).as_posix() for d in dirs} == {
        "audio",
        "transcripts",
        "summaries",
        "summaries/raw",
    }
    for sub in ("audio", "transcripts", "summaries", "summaries/raw"):
        assert (output / sub).is_dir()


def test_ensure_output_dirs_idempotent(tmp_path: Path) -> None:
    provision.ensure_output_dirs(tmp_path)
    provision.ensure_output_dirs(tmp_path)  # no raise on existing dirs
    assert (tmp_path / "output" / "audio").is_dir()


def test_provision_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_asset, "ensure_model", lambda asset, base, log=print: base / "models")
    monkeypatch.setattr(gpu, "preflight", lambda: (True, "GPU OK"))
    assert provision.provision(base=tmp_path, log=lambda *_: None) == provision.EXIT_OK
    assert (tmp_path / "output" / "transcripts").is_dir()


def test_provision_model_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(asset: object, base: object, log: object = print) -> Path:
        raise model_asset.ProvisionError("download blocked")

    monkeypatch.setattr(model_asset, "ensure_model", boom)
    monkeypatch.setattr(gpu, "preflight", lambda: (True, "GPU OK"))
    assert provision.provision(base=tmp_path, log=lambda *_: None) == provision.EXIT_MODEL


def test_provision_gpu_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_asset, "ensure_model", lambda asset, base, log=print: base / "models")
    monkeypatch.setattr(gpu, "preflight", lambda: (False, "no gpu"))
    assert provision.provision(base=tmp_path, log=lambda *_: None) == provision.EXIT_GPU


def test_provision_config_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point config dir at an empty dir so load_model_config raises ConfigError.
    monkeypatch.setenv("ECHOGIST_CONFIG_DIR", str(tmp_path / "empty"))
    assert provision.provision(base=tmp_path, log=lambda *_: None) == provision.EXIT_CONFIG


def test_gpu_preflight_signature() -> None:
    # preflight must be import-safe with no GPU stack; it returns (bool, str).
    ok, message = gpu.preflight()
    assert isinstance(ok, bool)
    assert isinstance(message, str)
