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
from pathlib import Path
from typing import Any

import pytest

from echogist import summarize
from echogist.chunk import Phase
from echogist.config import ChunkConfig, ModelTier, SummarizeConfig
from echogist.summarize import (
    ActionItem,
    CallOutcome,
    Decision,
    SummarizeError,
    SummarizeResult,
    Summary,
    SynthesisSection,
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
    return SummarizeConfig(max_output_tokens=4096)


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
# save_raw_result — F13: raw json saved before render
# --------------------------------------------------------------------------- #
def _summary(title: str) -> Summary:
    return Summary(
        title=title,
        core_idea="c",
        decisions=(Decision("d", "r", "[00:00:00]"),),
        action_items=(ActionItem("task", "owner", "1h", "[00:00:00]"),),
        language="ru",
        synthesis=(SynthesisSection("H", "prose", ("[00:00:00]",)),),
        main_themes=("t",),
    )


def test_save_raw_result_writes_titled_json_no_date_prefix(tmp_path: Path) -> None:
    path = summarize.save_raw_result(_summary("AI in 2026"), tmp_path)
    assert path == tmp_path / "AI in 2026.json"  # no date prefix (plan §3)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "AI in 2026"
    assert data["synthesis"] == [{"heading": "H", "prose": "prose", "anchors": ["[00:00:00]"]}]


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


def test_default_caller_extracts_the_forced_reconcile_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The shared caller must extract whatever tool the request forced. The reconcile
    # request forces emit_reconcile; a hardcoded emit_phase match would skip the valid
    # reconcile block and fail loud with a false "no tool call".
    reconcile = {"title": "T", "core_idea": "C", "main_themes": ["a", "b"]}

    def create(**kwargs: Any) -> Any:
        return _FakeResponse([_FakeBlock("tool_use", "emit_reconcile", reconcile)])

    _install_fake(monkeypatch, _fake_anthropic(create=create))
    request = {"model": "m", "tool_choice": {"type": "tool", "name": "emit_reconcile"}}
    out = summarize._default_caller(request, "sk-test")
    assert out.tool_input == reconcile


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


def test_summarize_auto_single_phase_for_short_input() -> None:
    # TD-16 v2: short material collapses to K=1 — one synthesis call, no reconcile. Its
    # heading becomes the document title.
    caller = _seq_caller(_outcome(_phase_ti("Only", "Only prose.", anchors=["[00:00:00]"])))
    cfg = ChunkConfig(phase_target_tokens=10_000_000)  # everything fits in one phase
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
    assert len(caller.requests) == 1  # type: ignore[attr-defined]  # one phase, no reconcile
    assert result.summary.title == "Only"


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


def test_summarize_auto_runs_multi_phase_synthesis() -> None:
    # TD-16 v2: summarize_auto always phase-splits and synthesizes directly. A tiny
    # phase target forces K>1 (one phase per block), so we see several emit_phase calls
    # plus exactly one emit_reconcile — no map-reduce, no emit_summary.
    calls = {"phase": 0, "reconcile": 0}

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        name = request["tools"][0]["name"]
        if name == "emit_reconcile":
            calls["reconcile"] += 1
            return CallOutcome(
                tool_input={"title": "T", "core_idea": "c", "main_themes": []},
                stop_reason="tool_use",
                input_tokens=1,
                output_tokens=1,
            )
        calls["phase"] += 1
        return CallOutcome(
            tool_input=_phase_ti(f"P{calls['phase']}", "prose"),
            stop_reason="tool_use",
            input_tokens=1,
            output_tokens=1,
        )

    cfg = ChunkConfig(phase_target_tokens=1)  # K clamps to len(blocks) -> one phase/block
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
    assert calls["phase"] >= 2  # multi-phase synthesis
    assert calls["reconcile"] == 1  # the reconcile pass fires once when K>1


# --------------------------------------------------------------------------- #
# TD-16 v2 — direct transcript synthesis (phases -> synthesize -> reconcile)
# --------------------------------------------------------------------------- #
def _phase_ti(
    heading: str,
    prose: str,
    *,
    anchors: list[str] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    action_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An emit_phase tool_input."""
    return {
        "heading": heading,
        "prose": prose,
        "anchors": anchors or [],
        "decisions": decisions or [],
        "action_items": action_items or [],
    }


def _seq_caller(*outcomes: CallOutcome) -> summarize.Caller:
    """A caller that returns the given outcomes in order and records each request."""
    it = iter(outcomes)

    def caller(request: dict[str, Any], api_key: str) -> CallOutcome:
        caller.requests.append(request)  # type: ignore[attr-defined]
        return next(it)

    caller.requests = []  # type: ignore[attr-defined]
    return caller


def _outcome(tool_input: dict[str, Any], *, stop_reason: str = "tool_use") -> CallOutcome:
    return CallOutcome(
        tool_input=tool_input, stop_reason=stop_reason, input_tokens=1500, output_tokens=400
    )


def _two_phases() -> list[Phase]:
    return [
        Phase(text="[00:00:00] intro words", index=1, total=2, start_seconds=0.0, end_seconds=0.0),
        Phase(
            text="[00:10:00] body words", index=2, total=2, start_seconds=600.0, end_seconds=600.0
        ),
    ]


def test_synthesize_summary_happy_path_two_phases_plus_reconcile() -> None:
    phases = _two_phases()
    caller = _seq_caller(
        _outcome(
            _phase_ti(
                "Intro",
                "First idea. Second idea. Third idea.",
                anchors=["[00:00:00]"],
                decisions=[
                    {"decision": "Ship local first", "rationale": "cheaper", "anchor": "[00:00:00]"}
                ],
            )
        ),
        _outcome(
            _phase_ti(
                "Body",
                "Body prose here.",
                anchors=["[00:10:00]"],
                action_items=[
                    {"task": "Benchmark", "owner": "Pat", "estimate": "1d", "anchor": "[00:10:00]"}
                ],
            )
        ),
        _outcome(
            {"title": "The Talk", "core_idea": "AI is infrastructure.", "main_themes": ["a", "b"]}
        ),
    )
    result = summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="lecture",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    s = result.summary
    assert [sec.heading for sec in s.synthesis] == ["Intro", "Body"]
    assert s.synthesis[0].anchors == ("[00:00:00]",)  # exact -> kept
    assert s.synthesis[1].anchors == ("[00:10:00]",)
    assert s.title == "The Talk"
    assert s.core_idea == "AI is infrastructure."
    assert s.main_themes == ("a", "b")
    assert len(s.decisions) == 1 and s.decisions[0].anchor == "[00:00:00]"
    assert len(s.action_items) == 1 and s.action_items[0].anchor == "[00:10:00]"
    assert len(caller.requests) == 3  # type: ignore[attr-defined]  # 2 phases + 1 reconcile
    assert result.input_tokens == 4500 and result.output_tokens == 1200  # summed over 3 calls


def test_synthesize_forward_only_passes_prior_context_to_later_phase() -> None:
    phases = _two_phases()
    caller = _seq_caller(
        _outcome(_phase_ti("Intro", "Alpha sentence. Beta sentence. Gamma sentence.")),
        _outcome(_phase_ti("Body", "Body.")),
        _outcome({"title": "T", "core_idea": "c", "main_themes": []}),
    )
    summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="s",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    phase1_system = caller.requests[0]["system"]  # type: ignore[attr-defined]
    phase2_system = caller.requests[1]["system"]  # type: ignore[attr-defined]
    # Assert on the injected delimiter (the prompt TEXT itself mentions "PRIOR CONTEXT").
    assert "=== PRIOR CONTEXT" not in phase1_system  # nothing precedes the first phase
    assert "=== PRIOR CONTEXT" in phase2_system  # the second phase gets continuity context
    assert "Intro" in phase2_system  # the prior heading
    assert "Gamma sentence." in phase2_system  # the prior phase's tail prose, verbatim


def test_synthesize_single_phase_skips_reconcile_uses_heading_as_title() -> None:
    phases = [Phase(text="[00:00:00] only", index=1, total=1, start_seconds=0.0, end_seconds=0.0)]
    caller = _seq_caller(_outcome(_phase_ti("Lone Heading", "Only prose.", anchors=["[00:00:00]"])))
    result = summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="s",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
    )
    assert len(caller.requests) == 1  # type: ignore[attr-defined]  # no reconcile call for K=1
    assert result.summary.title == "Lone Heading"
    assert result.summary.main_themes == ()


def test_synthesize_phase_max_tokens_fails_loud_naming_phase() -> None:
    phases = _two_phases()
    caller = _seq_caller(
        _outcome(_phase_ti("Intro", "ok")),
        _outcome(_phase_ti("Body", "cut"), stop_reason="max_tokens"),
    )
    with pytest.raises(SummarizeError, match="Phase 2/2 hit the output cap"):
        summarize.synthesize_summary(
            phases,
            _tier(),
            _cfg(),
            language="en",
            source_stem="s",
            api_key="k",
            caller=caller,
            log=lambda _m: None,
        )


def test_synthesize_reconcile_max_tokens_fails_loud() -> None:
    phases = _two_phases()
    caller = _seq_caller(
        _outcome(_phase_ti("Intro", "a")),
        _outcome(_phase_ti("Body", "b")),
        _outcome({"title": "T", "core_idea": "c", "main_themes": []}, stop_reason="max_tokens"),
    )
    with pytest.raises(SummarizeError, match="reconcile step hit the output cap"):
        summarize.synthesize_summary(
            phases,
            _tier(),
            _cfg(),
            language="en",
            source_stem="s",
            api_key="k",
            caller=caller,
            log=lambda _m: None,
        )


def test_synthesize_on_phase_callback_fires_per_phase() -> None:
    phases = _two_phases()
    caller = _seq_caller(
        _outcome(_phase_ti("Intro", "a")),
        _outcome(_phase_ti("Body", "b")),
        _outcome({"title": "T", "core_idea": "c", "main_themes": []}),
    )
    seen: list[tuple[str, ...]] = []
    summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="s",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
        # The seam fires with the RUNNING partial after each phase (T5 artifact-resume):
        # the menu persists it, so it grows one synthesis section per call.
        on_phase=lambda partial: seen.append(tuple(s.heading for s in partial.synthesis)),
    )
    assert seen == [("Intro",), ("Intro", "Body")]  # partial accumulates each phase


def test_synthesize_resumes_from_partial_skips_done_phases() -> None:
    # TD-16 v2 artifact-resume: a partial whose synthesis is a strict prefix of the plan
    # seeds the done phases; only the remaining phase (+ reconcile) hits the wire, and the
    # resumed phase's decisions + tail prose carry through.
    phases = _two_phases()
    resume = summarize._running_summary(
        [SynthesisSection("Intro", "Prior prose.", ("[00:00:00]",))],
        [Decision("D1", "r", "[00:00:00]")],
        [],
        "en",
    )
    caller = _seq_caller(
        _outcome(_phase_ti("Body", "Body prose.", anchors=["[00:10:00]"])),
        _outcome({"title": "T", "core_idea": "c", "main_themes": ["x"]}),
    )
    result = summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="s",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
        resume_from=resume,
    )
    assert len(caller.requests) == 2  # type: ignore[attr-defined]  # phase 2 + reconcile only
    assert [s.heading for s in result.summary.synthesis] == ["Intro", "Body"]
    assert any(d.decision == "D1" for d in result.summary.decisions)  # resumed decision kept
    assert "Prior prose." in caller.requests[0]["system"]  # type: ignore[attr-defined]


def test_synthesize_ignores_stale_partial_and_runs_fresh() -> None:
    # A partial that is NOT a strict prefix (here: already complete, len == K) is ignored —
    # the run starts fresh, all K phases + reconcile re-run. No job engine; just skip-if-prefix.
    phases = _two_phases()
    stale = summarize._running_summary(
        [
            SynthesisSection("A", "a", ()),
            SynthesisSection("B", "b", ()),
        ],
        [],
        [],
        "en",
    )
    caller = _seq_caller(
        _outcome(_phase_ti("Intro", "fresh1")),
        _outcome(_phase_ti("Body", "fresh2")),
        _outcome({"title": "T", "core_idea": "c", "main_themes": []}),
    )
    result = summarize.synthesize_summary(
        phases,
        _tier(),
        _cfg(),
        language="en",
        source_stem="s",
        api_key="k",
        caller=caller,
        log=lambda _m: None,
        resume_from=stale,
    )
    assert len(caller.requests) == 3  # type: ignore[attr-defined]  # 2 phases + reconcile, fresh
    assert [s.heading for s in result.summary.synthesis] == ["Intro", "Body"]


def test_synthesize_empty_phases_fails_loud() -> None:
    with pytest.raises(SummarizeError, match="No transcript phases"):
        summarize.synthesize_summary(
            [], _tier(), _cfg(), language="en", source_stem="s", api_key="k", log=lambda _m: None
        )


def test_build_synthesis_request_substitutes_language_and_interpretation() -> None:
    cfg = SummarizeConfig(
        max_output_tokens=4096,
        synthesis_system_prompt="Write in {language}. Mark with [{interpretation}]:.",
    )
    phase = Phase(text="[00:00:00] t", index=1, total=1, start_seconds=0.0, end_seconds=0.0)
    req = summarize.build_synthesis_request(phase, _tier(), cfg, language="ru")
    assert "Russian" in req["system"]
    assert "[интерпретация]:" in req["system"]  # per-language marker label, substituted
    assert req["tool_choice"]["name"] == "emit_phase"


# --------------------------------------------------------------------------- #
# Anchor validation — exact accept, snap-within-window, drop hallucinated
# --------------------------------------------------------------------------- #
def _synth_summary(anchors: tuple[str, ...]) -> Summary:
    return Summary(
        title="t",
        core_idea="",
        decisions=(),
        action_items=(),
        language="en",
        synthesis=(SynthesisSection(heading="H", prose="p", anchors=anchors),),
    )


def test_validate_anchors_exact_match_is_kept() -> None:
    transcript = "[00:00:00] a\n[00:10:00] b"
    out = summarize.validate_anchors(
        _synth_summary(("[00:10:00]",)), transcript, log=lambda _m: None
    )
    assert out.synthesis[0].anchors == ("[00:10:00]",)


def test_validate_anchors_snaps_within_window() -> None:
    transcript = "[00:00:00] a\n[00:10:00] b"
    # 1s past a real block, inside the 2s default window -> snapped to the real block.
    out = summarize.validate_anchors(
        _synth_summary(("[00:10:01]",)), transcript, log=lambda _m: None
    )
    assert out.synthesis[0].anchors == ("[00:10:00]",)


def test_validate_anchors_drops_hallucinated_outside_window() -> None:
    transcript = "[00:00:00] a\n[00:10:00] b"
    # 00:05:00 is minutes from any real block -> dropped (no false coordinate kept).
    out = summarize.validate_anchors(
        _synth_summary(("[00:05:00]",)), transcript, log=lambda _m: None
    )
    assert out.synthesis[0].anchors == ()


def test_validate_anchors_dedups_within_a_section() -> None:
    transcript = "[00:00:00] a\n[00:10:00] b"
    # Two anchors that both resolve to the same real block collapse to one.
    out = summarize.validate_anchors(
        _synth_summary(("[00:10:00]", "[00:10:01]")), transcript, log=lambda _m: None
    )
    assert out.synthesis[0].anchors == ("[00:10:00]",)


def test_validate_anchors_cleans_decision_and_action_anchors() -> None:
    transcript = "[00:00:00] a\n[00:10:00] b"
    summary = Summary(
        title="t",
        core_idea="",
        language="en",
        decisions=(Decision(decision="d", rationale="r", anchor="[00:05:00]"),),  # hallucinated
        action_items=(ActionItem(task="x", owner="", estimate="", anchor="[00:00:00]"),),  # real
        synthesis=(SynthesisSection(heading="H", prose="p", anchors=()),),
    )
    out = summarize.validate_anchors(summary, transcript, log=lambda _m: None)
    assert out.decisions[0].anchor == ""  # dropped
    assert out.action_items[0].anchor == "[00:00:00]"  # kept


def test_validate_anchors_no_timecodes_drops_all() -> None:
    out = summarize.validate_anchors(
        _synth_summary(("[00:00:00]",)), "no timecodes", log=lambda _m: None
    )
    assert out.synthesis[0].anchors == ()  # nothing to anchor to -> no false coordinate survives


# --------------------------------------------------------------------------- #
# Anchor-preserving merge (TD-16 v2 regression — caught in adversarial review)
# --------------------------------------------------------------------------- #
def test_merge_decisions_back_fills_anchor_not_just_rationale() -> None:
    # The emptier copy comes first (chunk/phase order); the richer duplicate must
    # back-fill BOTH rationale and anchor. Dropping the anchor here would silently
    # break "jump to where decided" before validate_anchors ever runs.
    merged = summarize._merge_decisions(
        [
            Decision("Ship local first", "", anchor=""),
            Decision("ship local first", "cheaper", anchor="[00:05:00]"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].rationale == "cheaper"
    assert merged[0].anchor == "[00:05:00]"  # preserved through the merge


def test_merge_action_items_back_fills_anchor() -> None:
    merged = summarize._merge_action_items(
        [
            ActionItem("Benchmark int8", "", "", anchor=""),
            ActionItem("benchmark int8", "Pat", "1d", anchor="[00:10:00]"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].owner == "Pat" and merged[0].estimate == "1d"
    assert merged[0].anchor == "[00:10:00]"  # preserved through the merge
