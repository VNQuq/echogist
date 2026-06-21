"""T9 — the interactive console menu (plan §3 / §5). Orchestration, not a stage.

The main menu loops until the operator explicitly exits. It wires the pure stages
(extract, transcribe, guard, summarize, render) and the cost flow (T8) around the
three input sources:

* **1. Local file** — audio or video → {summary · MP3 only · both}.
* **2. Saved transcript** — pick a saved ``output/transcripts/*.txt`` or type a
  path; re-summarize it. This is the artifact-based recovery path (plan §3): a
  summarize that failed (F2/F4/F5) re-runs from here without re-transcribing.
* **3. Settings** — edit summary language / output format / model tier / cost
  threshold; persisted to ``settings.json``.
* **4. Exit.**

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

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import config, cost, extract, guard, provision, render, summarize, transcribe
from .config import VALID_FORMATS, VALID_LANGUAGES, ConfigError, Settings
from .extract import ExtractError
from .model_asset import ProvisionError
from .render import RenderError
from .summarize import SummarizeError, SummarizeResult
from .transcribe import TranscribeError, Transcript
from .ui import UI, Choice, NotInteractiveError, build_default_ui

Logger = Callable[[str], object]

# The injectable stage seams. ``...`` arg types keep the alias readable; the real
# functions take keyword-only args a bare Callable cannot spell out, and the
# defaults below pin the production implementations.
ExtractFn = Callable[..., Path]
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

_API_KEY_HELP = (
    "No Anthropic API key found, so the summarization step can't run (F3). Set the "
    "ANTHROPIC_API_KEY environment variable, or put it in config/secrets.toml (copy "
    "config/secrets.toml.example), then re-launch; MP3 extraction works without a key. "
    "Your transcript is saved — re-summarize it from menu option 2 once the key is set."
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
    transcribe: TranscribeFn = transcribe.transcribe
    summarize: SummarizeFn = summarize.summarize
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

    Shared by the transcript prompt (source 2) and the file picker's path entry
    (source 1, TD-10), so the F1 message lives here once."""
    typed = typed.strip()
    if not typed:
        return None
    path = Path(typed).expanduser()
    if not path.is_file():  # F1
        _ui(deps).error(f"File not found: {path}. Check the path and try again.")
        return None
    return path


# --------------------------------------------------------------------------- #
# The one summary sub-flow, shared by every summary-producing path
# --------------------------------------------------------------------------- #
def _run_summary(
    deps: Deps,
    settings: Settings,
    model_config: config.ModelConfig,
    transcript_text: str,
    source_stem: str,
) -> None:
    """GUARD → cost/threshold → the one paid SUMMARIZE call → persist .json → RENDER.

    Pure-stage ordering of plan §3: the local overflow guard (F6) and the cost
    estimate run BEFORE any network touch (killswitch); the raw result is saved to
    ``.json`` BEFORE render so a render failure never costs a re-pay (F13). Every
    early return lands back in the menu loop with the transcript already saved.
    """
    ui = _ui(deps)
    tier = model_config.tier(settings.model_tier)  # ConfigError (F5) → loop backstop

    verdict = guard.check_overflow(transcript_text, tier, model_config.guard)
    if verdict.over_budget:  # F6 — local, offline, before the wire
        ui.warn(guard.overflow_message(verdict, tier))
        return

    api_key = deps.get_api_key()
    if api_key is None:  # F3 — never reach the wire without a key
        ui.warn(_API_KEY_HELP)
        return

    estimate = cost.estimate_cost(verdict.est_input_tokens, tier, model_config.guard)
    ui.info(cost.estimate_message(estimate, tier))

    def _confirm(prompt: str, default: bool) -> bool:  # adapt ui.confirm's kw-only default
        return ui.confirm(prompt, default=default)

    if not cost.confirm_proceed(estimate, settings.confirm_threshold_usd, confirm=_confirm):
        ui.info("Summarization cancelled; your transcript is saved.")
        return

    with ui.spinner("Summarizing (one cloud call)") as sp:
        try:
            result = deps.summarize(
                transcript_text,
                tier,
                model_config.summarize,
                language=settings.summary_language,
                source_stem=source_stem,
                api_key=api_key,
                log=ui.info,
            )
        except SummarizeError as exc:  # F2 / F4 / F5 — message carried by the stage
            sp.done(ok=False, message="Summarization failed")
            ui.error(str(exc))
            return
        sp.done(ok=True, message="Summary received")

    ui.info(cost.actual_message(cost.actual_cost(result, tier)))

    summaries_dir = deps.base / "output" / "summaries"
    # F13: the raw .json goes under summaries/raw/ so summaries/ holds only the
    # readable .pdf/.md; render reuses its stem so the triplet still shares a base.
    json_path = summarize.save_raw_result(result.summary, summaries_dir / "raw")  # BEFORE render
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
        return

    ui.success(f"Done — summary written to {out_path}")


