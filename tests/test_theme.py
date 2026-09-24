"""Theme-module tests (T3).

Pure, offline, no TTY: the capability detection is driven by constructing rich
Consoles with the relevant signals (encoding, legacy_windows, no_color, terminal)
and asserting the fancy-vs-ASCII glyph-table selection. Mirrors the §7 coverage:
utf-8 TTY → fancy; cp437/non-utf8 → ascii; NO_COLOR → ascii.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from echogist import theme


class _FakeFile:
    """A minimal output file with a fixed ``encoding`` rich reads off it."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding

    def write(self, _text: str) -> int:  # pragma: no cover - never written to
        return 0

    def flush(self) -> None:  # pragma: no cover
        pass

    def isatty(self) -> bool:
        return True


def _console(**kwargs: Any) -> Console:
    return Console(**kwargs)


# --------------------------------------------------------------------------- #
# detect_caps
# --------------------------------------------------------------------------- #
def test_utf8_terminal_is_fancy() -> None:
    # Forced terminal + default UTF-8 encoding + color on → fancy glyphs.
    console = _console(
        file=_FakeFile("utf-8"), force_terminal=True, no_color=False, legacy_windows=False
    )
    assert theme.detect_caps(console) is True


def test_no_color_falls_back_to_ascii() -> None:
    console = _console(force_terminal=True, no_color=True)
    assert theme.detect_caps(console) is False


def test_legacy_windows_falls_back_to_ascii() -> None:
    console = _console(force_terminal=True, legacy_windows=True)
    assert theme.detect_caps(console) is False


def test_non_utf8_encoding_falls_back_to_ascii() -> None:
    # A cp437 codepage (legacy Windows cmd) → ASCII, even with a terminal + color.
    console = _console(file=_FakeFile("cp437"), force_terminal=True)
    assert theme.detect_caps(console) is False


def test_non_terminal_falls_back_to_ascii() -> None:
    # A captured/piped console (no TTY) is never fancy.
    console = _console(force_terminal=False)
    assert theme.detect_caps(console) is False


# --------------------------------------------------------------------------- #
# glyph table selection
# --------------------------------------------------------------------------- #
def test_glyphs_fancy_vs_ascii() -> None:
    fancy = theme.glyphs(True)
    ascii_ = theme.glyphs(False)
    assert fancy.ok == "✓" and fancy.fail == "✗" and fancy.arrow == "→"
    assert ascii_.ok == "OK" and ascii_.fail == "X" and ascii_.arrow == ">"
    # The box style differs too (Unicode rounded vs ASCII).
    assert fancy.box is not ascii_.box


def test_theme_objects_present() -> None:
    # The single rich.Theme and questionary.Style exist and carry the named styles.
    assert "error" in theme.RICH_THEME.styles
    assert "success" in theme.RICH_THEME.styles
    assert theme.QUESTIONARY_STYLE is not None


def test_the_section_rule_does_not_share_a_colour_with_ordinary_output() -> None:
    """TD-32: `rule` wore the bare `cyan` that `info` already owned, so a file header was
    drawn in the same colour and weight as the lines it separates."""
    styles = theme.RICH_THEME.styles
    assert str(styles["rule"].color) != str(styles["info"].color)
    assert str(styles["rule"].color) != str(styles["heading"].color)


def test_the_rule_title_keeps_the_line_colour_and_adds_weight() -> None:
    """Legibility comes from bold, not from climbing back into the conversational range —
    a brighter title would undo the separation the darker line just bought."""
    styles = theme.RICH_THEME.styles
    assert str(styles["rule.title"].color) == str(styles["rule"].color)
    assert styles["rule.title"].bold
    assert not styles["rule"].bold
