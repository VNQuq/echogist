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
(8192 — real headroom for a dense, no-upper-limit RU summary with per-section
bullets) — separate from the :attr:`GuardConfig.output_tokens_estimate` cost
projection. A reply that still hits the cap (``stop_reason == "max_tokens"``) yields
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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from . import naming
from .chunk import Chunk, needs_chunking, plan_chunks, total_duration_seconds
from .config import ChunkConfig, ModelTier, SummarizeConfig
from .guard import estimate_input_tokens

Logger = Callable[[str], object]

# The forced-tool name. The model is pinned to call exactly this tool, and its
# input becomes the structured summary.
_TOOL_NAME = "emit_summary"

# The forced-tool name for the REDUCE/synthesis step (TD-5 map-reduce). It emits only
# the holistic fields (title/overview/core_idea); the list fields are merged
# mechanically and never pass through this call, so the merge cannot drop a point.
_SYNTHESIS_TOOL_NAME = "emit_synthesis"

# The forced-tool name for the GROUPING step (TD-15 Phase 2). It returns headings +
# the INDICES of points under each — never the point text — so groups are rebuilt
# verbatim by index and the grouping can structurally never drop or reword a point.
_GROUPING_TOOL_NAME = "emit_grouping"

# Human language names injected into the prompt's {language} token. settings
# validation already restricts the code to these; an unknown code falls back to
# itself so a hand-edited settings file never crashes the stage.
_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}

# The fixed "unassigned" owner label injected into the prompt's {unassigned} token.
# Pinned here (not free-chosen by the model) so an action_item with no named owner
# gets ONE consistent string per language instead of the model drifting between
# "Не назначено" / "Без ответственного" / "Не указано" across calls. Lives beside
# _LANGUAGE_NAMES — both are per-language prompt-substitution values. Unknown code
# falls back to the English label, mirroring _language_name's fail-soft default.
_UNASSIGNED_LABELS = {"ru": "Не назначено", "en": "Unassigned"}

# The catch-all heading for points the grouping step left unplaced (TD-15 Phase 2).
# Substituted at call time, per language, exactly like {unassigned} — never a hardcoded
# literal, since it prints in the summary's target language. Unknown code -> English.
_CATCHALL_LABELS = {"ru": "Прочее", "en": "Other"}


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
    """One section of the material: a transcript ``[HH:MM:SS]``, a title, and bullets.

    Timecodes are copied from the transcript by the model (the prompt forbids
    inventing them); we keep them as the raw string the model emitted. ``bullets``
    are the section's key points (3-5 for material longer than ~20 min, empty for
    short material) — the structural way to surface section CONTENT instead of just
    a label. Defaults to empty so a section is well-formed with no bullets and older
    callers/tests that pass only timecode+title still construct.
    """

    timecode: str
    title: str
    bullets: tuple[str, ...] = ()


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
class PointGroup:
    """A grouping overlay (TD-15 Phase 2): a heading + the points placed under it.

    ``points`` are reconstructed VERBATIM by index from the canonical flat list — the
    grouping call only assigns indices, it never emits point text — so a group can
    never reword or invent a point. Used for ``takeaway_groups`` / ``theme_groups``.
    """

    heading: str
    points: tuple[str, ...]


@dataclass(frozen=True)
class SectionGroup:
    """A macro-section (TD-15 Phase 2): a heading + the original sections under it.

    The 69 micro-sections of a long lecture are consolidated into a handful of
    time-ordered macro-sections. ``sections`` are the original :class:`SectionMarker`
    s (timecodes + bullets) verbatim, just regrouped — nothing is summarized away.
    """

    heading: str
    sections: tuple[SectionMarker, ...]


