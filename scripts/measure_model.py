#!/usr/bin/env python3
"""T11 — model measurement harness (TD-4, plan §12.3).

The int8_float16 default is a *hypothesis* until measured on the real 4060. This
harness is the repeatable gate that confirms or overturns it. It is split along
the one line that matters here — **GPU vs no-GPU**:

* **Pure, GPU-free (finished + unit-tested in WSL/CI):** :func:`realtime_factor`,
  :func:`speed_verdict`, :func:`render_report` and the :class:`ClipResult` shape.
  These compute the numbers and render ``docs/measurements/<model>-<date>.md``;
  they import nothing CUDA and are exercised by ``tests/test_measure.py``.
* **GPU-only (the operator runs on the Windows 4060):** :func:`run` /
  :func:`measure_clip` load the CT2 model directly (NOT via
  :func:`echogist.transcribe.transcribe`) so the measurement can be **warm** —
  load once per ``compute_type``, throw away a warmup pass, *then* time the
  clips. That is the fix for the TD-4 trap where the cold 0.35x was model-load /
  ``/mnt/c`` 9p dominated, not the real inference number.

The A/B is a ``compute_type`` flip on the **one** float16 ``model.bin`` pulled
from HF (``int8_float16`` then ``float16``) — no second download, no second
artifact (TD-1 HF decision). The harness ALWAYS surfaces the measured
``realtime_factor`` regardless of pass/fail; ``--bar`` is optional and set by the
operator *after* seeing the real number, not hardcoded blind. TD-4 closes when
the written doc carries real numbers plus the operator's manual int8-vs-float16
RU quality verdict.

Run (on the Windows 4060, from the repo root)::

    python scripts/measure_model.py --ru tests/fixtures/audio/ru.mp3 \
                                    --en tests/fixtures/audio/en.mp3
    # ...read the measured realtime_factor, then re-run with the bar you choose:
    python scripts/measure_model.py --bar 1.5
"""

from __future__ import annotations

import argparse
import gc
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
# Standalone-run bootstrap: `python scripts/measure_model.py` puts scripts/ on
# sys.path, not the repo root, so the echogist package would not import. (Under
# pytest the rootdir is already on the path, so this is a no-op there.)
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from echogist import config, gpu  # noqa: E402  (after the sys.path bootstrap above)
from echogist.transcribe import format_timecode  # noqa: E402

Logger = Callable[[str], object]
_DEFAULT_OUT = _APP_ROOT / "docs" / "measurements"
_DEFAULT_AUDIO_DIR = _APP_ROOT / "tests" / "fixtures" / "audio"
_COMPUTE_TYPES: tuple[str, ...] = ("int8_float16", "float16")


class MeasureError(Exception):
    """A recoverable measurement failure. Print it, exit non-zero (fail loud)."""


# --------------------------------------------------------------------------- #
# Pure, GPU-free core (unit-tested in CI — tests/test_measure.py)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClipResult:
    """One timed transcription: the durable numbers plus the text for a quality read."""

    compute_type: str
    label: str  # "RU" / "EN"
    audio_sec: float
    wall_sec: float
    peak_vram_mb: float | None
    language: str
    transcript: str


def realtime_factor(audio_sec: float, wall_sec: float) -> float:
    """``audio_sec / wall_sec`` — higher is faster. Non-positive wall -> 0.0 (guard)."""
    if wall_sec <= 0:
        return 0.0
    return audio_sec / wall_sec


def speed_verdict(int8_rtf: float, bar: float | None) -> tuple[bool | None, str]:
    """Stamp the speed gate against the int8_float16 RU realtime_factor.

    ``bar is None`` (the default first run) is NOT a failure: the harness reports
    the measured number and asks the operator to set the bar from it. Returns
    ``(passed, line)`` where ``passed`` is ``None`` when no bar is set.
    """
    if bar is None:
        return None, (
            f"Bar not set. Measured int8_float16 realtime_factor = {int8_rtf:.2f}x. "
            "Set the bar from this number (and the RU quality read below), then "
            "re-run with --bar to stamp PASS/FAIL."
        )
    passed = int8_rtf >= bar
    relation = ">=" if passed else "<"
    mark = "PASS" if passed else "FAIL"
    return passed, (
        f"{mark}: int8_float16 realtime_factor {int8_rtf:.2f}x {relation} bar {bar:.2f}x."
    )


def _find(results: Iterable[ClipResult], compute_type: str, label: str) -> ClipResult | None:
    for r in results:
        if r.compute_type == compute_type and r.label == label:
            return r
    return None


def _vram(value: float | None) -> str:
    return f"{value:.0f} MB" if value is not None else "n/a"


