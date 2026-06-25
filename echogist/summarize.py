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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

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
    no-owner label for that language. ``temperature=0`` pins the decoding so the
    same transcript yields the same title (the title is the artifact filename stem
    via :func:`naming.summary_stem`; a drifting title would dedup into ``-2``/``-3``
    duplicates instead of overwriting on a re-run). ``tool_choice`` forces the one
    tool, suppressing any prose preamble. ``extra_system`` is prepended to the system
    prompt — the map step uses it to mark "this is segment N of M" (TD-5 chunking).
    """
    system = cfg.system_prompt.replace("{language}", _language_name(language)).replace(
        "{unassigned}", _unassigned_label(language)
    )
    if extra_system:
        system = f"{extra_system.strip()}\n\n{system}"
    return {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "temperature": 0,
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
    return {
        "model": tier.model_id,
        "max_tokens": cfg.max_output_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": points_text}],
        "tools": [_reduce_tool_schema()],
        "tool_choice": {"type": "tool", "name": _SYNTHESIS_TOOL_NAME},
    }


def _norm(s: str) -> str:
    """Normalize for dedup: lowercased, whitespace-collapsed. Conservative — only
    near-identical strings collide, so a genuinely distinct point is never merged away."""
    return " ".join(s.lower().split())


def _dedup_strs(items: Iterable[str]) -> tuple[str, ...]:
    """Concatenate, dropping later exact (normalized) duplicates; order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        key = _norm(s)
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return tuple(out)


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
