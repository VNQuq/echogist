"""T6 — summarize stage (Anthropic, plan §3 / §7). The ONE network stage.

This is the only place EchoGist touches the network, and the killswitch boundary
(CLAUDE.md): everything left of here is offline and key-free. The paid call(s) turn the
timecoded transcript into a structured :class:`Summary` (TD-16 v2: synthesized phases +
anchors) in the operator's language, reading the transcript directly — one hop, no
map-extracted intermediate (see :func:`synthesize_summary`).

Split along two seams so the whole stage is unit-testable with no key, no network,
and no ``anthropic`` installed:

* **Pure half** — :class:`Summary` / :class:`SynthesisSection` and the parsing
  (:func:`_synthesis_section`, F10 title fallback) + the F13 artifact save
  (:func:`save_raw_result`). Total functions over plain dicts/values.
* **The network adapter** — :func:`_default_caller` lazily imports ``anthropic``,
  makes the forced **tool-use** call, maps the SDK's errors to a recoverable
  :class:`SummarizeError` (F2/F4/F5), and hands back a plain :class:`CallOutcome`.
  Injected via the ``caller`` seam, so CI drives success/failure/truncation with a
  stub. The module imports clean offline — ``anthropic`` is never imported at top.

**Structured output = forced tool-use (not prose-JSON).** The model is forced to call
one tool whose ``input_schema`` IS the phase contract, so we get schema-shaped JSON on a
paid call instead of parsing free text. The prompt TEXT is config data
(:attr:`SummarizeConfig.synthesis_system_prompt`); the SCHEMA is code, here, so the two
cannot drift (operator decision 2026-06-15: "prompt = data, schema = code").

**Cap vs projection.** The request's ``max_tokens`` is :attr:`max_output_tokens`
(real headroom for a dense, no-upper-limit RU summary with per-section bullets; the
value itself lives in models.toml, which carries the sizing rationale). The cost
projection is separate: it clamps a call's projected output to this cap but never
uses it as the projection (see :func:`echogist.cost.estimate_cost_synthesis`). A reply that
still hits the cap (``stop_reason == "max_tokens"``) yields
truncated, invalid tool JSON, so it is caught and surfaced (the operator raises the
cap in models.toml) rather than parsed into a half-summary.

**F13 ordering.** :func:`save_raw_result` persists the raw structured result as
``output/summaries/raw/<title>.json`` (no date prefix, plan §3) and is meant to run
BEFORE render — a render failure then never costs a re-pay (re-render from the
``.json``). Exact cost comes from :class:`SummarizeResult` usage (``response.usage``),
never ``count_tokens`` (that is a network call — see the guard).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from . import alphabet, naming
from .chunk import Phase, block_timecodes, plan_phases
from .config import ChunkConfig, ModelTier, SummarizeConfig

#: Progress chatter — the phase-by-phase lines the operator scrolls past. The menu wires
#: this to the muted console channel.
Logger = Callable[[str], object]

#: The OTHER channel: things the operator must actually see. Same shape as
#: :data:`Logger` deliberately, so a caller that does not care passes nothing and both
#: land in the same ``print``. Two named channels rather than a level argument on one:
#: ``Logger`` is redefined in seven modules and ``print`` takes no level kwarg, and there
#: are exactly two audiences here (chatter, and findings), not a spectrum.
Notice = Callable[[str], object]

# Human language names injected into the prompt's {language} token. settings
# validation already restricts the code to these; an unknown code falls back to
# itself so a hand-edited settings file never crashes the stage.
_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}

# TD-16 v2 direct synthesis tool names: one phase pass + one document-header reconcile.
_PHASE_TOOL_NAME = "emit_phase"
_RECONCILE_TOOL_NAME = "emit_reconcile"

# The per-language label for the inline [interpretation]: marker (TD-16 v2): substituted
# into the synthesis prompt's {interpretation} token exactly like {unassigned}. The model
# prefixes any bridge beyond what the author literally said with "[<label>]:", so the
# reader sees author-versus-model at a glance. Unknown code -> English, fail-soft.
_INTERPRETATION_LABELS = {"ru": "интерпретация", "en": "interpretation"}

# The writing systems a summary in this language may legitimately use
# (:func:`echogist.alphabet.script_of` names). Latin rides along with Cyrillic on purpose:
# a Russian lecture legitimately says "coach", "MVP", or an English book title, and the
# rule catches a change of SCRIPT, not a foreign word. An unknown code allows everything
# — fail-soft; flagging every character of a language nobody calibrated would be noise.
# Greek rides along with both for the same reason Latin rides along with Cyrillic: in a
# technical summary α, β, π, Δ, Ω are NOTATION, not a change of writing system, and a
# Russian physics lecture produces them legitimately. Cyrillic rides along with English
# because summarizing a Russian lecture in English is a supported setting
# (``summary_language`` is validated independently of the source), and every faithful
# quotation of the author's own words would otherwise be a finding. That is a real
# tradeoff — an EN reply drifting wholesale back into Russian is now invisible — taken
# deliberately: the measured defect is a CJK morpheme spliced into a word (3x in 7
# lectures), and an instrument that cries at correct text is one the operator stops
# reading. See TD-30.
_ALLOWED_SCRIPTS = {
    "ru": frozenset({"cyrillic", "latin", "greek"}),
    "en": frozenset({"latin", "cyrillic", "greek"}),
}

# How many script findings reach the console before they are counted instead of quoted. A
# stray morpheme is one or two lines; a reply that switched language wholesale would be
# hundreds, and burying the run's own result under them helps nobody.
_MAX_SCRIPT_FINDINGS = 5


class SummarizeError(Exception):
    """A recoverable summarization failure. Print it, return to the menu.

    Every network/billing/model error (F2/F4/F5) and a truncated reply funnel here
    with a human message; the transcript is already saved, so the menu can re-run.
    """


# --------------------------------------------------------------------------- #
# Pure in-memory model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Decision:
    """One decision reached in the material: the decision plus the reasoning.

    The meeting/planning half of the summary. ``rationale`` may be empty when the
    transcript states a decision without spelling out the why. ``anchor`` (TD-16 v2) is
    one ``[HH:MM:SS]`` from the transcript so the operator can jump to where it was
    decided; default empty so a hand-edited/older saved ``.json`` still constructs, and
    validated against the real transcript timecodes by :func:`validate_anchors`.
    """

    decision: str
    rationale: str
    anchor: str = ""


@dataclass(frozen=True)
class ActionItem:
    """One action item in planning format: the task, who owns it, a rough estimate.

    ``owner`` is a name from the transcript or the model's word for "unassigned"
    when none is stated; ``estimate`` is the model's best-effort effort sizing (a
    planning estimate, not a transcript fact). Either may be empty defensively.
    ``anchor`` (TD-16 v2) is one ``[HH:MM:SS]`` from the transcript, validated like a
    decision's; default empty so a hand-edited/older saved ``.json`` still constructs.
    """

    task: str
    owner: str
    estimate: str
    anchor: str = ""


@dataclass(frozen=True)
class CheckQuestion:
    """One self-check question over the WHOLE material, plus its reference answer.

    The essence block's third point. The questions render up front (a reader can try to
    answer them before or after reading); the ``answer`` texts render as a separate
    section at the very END of the document, so seeing a question does not give away its
    answer. Both are written by the reconcile pass from the phase prose only — the same
    grounding rule as everything else — so an answer is a pointer back into the material,
    never new content. ``answer`` defaults empty so a hand-edited/older saved ``.json``
    still constructs; an empty answer simply renders no entry in the answers section.
    """

    question: str
    answer: str = ""


@dataclass(frozen=True)
class SynthesisSection:
    """One synthesized phase (TD-16 v2): a heading, faithful prose, and its anchors.

    The unit of the v2 readable document. ``prose`` is the transcript-grounded synthesis
    of one phase, written directly from the transcript (one hop), and may carry inline
    ``[<interpretation>]:`` markers for any bridge beyond the author's words. ``anchors``
    are the ``[HH:MM:SS]`` timecodes the passage cites, validated against the real
    transcript timecodes (:func:`validate_anchors`) so a "jump to the recording" always
    lands true. No ``dropped_point_indices`` — there is no map-extracted checklist in v2
    (coverage is the manual operator gate), so the section carries only what it asserts.
    """

    heading: str
    prose: str
    anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Summary:
    """The structured summary every downstream stage (render T7) reads (TD-16 v2).

    The readable document is the ordered ``synthesis`` phases (heading + faithful prose
    + validated anchors) plus ``main_themes`` (the reconcile pass's ~5-8 cross-phase
    threads) and the ESSENCE BLOCK — ``core_idea`` + ``main_skill`` + ``test_questions``,
    the 1-2 page digest the reconcile pass writes over the finished phases. ``title`` is
    always non-empty — the F10 fallback fills it when the reconcile/phase returns none.
    ``decisions`` / ``action_items`` are the meeting/planning half, each carrying a
    validated anchor: empty for material (a lecture, a monologue) that has none. The
    pre-v2 map-reduce/grouping fields
    (overview/key_takeaways/section_timecodes/recurring_themes/*_groups) were removed when
    map-reduce retired — v2 reads the transcript directly, one hop, no extracted checklist.

    The essence fields all default empty, so a ``.json`` saved before the block existed
    still loads and re-renders (it simply has no block).
    """

    title: str
    core_idea: str
    decisions: tuple[Decision, ...]
    action_items: tuple[ActionItem, ...]
    language: str
    synthesis: tuple[SynthesisSection, ...] = ()
    main_themes: tuple[str, ...] = ()
    main_skill: str = ""
    test_questions: tuple[CheckQuestion, ...] = ()
    # TD-22 back-link: the resolved source file this summary was made from, so a later
    # run can ask "is this already summarized" and skip work already paid for. NOT an
    # LLM field — the tool schema never sets it; :func:`save_raw_result` stamps it at
    # save time. Empty in every .json written before TD-22 closed, which reads as
    # "unknown source", never as "no summary exists".
    source_path: str = ""


@dataclass(frozen=True)
class CallOutcome:
    """What the ``caller`` seam returns: the SDK details, normalized to plain data.

    ``tool_input`` is the forced tool's JSON input (the raw summary fields);
    ``stop_reason`` lets the pure half catch a truncated reply; the token counts
    are the audited usage for cost (T8).
    """

    tool_input: dict[str, Any]
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class SummarizeResult:
    """The summary plus the exact usage from ``response.usage`` (T8 reads cost)."""

    summary: Summary
    input_tokens: int
    output_tokens: int


# A caller takes the ``messages.create`` request kwargs + the API key and returns
# a normalized CallOutcome. Injectable so tests drive success/error/truncation
# without a key, network, or the anthropic package. Default: :func:`_default_caller`.
Caller = Callable[[dict[str, Any], str], CallOutcome]


# --------------------------------------------------------------------------- #
# Pure request building + parsing
# --------------------------------------------------------------------------- #
def _language_name(code: str) -> str:
    """Map a settings language code to the prompt's human name (ru -> Russian)."""
    return _LANGUAGE_NAMES.get(code, code)


def _str_list(value: Any) -> tuple[str, ...]:
    """Coerce a tool-input array field to a tuple of trimmed, non-empty strings."""
    if not isinstance(value, list):
        return ()
    return tuple(s for s in (str(item).strip() for item in value) if s)


def _decisions(value: Any) -> tuple[Decision, ...]:
    """Coerce the decisions array to :class:`Decision`s; skip entries with no decision."""
    if not isinstance(value, list):
        return ()
    out: list[Decision] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        decision = str(item.get("decision", "")).strip()
        if not decision:  # a decision with no statement is dropped, not faked
            continue
        out.append(
            Decision(
                decision=decision,
                rationale=str(item.get("rationale", "")).strip(),
                # Optional (TD-16 v2): the old emit_summary schema has no anchor key, so
                # this is "" there; the v2 emit_phase schema supplies it.
                anchor=str(item.get("anchor", "")).strip(),
            )
        )
    return tuple(out)


def _action_items(value: Any) -> tuple[ActionItem, ...]:
    """Coerce the action_items array to :class:`ActionItem`s; skip entries with no task."""
    if not isinstance(value, list):
        return ()
    out: list[ActionItem] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip()
        if not task:  # an action item with no task is dropped, not faked
            continue
        out.append(
            ActionItem(
                task=task,
                owner=str(item.get("owner", "")).strip(),
                estimate=str(item.get("estimate", "")).strip(),
                anchor=str(item.get("anchor", "")).strip(),  # optional, "" pre-v2
            )
        )
    return tuple(out)


def _test_questions(value: Any) -> tuple[CheckQuestion, ...]:
    """Coerce the test_questions array to :class:`CheckQuestion`s; skip entries with no
    question. A question with no answer is KEPT (it still renders in the block) — only the
    answers section skips it, so the two never renumber out of step."""
    if not isinstance(value, list):
        return ()
    out: list[CheckQuestion] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:  # an answer with no question is dropped, not faked
            continue
        out.append(CheckQuestion(question=question, answer=str(item.get("answer", "")).strip()))
    return tuple(out)


def _fallback_title(source_stem: str, today: date | None) -> str:
    """F10: model returned no usable title -> ``<source-stem>-<date>`` (named, no crash)."""
    stamp = (today or date.today()).isoformat()
    return f"{naming.sanitize_stem(source_stem, fallback='summary')}-{stamp}"


# --------------------------------------------------------------------------- #
# F13 — persist the raw structured result BEFORE render
# --------------------------------------------------------------------------- #
def save_raw_result(
    summary: Summary,
    out_dir: Path,
    *,
    today: date | None = None,
    source_path: Path | None = None,
) -> Path:
    """Write the summary to ``out_dir/<title>.json`` (no date prefix, deduped). F13.

    Called BEFORE render so a render failure (fpdf2 edge, T7) never costs a re-pay:
    the menu re-renders from this ``.json`` (:func:`echogist.render.load_summary`).
    The title is the filename stem (:func:`naming.summary_stem` — F9 illegal-char
    strip + Windows MAX_PATH truncation + ``-2``/``-3`` dedup). T7's render reuses
    THIS file's stem (``json_path.stem``) for the ``.pdf``/``.md``, so the triplet
    shares one base. ``today`` is unused today but kept for a future dated-summary
    option and signature symmetry with the other artifact saves.

    ``source_path`` is the media/transcript file this summary came from (TD-22). Passing
    it stamps the back-link :func:`summary_index` reads back; omitting it writes the
    pre-TD-22 shape, which indexes as "unknown source".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = naming.summary_stem(summary.title, fallback="summary")
    path = naming.dedup_path(out_dir, stem, ".json")
    if source_path is not None:
        # TD-22: stamped here, not in the summarize call, because the back-link is
        # provenance rather than model output. Resolved through the SAME rule the scan
        # uses for its dedup key, or the two strings never join.
        summary = replace(summary, source_path=str(naming.resolve_source(source_path)))
    # Atomic: this file is what makes a render failure free to retry, and the call that
    # produced it is ALREADY BILLED by the time it is written.
    return naming.publish_text(path, _summary_json(summary))


def summary_index(raw_dir: Path) -> set[str]:
    """Resolved source paths that already have a summary on disk (TD-22).

    The read side of the ``source_path`` back-link: a bulk run over a folder asks this
    which sources are already paid for and skips them, instead of re-quoting and
    re-paying for work already done. Keys are ``str(naming.resolve_source(...))``, the
    same form the scan produces, so the join is exact.

    Deliberately tolerant and never raising: an unreadable, non-JSON or hand-edited file
    is SKIPPED, and a ``.json`` written before TD-22 has no ``source_path`` at all. Every
    such file therefore reads as "no record", so the worst case is re-summarizing
    something already done — visible and merely wasteful. The opposite failure (claiming
    a source is covered when it is not) would silently drop a lecture from a bulk run,
    so this function never guesses a link it cannot read.
    """
    found: set[str] = set()
    try:
        entries = sorted(raw_dir.glob("*.json"))
    except OSError:
        return found
    for entry in entries:
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        source = raw.get("source_path")
        if isinstance(source, str) and source:
            found.add(source)
    return found


def _summary_json(summary: Summary) -> str:
    """Serialize a Summary to pretty UTF-8 JSON (Cyrillic stays literal, not \\u)."""
    return json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n"


def write_summary_json(summary: Summary, path: Path) -> None:
    """Write ``summary`` to ``path`` as UTF-8 JSON, overwriting in place (TD-16 v2).

    The artifact-resume partial-save seam (decision #2): unlike :func:`save_raw_result`
    (which names by title and dedups into ``-2``/``-3``), this writes to a STABLE path so
    a re-run finds the same partial and skips the phases already on it. Reuses the same
    ``_summary_json`` round-trip :func:`echogist.render.load_summary` reads back, so the
    persisted partial reconstructs verbatim. The menu owns the path and the cleanup.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_summary_json(summary), encoding="utf-8")


# --------------------------------------------------------------------------- #
# The network call (lazy anthropic import; covered by stub-driven unit tests)
# --------------------------------------------------------------------------- #
def _extract_tool_input(content: Any, tool_name: str = "") -> dict[str, Any]:
    """Pull the forced tool's input out of the SDK response content blocks.

    ``tool_name`` is the tool the request forced via ``tool_choice``: the synthesis
    path forces ``emit_phase``, the reconcile path forces ``emit_reconcile``. The caller
    is SHARED across both, so we match the block by the forced name rather than a
    hardcoded one — otherwise a valid reconcile tool_use is skipped and the step fails
    loud with a false "no tool call". An empty ``tool_name`` accepts the first tool_use
    block.
    """
    for block in content or ():
        if getattr(block, "type", None) != "tool_use":
            continue
        if tool_name and getattr(block, "name", "") != tool_name:
            continue
        tool_input = getattr(block, "input", None)
        if isinstance(tool_input, dict):
            return tool_input
    raise SummarizeError(
        "The model did not return a structured summary (no tool call). "
        "Your transcript is saved — retry, or pick another model in Settings."
    )


def _api_error_detail(exc: Exception) -> str:
    """Best-effort human-readable reason from an Anthropic API error.

    A 4xx from Anthropic always explains itself — a low credit balance, a bad
    max_tokens, a rejected tool schema, a model the org can't access — in the
    response body (``error.message``) and on ``exc.message`` / ``str(exc)``. We
    surface it so a live failure is diagnosable instead of a bare status code
    (CLAUDE.md "fail loud" means a HUMAN-READABLE message, not just a number).
    Never raises; falls back to a generic phrase. No secret is echoed — Anthropic
    error bodies carry the reason, never the API key.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message", "")).strip()
            if msg:
                return msg
    text = str(getattr(exc, "message", "") or exc).strip()
    return text or "no detail provided by the API"


def _default_caller(request: dict[str, Any], api_key: str) -> CallOutcome:
    """Make the real Anthropic call and normalize it to a :class:`CallOutcome`.

    Lazily imports ``anthropic`` (killswitch — the module imports offline), then
    maps the SDK's failure modes to a recoverable :class:`SummarizeError`:
    F2 (no internet), F4 (rate limit / out of credit), F5 (unknown/deprecated
    model), plus a bad key. The cost comes from ``response.usage`` — never
    ``count_tokens`` (a network call the guard deliberately avoids).
    """
    try:
        import anthropic
    except ImportError as exc:  # installed by run.bat from requirements.lock
        raise SummarizeError(
            "anthropic is not installed — run.bat installs it from requirements.lock."
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(**request)
    except anthropic.APIConnectionError as exc:  # F2
        raise SummarizeError(
            "The summarization step could not reach the API (no internet?). "
            "Your transcript and audio are saved — retry from the saved transcript later."
        ) from exc
    except anthropic.RateLimitError as exc:  # F4
        raise SummarizeError(
            "The API is rate-limited or the account is out of credit. "
            "Your transcript is saved — retry from it later."
        ) from exc
    except anthropic.AuthenticationError as exc:  # bad/expired key
        raise SummarizeError(
            "The API rejected the key. Check ANTHROPIC_API_KEY, then retry from the transcript."
        ) from exc
    except anthropic.NotFoundError as exc:  # F5
        raise SummarizeError(
            f"The model '{request.get('model')}' was not found (deprecated or renamed). "
            "Pick another model in Settings."
        ) from exc
    except anthropic.APIStatusError as exc:  # any other 4xx/5xx incl. billing 400s (F4-adjacent)
        raise SummarizeError(
            f"The API returned an error ({exc.status_code}): {_api_error_detail(exc)} "
            "Your transcript is saved — retry from it later."
        ) from exc
    except anthropic.APIError as exc:  # base class catch-all, still recoverable
        raise SummarizeError(
            f"The summarization call failed: {exc}. Your transcript is saved — retry from it."
        ) from exc

    tool_choice = request.get("tool_choice")
    forced_tool = str(tool_choice.get("name", "") or "") if isinstance(tool_choice, dict) else ""
    usage = response.usage
    return CallOutcome(
        tool_input=_extract_tool_input(response.content, forced_tool),
        stop_reason=str(getattr(response, "stop_reason", "") or ""),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def _tc_seconds(timecode: str) -> float:
    """Parse an ``[HH:MM:SS]`` timecode to seconds for ordering; unparseable -> inf
    (sorts last, never crashes the merge)."""
    digits = timecode.strip().strip("[]").split(":")
    try:
        h, m, s = (int(p) for p in digits)
    except (ValueError, TypeError):
        return float("inf")
    return float(h * 3600 + m * 60 + s)


def summarize_auto(
    transcript_text: str,
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    chunk_cfg: ChunkConfig,
    language: str,
    source_stem: str,
    api_key: str,
    today: date | None = None,
    caller: Caller = _default_caller,
    log: Logger = print,
    notice: Notice = print,
    on_phase: Callable[[Summary], None] | None = None,
    resume_from: Summary | None = None,
) -> SummarizeResult:
    """Phase-split + synthesize the transcript directly (TD-16 v2). The one entry the menu
    calls. ALL material runs through the same path: ``plan_phases`` partitions the
    transcript into a computed K contiguous phases (short material -> K=1, the whole
    transcript in one call), then :func:`synthesize_summary` writes faithful prose with
    anchors. The phase plan is local + deterministic (the same inputs the menu used for its
    cost estimate), so the K that runs matches the price shown. ``on_phase`` / ``resume_from``
    are the artifact-resume seams, forwarded straight through."""
    phases = plan_phases(transcript_text, chunk_cfg)
    return synthesize_summary(
        phases,
        tier,
        cfg,
        language=language,
        source_stem=source_stem,
        api_key=api_key,
        today=today,
        caller=caller,
        log=log,
        notice=notice,
        on_phase=on_phase,
        resume_from=resume_from,
    )


# --------------------------------------------------------------------------- #
# TD-16 v2 — direct transcript synthesis (phase-split -> synthesize -> reconcile)
# --------------------------------------------------------------------------- #
# The v2 path reads the TRANSCRIPT directly (one hop, no map-extracted checklist): the
# transcript is split into contiguous phases (chunk.plan_phases), each synthesized into
# faithful prose with anchors, sequentially and forward-only (each phase sees the prior
# headings + the prior phase's TAIL PROSE as continuity context). A reconcile pass writes
# the document header (title/core_idea/main_themes) from the phase outputs, and a
# deterministic, offline anchor validator snaps or drops every emitted timecode against
# the real transcript timecodes — the load-bearing check behind the manual fidelity gate.
def _interpretation_label(code: str) -> str:
    """The inline interpretation-marker label for ``code`` (ru -> интерпретация).

    Substituted into the synthesis prompt's {interpretation} token. Unknown code falls
    back to English, mirroring :func:`_unassigned_label` / :func:`_language_name`.
    """
    return _INTERPRETATION_LABELS.get(code, "interpretation")


def _phase_tool_schema() -> dict[str, Any]:
    """The forced emit_phase tool: one phase's heading, prose, anchors, decisions, actions."""
    string = {"type": "string"}
    anchor_array = {
        "type": "array",
        "items": string,
        "description": (
            "[HH:MM:SS] timecodes that ACTUALLY APPEAR in this phase's transcript and "
            "anchor this content. Copy only real ones; omit rather than invent."
        ),
    }
    return {
        "name": _PHASE_TOOL_NAME,
        "description": "Return the faithful synthesis of THIS phase of the transcript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "heading": {
                    "type": "string",
                    "description": "Short, specific heading for this phase, in summary language.",
                },
                "prose": {
                    "type": "string",
                    "description": (
                        "Faithful, readable synthesis of THIS phase, in the target language. "
                        "Mark any bridge beyond what the author says with an inline "
                        "[<interpretation>]: token. Ground every sentence in the transcript."
                    ),
                },
                "anchors": anchor_array,
                "decisions": {
                    "type": "array",
                    "description": (
                        "Decisions stated in THIS phase, each with an anchor. Empty if none."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "description": "The decision that was made.",
                            },
                            "rationale": {
                                "type": "string",
                                "description": "Why it was decided.",
                            },
                            "anchor": {
                                "type": "string",
                                "description": "An [HH:MM:SS] from this phase where decided.",
                            },
                        },
                        "required": ["decision", "rationale", "anchor"],
                    },
                },
                "action_items": {
                    "type": "array",
                    "description": (
                        "Next actions stated in THIS phase, each with an anchor. Empty if none."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "The concrete action to take.",
                            },
                            "owner": {
                                "type": "string",
                                "description": "Who is responsible; leave empty if unstated.",
                            },
                            "estimate": {
                                "type": "string",
                                "description": "A rough effort/time estimate.",
                            },
                            "anchor": {
                                "type": "string",
                                "description": "An [HH:MM:SS] from this phase where it came up.",
                            },
                        },
                        "required": ["task", "owner", "estimate", "anchor"],
                    },
                },
            },
            "required": ["heading", "prose", "anchors", "decisions", "action_items"],
        },
    }


def _reconcile_tool_schema() -> dict[str, Any]:
    """The forced emit_reconcile tool: the document header + essence block over the phases."""
    return {
        "name": _RECONCILE_TOOL_NAME,
        "description": (
            "Return the document title, the essence block (core idea, key skill, "
            "self-check questions with answers), and the main themes over the phases."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short, specific, meaningful title for the WHOLE material in the "
                        "target language. No date, no extension, no quotes."
                    ),
                },
                "core_idea": {
                    "type": "string",
                    "description": (
                        "Essence block point 1: the single central point a reader should "
                        "leave with, ~250-350 words. Note any cross-phase contradiction here."
                    ),
                },
                "main_skill": {
                    "type": "string",
                    "description": (
                        "Essence block point 2: the ONE thing the material teaches the "
                        "reader to DO, ~150-200 words — what it is, when to apply it, how "
                        "the author says to do it. Empty only if the material teaches none."
                    ),
                },
                "test_questions": {
                    "type": "array",
                    "description": (
                        "Essence block point 3: exactly 3 questions that check whether the "
                        "reader understood the material, each with a short reference answer."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "One self-check question over the whole material, "
                                    "answerable only by someone who understood it. One "
                                    "sentence; no answer, no hint."
                                ),
                            },
                            "answer": {
                                "type": "string",
                                "description": (
                                    "The reference answer, 2-4 sentences, grounded in the "
                                    "phase passages. It renders far from the question, so "
                                    "it must stand on its own."
                                ),
                            },
                        },
                        "required": ["question", "answer"],
                    },
                },
                "main_themes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The 5-8 threads across the phases, each a short noun phrase.",
                },
                "phase_headings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The phase headings rewritten into ONE coherent outline, exactly one "
                        "per phase, in the SAME order as the phases. Each stays faithful to its "
                        "own phase; only wording/parallelism changes so they read as a meta-frame."
                    ),
                },
            },
            "required": [
                "title",
                "core_idea",
                "main_skill",
                "test_questions",
                "main_themes",
                "phase_headings",
            ],
        },
    }


# How much of the previous phase's prose to carry forward as continuity context. The
# tail (not a model-written thread-line) is the real artifact — honest by construction —
# and is handed forward in a SEPARATE "do not restate" prompt section so the model uses
# it for continuity without re-synthesizing it (the seam-duplication guard).
_PRIOR_TAIL_SENTENCES = 3


def _tail(prose: str, sentences: int = _PRIOR_TAIL_SENTENCES) -> str:
    """The last ``sentences`` sentences of ``prose`` (continuity context, not a summary)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", prose.strip()) if p.strip()]
    return " ".join(parts[-sentences:])


def _prior_context(sections: Sequence[SynthesisSection]) -> str | None:
    """Continuity block for the next phase: earlier headings + the previous phase's tail.

    Returns None before the first phase (nothing precedes it). The block is plain text;
    :func:`build_synthesis_request` wraps it in a clearly delimited, do-not-restate
    section so the model keeps one coherent thread without repeating prior prose.
    """
    if not sections:
        return None
    headings = "\n".join(f"- {s.heading}" for s in sections if s.heading)
    lines = ["Earlier phase headings:", headings or "- (none)"]
    tail = _tail(sections[-1].prose)
    if tail:
        lines += ["", "Tail of the previous phase:", tail]
    return "\n".join(lines)


def build_synthesis_request(
    phase: Phase,
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    language: str,
    prior_context: str | None = None,
) -> dict[str, Any]:
    """The synthesis request for one phase: forced emit_phase, with coherence context.

    Substitutes ``{language}`` and ``{interpretation}`` in the synthesis prompt (like
    :func:`build_request`'s ``{unassigned}``). A "phase N of M" line and the optional
    PRIOR CONTEXT block are prepended to the system prompt (mirroring the map note),
    the PRIOR CONTEXT as a clearly delimited do-not-restate section so it drives
    continuity without being re-synthesized. Pure; no network.
    """
    system = cfg.synthesis_system_prompt.replace("{language}", _language_name(language)).replace(
        "{interpretation}", _interpretation_label(language)
    )
    preamble = f"This is phase {phase.index} of {phase.total} ({phase.span}) of a longer talk."
    if prior_context:
        preamble += (
            "\n\n=== PRIOR CONTEXT (continuity only — do NOT restate or re-summarize) ===\n"
            f"{prior_context}\n=== END PRIOR CONTEXT ==="
        )
    request: dict[str, Any] = {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": f"{preamble}\n\n{system}",
        "messages": [{"role": "user", "content": phase.text}],
        "tools": [_phase_tool_schema()],
        "tool_choice": {"type": "tool", "name": _PHASE_TOOL_NAME},
    }
    if tier.temperature is not None:  # mirror build_request: omit on models that deprecate it
        request["temperature"] = tier.temperature
    return request


def build_reconcile_request(
    sections: Sequence[SynthesisSection], tier: ModelTier, cfg: SummarizeConfig, *, language: str
) -> dict[str, Any]:
    """The reconcile request: title/core_idea/main_themes over the synthesized phases.

    Header-only — it reads the phase prose/headings, NEVER the transcript (re-reading
    would be a second lossy hop). Forced emit_reconcile, ``temperature`` only when set.
    """
    system = cfg.reconcile_system_prompt.replace("{language}", _language_name(language))
    content = "\n\n".join(f"[Phase {i}] {s.heading}\n{s.prose}" for i, s in enumerate(sections, 1))
    request: dict[str, Any] = {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "tools": [_reconcile_tool_schema()],
        "tool_choice": {"type": "tool", "name": _RECONCILE_TOOL_NAME},
    }
    if tier.temperature is not None:
        request["temperature"] = tier.temperature
    return request


def _synthesis_section(tool_input: dict[str, Any]) -> SynthesisSection:
    """Parse one emit_phase reply into a :class:`SynthesisSection` (defensive)."""
    return SynthesisSection(
        heading=str(tool_input.get("heading", "")).strip(),
        prose=str(tool_input.get("prose", "")).strip(),
        anchors=_str_list(tool_input.get("anchors")),
    )


def _snap_anchor(anchor: str, valid_by_sec: dict[float, str], window: float) -> str | None:
    """Resolve one anchor against the real timecodes: exact -> snap -> drop.

    Returns the canonical ``[HH:MM:SS]`` of the matching real block (exact, or the
    nearest within ``window`` seconds — a rounding fix), or None to DROP it (unparseable,
    or no real block within the window — a hallucinated coordinate). Absence beats a
    false coordinate: a dropped anchor is honest, a wrong one poisons trust in all.
    """
    sec = _tc_seconds(anchor)
    if sec == float("inf") or not valid_by_sec:
        return None
    if sec in valid_by_sec:
        return valid_by_sec[sec]
    nearest = min(valid_by_sec, key=lambda v: abs(v - sec))
    return valid_by_sec[nearest] if abs(nearest - sec) <= window else None


# Inline [HH:MM:SS] timecodes the model may weave into prose or the reconcile header. These
# never pass through the validated ``anchors`` array, so they are snapped/dropped here too —
# nothing timecoded reaches the operator without resolving to a real transcript block.
# The optional third field is what makes a two-part ``[MM:SS]`` visible here. It can never
# RESOLVE (``_tc_seconds`` needs three fields, so it scores inf and is dropped), and that is
# the point: a model asked for minute-scale citations writes ``[12:34]``, and before this it
# sailed past the validator into the artifact as a coordinate the operator would click on.
_INLINE_TC_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")


def _make_fixer(
    valid_by_sec: dict[float, str], window: float
) -> tuple[
    Callable[[tuple[str, ...]], tuple[str, ...]],
    Callable[[str], str],
    Callable[[str], str],
    list[int],
]:
    """Anchor-fixing closures bound to one timecode set (accept/snap/drop), sharing counts.

    Returns ``(fix_many, fix_str, strip_inline, stats)`` where ``stats`` is
    ``[exact, snapped, dropped]``. ``fix_many`` cleans + dedups an anchor tuple; ``fix_str``
    cleans a single optional anchor ("" when dropped/absent); ``strip_inline`` snaps/drops
    every ``[HH:MM:SS]`` embedded in free text (prose / header) without collapsing newlines.
    """
    stats = [0, 0, 0]  # exact, snapped, dropped

    def fix_one(anchor: str) -> str | None:
        resolved = _snap_anchor(anchor, valid_by_sec, window)
        if resolved is None:
            stats[2] += 1
        elif resolved == anchor.strip():
            stats[0] += 1
        else:
            stats[1] += 1
        return resolved

    def fix_many(anchors: tuple[str, ...]) -> tuple[str, ...]:
        out: list[str] = []
        for a in anchors:
            r = fix_one(a)
            if r is not None and r not in out:
                out.append(r)
        return tuple(out)

    def fix_str(anchor: str) -> str:
        return (fix_one(anchor) or "") if anchor else ""

    def strip_inline(text: str) -> str:
        if not text:
            return text
        cleaned = _INLINE_TC_RE.sub(lambda m: fix_one(m.group(0)) or "", text)
        # A dropped token can leave a double space; collapse runs of spaces/tabs but keep
        # newlines so multi-paragraph prose still renders as separate paragraphs.
        return re.sub(r"[ \t]{2,}", " ", cleaned)

    return fix_many, fix_str, strip_inline, stats


def report_foreign_scripts(
    summary: Summary,
    language: str,
    *,
    notice: Notice = print,
) -> tuple[alphabet.Finding, ...]:
    """Report every stretch of ``summary`` written in a script ``language`` does not use.

    The second deterministic, offline gate over the model's finished reply, next to
    :func:`validate_anchors`. It walks the SAME prose the operator will read — the title,
    the essence block, every phase heading and its prose, the decisions and actions — and
    never the anchors, which are timecodes rather than language.

    **It reports and returns; it never raises and never alters the summary.** Dropping a
    paid summary over three characters would throw away money already spent for a defect
    the operator can read straight past, and rewriting the model's words to guess at the
    intended one would be exactly the silent fabrication this pipeline exists to prevent.
    The machine states the fact; the human decides — the same division as the anchor gate.

    Returns every finding (the caller may want the count); prints at most
    ``_MAX_SCRIPT_FINDINGS`` of them and then says how many more there were.
    """
    allowed = _ALLOWED_SCRIPTS.get(language, frozenset())
    text = "\n".join(_readable_text(summary))
    findings = alphabet.foreign_findings(text, allowed)
    if not findings:
        return ()
    scripts = ", ".join(sorted({f.script for f in findings}))
    where = "1 place" if len(findings) == 1 else f"{len(findings)} places"
    notice(
        f"Foreign script in the summary ({scripts}), {where} — the model slipped out of "
        f"the target language. The text is kept as written; check it:"
    )
    for finding in findings[:_MAX_SCRIPT_FINDINGS]:
        notice(f"  ...{finding.context}...")
    if len(findings) > _MAX_SCRIPT_FINDINGS:
        notice(f"  ...and {len(findings) - _MAX_SCRIPT_FINDINGS} more.")
    return findings


def _readable_text(summary: Summary) -> tuple[str, ...]:
    """Every field of ``summary`` that becomes prose in the rendered document.

    Anchors are excluded on purpose: ``[00:41:12]`` is a coordinate, already validated by
    :func:`validate_anchors`, and running a language check over it would be a category
    error. Everything else the reader's eye lands on is here.
    """
    parts: list[str] = [summary.title, summary.core_idea, summary.main_skill]
    parts.extend(summary.main_themes)
    for section in summary.synthesis:
        parts.append(section.heading)
        parts.append(section.prose)
    for decision in summary.decisions:
        parts.append(decision.decision)
        parts.append(decision.rationale)
    for action in summary.action_items:
        parts.extend((action.task, action.owner, action.estimate))
    for question in summary.test_questions:
        parts.extend((question.question, question.answer))
    return tuple(part for part in parts if part)


def validate_anchors(
    summary: Summary,
    transcript_text: str,
    *,
    snap_window_seconds: float = 2.0,
    log: Logger = print,
    notice: Notice = print,
) -> Summary:
    """Snap or drop every anchor + inline timecode in ``summary`` against ``transcript_text``.

    The deterministic, offline backbone of the manual fidelity gate (TD-16 v2): the operator
    trusts "jump to the anchor", so any timecode that is not a real transcript block is
    snapped to the nearest one (rounding) or dropped (hallucination) — never left as a false
    coordinate. Runs over section anchors AND each section's prose AND heading, the
    decision/action anchors, AND the reconcile header (core_idea + main_skill + the essence
    block's questions/answers + main_themes), so nothing timecoded reaches
    the operator unvalidated. In synthesis this is applied PER PHASE against that phase's own
    timecodes — a phase anchor that only matches some other phase's block is a hallucination,
    not a citation — and the document header is validated once against the whole transcript.
    Returns a new Summary; logs the exact/snapped/dropped counts. Nothing to validate -> the
    summary is returned unchanged.
    """
    if not (
        summary.title
        or summary.synthesis
        or summary.decisions
        or summary.action_items
        or summary.core_idea
        or summary.main_themes
        or summary.main_skill
        or summary.test_questions
    ):
        return summary
    valid_by_sec = {_tc_seconds(tc): tc for tc in block_timecodes(transcript_text)}
    fix_many, fix_str, strip_inline, stats = _make_fixer(valid_by_sec, snap_window_seconds)
    sections = tuple(
        replace(
            s,
            anchors=fix_many(s.anchors),
            prose=strip_inline(s.prose),
            # A stray [HH:MM:SS] in a heading is an emitted timecode too (esp. a TD-18
            # reconcile-normalized heading): snap/drop it so nothing timecoded renders
            # unvalidated, holding the "every anchor resolves to a real timecode" invariant.
            heading=strip_inline(s.heading),
        )
        for s in summary.synthesis
    )
    decisions = tuple(replace(d, anchor=fix_str(d.anchor)) for d in summary.decisions)
    actions = tuple(replace(a, anchor=fix_str(a.anchor)) for a in summary.action_items)
    title = strip_inline(summary.title)
    core_idea = strip_inline(summary.core_idea)
    main_themes = tuple(strip_inline(t) for t in summary.main_themes)
    # The essence block is reconcile-written free text like core_idea, so a timecode the
    # model copied over from the phase prose is snapped/dropped here too.
    main_skill = strip_inline(summary.main_skill)
    questions = tuple(
        replace(q, question=strip_inline(q.question), answer=strip_inline(q.answer))
        for q in summary.test_questions
    )
    # Routed by what it says, not by where it is printed from. A dropped anchor is a
    # timecode the model invented, which is the one fidelity failure this stage can detect
    # on its own — it must not arrive in the same muted grey as the sixty phase lines.
    line = f"Validated anchors: {stats[0]} exact, {stats[1]} snapped, {stats[2]} dropped."
    (notice if stats[2] else log)(line)
    return replace(
        summary,
        title=title,
        synthesis=sections,
        decisions=decisions,
        action_items=actions,
        core_idea=core_idea,
        main_themes=main_themes,
        main_skill=main_skill,
        test_questions=questions,
    )


def _apply_normalized_headings(
    sections: Sequence[SynthesisSection],
    raw_headings: Any,
    *,
    log: Logger = print,
) -> list[SynthesisSection]:
    """Replace each phase heading with the reconcile pass's normalized outline (TD-18).

    The reconcile call (K>1 only) rewrites the forward-only, independently-worded phase
    headings into ONE coherent meta-frame — one per phase, in order. It already reads all the
    phase prose, re-reads no transcript, and a heading carries no anchored claim, so this is a
    presentation normalization with NO fidelity cost. Fail-soft: applied ONLY when the model
    returns exactly one NON-EMPTY heading per phase; any count/shape mismatch keeps the
    original headings (a wrong-length remap could mislabel a phase) and logs it.
    """
    cleaned = tuple(str(h).strip() for h in raw_headings) if isinstance(raw_headings, list) else ()
    if len(cleaned) != len(sections) or not all(cleaned):
        if sections:
            log("Reconcile returned no usable phase-heading outline; keeping phase headings.")
        return list(sections)
    return [replace(s, heading=h) for s, h in zip(sections, cleaned, strict=True)]


def _running_summary(
    sections: Sequence[SynthesisSection],
    decisions: Sequence[Decision],
    actions: Sequence[ActionItem],
    language: str,
) -> Summary:
    """A partial Summary of the phases synthesized so far (artifact-resume persist seam).

    Carries the per-phase-validated sections/decisions/actions accumulated to this point
    (each already snapped/dropped against its own phase's timecodes as it landed) so a re-run
    can reload it and continue without re-validating the resumed phases. Only the reconcile
    header is written + validated at the end over the full set.
    """
    return Summary(
        title="",
        core_idea="",
        decisions=tuple(decisions),
        action_items=tuple(actions),
        language=language,
        synthesis=tuple(sections),
    )


def synthesize_summary(
    phases: Sequence[Phase],
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    language: str,
    source_stem: str,
    api_key: str,
    today: date | None = None,
    caller: Caller = _default_caller,
    log: Logger = print,
    notice: Notice = print,
    on_phase: Callable[[Summary], None] | None = None,
    resume_from: Summary | None = None,
) -> SummarizeResult:
    """Synthesize ``phases`` sequentially into one transcript-grounded Summary (TD-16 v2).

    K synthesis calls (forward-only: each phase sees the prior headings + the previous
    phase's tail prose for continuity) + 1 reconcile call, ALWAYS — it writes the document
    header, the essence block (core idea / key skill / 3 self-check questions + answers),
    and a normalized phase-heading outline (TD-18, applied fail-soft). K=1 pays for that
    second call too: the block is the point of the document, and skipping it exactly when
    the material is short would make the feature silently absent (operator decision).

    Each phase's anchors (section + inline prose + decisions + actions) are
    validated against THAT phase's own transcript timecodes as it lands (a strict per-phase
    gate); the per-phase decisions/actions are then concatenated (phases are non-overlapping,
    so there is nothing to dedup — collapsing same-worded distinct points would violate
    fidelity property #4). A truncated reply on ANY call fails loud, naming the phase; the
    transcript is already saved.

    **Artifact-resume (decision #2).** ``on_phase`` (if given) fires after each phase with
    the running partial Summary — the menu persists it. ``resume_from`` (a previously
    persisted partial) seeds the already-done phases when its synthesis is a prefix of this
    plan, ``0 < len <= K`` — INCLUDING the complete case (a run that finished every phase but
    died before the durable .json, e.g. reconcile failed): those phases are skipped (no
    re-pay), only the missing phases + reconcile run. A truly stale partial (len > K, the
    transcript/K shrank) or an empty one is ignored — no job engine, just "phase N on disk
    -> skip."
    """
    if not phases:
        raise SummarizeError("No transcript phases to synthesize (empty transcript?).")
    if not any(ph.text.strip() for ph in phases):
        # ``plan_phases`` hands back one phase holding the whole text when no timecoded
        # block parses, and blank text parses to no blocks — so a zero-byte transcript
        # (a silent recording, a truncated write) reached the wire and bought a phase call
        # plus reconcile to summarize nothing. A transcript with no timecodes but real text
        # is NOT refused here: it summarizes fine, it just carries no anchors.
        raise SummarizeError("The transcript is empty; there is nothing to summarize.")
    sections: list[SynthesisSection] = []
    decisions: list[Decision] = []
    actions: list[ActionItem] = []
    done = 0
    # #2: accept a partial up to AND INCLUDING all K phases (``<= len``). A run that
    # synthesized every phase but died before the durable .json (reconcile failure / crash)
    # left a len==K partial; rejecting it would re-run ALL K paid phases. A truly stale
    # partial (len > K — the transcript/K shrank) or an empty one is still ignored.
    if resume_from is not None and 0 < len(resume_from.synthesis) <= len(phases):
        sections = list(resume_from.synthesis)
        decisions = list(resume_from.decisions)
        actions = list(resume_from.action_items)
        done = len(sections)
        log(f"Resuming: {done}/{len(phases)} phases already on disk — skipping them.")
    total_in = total_out = 0
    for ph in phases:
        if ph.index <= done:  # already synthesized in a prior run (resume), no re-pay
            continue
        log(f"Synthesizing phase {ph.index}/{ph.total} ({ph.span})...")
        request = build_synthesis_request(
            ph, tier, cfg, language=language, prior_context=_prior_context(sections)
        )
        outcome = caller(request, api_key)
        if outcome.stop_reason == "max_tokens":
            raise SummarizeError(
                f"Phase {ph.index}/{ph.total} hit the output cap and was cut off. Raise "
                "max_output_tokens (or lower phase_target_tokens) in models.toml, then "
                "retry from the saved transcript."
            )
        # #4: validate THIS phase's anchors against THIS phase's own timecodes. A phase
        # that cites a timecode resolving only to some OTHER phase's block is hallucinating,
        # not citing — per-phase (not whole-transcript) is the strict gate. Inline [HH:MM:SS]
        # woven into the prose is snapped/dropped here too (#3). Persist the validated partial.
        phase_summary = validate_anchors(
            Summary(
                title="",
                core_idea="",
                decisions=_decisions(outcome.tool_input.get("decisions")),
                action_items=_action_items(outcome.tool_input.get("action_items")),
                language=language,
                synthesis=(_synthesis_section(outcome.tool_input),),
            ),
            ph.text,
            log=log,
            notice=notice,
        )
        sections.append(phase_summary.synthesis[0])
        decisions.extend(phase_summary.decisions)
        actions.extend(phase_summary.action_items)
        total_in += outcome.input_tokens
        total_out += outcome.output_tokens
        if on_phase is not None:  # artifact-resume seam (T5 persists the running partial)
            on_phase(_running_summary(sections, decisions, actions, language))

    log("Reconciling the phases into a document header + essence block...")
    rec = caller(build_reconcile_request(sections, tier, cfg, language=language), api_key)
    if rec.stop_reason == "max_tokens":
        raise SummarizeError(
            "The reconcile step hit the output cap. Raise max_output_tokens in "
            "models.toml, then retry from the saved transcript."
        )
    total_in += rec.input_tokens
    total_out += rec.output_tokens
    title = str(rec.tool_input.get("title", "")).strip()
    core_idea = str(rec.tool_input.get("core_idea", "")).strip()
    main_skill = str(rec.tool_input.get("main_skill", "")).strip()
    test_questions = _test_questions(rec.tool_input.get("test_questions"))
    main_themes = _str_list(rec.tool_input.get("main_themes"))
    # TD-18: normalize the forward-only phase headings into one coherent outline.
    sections = _apply_normalized_headings(sections, rec.tool_input.get("phase_headings"), log=log)
    if not title and len(sections) == 1:
        # K=1 degenerate: the sole phase IS the document, so its heading is a better title
        # than the dated source stem when reconcile returned none.
        title = sections[0].heading

    summary = Summary(
        title=title or _fallback_title(source_stem, today),
        core_idea=core_idea,
        # #1: phases are contiguous and NON-overlapping, so there are no overlap-duplicates
        # to merge — concatenate. Cross-phase text dedup would only collapse genuinely
        # distinct same-worded points from different phases (violating fidelity property #4
        # "no merged distinctions") and silently drop the second's anchor.
        decisions=tuple(decisions),
        action_items=tuple(actions),
        language=language,
        synthesis=tuple(sections),
        main_themes=main_themes,
        main_skill=main_skill,
        test_questions=test_questions,
    )
    # Sections/decisions/actions are already per-phase validated above; this final pass
    # validates the reconcile header (core_idea + main_themes) against the whole transcript
    # (#3) and harmlessly re-confirms the already-clean per-phase anchors.
    summary = validate_anchors(summary, "\n".join(ph.text for ph in phases), log=log, notice=notice)
    # Once, over the FINAL summary, rather than per phase: this way the reconcile pass's
    # own prose (the title and the essence block, the freest writing in the document) is
    # covered too, and a resumed run — whose earlier phases were restored from disk and
    # never re-synthesized — is still checked end to end.
    report_foreign_scripts(summary, summary.language, notice=notice)
    log(f"Summary ready: {summary.title}")
    return SummarizeResult(summary=summary, input_tokens=total_in, output_tokens=total_out)
