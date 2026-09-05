"""UI-seam tests (T2 / T9).

The live ``RichQuestionaryUI`` prompts are TTY-bound and operator-verified on Windows
(§7) — not exercised here. What IS unit-testable off-TTY: the no-TTY construction guard
(§6.1), the cancel → clean-exit translation (§6.2 / T9), and the offline ``StubUI``
double the whole menu suite rides on. Killswitch-safe: no model, no key, no network.
"""

from __future__ import annotations

import io
import os
import re
import sys
import types
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import questionary
from prompt_toolkit.key_binding import KeyBindings
from rich.text import Text

from echogist.ui import (
    UI,
    NotInteractiveError,
    RichQuestionaryUI,
    RunLog,
    StubUI,
    build_default_ui,
    human_size,
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

    fd = types.SimpleNamespace(
        askopenfilename=lambda **_kw: picked,
        askdirectory=lambda **_kw: picked,
    )
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


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, "0 B"),
        (1023, "1,023 B"),
        (1024, "1.0 KB"),
        (1024**2, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (5 * 1024**4, "5,120.0 GB"),  # past the last named unit, still readable
    ],
)
def test_human_size_units(total: int, expected: str) -> None:
    assert human_size(total) == expected


# --------------------------------------------------------------------------- #
# pick_dir — the folder sibling (bulk v3 increment 1). Same cancel split as
# pick_file; askdirectory takes no filetypes, which is why it is its own seam.
# --------------------------------------------------------------------------- #
def test_pick_dir_native_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="/lectures/course-1", destroyed=destroyed)
    ui_ = _tty_ui()
    got = ui_.pick_dir("Pick a folder", initialdir=Path("/start"))
    assert got == "/lectures/course-1"
    assert destroyed == [True]  # the hidden root was always destroyed


def test_pick_dir_native_cancel_returns_none_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # Native Cancel returns "" — a soft cancel (→ menu), NOT a drop into the in-console
    # fallback. Make the fallback explode so we prove it is not reached.
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="", destroyed=destroyed)
    ui_ = _tty_ui()

    def _no_fallback(_p: str) -> str | None:
        pytest.fail("fallback must not run after a native cancel")

    monkeypatch.setattr(ui_, "_path_fallback", _no_fallback)
    assert ui_.pick_dir("Pick a folder") is None
    assert destroyed == [True]


def test_native_open_dir_importerror_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tkinter", cast(Any, None))
    assert RichQuestionaryUI._native_open_dir("Pick a folder", None) is None


def test_native_open_dir_tclerror_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    _install_fake_tk(monkeypatch, picked="x", destroyed=destroyed, raise_tcl=True)
    assert RichQuestionaryUI._native_open_dir("Pick a folder", None) is None
    assert destroyed == []  # root was never created → nothing to destroy


def test_pick_dir_falls_back_when_tk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real WSL state: no tkinter, so the operator types a path instead.
    ui_ = _tty_ui()
    monkeypatch.setattr(RichQuestionaryUI, "_native_open_dir", staticmethod(lambda *_a: None))
    monkeypatch.setattr(ui_, "_path_fallback", lambda _p: "/typed/folder")
    assert ui_.pick_dir("Pick a folder") == "/typed/folder"


def test_pick_dir_native_keyboardinterrupt_becomes_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    ui_ = _tty_ui()

    def _boom(*_a: object) -> str | None:
        raise KeyboardInterrupt

    monkeypatch.setattr(RichQuestionaryUI, "_native_open_dir", staticmethod(_boom))
    with pytest.raises(EOFError):
        ui_.pick_dir("Pick a folder")


# --------------------------------------------------------------------------- #
# RunLog (session transcript) — a run must survive its terminal
# --------------------------------------------------------------------------- #
def test_runlog_records_every_level_with_a_timestamp(tmp_path: Path) -> None:
    log = RunLog(tmp_path / "logs" / "session.log")
    log.write("plain line")
    log.write("something odd", level="WARN")
    log.write("broke", level="ERROR")

    body = (tmp_path / "logs" / "session.log").read_text(encoding="utf-8")
    assert "EchoGist session" in body  # the header that separates one launch from the next
    assert "plain line" in body
    assert "WARN" in body and "something odd" in body
    assert "ERROR" in body and "broke" in body
    # Every message line carries a clock, so a hang can be located afterwards.
    for line in body.splitlines():
        if "plain line" in line:
            assert re.match(r"^\[\d\d:\d\d:\d\d\] ", line)


def test_runlog_splits_a_multiline_message(tmp_path: Path) -> None:
    """A cost table or an ffmpeg dump must not land as one unreadable line."""
    path = tmp_path / "s.log"
    RunLog(path).write("first\nsecond", level="INFO")

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "INFO" in ln]
    assert len(lines) == 2


def test_runlog_that_cannot_be_written_disables_itself_silently(tmp_path: Path) -> None:
    """A log is a courtesy. It must never take down the run it exists to record."""
    blocker = tmp_path / "blocked"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")

    log = RunLog(blocker / "nested" / "session.log")  # mkdir under a FILE -> OSError

    assert log.path is None
    log.write("this must not raise")  # the whole point


