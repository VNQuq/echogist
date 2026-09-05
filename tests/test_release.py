"""Guards on the release script. It is run once per version, by hand, with a live PAT —
so the failure mode that matters is the one a retry cannot undo."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "echogist_release", Path(__file__).resolve().parent.parent / "scripts" / "release.py"
)
assert _SPEC and _SPEC.loader
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)


def _ls_remote(stdout: str) -> Any:
    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["git", "ls-remote", "--tags"]
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def test_a_tag_that_is_not_on_origin_stops_the_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub CREATES a missing tag at the default branch head.

    So a typo publishes a real release pointing at an invented tag, and the
    422-is-success path makes the retry look idempotent. The push has to have happened.
    """
    monkeypatch.setattr(subprocess, "run", _ls_remote(""))

    with pytest.raises(release.ReleaseError) as exc:
        release.require_pushed_tag("v2.4.O")

    assert "not on origin" in str(exc.value)


def test_a_pushed_tag_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _ls_remote("abc123\trefs/tags/v2.4.0\n"))

    release.require_pushed_tag("v2.4.0")  # does not raise
