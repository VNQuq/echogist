#!/usr/bin/env python3
"""Cold-Windows clip acceptance driver (plan §12.1 / §12.3, T13).

Drives one canned clip through the real ship pipeline and asserts the artifacts
land — the "the ship target still works" gate that ``scripts/win-smoke.bat`` runs
on Windows before a release-worthy commit, and that ``scripts/dev-loop --gpu``
runs in WSL for the optional real-GPU transcribe of a fixture.

Split by GPU dependency (same split as the T11 measurement harness):

* The pure decision logic — the killswitch gate for the paid summary, artifact
  verification, clip resolution — is unit-tested in CI (``tests/test_smoke.py``).
* The actual run (:func:`run_smoke`) needs the bundled ffmpeg, the CUDA stack, the
  model, and a clip, so it is operator-run on Windows / WSL, never CI.

**Killswitch (CLAUDE.md).** Extraction (ffmpeg → mp3) and transcription (GPU →
transcript) always run — they are offline and are exactly what only the ship
target validates. The one network/paid stage, SUMMARIZE, runs ONLY when
``ECHOGIST_LIVE_SMOKE`` is set AND a key is present — the same opt-in shape as the
T10 ``ECHOGIST_LIVE_EVAL`` eval gate. No flag (or no key) ⇒ no call.

Run directly (standalone), not imported as a package module, so the same sys.path
bootstrap as the measurement harness is used to find ``echogist``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:  # allow `python scripts/smoke_run.py` to import echogist
    sys.path.insert(0, str(_APP_ROOT))

from echogist import (  # noqa: E402
    config,
    extract,
    guard,
    provision,
    render,
    summarize,
    transcribe,
)

_LIVE_ENV = "ECHOGIST_LIVE_SMOKE"
_DEFAULT_CLIP_DIR = _APP_ROOT / "tests" / "fixtures" / "audio"
# Clip stems searched, in order, when --clip is not given. Not committed — the
# operator places one on the ship box (see tests/fixtures/audio/README.md).
_CLIP_STEMS: tuple[str, ...] = ("smoke", "ru", "en")


class SmokeError(Exception):
    """A recoverable smoke failure — printed, then a non-zero exit."""


# --------------------------------------------------------------------------- #
# Pure core (CI-tested — no GPU, no ffmpeg, no network)
# --------------------------------------------------------------------------- #
def live_summary_enabled(env: Mapping[str, str], api_key: str | None) -> tuple[bool, str]:
    """Decide whether the paid SUMMARIZE stage runs (killswitch).

    Returns ``(enabled, reason)``. Enabled only when ``ECHOGIST_LIVE_SMOKE`` is
    truthy in ``env`` AND ``api_key`` is present, mirroring the T10 live-eval gate.
    The reason is always printed so a skipped summary is never silent.
    """
    if not env.get(_LIVE_ENV, "").strip():
        return (
            False,
            f"Summary step skipped (offline smoke). Set {_LIVE_ENV}=1 and "
            "ANTHROPIC_API_KEY to exercise the one paid call.",
        )
    if not api_key:
        return (
            False,
            f"{_LIVE_ENV} is set but ANTHROPIC_API_KEY is not — summary skipped "
            "(killswitch: no key, no call).",
        )
    return (True, f"Live summary enabled ({_LIVE_ENV} + key present).")


def missing_artifacts(paths: Iterable[Path]) -> list[Path]:
    """Return the expected artifacts that did not land — empty means PASS."""
    return [p for p in paths if not p.is_file()]


def resolve_clip(arg: str | None, clip_dir: Path) -> Path:
    """Resolve the clip to drive: an explicit ``--clip``, else the first matching
    ``<stem>.*`` in ``clip_dir`` (``smoke`` > ``ru`` > ``en``). Raises with
    placement guidance when none is found."""
    if arg:
        clip = Path(arg).expanduser()
        if not clip.is_file():
            raise SmokeError(f"Clip not found: {clip}.")
        return clip
    for stem in _CLIP_STEMS:
        matches = sorted(p for p in clip_dir.glob(f"{stem}.*") if p.is_file())
        if matches:
            return matches[0]
    raise SmokeError(
        f"No smoke clip found in {clip_dir}. Place a short audio/video clip there "
        f"as one of {', '.join(f'{s}.*' for s in _CLIP_STEMS)} (not committed; see "
        "the README in that dir), or pass --clip PATH."
    )


def model_dir(model_config: config.ModelConfig, base: Path) -> Path:
    """Resolve the Whisper model dir exactly as the menu/provisioning does."""
    local = Path(model_config.asset.local_dir)
    return local if local.is_absolute() else (base / local)


# --------------------------------------------------------------------------- #
# The run (operator-side: needs ffmpeg + GPU + model + a clip — never CI)
# --------------------------------------------------------------------------- #
def run_smoke(
    *,
    clip: Path,
    base: Path,
    env: Mapping[str, str] | None = None,
    log: Callable[[str], object] = print,
) -> list[Path]:
    """Drive ``clip`` through extract → transcribe → (gated) summarize+render.

    Returns the artifacts produced. Mirrors the menu's local-file flow ordering so
    this exercises the real wiring, not a parallel pipeline. The summary stage is
    skipped unless :func:`live_summary_enabled` says go (killswitch).
    """
    emit = log
    env = os.environ if env is None else env
    model_config = config.load_model_config()
    settings = config.load_settings()
    artifacts: list[Path] = []

    # 1. Extract → mp3 (ffmpeg). An already-mp3 clip is kept as-is (menu §3).
    if extract.is_mp3(clip):
        emit(f"Clip is already an mp3; keeping {clip.name} as the audio artifact.")
        artifacts.append(clip)
    else:
        mp3 = extract.extract_audio(clip, base / "output" / "audio", log=emit)
        emit(f"Saved mp3: {mp3}")
        artifacts.append(mp3)

    # 2. Transcribe → saved transcript (GPU). The original clip is the input, as
    #    in the menu (faster-whisper decodes the source directly).
    transcript = transcribe.transcribe(clip, model_dir(model_config, base), log=emit)
    tpath = transcribe.save_transcript(transcript, base / "output" / "transcripts", clip.stem)
    emit(f"Saved transcript: {tpath}")
    artifacts.append(tpath)

    # 3. Summarize + render — the one paid call, killswitch-gated.
    api_key = config.get_api_key()
    enabled, reason = live_summary_enabled(env, api_key)
    emit(reason)
    if not enabled or api_key is None:  # api_key is non-None when enabled — narrows for the call
        return artifacts

    tier = model_config.tier(settings.model_tier)
    verdict = guard.check_overflow(transcript.text, tier, model_config.guard)
    if verdict.over_budget:  # a ~2-min smoke clip should never trip this
        raise SmokeError(guard.overflow_message(verdict, tier))
    result = summarize.summarize_auto(
        transcript.text,
        tier,
        model_config.summarize,
        chunk_cfg=model_config.chunk,
        language=settings.summary_language,
        source_stem=clip.stem,
        api_key=api_key,
        log=emit,
    )
    summaries_dir = base / "output" / "summaries"
    json_path = summarize.save_raw_result(result.summary, summaries_dir / "raw")  # F13
    out_path = render.render(
        result.summary,
        summaries_dir,
        settings.output_format,
        base=json_path.stem,
        log=emit,
        notice=emit,
    )
    emit(f"Saved summary: {out_path}")
    artifacts.append(out_path)
    return artifacts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EchoGist cold-ship clip acceptance (T13).")
    parser.add_argument("--clip", help="clip to drive (default: a smoke/ru/en clip in fixtures)")
    parser.add_argument(
        "--base", help="app root holding output/ (default: the repo root)", default=None
    )
    args = parser.parse_args(argv)

    base = Path(args.base).expanduser() if args.base else provision.app_root()
    try:
        clip = resolve_clip(args.clip, _DEFAULT_CLIP_DIR)
        print(f"[smoke] clip: {clip}")
        artifacts = run_smoke(clip=clip, base=base)
    except (SmokeError, config.ConfigError) as exc:
        print(f"[smoke] FAILED: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - fail loud with the stage's message
        print(f"[smoke] FAILED: {exc}")
        return 1

    missing = missing_artifacts(artifacts)
    if missing:
        print("[smoke] FAILED: expected artifacts missing: " + ", ".join(str(p) for p in missing))
        return 1
    print(f"[smoke] PASS — {len(artifacts)} artifact(s):")
    for p in artifacts:
        print(f"  - {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
