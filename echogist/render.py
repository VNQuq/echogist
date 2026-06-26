"""T7 — render stage (fpdf2 PDF / Markdown, plan §3 / §7).

The last stage. Turns the structured :class:`~echogist.summarize.Summary` into the
kept artifact the operator actually reads: ``output/summaries/<title>.pdf`` (the
default) or ``<title>.md``. It is LOCAL and offline — no network, no key — so it
sits to the left of the killswitch like every stage except summarize.

**Grouping the triplet.** Summarize (T6) already wrote the raw
``output/summaries/raw/<title>.json`` (F13). Render reuses THAT file's stem for the
``.pdf``/``.md`` it writes to ``output/summaries/`` (the ``base`` argument is
normally ``saved_json_path.stem``), so ``.json``/``.pdf``/``.md`` share one base
name (the .json one level down); only the chosen extension is
deduped here. The stem itself is the Windows-safe, length-capped
:func:`naming.summary_stem`.

**Cyrillic, no tofu.** The PDF embeds the **bundled** DejaVuSans (regular + bold)
shipped under ``echogist/assets/fonts/`` — fpdf2's built-in fonts are Latin-1 only
and would render Russian as blank boxes. The font travels with the app, so a
Windows box with no Cyrillic system font still renders correctly offline
(spike-verified design, plan §6).

**F13 re-render.** :func:`load_summary` reconstructs a :class:`Summary` from the
saved ``.json`` so the menu can re-render an earlier summary in either format
WITHOUT a second paid call — a render failure (an fpdf2 edge, a full disk) is
always recoverable from the artifact.

The two seams that could fail at runtime — the lazily-imported ``fpdf`` package
and the bundled font files — each raise a recoverable :class:`RenderError` with a
human message (print it, return to the menu), and the Markdown path needs neither,
so it is the always-available fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import naming
from .summarize import (
    ActionItem,
    Decision,
    PointGroup,
    SectionGroup,
    SectionMarker,
    Summary,
    SynthesisSection,
    _unassigned_label,
)

Logger = Callable[[str], object]

# The bundled Unicode font (Cyrillic-capable), resolved relative to the package so
# it works from a frozen/copied install on Windows, not just the dev checkout.
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FONT_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"
_FONT_FAMILY = "DejaVu"

# Display fallback when a reconstructed Summary has an empty title (load_summary can
# rebuild one from a hand-edited .json). Both renderers use it so PDF and Markdown
# agree on the same input. Distinct from the lowercase "summary" filename fallback.
_FALLBACK_TITLE = "Summary"

# Localized section labels. The summary BODY is RU or EN (the model wrote it); the
# structural headings are ours, so we localize them too — an RU summary under
# English headings reads wrong. Two languages, the spec §7 structure. Unknown code
# falls back to English (settings validation already restricts it to ru/en).
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "overview": "Overview",
        "key_takeaways": "Key takeaways",
        "decisions": "Decisions",
        "action_items": "Action items",
        "estimate": "estimate",
        "sections": "Sections",
        "recurring_themes": "Recurring themes",
        "core_idea": "Core idea",
        "main_themes": "Main themes",
    },
    "ru": {
        "overview": "Обзор",
        "key_takeaways": "Ключевые выводы",
        "decisions": "Принятые решения",
        "action_items": "Пункты к выполнению",
        "estimate": "оценка",
        "sections": "Разделы",
        "recurring_themes": "Повторяющиеся темы",
        "core_idea": "Главная мысль",
        "main_themes": "Основные темы",
    },
}


class RenderError(Exception):
    """A recoverable render failure. Print it, return to the menu.

    The raw summary ``.json`` is saved before render (F13), so every failure here
    is recoverable: re-render from the artifact, or switch to Markdown — never a
    re-pay, never a crash.
    """


def _labels(language: str) -> dict[str, str]:
    return _LABELS.get(language, _LABELS["en"])


# --------------------------------------------------------------------------- #
# F13 — reconstruct a Summary from the saved .json (the re-render path)
# --------------------------------------------------------------------------- #
def load_summary(json_path: Path) -> Summary:
    """Reconstruct a :class:`Summary` from a saved F13 ``.json`` (plan §3).

    The reverse of :func:`echogist.summarize.save_raw_result`, so the menu can
    re-render an earlier summary without a second paid call. Tolerant of a
    hand-edited file — a missing field falls back to empty rather than crashing,
    the same defensive stance as the summarize parser — but a file that is not even
    readable JSON raises a recoverable :class:`RenderError`.
    """
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderError(
            f"Could not read the saved summary {json_path}: {exc}. "
            "Re-summarize from the transcript, or check the file."
        ) from exc
    if not isinstance(raw, dict):
        raise RenderError(f"{json_path}: expected a JSON object (a saved summary).")

    markers = tuple(
        SectionMarker(
            timecode=str(m.get("timecode", "")),
            title=str(m.get("title", "")),
            bullets=_str_tuple(m.get("bullets")),
        )
        for m in raw.get("section_timecodes", [])
        if isinstance(m, dict)
    )
    decisions = tuple(
        Decision(
            decision=str(d.get("decision", "")),
            rationale=str(d.get("rationale", "")),
            anchor=str(d.get("anchor", "")),  # TD-16 v2; absent in pre-v2 .json -> ""
        )
        for d in raw.get("decisions", [])
        if isinstance(d, dict)
    )
    actions = tuple(
        ActionItem(
            task=str(a.get("task", "")),
            owner=str(a.get("owner", "")),
            estimate=str(a.get("estimate", "")),
            anchor=str(a.get("anchor", "")),
        )
        for a in raw.get("action_items", [])
        if isinstance(a, dict)
    )
    return Summary(
        title=str(raw.get("title", "")),
        overview=str(raw.get("overview", "")),
        key_takeaways=_str_tuple(raw.get("key_takeaways")),
        section_timecodes=markers,
        recurring_themes=_str_tuple(raw.get("recurring_themes")),
        core_idea=str(raw.get("core_idea", "")),
        decisions=decisions,
        action_items=actions,
        language=str(raw.get("language", "")),
        takeaway_groups=_point_groups(raw.get("takeaway_groups")),
        theme_groups=_point_groups(raw.get("theme_groups")),
        section_groups=_section_groups(raw.get("section_groups")),
        synthesis=_synthesis_sections(raw.get("synthesis")),
        main_themes=_str_tuple(raw.get("main_themes")),
    )


def _synthesis_sections(value: Any) -> tuple[SynthesisSection, ...]:
    """Reconstruct the TD-16 v2 synthesis sections from saved JSON (defensive).

    Missing/garbage -> empty, so a pre-v2 ``.json`` loads with no synthesis and render
    falls back to the flat/grouped path — the same additive contract as the grouping
    overlay.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        SynthesisSection(
            heading=str(s.get("heading", "")),
            prose=str(s.get("prose", "")),
            anchors=_str_tuple(s.get("anchors")),
        )
        for s in value
        if isinstance(s, dict)
    )


