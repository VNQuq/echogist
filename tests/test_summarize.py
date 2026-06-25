"""Summarize-stage tests (T6) — the one network stage, driven offline.

The network is reached only through the ``caller`` seam and a lazily-imported
``anthropic``, so every path runs with no key, no network, and no real SDK: the
stage logic uses a stub caller, and the SDK adapter (:func:`summarize._default_caller`)
is exercised against a fake ``anthropic`` module installed into ``sys.modules``.
Coverage: the killswitch (module imports clean offline), forced-tool request
assembly, defensive parsing + F10 title fallback, truncation guard, F13 raw-json
save, and the F2/F4/F5 SDK error mapping.
"""

from __future__ import annotations

import ast
import json
import sys
import types
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from echogist import summarize
from echogist.chunk import Chunk
from echogist.config import ChunkConfig, ModelTier, SummarizeConfig
from echogist.summarize import (
    ActionItem,
    CallOutcome,
    Decision,
    SectionMarker,
    SummarizeError,
    SummarizeResult,
    Summary,
)


def _tier() -> ModelTier:
    return ModelTier(
        name="balanced",
        model_id="claude-sonnet-4-6",
        context_window=1_000_000,
        price_in_per_mtok=3.0,
        price_out_per_mtok=15.0,
    )


def _cfg() -> SummarizeConfig:
    return SummarizeConfig(
        system_prompt="Summarize in {language}. Call emit_summary once.",
        max_output_tokens=4096,
    )


def _full_tool_input() -> dict[str, Any]:
    return {
        "title": "Состояние ИИ в 2026",
        "overview": "A talk about where AI is heading.",
        "key_takeaways": ["Models got cheaper.", "Local inference matters."],
        "section_timecodes": [
            {"timecode": "[00:00:00]", "title": "Intro"},
            {"timecode": "[00:12:30]", "title": "Costs"},
        ],
        "decisions": [
            {"decision": "Ship the local-inference path first.", "rationale": "Lower cost."},
        ],
        "action_items": [
            {"task": "Benchmark int8 on the 4060.", "owner": "Pat", "estimate": "1 day"},
        ],
        "recurring_themes": ["efficiency", "access"],
        "core_idea": "AI is becoming infrastructure.",
    }


def _ok_caller(tool_input: dict[str, Any], *, stop_reason: str = "tool_use") -> summarize.Caller:
    """A caller that records the request and returns a fixed CallOutcome."""

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        caller.seen_request = request  # type: ignore[attr-defined]
        caller.seen_key = api_key  # type: ignore[attr-defined]
        return CallOutcome(
            tool_input=tool_input, stop_reason=stop_reason, input_tokens=1500, output_tokens=400
        )

    return caller


# --------------------------------------------------------------------------- #
# Killswitch — the module must import offline, no network packages at top level
# --------------------------------------------------------------------------- #
def test_module_imports_nothing_network_at_top_level() -> None:
    src = Path(summarize.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"anthropic", "httpx", "requests", "urllib", "http", "socket", "ssl"}
    imported: set[str] = set()
    for node in tree.body:  # MODULE level only — the lazy import inside _default_caller is fine
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"network import at module top: {imported & banned}"


def test_anthropic_is_only_imported_inside_the_default_caller() -> None:
    # The lazy `import anthropic` must live inside _default_caller's body, never at
    # module scope, so importing echogist.summarize never pulls the SDK (killswitch).
    src = Path(summarize.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    caller = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_default_caller"
    )
    imports_anthropic = any(
        isinstance(n, ast.Import) and any(a.name == "anthropic" for a in n.names)
        for n in ast.walk(caller)
    )
    assert imports_anthropic, "anthropic should be imported lazily inside _default_caller"


