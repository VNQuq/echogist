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

import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import IO, Protocol, runtime_checkable

import questionary
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
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
from rich.rule import Rule
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


# Reveal-folder priority (TD-14, revised TD-12): the once-per-launch pop fires for the
# highest priority seen, so the richest deliverable's folder always supersedes a lower
# one already shown this launch — even if the lower-priority run came first. Ordering is
# by how much the operator gets out of that run: a summary (audio + transcript + summary)
# outranks a transcript (audio + transcript), which outranks a bare MP3-only run.
REVEAL_AUDIO = 1
REVEAL_TRANSCRIPT = 2
REVEAL_SUMMARY = 3


# --------------------------------------------------------------------------- #
# The seam — Protocols
# --------------------------------------------------------------------------- #
@runtime_checkable
class ProgressHandle(Protocol):
    """A live progress bar. ``advance_to`` takes a 0.0..1.0 fraction (known duration →
    %/ETA) or a running count (unknown duration → no-ETA readout); ``done`` completes it.

    ``fail`` is the failure-aware counterpart to ``done`` (TD-17): when the stage inside
    the bar raises, the context manager calls ``fail`` INSTEAD of ``done`` so the bar is
    stopped at its last real fraction rather than snapped to a false 100% right before the
    error panel. The two are mutually exclusive per run — exactly one fires on context exit.
    """

    def advance_to(self, value: float) -> None: ...
    def done(self) -> None: ...
    def fail(self) -> None: ...


@runtime_checkable
class SpinnerHandle(Protocol):
    """A live spinner for a non-progress wait (provision, model load, the paid call).
    ``done`` stops it and prints a ✓/✗ status line in its place.

    ``update`` replaces the label while it spins. A long wait with a static label is
    indistinguishable from a hung process — which is how the operator lost a folder run on
    2026-09-04, closing a console that was working. Every spinner also carries an elapsed
    clock, so "nothing is happening" and "this call is slow" stop looking the same.
    """

    def done(self, *, ok: bool = True, message: str | None = None) -> None: ...
    def update(self, label: str) -> None: ...


def human_size(total: int) -> str:
    """Bytes as a short human string — the "how much is this" proxy for a selection.

    Lives here rather than in ``menu`` because ``menu`` imports ``scan`` for the scan
    flow, so ``scan`` importing ``menu`` back for the formatter would be a cycle — and
    two copies would render the same volume differently in two places.
    """
    size = float(total)
    if size < 1024.0:
        return f"{size:,.0f} B"
    for unit in ("KB", "MB"):
        size /= 1024.0
        if size < 1024.0:
            return f"{size:,.1f} {unit}"
    return f"{size / 1024.0:,.1f} GB"