def _point_groups(value: Any) -> tuple[PointGroup, ...]:
    """Reconstruct the TD-15 takeaway/theme grouping overlay from saved JSON (defensive).

    Missing/garbage -> empty, so a pre-grouping ``.json`` (no overlay) loads as the
    flat-list case and render falls back to the flat list — the additive contract.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        PointGroup(heading=str(g.get("heading", "")), points=_str_tuple(g.get("points")))
        for g in value
        if isinstance(g, dict)
    )


def _section_groups(value: Any) -> tuple[SectionGroup, ...]:
    """Reconstruct the TD-15 macro-section grouping overlay from saved JSON (defensive)."""
    if not isinstance(value, list):
        return ()
    out: list[SectionGroup] = []
    for g in value:
        if not isinstance(g, dict):
            continue
        sections = tuple(
            SectionMarker(
                timecode=str(m.get("timecode", "")),
                title=str(m.get("title", "")),
                bullets=_str_tuple(m.get("bullets")),
            )
            for m in g.get("sections", [])
            if isinstance(m, dict)
        )
        out.append(SectionGroup(heading=str(g.get("heading", "")), sections=sections))
    return tuple(out)


def _str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a saved array field back to a tuple of strings (defensive).

    Deliberately looser than :func:`summarize._str_list` (which trims and drops
    empties): the ``.json`` was written from an already-parsed Summary, so its
    arrays are clean — load_summary preserves them verbatim rather than re-filtering
    content the operator might have hand-edited in on purpose.
    """
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