# --------------------------------------------------------------------------- #
# build_request — forced tool-use, language injection, the cap
# --------------------------------------------------------------------------- #
def test_build_request_forces_the_tool_and_injects_language() -> None:
    req = summarize.build_request("hello", _tier(), _cfg(), language="ru")
    assert req["model"] == "claude-sonnet-4-6"
    assert req["max_tokens"] == 4096  # the cap, not the cost projection
    assert "temperature" not in req  # tier omits it -> param absent (4.x models 400 on it)
    assert req["tool_choice"] == {"type": "tool", "name": "emit_summary"}
    assert req["tools"][0]["name"] == "emit_summary"
    assert "Russian" in req["system"]  # {language} replaced ru -> Russian
    assert "{language}" not in req["system"]
    assert req["messages"] == [{"role": "user", "content": "hello"}]


def test_build_request_includes_temperature_only_when_the_tier_sets_it() -> None:
    # An older model that still honors sampling can pin temperature; current 4.x tiers
    # leave it None (omitted). The same gate applies to the reduce/synthesis request.
    pinned = replace(_tier(), temperature=0.0)
    map_req = summarize.build_request("hi", pinned, _cfg(), language="ru")
    reduce_req = summarize.build_reduce_request("points", pinned, _cfg(), language="ru")
    assert map_req["temperature"] == 0.0
    assert reduce_req["temperature"] == 0.0
    # And omitted when the tier does not set it.
    assert "temperature" not in summarize.build_reduce_request("p", _tier(), _cfg(), language="ru")


def test_build_request_substitutes_unassigned_label_per_language() -> None:
    # FIX-4: {unassigned} is replaced with the fixed per-language no-owner label so
    # the model never free-chooses the wording. The token must not survive into the
    # system prompt for either language.
    cfg = SummarizeConfig(
        system_prompt="In {language}, unowned tasks use {unassigned}.",
        max_output_tokens=4096,
    )
    ru = summarize.build_request("x", _tier(), cfg, language="ru")
    en = summarize.build_request("x", _tier(), cfg, language="en")
    assert "Не назначено" in ru["system"]
    assert "Unassigned" in en["system"]
    assert "{unassigned}" not in ru["system"]
    assert "{unassigned}" not in en["system"]


def test_unassigned_label_falls_back_to_english_for_unknown_code() -> None:
    # Mirrors _language_name's fail-soft default: a hand-edited settings code that
    # is not ru/en never crashes the stage — it gets the English label.
    assert summarize._unassigned_label("ru") == "Не назначено"
    assert summarize._unassigned_label("xx") == "Unassigned"


def test_build_request_schema_requires_all_summary_fields() -> None:
    req = summarize.build_request("x", _tier(), _cfg(), language="en")
    schema = req["tools"][0]["input_schema"]
    assert set(schema["required"]) == {
        "title",
        "overview",
        "key_takeaways",
        "section_timecodes",
        "recurring_themes",
        "core_idea",
        "decisions",
        "action_items",
    }
    assert "English" in req["system"]


# --------------------------------------------------------------------------- #
# _parse_summary — happy parse, F10 fallback, defensive coercion
# --------------------------------------------------------------------------- #
def test_parse_full_result() -> None:
    s = summarize._parse_summary(_full_tool_input(), "ru", source_stem="talk")
    assert s.title == "Состояние ИИ в 2026"
    assert s.language == "ru"
    assert s.key_takeaways == ("Models got cheaper.", "Local inference matters.")
    assert s.section_timecodes == (
        SectionMarker("[00:00:00]", "Intro"),
        SectionMarker("[00:12:30]", "Costs"),
    )
    assert s.decisions == (Decision("Ship the local-inference path first.", "Lower cost."),)
    assert s.action_items == (ActionItem("Benchmark int8 on the 4060.", "Pat", "1 day"),)
    assert s.recurring_themes == ("efficiency", "access")


def test_parse_missing_title_uses_f10_fallback() -> None:
    data = _full_tool_input() | {"title": "   "}
    s = summarize._parse_summary(data, "en", source_stem="my lecture", today=date(2026, 6, 15))
    assert s.title == "my lecture-2026-06-15"  # F10: source-stem + date, sanitized


def test_parse_drops_sections_without_a_timecode_never_fakes_one() -> None:
    data = _full_tool_input() | {
        "section_timecodes": [
            {"timecode": "", "title": "ghost"},  # no real timecode -> dropped
            {"timecode": "[00:01:00]", "title": "real"},
            "not even a dict",
        ]
    }
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.section_timecodes == (SectionMarker("[00:01:00]", "real"),)


