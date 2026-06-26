"""T13 smoke-driver tests (plan §12.1 / §12.3).

Only the GPU-free core is unit-tested: the killswitch gate for the paid summary,
artifact verification, and clip resolution. The run itself
(:func:`smoke_run.run_smoke`) needs ffmpeg + the CUDA stack + the model + a clip
and is the operator's Windows / WSL run, not CI — the same split as the T11
measurement harness and the T10 eval suite.

``scripts/smoke_run.py`` is a standalone script (run directly on Windows), so it
is loaded here by file path rather than imported as a package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_run.py"
_spec = importlib.util.spec_from_file_location("smoke_run", _SCRIPT)
assert _spec is not None and _spec.loader is not None
smoke_run = importlib.util.module_from_spec(_spec)
# Register before exec so any @dataclass resolves its module via sys.modules (py3.12).
sys.modules["smoke_run"] = smoke_run
_spec.loader.exec_module(smoke_run)


# --------------------------------------------------------------------------- #
# live_summary_enabled — the killswitch gate
# --------------------------------------------------------------------------- #
def test_live_summary_skipped_without_flag() -> None:
    enabled, reason = smoke_run.live_summary_enabled({}, "sk-key")
    assert enabled is False
    assert "ECHOGIST_LIVE_SMOKE" in reason


def test_live_summary_skipped_with_flag_but_no_key() -> None:
    enabled, reason = smoke_run.live_summary_enabled({"ECHOGIST_LIVE_SMOKE": "1"}, None)
    assert enabled is False
    assert "no key, no call" in reason


def test_live_summary_enabled_with_flag_and_key() -> None:
    enabled, reason = smoke_run.live_summary_enabled({"ECHOGIST_LIVE_SMOKE": "1"}, "sk-key")
    assert enabled is True
    assert "enabled" in reason.lower()


def test_live_summary_blank_flag_is_off() -> None:
    # An empty/whitespace value must NOT enable the paid call.
    enabled, _ = smoke_run.live_summary_enabled({"ECHOGIST_LIVE_SMOKE": "  "}, "sk-key")
    assert enabled is False


# --------------------------------------------------------------------------- #
# missing_artifacts
# --------------------------------------------------------------------------- #
def test_missing_artifacts_all_present(tmp_path: Path) -> None:
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.txt"
    a.write_text("x")
    b.write_text("y")
    assert smoke_run.missing_artifacts([a, b]) == []


def test_missing_artifacts_reports_absent(tmp_path: Path) -> None:
    a = tmp_path / "a.mp3"
    a.write_text("x")
    gone = tmp_path / "gone.pdf"
    assert smoke_run.missing_artifacts([a, gone]) == [gone]


# --------------------------------------------------------------------------- #
# resolve_clip
# --------------------------------------------------------------------------- #
def test_resolve_clip_explicit_path(tmp_path: Path) -> None:
    clip = tmp_path / "my.mp4"
    clip.write_text("data")
    assert smoke_run.resolve_clip(str(clip), tmp_path) == clip


def test_resolve_clip_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(smoke_run.SmokeError, match="not found"):
        smoke_run.resolve_clip(str(tmp_path / "nope.mp4"), tmp_path)


def test_resolve_clip_prefers_smoke_over_ru(tmp_path: Path) -> None:
    (tmp_path / "ru.mp4").write_text("ru")
    (tmp_path / "smoke.mp4").write_text("smoke")
    assert smoke_run.resolve_clip(None, tmp_path).name == "smoke.mp4"


def test_resolve_clip_falls_back_to_ru(tmp_path: Path) -> None:
    (tmp_path / "ru.wav").write_text("ru")
    assert smoke_run.resolve_clip(None, tmp_path).name == "ru.wav"


def test_resolve_clip_none_found_raises_with_guidance(tmp_path: Path) -> None:
    with pytest.raises(smoke_run.SmokeError, match="No smoke clip"):
        smoke_run.resolve_clip(None, tmp_path)


# --------------------------------------------------------------------------- #
# model_dir — relative resolves under base, absolute passes through
# --------------------------------------------------------------------------- #
def _model_config(local_dir: str) -> object:
    from echogist import config

    return config.ModelConfig(
        tiers={},
        guard=config.GuardConfig(safe_budget_fraction=0.8, output_tokens_estimate=2000),
        summarize=config.SummarizeConfig(max_output_tokens=4000),
        asset=config.ModelAsset(name="m", hf_repo="r", local_dir=local_dir),
        transcript=config.TranscriptConfig(block_seconds=60.0),
    )


def test_model_dir_relative_under_base(tmp_path: Path) -> None:
    mc = _model_config("models/whisper")
    assert smoke_run.model_dir(mc, tmp_path) == tmp_path / "models" / "whisper"


def test_model_dir_absolute_passes_through(tmp_path: Path) -> None:
    abs_dir = (tmp_path / "abs").resolve()
    mc = _model_config(str(abs_dir))
    assert smoke_run.model_dir(mc, tmp_path) == abs_dir