@dataclass(frozen=True)
class Summary:
    """The structured summary every downstream stage (render T7) reads.

    Matches the tool-use ``input_schema`` field-for-field, plus ``language`` (the
    code the summary was written in). ``title`` is always non-empty — the F10
    fallback fills it when the model returns none. ``decisions`` / ``action_items``
    are the meeting/planning half: empty for material (a lecture, a monologue) that
    has none.

    ``takeaway_groups`` / ``theme_groups`` / ``section_groups`` are the TD-15 Phase 2
    grouping OVERLAY — additive and default-empty, so a single-pass summary and every
    pre-grouping ``.json`` still construct unchanged. They reorganize the flat lists
    (which stay canonical and complete) into headings for readability; render prefers
    the groups and falls back to the flat list when they are empty. Grouping is
    reconstruct-by-index (see :func:`group_summary`), so the overlay can never endanger
    the completeness of the flat lists.
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
    takeaway_groups: tuple[PointGroup, ...] = ()
    theme_groups: tuple[PointGroup, ...] = ()
    section_groups: tuple[SectionGroup, ...] = ()


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
                "overview": {
                    "type": "string",
                    "description": (
                        "What the material is and what it covers. As many sentences as the "
                        "content needs to be faithful — no upper limit; never truncate."
                    ),
                },
                "key_takeaways": {
                    "type": "array",
                    "items": string,
                    "description": (
                        "EVERY important concrete point, one sentence each. Include ALL of "
                        "them, however many there are — no upper limit; do not stop early or "
                        "collapse distinct points. At least 3 when the material has that many."
                    ),
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
                            "title": {
                                "type": "string",
                                "description": "Short section title, in the target language.",
                            },
                            "bullets": {
                                "type": "array",
                                "items": string,
                                "description": (
                                    "Key points of THIS section, one short sentence each. For "
                                    "material longer than ~20 minutes give 3-5 bullets per "
                                    "section that actually convey its content; for short "
                                    "material an empty list is fine."
                                ),
                            },
                        },
                        "required": ["timecode", "title", "bullets"],
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
                                    "fixed 'unassigned' label given in the prompt if unstated."
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
                "recurring_themes": {
                    "type": "array",
                    "items": string,
                    "description": (
                        "Recurring ideas as short noun phrases. Include ALL that genuinely "
                        "recur — no upper limit; empty list if nothing does."
                    ),
                },
                "core_idea": {
                    "type": "string",
                    "description": (
                        "The single central point a reader should leave with. As many "
                        "sentences as it takes to state it fully; no upper limit."
                    ),
                },
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


def _unassigned_label(code: str) -> str:
    """The fixed 'unassigned' owner label for ``code`` (ru -> Не назначено).

    Unknown code falls back to the English label, so a hand-edited settings file
    never crashes the stage (mirrors :func:`_language_name`'s fail-soft default).
    """
    return _UNASSIGNED_LABELS.get(code, "Unassigned")


def _catchall_heading(code: str) -> str:
    """The grouping catch-all heading for ``code`` (ru -> Прочее). Unknown -> English."""
    return _CATCHALL_LABELS.get(code, "Other")


def build_request(
    transcript_text: str,
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    language: str,
    extra_system: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``messages.create`` kwargs (pure; no network).

    The config prompt's tokens are filled with ``str.replace`` (not ``.format``) so
    the operator can use literal braces in the prompt text without breaking:
    ``{language}`` -> the human language name, ``{unassigned}`` -> the fixed
    no-owner label for that language. ``temperature`` is sent ONLY when the tier
    sets it (``tier.temperature is not None``): the current 4.x models deprecate the
    parameter and 400 if it is present, so it is omitted by default; an older model
    can pin ``temperature = 0`` in models.toml to keep the title (the filename stem
    via :func:`naming.summary_stem`) stable so a re-run overwrites instead of
    deduping into ``-2``/``-3``. ``tool_choice`` forces the one tool, suppressing any
    prose preamble. ``extra_system`` is prepended to the system prompt — the map step
    uses it to mark "this is segment N of M" (TD-5 chunking).
    """
    system = cfg.system_prompt.replace("{language}", _language_name(language)).replace(
        "{unassigned}", _unassigned_label(language)
    )
    if extra_system:
        system = f"{extra_system.strip()}\n\n{system}"
    request: dict[str, Any] = {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": transcript_text}],
        "tools": [_tool_schema()],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }
    if tier.temperature is not None:  # current 4.x models deprecate it -> omitted by default
        request["temperature"] = tier.temperature
    return request


def _str_list(value: Any) -> tuple[str, ...]:
    """Coerce a tool-input array field to a tuple of trimmed, non-empty strings."""
    if not isinstance(value, list):
        return ()
    return tuple(s for s in (str(item).strip() for item in value) if s)


def _markers(value: Any) -> tuple[SectionMarker, ...]:
    """Coerce the section_timecodes array to markers; skip entries missing a timecode.

    ``bullets`` is coerced through :func:`_str_list` (trimmed, empties dropped), so a
    section with no bullets — or a malformed bullets field — degrades to an empty
    tuple rather than crashing the stage.
    """
    if not isinstance(value, list):
        return ()
    out: list[SectionMarker] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        timecode = str(item.get("timecode", "")).strip()
        if not timecode:  # a section with no real timecode is dropped, not faked
            continue
        out.append(
            SectionMarker(
                timecode=timecode,
                title=str(item.get("title", "")).strip(),
                bullets=_str_list(item.get("bullets")),
            )
        )
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
def _extract_tool_input(content: Any, tool_name: str = "") -> dict[str, Any]:
    """Pull the forced tool's input out of the SDK response content blocks.

    ``tool_name`` is the tool the request forced via ``tool_choice``: the MAP /
    single-pass path forces ``emit_summary``, the REDUCE path forces
    ``emit_synthesis``. The caller is SHARED across both, so we match the block by
    the forced name rather than a hardcoded one — otherwise a valid synthesis
    tool_use is skipped and the reduce step fails loud with a false "no tool call".
    An empty ``tool_name`` (no forced tool) accepts the first tool_use block.
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


# --------------------------------------------------------------------------- #
# TD-5 — map-reduce over chunks (the completeness path for long/dense material)
# --------------------------------------------------------------------------- #
# The single-pass `summarize` above stays the MAP primitive (one chunk -> one partial
# Summary). The REDUCE step concatenates and DEDUPS the partials' list fields — it
# never re-summarizes them, so a point a chunk extracted can't be dropped in the merge
# — and a small synthesis call writes only the holistic title/overview/core_idea.
def _reduce_tool_schema() -> dict[str, Any]:
    """The synthesis tool: only the holistic fields, over already-extracted points."""
    return {
        "name": _SYNTHESIS_TOOL_NAME,
        "description": "Return the overall title, overview, and core idea of the whole material.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short, specific, meaningful title for the WHOLE material in the "
                        "target summary language. No date, no extension."
                    ),
                },
                "overview": {
                    "type": "string",
                    "description": (
                        "Faithful overview of the whole material. As many sentences as it "
                        "needs — no upper limit; never truncate."
                    ),
                },
                "core_idea": {
                    "type": "string",
                    "description": "The single central point a reader should leave with.",
                },
            },
            "required": ["title", "overview", "core_idea"],
        },
    }


def build_map_request(
    chunk: Chunk, tier: ModelTier, cfg: SummarizeConfig, *, language: str
) -> dict[str, Any]:
    """The MAP request for one chunk: the normal summary call + a 'segment N of M' note.

    Reuses the full emit_summary contract (so each chunk yields a complete partial
    Summary), with ``cfg.map_note_template`` prepended to the system prompt to tell the
    model this is one segment of a longer transcript and to extract everything.
    """
    note = (
        cfg.map_note_template.replace("{n}", str(chunk.index))
        .replace("{total}", str(chunk.total))
        .replace("{span}", chunk.span)
        .replace("{language}", _language_name(language))
    )
    return build_request(chunk.text, tier, cfg, language=language, extra_system=note)


def build_reduce_request(
    points_text: str, tier: ModelTier, cfg: SummarizeConfig, *, language: str
) -> dict[str, Any]:
    """The REDUCE/synthesis request: title+overview+core_idea over the merged points."""
    system = cfg.reduce_system_prompt.replace("{language}", _language_name(language))
    request: dict[str, Any] = {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": points_text}],
        "tools": [_reduce_tool_schema()],
        "tool_choice": {"type": "tool", "name": _SYNTHESIS_TOOL_NAME},
    }
    if tier.temperature is not None:  # mirror build_request: omit on models that deprecate it
        request["temperature"] = tier.temperature
    return request


def _norm(s: str) -> str:
    """Normalize for exact dedup: lowercased, whitespace-collapsed. Conservative —
    only near-identical strings collide on this key."""
    return " ".join(s.lower().split())


def _tokens(s: str) -> tuple[str, ...]:
    """Word tokens for near-dup matching: lowercased alphanumeric runs, punctuation
    and separators dropped. ``\\w`` is Unicode-aware, so Cyrillic tokenizes like Latin
    (``хочу / надо`` and ``хочу/надо`` both -> ``('хочу', 'надо')``)."""
    return tuple(re.findall(r"\w+", s.lower()))


def _is_sublist(short: tuple[str, ...], whole: tuple[str, ...]) -> bool:
    """True if ``short`` is a contiguous run of tokens inside (and shorter than)
    ``whole``. Token-level, so ``свобода`` never matches inside ``несвобода`` — only
    whole-word containment counts. Generic; the dedup caller adds the length floor."""
    n = len(short)
    if not n or n >= len(whole):
        return False
    return any(whole[i : i + n] == short for i in range(len(whole) - n + 1))


def _dedup_strs(items: Iterable[str]) -> tuple[str, ...]:
    """Concatenate, dropping near-duplicates; order preserved, the fullest copy kept.

    Three mechanical, never-re-summarize rules (TD-15 Phase 1), each a tightening of
    the original exact-(normalized)-match dedup that let visible near-dupes survive a
    real run:

    * **exact** — same text after lowercasing + whitespace-collapse (the original rule).
    * **word-order** — same multiset of word tokens (``хочу/надо/могу`` ==
      ``хочу / могу / надо``). Slash/space/order differences are noise for the short
      noun-phrase themes this runs on.
    * **containment** — one point's words are a contiguous run inside another's
      (``внутренняя свобода`` ⊂ ``внутренняя свобода независимо…``). The longer, more
      complete point wins; the contained restatement is dropped. Floored at two tokens
      so a distinct single-word theme is never swallowed just for sharing one word.

    All three compare WORDS, never meaning — nothing is rewritten or summarized, so a
    genuinely distinct point is never merged away (the TD-5 completeness guarantee).
    """
    kept: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for s in items:
        norm = _norm(s)
        if not norm:
            continue
        toks = _tokens(s)
        skey = tuple(sorted(toks))
        done = False
        for i, (_orig, k_norm, k_toks, k_skey) in enumerate(kept):
            if norm == k_norm:  # exact (normalized)
                done = True
                break
            if toks and skey == k_skey:  # same words, reordered / re-separated
                done = True
                break
            if len(toks) >= 2 and _is_sublist(toks, k_toks):  # contained in a fuller kept point
                done = True
                break
            if len(k_toks) >= 2 and _is_sublist(k_toks, toks):  # this is the fuller version
                kept[i] = (s, norm, toks, skey)  # keep it, drop the contained one, hold position
                done = True
                break
        if not done:
            kept.append((s, norm, toks, skey))
    return tuple(k[0] for k in kept)


def _tc_seconds(timecode: str) -> float:
    """Parse an ``[HH:MM:SS]`` timecode to seconds for ordering; unparseable -> inf
    (sorts last, never crashes the merge)."""
    digits = timecode.strip().strip("[]").split(":")
    try:
        h, m, s = (int(p) for p in digits)
    except (ValueError, TypeError):
        return float("inf")
    return float(h * 3600 + m * 60 + s)


def _merge_sections(markers: Iterable[SectionMarker]) -> tuple[SectionMarker, ...]:
    """Concatenate section markers across chunks, dedup by TIMECODE, union their
    bullets, and order by timecode. No bullet is dropped (bullets are unioned, not
    summarized).

    Keyed on the normalized timecode ALONE, not (timecode, title): the overlap window
    re-feeds a boundary block to two adjacent chunks, and at temperature=0 the model
    can still title that same-timecode section differently in each chunk's context. A
    (timecode, title) key would let those slip through as two sections at one timecode
    with their bullets split — the exact duplication the overlap-dedup exists to
    prevent. The timecode is copied verbatim from the transcript, so it is the stable
    identity of a section; the first title seen wins.
    """
    grouped: dict[str, list[Any]] = {}
    order: list[str] = []
    for m in markers:
        key = _norm(m.timecode)
        if key not in grouped:
            grouped[key] = [m.timecode, m.title, list(m.bullets), {_norm(b) for b in m.bullets}]
            order.append(key)
        else:
            _, _, bullets, seen = grouped[key]
            for b in m.bullets:
                if _norm(b) not in seen:
                    seen.add(_norm(b))
                    bullets.append(b)
    merged = [
        SectionMarker(timecode=grouped[k][0], title=grouped[k][1], bullets=tuple(grouped[k][2]))
        for k in order
    ]
    return tuple(sorted(merged, key=lambda mk: _tc_seconds(mk.timecode)))


def _merge_decisions(decisions: Iterable[Decision]) -> tuple[Decision, ...]:
    """Dedup decisions by their (normalized) statement, preferring the richer copy.

    Overlap can emit the same decision in two adjacent chunks — once with an empty
    rationale, once with one — and chunk order means the emptier copy often comes
    first. So on a collision a missing rationale is back-filled from the later
    duplicate rather than discarded; a present rationale is never overwritten.
    """
    index: dict[str, int] = {}
    out: list[Decision] = []
    for d in decisions:
        key = _norm(d.decision)
        if not key:
            continue
        if key not in index:
            index[key] = len(out)
            out.append(d)
        elif not out[index[key]].rationale and d.rationale:
            kept = out[index[key]]
            out[index[key]] = Decision(decision=kept.decision, rationale=d.rationale)
    return tuple(out)


def _merge_action_items(items: Iterable[ActionItem]) -> tuple[ActionItem, ...]:
    """Dedup action items by their (normalized) task, preferring the richer copy.

    Like :func:`_merge_decisions`: on a collision a missing owner or estimate is
    back-filled from the later duplicate (overlap can emit the same task twice, the
    emptier copy often first); a value already present is never overwritten.
    """
    index: dict[str, int] = {}
    out: list[ActionItem] = []
    for a in items:
        key = _norm(a.task)
        if not key:
            continue
        if key not in index:
            index[key] = len(out)
            out.append(a)
        else:
            kept = out[index[key]]
            owner = kept.owner or a.owner
            estimate = kept.estimate or a.estimate
            if (owner, estimate) != (kept.owner, kept.estimate):
                out[index[key]] = ActionItem(task=kept.task, owner=owner, estimate=estimate)
    return tuple(out)


def _serialize_points(
    takeaways: tuple[str, ...],
    sections: tuple[SectionMarker, ...],
    decisions: tuple[Decision, ...],
    action_items: tuple[ActionItem, ...],
    themes: tuple[str, ...],
) -> str:
    """Render the merged points as plain text for the reduce/synthesis call's input.

    The synthesis model reads these to write a title/overview/core_idea; it does NOT
    re-emit them, so the format only needs to be legible, not machine-parseable.
    """
    lines: list[str] = ["KEY POINTS:"]
    lines += [f"- {t}" for t in takeaways] or ["- (none)"]
    if sections:
        lines.append("\nSECTIONS:")
        for m in sections:
            lines.append(f"- {m.timecode} {m.title}".rstrip())
            lines += [f"  - {b}" for b in m.bullets]
    if decisions:
        lines.append("\nDECISIONS:")
        lines += [f"- {_decision_for_points(d)}" for d in decisions]
    if action_items:
        lines.append("\nACTION ITEMS:")
        lines += [f"- {a.task}" for a in action_items]
    if themes:
        lines.append("\nRECURRING THEMES:")
        lines += [f"- {t}" for t in themes]
    return "\n".join(lines)


def _decision_for_points(d: Decision) -> str:
    return f"{d.decision} — {d.rationale}" if d.rationale else d.decision


def summarize_chunked(
    transcript_text: str,
    tier: ModelTier,
    cfg: SummarizeConfig,
    chunks: Sequence[Chunk],
    *,
    language: str,
    source_stem: str,
    api_key: str,
    today: date | None = None,
    caller: Caller = _default_caller,
    log: Logger = print,
) -> SummarizeResult:
    """Map-reduce summarize over pre-planned ``chunks`` (TD-5). N map calls + 1 reduce.

    MAP: each chunk -> a full partial Summary via the same forced-tool contract.
    MERGE: the partials' list fields are concatenated and conservatively deduped — a
    point any chunk extracted survives verbatim into the result. REDUCE: a synthesis
    call writes only title/overview/core_idea over the merged points. Token usage is
    summed across every call. ``transcript_text`` is unused for the calls (the chunks
    carry the text) but kept in the signature for symmetry with :func:`summarize`.
    A truncated reply on ANY call fails loud, naming the segment, transcript saved.
    """
    partials: list[Summary] = []
    total_in = total_out = 0
    for ch in chunks:
        log(f"Summarizing segment {ch.index}/{ch.total} ({ch.span})...")
        outcome = caller(build_map_request(ch, tier, cfg, language=language), api_key)
        if outcome.stop_reason == "max_tokens":
            raise SummarizeError(
                f"Segment {ch.index}/{ch.total} hit the output cap and was cut off. "
                "Raise max_output_tokens (or lower target_chunk_tokens) in models.toml, "
                "then retry from the saved transcript."
            )
        partials.append(_parse_summary(outcome.tool_input, language, source_stem=source_stem))
        total_in += outcome.input_tokens
        total_out += outcome.output_tokens

    takeaways = _dedup_strs(t for p in partials for t in p.key_takeaways)
    themes = _dedup_strs(t for p in partials for t in p.recurring_themes)
    sections = _merge_sections(m for p in partials for m in p.section_timecodes)
    decisions = _merge_decisions(d for p in partials for d in p.decisions)
    action_items = _merge_action_items(a for p in partials for a in p.action_items)

    # The acceptance invariant (operator decision): map-stage idea count vs post-merge.
    # Conservative dedup makes a sharp collapse structurally impossible; surfacing the
    # numbers makes a regressive "meat-grinder" reduce visible on every run.
    extracted = sum(len(p.key_takeaways) for p in partials)
    log(
        f"Merged {len(chunks)} segments: {extracted} key points extracted "
        f"-> {len(takeaways)} after dedup ({len(sections)} sections, "
        f"{len(decisions)} decisions, {len(action_items)} actions)."
    )

    points_text = _serialize_points(takeaways, sections, decisions, action_items, themes)
    log("Synthesizing overall title, overview, and core idea...")
    syn = caller(build_reduce_request(points_text, tier, cfg, language=language), api_key)
    if syn.stop_reason == "max_tokens":
        raise SummarizeError(
            "The synthesis step hit the output cap. Raise max_output_tokens in "
            "models.toml, then retry from the saved transcript."
        )
    total_in += syn.input_tokens
    total_out += syn.output_tokens

    title = str(syn.tool_input.get("title", "")).strip() or _fallback_title(source_stem, today)
    summary = Summary(
        title=title,
        overview=str(syn.tool_input.get("overview", "")).strip(),
        key_takeaways=takeaways,
        section_timecodes=sections,
        recurring_themes=themes,
        core_idea=str(syn.tool_input.get("core_idea", "")).strip(),
        decisions=decisions,
        action_items=action_items,
        language=language,
    )
    log(f"Summary ready: {summary.title}")
    return SummarizeResult(summary=summary, input_tokens=total_in, output_tokens=total_out)


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
) -> SummarizeResult:
    """Dispatch single-pass vs map-reduce on the QualityBudget (TD-5). The one entry
    the menu calls. Chunking decision is local + deterministic (same inputs the menu
    used for its cost estimate), so the path that runs matches the price shown."""
    est = estimate_input_tokens(transcript_text)
    duration = total_duration_seconds(transcript_text)
    if needs_chunking(est, duration, chunk_cfg):
        chunks = plan_chunks(transcript_text, chunk_cfg)
        log(f"Long/dense transcript: summarizing in {len(chunks)} segments (map-reduce).")
        return summarize_chunked(
            transcript_text,
            tier,
            cfg,
            chunks,
            language=language,
            source_stem=source_stem,
            api_key=api_key,
            today=today,
            caller=caller,
            log=log,
        )
    return summarize(
        transcript_text,
        tier,
        cfg,
        language=language,
        source_stem=source_stem,
        api_key=api_key,
        today=today,
        caller=caller,
        log=log,
    )


# --------------------------------------------------------------------------- #
# TD-15 Phase 2 — hierarchical grouping (assign by index, reconstruct verbatim)
# --------------------------------------------------------------------------- #
# Grouping ASSIGNS points to headings; it never REWRITES them. The grouping call sees
# numbered flat lists and returns headings + the 1-based indices under each; the point
# text is taken only from the original lists, so grouping structurally cannot reword,
# merge, or invent a point. A completeness invariant places every index exactly once;
# any index the model forgets falls into a language-aware catch-all, logged like the
# TD-5 `extracted -> after dedup` line. The flat lists stay canonical; groups are an
# additive overlay (render falls back to the flat list if grouping returns nothing).
_T = TypeVar("_T")


def _grouping_tool_schema() -> dict[str, Any]:
    """The forced grouping tool: headings + INDICES for each flat list (never text)."""
    group_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "heading": {
                    "type": "string",
                    "description": "Short, specific heading in the target language.",
                },
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "1-based indices, into THIS list only, of the items under this "
                        "heading. Every item's index must appear under exactly one heading."
                    ),
                },
            },
            "required": ["heading", "indices"],
        },
    }
    return {
        "name": _GROUPING_TOOL_NAME,
        "description": "Group the numbered takeaways, themes, and sections by index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "takeaway_groups": {
                    **group_array,
                    "description": "Headings grouping the TAKEAWAYS by index (aim for ~10-15).",
                },
                "theme_groups": {
                    **group_array,
                    "description": "Headings grouping the THEMES by index (fewer than takeaways).",
                },
                "section_groups": {
                    **group_array,
                    "description": "Time-ordered macro-sections grouping the SECTIONS by index.",
                },
            },
            "required": ["takeaway_groups", "theme_groups", "section_groups"],
        },
    }


def _serialize_for_grouping(summary: Summary) -> str:
    """Render the flat lists as three independently 1-numbered lists for the grouping
    call. Each list restarts at 1, so the model's per-list indices map straight back."""
    lines: list[str] = ["TAKEAWAYS:"]
    lines += [f"{i}. {t}" for i, t in enumerate(summary.key_takeaways, 1)] or ["(none)"]
    lines.append("\nTHEMES:")
    lines += [f"{i}. {t}" for i, t in enumerate(summary.recurring_themes, 1)] or ["(none)"]
    lines.append("\nSECTIONS:")
    lines += [
        f"{i}. {m.timecode} {m.title}".rstrip() for i, m in enumerate(summary.section_timecodes, 1)
    ] or ["(none)"]
    return "\n".join(lines)


def build_grouping_request(
    summary: Summary, tier: ModelTier, cfg: SummarizeConfig
) -> dict[str, Any]:
    """The grouping request: assign the summary's flat lists to headings by index.

    The language is the summary's own (it was already written in it). Mirrors
    :func:`build_reduce_request` — forced single tool, ``temperature`` only when the
    tier sets it. Pure; no network.
    """
    system = cfg.grouping_system_prompt.replace("{language}", _language_name(summary.language))
    request: dict[str, Any] = {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": _serialize_for_grouping(summary)}],
        "tools": [_grouping_tool_schema()],
        "tool_choice": {"type": "tool", "name": _GROUPING_TOOL_NAME},
    }
    if tier.temperature is not None:  # mirror build_request: omit on models that deprecate it
        request["temperature"] = tier.temperature
    return request


def _assign_by_index(
    items: tuple[_T, ...], spec: Any, *, catchall_heading: str
) -> tuple[tuple[tuple[str, tuple[_T, ...]], ...], int]:
    """Rebuild groups from ``{heading, indices}`` specs, VERBATIM by index.

    The point text comes only from ``items`` (never the model), so a group cannot
    reword or invent a point. Completeness invariant: every index ``1..N`` lands under
    exactly one heading. An index is placed at most once (first kept group wins a
    duplicate); a non-integer or out-of-range index is ignored; a group with an empty
    heading or no valid members is dropped (its indices fall through to orphans). Any
    index no kept group claims goes into the ``catchall_heading`` group so a forgotten
    point is surfaced, never lost. Returns ``(groups, orphan_count)``.
    """
    n = len(items)
    placed: set[int] = set()
    groups: list[tuple[str, tuple[_T, ...]]] = []
    for g in spec if isinstance(spec, list) else ():
        if not isinstance(g, dict):
            continue
        heading = str(g.get("heading", "")).strip()
        raw_idxs = g.get("indices")
        members: list[int] = []
        seen: set[int] = set()
        for raw in raw_idxs if isinstance(raw_idxs, list) else ():
            try:
                i = int(raw)
            except (ValueError, TypeError):
                continue
            if 1 <= i <= n and i not in placed and i not in seen:
                seen.add(i)
                members.append(i)
        if heading and members:  # an empty heading or empty group is dropped -> orphans
            placed.update(members)
            groups.append((heading, tuple(items[i - 1] for i in members)))
    orphans = tuple(items[i - 1] for i in range(1, n + 1) if i not in placed)
    if orphans:
        groups.append((catchall_heading, orphans))
    return tuple(groups), len(orphans)


def _group_points(
    items: tuple[str, ...], spec: Any, *, catchall_heading: str
) -> tuple[tuple[PointGroup, ...], int]:
    """Group a flat string list into :class:`PointGroup`s; returns (groups, orphans)."""
    grouped, orphans = _assign_by_index(items, spec, catchall_heading=catchall_heading)
    return tuple(PointGroup(heading=h, points=pts) for h, pts in grouped), orphans


def _group_sections(
    sections: tuple[SectionMarker, ...], spec: Any, *, catchall_heading: str
) -> tuple[tuple[SectionGroup, ...], int]:
    """Group sections into time-ordered :class:`SectionGroup`s; returns (groups, orphans).

    Sections within a macro-section are sorted by timecode, and the macro-sections are
    ordered by their earliest timecode — the ADR's "time-ordered macro-sections"."""
    grouped, orphans = _assign_by_index(sections, spec, catchall_heading=catchall_heading)
    out = [
        SectionGroup(heading=h, sections=tuple(sorted(secs, key=lambda m: _tc_seconds(m.timecode))))
        for h, secs in grouped
    ]
    out.sort(key=lambda gp: _tc_seconds(gp.sections[0].timecode) if gp.sections else float("inf"))
    return tuple(out), orphans


def group_summary(
    summary: Summary,
    tier: ModelTier,
    cfg: SummarizeConfig,
    *,
    api_key: str,
    caller: Caller = _default_caller,
    log: Logger = print,
) -> SummarizeResult:
    """Add the TD-15 grouping overlay to an already-complete ``summary``. One small call.

    Works on ANY Summary — a fresh paid run OR one reloaded from a saved ``.json`` — so
    grouping can be validated/iterated against the saved 221-point artifact for pennies,
    without re-paying the map-reduce. The grouping call returns only headings + indices;
    the points are reconstructed verbatim from the summary's own flat lists. Returns the
    grouped summary plus the (small) token usage. A summary with nothing to group is
    returned unchanged, no call made.
    """
    if not (summary.key_takeaways or summary.recurring_themes or summary.section_timecodes):
        return SummarizeResult(summary=summary, input_tokens=0, output_tokens=0)

    log("Grouping the extracted points into headings...")
    outcome = caller(build_grouping_request(summary, tier, cfg), api_key)
    if outcome.stop_reason == "max_tokens":
        raise SummarizeError(
            "The grouping step hit the output cap. Raise max_output_tokens in "
            "models.toml, then retry from the saved summary."
        )

    catchall = _catchall_heading(summary.language)
    spec = outcome.tool_input
    tgroups, t_orphans = _group_points(
        summary.key_takeaways, spec.get("takeaway_groups"), catchall_heading=catchall
    )
    thgroups, th_orphans = _group_points(
        summary.recurring_themes, spec.get("theme_groups"), catchall_heading=catchall
    )
    sgroups, s_orphans = _group_sections(
        summary.section_timecodes, spec.get("section_groups"), catchall_heading=catchall
    )
    log(
        f"Grouped {len(summary.key_takeaways)} takeaways into {len(tgroups)} sections "
        f"({t_orphans} orphaned), {len(summary.recurring_themes)} themes into "
        f"{len(thgroups)} ({th_orphans} orphaned), {len(summary.section_timecodes)} "
        f"sections into {len(sgroups)} ({s_orphans} orphaned)."
    )
    grouped = replace(
        summary, takeaway_groups=tgroups, theme_groups=thgroups, section_groups=sgroups
    )
    return SummarizeResult(
        summary=grouped, input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens
    )