# --------------------------------------------------------------------------- #
# Render dispatch
# --------------------------------------------------------------------------- #
def render(
    summary: Summary,
    out_dir: Path,
    fmt: str,
    *,
    base: str | None = None,
    log: Logger = print,
) -> Path:
    """Write ``summary`` to ``out_dir/<base>.<fmt>`` (deduped); return the path.

    ``fmt`` is ``"pdf"`` (default) or ``"md"`` — the validated
    ``settings.output_format``. ``base`` is normally the stem of the saved F13
    ``.json`` (``saved_json_path.stem``) so the ``.json``/``.pdf``/``.md`` share one
    name; when omitted it is derived from the title. Either way the name runs
    through :func:`naming.summary_stem` (Windows-safe, length-capped) — idempotent
    for an already-safe json stem, but it means a raw ``base`` from any caller can
    never reintroduce a path-traversal/reserved-name hole. Only the chosen
    extension is deduped here. Offline; the PDF path embeds DejaVuSans for Cyrillic.
    """
    fmt = fmt.lower()
    if fmt not in ("pdf", "md"):
        raise RenderError(f"Unknown output format '{fmt}'; expected 'pdf' or 'md'.")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = naming.summary_stem(base if base is not None else summary.title, fallback="summary")
    suffix = ".pdf" if fmt == "pdf" else ".md"
    out_path = naming.dedup_path(out_dir, stem, suffix)
    log(f"Rendering {fmt.upper()} summary -> {out_path.name}")

    if fmt == "md":
        out_path.write_text(_markdown(summary), encoding="utf-8")
    else:
        _render_pdf(summary, out_path)
    return out_path


# --------------------------------------------------------------------------- #
# Shared one-line formatting for the meeting/planning fields (Markdown + PDF agree)
# --------------------------------------------------------------------------- #
def _with_anchor(text: str, anchor: str) -> str:
    """Append a validated ``[HH:MM:SS]`` anchor in parentheses (TD-16 v2), if present.

    Empty on the pre-v2 paths (decisions/actions there carry no anchor), so this is a
    no-op for the flat/grouped render and only annotates the synthesis-path lines.
    """
    return f"{text}  ({anchor})" if anchor else text


def _decision_text(item: Decision) -> str:
    """A decision as one line: the decision, the rationale when present, then its anchor."""
    base = f"{item.decision} — {item.rationale}" if item.rationale else item.decision
    return _with_anchor(base, item.anchor)


def _action_text(item: ActionItem, lab: dict[str, str], *, unassigned: str = "") -> str:
    """An action item as one line: the task, then owner / labelled estimate when present.

    ``unassigned`` is the per-language 'no owner' placeholder (``Не назначено``); an
    owner equal to it — or blank — is dropped per item (TD-15 Phase 1). On a solo
    lecture the placeholder is identical noise on every row; on a mixed list (some real
    owners, many placeholders) the named rows keep their names and only the placeholder
    rows shed it. A real owner is never hidden. The validated anchor (TD-16 v2) trails.
    """
    bits: list[str] = []
    if item.owner and item.owner != unassigned:
        bits.append(item.owner)
    if item.estimate:
        bits.append(f"{lab['estimate']}: {item.estimate}")
    base = f"{item.task} — {', '.join(bits)}" if bits else item.task
    return _with_anchor(base, item.anchor)


# --------------------------------------------------------------------------- #
# Paragraphing — break a long single-blob field into readable paragraphs
# --------------------------------------------------------------------------- #
# Sentences per paragraph when the model emits the overview as one unbroken slab
# (a 5k-char wall on a long lecture). Small, so the rendered overview breathes.
_SENTENCES_PER_PARAGRAPH = 3


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace, keeping the
    punctuation. Heuristic (no abbreviation handling) and language-agnostic — works
    for RU and EN alike; a missed split just yields a longer paragraph, never lost
    text."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _paragraphs(text: str) -> list[str]:
    """Reflow a long single-blob field (the overview) into readable paragraphs.

    Respects blank-line breaks if the text already has them; otherwise groups
    sentences a few at a time. Pure text reflow — no word is added or dropped, so the
    completeness guarantee is untouched (TD-15 Phase 1).
    """
    text = text.strip()
    if not text:
        return []
    explicit = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(explicit) > 1:  # the model/operator already paragraphed it — respect that
        return explicit
    sentences = _split_sentences(text)
    if len(sentences) <= _SENTENCES_PER_PARAGRAPH:
        return [text]
    return [
        " ".join(sentences[i : i + _SENTENCES_PER_PARAGRAPH])
        for i in range(0, len(sentences), _SENTENCES_PER_PARAGRAPH)
    ]