def test_runlog_write_failure_mid_run_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "s.log"
    log = RunLog(path)
    path.unlink()
    path.mkdir()  # the file's name is now a directory: every further write fails

    log.write("still must not raise")

    assert log.path is None, "one failure disables the sink instead of raising per line"


def test_the_production_ui_mirrors_its_output_into_the_run_log(tmp_path: Path) -> None:
    """The wiring, not the sink: RunLog on its own passing proves nothing if no console
    method calls it. Every level the operator can see must land in the file."""
    path = tmp_path / "session.log"
    real = RichQuestionaryUI(stdin=_TTY(), stdout=_TTY(), log_path=path)

    real.banner("EchoGist", "local transcription + summary")
    real.info("Reusing 7 saved transcript(s).")
    real.success("Summary received")
    real.warn("Couldn't save the MP3")
    real.error("Summarization failed")
    real.table("Transcribed", [("Transcribed", "7"), ("Failed", "0")])
    with real.spinner("Summarizing (34 cloud calls)"):
        pass

    body = path.read_text(encoding="utf-8")
    for expected in (
        # NOT the bare title: RunLog's own session header already says "EchoGist", so
        # asserting that would pass with the banner unwired. The subtitle is banner-only.
        "local transcription + summary",
        "Reusing 7 saved transcript(s).",
        "Summary received",
        "Couldn't save the MP3",
        "Summarization failed",
        "Transcribed: 7",  # the table's rows, not just its title
        "Summarizing (34 cloud calls)",
    ):
        assert expected in body, f"{expected!r} never reached the run log"


def test_a_ui_built_without_a_log_path_writes_nothing(tmp_path: Path) -> None:
    """Tests and any non-logging caller must not need a writable output tree."""
    real = RichQuestionaryUI(stdin=_TTY(), stdout=_TTY())

    real.info("no sink configured")  # must not raise

    assert real.runlog.path is None
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# drain_input — the buffered-keystroke fix
# --------------------------------------------------------------------------- #
def test_drain_input_flushes_the_posix_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    import termios

    flushed: list[tuple[int, int]] = []
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr(termios, "tcflush", lambda fd, queue: flushed.append((fd, queue)))
    with open(os.devnull) as real_stdin:  # pytest's captured stdin has no fileno()
        monkeypatch.setattr(sys, "stdin", real_stdin)
        _tty_ui().drain_input()
    assert flushed and flushed[0][1] == termios.TCIFLUSH  # input queue, not output


def test_drain_input_never_raises_on_a_terminal_that_cannot_be_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished hour-long run must not end in a traceback because a stream could not be
    flushed. Every failure here is swallowed; the terminal just keeps its old behavior."""
    import termios

    monkeypatch.setattr("os.name", "posix")

    def _boom(fd: int, queue: int) -> None:
        raise termios.error("not a terminal")

    monkeypatch.setattr(termios, "tcflush", _boom)
    with open(os.devnull) as real_stdin:
        monkeypatch.setattr(sys, "stdin", real_stdin)
        _tty_ui().drain_input()  # no exception


def test_stub_ui_records_the_drain() -> None:
    stub = StubUI()
    stub.drain_input()
    assert ("drain", "") in stub.messages


# --------------------------------------------------------------------------- #
# Output styling — one style per line, chosen here and nowhere else
# --------------------------------------------------------------------------- #
def test_every_printed_line_disables_markup_and_highlighting() -> None:
    """Two promises, one assertion.

    ``markup=False`` keeps a filename or an ffmpeg line carrying ``[...]`` as text — a raw
    ``[libmp3lame @ 0x7f] error`` must not render as the word "error", and a stray ``[/]``
    must not raise MarkupError in the middle of a run report. ``highlight=False`` keeps
    rich's repr highlighter from recoloring numbers, paths and times INSIDE our strings,
    which is what turned a muted sub-step into bright cyan digits on a dim line.
    """
    ui = _tty_ui()
    ui.console = MagicMock()

    ui.info("a [tag] 1")
    ui.detail("b 2")
    ui.success("c 3")
    ui.warn("d 4")
    ui.error("e 5")

    assert ui.console.print.call_count == 5
    for call in ui.console.print.call_args_list:
        assert call.kwargs["markup"] is False
        assert call.kwargs["highlight"] is False


def test_a_rule_carries_the_glyph_tables_character() -> None:
    """Rules are drawn with our own character, not a hardcoded one: rich degrades its box
    drawing on legacy cmd, but not a Unicode dash we place in the call ourselves."""
    ui = _tty_ui()
    ui.console = MagicMock()

    ui.rule("[1/7] lecture.mp4")

    (rendered,) = ui.console.print.call_args.args
    assert rendered.characters == ui.glyphs.rule
    # TD-32: the line and the file name are styled separately, so the name can carry weight
    # while the line stays dark. Passing one style name to both is what made the header
    # indistinguishable from `info`.
    assert rendered.style == "rule"
    assert isinstance(rendered.title, Text)
    assert rendered.title.style == "rule.title"


def test_stub_ui_records_rules_and_details() -> None:
    stub = StubUI()
    stub.rule("[1/2] a.mp4")
    stub.detail("phase 1/6")
    assert ("rule", "[1/2] a.mp4") in stub.messages
    assert ("detail", "phase 1/6") in stub.messages