def _quality_block(title: str, result: ClipResult | None) -> list[str]:
    body = (result.transcript if result and result.transcript else "(missing)").strip() or "(empty)"
    return [f"### {title}", "", "```", body, "```", ""]


def render_report(
    results: list[ClipResult],
    *,
    model_name: str,
    bar: float | None,
    today: date,
    host: str | None = None,
) -> str:
    """Render the durable ``docs/measurements/<model>-<date>.md`` report.

    A speed+VRAM table over every (compute_type, clip), an auto speed verdict
    against the int8 RU realtime_factor, the int8-vs-float16 RU transcripts side
    by side for the operator's manual quality read, and the TD-4-closing operator
    verdict checklist.
    """
    int8_ru = _find(results, "int8_float16", "RU")
    int8_rtf = realtime_factor(int8_ru.audio_sec, int8_ru.wall_sec) if int8_ru else 0.0
    _passed, verdict_line = speed_verdict(int8_rtf, bar)

    bar_text = (
        f"{bar:.2f}x realtime" if bar is not None else "not set (operator sets from measured)"
    )
    lines: list[str] = [
        f"# Model measurement — {model_name}",
        "",
        f"- Date: {today.isoformat()}",
        f"- Host: {host or 'unspecified'}",
        f"- Bar: {bar_text}",
        "- Method: warm (model load + one warmup pass excluded from the timed region).",
        "",
        "## Speed + VRAM",
        "",
        "| compute_type | clip | audio | wall | realtime_factor | peak VRAM |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in results:
        rtf = realtime_factor(r.audio_sec, r.wall_sec)
        lines.append(
            f"| {r.compute_type} | {r.label} | {format_timecode(r.audio_sec)} | "
            f"{format_timecode(r.wall_sec)} | {rtf:.2f}x | {_vram(r.peak_vram_mb)} |"
        )

    lines += ["", "## Speed verdict (auto)", "", verdict_line, ""]

    lines += ["## RU quality A/B — operator read", ""]
    lines += _quality_block("int8_float16 — RU", _find(results, "int8_float16", "RU"))
    lines += _quality_block("float16 (control) — RU", _find(results, "float16", "RU"))

    lines += [
        "## OPERATOR VERDICT (closes TD-4)",
        "",
        "- [ ] Keep int8_float16 (default) — RU quality >= the float16 control.",
        "- [ ] Switch to the float16 fallback (plan §6) — int8 RU quality regressed.",
        "",
        "Reason: _<fill in: what the RU A/B showed>_",
        "",
    ]
    return "\n".join(lines) + "\n"


def report_filename(model_name: str, today: date) -> str:
    """``<slug>-<date>.md`` with the model name slugged to filesystem-safe chars."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip("-") or "model"
    return f"{slug}-{today.isoformat()}.md"


# --------------------------------------------------------------------------- #
# GPU-only path (the operator runs this on the Windows 4060 — never in CI)
# --------------------------------------------------------------------------- #
class _VramSampler:
    """Best-effort peak-VRAM probe: polls ``nvidia-smi`` in a daemon thread.

    Degrades to ``None`` (rendered "n/a") when ``nvidia-smi`` is absent or
    unparseable — the speed numbers are the gate, VRAM is informational.
    """

    def __init__(self, interval: float = 0.2) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_mb: float | None = None

    @staticmethod
    def _poll_once() -> float | None:
        try:
            proc = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        vals: list[float] = []
        for token in proc.stdout.split():
            try:
                vals.append(float(token))
            except ValueError:
                continue
        return max(vals) if vals else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            value = self._poll_once()
            if value is not None:
                self.peak_mb = value if self.peak_mb is None else max(self.peak_mb, value)
            self._stop.wait(self._interval)

    def __enter__(self) -> _VramSampler:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def _consume(stream: Iterable[object]) -> list[str]:
    """Force the lazy faster-whisper segment generator (where the GPU work happens)."""
    return [str(getattr(seg, "text", "")).strip() for seg in stream]


def _load_model(model_dir: Path, compute_type: str, device: str) -> object:
    """Load the CT2 model the same way :func:`echogist.transcribe.transcribe` does.

    Registers the bundled cuDNN/cuBLAS DLLs (win32 shim) before importing
    faster-whisper, and maps a backend load failure to gpu's friendly F8 message.
    """
    try:
        gpu.register_cuda_libraries()
    except Exception as exc:  # noqa: BLE001 - add_dll_directory can raise on an odd env
        raise MeasureError(gpu.diagnose_import_error(exc)) from exc
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MeasureError(
            "faster-whisper is not installed — run.bat installs it from requirements.lock."
        ) from exc
    try:
        return WhisperModel(str(model_dir), device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly diagnostic
        raise MeasureError(gpu.diagnose_import_error(exc)) from exc


def measure_clip(model: object, clip_path: Path, compute_type: str, label: str) -> ClipResult:
    """Warm-time one clip: peak VRAM sampled across the timed decode loop."""
    with _VramSampler() as sampler:
        start = time.perf_counter()
        segment_stream, info = model.transcribe(str(clip_path))  # type: ignore[attr-defined]
        parts = _consume(segment_stream)
        wall = time.perf_counter() - start
    audio = float(getattr(info, "duration", 0.0) or 0.0)
    language = str(getattr(info, "language", "") or "")
    return ClipResult(
        compute_type=compute_type,
        label=label,
        audio_sec=audio,
        wall_sec=wall,
        peak_vram_mb=sampler.peak_mb,
        language=language,
        transcript=" ".join(p for p in parts if p).strip(),
    )


def run(
    *,
    model_dir: Path,
    ru_clip: Path,
    en_clip: Path,
    bar: float | None,
    out_dir: Path,
    model_name: str,
    device: str = "cuda",
    today: date | None = None,
    host: str | None = None,
    log: Logger = print,
) -> Path:
    """Run the full int8-vs-float16 A/B on the 4060 and write the measurements doc."""
    today = today or date.today()
    results: list[ClipResult] = []
    for compute_type in _COMPUTE_TYPES:
        log(f"Loading model ({compute_type}) from {model_dir} ...")
        model = _load_model(model_dir, compute_type, device)
        try:
            log("  warmup pass (excluded from timing) ...")
            warm_stream, _info = model.transcribe(str(ru_clip))  # type: ignore[attr-defined]
            _consume(warm_stream)
            for clip, label in ((ru_clip, "RU"), (en_clip, "EN")):
                log(f"  measuring {label} ({clip.name}) ...")
                result = measure_clip(model, clip, compute_type, label)
                rtf = realtime_factor(result.audio_sec, result.wall_sec)
                log(f"    realtime_factor = {rtf:.2f}x")
                results.append(result)
        except MeasureError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail loud, recoverable
            raise MeasureError(f"Measurement failed ({compute_type}): {exc}.") from exc
        finally:
            del model
            gc.collect()  # free VRAM before loading the next compute_type

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / report_filename(model_name, today)
    out_path.write_text(
        render_report(results, model_name=model_name, bar=bar, today=today, host=host),
        encoding="utf-8",
    )
    return out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_model(model_dir_arg: str | None) -> tuple[Path, str]:
    """Resolve (model_dir, model_name) from --model-dir or models.toml [model_asset]."""
    cfg = config.load_model_config()
    name = cfg.asset.name
    if model_dir_arg:
        return Path(model_dir_arg), name
    local = Path(cfg.asset.local_dir)
    return (local if local.is_absolute() else _APP_ROOT / local), name


def _resolve_clip(arg: str | None, label: str) -> Path:
    """A --ru/--en path, else the first tests/fixtures/audio/<ru|en>.* on disk."""
    if arg:
        clip = Path(arg)
    else:
        matches = sorted(_DEFAULT_AUDIO_DIR.glob(f"{label}.*"))
        if not matches:
            raise MeasureError(
                f"No {label.upper()} clip given and none found in {_DEFAULT_AUDIO_DIR} "
                f"(expected {label}.mp3 or similar — see that dir's README.md)."
            )
        clip = matches[0]
    if not clip.is_file():
        raise MeasureError(f"{label.upper()} clip not found: {clip}.")
    return clip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EchoGist T11 model measurement harness (TD-4).")
    parser.add_argument("--model-dir", default=None, help="CT2 model dir (default: models.toml).")
    parser.add_argument("--ru", default=None, help="RU reference clip (default: fixtures ru.*)")
    parser.add_argument("--en", default=None, help="EN reference clip (default: fixtures en.*)")
    parser.add_argument(
        "--bar",
        type=float,
        default=None,
        help="realtime_factor PASS threshold. Omit on the first run, then set it from "
        "the measured number (e.g. --bar 1.5).",
    )
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="Output dir for the report.")
    parser.add_argument("--device", default="cuda", help="ctranslate2 device (default: cuda).")
    parser.add_argument("--host", default=None, help="Host label for the report (e.g. 'RTX 4060').")
    args = parser.parse_args(argv)

    try:
        model_dir, model_name = _resolve_model(args.model_dir)
        ru_clip = _resolve_clip(args.ru, "ru")
        en_clip = _resolve_clip(args.en, "en")
        out_path = run(
            model_dir=model_dir,
            ru_clip=ru_clip,
            en_clip=en_clip,
            bar=args.bar,
            out_dir=Path(args.out),
            model_name=model_name,
            device=args.device,
            host=args.host,
        )
    except (MeasureError, config.ConfigError) as exc:
        print(f"measure_model: {exc}")
        return 1
    print(f"measure_model: wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