# --------------------------------------------------------------------------- #
# Markdown (pure, no dependency — the always-available fallback)
# --------------------------------------------------------------------------- #
def _md_string_section(
    heading: str, flat: tuple[str, ...], groups: tuple[PointGroup, ...]
) -> list[str]:
    """A flat string list (takeaways/themes) as Markdown — grouped under ``###`` sub-
    headings when the TD-15 overlay is present, else the flat bullet list (fallback)."""
    lines = [f"## {heading}", ""]
    if groups:
        for g in groups:
            lines += [f"### {g.heading}".rstrip(), "", *(f"- {p}" for p in g.points), ""]
    else:
        lines += [*(f"- {t}" for t in flat), ""]
    return lines


def _md_sections(
    heading: str, flat: tuple[SectionMarker, ...], groups: tuple[SectionGroup, ...]
) -> list[str]:
    """Section markers as Markdown — under macro-section ``###`` headings when the
    TD-15 overlay is present, else the flat marker list (fallback). Bullets stay nested."""

    def _markers(markers: tuple[SectionMarker, ...]) -> list[str]:
        lines: list[str] = []
        for m in markers:
            lines.append(f"- `{m.timecode}` {m.title}".rstrip())
            lines += [f"  - {b}" for b in m.bullets]  # indented sub-bullets = section content
        return lines

    lines = [f"## {heading}", ""]
    if groups:
        for g in groups:
            lines += [f"### {g.heading}".rstrip(), "", *_markers(g.sections), ""]
    else:
        lines += [*_markers(flat), ""]
    return lines


def _md_decisions_actions(summary: Summary, lab: dict[str, str]) -> list[str]:
    """The shared decisions/action-items Markdown block (flat and synthesis paths)."""
    out: list[str] = []
    if summary.decisions:
        out += [f"## {lab['decisions']}", ""]
        out += [f"- {_decision_text(d)}" for d in summary.decisions]
        out += [""]
    if summary.action_items:
        unassigned = _unassigned_label(summary.language)
        out += [f"## {lab['action_items']}", ""]
        out += [f"- {_action_text(a, lab, unassigned=unassigned)}" for a in summary.action_items]
        out += [""]
    return out


def _markdown_synthesis(summary: Summary) -> str:
    """The TD-16 v2 readable document: core idea -> phases (prose + anchors) -> themes.

    Used when ``summary.synthesis`` is present; the structured flat/grouped fields are
    empty on this path (retired in v2), so the document is the synthesized prose plus
    the document header and the de-noised decisions/actions.
    """
    lab = _labels(summary.language)
    out: list[str] = [f"# {summary.title or _FALLBACK_TITLE}".rstrip(), ""]
    if summary.core_idea:
        out += [f"## {lab['core_idea']}", "", summary.core_idea, ""]
    for s in summary.synthesis:
        # Collapse any whitespace/newlines in the heading: a heading is one line, and a
        # garbled/hand-edited transcript must not inject extra Markdown structure via a
        # newline + `#` in the model-supplied heading (the summarize side strips, but
        # load_summary is verbatim by design).
        heading = " ".join(s.heading.split())
        out += [f"## {heading}".rstrip(), ""]
        for para in _paragraphs(s.prose):
            out += [para, ""]
        if s.anchors:
            out += [f"*{' · '.join(s.anchors)}*", ""]  # validated timecodes for this phase
    if summary.main_themes:
        out += [f"## {lab['main_themes']}", "", *(f"- {t}" for t in summary.main_themes), ""]
    out += _md_decisions_actions(summary, lab)
    return "\n".join(out).rstrip() + "\n"


