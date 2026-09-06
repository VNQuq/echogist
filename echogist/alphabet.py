"""Script check — did the model keep writing in the language it was asked for?

Local, offline, pure. The deterministic sibling of
:func:`echogist.summarize.validate_anchors`: that one asks "does every timecode point at
real speech", this one asks "is every letter from a writing system this language uses".
Both run over the model's finished reply, both report and never block.

**Why this exists.** The first real folder run (7 lectures, ``economy``/Haiku, 2026-09-04)
spliced Chinese morphemes into the middle of Russian words — ``как催化剂для перехода``
(催化剂 = catalyst), ``и技ической полноты`` (技 = tech-), ``Преподаватель描написывает``
(描 = to describe). Nothing was fabricated: the meaning survived and only the spelling
broke, three words across seven lectures.

The severity is low. The category is not. We learned about it ONLY because the PDF font
could not draw those glyphs and fpdf2 warned; had the model swapped a Russian word for a
different Russian word, the render would have been clean and nothing would have said a
thing. So a foreign script is not the bug — it is the one class of model drift that
DECLARES ITSELF, and it is free signal about how far a cheap tier wanders. Removing the
symptom (a prompt sentence, which is also shipped) without keeping the instrument would
trade a visible defect for an invisible one.

**How a script is decided.** From the first word of the character's own
:func:`unicodedata.name` — ``CYRILLIC SMALL LETTER A`` -> ``cyrillic``,
``CJK UNIFIED IDEOGRAPH-50AC`` -> ``cjk``. No range tables to maintain and no dependency:
the Unicode database ships with Python and already knows what every codepoint is. A
character with no name at all is reported rather than assumed innocent.

**Killswitch (CLAUDE.md).** ``unicodedata`` and nothing else; this never reaches the wire.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

#: How much text either side of a run is quoted back. Enough to recognize the sentence,
#: short enough that a finding stays one console line.
_CONTEXT_CHARS = 30


@dataclass(frozen=True)
class Finding:
    """One run of characters written in a script this language does not use."""

    #: The offending script, as :func:`script_of` names it (``cjk``, ``greek``, ...).
    script: str
    #: The offending characters themselves, consecutive, as emitted.
    run: str
    #: The run plus surrounding text, whitespace collapsed — what the operator reads.
    context: str


# ``unicodedata.name()`` opens with a SCRIPT for a letter of a writing system, but the
# same first-token rule reads a symbol as one: ``µ`` is MICRO SIGN, ``ℓ`` is SCRIPT SMALL
# L, ``𝑥`` is MATHEMATICAL ITALIC SMALL X, ``ª`` is FEMININE ORDINAL INDICATOR — all
# ``isalpha()``, none of them a change of writing system. They are notation, shared by
# every language exactly like the digits and the em dash this function already excludes,
# and reporting them as "the model slipped out of the target language" is how an
# instrument earns the right to be ignored.
_NOT_A_SCRIPT = frozenset(
    {
        "micro",
        "script",
        "mathematical",
        "feminine",
        "masculine",
        "ohm",
        "angstrom",
        "kelvin",
        "planck",
        "estimated",
        "information",
        "numero",
        "turned",
        "modifier",
        "double-struck",
        "black-letter",
        "unnamed",
    }
)


@lru_cache(maxsize=4096)
def script_of(char: str) -> str | None:
    """The writing system ``char`` belongs to, or ``None`` if it belongs to none.

    ``None`` means "not a letter" — digits, punctuation, whitespace, the ``[HH:MM:SS]``
    brackets, the em dash. Those are shared by every language and are never a finding:
    the rule is about writing systems, not about vocabulary or typography.

    Cached because a summary is tens of thousands of characters drawn from a few dozen
    distinct ones, so this costs one lookup per DISTINCT character rather than per
    character. The cache is keyed by the character itself and the result is a pure
    function of Unicode, so it is safe for the process's whole life.
    """
    if not char.isalpha():
        return None
    try:
        name = unicodedata.name(char)
    except ValueError:  # a letter with no assigned name — report it, do not excuse it
        return "unnamed"
    first = name.split(" ", 1)[0].lower()
    return None if first in _NOT_A_SCRIPT else first


def foreign_findings(text: str, allowed: frozenset[str]) -> tuple[Finding, ...]:
    """Every run of letters in ``text`` whose script is not in ``allowed``.

    ``allowed`` is a set of :func:`script_of` names. An EMPTY set means "allow anything"
    and returns nothing — the fail-soft path for a language nobody has calibrated, which
    must stay silent rather than flag every character of a summary it knows nothing about.

    Consecutive offending characters collapse into ONE finding: ``催化剂`` is one report of
    a three-character run, not three reports of one character. Runs are split by script,
    so a stretch mixing two foreign scripts is reported as two findings and each names the
    script it actually is.

    Returns everything it finds, in order, unbounded — a caller printing to a console is
    the one that decides how many to show, because only the caller knows what a screen
    holds.
    """
    if not allowed:
        return ()
    findings: list[Finding] = []
    start: int | None = None
    script = ""
    for index, char in enumerate(text):
        found = script_of(char)
        if found is not None and found not in allowed:
            if start is None:
                start, script = index, found
            elif found != script:  # one foreign script runs straight into another
                findings.append(_finding(text, start, index, script))
                start, script = index, found
            continue
        if start is not None:
            findings.append(_finding(text, start, index, script))
            start = None
    if start is not None:
        findings.append(_finding(text, start, len(text), script))
    return tuple(findings)


def script_shares(text: str) -> dict[str, float]:
    """The fraction of ``text``'s LETTERS written in each script, biggest first (TD-30).

    The other half of the instrument. :func:`foreign_findings` answers "is there a
    character from a writing system this language does not use", which cannot see the
    failure where every character is individually legal: a summary asked for in English
    that came back wholesale in Russian. Both scripts are allowed there — a faithful
    quotation of the author's own words is the reason — so only the PROPORTION separates
    a quote from a language switch.

    Counts letters only, by :func:`script_of`, so digits, punctuation, the notation
    excluded there and every space are outside the denominator. Empty text (or text with
    no letters at all) returns an empty mapping rather than a zero share for anything —
    there is nothing to be a share OF, and a caller must not read that as "0% foreign".
    """
    counts: dict[str, int] = {}
    for char in text:
        script = script_of(char)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    total = sum(counts.values())
    if not total:
        return {}
    return {
        script: count / total
        for script, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    }


def _finding(text: str, start: int, end: int, script: str) -> Finding:
    """Build the report for ``text[start:end]``, quoted with its surroundings."""
    left = max(0, start - _CONTEXT_CHARS)
    right = min(len(text), end + _CONTEXT_CHARS)
    # Whitespace collapsed: the prose carries newlines, and a finding has to survive as a
    # single console line to be read at all.
    context = " ".join(text[left:right].split())
    return Finding(script=script, run=text[start:end], context=context)
