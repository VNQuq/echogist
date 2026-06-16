"""T11 measurement-harness tests (TD-4, plan §12.3).

Only the GPU-free core is unit-tested: realtime_factor math, the speed verdict
(including the bar-not-set first run), and the markdown report. The GPU path
(:func:`measure_model.run` / :func:`measure_model.measure_clip`) needs the CUDA
stack + the model + the 4060 and is the operator's Windows run, not CI — the same
split as the T10 eval suite.

``scripts/measure_model.py`` is a standalone script (run directly on Windows), so
it is loaded here by file path rather than imported as a package.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_model.py"
_spec = importlib.util.spec_from_file_location("measure_model", _SCRIPT)
assert _spec is not None and _spec.loader is not None
measure_model = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its module via sys.modules (py3.12).
sys.modules["measure_model"] = measure_model
_spec.loader.exec_module(measure_model)

ClipResult = measure_model.ClipResult


def _clip(compute_type: str, label: str, audio: float, wall: float, **kw: object) -> object:
    defaults: dict[str, object] = {
        "peak_vram_mb": 2048.0,
        "language": label.lower(),
        "transcript": f"{label} text",
    }
    defaults.update(kw)
    return ClipResult(
        compute_type=compute_type, label=label, audio_sec=audio, wall_sec=wall, **defaults
    )


# --------------------------------------------------------------------------- #
# realtime_factor
# --------------------------------------------------------------------------- #
def test_realtime_factor_basic() -> None:
    # 300s of audio in 60s wall = 5x realtime.
    assert measure_model.realtime_factor(300.0, 60.0) == pytest.approx(5.0)


def test_realtime_factor_below_one_is_slower_than_realtime() -> None:
    assert measure_model.realtime_factor(60.0, 120.0) == pytest.approx(0.5)


def test_realtime_factor_zero_wall_guards_to_zero() -> None:
    # No divide-by-zero on a degenerate measurement.
    assert measure_model.realtime_factor(300.0, 0.0) == 0.0
    assert measure_model.realtime_factor(300.0, -1.0) == 0.0


# --------------------------------------------------------------------------- #
# speed_verdict — the bar is optional (operator sets it after the run)
# --------------------------------------------------------------------------- #
def test_speed_verdict_no_bar_is_informational_not_failure() -> None:
    passed, line = measure_model.speed_verdict(4.2, None)
    assert passed is None  # not a fail — the operator hasn't set the bar yet
    assert "4.20x" in line
    assert "--bar" in line


def test_speed_verdict_pass() -> None:
    passed, line = measure_model.speed_verdict(2.0, 1.5)
    assert passed is True
    assert line.startswith("PASS")
    assert "2.00x" in line and "1.50x" in line


def test_speed_verdict_fail() -> None:
    passed, line = measure_model.speed_verdict(1.2, 1.5)
    assert passed is False
    assert line.startswith("FAIL")


def test_speed_verdict_exactly_at_bar_passes() -> None:
    passed, _line = measure_model.speed_verdict(1.5, 1.5)
    assert passed is True


# --------------------------------------------------------------------------- #
# report_filename
# --------------------------------------------------------------------------- #
def test_report_filename_slugs_and_dates() -> None:
    name = measure_model.report_filename("large-v3", date(2026, 6, 16))
    assert name == "large-v3-2026-06-16.md"


def test_report_filename_sanitizes_illegal_chars() -> None:
    name = measure_model.report_filename("Systran/faster-whisper:large", date(2026, 6, 16))
    assert "/" not in name and ":" not in name
    assert name.endswith("-2026-06-16.md")


# --------------------------------------------------------------------------- #
# render_report
# --------------------------------------------------------------------------- #
def _full_results() -> list[object]:
    return [
        _clip("int8_float16", "RU", 300.0, 60.0, transcript="Привет мир int8", peak_vram_mb=2100.0),
        _clip("int8_float16", "EN", 280.0, 50.0),
        _clip("float16", "RU", 300.0, 80.0, transcript="Привет мир float16", peak_vram_mb=3400.0),
        _clip("float16", "EN", 280.0, 70.0),
    ]


def test_render_report_has_table_verdict_and_ab() -> None:
    report = measure_model.render_report(
        _full_results(), model_name="large-v3", bar=1.5, today=date(2026, 6, 16), host="RTX 4060"
    )
    # Header + provenance.
    assert "# Model measurement — large-v3" in report
    assert "RTX 4060" in report
    assert "Bar: 1.50x realtime" in report
    # Speed table: the int8 RU row is 300/60 = 5.00x.
    assert "| int8_float16 | RU |" in report
    assert "5.00x" in report
    assert "2100 MB" in report
    # Auto speed verdict (5.00x >= 1.5x).
    assert "PASS" in report
    # RU quality A/B carries BOTH transcripts for the operator's manual read.
    assert "Привет мир int8" in report
    assert "Привет мир float16" in report
    # TD-4-closing operator checklist.
    assert "closes TD-4" in report
    assert "Keep int8_float16" in report
    assert "float16 fallback" in report


def test_render_report_bar_not_set_is_informational() -> None:
    report = measure_model.render_report(
        _full_results(), model_name="large-v3", bar=None, today=date(2026, 6, 16)
    )
    assert "Bar not set" in report
    # Still surfaces the real measured number even with no bar.
    assert "5.00x" in report
    # No PASS/FAIL stamp without a bar (the colon distinguishes the verdict mark
    # from the "re-run ... to stamp PASS/FAIL" instruction).
    assert "PASS:" not in report and "FAIL:" not in report


def test_render_report_handles_missing_vram() -> None:
    results = [_clip("int8_float16", "RU", 300.0, 60.0, peak_vram_mb=None)]
    report = measure_model.render_report(results, model_name="m", bar=None, today=date(2026, 6, 16))
    assert "n/a" in report