@runtime_checkable
class UI(Protocol):
    """Every interactive surface the menu touches. Production = rich+questionary;
    tests = a scripted stub. The menu depends only on this Protocol."""

    def banner(self, title: str, subtitle: str = "") -> None: ...
    def clear(self) -> None: ...
    def drain_input(self) -> None: ...
    def select(self, prompt: str, choices: Sequence[Choice]) -> str: ...
    def text(self, prompt: str, *, default: str = "") -> str: ...
    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...
    def pick_file(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> str | None: ...
    def pick_files(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> tuple[str, ...] | None: ...
    def pick_dir(self, prompt: str, *, initialdir: Path | None = None) -> str | None: ...
    def reveal_dir(self, path: Path, *, priority: int = REVEAL_AUDIO) -> None: ...
    def rule(self, title: str) -> None: ...
    def info(self, message: str) -> None: ...
    def detail(self, message: str) -> None: ...
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


def _is_control_value(value: str) -> bool:
    """True for a navigation control choice — a dunder-wrapped value such as
    ``__back__`` or ``__cancel__``. Content choices (``"1"``, a file path, a settings
    value) never match, so they keep the normal style; controls render muted."""
    return value.startswith("__") and value.endswith("__")


class RunLog:
    """Append-only plain-text mirror of everything the console said this launch.

    The console is where EchoGist reports, and a console is a terrible record: it wraps,
    it scrolls, and it dies with the window. A folder run is HOURS long and prints the
    only copy of what it transcribed, what it dropped, what it quoted and what it actually
    spent — losing that to a closed terminal (which is exactly how the operator lost the
    2026-09-04 run) means the run is unauditable afterwards.

    Deliberately dumb: one file per launch, one timestamped line per message, plain text,
    no rotation and no levels beyond a tag. It is a transcript of the session, not
    telemetry. Writes are best-effort — a log that cannot be written must never take down
    the run it is logging, so any failure disables the sink and is otherwise ignored.
    """

    def __init__(self, path: Path) -> None:
        self._path: Path | None = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== EchoGist session {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        except OSError:
            self._path = None  # unwritable (locked dir, read-only media): log nothing, run on

    @property
    def path(self) -> Path | None:
        """Where this session is being recorded, or None if the sink is disabled."""
        return self._path

    def write(self, message: str, *, level: str = "info") -> None:
        if self._path is None:
            return
        stamp = f"{datetime.now():%H:%M:%S}"
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                for line in str(message).splitlines() or [""]:
                    fh.write(f"[{stamp}] {level:<6} {line}\n")
        except OSError:
            self._path = None  # went away mid-run; stop trying rather than raise per line


class _NullLog:
    """The sink when no path was given (tests, or a UI built without a base directory)."""

    path: Path | None = None

    def write(self, message: str, *, level: str = "info") -> None:
        return


class RichQuestionaryUI:
    """The production UI: rich for output, questionary for arrow-key input.

    Refuses to construct without a TTY (plan §6.1). Capability detection picks the
    fancy-vs-ASCII glyph table once (plan §4). Every prompt routes through
    :meth:`_ask`, which maps cancel → ``EOFError`` (plan §6.2) so Ctrl-C/Ctrl-D exits
    the loop cleanly instead of crashing.
    """

    def __init__(
        self,
        *,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
        log_path: Path | None = None,
    ) -> None:
        stdin = stdin if stdin is not None else sys.stdin
        stdout = stdout if stdout is not None else sys.stdout
        if not (_is_tty(stdin) and _is_tty(stdout)):
            raise NotInteractiveError(
                "EchoGist needs an interactive terminal. Launch it from a console window, "
                "not a pipe or a redirected stream."
            )
        self.console = Console(theme=RICH_THEME)
        self.glyphs: Glyphs = glyphs(detect_caps(self.console))
        # TD-14: reveal the output folder at most once per launch (= per UI instance),
        # for the highest priority seen. 0 = nothing revealed yet. The guard lives here
        # so the menu stays declarative.
        self._revealed_priority = 0
        # Everything printed below is mirrored here, so a run survives its terminal.
        self.runlog: RunLog | _NullLog = RunLog(log_path) if log_path is not None else _NullLog()

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
        self.runlog.write(f"{title} — {subtitle}" if subtitle else title, level="BANNER")
        body = Text(title, style="banner")
        if subtitle:
            body.append("\n" + subtitle, style="dim")
        self.console.print(Panel(body, box=self.glyphs.box, border_style="banner", expand=False))

    def clear(self) -> None:
        """Wipe the console (TD-11). The menu calls this on entry to each flow so the
        prior cycle's answered prompts and tables don't pile up; the working log of the
        flow about to run starts on a clean screen."""
        self.console.clear()

    def drain_input(self) -> None:
        """Discard anything already typed at the console, unread.

        A folder run holds the terminal for an hour with no prompt on screen. Every
        keystroke made in that window — a key pressed to check the machine is alive, a
        stray click-through, a wake-up tap — sits in the OS input buffer, and the next
        prompt reads it the instant it opens. That is the mechanism behind the 2026-09-04
        run, which finished seven summaries and then opened a folder picker nobody asked
        for: the main menu appeared, took an answer it was not given, and launched the row
        that answer selected. What produced the keystroke was never established — the log
        shows only that the menu was answered instantly and that the answer was row 4. The
        mechanism is the part a fix can close, and this closes it whatever the source.

        So the menu drains the buffer before asking anything after a flow. Type-ahead into
        a fresh prompt still works; only input typed while EchoGist was not asking is
        dropped. Every failure mode here is non-fatal — a terminal that cannot be drained
        just keeps its old behavior — so the whole thing is wrapped: this must never be the
        reason a finished run ends in a traceback.
        """
        with suppress(Exception):
            if os.name == "nt":
                import msvcrt

                while msvcrt.kbhit():  # type: ignore[attr-defined]  # nt-only, guarded
                    msvcrt.getwch()  # type: ignore[attr-defined]  # nt-only, guarded
                return
            import termios

            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)

    def select(self, prompt: str, choices: Sequence[Choice]) -> str:
        # A dunder-wrapped value (``__back__`` / ``__cancel__``) is a navigation control,
        # not a content option: render its label with the muted ``control`` style so it
        # reads as subtle chrome. questionary takes a formatted-text title verbatim, so
        # the grey survives the arrow pointer landing on the row.
        options = [
            questionary.Choice(
                title=[("class:control", label)] if _is_control_value(value) else label,
                value=value,
            )
            for value, label in choices
        ]
        # Hidden number quick-select (1-9): the default "(Use arrow keys)" hint is
        # extended in place — same muted grey — to reveal the binding elegantly, with
        # the upper bound matched to this menu's size (never promising a key past the
        # last row). One row → no number worth advertising, so keep the bare hint.
        bound = min(len(options), 9)
        instruction = f"(Use arrow keys or 1-{bound})" if bound >= 2 else "(Use arrow keys)"
        question = questionary.select(
            prompt,
            choices=options,
            style=QUESTIONARY_STYLE,
            qmark=self.glyphs.arrow,
            instruction=instruction,
        )
        self._bind_number_keys(question, options)
        answer = self._ask(question)
        return str(answer)

    @staticmethod
    def _bind_number_keys(
        question: questionary.Question, options: Sequence[questionary.Choice]
    ) -> None:
        """Wire hidden 1-9 quick-select onto a select prompt: pressing digit *N* picks
        the *N*-th visible row immediately (including a ``← Back`` row — it is just a
        position). Position-based, so it works for every menu regardless of the choices'
        own values, and lives here once so all menus/submenus get it.

        Only digit keys are added; the cancel/exit keys (Ctrl-C / Ctrl-D / ESC → None →
        ``EOFError`` via :meth:`_ask`) are left untouched, so the load-bearing exit
        contract (TD-13) is unaffected. questionary builds the prompt on a prompt_toolkit
        ``Application`` whose ``KeyBindings`` we extend in place before ``ask()``;
        ``KeyBindings.add`` bumps a version counter, so the additions take effect.
        """
        bindings = question.application.key_bindings
        if not isinstance(bindings, KeyBindings):  # defensive; questionary uses KeyBindings
            return

        def _make(value: object) -> Callable[[KeyPressEvent], None]:
            def _handler(event: KeyPressEvent) -> None:
                event.app.exit(result=value)

            return _handler

        for index, choice in enumerate(options[:9]):
            bindings.add(str(index + 1), eager=True)(_make(choice.value))

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
        # tkinter's typeshed stubs ship with mypy and resolve under --strict (verified
        # TD-10 T4: Tk / askopenfilename type-check as real signatures, not Any), so the
        # lazy import below is fully checked even though tkinter is absent at runtime in
        # the WSL dev venv. No `type: ignore` needed.
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

    def pick_files(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> tuple[str, ...] | None:
        """Several chosen file paths, or None on a soft cancel (return to menu).

        The multi-select sibling of :meth:`pick_file`, with the same split cancel
        semantics. In the native dialog Shift-click takes a range, Ctrl-click picks
        individually and Ctrl+A takes everything *visible* — which the ``filetypes``
        filter has already narrowed to media, so Ctrl+A in a folder of lectures grabs
        the videos and leaves the .txt and the cover art behind.

        The no-tkinter fallback (WSL, or a Windows box with a broken Tk) can only take one
        typed path, so it accepts a **directory** there — typing twelve paths by hand is
        not a workflow. Expanding that directory is the caller's job; this seam stays
        free of any knowledge about media formats.
        """
        try:
            chosen = self._native_open_many(prompt, filetypes, initialdir)
        except KeyboardInterrupt as exc:  # rare: Ctrl-C through the Tk modal loop
            raise EOFError from exc
        if chosen is None:  # tkinter unavailable → in-console fallback
            single = self._path_fallback(prompt)
            return (single,) if single else None
        return chosen or None  # empty = native Cancel → return to menu

    @staticmethod
    def _native_open_many(
        prompt: str, filetypes: Sequence[tuple[str, str]], initialdir: Path | None
    ) -> tuple[str, ...] | None:
        """The native multi-select dialog's result (empty on cancel), or None when
        tkinter is unavailable. Same lazy import and dual ImportError/TclError guard as
        :meth:`_native_open`; ``askopenfilenames`` returns ``""`` on cancel and a tuple of
        paths otherwise, so both shapes collapse to an empty tuple here."""
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
            chosen = filedialog.askopenfilenames(
                title=prompt,
                filetypes=list(filetypes),
                initialdir=str(initialdir) if initialdir else "",
            )
            return tuple(chosen) if chosen else ()
        finally:
            root.destroy()  # never leak the hidden root

    def pick_dir(self, prompt: str, *, initialdir: Path | None = None) -> str | None:
        """A chosen directory path, or None on a soft cancel (return to menu).

        The folder sibling of :meth:`pick_file`, with the same split cancel semantics and
        the same no-tkinter fallback. ``askdirectory`` takes no ``filetypes`` — a folder
        has no extension to filter on — which is why this is its own seam rather than a
        flag on ``pick_file``.
        """
        try:
            chosen = self._native_open_dir(prompt, initialdir)
        except KeyboardInterrupt as exc:  # rare: Ctrl-C through the Tk modal loop
            raise EOFError from exc
        if chosen is None:  # tkinter unavailable → in-console fallback
            return self._path_fallback(prompt)
        return chosen or None  # "" = native Cancel → return to menu

    @staticmethod
    def _native_open_dir(prompt: str, initialdir: Path | None) -> str | None:
        """The native folder dialog's result (``""`` on cancel), or None when tkinter is
        unavailable. Same lazy import and dual ImportError/TclError guard as
        :meth:`_native_open`."""
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
            return filedialog.askdirectory(
                title=prompt,
                initialdir=str(initialdir) if initialdir else "",
                mustexist=True,
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

    # -- reveal (TD-14) ------------------------------------------------------ #
    def reveal_dir(self, path: Path, *, priority: int = REVEAL_AUDIO) -> None:
        """Open the OS file browser at ``path`` in the background — once per launch.

        Fires once per launch on the folder the chosen flow produced: the ``summaries``
        folder after a summary (``REVEAL_SUMMARY``), the ``transcripts`` folder after a
        transcript-only run (``REVEAL_TRANSCRIPT``, TD-12), or the ``audio`` folder after an
        MP3-only run (``REVEAL_AUDIO``). A higher ``priority`` supersedes a lower one already
        shown this launch, so a summary's folder always wins over an earlier transcript or
        audio pop. Windows only (guarded on ``nt``); on
        the WSL dev box it is a no-op. The pop opens *without* stealing focus from the
        console (the operator's "в фоне"). Failure is non-fatal — revealing a folder must
        never mask a completed run — but instead of swallowing it silently we log a
        one-line fallback so a missing pop is diagnosable."""
        if priority <= self._revealed_priority:
            return
        self._revealed_priority = priority
        if os.name != "nt":
            return
        if not self._open_in_background(path):
            self.info(f"Folder ready: {path}")

    @staticmethod
    def _open_in_background(path: Path) -> bool:
        """Open ``path`` in the file browser without stealing console focus. True on a
        confirmed open, False otherwise.

        ``ShellExecuteW`` with ``SW_SHOWNOACTIVATE`` (4) opens Explorer behind the
        console — true no-focus-steal, which ``os.startfile`` cannot promise. If the
        ctypes call is unavailable or fails, fall back to ``os.startfile`` (which at
        least opens the folder, even if it may foreground)."""
        sw_shownoactivate = 4
        with suppress(OSError, AttributeError, ValueError):
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]  # nt-only
                None, "open", str(path), None, None, sw_shownoactivate
            )
            if int(result) > 32:  # ShellExecuteW: an HINSTANCE > 32 means success
                return True
        with suppress(OSError):
            os.startfile(str(path))  # type: ignore[attr-defined]  # nt-only, guarded by caller
            return True
        return False

    # -- output -------------------------------------------------------------- #
    def rule(self, title: str) -> None:
        """A titled horizontal rule — the header of one unit of work in a long run.

        A folder run prints sixty-odd lines of equal weight, and finding where lecture
        five started means reading all of them. A rule per file turns that wall into
        sections the eye can skip through, which is the whole difference between a log
        that scrolls past and one an operator can actually use an hour later.
        """
        self.runlog.write(title, level="SECTION")
        self.console.print(
            Rule(Text(title, style="rule"), characters=self.glyphs.rule, style="rule")
        )

    # Every print below passes markup=False AND highlight=False. markup=False is the old
    # promise: a filename or an ffmpeg line carrying [...] is text, not console markup.
    # highlight=False is the other half, added when the styled output was first looked at
    # end to end: rich's repr highlighter recolors numbers, paths, times and quotes INSIDE
    # our strings, so a muted sub-step came out with bright cyan digits and yellow dots —
    # the style we asked for lost an argument with a highlighter nobody asked for. Every
    # line now renders in exactly one style: the one this method chose.
    def info(self, message: str) -> None:
        self.runlog.write(message)
        self.console.print(message, style="info", markup=False, highlight=False)

    def detail(self, message: str) -> None:
        """A sub-step inside the current section — muted, and never the point.

        The phase-by-phase synthesis lines live here. They exist to prove the run is
        moving and to be scrolled back to when it is not; at the same weight as the
        result they bury it.
        """
        self.runlog.write(message, level="DETAIL")
        self.console.print(message, style="detail", markup=False, highlight=False)

    def success(self, message: str) -> None:
        self.runlog.write(message, level="OK")
        self.console.print(
            f"{self.glyphs.ok} {message}", style="success", markup=False, highlight=False
        )

    def warn(self, message: str) -> None:
        self.runlog.write(message, level="WARN")
        self.console.print(message, style="warn", markup=False, highlight=False)

    def error(self, message: str) -> None:
        self.runlog.write(message, level="ERROR")
        self.console.print(message, style="error", markup=False, highlight=False)

    def table(self, title: str, rows: Sequence[Choice]) -> None:
        """Render rows as a two-column table, treating every cell as PLAIN TEXT.

        The ``Text()`` wrap is the same promise ``info``/``warn``/``error`` make with
        ``markup=False``, and it became load-bearing when the batch failure table started
        carrying operator filenames and raw ffmpeg stderr. rich reads ``[...]`` in a bare
        string as console markup, so ``[libmp3lame @ 0x7f] error`` renders as ``error`` —
        silently eating the one token that says which stage broke — and a stray ``[/]``
        raises ``MarkupError``, which would take out the whole post-batch report.
        """
        self.runlog.write(title, level="TABLE")
        for key, value in rows:
            self.runlog.write(f"  {key}: {value}", level="TABLE")
        table = Table(title=title, box=self.glyphs.box, show_header=False, title_style="heading")
        table.add_column("field", style="dim")
        table.add_column("value")
        for key, value in rows:
            table.add_row(Text(key), Text(value))
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
            # TD-17: complete the bar on a clean exit, but STOP (don't fill) it when the
            # stage raised — a false 100% must never precede the error panel. ``BaseException``
            # so a KeyboardInterrupt / GeneratorExit mid-stage is treated as a failure too,
            # not a success; the exception is always re-raised, so the loop's error handling
            # (warn/degrade or fatal return-to-menu) is unchanged.
            try:
                yield handle
            except BaseException:
                handle.fail()
                raise
            else:
                handle.done()

    @contextmanager
    def spinner(self, label: str) -> Iterator[SpinnerHandle]:
        self.runlog.write(label, level="START")
        live = _ElapsedLabel(label)
        status = self.console.status(live, spinner="dots")
        status.start()
        handle = _RichSpinnerHandle(self.console, status, live, self.glyphs)
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

    def fail(self) -> None:
        """Stop the bar WITHOUT completing it (TD-17): freeze the timer and leave the bar
        at its last real fraction so a failed stage shows how far it got, not a false 100%.
        ``stop_task`` marks the task stopped (elapsed/ETA freeze) but never touches
        ``completed``; rich stops rendering when the enclosing ``Progress`` context exits."""
        self._prog.stop_task(self._task_id)


class _ElapsedLabel:
    """A spinner label that re-renders itself, so the clock ticks between log lines.

    rich re-renders a live status on every refresh, calling ``__rich__`` each time; the
    elapsed figure is therefore computed at draw time rather than frozen at creation.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self._started = monotonic()

    def __rich__(self) -> str:
        seconds = int(monotonic() - self._started)
        return f"{self.label}  [{seconds // 60}:{seconds % 60:02d}]"


class _RichSpinnerHandle:
    """rich-backed :class:`SpinnerHandle`. ``done`` stops the live spinner and prints a
    ✓/✗ status line in its place (replacing the bare 'GPU preflight OK', plan §1.3)."""

    def __init__(
        self, console: Console, status: object, label: _ElapsedLabel, glyphs: Glyphs
    ) -> None:
        self._console = console
        self._status = status
        self._label = label
        self._glyphs = glyphs
        self._running = True

    def update(self, label: str) -> None:
        """Swap the text beside the spinner; the elapsed clock keeps running."""
        self._label.label = label

    def _stop(self) -> None:
        if self._running:
            self._status.stop()  # type: ignore[attr-defined]
            self._running = False

    def done(self, *, ok: bool = True, message: str | None = None) -> None:
        self._stop()
        glyph = self._glyphs.ok if ok else self._glyphs.fail
        self._console.print(
            f"{glyph} {message or self._label.label}",
            style="success" if ok else "error",
            markup=False,
        )


def build_default_ui(log_path: Path | None = None) -> UI:
    """The production UI, or raise :class:`NotInteractiveError` (plan §6.1).

    ``log_path`` turns on the session transcript (:class:`RunLog`). The caller owns the
    location because ``ui`` knows nothing about the output tree.
    """
    return RichQuestionaryUI(log_path=log_path)


# --------------------------------------------------------------------------- #
# Test double (offline, no TTY, no rich) — the killswitch-safe UI (plan §3)
# --------------------------------------------------------------------------- #
class _StubProgressHandle:
    """No-op :class:`ProgressHandle`: records advances so a test can assert progress
    fired, but renders nothing (no TTY). ``done``/``fail`` append their name to a shared
    ``events`` list so a test can assert the bar completed on success and was FAILED (not
    completed) when the stage raised (TD-17)."""

    def __init__(self, recorder: list[float], events: list[str]) -> None:
        self._recorder = recorder
        self._events = events

    def advance_to(self, value: float) -> None:
        self._recorder.append(value)

    def done(self) -> None:
        self._events.append("done")

    def fail(self) -> None:
        self._events.append("fail")


class _StubSpinnerHandle:
    """Records the labels it was given so a test can assert the operator was told which
    step is in flight, not just that a spinner existed."""

    def __init__(self, labels: list[str]) -> None:
        self._labels = labels

    def done(self, *, ok: bool = True, message: str | None = None) -> None:
        pass

    def update(self, label: str) -> None:
        self._labels.append(label)


class StubUI:
    """Scripted, offline UI for tests. ``select``/``text``/``confirm`` pop the next queued
    answer (empty queue → ``EOFError``, mirroring a closed stdin → clean exit); every
    message is recorded as ``(level, plain text)`` for assertions. No TTY, no rich, no
    network — the whole menu runs under it in CI."""

    def __init__(self, answers: Sequence[object] | None = None) -> None:
        self.answers: list[object] = list(answers or [])
        self.messages: list[tuple[str, str]] = []
        self.progress_values: list[float] = []
        self.progress_events: list[str] = []  # "done"/"fail" per bar exit (TD-17)
        self.spinner_labels: list[str] = []  # every live label a flow pushed into a spinner
        self._revealed_priority = 0  # mirrors the once-per-launch reveal guard (TD-14)

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

    def clear(self) -> None:
        self.messages.append(("clear", ""))

    def drain_input(self) -> None:
        self.messages.append(("drain", ""))

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

    def pick_files(
        self, prompt: str, *, filetypes: Sequence[tuple[str, str]], initialdir: Path | None = None
    ) -> tuple[str, ...] | None:
        """Pops the next queued answer. A sequence of paths → that tuple; a queued
        ``None`` (or an empty sequence) → soft cancel; empty queue → ``EOFError``.
        A bare string is accepted as a one-file selection so a test does not have to
        wrap the common single-pick case in a list."""
        self.messages.append(("pick_files", prompt))
        answer = self._pop()
        if answer is None:
            return None
        if isinstance(answer, str):
            return (answer,)
        assert isinstance(answer, Sequence)  # a test queued the wrong shape
        return tuple(str(item) for item in answer) or None

    def pick_dir(self, prompt: str, *, initialdir: Path | None = None) -> str | None:
        """Pops the next queued answer, with :meth:`pick_file`'s three states."""
        self.messages.append(("pick_dir", prompt))
        answer = self._pop()
        return None if answer is None else str(answer)

    def reveal_dir(self, path: Path, *, priority: int = REVEAL_AUDIO) -> None:
        """Record the reveal once per instance for the highest priority seen (mirrors the
        production guard), so a test can assert summaries supersede an earlier audio pop
        and that it fires at most once per priority across a launch."""
        if priority <= self._revealed_priority:
            return
        self._revealed_priority = priority
        self.messages.append(("reveal_dir", str(path)))

    def rule(self, title: str) -> None:
        self.messages.append(("rule", title))

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def detail(self, message: str) -> None:
        self.messages.append(("detail", message))

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
        handle = _StubProgressHandle(self.progress_values, self.progress_events)
        # Mirror the production failure-awareness (TD-17) so a menu-level test sees the same
        # done-vs-fail bar outcome the real UI would render.
        try:
            yield handle
        except BaseException:
            handle.fail()
            raise
        else:
            handle.done()

    @contextmanager
    def spinner(self, label: str) -> Iterator[SpinnerHandle]:
        self.messages.append(("spinner", label))
        yield _StubSpinnerHandle(self.spinner_labels)
