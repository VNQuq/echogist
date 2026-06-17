"""UI-seam tests (T2 / T9).

The live ``RichQuestionaryUI`` prompts are TTY-bound and operator-verified on Windows
(§7) — not exercised here. What IS unit-testable off-TTY: the no-TTY construction guard
(§6.1), the cancel → clean-exit translation (§6.2 / T9), and the offline ``StubUI``
double the whole menu suite rides on. Killswitch-safe: no model, no key, no network.
"""

from __future__ import annotations

import io
import sys
from typing import Any, cast

import pytest

from echogist.ui import (
    UI,
    NotInteractiveError,
    RichQuestionaryUI,
    StubUI,
    build_default_ui,
)


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
