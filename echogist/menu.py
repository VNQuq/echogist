"""T9 — the interactive console menu (plan §3 / §5). Orchestration, not a stage.

The main menu loops until the operator explicitly exits. It wires the pure stages
(extract, transcribe, guard, summarize, render) and the cost flow (T8) around TWO
modules that differ only in the SCALE of their input (operator, 2026-09-04). Both ask
the same question about the output, off the same ``_ACTION_CHOICES``, so neither can
drift into offering something the other cannot produce:

* **1. Single file** — one recording, or one transcript already on disk.

  * *Audio or video file* → {MP3 only · summary · transcript}. On a summary/transcript
    run the MP3 is kept by default with a per-run opt-out confirm. An mp3 source gets
    {summary · transcript only · re-encode to a smaller MP3} instead.
  * *Saved transcript* → pick a saved ``output/transcripts/*.txt`` or type a path and
    re-summarize it. This is the artifact-based recovery path (plan §3): a summarize
    that failed (F2/F4/F5) re-runs from here without re-transcribing.

* **2. Folder** — every file under one folder, picked ONCE and handed to the action, so
  the preview and the run that follows it cannot end up on different trees. The preview
  is not a row to choose: picking the folder RUNS it, so what is in the tree and what it
  would cost are on screen before the action is asked for.

  * *MP3 only* — the bounded conversion pool over the folder's contents.
  * *Transcript* — the folder run stopped after its free local phase; no gate, no wire.
  * *Summary* — the two-phase folder run: transcribe everything, then ONE exact quote
    over the real transcripts and ONE answer for the whole run.

* **3. Settings** — edit summary language / output format / model tier / cost
  threshold / batch workers; persisted to ``settings.json``.
* **4. Exit.**

The five functional rows this replaced (Local file · Batch: videos → MP3 · Saved
transcript · Scan a folder · Summarize a folder) are all still reachable one level
down. The one capability that lost its menu row is hand-picking a SUBSET of files to
convert: :func:`_flow_mp3` still takes a file list and is still covered, the
folder module simply hands it the folder instead.

**The UI seam (v1.1 §3).** Every prompt and every line of output goes through a
:class:`~echogist.ui.UI` injected on :class:`Deps`. Production is the rich+questionary
adapter (arrow-key menus, a %/ETA transcription bar, panels); tests inject
:class:`~echogist.ui.StubUI` so the whole menu runs offline with no TTY, no key, no
network (killswitch). The flows below are pure orchestration over that Protocol.

**Fail loud, return to menu (CLAUDE.md).** Every stage error is recoverable: the
flow surfaces the human-readable message the stage already carried (F1 bad path, F2
no internet, F3 missing key, F4 billing, F5 unknown model, F6 overflow, F11 ffmpeg,
F13 render-after-pay) and returns to the loop. The loop keeps a broad backstop so an
unexpected error returns to the menu instead of crashing — and if the UI itself can't
report (a broken adapter), it falls back to builtin ``print`` (plan §6.3).

**Killswitch (CLAUDE.md).** This module orchestrates the one network stage
(SUMMARIZE) but is itself offline at import time — it pulls in no network package
at module top level (``anthropic`` is lazy inside :mod:`echogist.summarize`, and
``rich``/``questionary`` are offline libs behind the UI seam). Every collaborator
that touches the GPU, the wire, or ffmpeg is injected through :class:`Deps`, so the
whole menu is unit-testable with no model, no key, no network.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from time import monotonic

from . import (
    chunk,
    config,
    cost,
    extract,
    folder,
    guard,
    provision,
    render,
    scan,
    summarize,
    transcribe,
)
from .config import VALID_FORMATS, VALID_LANGUAGES, ConfigError, Settings
from .extract import ExtractError
from .folder import Cancelled as FolderCancelled
from .folder import Report as FolderReport
from .model_asset import ProvisionError
from .render import RenderError
from .scan import ScanCancelled, ScanResult
from .summarize import SummarizeError, SummarizeResult
from .transcribe import TranscribeError, Transcript
from .ui import (
    REVEAL_AUDIO,
    REVEAL_SUMMARY,
    REVEAL_TRANSCRIPT,
    UI,
    Choice,
    NotInteractiveError,
    build_default_ui,
    human_size,
)

Logger = Callable[[str], object]

# The injectable stage seams. ``...`` arg types keep the alias readable; the real
# functions take keyword-only args a bare Callable cannot spell out, and the
# defaults below pin the production implementations.
ExtractFn = Callable[..., Path]
ConvertManyFn = Callable[..., FolderReport]
TranscribeFn = Callable[..., Transcript]
SummarizeFn = Callable[..., SummarizeResult]
RenderFn = Callable[..., Path]
ApiKeyFn = Callable[[], str | None]

# The stage errors a flow may surface; the loop's backstop returns to the menu on
# any of these (plus the unexpected-error guard) instead of crashing.
_RECOVERABLE = (
    ConfigError,
    ExtractError,
    TranscribeError,
    SummarizeError,
    RenderError,
    ProvisionError,
    OSError,
)

# The F1 bad-path message, in ONE place: the transcript prompt, the single-file picker's
# typed entry, and the batch picker's console fallback all say the same thing.
_F1_NOT_FOUND = "File not found: {path}. Check the path and try again."

_API_KEY_HELP = (
    "No Anthropic API key found, so the summarization step can't run (F3). Set the "
    "ANTHROPIC_API_KEY environment variable, or put it in config/secrets.toml (copy "
    "config/secrets.toml.example), then re-launch; MP3 extraction works without a key. "
    "Your transcript is saved — re-summarize it from the 'Saved transcript' menu entry "
    "once the key is set."
)


@dataclass(frozen=True)
class Deps:
    """Injected collaborators. Defaults are the production wiring; tests pass stubs
    for the seams that touch the GPU, the wire, ffmpeg, and the operator's terminal so
    the whole menu runs offline and deterministically (killswitch-safe).

    ``ui`` is built lazily in :func:`run_menu` when ``None`` (the production adapter
    needs a TTY, which must not be required at ``Deps()`` construction / import time);
    tests always inject a :class:`~echogist.ui.StubUI`.
    """

    ui: UI | None = None
    # External / heavy stages — stubbed in tests.
    extract_audio: ExtractFn = extract.extract_audio
    convert_many: ConvertManyFn = folder.convert_many
    transcribe: TranscribeFn = transcribe.transcribe
    summarize: SummarizeFn = summarize.summarize_auto
    render: RenderFn = render.render
    get_api_key: ApiKeyFn = config.get_api_key
    # App root (holds ``output/``) and the settings file location.
    base: Path = field(default_factory=provision.app_root)
    settings_path: Path | None = None


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _ui(deps: Deps) -> UI:
    """The resolved UI. ``run_menu`` guarantees it is set before any flow runs."""
    assert deps.ui is not None  # set by run_menu before dispatching a flow
    return deps.ui


def _model_dir(model_config: config.ModelConfig, base: Path) -> Path:
    """Resolve the Whisper model dir the same way provisioning does (relative to the
    app root unless the configured ``local_dir`` is absolute)."""
    local = Path(model_config.asset.local_dir)
    return local if local.is_absolute() else (base / local)


def _resolve_typed_path(deps: Deps, typed: str) -> Path | None:
    """A typed/picked path → an existing file, or None (cancel / F1 bad path).

    Shared by the transcript prompt (the 'transcript' flow) and the file picker's path entry
    (the 'local' flow, TD-10), so the F1 message lives here once."""
    typed = typed.strip()
    if not typed:
        return None
    path = Path(typed).expanduser()
    if not path.is_file():  # F1
        _ui(deps).error(_F1_NOT_FOUND.format(path=path))
        return None
    return path


def _oversize_phase_message(p: chunk.Phase, est: int, budget: int, tier: config.ModelTier) -> str:
    """F6 message when one synthesis phase still overflows the tier context (TD-16 v2).

    Reached only when a single transcript block is so large that ``plan_phases``
    (which never cuts mid-block, K clamped to len(blocks)) cannot get it under the tier's
    safe budget — not a normal long lecture, but an abnormally coarse/large block. Guides
    the operator to the two real levers (bigger-context tier, or a finer-timecoded re-save).
    """
    return (
        f"Even split into phases, phase {p.index}/{p.total} ({p.span}) is too large "
        f"for the '{tier.name}' tier (estimated {est:,} input tokens vs a safe budget of "
        f"{budget:,}). One transcript block is abnormally large to summarize on its own — "
        f"choose a larger-context model in Settings, or re-save the transcript with finer "
        f"timecodes. Your transcript is saved."
    )


# blake2s(digest_size=N) renders as 2N hex chars, so the sweep's shape check is DERIVED
# from the digest size rather than restated. Getting these two out of step would make
# _sweep_stale_resumes delete every VALID partial as "stale" -- silently, and only on the
# re-run that was supposed to save the work.
_RESUME_KEY_DIGEST_SIZE = 8
_RESUME_KEY_RE = re.compile(rf"[0-9a-f]{{{_RESUME_KEY_DIGEST_SIZE * 2}}}")


def _resume_key(source_path: Path) -> str:
    """Identity of the source being summarized, as the stem of its resume partial.

    Keyed on the resolved PATH, not the stem. Two different recordings can share a stem
    (``lecture.mp4`` in two folders, the same talk saved twice); under a stem key the second
    one would load the first's partial, and ``_load_resume`` cannot tell them apart — it only
    checks the phase count. The result is a silently wrong PAID summary carrying the other
    recording's prose and timecodes, which the anchor validator accepts because those
    timecodes are real, just from the wrong file.

    ``casefold`` because :meth:`Path.resolve` does not reliably normalize case on Windows:
    the same file picked twice with different casing must not produce two keys and silently
    lose the resume.
    """
    return hashlib.blake2s(
        str(source_path.resolve()).casefold().encode(), digest_size=_RESUME_KEY_DIGEST_SIZE
    ).hexdigest()


def _sweep_stale_resumes(resume_dir: Path, ui: UI) -> None:
    """Delete partials left by the pre-hash stem key, which nothing can reach (best-effort).

    Swept by SHAPE, not by date: a stem that is not 16 hex chars predates :func:`_resume_key`
    and will never be looked up again. These are within-run partials, not durable artifacts,
    so dropping one costs at most a re-synthesis of phases that were never going to be
    reloaded anyway.
    """
    # glob yields nothing for a missing or unreadable dir rather than raising, so the
    # first run (no .resume/ yet) needs no special case.
    stale = [p for p in resume_dir.glob("*.json") if not _RESUME_KEY_RE.fullmatch(p.stem)]
    for path in stale:
        with contextlib.suppress(OSError):
            path.unlink()
    if stale:
        ui.info(f"Removed {len(stale)} resume file(s) left by an older version.")


def _load_resume(resume_path: Path, k: int, ui: UI) -> summarize.Summary | None:
    """Load a within-run phase partial, if one is a valid PREFIX of this K-phase plan.

    Artifact-resume (decision #2): a prior run that died mid-way left its completed phases
    at ``resume_path``. Reuse it when its synthesis is a non-empty prefix up to AND INCLUDING
    all K phases (``0 < len <= k``): the complete case (every phase synthesized but the run
    died before the durable .json, e.g. reconcile failed) is finished — only reconcile re-runs
    — not re-paid. A truly stale partial (``len > k`` — the transcript/K shrank) is ignored
    and the run starts fresh. Best-effort — any read/parse failure falls back to a fresh run
    rather than blocking the operator.
    """
    if not resume_path.is_file():
        return None
    try:
        partial = render.load_summary(resume_path)
    except (OSError, ValueError, RenderError):  # unreadable/corrupt partial -> fresh run
        return None
    if not (0 < len(partial.synthesis) <= k):  # stale only if K shrank below the saved count
        return None
    ui.info(f"Resuming a previous run: {len(partial.synthesis)}/{k} phases already saved.")
    return partial


def _clear_resume(resume_path: Path) -> None:
    """Delete the within-run phase partial once the durable .json exists (best-effort)."""
    with contextlib.suppress(OSError):
        resume_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# The one summary sub-flow, shared by every summary-producing path
# --------------------------------------------------------------------------- #
def _report_dropped_blocks(ui: UI, dropped: Sequence[str], *, preview: int = 3) -> None:
    """Say what :func:`chunk.drop_degenerate_blocks` removed from the synthesis input.

    CLAUDE.md forbids a silent skip, and this one is worth seeing for its own sake: a long
    boilerplate run at the head of a transcript usually means the recording opens on silence
    or a title card, which is the operator's cue that the timecodes they are about to read
    start later than the video does. A few examples, not the whole list — six identical
    greeting lines say nothing the first one did not.
    """
    if not dropped:
        return
    ui.info(
        f"Skipping {len(dropped)} non-speech block(s) before summarizing "
        f"(repeated filler transcribed over silence; the saved transcript keeps them):"
    )
    for line in dropped[:preview]:
        ui.info(f"    {line[:88]}")
    if len(dropped) > preview:
        ui.info(f"    ... and {len(dropped) - preview} more")


def _run_summary(
    deps: Deps,
    settings: Settings,
    model_config: config.ModelConfig,
    transcript_text: str,
    source_path: Path,
    source_stem: str,
    *,
    gate: bool = True,
    on_cost: Callable[[cost.CostEstimate], None] | None = None,
) -> Path | None:
    """GUARD → cost/threshold → the one paid SUMMARIZE call → persist .json → RENDER.

    Pure-stage ordering of plan §3: the local overflow guard (F6) and the cost
    estimate run BEFORE any network touch (killswitch); the raw result is saved to
    ``.json`` BEFORE render so a render failure never costs a re-pay (F13). Every
    early return lands back in the menu loop with the transcript already saved.

    Returns the rendered document's path, or ``None`` if any step declined or failed —
    the single-file flow ignores it (the message is already on screen); the folder run
    reads it to build its report.

    ``gate=False`` skips ONLY the per-file estimate and threshold confirm, because the
    folder run already showed one exact quote over every transcript and took one answer
    for the whole run (asking again per file is the babysitting the flow exists to
    remove). Everything else that protects a paid call still runs per file: the overflow
    guard, the API-key check, and the unverified-price notice.

    ``on_cost`` receives the AUDITED cost of the call that just landed, so a folder run
    can total real spend against the one quote it showed. Mirrors the ``on_phase`` seam:
    the caller decides what to do with it, this function only reports.
    """
    ui = _ui(deps)
    tier = model_config.tier(settings.model_tier)  # ConfigError (F5) → loop backstop

    # TD-26: strip Whisper's boilerplate loops (subtitle credits / channel greetings emitted
    # over silence) from what the model reads. The file on disk keeps every block — this
    # trims the synthesis INPUT only. Reported, never silent (CLAUDE.md: no silent skip).
    transcript_text, dropped = chunk.drop_degenerate_blocks(transcript_text, model_config.chunk)
    _report_dropped_blocks(ui, dropped)

    # TD-16 v2: split the transcript into K contiguous synthesis phases, locally + before
    # the wire. The same deterministic plan_phases is used inside summarize_auto, so the K
    # that runs matches the cost shown. ALL material runs this path — short collapses to K=1.
    plan = chunk.plan_phases(transcript_text, model_config.chunk)
    phase_inputs = [guard.estimate_input_tokens(p.text) for p in plan]
    # F6: phase-split lowers the per-call input but does NOT repeal the overflow guard. A
    # single un-splittable block (plan_phases caps K at len(blocks)) can still exceed the
    # tier context; catch it locally, before the wire, not mid-run after partial spend.
    budget = model_config.guard.safe_budget(tier)
    oversized = next(
        ((p, est) for p, est in zip(plan, phase_inputs, strict=True) if est > budget), None
    )
    if oversized is not None:
        p, est = oversized
        ui.warn(_oversize_phase_message(p, est, budget, tier))
        return None
    estimate = cost.estimate_cost_synthesis(
        phase_inputs,
        tier,
        output_cap=model_config.summarize.max_output_tokens,
        reconcile_floor=model_config.summarize.reconcile_output_floor_tokens,
    )

    api_key = deps.get_api_key()
    if api_key is None:  # F3 — never reach the wire without a key
        ui.warn(_API_KEY_HELP)
        return None

    k = len(plan)
    # K phase calls + 1 reconcile, always (matches the cost estimate): the reconcile writes
    # the essence block the document opens with, so even K=1 short material pays for it.
    calls = k + 1
    ui.info(
        f"Synthesizing the transcript in {k} phase{'s' if k > 1 else ''} (+1 reconcile) so it "
        f"reads as one faithful document — {calls} cloud calls."
    )
    if gate:
        # Under a folder run this line is the SEVENTH restatement of a price the operator
        # already approved once, for a fraction they never agreed to separately. The
        # per-file ACTUAL below still prints: that is real spend, and it accumulates into
        # the folder total.
        ui.info(cost.estimate_message(estimate, tier))

    # Unverified-price notice: scripts/check-models.py flags a tier whose model had a
    # generation bump but whose prices a human has not re-confirmed yet. The estimate
    # above uses those carried-over prices, so it may under-state the bill. One-time,
    # non-blocking (fires once per run) — it does NOT move the gate or the threshold.
    if gate and tier.prices_unverified:
        ui.warn(
            f"Model generation changed for the '{tier.name}' tier — prices unconfirmed, so "
            f"the estimate above may under-state the real bill and clear the "
            f"${settings.confirm_threshold_usd:g} gate when it should not. "
            "Verify the pricing page and clear prices_unverified in config/models.toml."
        )

    # Threshold friction (plan §3): above the operator's threshold, an explicit y/N gate
    # (default No) must clear before spending — always, regardless of the flag below. At/below
    # threshold the call always runs (a y/N there could wrongly decline a call that just
    # proceeds); TD-9 makes that path operator-controlled via settings.auto_accept_under_threshold.
    # Default (True) → proceed immediately, the shown estimate is the acknowledgment. False → a
    # non-decision "press Enter" acknowledge beat first, so the operator can Ctrl-C out.
    if not gate:
        pass  # folder run: one quote, one answer, already given for every file
    elif cost.requires_explicit_confirmation(estimate, settings.confirm_threshold_usd):

        def _confirm(prompt: str, default: bool) -> bool:  # adapt ui.confirm's kw-only default
            return ui.confirm(prompt, default=default)

        if not cost.confirm_proceed(estimate, settings.confirm_threshold_usd, confirm=_confirm):
            ui.info("Summarization cancelled; your transcript is saved.")
            return None
    elif not settings.auto_accept_under_threshold:
        # Auto-accept off: the operator wants a non-decision acknowledge beat on the cheap
        # path — a chance to Ctrl-C out before spending. Default is auto-accept ON, where the
        # shown estimate is the acknowledgment and the call just proceeds.
        ui.text("Press Enter to summarize, or Ctrl-C to cancel")

    summaries_dir = deps.base / "output" / "summaries"
    # Artifact-resume (decision #2): completed phases persist to a STABLE per-source path
    # under raw/.resume/; a re-run reloads it and skips the phases already on disk (no job
    # engine — just "phase N on disk -> skip"). The transcript is the checkpoint; this is a
    # within-run partial that is deleted once the durable .json exists.
    resume_dir = summaries_dir / "raw" / ".resume"
    _sweep_stale_resumes(resume_dir, ui)
    resume_path = resume_dir / f"{_resume_key(source_path)}.json"
    resume_from = _load_resume(resume_path, k, ui)

    def _persist(partial: summarize.Summary) -> None:
        # best-effort: a state-write failure must never abort a paid run
        with contextlib.suppress(OSError):
            summarize.write_summary_json(partial, resume_path)

    with ui.spinner(f"Summarizing ({calls} cloud calls)") as sp:

        def _progress(message: str) -> None:
            """Print the stage's own line AND put it beside the spinner.

            The printed lines are the history (which phase covered which span); the spinner
            shows what is in flight RIGHT NOW next to a running clock. Without the second
            half a phase call is minutes of a motionless label, which reads as a hung
            process — the operator killed a working run over exactly that on 2026-09-04.

            They print MUTED: a seven-file run emits ~60 of these, and at the weight of the
            result they bury it. They are proof of movement and a place to scroll back to,
            not the thing the operator is waiting for.
            """
            ui.detail(message)
            sp.update(message.rstrip("."))

        try:
            result = deps.summarize(
                transcript_text,
                tier,
                model_config.summarize,
                chunk_cfg=model_config.chunk,
                language=settings.summary_language,
                source_stem=source_stem,
                api_key=api_key,
                log=_progress,
                # The OTHER channel (TD-29). ``_progress`` is muted and feeds the spinner:
                # right for sixty phase lines, wrong for "I dropped four anchors" or "the
                # model wrote three characters of Chinese". A finding the operator must
                # act on goes to ui.warn, at full contrast, and does not touch the spinner.
                notice=ui.warn,
                on_phase=_persist,
                resume_from=resume_from,
            )
        except SummarizeError as exc:  # F2 / F4 / F5 — message carried by the stage
            sp.done(ok=False, message="Summarization failed")
            ui.error(str(exc))
            return None
        sp.done(ok=True, message="Summary received")

    actual = cost.actual_cost(result, tier)
    ui.info(cost.actual_message(actual))
    if on_cost is not None:
        on_cost(actual)

    # F13: the raw .json goes under summaries/raw/ so summaries/ holds only the
    # readable .pdf/.md; render reuses its stem so the triplet still shares a base.
    json_path = summarize.save_raw_result(  # BEFORE render
        result.summary, summaries_dir / "raw", source_path=source_path
    )
    _clear_resume(resume_path)  # durable artifact exists — the within-run partial is spent
    try:
        out_path = deps.render(
            result.summary,
            summaries_dir,
            settings.output_format,
            base=json_path.stem,  # json/pdf/md share one stem
            log=ui.info,
        )
    except RenderError as exc:  # F13 — saved, re-render without re-paying
        ui.error(
            f"{exc}\nThe summary is saved as {json_path.name}; re-render it later "
            "without paying for the call again."
        )
        return None

    ui.success(f"Done — summary written to {out_path}")
    # TD-14: pop the summaries folder (Windows, once/launch). REVEAL_SUMMARY outranks an
    # earlier audio reveal, so a video Summary run (which also kept the MP3 baseline, TD-12)
    # still ends on the summaries folder, not audio.
    ui.reveal_dir(summaries_dir, priority=REVEAL_SUMMARY)
    return out_path


def _transcribe_to_checkpoint(
    deps: Deps, source: Path, model_config: config.ModelConfig
) -> tuple[Path, str]:
    """Run TRANSCRIBE behind a %/ETA progress bar, save the checkpoint, return it.

    Returns ``(saved path, its text)``: the folder run needs the path for its report and
    its resume, the single-file flow needs only the text.

    The bar advances on the stage's progress fraction (plan §5). For zero-duration /
    unprobeable audio no fraction is knowable, so an honest segment-count line is shown
    after the bar instead of a fake percentage (T8)."""
    ui = _ui(deps)
    model_dir = _model_dir(model_config, deps.base)
    with ui.progress("Transcribing audio", total=1.0) as bar:
        transcript = deps.transcribe(source, model_dir, progress=bar.advance_to, log=ui.info)
        bar.done()
    if transcript.duration <= 0 and transcript.segments:  # T8: no-ETA readout
        ui.info(f"{len(transcript.segments)} segments transcribed (duration unknown).")
    tpath = transcribe.save_transcript(
        transcript,
        deps.base / "output" / "transcripts",
        source.stem,
        block_seconds=model_config.transcript.block_seconds,
    )
    ui.info(f"Saved transcript: {tpath}")
    # TD-12: the transcripts folder is revealed by the caller's transcript-only branch, NOT
    # here — this helper also runs on the Summary path, and revealing from here would pop
    # transcripts on a Summary run (the original TD-14 bug). Reveal stays caller-side.
    # Summarize the saved checkpoint VERBATIM (not transcript.text) so the fresh-run
    # and recovery (re-summarize saved .txt) paths feed byte-identical, timecoded text
    # to GUARD + SUMMARIZE — the model can cite real section_timecodes on both.
    return tpath, tpath.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Source flows
# --------------------------------------------------------------------------- #
# TD-12 (redesign): for a video, MP3 is the BASELINE, not a toggle or a combo — every
# branch extracts and KEEPS the MP3 in output/audio. The menu only varies how far down the
# pipeline the run goes, so the old "Both" framing (and the misleading "Summary needs no
# audio" label) is gone. Order is prominence, NOT cost: Summary (the primary feature) gets
# the middle slot, Transcript (the niche "just the text" fallback) is last. Keys are
# semantic so _flow_local_file branches on the name, not an opaque digit.
_ACTION_CHOICES: tuple[Choice, ...] = (
    ("mp3", "MP3 only"),
    ("summary", "Summary (MP3 + summary + transcript)"),
    ("transcript", "Transcript (MP3 + transcript)"),
    ("__back__", "← Back"),
)

# TD-12 dropped MP3-only for an mp3 source (re-encoding only loses quality). Reversed by
# operator request: a re-encode to VBR ~q2 meaningfully SHRINKS an oversized/high-bitrate
# mp3, which is the whole point here — the "mp3" key drives the same _convert_to_mp3 path as
# the video menu (extract_audio re-encodes an mp3 source fine). Shares the semantic keys with
# the video menu so the flow ladder is one shared branch.
_MP3_ACTION_CHOICES: tuple[Choice, ...] = (
    ("summary", "Summary"),
    ("transcript", "Transcript only"),
    ("mp3", "Re-encode to a smaller MP3"),
    ("__back__", "← Back"),
)

# Advisory filter for the native picker (TD-10). The pipeline transcodes anything
# ffmpeg reads, so the trailing "All files" entry keeps an odd-extension input from
# being silently hidden; the in-console fallback ignores this list entirely.
_AV_FILETYPES: tuple[tuple[str, str], ...] = (
    # Derived from the batch whitelist, not hand-listed: the two had already drifted
    # (.avi/.wmv/.flv converted fine from an expanded directory but were hidden by the
    # picker in the very same flow), and a hand-kept copy would drift again.
    ("Audio/Video", " ".join(f"*{suffix}" for suffix in sorted(scan.CONVERTIBLE_SUFFIXES))),
    ("All files", "*.*"),
)


def _pick_folder(ui: UI, prompt: str) -> Path | None:
    """Pick + validate a folder, or None on cancel / a bad path (message already shown).

    One implementation for every folder-shaped flow: the module picks once and hands the
    root down, so a preview and the run that follows it can never end up on different
    folders. TD-10: a native dialog where there is one, a Tab-completing prompt otherwise.
    """
    initialdir = config.resolve_initial_dir(config.load_last_dir())
    raw = ui.pick_dir(prompt, initialdir=initialdir)
    if not raw or not raw.strip():  # dialog Cancel / blank fallback entry → soft cancel
        ui.info("No folder selected; returning to the menu.")
        return None
    root = Path(raw.strip()).expanduser()
    if not root.is_dir():
        ui.error(f"Not a folder: {root}. Check the path and try again.")
        return None
    config.save_last_dir(root)
    return root


def _flow_local_file(deps: Deps) -> None:
    """Menu 'local' — a local audio/video file → {MP3 only · summary · transcript} (§5, TD-12).

    For a video, MP3-only fails loud if extraction fails; on a Summary/Transcript run the MP3
    is a kept-by-default SECONDARY artifact (a per-run confirm, checked by default, lets the
    operator opt out) and degrades — warn + continue — since the paid/primary deliverable
    outranks the audio artifact. An mp3 source skips that keep-question and offers a three-way
    {summary · transcript only · re-encode to a smaller MP3}; the re-encode re-runs extraction
    on the mp3 to shrink an oversized/high-bitrate file.

    The operator picks the file through :meth:`UI.pick_file` (TD-10): a native OS
    dialog where one is available, an in-console Tab-completing prompt otherwise. The
    dialog opens at the last-used directory (or a sensible default); a successful pick
    remembers its parent for next time. A soft cancel (dialog Cancel / blank entry)
    returns to the menu; Ctrl-C/Ctrl-D still exits via the loop's ``EOFError`` contract.
    """
    ui = _ui(deps)
    ui.clear()  # TD-11: start this flow on a clean screen
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()

    initialdir = config.resolve_initial_dir(config.load_last_dir())
    raw = ui.pick_file("Select an audio/video file", filetypes=_AV_FILETYPES, initialdir=initialdir)
    if raw is None:  # dialog Cancel / blank fallback entry → soft cancel
        ui.info("No file selected; returning to the menu.")
        return
    source = _resolve_typed_path(deps, raw)  # F1 bad path → message + None
    if source is None:
        return
    # Remember where the operator picks files (best-effort; swallows OSError so a
    # state-write failure never masks the run). Covers every action below.
    config.save_last_dir(source.parent)

    # TD-12: an mp3 has nothing to extract — re-encoding it would only lose quality — so it
    # gets the trimmed two-way menu (summary vs transcript). A video chooses from the full
    # set where MP3 is the baseline. Both menus share semantic keys, so the ladder below
    # branches on the name, not on which menu produced it.
    is_mp3 = extract.is_mp3(source)
    if is_mp3:
        ui.info(f"{source.name} is already an MP3.")
        action = ui.select("What should EchoGist produce?", _MP3_ACTION_CHOICES)
    else:
        action = ui.select("What should EchoGist produce?", _ACTION_CHOICES)
    if action == "__back__":  # TD-13: back out to the main menu, do nothing
        return

    # MP3 conversion (the one long blocking step, so it runs behind a %/ETA bar off ffmpeg's
    # progress). It is either the deliverable — a video "MP3 only" extraction, or an mp3
    # source re-encoded to a smaller VBR file (operator request) — or a kept-by-default
    # SECONDARY artifact on a video Summary/Transcript run.
    audio_dir = deps.base / "output" / "audio"

    def _convert_to_mp3() -> None:
        label = "Re-encoding MP3" if is_mp3 else "Converting to MP3"
        with ui.progress(label, total=1.0) as bar:
            mp3 = deps.extract_audio(source, audio_dir, progress=bar.advance_to, log=ui.info)
            bar.done()
        ui.success(f"Saved MP3: {mp3}")

    if action == "mp3":
        # The MP3 IS the deliverable (video extraction or an mp3 re-encode), so a failure is
        # fatal — let it bubble to the loop's abort-to-menu handler (nothing else to make).
        _convert_to_mp3()
        ui.reveal_dir(audio_dir, priority=REVEAL_AUDIO)  # TD-14: pop audio, then stop
        return

    # Summary / Transcript. For a video/non-mp3 source the MP3 is a SECONDARY artifact kept
    # BY DEFAULT (operator request, reversing TD-12's silent-baseline): a per-run confirm,
    # checked by default, lets the operator opt out. An mp3 source has nothing to extract, so
    # the question never arises. On the kept path a failed extract DEGRADES (warn + continue):
    # the primary deliverable (paid summary / transcript) outranks the audio artifact, so
    # catch both ExtractError (ffmpeg/F11) and a bare OSError (mkdir / os.replace into a
    # locked output/audio on Windows) here — either would otherwise bubble to the loop's
    # abort-to-menu handler and destroy the primary deliverable.
    if not is_mp3 and ui.confirm("Also save the converted MP3?", default=True):
        try:
            _convert_to_mp3()
        except (ExtractError, OSError) as exc:
            ui.warn(f"Couldn't save the MP3 ({exc}); continuing without the audio artifact.")

    _, text = _transcribe_to_checkpoint(deps, source, model_config)
    if action == "transcript":  # transcript only — the checkpoint is the deliverable
        # TD-12 (Issue 2): reveal the transcripts folder here, in the transcript branch ONLY
        # — never in _transcribe_to_checkpoint, which also runs on the Summary path (that was
        # the original TD-14 bug where a Summary popped transcripts).
        ui.reveal_dir(deps.base / "output" / "transcripts", priority=REVEAL_TRANSCRIPT)
        return
    _run_summary(deps, settings, model_config, text, source, source.stem)


def _selection_bytes(sources: Sequence[Path]) -> int:
    """Total size of the selection; an unreadable entry contributes 0 rather than
    aborting the run — this figure is a courtesy line, never a gate."""
    total = 0
    for path in sources:
        with contextlib.suppress(OSError):
            total += path.stat().st_size
    return total


def _report_mp3(ui: UI, report: FolderReport, *, cancelled: bool) -> None:
    """Render the outcome: one headline, then a table of ONLY what did not convert.

    Tabling all thirty rows would bury the four that need the operator's attention, and
    the converted files are already sitting in the folder that is about to pop open.
    """
    headline = f"Converted {report.done} of {len(report.items)} file(s)"
    parts = [
        f"{count} {label}"
        for count, label in (
            (report.skipped, "skipped"),
            (report.failed, "failed"),
            (report.cancelled, "not started"),
        )
        if count
    ]
    if parts:
        headline += f" — {', '.join(parts)}"
    if cancelled:
        ui.warn(f"Batch stopped. {headline}.")
    elif report.failed:
        ui.warn(f"{headline}.")
    else:
        ui.success(f"{headline}.")

    if report.failures:
        ui.table(
            "Not converted",
            [(item.source.name, item.detail) for item in report.failures],
        )


def _flow_mp3(deps: Deps, picked: list[Path] | None = None) -> None:
    """Folder action 'MP3 only' — convert every file to MP3 (offline, no cost).

    MP3 is the whole deliverable here: no transcription, no summary, nothing paid. The
    operator multi-selects in the native dialog (Shift/Ctrl/Ctrl+A), the pool converts
    ``settings.batch_workers`` at a time, and every file's outcome lands in one report.

    Three behaviours are deliberate and were operator-chosen:

    * **An already-mp3 source is skipped**, unless the operator answers the one question
      that appears only when the selection actually contains mp3s. Re-encoding mp3 is
      lossy-to-lossy, and a Shift-range that swept up a neighbouring mp3 must never
      quietly degrade it — the same reasoning that made "Re-encode to a smaller MP3" a
      deliberate menu item rather than an automatism in the single-file flow.
    * **One bad file does not kill the run.** Failures are collected and tabled at the
      end; the other conversions still land.
    * **Ctrl-C stops the run, not the app.** A deliberate local exception to the global
      Ctrl-C-exits contract: abandoning a twenty-minute run should not also throw away
      the report of what it already produced. Every other prompt in this flow keeps the
      normal contract, since ``FolderCancelled`` is raised only by the pool itself.
    """
    ui = _ui(deps)
    ui.clear()  # TD-11: start this flow on a clean screen
    settings = config.load_settings(deps.settings_path)

    own_pick = picked is None
    if picked is None:
        initialdir = config.resolve_initial_dir(config.load_last_dir())
        raw = ui.pick_files(
            "Select video files to convert to MP3", filetypes=_AV_FILETYPES, initialdir=initialdir
        )
        if not raw:  # dialog Cancel / blank fallback entry → soft cancel
            ui.info("No files selected; returning to the menu.")
            return

        # ``.strip()`` matches _resolve_typed_path: a typed path with trailing whitespace must
        # not fail here while working in the single-file flow.
        picked = [Path(entry.strip()).expanduser() for entry in raw if entry.strip()]
    missing = [path for path in picked if not path.exists()]
    if not picked or missing:  # F1 — a stale/blank path from the console fallback
        if missing:
            ui.error(_F1_NOT_FOUND.format(path=missing[0]))
        return
    sources = folder.expand_selection(picked)
    if not sources:
        ui.warn("Nothing convertible in that selection; returning to the menu.")
        return
    if own_pick:  # the folder module already remembered the folder it handed down
        config.save_last_dir(picked[0] if picked[0].is_dir() else picked[0].parent)

    # The one conditional question (operator decision): it appears only when the choice
    # is real, so a pure-video selection goes straight to converting.
    already_mp3 = [path for path in sources if extract.is_mp3(path)]
    skipped: tuple[folder.Item, ...] = ()
    # Short-circuit: the confirm is only reached when the selection actually holds mp3s,
    # so a pure-video batch never sees the question.
    if already_mp3 and not ui.confirm(
        f"{len(already_mp3)} of the selected files are already MP3. Re-encode those too?",
        default=False,
    ):
        skipped = tuple(
            folder.Item(path, "skipped", detail="already an MP3") for path in already_mp3
        )
        sources = [path for path in sources if not extract.is_mp3(path)]
    if not sources:
        ui.warn("Every selected file is already an MP3; nothing to convert.")
        return

    workers = settings.batch_workers
    mode = "one at a time" if workers == 1 else f"{workers} at a time"
    ui.info(
        f"Converting {len(sources)} file(s), {human_size(_selection_bytes(sources))} total, "
        f"{mode}. Ctrl-C stops the batch and keeps what is already converted."
    )

    audio_dir = deps.base / "output" / "audio"
    cancelled = False
    try:
        with ui.progress("Converting to MP3", total=float(len(sources))) as bar:
            # One aggregate bar over completed FILES, not one bar per file: with several
            # conversions in flight there is no single %/ETA that would be true, and N
            # live bars would need a new multi-task UI seam for no real gain.
            done = 0

            def _tick(item: folder.Item) -> None:
                nonlocal done
                # A cancelled file never ran, so it is not progress. Counting it would
                # walk the bar to a full 100% during the post-Ctrl-C drain — the exact
                # false-completion TD-17 exists to prevent — right before it freezes.
                if item.status == "cancelled":
                    return
                done += 1
                bar.advance_to(float(done))

            report = deps.convert_many(
                sources,
                audio_dir,
                workers=workers,
                # Route through the SAME injected seam the single-file flow uses. Letting
                # convert_many fall back to its own module-level default would quietly fork
                # the two flows: a test stubbing deps.extract_audio would spawn real ffmpeg
                # here, and any future extraction option would apply to menu #1 only.
                extract_fn=deps.extract_audio,
                on_item=_tick,
                extra=skipped,
            )
            bar.done()
    except FolderCancelled as exc:
        # The bar was FAILED, not completed, by the progress context manager (TD-17): a
        # cancelled batch must not flash a false 100% before its own partial report.
        report = exc.report
        cancelled = True

    _report_mp3(ui, report, cancelled=cancelled)
    if report.done:
        ui.reveal_dir(audio_dir, priority=REVEAL_AUDIO)  # TD-14


def _pick_transcript(deps: Deps, directory: Path) -> Path | None:
    """List saved transcripts and let the operator pick one with arrow keys, or type a
    path. Returns the chosen file, or None to cancel / on a bad path (F1)."""
    ui = _ui(deps)
    saved = sorted(directory.glob("*.txt")) if directory.is_dir() else []
    if not saved:
        return _resolve_typed_path(deps, ui.text("Type a transcript path (blank to cancel):"))

    choices: list[Choice] = [(str(i), p.name) for i, p in enumerate(saved)]
    choices.append(("__path__", "Type a path instead…"))
    choices.append(("__cancel__", "← Back"))  # TD-13: consistent back-gesture label
    chosen = ui.select("Pick a saved transcript", choices)
    if chosen == "__cancel__":
        return None
    if chosen == "__path__":
        return _resolve_typed_path(deps, ui.text("Type a transcript path (blank to cancel):"))
    return saved[int(chosen)]


def _flow_saved_transcript(deps: Deps) -> None:
    """Menu 'transcript' — re-summarize a saved transcript (the recovery path, plan §3)."""
    ui = _ui(deps)
    ui.clear()  # TD-11: start this flow on a clean screen
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()

    chosen = _pick_transcript(deps, deps.base / "output" / "transcripts")
    if chosen is None:
        return
    try:
        text = chosen.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # F1 — unreadable file
        ui.error(f"Could not read {chosen}: {exc}.")
        return
    if not text.strip():
        ui.warn(f"{chosen.name} is empty; nothing to summarize.")
        return
    _run_summary(deps, settings, model_config, text, chosen, chosen.stem)


# --------------------------------------------------------------------------- #
# Folder preview
# --------------------------------------------------------------------------- #
# Printed on entry: the preview has no menu row to carry it, and ``Choice`` is a value and
# a ONE-LINE label (``ui.py``) anyway, so a multi-line hint was never renderable there.
_SCAN_HINT = (
    "Walks a folder and every folder under it, counts what is there, and prices the "
    "summaries before you spend anything. Reads only. Writes nothing but its own cache. "
    "Tells you which files share a name, which is the one thing that will silently "
    "corrupt a paid run later."
)


def _found_nothing(result: ScanResult) -> bool:
    """True when the walk turned up no media at all — not even a file it could not read.

    A file that failed to probe still COUNTS as media: MP3-only never needed a duration,
    so an unreadable tree is a report, not a dead end. Shared with :func:`_flow_folder` so
    "nothing here" means one thing in the report and in the decision to stop.
    """
    return not result.files and not result.unreadable and not result.placeholders


def _report_scan(
    ui: UI,
    result: ScanResult,
    model_config: config.ModelConfig,
    tier: config.ModelTier,
    transcripts_dir: Path,
) -> None:
    """Render one scan: the per-folder table, the totals, then the two problem lists."""
    if _found_nothing(result):
        ui.warn("No media files found under that folder.")
        return

    index = scan.transcript_index(transcripts_dir)
    if result.files:
        ui.table("Folders", scan.folder_rows(result, index))
    ui.table("Totals", scan.totals_rows(result, model_config, tier))
    ui.info(
        "The dollar figure is a PROJECTION from duration, biased high (~1.3x the bill on "
        "the one course measured end to end): every file summarized from scratch at the "
        "current tier, nothing already done subtracted. The exact price is quoted from "
        "the real transcripts before anything is paid for."
    )

    duplicates = scan.collision_rows(result)
    if duplicates:
        ui.warn(
            f"{scan.plural(len(duplicates), 'name')} shared by more than one file. Their "
            "artifacts would be named from the same stem, so summarizing both is what "
            "corrupts a paid run."
        )
        ui.table("Duplicate names", duplicates)
    if result.unreadable:
        ui.table("Unreadable", scan.unreadable_rows(result))
    if result.placeholders:
        ui.table("Cloud placeholders (not downloaded, not probed)", scan.placeholder_rows(result))


def _preview_folder(deps: Deps, root: Path) -> ScanResult:
    """Walk a folder and report what is in it. Read-only, offline, free.

    Not an action the operator picks: :func:`_flow_folder` runs this the moment a folder is
    selected, so the library and the price are on screen BEFORE the question about what to
    produce. Nothing here converts, transcribes or summarizes, and no network call is
    possible on this path. The only write is the probe cache under ``output/``, which is
    also what keeps the automatic preview cheap — the run that follows re-walks the same
    tree and reads the same cache.

    Ctrl-C aborts the walk with whatever was already probed, rather than exiting the app:
    a cold scan of a large tree is minutes of ffmpeg spawns, and throwing away both the
    report and the cache for a keystroke would be the same mistake the batch flow already
    refuses to make. The caller still gets the partial result and still asks its question.
    """
    ui = _ui(deps)
    ui.clear()  # TD-11: start this flow on a clean screen
    ui.info(_SCAN_HINT)
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()
    tier = model_config.tier(settings.model_tier)

    # Resolved ONCE, before the walk: a missing or corrupt ffmpeg must fail loud one time,
    # not mark all 500 files unreadable.
    exe = extract.default_ffmpeg_exe()

    cache_path = deps.base / "output" / scan.CACHE_FILENAME
    cancelled = False
    try:
        with ui.spinner("Scanning (Ctrl-C stops and keeps what is already read)") as spin:
            result = scan.scan_tree(
                root, cache_path=cache_path, exe=exe, exclude=deps.base / "output"
            )
            spin.done(message=f"Scanned {len(result.files)} file(s).")
    except ScanCancelled as exc:
        result = exc.result
        cancelled = True

    _report_scan(ui, result, model_config, tier, deps.base / "output" / "transcripts")
    if cancelled:
        ui.warn("Scan cancelled; the numbers above cover only what was read.")
    return result


# --------------------------------------------------------------------------- #
# Folder run (bulk v3 increment 2)
# --------------------------------------------------------------------------- #
# Two hints, because the two modes make DIFFERENT promises. The summary run's hint talks
# about a price and about not re-paying; printing that in front of a free transcript run
# would promise a gate that never comes and a skip that deliberately does not apply.
_BULK_HINT = (
    "Summarizes a whole folder without you sitting through it. Transcribes every file "
    "first (local, free, slow), then shows ONE exact price for the summaries and asks "
    "once. Files already summarized are skipped, not re-paid for. Ctrl-C stops after the "
    "file in flight; everything finished is on disk, so running it again picks up there."
)

_BULK_TRANSCRIBE_HINT = (
    "Transcribes every file in a folder and stops there. Local, free and slow, with no "
    "cloud call on this path at all. Files that already have a transcript are skipped; "
    "a summary bought earlier does NOT skip a file here, because you are asking for the "
    "transcript. Ctrl-C stops after the file in flight; everything finished is on disk."
)

# The folder run DOES extract MP3s, asked once for the whole run (operator, 2026-09-04,
# reversing the earlier rule here). The old reasoning was that a second full pass over
# every file buys an artifact the flow was not asked for, and that the separate
# 'Batch: videos -> MP3' row already produced it on demand. Both halves stopped holding
# when the menu became two symmetric modules: that row is gone, and the folder module
# offers the SAME labels as the single-file one — a label reading 'MP3 + transcript' has
# to produce an MP3. Cost stays visible and refusable: one confirm, defaulted to yes,
# skipped entirely when the folder holds nothing to extract from.


class _StageFailed(Exception):
    """One file's stage failed inside a folder run; the message is already on screen.

    Exists so :func:`echogist.folder.run_phase` can record the file and carry on with the
    rest of the folder. The stage itself already reported the reason inline, right under
    the file name the progress line announced.
    """


def _run_transcript_texts(
    deps: Deps,
    ui: UI,
    plan: folder.Plan,
    model_config: config.ModelConfig,
    *,
    keep_mp3: bool = False,
) -> tuple[dict[Path, str], FolderReport]:
    """Phase 1 — every source that needs one gets a saved transcript. Local, free.

    Returns the transcript text per source (reused sources included, read back from the
    saved file) and the report for the transcribe phase. Raises
    :class:`echogist.folder.FolderCancelled` on Ctrl-C, with the partial report.
    """
    texts: dict[Path, str] = {}
    for source, saved in plan.ready:
        try:
            texts[source] = saved.read_text(encoding="utf-8")
        except OSError as exc:  # readable a moment ago at plan time; re-transcribe instead
            ui.warn(f"Couldn't read {saved.name} ({exc}); transcribing {source.name} again.")
            plan = replace(plan, to_transcribe=plan.to_transcribe + (source,))
    if plan.ready:
        ui.info(f"Reusing {len(texts)} saved transcript(s).")

    audio_dir = deps.base / "output" / "audio"

    def _step(source: Path) -> Path:
        # TD-12 symmetry: the MP3 is a SECONDARY artifact here exactly as it is on the
        # single-file flow, so it DEGRADES — a failed ffmpeg warns and the transcript still
        # lands. Letting it raise would hand the file to run_phase as failed and throw away
        # the free local work that already succeeded. An mp3 source has nothing to extract.
        if keep_mp3 and not extract.is_mp3(source):
            try:
                mp3 = deps.extract_audio(source, audio_dir, log=ui.info)
                ui.info(f"Saved MP3: {mp3.name}")
            except (ExtractError, OSError) as exc:
                ui.warn(f"Couldn't save the MP3 for {source.name} ({exc}); continuing.")
        path, text = _transcribe_to_checkpoint(deps, source, model_config)
        texts[source] = text
        return path

    def _announce(index: int, total: int, source: Path) -> None:
        ui.rule(f"[{index}/{total}] {source.name}")

    report = folder.run_phase(
        plan.to_transcribe,
        _step,
        on_start=_announce,
        recoverable=(TranscribeError, ExtractError, OSError),
    )
    return texts, report


def _flow_run(deps: Deps, root: Path | None = None, *, summarize_after: bool = True) -> None:
    """Folder actions 'Summary' / 'Transcript' — the whole folder, one gate for the run.

    Two phases on purpose (see :mod:`echogist.folder`): TRANSCRIBE the folder first so the
    cost gate is reached with the real transcript of every file, then one exact quote and
    one answer, then the paid calls. No per-file confirm, which is the babysitting this
    flow exists to remove.

    Every early return lands back in the menu with the local work already saved.
    """
    ui = _ui(deps)
    ui.clear()  # TD-11: start this flow on a clean screen
    ui.info(_BULK_HINT if summarize_after else _BULK_TRANSCRIBE_HINT)
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()
    tier = model_config.tier(settings.model_tier)

    # Resolved ONCE, before the walk, for the same reason the scan does it: a missing or
    # corrupt ffmpeg must fail loud one time, not mark every file unreadable.
    exe = extract.default_ffmpeg_exe()

    if root is None:
        picked = _pick_folder(ui, "Select a folder to summarize")
        if picked is None:
            return
        root = picked

    cache_path = deps.base / "output" / scan.CACHE_FILENAME
    try:
        with ui.spinner("Scanning (Ctrl-C stops and keeps what is already read)") as spin:
            result = scan.scan_tree(
                root, cache_path=cache_path, exe=exe, exclude=deps.base / "output"
            )
            spin.done(message=f"Found {len(result.files)} file(s).")
    except ScanCancelled:
        # A partial scan would run a partial folder and quote a partial price, which is
        # exactly the kind of silent half-job this flow must not do.
        ui.warn("Scan cancelled; nothing was transcribed or summarized.")
        return

    summaries_dir = deps.base / "output" / "summaries"
    transcripts_dir = deps.base / "output" / "transcripts"
    # A transcript-only run must NOT skip on "already summarized": the operator is asking
    # for transcripts, and a file whose summary was bought last week may well have had its
    # transcript deleted since. Feeding an empty index leaves plan_run's OTHER skip — a
    # saved transcript already on disk — as the only one, which is exactly the right one
    # here. On a summary run both skips apply, as before (TD-22).
    plan = folder.plan_run(
        [f.path for f in result.files],
        summarized=summarize.summary_index(summaries_dir / "raw") if summarize_after else set(),
        transcripts=scan.transcript_files(transcripts_dir),
    )
    if plan.summarized:
        ui.info(f"Skipping {len(plan.summarized)} file(s) already summarized (not re-paid for).")
    pending = len(plan.ready) + len(plan.to_transcribe)
    if not pending:
        ui.success("Every file in this folder already has a summary. Nothing to do.")
        return
    if not summarize_after and not plan.to_transcribe:
        ui.success(
            f"Every file in this folder already has a transcript "
            f"({len(plan.ready)} on disk). Nothing to do."
        )
        return

    # Same question the single-file flow asks, asked ONCE for the folder: the MP3 is a
    # kept-by-default secondary artifact, and the action label promises it. Skipped when
    # there is nothing to extract from (an all-mp3 folder), so the choice is never fake.
    keep_mp3 = any(not extract.is_mp3(src) for src in plan.to_transcribe) and ui.confirm(
        "Also save the converted MP3 for each file?", default=True
    )

    # Phase 1 — local, free, the long one.
    try:
        with_text, transcribed = _run_transcript_texts(
            deps, ui, plan, model_config, keep_mp3=keep_mp3
        )
    except FolderCancelled as exc:
        _report_run(ui, exc.report, stage="Transcribed", cancelled=True)
        return
    # On a summary run this table is shown ONLY when something failed — the successes speak
    # for themselves in the quote that follows. A transcript-only run always reports below,
    # because there the transcripts ARE the deliverable and there is no quote to follow.
    if transcribed.failed and summarize_after:
        _report_run(ui, transcribed, stage="Transcribed", cancelled=False)

    if not summarize_after:
        # Transcript-only: phase 1 IS the deliverable. Stop before the gate — nothing on
        # this path can reach the wire, which is the whole point of offering it.
        _report_run(ui, transcribed, stage="Transcribed", cancelled=False)
        ui.reveal_dir(transcripts_dir, priority=REVEAL_TRANSCRIPT)
        return

    ordered = sorted(src for src in plan.sources if src in with_text)  # plan order is sorted
    if not ordered:
        ui.error("No transcript survived; nothing to summarize.")
        return

    # The gate — one exact quote over the real transcripts, one answer for the whole run.
    # Quote the SAME text the run will send: _run_summary drops TD-26 boilerplate blocks per
    # file, so pricing the raw transcript here would quote phases that never get summarized.
    # The drops are reported per file during the run, not here — seven blocks of the same
    # greeting listed before the gate is noise in front of the only number that matters.
    per_file = [
        [
            guard.estimate_input_tokens(phase.text)
            for phase in chunk.plan_phases(
                chunk.drop_degenerate_blocks(with_text[src], model_config.chunk)[0],
                model_config.chunk,
            )
        ]
        for src in ordered
    ]
    estimate = folder.folder_estimate(
        per_file,
        tier,
        output_cap=model_config.summarize.max_output_tokens,
        reconcile_floor=model_config.summarize.reconcile_output_floor_tokens,
    )
    calls = sum(len(phases) + 1 for phases in per_file)
    ui.info(f"{len(ordered)} file(s) ready to summarize — {calls} cloud calls in total.")
    ui.info(cost.estimate_message(estimate, tier))
    if tier.prices_unverified:
        ui.warn(
            f"Model generation changed for the '{tier.name}' tier — prices unconfirmed, so "
            "the estimate above may under-state the real bill."
        )
    if not ui.confirm(f"Summarize {len(ordered)} file(s)?", default=False):
        ui.info("Nothing was summarized; your transcripts are saved.")
        return

    # Phase 2 — the paid half. Gated once, above.
    # Keyed by source, not a positional list: the report prints a price PER FILE, and a
    # file that fails after partial spend appends nothing, so position stops matching the
    # file it belongs to the moment anything goes wrong.
    spent: dict[Path, cost.CostEstimate] = {}
    started = monotonic()

    def _summarize_step(source: Path) -> Path:
        def _record(actual: cost.CostEstimate) -> None:
            spent[source] = actual

        out = _run_summary(
            deps,
            settings,
            model_config,
            with_text[source],
            source,
            source.stem,
            gate=False,
            on_cost=_record,
        )
        if out is None:
            raise _StageFailed(f"{source.name} produced no summary (see the message above)")
        return out

    def _announce(index: int, total: int, source: Path) -> None:
        ui.rule(f"[{index}/{total}] {source.name}")

    try:
        summarized = folder.run_phase(
            ordered, _summarize_step, on_start=_announce, recoverable=(_StageFailed,)
        )
    except FolderCancelled as exc:
        _report_run(
            ui,
            exc.report,
            stage="Summarized",
            cancelled=True,
            spent=spent,
            quoted=estimate,
            elapsed=monotonic() - started,
        )
        return
    _report_run(
        ui,
        summarized,
        stage="Summarized",
        cancelled=False,
        spent=spent,
        quoted=estimate,
        elapsed=monotonic() - started,
    )
    if summarized.done:
        ui.reveal_dir(summaries_dir, priority=REVEAL_SUMMARY)


def _usd(amount: float) -> str:
    """USD to 4 decimals — the same shape ``cost`` prints, so the run report and the
    per-file "Actual cost" lines above it are read as the same number, not two roundings."""
    return f"${amount:,.4f}"


def _report_run(
    ui: UI,
    report: FolderReport,
    *,
    stage: str,
    cancelled: bool,
    spent: Mapping[Path, cost.CostEstimate] | None = None,
    quoted: cost.CostEstimate | None = None,
    elapsed: float | None = None,
) -> None:
    """The close of a folder run: what each file cost, then what the run cost.

    A row per FILE, because after an hour away the operator's questions are per file —
    which ones landed, what each one cost — and the console history that would answer them
    is sixty scrolled-past lines. Failures keep their own loud rows underneath; a file that
    broke says so in its row AND in an error line, since a red cell in a seven-row table is
    exactly the thing a tired reader skips.

    The totals close the transaction the gate opened. ``quoted`` is what the operator
    agreed to and ``spent`` is the bill, shown side by side with the ratio between them:
    that ratio is the only feedback the cost model gets, and it is what the tier's
    ``output_per_input_ratio`` is re-seeded from (see ``config/models.toml``).
    """
    spent = spent or {}
    ui.rule(f"{stage} — result")

    # A row per file EARNS its place only when it carries something the scrolled-past
    # console did not: a price, or a failure. On the free transcribe phase — which also
    # reports mid-run, right before the cost gate — seven rows reading "done" would push
    # the one number the operator is about to answer for off the screen.
    if spent or report.failed:
        rows: list[Choice] = []
        for item in report.items:
            if item.status == "failed":
                rows.append((item.source.name, "failed"))
                continue
            # The price IS the success mark on the paid phase — a row with a number in it
            # worked. No glyph: these cells are plain text on a console that may be cp437,
            # and theme.Glyphs (not menu.py) is the only place allowed to pick a symbol.
            price = spent.get(item.source)
            rows.append((item.source.name, _usd(price.total_usd) if price else "done"))
        ui.table("Per file", rows)

    totals: list[Choice] = [(stage, str(report.done))]
    if report.failed:
        totals.append(("Failed", str(report.failed)))
    if elapsed is not None:
        totals.append(("Time", scan.human_hours(elapsed)))
    if spent:
        bill = sum(item.total_usd for item in spent.values())
        totals.append(("Actually spent", _usd(bill)))
        if quoted is not None and bill > 0:
            totals.append(
                (
                    "Quoted before the run",
                    f"{_usd(quoted.total_usd)} ({quoted.total_usd / bill:.2f}x)",
                )
            )
    ui.table("Totals", totals)

    for item in report.failures:
        ui.error(f"{item.source.name}: {item.detail}")
    if cancelled:
        ui.warn(
            f"Cancelled. {report.done} file(s) finished and are saved; "
            "run the folder again to pick up where this stopped."
        )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
_SETTINGS_FIELDS: tuple[Choice, ...] = (
    ("1", "Summary language"),
    ("2", "Output format"),
    ("3", "Model tier"),
    ("4", "Cost confirm threshold"),
    ("5", "Auto-accept at or below threshold"),
    ("6", "Batch MP3 workers"),
    ("__back__", "← Back"),
)


# --------------------------------------------------------------------------- #
# The two modules (single / folder) — one question shape, two input scales
# --------------------------------------------------------------------------- #
# The main menu used to carry five functional rows: three of them (Batch, Scan a folder,
# Summarize a folder) were the SAME module at different settings, and the folder side could
# not produce what the single-file side produced — Batch made an MP3 and nothing else,
# Summarize a folder made a summary and never an MP3. Operator call 2026-09-04: two modules,
# symmetric. What differs between them is only the SCALE of the input; the question they ask
# about the output is the same one, off the same _ACTION_CHOICES.
_SINGLE_SOURCE_CHOICES: tuple[Choice, ...] = (
    ("file", "Audio or video file"),
    ("transcript", "Saved transcript"),
    ("__back__", "← Back"),
)


def _flow_single(deps: Deps) -> None:
    """The single-file module: one recording, or one transcript already on disk.

    A router, deliberately thin — the two flows underneath are unchanged and still own
    their own pickers, because a saved transcript is picked from a list of transcripts
    while a recording is picked from the filesystem, and merging those would help nobody.
    """
    ui = _ui(deps)
    choice = ui.select("What are you starting from?", _SINGLE_SOURCE_CHOICES)
    if choice == "__back__":
        return
    if choice == "file":
        _flow_local_file(deps)
    elif choice == "transcript":
        _flow_saved_transcript(deps)


def _flow_folder(deps: Deps) -> None:
    """The folder module: every file under one folder, same outputs as a single file.

    The folder is picked ONCE, here, and handed to whichever action runs — so the preview
    and the run that follows it are guaranteed to be looking at the same tree, which they
    were not when they were two separate menu rows each with their own picker.

    **The preview is automatic** (operator, 2026-09-05). It used to be the first row of the
    action list, which put the count and the projected price one choice AFTER the decision
    they exist to inform. Now picking a folder runs it, and the action question is asked
    against a report that is already on screen. It is still free, still offline, still the
    only thing here that cannot spend anything — it just is not a thing to choose any more.

    A folder with nothing in it returns to the menu instead of asking what to produce from
    nothing. The remaining actions map onto work that already existed: ``summary`` is the
    two-phase folder run, ``transcript`` is that same run stopped after its free local
    phase, and ``mp3`` is the conversion pool pointed at a folder instead of a hand-picked
    list (``folder.expand_selection`` has always accepted directories).
    """
    ui = _ui(deps)
    ui.clear()  # TD-11: start the module on a clean screen
    # Fail-fast probe, BEFORE the picker: every action in this module shells out to ffmpeg,
    # so a missing or corrupt binary must surface as one loud error rather than after the
    # operator has already chosen a folder (and, deeper in, rather than as 500 "unreadable"
    # files). The flows below resolve it again for their own use; this call only fails.
    extract.default_ffmpeg_exe()
    root = _pick_folder(ui, "Select a folder")
    if root is None:
        return
    result = _preview_folder(deps, root)
    if _found_nothing(result):
        # _report_scan already said so. Asking "what should I produce?" about an empty tree
        # would offer three runs that can only report the same emptiness back.
        return
    action = ui.select(f"What should EchoGist produce for '{root.name}'?", _ACTION_CHOICES)
    if action == "__back__":  # TD-13: back out to the main menu, do nothing
        return
    if action == "mp3":
        _flow_mp3(deps, [root])
    elif action == "transcript":
        _flow_run(deps, root, summarize_after=False)
    elif action == "summary":
        _flow_run(deps, root)


def _flow_settings(deps: Deps) -> None:
    """Menu 'settings' — edit one setting and persist it (validated on save)."""
    ui = _ui(deps)
    ui.clear()  # TD-11: start on a clean screen so repeated edits don't stack tables
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()
    ui.table(
        "Current settings",
        [
            ("Summary language", settings.summary_language),
            ("Output format", settings.output_format),
            ("Model tier", settings.model_tier),
            ("Confirm threshold", f"${settings.confirm_threshold_usd:,.2f}"),
            ("Auto-accept ≤ threshold", "on" if settings.auto_accept_under_threshold else "off"),
            (
                "Batch MP3 workers",
                f"{settings.batch_workers}"
                + (" (sequential)" if settings.batch_workers == 1 else ""),
            ),
        ],
    )

    field_choice = ui.select("Change a setting", _SETTINGS_FIELDS)
    if field_choice == "__back__":
        return

    if field_choice == "1":
        settings.summary_language = ui.select(
            "Summary language", [(v, v) for v in sorted(VALID_LANGUAGES)]
        )
    elif field_choice == "2":
        settings.output_format = ui.select("Output format", [(v, v) for v in sorted(VALID_FORMATS)])
    elif field_choice == "3":
        settings.model_tier = ui.select("Model tier", [(t, t) for t in sorted(model_config.tiers)])
    elif field_choice == "4":
        raw = ui.text("Confirm threshold in USD (>= 0):").strip()
        try:
            threshold = float(raw)
        except ValueError:
            ui.warn(f"'{raw}' isn't a number; unchanged.")
            return
        if threshold < 0:
            ui.warn("Threshold must be >= 0; unchanged.")
            return
        settings.confirm_threshold_usd = threshold
    elif field_choice == "5":
        settings.auto_accept_under_threshold = ui.confirm(
            "Auto-accept summaries whose estimate is at or below the threshold?",
            default=settings.auto_accept_under_threshold,
        )
    elif field_choice == "6":
        # The core count is static help text on the field, not a new prompt or screen: 16
        # workers on a 2-core box is worse than sequential, and nothing here said so.
        cores = os.cpu_count() or 1
        raw = ui.text(
            f"How many files to convert at once (1-{config.MAX_BATCH_WORKERS}; 1 = one at "
            f"a time; this machine has {cores} core{'s' if cores != 1 else ''}):"
        ).strip()
        try:
            workers = int(raw)
        except ValueError:
            ui.warn(f"'{raw}' isn't a whole number; unchanged.")
            return
        if not 1 <= workers <= config.MAX_BATCH_WORKERS:
            ui.warn(f"Enter a number from 1 to {config.MAX_BATCH_WORKERS}; unchanged.")
            return
        settings.batch_workers = workers

    config.save_settings(settings, deps.settings_path)
    ui.success("Saved.")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
# Semantic keys, not digits — the same rule the action submenus already follow (TD-12).
# The operator still picks by pressing 1-9 because that binding is POSITIONAL
# (``ui._bind_number_keys``), so inserting a row re-numbers the keyboard shortcut without
# silently re-pointing any key here: adding "Batch" second did exactly that.
# Two modules, then Settings, then Exit. The five functional rows this replaces are all
# still reachable, one level down, from the module that owns them (see _flow_single /
# _flow_folder). Keys are POSITIONAL under the operator's fingers: Settings moved from 6 to
# 3 and Exit from 7 to 4.
_MAIN_MENU: tuple[Choice, ...] = (
    ("single", "Single file"),
    ("folder", "Folder"),
    ("settings", "Settings"),
    ("exit", "Exit"),
)


def _safe_error(ui: UI, message: str) -> None:
    """Report an error through the UI, falling back to builtin ``print`` if the UI
    adapter itself raises — a broken UI must not crash the loop (plan §6.3)."""
    try:
        ui.error(message)
    except Exception:  # noqa: BLE001 - the UI can't report its own failure through itself
        print(message)


def _session_log_path(base: Path) -> Path:
    """Where this launch records itself: ``output/logs/YYYY-MM-DD-HHMMSS.log``.

    One file per launch rather than one rolling file, so a run can be handed over or
    re-read on its own without slicing a shared log apart. Nothing prunes these: they are
    small plain text next to artifacts measured in gigabytes, and silently deleting the
    record of a paid run is a worse failure than a folder with too many files in it.

    Lives under ``output/`` with the artifacts it describes, which is also why it is named
    here and not in :mod:`echogist.ui` — see TD-27 on that tree's layout as a whole.
    """
    return base / "output" / "logs" / f"{datetime.now():%Y-%m-%d-%H%M%S}.log"


def run_menu(deps: Deps | None = None) -> int:
    """Run the interactive menu until the operator chooses Exit. Returns 0.

    Builds the production UI on first use; with no interactive terminal it prints one
    line and exits cleanly (plan §6.1). Each source flow is wrapped so any recoverable
    stage error (or an unexpected one) is reported and returns to the loop — never a
    crash (CLAUDE.md). A cancel (Ctrl-C / Ctrl-D / closed stdin) surfaces as ``EOFError``
    and exits cleanly, which is also how a piped test run ends.

    Navigation (TD-13): every submenu offers an explicit ``← Back`` entry that returns to
    its parent. ESC is deliberately NOT a back gesture — questionary maps ESC to the same
    ``None`` as Ctrl-C/Ctrl-D, so ESC exits the app (the ``EOFError`` contract above).
    Telling ESC apart from Ctrl-C would need custom prompt_toolkit key bindings on every
    prompt and would put the load-bearing cancel/exit contract at risk; ``← Back`` is the
    intentional, low-risk alternative.
    """
    deps = deps or Deps()
    if deps.ui is None:
        try:
            ui = build_default_ui(_session_log_path(deps.base))
        except NotInteractiveError as exc:
            print(str(exc))
            return 0
        deps = replace(deps, ui=ui)
    ui = _ui(deps)

    handlers: dict[str, Callable[[Deps], None]] = {
        "single": _flow_single,
        "folder": _flow_folder,
        "settings": _flow_settings,
    }
    ui.banner("EchoGist", "local transcription + summary")
    while True:
        try:
            choice = ui.select("Choose an action", _MAIN_MENU)
        except EOFError:  # cancel / closed-piped stdin → exit cleanly
            return 0

        if choice == "exit":
            ui.info("Goodbye.")
            return 0
        handler = handlers.get(choice)
        if handler is None:  # unreachable via select, but stay defensive
            continue
        try:
            handler(deps)
        except EOFError:  # cancel mid-flow → exit cleanly
            return 0
        except _RECOVERABLE as exc:  # carried message, back to menu
            _safe_error(ui, f"{exc}\nReturning to the main menu.")
        except Exception as exc:  # noqa: BLE001 - backstop: never crash the console
            _safe_error(ui, f"Unexpected error: {exc}\nReturning to the main menu.")
        # A flow can hold the console for an hour with nothing on screen to answer. Drop
        # whatever was typed into that silence before re-opening the menu, or the menu
        # answers itself with it — which is exactly what happened after the 2026-09-04
        # folder run: seven finished summaries, then an unasked-for folder picker.
        ui.drain_input()
