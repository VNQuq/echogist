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

**Fail loud, return to menu (CLAUDE.md).** Every stage error is recoverable: the
flow prints the human-readable message the stage already carried (F1 bad path, F2
no internet, F3 missing key, F4 billing, F5 unknown model, F6 overflow, F11 ffmpeg,
F13 render-after-pay) and returns to the loop. The loop also keeps a broad backstop
so an unexpected error returns to the menu instead of crashing the console.

**Killswitch (CLAUDE.md).** This module orchestrates the one network stage
(SUMMARIZE) but is itself offline at import time — it pulls in no network package
at module top level (``anthropic`` is lazy inside :mod:`echogist.summarize`). Every
collaborator that touches the GPU, the wire, or ffmpeg is injected through
:class:`Deps`, so the whole menu — including the cost/threshold flow and all the
return-to-menu paths — is unit-testable with no model, no key, no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, cost, extract, guard, provision, render, summarize, transcribe
from .config import VALID_FORMATS, VALID_LANGUAGES, ConfigError, Settings
from .extract import ExtractError
from .model_asset import ProvisionError
from .render import RenderError
from .summarize import SummarizeError, SummarizeResult
from .transcribe import TranscribeError, Transcript

Reader = Callable[[str], str]
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

# Minimal ANSI for the banner/headings — whole lines only, so a captured log still
# matches plain substrings. Degrades to visible escapes on a terminal without ANSI
# (modern Windows cmd supports them); cosmetic, never load-bearing.
_CYAN = "\033[1;36m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    """A bold-cyan heading line (plan §9 'colored loop')."""
    return f"{_CYAN}{text}{_RESET}"


_API_KEY_HELP = (
    "ANTHROPIC_API_KEY is not set, so the summarization step can't run (F3). Set it "
    "in your environment and re-launch; MP3 extraction works without a key. Your "
    "transcript is saved — re-summarize it from menu option 2 once the key is set."
)


@dataclass(frozen=True)
class Deps:
    """Injected collaborators. Defaults are the production wiring; tests pass stubs
    for the seams that touch the GPU, the wire, ffmpeg, and the operator's stdin so
    the whole menu runs offline and deterministically (killswitch-safe)."""

    reader: Reader = input
    log: Logger = print
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
# Small console helpers
# --------------------------------------------------------------------------- #
def _ask(deps: Deps, prompt: str) -> str:
    """Read one trimmed line from the operator through the injectable reader."""
    return deps.reader(prompt).strip()


def _model_dir(model_config: config.ModelConfig, base: Path) -> Path:
    """Resolve the Whisper model dir the same way provisioning does (relative to the
    app root unless the configured ``local_dir`` is absolute)."""
    local = Path(model_config.asset.local_dir)
    return local if local.is_absolute() else (base / local)


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
    tier = model_config.tier(settings.model_tier)  # ConfigError (F5) → loop backstop

    verdict = guard.check_overflow(transcript_text, tier, model_config.guard)
    if verdict.over_budget:  # F6 — local, offline, before the wire
        deps.log(guard.overflow_message(verdict, tier))
        return

    api_key = deps.get_api_key()
    if api_key is None:  # F3 — never reach the wire without a key
        deps.log(_API_KEY_HELP)
        return

    estimate = cost.estimate_cost(verdict.est_input_tokens, tier, model_config.guard)
    deps.log(cost.estimate_message(estimate, tier))
    if not cost.confirm_proceed(estimate, settings.confirm_threshold_usd, reader=deps.reader):
        deps.log("Summarization cancelled; your transcript is saved.")
        return

    try:
        result = deps.summarize(
            transcript_text,
            tier,
            model_config.summarize,
            language=settings.summary_language,
            source_stem=source_stem,
            api_key=api_key,
            log=deps.log,
        )
    except SummarizeError as exc:  # F2 / F4 / F5 — message carried by the stage
        deps.log(str(exc))
        return

    deps.log(cost.actual_message(cost.actual_cost(result, tier)))

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
            log=deps.log,
        )
    except RenderError as exc:  # F13 — saved, re-render without re-paying
        deps.log(
            f"{exc}\nThe summary is saved as {json_path.name}; re-render it later "
            "without paying for the call again."
        )
        return

    deps.log(_h(f"Done — summary written to {out_path}"))


# --------------------------------------------------------------------------- #
# Source flows
# --------------------------------------------------------------------------- #
def _flow_local_file(deps: Deps) -> None:
    """Source 1 — a local audio/video file → {summary · MP3 only · both} (§5)."""
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()

    raw = _ask(deps, "Path to the audio/video file: ")
    if not raw:
        deps.log("No path entered; returning to the menu.")
        return
    source = Path(raw).expanduser()
    if not source.is_file():  # F1 — classify→reject, clear message
        deps.log(f"File not found: {source}. Check the path and try again.")
        return

    action = _ask(deps, "Action — [1] summary  [2] MP3 only  [3] both: ")
    if action not in ("1", "2", "3"):
        deps.log("Please choose 1, 2, or 3.")
        return

    if action in ("2", "3"):  # produce the MP3 artifact
        if extract.is_mp3(source):
            deps.log(f"{source.name} is already an MP3; keeping it as-is.")
        else:
            mp3 = deps.extract_audio(source, deps.base / "output" / "audio", log=deps.log)
            deps.log(f"Saved MP3: {mp3}")
    if action == "2":  # MP3 only — done
        return

    model_dir = _model_dir(model_config, deps.base)
    transcript = deps.transcribe(source, model_dir, log=deps.log)
    tpath = transcribe.save_transcript(
        transcript, deps.base / "output" / "transcripts", source.stem
    )
    deps.log(f"Saved transcript: {tpath}")
    _run_summary(deps, settings, model_config, transcript.text, source.stem)


def _pick_transcript(deps: Deps, directory: Path) -> Path | None:
    """List saved transcripts and let the operator pick one by number or type a
    path. Returns the chosen file, or None to cancel / on a bad path (F1)."""
    saved = sorted(directory.glob("*.txt")) if directory.is_dir() else []
    if saved:
        deps.log(_h("Saved transcripts:"))
        for i, p in enumerate(saved, 1):
            deps.log(f"  {i}. {p.name}")
        ans = _ask(deps, "Pick a number, or type a path (blank to cancel): ")
    else:
        ans = _ask(deps, "No saved transcripts. Type a transcript path (blank to cancel): ")
    if not ans:
        return None
    if saved and ans.isdigit():
        idx = int(ans)
        if 1 <= idx <= len(saved):
            return saved[idx - 1]
        deps.log("That number isn't in the list.")
        return None
    path = Path(ans).expanduser()
    if not path.is_file():  # F1
        deps.log(f"File not found: {path}.")
        return None
    return path


def _flow_saved_transcript(deps: Deps) -> None:
    """Source 2 — re-summarize a saved transcript (the recovery path, plan §3)."""
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()

    chosen = _pick_transcript(deps, deps.base / "output" / "transcripts")
    if chosen is None:
        return
    try:
        text = chosen.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # F1 — unreadable file
        deps.log(f"Could not read {chosen}: {exc}.")
        return
    if not text.strip():
        deps.log(f"{chosen.name} is empty; nothing to summarize.")
        return
    _run_summary(deps, settings, model_config, text, chosen.stem)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _settings_text(settings: Settings) -> str:
    """A human-readable snapshot of the current settings."""
    return (
        f"{_DIM}Current settings — "
        f"language: {settings.summary_language} · format: {settings.output_format} · "
        f"tier: {settings.model_tier} · "
        f"confirm threshold: ${settings.confirm_threshold_usd:,.2f}{_RESET}"
    )


def _flow_settings(deps: Deps) -> None:
    """Source 3 — edit one setting and persist it (validated on save)."""
    settings = config.load_settings(deps.settings_path)
    model_config = config.load_model_config()
    deps.log(_settings_text(settings))

    field_choice = _ask(
        deps,
        "Change — [1] language  [2] format  [3] tier  [4] threshold  (blank to go back): ",
    )
    if not field_choice:
        return

    if field_choice == "1":
        val = _ask(deps, f"Summary language {VALID_LANGUAGES}: ").lower()
        if val not in VALID_LANGUAGES:
            deps.log(f"Unknown language '{val}'; unchanged.")
            return
        settings.summary_language = val
    elif field_choice == "2":
        val = _ask(deps, f"Output format {VALID_FORMATS}: ").lower()
        if val not in VALID_FORMATS:
            deps.log(f"Unknown format '{val}'; unchanged.")
            return
        settings.output_format = val
    elif field_choice == "3":
        tiers = sorted(model_config.tiers)
        val = _ask(deps, f"Model tier {tuple(tiers)}: ")
        if val not in model_config.tiers:
            deps.log(f"Unknown tier '{val}'; unchanged.")
            return
        settings.model_tier = val
    elif field_choice == "4":
        val = _ask(deps, "Confirm threshold in USD (>= 0): ")
        try:
            threshold = float(val)
        except ValueError:
            deps.log(f"'{val}' isn't a number; unchanged.")
            return
        if threshold < 0:
            deps.log("Threshold must be >= 0; unchanged.")
            return
        settings.confirm_threshold_usd = threshold
    else:
        deps.log("Please choose 1-4.")
        return

    config.save_settings(settings, deps.settings_path)
    deps.log("Saved.")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
_MAIN_MENU = (
    "1. Local file (audio/video)",
    "2. Saved transcript",
    "3. Settings",
    "4. Exit",
)


def run_menu(deps: Deps | None = None) -> int:
    """Run the interactive menu until the operator chooses Exit. Returns 0.

    Each source flow is wrapped so any recoverable stage error (or an unexpected
    one) prints and returns to the loop — never a crash (CLAUDE.md: fail loud,
    return to menu). A closed stdin (EOF) exits cleanly, which is also how a piped
    test run ends.
    """
    deps = deps or Deps()
    handlers = {
        "1": _flow_local_file,
        "2": _flow_saved_transcript,
        "3": _flow_settings,
    }
    deps.log(_h("EchoGist — local transcription + summary"))
    while True:
        deps.log("")
        for line in _MAIN_MENU:
            deps.log(f"  {line}")
        try:
            choice = _ask(deps, "Choose 1-4: ")
        except EOFError:  # closed/piped stdin → exit cleanly
            deps.log("")
            return 0

        if choice == "4":
            deps.log("Goodbye.")
            return 0
        handler = handlers.get(choice)
        if handler is None:
            deps.log("Please enter 1, 2, 3, or 4.")
            continue
        try:
            handler(deps)
        except EOFError:  # stdin closed mid-flow → exit cleanly
            deps.log("")
            return 0
        except _RECOVERABLE as exc:  # carried message, back to menu
            deps.log(f"{exc}\nReturning to the main menu.")
        except Exception as exc:  # noqa: BLE001 - backstop: never crash the console
            deps.log(f"Unexpected error: {exc}\nReturning to the main menu.")
