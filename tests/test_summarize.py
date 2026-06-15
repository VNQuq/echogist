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
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from echogist import summarize
from echogist.config import ModelTier, SummarizeConfig
from echogist.summarize import (
    CallOutcome,
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
    assert req["tool_choice"] == {"type": "tool", "name": "emit_summary"}
    assert req["tools"][0]["name"] == "emit_summary"
    assert "Russian" in req["system"]  # {language} replaced ru -> Russian
    assert "{language}" not in req["system"]
    assert req["messages"] == [{"role": "user", "content": "hello"}]


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
        language="ru",
    )


def test_save_raw_result_writes_titled_json_no_date_prefix(tmp_path: Path) -> None:
    path = summarize.save_raw_result(_summary("AI in 2026"), tmp_path)
    assert path == tmp_path / "AI in 2026.json"  # no date prefix (plan §3)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "AI in 2026"
    assert data["section_timecodes"] == [{"timecode": "[00:00:00]", "title": "s"}]


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
        def __init__(self, message: str = "", status_code: int = 400) -> None:
            super().__init__(message)
            self.status_code = status_code

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
    with pytest.raises(SummarizeError, match="402"):
        summarize._default_caller({"model": "m"}, "k")
