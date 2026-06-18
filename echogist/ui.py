"""T2 — the UI seam (v1.1 plan §3/§6). The one place interactive I/O lives.

The menu is pure orchestration: it calls a :class:`UI` for every prompt and every
line of output, and the :class:`UI` is injected through :class:`echogist.menu.Deps`.
That seam is what keeps the killswitch CI green — questionary/prompt_toolkit need a
real TTY and break under piped stdin, so production uses :class:`RichQuestionaryUI`
(touches the terminal) while tests inject :class:`StubUI` (no TTY, no rich, no
network; records every message and pops scripted answers).

**Handles are Protocol-typed, not rich-native (plan §3).** ``progress()``/``spinner()``
return :class:`ProgressHandle`/:class:`SpinnerHandle` context managers. Yielding a raw
rich ``Progress``/``Status`` would force :class:`StubUI` to fake a rich-compatible
context manager and puncture the no-TTY purity the seam exists for. The stub returns
trivial no-op handles; swapping rich later touches only this module.

**Never-crash / exit contract (plan §6).** ``RichQuestionaryUI`` refuses to construct
without a TTY (raises :class:`NotInteractiveError` so the launcher prints one line and
exits, no prompt_toolkit traceback), and it translates every questionary cancel
(``None`` / Ctrl-C / Ctrl-D) into ``EOFError`` — the loop's existing clean-exit signal.

**Killswitch (CLAUDE.md).** ``rich``/``questionary`` are offline; nothing here reaches
the network. The module imports clean with no model, no key, no wire.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .theme import QUESTIONARY_STYLE, RICH_THEME, Glyphs, detect_caps, glyphs

# A selectable option: ``(value, label)``. ``value`` is what the flow branches on;
# ``label`` is what the operator reads. NOT a new ``Choice`` type (dropped at the
# eng-review scope gate) — a plain tuple, mapped to ``questionary.Choice`` in the adapter.
Choice = tuple[str, str]


class NotInteractiveError(Exception):
    """Raised when the production UI is built without a TTY (plan §6.1).

    The launcher catches this, prints the one-line message, and exits cleanly — a
    prompt_toolkit prompt under piped stdin would otherwise blow up with a traceback.
    """


# --------------------------------------------------------------------------- #
# The seam — Protocols
# --------------------------------------------------------------------------- #
@runtime_checkable
class ProgressHandle(Protocol):
    """A live progress bar. ``advance_to`` takes a 0.0..1.0 fraction (known duration →
    %/ETA) or a running count (unknown duration → no-ETA readout); ``done`` completes it."""

    def advance_to(self, value: float) -> None: ...
    def done(self) -> None: ...


@runtime_checkable
class SpinnerHandle(Protocol):
    """A live spinner for a non-progress wait (provision, model load, the paid call).
    ``done`` stops it and prints a ✓/✗ status line in its place."""

    def done(self, *, ok: bool = True, message: str | None = None) -> None: ...


@runtime_checkable
class UI(Protocol):
    """Every interactive surface the menu touches. Production = rich+questionary;
    tests = a scripted stub. The menu depends only on this Protocol."""

    def banner(self, title: str, subtitle: str = "") -> None: ...
    def select(self, prompt: str, choices: Sequence[Choice]) -> str: ...
    def text(self, prompt: str, *, default: str = "") -> str: ...
    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...
    def pick_file(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> str | None: ...
    def info(self, message: str) -> None: ...
    def success(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def table(self, title: str, rows: Sequence[Choice]) -> None: ...
    def progress(
        self, label: str, *, total: float = 1.0
    ) -> AbstractContextManager[ProgressHandle]: ...
    def spinner(self, label: str) -> AbstractContextManager[SpinnerHandle]: ...


# --------------------------------------------------------------------------- #
# Production adapter
# --------------------------------------------------------------------------- #
def _is_tty(stream: IO[str] | None) -> bool:
    try:
        return stream is not None and stream.isatty()
    except (ValueError, OSError):  # closed/odd stream
        return False


class RichQuestionaryUI:
    """The production UI: rich for output, questionary for arrow-key input.

    Refuses to construct without a TTY (plan §6.1). Capability detection picks the
    fancy-vs-ASCII glyph table once (plan §4). Every prompt routes through
    :meth:`_ask`, which maps cancel → ``EOFError`` (plan §6.2) so Ctrl-C/Ctrl-D exits
    the loop cleanly instead of crashing.
    """

    def __init__(self, *, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        stdin = stdin if stdin is not None else sys.stdin
        stdout = stdout if stdout is not None else sys.stdout
        if not (_is_tty(stdin) and _is_tty(stdout)):
            raise NotInteractiveError(
                "EchoGist needs an interactive terminal. Launch it from a console window, "
                "not a pipe or a redirected stream."
            )
        self.console = Console(theme=RICH_THEME)
        self.glyphs: Glyphs = glyphs(detect_caps(self.console))

    # -- input (cancel → EOFError, the loop's clean-exit) -------------------- #
    def _ask(self, question: questionary.Question) -> object:
        try:
            answer = question.ask()
        except KeyboardInterrupt as exc:  # belt-and-braces; questionary usually returns None
            raise EOFError from exc
        if answer is None:  # Ctrl-C / Ctrl-D / ESC cancel
            raise EOFError
        return answer

    def banner(self, title: str, subtitle: str = "") -> None:
        body = Text(title, style="banner")
        if subtitle:
            body.append("\n" + subtitle, style="dim")
        self.console.print(Panel(body, box=self.glyphs.box, border_style="banner", expand=False))

    def select(self, prompt: str, choices: Sequence[Choice]) -> str:
        options = [questionary.Choice(title=label, value=value) for value, label in choices]
        answer = self._ask(
            questionary.select(
                prompt, choices=options, style=QUESTIONARY_STYLE, qmark=self.glyphs.arrow
            )
        )
        return str(answer)

    def text(self, prompt: str, *, default: str = "") -> str:
        answer = self._ask(questionary.text(prompt, default=default, style=QUESTIONARY_STYLE))
        return str(answer)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        answer = self._ask(
            questionary.confirm(prompt, default=default, style=QUESTIONARY_STYLE, auto_enter=False)
        )
        return bool(answer)

    # -- file picker (TD-10) ------------------------------------------------- #
    # Cancel semantics SPLIT from the rest of the seam (which routes through
    # ``_ask``: any None → EOFError → app-exit). The picker instead distinguishes
    # two gestures:
    #
    #   dialog Cancel / empty path entry ─► None     (soft cancel → return to menu)
    #   Ctrl-C / Ctrl-D                  ─► EOFError  (the loop's clean app-exit)
    #
    # so ``pick_file`` does NOT call ``_ask``; it hand-rolls per-backend handling.
    def pick_file(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> str | None:
        """A chosen file path, or None on a soft cancel (return to menu).

        Native OS "Open File" dialog first (tkinter); a Tab-completing in-console
        prompt when tkinter is unavailable — absent (WSL/CI) or no display.
        ``initialdir`` seeds only the native dialog; the fallback ignores it.
        """
        try:
            chosen = self._native_open(prompt, filetypes, initialdir)
        except KeyboardInterrupt as exc:  # rare: Ctrl-C through the Tk modal loop
            raise EOFError from exc
        if chosen is None:  # tkinter unavailable → in-console fallback
            return self._path_fallback(prompt)
        return chosen or None  # "" = native Cancel → return to menu

    @staticmethod
    def _native_open(
        prompt: str, filetypes: Sequence[tuple[str, str]], initialdir: Path | None
    ) -> str | None:
        """The native dialog's result (``""`` on cancel), or None when tkinter is
        unavailable. Imports lazily and dual-guards ``ImportError`` (Tk absent, as
        in the WSL dev venv) and ``TclError`` (present but no display). Manages an
        explicit withdrawn root so a ``.bat`` console gets no ghost window, the
        dialog floats on top, and a second invocation starts clean (plan TD-10)."""
        try:
            import tkinter
            from tkinter import filedialog
        except ImportError:
            return None
        try:
            root = tkinter.Tk()
        except tkinter.TclError:  # no usable display
            return None
        try:
            root.withdraw()
            root.wm_attributes("-topmost", True)
            return filedialog.askopenfilename(
                title=prompt,
                filetypes=list(filetypes),
                initialdir=str(initialdir) if initialdir else "",
            )
        finally:
            root.destroy()  # never leak the hidden root

    def _path_fallback(self, prompt: str) -> str | None:
        """In-console Tab-completing path entry (no GUI). Empty / ESC → None (menu);
        Ctrl-C / Ctrl-D → EOFError (app-exit). Uses ``unsafe_ask`` so an interrupt
        propagates (→ EOFError) instead of being swallowed to None like a soft cancel."""
        question = questionary.path(prompt, style=QUESTIONARY_STYLE)
        try:
            answer = question.unsafe_ask()
        except (KeyboardInterrupt, EOFError) as exc:
            raise EOFError from exc
        return str(answer) if answer else None

    # -- output -------------------------------------------------------------- #
    def info(self, message: str) -> None:
        self.console.print(message, style="info", markup=False)

    def success(self, message: str) -> None:
        self.console.print(f"{self.glyphs.ok} {message}", style="success", markup=False)

    def warn(self, message: str) -> None:
        self.console.print(message, style="warn", markup=False)

    def error(self, message: str) -> None:
        self.console.print(message, style="error", markup=False)

    def table(self, title: str, rows: Sequence[Choice]) -> None:
        table = Table(title=title, box=self.glyphs.box, show_header=False, title_style="heading")
        table.add_column("field", style="dim")
        table.add_column("value")
        for key, value in rows:
            table.add_row(key, value)
        self.console.print(table)

    @contextmanager
    def progress(self, label: str, *, total: float = 1.0) -> Iterator[ProgressHandle]:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        ) as prog:
            task_id = prog.add_task(label, total=total)
            handle = _RichProgressHandle(prog, task_id, total)
            try:
                yield handle
            finally:
                handle.done()

    @contextmanager
    def spinner(self, label: str) -> Iterator[SpinnerHandle]:
        status = self.console.status(label, spinner="dots")
        status.start()
        handle = _RichSpinnerHandle(self.console, status, label, self.glyphs)
        try:
            yield handle
        finally:
            handle._stop()  # idempotent: no double status if done() already ran


class _RichProgressHandle:
    """rich-backed :class:`ProgressHandle`. Clamps to the display total so a count-mode
    value (unknown-duration audio) never overruns the bar."""

    def __init__(self, prog: Progress, task_id: TaskID, total: float) -> None:
        self._prog = prog
        self._task_id = task_id
        self._total = total if total > 0 else 1.0

    def advance_to(self, value: float) -> None:
        self._prog.update(self._task_id, completed=min(value, self._total))

    def done(self) -> None:
        self._prog.update(self._task_id, completed=self._total)


class _RichSpinnerHandle:
    """rich-backed :class:`SpinnerHandle`. ``done`` stops the live spinner and prints a
    ✓/✗ status line in its place (replacing the bare 'GPU preflight OK', plan §1.3)."""

    def __init__(self, console: Console, status: object, label: str, glyphs: Glyphs) -> None:
        self._console = console
        self._status = status
        self._label = label
        self._glyphs = glyphs
        self._running = True

    def _stop(self) -> None:
        if self._running:
            self._status.stop()  # type: ignore[attr-defined]
            self._running = False

    def done(self, *, ok: bool = True, message: str | None = None) -> None:
        self._stop()
        glyph = self._glyphs.ok if ok else self._glyphs.fail
        self._console.print(
            f"{glyph} {message or self._label}",
            style="success" if ok else "error",
            markup=False,
        )


def build_default_ui() -> UI:
    """The production UI, or raise :class:`NotInteractiveError` (plan §6.1)."""
    return RichQuestionaryUI()


# --------------------------------------------------------------------------- #
# Test double (offline, no TTY, no rich) — the killswitch-safe UI (plan §3)
# --------------------------------------------------------------------------- #
class _StubProgressHandle:
    """No-op :class:`ProgressHandle`: records advances so a test can assert progress
    fired, but renders nothing (no TTY)."""

    def __init__(self, recorder: list[float]) -> None:
        self._recorder = recorder

    def advance_to(self, value: float) -> None:
        self._recorder.append(value)

    def done(self) -> None:
        pass


class _StubSpinnerHandle:
    def done(self, *, ok: bool = True, message: str | None = None) -> None:
        pass


class StubUI:
    """Scripted, offline UI for tests. ``select``/``text``/``confirm`` pop the next queued
    answer (empty queue → ``EOFError``, mirroring a closed stdin → clean exit); every
    message is recorded as ``(level, plain text)`` for assertions. No TTY, no rich, no
    network — the whole menu runs under it in CI."""

    def __init__(self, answers: Sequence[object] | None = None) -> None:
        self.answers: list[object] = list(answers or [])
        self.messages: list[tuple[str, str]] = []
        self.progress_values: list[float] = []

    def _pop(self) -> object:
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    @property
    def log_text(self) -> str:
        """Every recorded message joined — for substring assertions in tests."""
        return "\n".join(text for _level, text in self.messages)

    def banner(self, title: str, subtitle: str = "") -> None:
        self.messages.append(("banner", title))

    def select(self, prompt: str, choices: Sequence[Choice]) -> str:
        self.messages.append(("select", prompt))
        return str(self._pop())

    def text(self, prompt: str, *, default: str = "") -> str:
        self.messages.append(("text", prompt))
        return str(self._pop())

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.messages.append(("confirm", prompt))
        return bool(self._pop())

    def pick_file(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> str | None:
        """Pops the next queued answer. Three states, the only ones: a path string
        → that path; a queued ``None`` → soft cancel (return to menu); empty queue
        → ``EOFError`` (clean exit). A deliberate interrupt is not separately
        queueable — it collapses into the empty-queue EOFError."""
        self.messages.append(("pick_file", prompt))
        answer = self._pop()
        return None if answer is None else str(answer)

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def success(self, message: str) -> None:
        self.messages.append(("success", message))

    def warn(self, message: str) -> None:
        self.messages.append(("warn", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))

    def table(self, title: str, rows: Sequence[Choice]) -> None:
        self.messages.append(("table", title))
        for key, value in rows:
            self.messages.append(("table-row", f"{key}: {value}"))

    @contextmanager
    def progress(self, label: str, *, total: float = 1.0) -> Iterator[ProgressHandle]:
        self.messages.append(("progress", label))
        yield _StubProgressHandle(self.progress_values)

    @contextmanager
    def spinner(self, label: str) -> Iterator[SpinnerHandle]:
        self.messages.append(("spinner", label))
        yield _StubSpinnerHandle()