def _markdown(summary: Summary) -> str:
    """Render the summary as GitHub-flavored Markdown (UTF-8, Cyrillic literal)."""
    if summary.synthesis:  # TD-16 v2 path; pre-v2 summaries fall through to flat/grouped
        return _markdown_synthesis(summary)
    lab = _labels(summary.language)
    out: list[str] = [f"# {summary.title or _FALLBACK_TITLE}".rstrip(), ""]
    if summary.overview:
        out += [f"## {lab['overview']}", ""]
        for para in _paragraphs(summary.overview):
            out += [para, ""]  # blank line between paragraphs => separate <p> in MD
    if summary.key_takeaways:
        out += _md_string_section(
            lab["key_takeaways"], summary.key_takeaways, summary.takeaway_groups
        )
    out += _md_decisions_actions(summary, lab)
    if summary.section_timecodes:
        out += _md_sections(lab["sections"], summary.section_timecodes, summary.section_groups)
    if summary.recurring_themes:
        out += _md_string_section(
            lab["recurring_themes"], summary.recurring_themes, summary.theme_groups
        )
    if summary.core_idea:
        out += [f"## {lab['core_idea']}", "", summary.core_idea, ""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# PDF (fpdf2 + bundled DejaVuSans; lazily imported, like the other native seams)
# --------------------------------------------------------------------------- #
def _font_file(path: Path) -> str:
    """Return ``path`` if the bundled font is present, else a recoverable error."""
    if not path.is_file():
        raise RenderError(
            f"The bundled PDF font is missing ({path.name}); reinstall EchoGist. "
            "Markdown output (Settings) renders without it."
        )
    return str(path)


def _render_pdf(summary: Summary, out_path: Path) -> None:
    """Render the summary to a PDF at ``out_path`` with the embedded Unicode font.

    ``fpdf`` is imported lazily (the module stays import-clean if the wheel is
    absent, matching the extract/summarize seams). Both font weights are embedded
    so Cyrillic renders as real glyphs; auto page-break flows a long summary across
    pages. Any fpdf2/IO failure becomes a recoverable :class:`RenderError`.
    """
    try:
        from fpdf import FPDF
        from fpdf.errors import FPDFException
    except ImportError as exc:  # installed by run.bat from requirements.lock
        raise RenderError(
            "fpdf2 is not installed — run.bat installs it from requirements.lock. "
            "Switch output to Markdown in Settings to render without it."
        ) from exc

    lab = _labels(summary.language)
    try:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_margins(left=18, top=18, right=18)
        pdf.add_page()
        # add_font with an explicit file per weight — fpdf2 does NOT synthesize bold
        # for a TTF, so the bold face needs its own embedded file.
        pdf.add_font(_FONT_FAMILY, "", _font_file(_FONT_REGULAR))
        pdf.add_font(_FONT_FAMILY, "B", _font_file(_FONT_BOLD))

        _title(pdf, summary.title)
        if summary.synthesis:  # TD-16 v2 path; pre-v2 summaries render the flat/grouped body
            _pdf_synthesis_body(pdf, summary, lab)
            pdf.output(str(out_path))
            return
        if summary.overview:
            _heading(pdf, lab["overview"])
            _paragraphed_body(pdf, summary.overview)
        if summary.key_takeaways:
            _pdf_string_section(
                pdf, lab["key_takeaways"], summary.key_takeaways, summary.takeaway_groups
            )
        if summary.decisions:
            _heading(pdf, lab["decisions"])
            for decision in summary.decisions:
                _bullet(pdf, _decision_text(decision))
        if summary.action_items:
            unassigned = _unassigned_label(summary.language)
            _heading(pdf, lab["action_items"])
            for action in summary.action_items:
                _bullet(pdf, _action_text(action, lab, unassigned=unassigned))
        if summary.section_timecodes:
            _pdf_sections(pdf, lab["sections"], summary.section_timecodes, summary.section_groups)
        if summary.recurring_themes:
            _pdf_string_section(
                pdf, lab["recurring_themes"], summary.recurring_themes, summary.theme_groups
            )
        if summary.core_idea:
            _heading(pdf, lab["core_idea"])
            _body(pdf, summary.core_idea)

        pdf.output(str(out_path))
    except RenderError:
        raise
    except (OSError, RuntimeError, ValueError, FPDFException) as exc:  # fpdf2 layout/IO edge cases
        # FPDFException subclasses Exception directly (not OSError/ValueError), so it
        # MUST be named explicitly — fpdf2 raises it for unrenderable layouts (e.g. an
        # unbreakable token wider than the line). Without it, a pathological summary
        # would crash past the menu, violating "fail loud, return to menu — no crash".
        out_path.unlink(missing_ok=True)  # never leave a half-written PDF behind
        raise RenderError(
            f"Could not write the PDF ({exc}). Your summary is saved as .json — "
            "re-render, or switch output to Markdown in Settings."
        ) from exc


# Every line is a full-width multi_cell that returns the cursor to the left margin
# on the next line (new_x/new_y) — fpdf2 otherwise parks x at the right margin, so a
# following multi_cell(w=0) would see ~zero width and raise "not enough horizontal
# space". Strings are coerced to XPos/YPos by fpdf2, so the enum import stays lazy.
def _line(pdf: Any, height: float, text: str) -> None:
    pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT")


def _title(pdf: Any, text: str) -> None:
    pdf.set_font(_FONT_FAMILY, "B", 18)
    _line(pdf, 9, text or _FALLBACK_TITLE)
    pdf.ln(3)


def _heading(pdf: Any, text: str) -> None:
    pdf.ln(2)
    pdf.set_font(_FONT_FAMILY, "B", 13)
    _line(pdf, 7, text)
    pdf.ln(1)


def _body(pdf: Any, text: str) -> None:
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6, text)


