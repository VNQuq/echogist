"""T3 — the centralized console theme (v1.1 plan §4). Local, offline, no TTY needed.

One place owns the look of the interactive console: the :data:`RICH_THEME` style
names, the :data:`QUESTIONARY_STYLE` for the arrow-key prompts, and the fancy/ASCII
:class:`Glyphs` tables. :func:`detect_caps` reads a :class:`rich.console.Console`'s
capabilities once and picks the glyph table — fancy Unicode on a modern UTF-8
terminal, ASCII on legacy Windows ``cmd`` (cp437/cp1251) or under ``NO_COLOR``.

**Why a module, not ``models.toml`` (plan §4).** A palette is developer taste, not
operator behavior. Serializing it into the config would add a parse/validate
(:class:`~echogist.config.ConfigError`) surface for values the solo operator never
tunes. The config stays operator behavior; the look stays code.

**Killswitch (CLAUDE.md).** Pure data + one capability read. ``rich``/``questionary``
are offline libraries; this module touches no network and needs no TTY to import,
so it is import-safe in the offline CI run.
"""

from __future__ import annotations

from dataclasses import dataclass

import questionary
from rich import box
from rich.console import Console
from rich.theme import Theme

# Named rich styles used across the UI adapter. Markup like ``[error]…[/]`` resolves
# against these, so a palette change is one edit here (plan §4 "centralized theme").
RICH_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warn": "yellow",
        "error": "bold red",
        "dim": "dim",
        "heading": "bold cyan",
        "banner": "bold cyan",
    }
)

# The arrow-key prompt palette (questionary). Mirrors the rich heading/accent colors
# so the select pointer and the panels read as one console.
QUESTIONARY_STYLE = questionary.Style(
    [
        ("qmark", "fg:#00afaf bold"),
        ("question", "bold"),
        ("pointer", "fg:#00afaf bold"),
        ("highlighted", "fg:#00afaf bold"),
        ("selected", "fg:#5fd7ff"),
        ("answer", "fg:#5fd7ff bold"),
        ("instruction", "fg:#808080"),
    ]
)


@dataclass(frozen=True)
class Glyphs:
    """The status/decoration glyphs, in a fancy and an ASCII variant.

    ``box`` is the :class:`rich.box.Box` panels/tables draw with — Unicode rounded
    corners on a capable terminal, ASCII ``+--+`` on legacy ``cmd``. rich auto-degrades
    color and box-drawing, but it does NOT strip emoji/Unicode placed in our own
    strings, so ``ok``/``fail``/``arrow`` must come from this table, not be hardcoded
    (plan §4 — correctness on the ship target, not decoration).
    """

    ok: str
    fail: str
    arrow: str
    bullet: str
    box: box.Box


_FANCY = Glyphs(ok="✓", fail="✗", arrow="→", bullet="•", box=box.ROUNDED)
_ASCII = Glyphs(ok="OK", fail="X", arrow=">", bullet="-", box=box.ASCII)


def glyphs(fancy: bool) -> Glyphs:
    """The glyph table for the detected capability: fancy Unicode or ASCII."""
    return _FANCY if fancy else _ASCII


def detect_caps(console: Console) -> bool:
    """``True`` if ``console`` can render fancy Unicode glyphs and color.

    Fancy requires all of: color allowed (``NO_COLOR`` unset), not legacy Windows
    ``cmd``, a UTF-8 output encoding, and a real terminal. Anything else (a cp437/cp1251
    codepage, ``NO_COLOR``, the legacy console, a non-TTY) falls back to the ASCII table
    — the glyph table is the only thing that keeps emoji out of a cp437 console where
    rich's own color/box degradation would otherwise leave them as mojibake (plan §4).
    """
    if console.no_color:
        return False
    if getattr(console, "legacy_windows", False):
        return False
    if "utf" not in (console.encoding or "").lower():
        return False
    return bool(console.is_terminal)
