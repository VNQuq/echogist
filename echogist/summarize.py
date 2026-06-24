"""T6 — summarize stage (Anthropic, plan §3 / §7). The ONE network stage.

This is the only place EchoGist touches the network, and the killswitch boundary
(CLAUDE.md): everything left of here is offline and key-free. The single paid call
turns the timecoded transcript into a structured :class:`Summary` (title + sections)
in the operator's language.

Split along two seams so the whole stage is unit-testable with no key, no network,
and no ``anthropic`` installed:

* **Pure half** — :class:`Summary` / :class:`SectionMarker` and the parsing
  (:func:`_parse_summary`, F10 title fallback) + the F13 artifact save
  (:func:`save_raw_result`). Total functions over plain dicts/values.
* **The network adapter** — :func:`_default_caller` lazily imports ``anthropic``,
  makes the forced **tool-use** call, maps the SDK's errors to a recoverable
  :class:`SummarizeError` (F2/F4/F5), and hands back a plain :class:`CallOutcome`.
  Injected via the ``caller`` seam, so CI drives success/failure/truncation with a
  stub. The module imports clean offline — ``anthropic`` is never imported at top.

**Structured output = forced tool-use (not prose-JSON).** The model is forced to
call one tool whose ``input_schema`` IS the title+sections contract, so we get
schema-shaped JSON on a paid call instead of parsing free text. The prompt TEXT is
config data (:attr:`SummarizeConfig.system_prompt`); the SCHEMA is code, here, so
the two cannot drift (operator decision 2026-06-15: "prompt = data, schema = code").

**Cap vs projection.** The request's ``max_tokens`` is :attr:`max_output_tokens`
(~4096, real Cyrillic headroom) — separate from the ~2K cost projection. A reply
that still hits the cap (``stop_reason == "max_tokens"``) yields truncated, invalid
tool JSON, so it is caught and surfaced rather than parsed into a half-summary.

**F13 ordering.** :func:`save_raw_result` persists the raw structured result as
``output/summaries/raw/<title>.json`` (no date prefix, plan §3) and is meant to run
BEFORE render — a render failure then never costs a re-pay (re-render from the
``.json``). Exact cost comes from :class:`SummarizeResult` usage (``response.usage``),
never ``count_tokens`` (that is a network call — see the guard).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import naming
from .config import ModelTier, SummarizeConfig

Logger = Callable[[str], object]

# The forced-tool name. The model is pinned to call exactly this tool, and its
# input becomes the structured summary.
_TOOL_NAME = "emit_summary"

# Human language names injected into the prompt's {language} token. settings
# validation already restricts the code to these; an unknown code falls back to
# itself so a hand-edited settings file never crashes the stage.
_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


class SummarizeError(Exception):
    """A recoverable summarization failure. Print it, return to the menu.

    Every network/billing/model error (F2/F4/F5) and a truncated reply funnel here
    with a human message; the transcript is already saved, so the menu can re-run.
    """


# --------------------------------------------------------------------------- #
# Pure in-memory model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SectionMarker:
    """One section of the material: a transcript ``[HH:MM:SS]`` and a short title.

    Timecodes are copied from the transcript by the model (the prompt forbids
    inventing them); we keep them as the raw string the model emitted.
    """

    timecode: str
    title: str


@dataclass(frozen=True)
class Decision:
    """One decision reached in the material: the decision plus the reasoning.

    The meeting/planning half of the summary. ``rationale`` may be empty when the
    transcript states a decision without spelling out the why.
    """

    decision: str
    rationale: str


@dataclass(frozen=True)
class ActionItem:
    """One action item in planning format: the task, who owns it, a rough estimate.

    ``owner`` is a name from the transcript or the model's word for "unassigned"
    when none is stated; ``estimate`` is the model's best-effort effort sizing (a
    planning estimate, not a transcript fact). Either may be empty defensively.
    """

    task: str
    owner: str
    estimate: str


@dataclass(frozen=True)
class Summary:
    """The structured summary every downstream stage (render T7) reads.

    Matches the tool-use ``input_schema`` field-for-field, plus ``language`` (the
    code the summary was written in). ``title`` is always non-empty — the F10
    fallback fills it when the model returns none. ``decisions`` / ``action_items``
    are the meeting/planning half: empty for material (a lecture, a monologue) that
    has none.
    """

    title: str
    overview: str
    key_takeaways: tuple[str, ...]
    section_timecodes: tuple[SectionMarker, ...]
    recurring_themes: tuple[str, ...]
    core_idea: str
    decisions: tuple[Decision, ...]
    action_items: tuple[ActionItem, ...]
    language: str


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
# Tool schema (CODE — the contract the parser depends on; must not drift)
# --------------------------------------------------------------------------- #
def _tool_schema() -> dict[str, Any]:
    """The forced-tool definition: the title+sections contract (plan §7).

    Lives in code (not config) so the prompt text can be edited freely without
    silently breaking the parser. Arrays may be empty (e.g. a monologue with no
    clear sections), but every field is required so the model fills them all.
    """
    string = {"type": "string"}
    return {
        "name": _TOOL_NAME,
        "description": "Return the structured summary of the transcript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short, specific, meaningful title in the TARGET summary language "
                        "(not the spoken language). No date, no extension."
                    ),
                },
                "overview": {"type": "string", "description": "2-4 sentences of what it covers."},
                "key_takeaways": {
                    "type": "array",
                    "items": string,
                    "description": "Most important concrete points, one sentence each.",
                },
                "section_timecodes": {
                    "type": "array",
                    "description": "Major sections in order; timecodes copied from the transcript.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "timecode": {
                                "type": "string",
                                "description": "An [HH:MM:SS] that appears in the transcript.",
                            },
                            "title": {"type": "string", "description": "Short section title."},
                        },
                        "required": ["timecode", "title"],
                    },
                },
                "decisions": {
                    "type": "array",
                    "description": (
                        "Concrete decisions reached in the material, in order. "
                        "Empty list if none were made (e.g. a lecture)."
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
                                "description": "Why it was decided / the reasoning given.",
                            },
                        },
                        "required": ["decision", "rationale"],
                    },
                },
                "action_items": {
                    "type": "array",
                    "description": (
                        "Action items in planning format, in order. "
                        "Empty list if the material has none."
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
                                "description": (
                                    "Who is responsible (a name from the transcript), or the "
                                    "word for 'unassigned' in the target language if unstated."
                                ),
                            },
                            "estimate": {
                                "type": "string",
                                "description": "A rough effort/time estimate for the task.",
                            },
                        },
                        "required": ["task", "owner", "estimate"],
                    },
                },
                "recurring_themes": {"type": "array", "items": string},
                "core_idea": {"type": "string", "description": "1-2 sentences: the central point."},
            },
            "required": [
                "title",
                "overview",
                "key_takeaways",
                "section_timecodes",
                "recurring_themes",
                "core_idea",
                "decisions",
                "action_items",
            ],
        },
    }


# --------------------------------------------------------------------------- #
# Pure request building + parsing
# --------------------------------------------------------------------------- #
def _language_name(code: str) -> str:
    """Map a settings language code to the prompt's human name (ru -> Russian)."""
    return _LANGUAGE_NAMES.get(code, code)


