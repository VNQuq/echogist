"""UI-seam tests (T2 / T9).

The live ``RichQuestionaryUI`` prompts are TTY-bound and operator-verified on Windows
(§7) — not exercised here. What IS unit-testable off-TTY: the no-TTY construction guard
(§6.1), the cancel → clean-exit translation (§6.2 / T9), and the offline ``StubUI``
double the whole menu suite rides on. Killswitch-safe: no model, no key, no network.
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import questionary
from prompt_toolkit.key_binding import KeyBindings

from echogist.ui import (
    UI,
    NotInteractiveError,
    RichQuestionaryUI,
    StubUI,
    build_default_ui,
)

_AV_FILETYPES = [("Audio/Video", "*.mp3 *.mp4"), ("All files", "*.*")]


class _TTY(io.StringIO):
    """A stream that claims to be a terminal (so the §6.1 guard lets the UI construct)."""

    def isatty(self) -> bool:
        return True


class _FakeQuestion:
    """Stands in for a questionary.Question — ``ask()`` returns a queued answer, or
    raises to simulate Ctrl-C."""

    def __init__(self, answer: Any = None, *, raises: BaseException | None = None) -> None:
        self._answer = answer
        self._raises = raises
        # select() now wires hidden number keys onto question.application.key_bindings;
        # give the fake a real (empty) KeyBindings so that path is exercised, not skipped.
        self.application = types.SimpleNamespace(key_bindings=KeyBindings())

    def ask(self) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._answer


def _tty_ui() -> RichQuestionaryUI:
    return RichQuestionaryUI(stdin=_TTY(), stdout=_TTY())


# --------------------------------------------------------------------------- #
# §6.1 — no-TTY construction guard
# --------------------------------------------------------------------------- #
def test_non_tty_construction_raises_clean_message() -> None:
    with pytest.raises(NotInteractiveError, match="interactive terminal"):
        RichQuestionaryUI(stdin=io.StringIO(), stdout=io.StringIO())


def test_tty_construction_succeeds() -> None:
    ui_ = _tty_ui()
    assert ui_.glyphs is not None  # capability detection ran


# --------------------------------------------------------------------------- #
# §6.2 / T9 — cancel → EOFError (the loop's clean-exit signal)
# --------------------------------------------------------------------------- #
def test_ask_returns_value_unchanged() -> None:
    ui_ = _tty_ui()
    assert ui_._ask(cast(Any, _FakeQuestion("picked"))) == "picked"


def test_ask_none_becomes_eof() -> None:
    # questionary returns None on Ctrl-C / Ctrl-D / ESC → translate to the clean exit.
    ui_ = _tty_ui()
    with pytest.raises(EOFError):
        ui_._ask(cast(Any, _FakeQuestion(None)))


def test_ask_keyboard_interrupt_becomes_eof() -> None:
    # Belt-and-braces: a raw KeyboardInterrupt must never crash the console.
    ui_ = _tty_ui()
    with pytest.raises(EOFError):
        ui_._ask(cast(Any, _FakeQuestion(raises=KeyboardInterrupt())))


def test_select_styles_control_choices_subtly(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dunder-wrapped control value (← Back) renders with the muted ``control`` style;
    # a functional choice keeps a plain string title (the normal palette).
    captured: dict[str, Any] = {}

    def fake_select(prompt: str, *, choices: Any, **_kw: Any) -> Any:
        captured["choices"] = choices
        return _FakeQuestion("1")

    monkeypatch.setattr(questionary, "select", fake_select)
    ui_ = _tty_ui()
    assert ui_.select("pick", [("1", "Summary"), ("__back__", "← Back")]) == "1"

    by_value = {c.value: c.title for c in captured["choices"]}
    assert by_value["1"] == "Summary"  # functional → plain title
    assert by_value["__back__"] == [("class:control", "← Back")]  # control → muted style


# --------------------------------------------------------------------------- #
# Hidden number quick-select (1-9) — the binding + its subtle in-hint advert.
# --------------------------------------------------------------------------- #
def _captured_instruction(monkeypatch: pytest.MonkeyPatch, choices: list[tuple[str, str]]) -> str:
    captured: dict[str, Any] = {}

    def fake_select(prompt: str, *, choices: Any, **kw: Any) -> Any:
        captured.update(kw)
        return _FakeQuestion(choices[0].value)

    monkeypatch.setattr(questionary, "select", fake_select)
    _tty_ui().select("pick", choices)
    return str(captured["instruction"])


def test_select_advertises_number_keys_in_arrow_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bind is hidden per-row; the only tell is the existing "(Use arrow keys)" hint,
    # extended with the range — and the upper bound matches THIS menu's row count.
    got = _captured_instruction(monkeypatch, [("1", "A"), ("2", "B"), ("3", "C")])
    assert got == "(Use arrow keys or 1-3)"


def test_select_number_hint_caps_at_nine(monkeypatch: pytest.MonkeyPatch) -> None:
    choices = [(str(i), str(i)) for i in range(12)]
    assert _captured_instruction(monkeypatch, choices) == "(Use arrow keys or 1-9)"


def test_select_single_choice_keeps_bare_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # One row → no number worth advertising; the hint stays exactly as it was.
    assert _captured_instruction(monkeypatch, [("only", "Only")]) == "(Use arrow keys)"


def test_bind_number_keys_maps_digits_to_row_positions() -> None:
    # Position-based: digit N picks the N-th row (a ← Back row is just a position), and
    # the handler exits with that row's value — exactly what Enter on the row would do.
    choices = [
        questionary.Choice(title=lbl, value=val)
        for val, lbl in (("a", "A"), ("b", "B"), ("__back__", "← Back"))
    ]
    q = questionary.select("pick", choices=choices)
    RichQuestionaryUI._bind_number_keys(q, choices)
    kb = q.application.key_bindings
    assert isinstance(kb, KeyBindings)
    digit = {
        str(b.keys[0]): b for b in kb.bindings if len(b.keys) == 1 and str(b.keys[0]).isdigit()
    }
    assert sorted(digit) == ["1", "2", "3"]  # no "4" — only three rows
    for key, expected in (("1", "a"), ("2", "b"), ("3", "__back__")):
        event = MagicMock()
        digit[key].handler(event)
        event.app.exit.assert_called_once_with(result=expected)


def test_bind_number_keys_caps_at_nine() -> None:
    choices = [questionary.Choice(title=str(i), value=str(i)) for i in range(12)]
    q = questionary.select("pick", choices=choices)
    RichQuestionaryUI._bind_number_keys(q, choices)
    kb = q.application.key_bindings
    assert isinstance(kb, KeyBindings)
    digits = {str(b.keys[0]) for b in kb.bindings if len(b.keys) == 1 and str(b.keys[0]).isdigit()}
    assert digits == {"1", "2", "3", "4", "5", "6", "7", "8", "9"}


# --------------------------------------------------------------------------- #
# StubUI — the offline double
# --------------------------------------------------------------------------- #
def test_stub_is_a_ui() -> None:
    assert isinstance(StubUI(), UI)


def test_stub_pops_answers_in_order() -> None:
    stub = StubUI(["a", "b", True])
    assert stub.select("pick", [("a", "A")]) == "a"
    assert stub.text("type") == "b"
    assert stub.confirm("ok?") is True


def test_stub_empty_queue_raises_eof() -> None:
    # Mirrors a closed stdin: the loop exits cleanly on EOF.
    stub = StubUI([])
    with pytest.raises(EOFError):
        stub.select("pick", [])


def test_stub_records_messages() -> None:
    stub = StubUI()
    stub.info("hello")
    stub.success("done")
    stub.error("boom")
    assert ("info", "hello") in stub.messages
    assert "done" in stub.log_text and "boom" in stub.log_text


def test_stub_progress_records_advances() -> None:
    stub = StubUI()
    with stub.progress("transcribing", total=1.0) as bar:
        bar.advance_to(0.25)
        bar.advance_to(1.0)
        bar.done()
    assert stub.progress_values == [0.25, 1.0]


def test_stub_progress_records_done_on_clean_exit() -> None:
    # TD-17: a clean exit records "done" (the bar completed), never "fail".
    stub = StubUI()
    with stub.progress("work", total=1.0) as bar:
        bar.advance_to(0.5)
    assert "done" in stub.progress_events
    assert "fail" not in stub.progress_events


def test_stub_progress_records_fail_on_exception() -> None:
    # TD-17: when the stage raises, the bar is FAILED (not completed) and the exception still
    # propagates. This is the seam the menu-level tests assert against.
    stub = StubUI()
    with pytest.raises(RuntimeError, match="boom"), stub.progress("work", total=1.0) as bar:
        bar.advance_to(0.5)
        raise RuntimeError("boom")
    assert stub.progress_events == ["fail"]  # failed, never "done"


def test_production_progress_completes_bar_on_clean_exit() -> None:
    # TD-17: the real rich bar is filled to 100% at a clean context exit (success safety net).
    ui_ = _tty_ui()
    with ui_.progress("work", total=1.0) as bar:
        bar.advance_to(0.5)
    assert bar._prog.tasks[0].completed == 1.0  # type: ignore[attr-defined]


def test_production_progress_stops_bar_without_completing_on_failure() -> None:
    # TD-17 (the core fix): when the stage raises, the real bar is stopped at its last real
    # fraction — NOT snapped to a false 100% right before the error panel — and the exception
    # still propagates unchanged.
    ui_ = _tty_ui()
    with pytest.raises(RuntimeError, match="stage blew up"), ui_.progress("work", total=1.0) as bar:
        bar.advance_to(0.4)
        raise RuntimeError("stage blew up")
    task = bar._prog.tasks[0]  # type: ignore[attr-defined]
    assert task.completed == 0.4  # left where it was
    assert not task.finished  # never forced to the 100% "done" state


def test_stub_spinner_is_noop_context_manager() -> None:
    stub = StubUI()
    with stub.spinner("loading model") as sp:
        sp.done(ok=True)
    assert ("spinner", "loading model") in stub.messages


def test_build_default_ui_raises_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest captures stdio (no TTY), so the production builder must refuse cleanly.
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with pytest.raises(NotInteractiveError):
        build_default_ui()


# --------------------------------------------------------------------------- #
# StubUI.pick_file (TD-10) — the three queue states, the only ones
# --------------------------------------------------------------------------- #
def test_stub_pick_file_returns_queued_path() -> None:
    stub = StubUI(["/clips/lecture.mp4"])
    assert stub.pick_file("Pick a file", filetypes=_AV_FILETYPES) == "/clips/lecture.mp4"
    assert ("pick_file", "Pick a file") in stub.messages


def test_stub_pick_file_none_is_soft_cancel() -> None:
    # queued None = the user cancelled the dialog → return to menu (NOT an exit).
    stub = StubUI([None])
    assert stub.pick_file("Pick a file", filetypes=_AV_FILETYPES) is None


def test_stub_pick_file_empty_queue_raises_eof() -> None:
    stub = StubUI([])
    with pytest.raises(EOFError):
        stub.pick_file("Pick a file", filetypes=_AV_FILETYPES)


# --------------------------------------------------------------------------- #
# RichQuestionaryUI.pick_file (TD-10) — native dialog + fallback routing.
# The live dialog is Windows-gate-verified; here we drive the branches with a
# fake tkinter module and a fake questionary prompt (killswitch-safe, no GUI).
# --------------------------------------------------------------------------- #
class _FakeTclError(Exception):
    """Stand-in for tkinter.TclError — both what Tk() raises and what code catches."""


class _FakeRoot:
    """A withdrawn Tk root; records that destroy() ran (the lifecycle guarantee)."""

    def __init__(self, destroyed: list[bool]) -> None:
        self._destroyed = destroyed

    def withdraw(self) -> None: ...
    def wm_attributes(self, *args: object) -> None: ...
    def destroy(self) -> None:
        self._destroyed.append(True)


def _install_fake_tk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    picked: str,
    destroyed: list[bool],
    raise_tcl: bool = False,
) -> None:
    """Inject a fake ``tkinter`` (+ filedialog) so _native_open runs without a GUI."""

    def _tk() -> _FakeRoot:
        if raise_tcl:
            raise _FakeTclError("no display")
        return _FakeRoot(destroyed)

    fd = types.SimpleNamespace(askopenfilename=lambda **_kw: picked)
    fake = types.SimpleNamespace(TclError=_FakeTclError, Tk=_tk, filedialog=fd)
    monkeypatch.setitem(sys.modules, "tkinter", cast(Any, fake))
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", cast(Any, fd))


def test_pick_file_native_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="/clips/lecture.mp4", destroyed=destroyed)
    ui_ = _tty_ui()
    got = ui_.pick_file("Pick", filetypes=_AV_FILETYPES, initialdir=Path("/start"))
    assert got == "/clips/lecture.mp4"
    assert destroyed == [True]  # the hidden root was always destroyed


def test_pick_file_native_cancel_returns_none_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # Native Cancel returns "" — a soft cancel (→ menu), NOT a drop into the
    # in-console fallback. Make the fallback explode so we prove it isn't hit.
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="", destroyed=destroyed)
    ui_ = _tty_ui()

    def _no_fallback(_p: str) -> str | None:
        pytest.fail("fallback must not run after a native cancel")

    monkeypatch.setattr(ui_, "_path_fallback", _no_fallback)
    assert ui_.pick_file("Pick", filetypes=_AV_FILETYPES) is None
    assert destroyed == [True]


def test_native_open_importerror_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # tkinter absent (the real WSL/CI state) → guard returns None → caller falls back.
    monkeypatch.setitem(sys.modules, "tkinter", cast(Any, None))
    assert RichQuestionaryUI._native_open("Pick", _AV_FILETYPES, None) is None


def test_native_open_tclerror_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # tkinter present but no display → TclError on Tk() → guard returns None.
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="x", destroyed=destroyed, raise_tcl=True)
    assert RichQuestionaryUI._native_open("Pick", _AV_FILETYPES, None) is None
    assert destroyed == []  # root was never created → nothing to destroy


def test_pick_file_falls_back_when_tk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    ui_ = _tty_ui()
    monkeypatch.setattr(RichQuestionaryUI, "_native_open", staticmethod(lambda *_a: None))
    monkeypatch.setattr(ui_, "_path_fallback", lambda _p: "/typed/path.wav")
    assert ui_.pick_file("Pick", filetypes=_AV_FILETYPES) == "/typed/path.wav"


def test_pick_file_native_keyboardinterrupt_becomes_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    ui_ = _tty_ui()

    def _boom(*_a: object) -> str | None:
        raise KeyboardInterrupt

    monkeypatch.setattr(RichQuestionaryUI, "_native_open", staticmethod(_boom))
    with pytest.raises(EOFError):
        ui_.pick_file("Pick", filetypes=_AV_FILETYPES)


# --------------------------------------------------------------------------- #
# pick_files — the multi-select sibling. Same cancel split as pick_file, plus the
# tuple/"" shapes askopenfilenames returns.
# --------------------------------------------------------------------------- #
def _install_fake_tk_many(
    monkeypatch: pytest.MonkeyPatch,
    *,
    picked: tuple[str, ...] | str,
    destroyed: list[bool],
) -> None:
    fd = types.SimpleNamespace(askopenfilenames=lambda **_kw: picked)
    fake = types.SimpleNamespace(
        TclError=_FakeTclError, Tk=lambda: _FakeRoot(destroyed), filedialog=fd
    )
    monkeypatch.setitem(sys.modules, "tkinter", cast(Any, fake))
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", cast(Any, fd))


def test_pick_files_native_returns_every_selected_path(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    _install_fake_tk_many(monkeypatch, picked=("/a.mp4", "/b.mkv", "/c.mov"), destroyed=destroyed)
    ui_ = _tty_ui()

    got = ui_.pick_files("Pick", filetypes=_AV_FILETYPES, initialdir=Path("/start"))

    assert got == ("/a.mp4", "/b.mkv", "/c.mov")
    assert destroyed == [True]


def test_pick_files_native_cancel_is_a_soft_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    # askopenfilenames returns "" on Cancel (not an empty tuple) — both must read as a
    # soft cancel, and neither may drop into the in-console fallback.
    destroyed: list[bool] = []
    _install_fake_tk_many(monkeypatch, picked="", destroyed=destroyed)
    ui_ = _tty_ui()

    def _no_fallback(_p: str) -> str | None:
        pytest.fail("fallback must not run after a native cancel")

    monkeypatch.setattr(ui_, "_path_fallback", _no_fallback)
    assert ui_.pick_files("Pick", filetypes=_AV_FILETYPES) is None


def test_native_open_many_importerror_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tkinter", cast(Any, None))
    assert RichQuestionaryUI._native_open_many("Pick", _AV_FILETYPES, None) is None


def test_pick_files_falls_back_to_one_typed_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # No tkinter (the real WSL state): one typed path, wrapped as a one-item selection.
    # A directory typed here is expanded by the caller, not by this seam.
    ui_ = _tty_ui()
    monkeypatch.setattr(RichQuestionaryUI, "_native_open_many", staticmethod(lambda *_a: None))
    monkeypatch.setattr(ui_, "_path_fallback", lambda _p: "/clips")

    assert ui_.pick_files("Pick", filetypes=_AV_FILETYPES) == ("/clips",)


def test_pick_files_fallback_blank_entry_is_a_soft_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    ui_ = _tty_ui()
    monkeypatch.setattr(RichQuestionaryUI, "_native_open_many", staticmethod(lambda *_a: None))
    monkeypatch.setattr(ui_, "_path_fallback", lambda _p: None)

    assert ui_.pick_files("Pick", filetypes=_AV_FILETYPES) is None


def test_pick_files_native_keyboardinterrupt_becomes_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    ui_ = _tty_ui()

    def _boom(*_a: object) -> tuple[str, ...] | None:
        raise KeyboardInterrupt

    monkeypatch.setattr(RichQuestionaryUI, "_native_open_many", staticmethod(_boom))
    with pytest.raises(EOFError):
        ui_.pick_files("Pick", filetypes=_AV_FILETYPES)


def test_stub_pick_files_accepts_a_sequence_a_string_and_none() -> None:
    stub = StubUI([["/a.mp4", "/b.mkv"], "/solo.mp4", None])

    assert stub.pick_files("Pick", filetypes=_AV_FILETYPES) == ("/a.mp4", "/b.mkv")
    assert stub.pick_files("Pick", filetypes=_AV_FILETYPES) == ("/solo.mp4",)
    assert stub.pick_files("Pick", filetypes=_AV_FILETYPES) is None
    assert ("pick_files", "Pick") in stub.messages


def test_stub_pick_files_empty_queue_raises_eof() -> None:
    with pytest.raises(EOFError):
        StubUI([]).pick_files("Pick", filetypes=_AV_FILETYPES)


class _FakePathQ:
    """Stands in for questionary.path(...). unsafe_ask returns the answer or raises."""

    def __init__(self, answer: Any = None, *, raises: BaseException | None = None) -> None:
        self._answer = answer
        self._raises = raises

    def unsafe_ask(self) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("/typed/clip.mp3", "/typed/clip.mp3"), ("", None), (None, None)],
)
def test_path_fallback_cancel_and_value(
    monkeypatch: pytest.MonkeyPatch, answer: Any, expected: str | None
) -> None:
    monkeypatch.setattr(questionary, "path", lambda *_a, **_k: _FakePathQ(answer))
    assert _tty_ui()._path_fallback("Type a path") == expected


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), EOFError()])
def test_path_fallback_interrupt_becomes_eof(
    monkeypatch: pytest.MonkeyPatch, interrupt: BaseException
) -> None:
    monkeypatch.setattr(questionary, "path", lambda *_a, **_k: _FakePathQ(raises=interrupt))
    with pytest.raises(EOFError):
        _tty_ui()._path_fallback("Type a path")