def test_parse_coerces_non_list_arrays_to_empty() -> None:
    data = _full_tool_input() | {"key_takeaways": None, "recurring_themes": "oops"}
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.key_takeaways == ()
    assert s.recurring_themes == ()


def test_parse_reads_section_bullets() -> None:
    # bullets carry the section's CONTENT (3-5 for long material); they parse onto
    # the marker, trimmed and with empties dropped (via _str_list).
    data = _full_tool_input() | {
        "section_timecodes": [
            {
                "timecode": "[00:00:00]",
                "title": "Intro",
                "bullets": ["  First point  ", "", "Second point"],
            },
            {"timecode": "[00:12:30]", "title": "Costs"},  # missing bullets -> empty tuple
        ]
    }
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.section_timecodes[0] == SectionMarker(
        "[00:00:00]", "Intro", ("First point", "Second point")
    )
    assert s.section_timecodes[1].bullets == ()  # absent bullets degrade to empty, not crash


def test_parse_coerces_non_list_bullets_to_empty() -> None:
    data = _full_tool_input() | {
        "section_timecodes": [{"timecode": "[00:01:00]", "title": "s", "bullets": "oops"}]
    }
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.section_timecodes[0].bullets == ()


def test_no_upper_limit_in_schema_or_prompt() -> None:
    # Regression for the truncation bug: the schema/prompt must not reimpose the old
    # "3-7 / 2-6 / 2-4 / 1-2" element caps. Guards against a silent re-tightening.
    schema = summarize._tool_schema()["input_schema"]["properties"]
    blob = json.dumps(schema, ensure_ascii=False)
    for capped in ("3-7", "2-6", "2-4", "1-2 sentence", "between 3 and 7", "between 2 and 6"):
        assert capped not in blob, f"old element cap leaked back into the schema: {capped!r}"


def test_parse_drops_decisions_without_a_statement() -> None:
    data = _full_tool_input() | {
        "decisions": [
            {"decision": "", "rationale": "orphan rationale"},  # no decision -> dropped
            {"decision": "Adopt int8.", "rationale": ""},  # empty rationale is fine
            "not a dict",
        ]
    }
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.decisions == (Decision("Adopt int8.", ""),)


def test_parse_drops_action_items_without_a_task() -> None:
    data = _full_tool_input() | {
        "action_items": [
            {"task": "", "owner": "Pat", "estimate": "2h"},  # no task -> dropped
            {"task": "Write the doc."},  # missing owner/estimate coerce to ""
        ]
    }
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.action_items == (ActionItem("Write the doc.", "", ""),)


def test_parse_coerces_non_list_meeting_fields_to_empty() -> None:
    data = _full_tool_input() | {"decisions": "oops", "action_items": None}
    s = summarize._parse_summary(data, "en", source_stem="x")
    assert s.decisions == ()
    assert s.action_items == ()


# --------------------------------------------------------------------------- #
# summarize() — stub caller drives the stage end to end
# --------------------------------------------------------------------------- #
def test_summarize_happy_path_returns_usage() -> None:
    caller = _ok_caller(_full_tool_input())
    result = summarize.summarize(
        "the transcript",
        _tier(),
        _cfg(),
        language="ru",
        source_stem="talk",
        api_key="sk-test",
        caller=caller,
        log=lambda _m: None,
    )
    assert isinstance(result, SummarizeResult)
    assert result.summary.title == "Состояние ИИ в 2026"
    assert result.input_tokens == 1500
    assert result.output_tokens == 400
    # The seam received the built request + the key (never logged elsewhere).
    assert caller.seen_request["model"] == "claude-sonnet-4-6"  # type: ignore[attr-defined]
    assert caller.seen_key == "sk-test"  # type: ignore[attr-defined]