def build_request(
    transcript_text: str, tier: ModelTier, cfg: SummarizeConfig, *, language: str
) -> dict[str, Any]:
    """Assemble the ``messages.create`` kwargs (pure; no network).

    ``{language}`` in the config prompt is replaced with ``str.replace`` (not
    ``.format``) so the operator can use literal braces in the prompt text without
    breaking. ``tool_choice`` forces the one tool, suppressing any prose preamble.
    """
    system = cfg.system_prompt.replace("{language}", _language_name(language))
    return {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": transcript_text}],
        "tools": [_tool_schema()],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }


def _str_list(value: Any) -> tuple[str, ...]:
    """Coerce a tool-input array field to a tuple of trimmed, non-empty strings."""
    if not isinstance(value, list):
        return ()
    return tuple(s for s in (str(item).strip() for item in value) if s)


def _markers(value: Any) -> tuple[SectionMarker, ...]:
    """Coerce the section_timecodes array to markers; skip entries missing a timecode."""
    if not isinstance(value, list):
        return ()
    out: list[SectionMarker] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        timecode = str(item.get("timecode", "")).strip()
        if not timecode:  # a section with no real timecode is dropped, not faked
            continue
        out.append(SectionMarker(timecode=timecode, title=str(item.get("title", "")).strip()))
    return tuple(out)


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
        out.append(Decision(decision=decision, rationale=str(item.get("rationale", "")).strip()))
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
            )
        )
    return tuple(out)


def _fallback_title(source_stem: str, today: date | None) -> str:
    """F10: model returned no usable title -> ``<source-stem>-<date>`` (named, no crash)."""
    stamp = (today or date.today()).isoformat()
    return f"{naming.sanitize_stem(source_stem, fallback='summary')}-{stamp}"