def _transcribe_to_checkpoint(deps: Deps, source: Path, model_config: config.ModelConfig) -> str:
    """Run TRANSCRIBE behind a %/ETA progress bar, save the checkpoint, return its text.

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
        transcript, deps.base / "output" / "transcripts", source.stem
    )
    ui.info(f"Saved transcript: {tpath}")
    ui.reveal_dir(tpath.parent)  # TD-14: pop the transcript folder (Windows, once/launch)
    return transcript.text


# --------------------------------------------------------------------------- #
# Source flows
# --------------------------------------------------------------------------- #
_ACTION_CHOICES: tuple[Choice, ...] = (
    ("1", "Summary"),
    ("2", "MP3 only"),
    ("3", "Both (MP3 + summary)"),
    ("__back__", "← Back"),
)

# TD-12: an mp3 has nothing to extract, so the MP3-only / Both options are dropped —
# but the operator still chooses whether to stop at the saved transcript or go on to a
# summary. "transcript" runs Whisper and keeps the checkpoint without the network call.
_MP3_ACTION_CHOICES: tuple[Choice, ...] = (
    ("1", "Summary"),
    ("transcript", "Transcript only"),
    ("__back__", "← Back"),
)

# Advisory filter for the native picker (TD-10). The pipeline transcodes anything
# ffmpeg reads, so the trailing "All files" entry keeps an odd-extension input from
# being silently hidden; the in-console fallback ignores this list entirely.
_AV_FILETYPES: tuple[tuple[str, str], ...] = (
    ("Audio/Video", "*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus *.mp4 *.mkv *.mov *.webm *.ts"),
    ("All files", "*.*"),
)


def _flow_local_file(deps: Deps) -> None:
    """Source 1 — a local audio/video file → {summary · MP3 only · both} (§5).

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

    # TD-12: an mp3 has nothing to extract — re-encoding it would only lose quality — so
    # it gets a trimmed menu (transcript vs summary, no MP3/Both). Non-mp3 inputs choose
    # from the full set.
    if extract.is_mp3(source):
        ui.info(f"{source.name} is already an MP3.")
        action = ui.select("What should EchoGist produce?", _MP3_ACTION_CHOICES)
    else:
        action = ui.select("What should EchoGist produce?", _ACTION_CHOICES)
    if action == "__back__":  # TD-13: back out to the main menu, do nothing
        return

    if action in ("2", "3"):  # produce the MP3 artifact (only a non-mp3 reaches here)
        mp3 = deps.extract_audio(source, deps.base / "output" / "audio", log=ui.info)
        ui.success(f"Saved MP3: {mp3}")
    if action == "2":  # MP3 only — done
        return

    text = _transcribe_to_checkpoint(deps, source, model_config)
    if action == "transcript":  # transcript only — the checkpoint is the deliverable
        return
    _run_summary(deps, settings, model_config, text, source.stem)


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
    """Source 2 — re-summarize a saved transcript (the recovery path, plan §3)."""
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
    _run_summary(deps, settings, model_config, text, chosen.stem)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
_SETTINGS_FIELDS: tuple[Choice, ...] = (
    ("1", "Summary language"),
    ("2", "Output format"),
    ("3", "Model tier"),
    ("4", "Cost confirm threshold"),
    ("__back__", "← Back"),
)


def _flow_settings(deps: Deps) -> None:
    """Source 3 — edit one setting and persist it (validated on save)."""
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

    config.save_settings(settings, deps.settings_path)
    ui.success("Saved.")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
_MAIN_MENU: tuple[Choice, ...] = (
    ("1", "Local file (audio/video)"),
    ("2", "Saved transcript"),
    ("3", "Settings"),
    ("4", "Exit"),
)


def _safe_error(ui: UI, message: str) -> None:
    """Report an error through the UI, falling back to builtin ``print`` if the UI
    adapter itself raises — a broken UI must not crash the loop (plan §6.3)."""
    try:
        ui.error(message)
    except Exception:  # noqa: BLE001 - the UI can't report its own failure through itself
        print(message)


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
            ui = build_default_ui()
        except NotInteractiveError as exc:
            print(str(exc))
            return 0
        deps = replace(deps, ui=ui)
    ui = _ui(deps)

    handlers: dict[str, Callable[[Deps], None]] = {
        "1": _flow_local_file,
        "2": _flow_saved_transcript,
        "3": _flow_settings,
    }
    ui.banner("EchoGist", "local transcription + summary")
    while True:
        try:
            choice = ui.select("Choose an action", _MAIN_MENU)
        except EOFError:  # cancel / closed-piped stdin → exit cleanly
            return 0

        if choice == "4":
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