def test_summarize_truncated_reply_fails_loud() -> None:
    # stop_reason max_tokens => the tool JSON is cut off; never parse a half-summary.
    caller = _ok_caller(_full_tool_input(), stop_reason="max_tokens")
    with pytest.raises(SummarizeError, match="cap"):
        summarize.summarize(
            "t",
            _tier(),
            _cfg(),
            language="en",
            source_stem="x",
            api_key="k",
            caller=caller,
            log=lambda _m: None,
        )


def test_summarize_propagates_caller_error() -> None:
    def boom(request: dict[str, Any], api_key: str) -> CallOutcome:
        raise SummarizeError("the API is rate-limited or the account is out of credit.")

    with pytest.raises(SummarizeError, match="rate-limited"):
        summarize.summarize(
            "t",
            _tier(),
            _cfg(),
            language="en",
            source_stem="x",
            api_key="k",
            caller=boom,
            log=lambda _m: None,
        )


# --------------------------------------------------------------------------- #
# save_raw_result — F13: raw json saved before render
# --------------------------------------------------------------------------- #
def _summary(title: str) -> Summary:
    return Summary(
        title=title,
        overview="o",
        key_takeaways=("a",),
        section_timecodes=(SectionMarker("[00:00:00]", "s"),),
        recurring_themes=("t",),
        core_idea="c",
        decisions=(Decision("d", "r"),),
        action_items=(ActionItem("task", "owner", "1h"),),
        language="ru",
    )


def test_save_raw_result_writes_titled_json_no_date_prefix(tmp_path: Path) -> None:
    path = summarize.save_raw_result(_summary("AI in 2026"), tmp_path)
    assert path == tmp_path / "AI in 2026.json"  # no date prefix (plan §3)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "AI in 2026"
    assert data["section_timecodes"] == [{"timecode": "[00:00:00]", "title": "s", "bullets": []}]


def test_save_raw_result_keeps_cyrillic_literal(tmp_path: Path) -> None:
    path = summarize.save_raw_result(_summary("Состояние ИИ"), tmp_path)
    assert path.name == "Состояние ИИ.json"
    assert "Состояние" in path.read_text(encoding="utf-8")  # not \\u-escaped


def test_save_raw_result_dedups(tmp_path: Path) -> None:
    p1 = summarize.save_raw_result(_summary("Talk"), tmp_path)
    p2 = summarize.save_raw_result(_summary("Talk"), tmp_path)
    assert p1.name == "Talk.json"
    assert p2.name == "Talk-2.json"


def test_save_raw_result_illegal_title_sanitized(tmp_path: Path) -> None:
    path = summarize.save_raw_result(_summary("a/b:c?"), tmp_path)
    assert path.name == "a-b-c.json"  # F9 illegal-char strip via naming.sanitize_stem


# --------------------------------------------------------------------------- #
# _default_caller — SDK adapter against a FAKE anthropic module (F2/F4/F5)
# --------------------------------------------------------------------------- #
class _FakeBlock:
    def __init__(self, type: str, name: str, input: dict[str, Any]) -> None:
        self.type = type
        self.name = name
        self.input = input


class _FakeUsage:
    input_tokens = 1200
    output_tokens = 350


class _FakeResponse:
    def __init__(self, content: list[Any], stop_reason: str = "tool_use") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


def _fake_anthropic(*, create: Any) -> types.ModuleType:
    """Build a fake ``anthropic`` module with the SDK's exception tree + a client."""
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIConnectionError(APIError):
        pass

    class RateLimitError(APIError):
        pass

    class AuthenticationError(APIError):
        pass

    class NotFoundError(APIError):
        pass

    class APIStatusError(APIError):
        def __init__(self, message: str = "", status_code: int = 400, body: Any = None) -> None:
            super().__init__(message)
            self.status_code = status_code
            self.message = message
            self.body = body

    class _Messages:
        def create(self, **kwargs: Any) -> Any:
            return create(**kwargs)

    class Anthropic:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key
            self.messages = _Messages()

    for name, obj in {
        "APIError": APIError,
        "APIConnectionError": APIConnectionError,
        "RateLimitError": RateLimitError,
        "AuthenticationError": AuthenticationError,
        "NotFoundError": NotFoundError,
        "APIStatusError": APIStatusError,
        "Anthropic": Anthropic,
    }.items():
        setattr(mod, name, obj)
    return mod