def _parse_summary(
    tool_input: dict[str, Any],
    language: str,
    *,
    source_stem: str,
    today: date | None = None,
) -> Summary:
    """Turn the forced tool's JSON input into a :class:`Summary` (defensive, F10).

    The schema requires every field, but we still coerce defensively — a model can
    return an empty title or a malformed array, and a recoverable stage never
    trusts remote shape blindly. An empty/blank title triggers the F10 fallback.
    """
    title = str(tool_input.get("title", "")).strip() or _fallback_title(source_stem, today)
    return Summary(
        title=title,
        overview=str(tool_input.get("overview", "")).strip(),
        key_takeaways=_str_list(tool_input.get("key_takeaways")),
        section_timecodes=_markers(tool_input.get("section_timecodes")),
        recurring_themes=_str_list(tool_input.get("recurring_themes")),
        core_idea=str(tool_input.get("core_idea", "")).strip(),
        decisions=_decisions(tool_input.get("decisions")),
        action_items=_action_items(tool_input.get("action_items")),
        language=language,
    )


# --------------------------------------------------------------------------- #
# F13 — persist the raw structured result BEFORE render
# --------------------------------------------------------------------------- #
def save_raw_result(summary: Summary, out_dir: Path, *, today: date | None = None) -> Path:
    """Write the summary to ``out_dir/<title>.json`` (no date prefix, deduped). F13.

    Called BEFORE render so a render failure (fpdf2 edge, T7) never costs a re-pay:
    the menu re-renders from this ``.json`` (:func:`echogist.render.load_summary`).
    The title is the filename stem (:func:`naming.summary_stem` — F9 illegal-char
    strip + Windows MAX_PATH truncation + ``-2``/``-3`` dedup). T7's render reuses
    THIS file's stem (``json_path.stem``) for the ``.pdf``/``.md``, so the triplet
    shares one base. ``today`` is unused today but kept for a future dated-summary
    option and signature symmetry with the other artifact saves.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = naming.summary_stem(summary.title, fallback="summary")
    path = naming.dedup_path(out_dir, stem, ".json")
    path.write_text(_summary_json(summary), encoding="utf-8")
    return path


def _summary_json(summary: Summary) -> str:
    """Serialize a Summary to pretty UTF-8 JSON (Cyrillic stays literal, not \\u)."""
    return json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------- #
# The network call (lazy anthropic import; covered by stub-driven unit tests)
# --------------------------------------------------------------------------- #
def _extract_tool_input(content: Any) -> dict[str, Any]:
    """Pull the forced tool's input out of the SDK response content blocks."""
    for block in content or ():
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == _TOOL_NAME:
            tool_input = getattr(block, "input", None)
            if isinstance(tool_input, dict):
                return tool_input
    raise SummarizeError(
        "The model did not return a structured summary (no tool call). "
        "Your transcript is saved — retry, or pick another model in Settings."
    )


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
            f"The API returned an error ({exc.status_code}). "
            "Your transcript is saved — retry from it later."
        ) from exc
    except anthropic.APIError as exc:  # base class catch-all, still recoverable
        raise SummarizeError(
            f"The summarization call failed: {exc}. Your transcript is saved — retry from it."
        ) from exc

    usage = response.usage
    return CallOutcome(
        tool_input=_extract_tool_input(response.content),
        stop_reason=str(getattr(response, "stop_reason", "") or ""),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def summarize(
    transcript_text: str,
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    language: str,
    source_stem: str,
    api_key: str,
    today: date | None = None,
    caller: Caller = _default_caller,
    log: Logger = print,
) -> SummarizeResult:
    """Summarize ``transcript_text`` in one structured call (plan §3). The ONE network stage.

    Builds the forced tool-use request, calls through the ``caller`` seam (which
    owns the SDK + error mapping), guards against a truncated reply
    (``stop_reason == "max_tokens"`` -> the cap was hit, the tool JSON is
    incomplete), then parses the result into a :class:`Summary` with the F10
    title fallback. Returns the summary plus the audited token usage for cost (T8).
    The caller surfaces F2/F4/F5 as :class:`SummarizeError`; the menu prints it and
    returns — the transcript is already saved.
    """
    request = build_request(transcript_text, tier, cfg, language=language)
    log(f"Summarizing transcript with {tier.model_id} (language: {_language_name(language)})...")

    outcome = caller(request, api_key)
    if outcome.stop_reason == "max_tokens":
        raise SummarizeError(
            "The model's reply hit the output cap and was cut off, so the summary is "
            "incomplete. Raise max_output_tokens in models.toml (or pick shorter input), "
            "then retry from the saved transcript."
        )

    summary = _parse_summary(outcome.tool_input, language, source_stem=source_stem, today=today)
    log(f"Summary ready: {summary.title}")
    return SummarizeResult(
        summary=summary,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
    )