def _paragraphed_body(pdf: Any, text: str) -> None:
    """Body text broken into paragraphs with a small gap between them (the overview)."""
    for i, para in enumerate(_paragraphs(text)):
        if i:
            pdf.ln(2)
        _body(pdf, para)


def _bullet(pdf: Any, text: str) -> None:
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6, f"•  {text}")  # DejaVuSans carries U+2022, so no tofu bullet


def _subbullet(pdf: Any, text: str) -> None:
    """An indented second-level bullet (section content under a section marker)."""
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6, f"      ◦  {text}")  # leading spaces indent; U+25E6 in DejaVuSans


def _subheading(pdf: Any, text: str) -> None:
    """A group heading inside a section (the TD-15 grouping overlay) — smaller than a
    section heading, bold, so the hierarchy reads ## section / ### group / • point."""
    pdf.ln(1)
    pdf.set_font(_FONT_FAMILY, "B", 11)
    _line(pdf, 6, text)


def _pdf_string_section(
    pdf: Any, heading: str, flat: tuple[str, ...], groups: tuple[PointGroup, ...]
) -> None:
    """A flat string list (takeaways/themes) in the PDF — grouped under sub-headings
    when the TD-15 overlay is present, else the flat bullet list (fallback)."""
    _heading(pdf, heading)
    if groups:
        for g in groups:
            _subheading(pdf, g.heading)
            for point in g.points:
                _bullet(pdf, point)
    else:
        for item in flat:
            _bullet(pdf, item)


def _pdf_sections(
    pdf: Any, heading: str, flat: tuple[SectionMarker, ...], groups: tuple[SectionGroup, ...]
) -> None:
    """Section markers in the PDF — under macro-section sub-headings when the TD-15
    overlay is present, else the flat marker list (fallback). Bullets stay indented."""

    def emit(markers: tuple[SectionMarker, ...]) -> None:
        for m in markers:
            _bullet(pdf, f"{m.timecode}  {m.title}".rstrip())
            for point in m.bullets:  # section content as indented sub-bullets
                _subbullet(pdf, point)

    _heading(pdf, heading)
    if groups:
        for g in groups:
            _subheading(pdf, g.heading)
            emit(g.sections)
    else:
        emit(flat)


def _anchor_line(pdf: Any, text: str) -> None:
    """A small, quiet line of validated timecodes under a phase's prose (TD-16 v2)."""
    pdf.set_font(_FONT_FAMILY, "", 9)
    _line(pdf, 5, text)


def _pdf_synthesis_body(pdf: Any, summary: Summary, lab: dict[str, str]) -> None:
    """The TD-16 v2 PDF body: core idea -> phases (prose + anchors) -> themes -> decisions.

    Mirrors :func:`_markdown_synthesis`; the flat/grouped fields are empty on this path,
    so the document is the synthesized prose plus the header and de-noised actions.
    """
    if summary.core_idea:
        _heading(pdf, lab["core_idea"])
        _body(pdf, summary.core_idea)
    for s in summary.synthesis:
        _heading(pdf, s.heading)
        _paragraphed_body(pdf, s.prose)
        if s.anchors:
            _anchor_line(pdf, " · ".join(s.anchors))  # U+00B7 is in DejaVuSans
    if summary.main_themes:
        _heading(pdf, lab["main_themes"])
        for theme in summary.main_themes:
            _bullet(pdf, theme)
    if summary.decisions:
        _heading(pdf, lab["decisions"])
        for decision in summary.decisions:
            _bullet(pdf, _decision_text(decision))
    if summary.action_items:
        unassigned = _unassigned_label(summary.language)
        _heading(pdf, lab["action_items"])
        for action in summary.action_items:
            _bullet(pdf, _action_text(action, lab, unassigned=unassigned))