class _no_module:
    """Context manager: force ``import <name>`` to raise ImportError within."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._saved: Any = None

    def __enter__(self) -> None:
        self._saved = sys.modules.get(self.name, "absent")
        sys.modules[self.name] = None  # type: ignore[assignment]

    def __exit__(self, *exc: Any) -> None:
        if self._saved == "absent":
            sys.modules.pop(self.name, None)
        else:
            sys.modules[self.name] = self._saved


def _install_fake(monkeypatch: pytest.MonkeyPatch, mod: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def test_default_caller_success_extracts_input_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def create(**kwargs: Any) -> Any:
        return _FakeResponse([_FakeBlock("tool_use", "emit_summary", _full_tool_input())])

    _install_fake(monkeypatch, _fake_anthropic(create=create))
    out = summarize._default_caller({"model": "m"}, "sk-test")
    assert out.tool_input["title"] == "Состояние ИИ в 2026"
    assert out.stop_reason == "tool_use"
    assert out.input_tokens == 1200
    assert out.output_tokens == 350


def test_default_caller_no_tool_block_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    def create(**kwargs: Any) -> Any:
        return _FakeResponse([_FakeBlock("text", "", {})])  # model emitted prose, no tool call

    _install_fake(monkeypatch, _fake_anthropic(create=create))
    with pytest.raises(SummarizeError, match="did not return a structured summary"):
        summarize._default_caller({"model": "m"}, "k")


def test_default_caller_missing_package_fails_loud() -> None:
    with _no_module("anthropic"), pytest.raises(SummarizeError, match="anthropic is not installed"):
        summarize._default_caller({"model": "m"}, "k")


def test_default_caller_connection_error_is_f2(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].APIConnectionError("down")

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match="could not reach the API"):
        summarize._default_caller({"model": "m"}, "k")


def test_default_caller_rate_limit_is_f4(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].RateLimitError("429")

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match="out of credit"):
        summarize._default_caller({"model": "m"}, "k")


def test_default_caller_not_found_is_f5(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].NotFoundError("404")

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match="not found"):
        summarize._default_caller({"model": "claude-x"}, "k")


def test_default_caller_bad_key_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].AuthenticationError("401")

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match="ANTHROPIC_API_KEY"):
        summarize._default_caller({"model": "m"}, "bad")


def test_default_caller_status_error_is_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].APIStatusError("billing", status_code=402)

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match=r"402.*billing"):  # status AND the API's own reason
        summarize._default_caller({"model": "m"}, "k")


def test_default_caller_status_error_surfaces_api_body_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 400 from Anthropic explains itself in the response body; we must not swallow it
    # behind a bare status code (CLAUDE.md "fail loud" = human-readable, not a number).
    holder: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        raise holder["mod"].APIStatusError(
            "Error code: 400",
            status_code=400,
            body={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            },
        )

    mod = _fake_anthropic(create=create)
    holder["mod"] = mod
    _install_fake(monkeypatch, mod)
    with pytest.raises(SummarizeError, match="credit balance is too low"):
        summarize._default_caller({"model": "m"}, "k")


# --------------------------------------------------------------------------- #
# TD-5 — map-reduce: request builders, merge/dedup, orchestration, dispatch
# --------------------------------------------------------------------------- #
def _chunk(text: str = "[00:00:00] hi", index: int = 1, total: int = 2) -> Chunk:
    return Chunk(text=text, index=index, total=total, start_seconds=0.0, end_seconds=60.0)


def _scripted_caller(map_outputs: list[dict[str, Any]], synthesis: dict[str, Any]) -> Any:
    """A caller that returns the queued map outputs in order, and ``synthesis`` for the
    reduce call (detected by the forced tool name in the request). Records call count."""

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        name = request["tools"][0]["name"]
        if name == "emit_synthesis":
            return CallOutcome(
                tool_input=synthesis, stop_reason="tool_use", input_tokens=10, output_tokens=20
            )
        out = map_outputs[caller.calls]  # type: ignore[attr-defined]
        caller.calls += 1  # type: ignore[attr-defined]
        return CallOutcome(
            tool_input=out, stop_reason="tool_use", input_tokens=100, output_tokens=50
        )

    caller.calls = 0  # type: ignore[attr-defined]
    return caller


def test_build_map_request_marks_the_segment() -> None:
    req = summarize.build_map_request(_chunk(index=2, total=5), _tier(), _cfg(), language="ru")
    assert req["tools"][0]["name"] == "emit_summary"  # full extraction contract per chunk
    assert "segment 2 of 5" in req["system"]  # map note injected ahead of the base prompt
    assert "Summarize in" in req["system"]  # base prompt still present


def test_build_reduce_request_forces_synthesis_tool() -> None:
    req = summarize.build_reduce_request("KEY POINTS:\n- x", _tier(), _cfg(), language="en")
    assert req["tool_choice"] == {"type": "tool", "name": "emit_synthesis"}
    schema = req["tools"][0]["input_schema"]
    assert set(schema["required"]) == {"title", "overview", "core_idea"}


def test_dedup_keeps_distinct_drops_normalized_duplicates() -> None:
    assert summarize._dedup_strs(["A point.", "a  POINT.", "Other"]) == ("A point.", "Other")


def test_merge_sections_unions_bullets_and_sorts_by_timecode() -> None:
    a = SectionMarker("[00:10:00]", "Costs", ("cheaper",))
    b = SectionMarker("[00:00:00]", "Intro", ("hi",))
    dup = SectionMarker("[00:10:00]", "Costs", ("cheaper", "and faster"))  # overlap dup, new bullet
    merged = summarize._merge_sections([a, b, dup])
    assert [m.timecode for m in merged] == ["[00:00:00]", "[00:10:00]"]  # sorted by time
    assert merged[1].bullets == ("cheaper", "and faster")  # bullets unioned, none dropped


def test_summarize_chunked_maps_each_chunk_then_reduces() -> None:
    p1 = _full_tool_input() | {"key_takeaways": ["A", "B"], "recurring_themes": ["x"]}
    p2 = _full_tool_input() | {"key_takeaways": ["B", "C"], "recurring_themes": ["x", "y"]}
    synthesis = {"title": "Whole", "overview": "ov", "core_idea": "ci"}
    caller = _scripted_caller([p1, p2], synthesis)
    chunks = [_chunk(index=1, total=2), _chunk(index=2, total=2)]

    result = summarize.summarize_chunked(
        "ignored",
        _tier(),
        _cfg(),
        chunks,
        language="ru",
        source_stem="talk",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    assert caller.calls == 2  # one MAP call per chunk...
    # ...plus the reduce call: 3 total. Usage summed across all of them.
    assert result.input_tokens == 100 + 100 + 10
    assert result.output_tokens == 50 + 50 + 20
    # Completeness: B is deduped, A and C survive; holistic fields come from synthesis.
    assert result.summary.key_takeaways == ("A", "B", "C")
    assert result.summary.recurring_themes == ("x", "y")
    assert result.summary.title == "Whole"
    assert result.summary.core_idea == "ci"


def test_summarize_chunked_truncated_map_fails_loud_naming_segment() -> None:
    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        return CallOutcome(
            tool_input=_full_tool_input(), stop_reason="max_tokens", input_tokens=1, output_tokens=1
        )

    with pytest.raises(SummarizeError, match="Segment 1/1"):
        summarize.summarize_chunked(
            "x",
            _tier(),
            _cfg(),
            [_chunk(index=1, total=1)],
            language="en",
            source_stem="x",
            api_key="k",
            caller=caller,
            log=lambda _m: None,
        )


def test_summarize_auto_single_pass_for_short_input() -> None:
    caller = _ok_caller(_full_tool_input())
    # Budgets so high nothing trips -> single pass, exactly one call through the seam.
    cfg = ChunkConfig(quality_budget_tokens=10_000_000, quality_budget_seconds=10_000_000)
    result = summarize.summarize_auto(
        "[00:00:00] short transcript",
        _tier(),
        _cfg(),
        chunk_cfg=cfg,
        language="ru",
        source_stem="t",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    assert isinstance(result, SummarizeResult)


def test_merge_sections_collapses_same_timecode_under_different_titles() -> None:
    # Overlap re-feeds one boundary block to two chunks; at temperature=0 the model can
    # still retitle that same-timecode section per chunk context. Keyed on timecode ALONE,
    # the two copies collapse into ONE section (first title) with bullets unioned — not two
    # sections at one timecode with their bullets split.
    a = SectionMarker("[00:05:00]", "Pricing", ("tiered",))
    b = SectionMarker("[00:05:00]", "Costs and pricing", ("tiered", "per seat"))
    merged = summarize._merge_sections([a, b])
    assert len(merged) == 1
    assert merged[0].title == "Pricing"  # first title wins
    assert merged[0].bullets == ("tiered", "per seat")  # bullets unioned, none split off


def test_merge_decisions_backfills_missing_rationale_from_later_duplicate() -> None:
    # Overlap order often puts the emptier copy first; the later rationale is back-filled
    # onto the same (normalized) decision rather than discarded.
    out = summarize._merge_decisions(
        [
            Decision("Adopt int8", ""),
            Decision("adopt  INT8", "halves VRAM"),  # normalized-equal statement, has rationale
            Decision("Hire", "growth"),
        ]
    )
    assert [d.decision for d in out] == ["Adopt int8", "Hire"]  # deduped, order kept
    assert out[0].rationale == "halves VRAM"  # back-filled, not lost


def test_merge_decisions_keeps_present_rationale_over_later_duplicate() -> None:
    out = summarize._merge_decisions(
        [Decision("X", "first reason"), Decision("x", "second reason")]
    )
    assert out == (Decision("X", "first reason"),)  # an existing rationale is never overwritten


def test_merge_action_items_backfills_missing_owner_and_estimate() -> None:
    out = summarize._merge_action_items(
        [ActionItem("Write spec", "", ""), ActionItem("write  SPEC", "Ann", "2d")]
    )
    assert out == (ActionItem("Write spec", "Ann", "2d"),)  # owner + estimate back-filled


def test_summarize_chunked_truncated_synthesis_fails_loud() -> None:
    # The reduce/synthesis call has its OWN truncation guard, distinct from the map guard.
    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        name = request["tools"][0]["name"]
        if name == "emit_synthesis":
            return CallOutcome(
                tool_input={"title": "T", "overview": "o", "core_idea": "c"},
                stop_reason="max_tokens",
                input_tokens=1,
                output_tokens=1,
            )
        return CallOutcome(
            tool_input=_full_tool_input(), stop_reason="tool_use", input_tokens=1, output_tokens=1
        )

    with pytest.raises(SummarizeError, match="synthesis step hit the output cap"):
        summarize.summarize_chunked(
            "x",
            _tier(),
            _cfg(),
            [_chunk(index=1, total=1)],
            language="en",
            source_stem="x",
            api_key="k",
            caller=caller,
            log=lambda _m: None,
        )


def test_summarize_auto_chunks_when_over_quality_budget() -> None:
    calls = {"n": 0}

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        calls["n"] += 1
        name = request["tools"][0]["name"]
        if name == "emit_synthesis":
            return CallOutcome(
                tool_input={"title": "T", "overview": "o", "core_idea": "c"},
                stop_reason="tool_use",
                input_tokens=1,
                output_tokens=1,
            )
        return CallOutcome(
            tool_input=_full_tool_input(), stop_reason="tool_use", input_tokens=1, output_tokens=1
        )

    # Force chunking on any input; a multi-block transcript so the planner splits it.
    cfg = ChunkConfig(quality_budget_tokens=1, quality_budget_seconds=1, target_chunk_tokens=20)
    text = "\n".join(f"[00:{m:02d}:00] word word word" for m in range(6))
    summarize.summarize_auto(
        text,
        _tier(),
        _cfg(),
        chunk_cfg=cfg,
        language="ru",
        source_stem="t",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    assert calls["n"] >= 3  # >=2 map calls + 1 reduce (chunked, not single-pass)
