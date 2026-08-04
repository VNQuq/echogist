"""T7 — render stage (fpdf2 PDF / Markdown, plan §3 / §7).

The last stage. Turns the structured :class:`~echogist.summarize.Summary` into the
kept artifact the operator actually reads: ``output/summaries/<title>.pdf`` (the
default) or ``<title>.md``. It is LOCAL and offline — no network, no key — so it
sits to the left of the killswitch like every stage except summarize.

**The v2 document (TD-16).** The summary is the ordered ``synthesis`` phases (heading +
faithful prose + validated anchors) plus ``main_themes`` and the decisions/actions — one
readable document, no flat/grouped fallback (map-reduce retired). It OPENS with the
essence block (``core_idea`` / ``main_skill`` / ``test_questions``, ~1-2 pages) and CLOSES
with the questions' reference answers: the two are deliberately at opposite ends so
reading a self-check question does not hand the reader its answer.

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
from .summarize import ActionItem, CheckQuestion, Decision, Summary, SynthesisSection

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

# The per-language 'no owner' placeholder (a render concern only — used to drop a
# noise owner from an action-item line). Unknown code -> English, fail-soft.
_UNASSIGNED_LABELS = {"ru": "Не назначено", "en": "Unassigned"}

# Localized section labels. The summary BODY is RU or EN (the model wrote it); the
# structural headings are ours, so we localize them too — an RU summary under
# English headings reads wrong. Two languages, the spec §7 structure. Unknown code
# falls back to English (settings validation already restricts it to ru/en).
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "decisions": "Decisions",
        "action_items": "Action items",
        "estimate": "estimate",
        "essence": "Essence",
        "core_idea": "Core idea",
        "main_skill": "Key skill",
        "test_questions": "Self-check questions",
        "answers": "Reference answers",
        "main_themes": "Main themes",
    },
    "ru": {
        "decisions": "Принятые решения",
        "action_items": "Пункты к выполнению",
        "estimate": "оценка",
        "essence": "Суть",
        "core_idea": "Главная мысль",
        "main_skill": "Главный навык",
        "test_questions": "Проверочные вопросы",
        "answers": "Ориентиры для ответов",
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


def _unassigned_label(code: str) -> str:
    """The fixed 'unassigned' owner label for ``code`` (ru -> Не назначено)."""
    return _UNASSIGNED_LABELS.get(code, "Unassigned")


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

    decisions = tuple(
        Decision(
            decision=str(d.get("decision", "")),
            rationale=str(d.get("rationale", "")),
            anchor=str(d.get("anchor", "")),
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
        core_idea=str(raw.get("core_idea", "")),
        decisions=decisions,
        action_items=actions,
        language=str(raw.get("language", "")),
        synthesis=_synthesis_sections(raw.get("synthesis")),
        main_themes=_str_tuple(raw.get("main_themes")),
        # Essence block: absent in a .json saved before it existed -> empty, so an older
        # artifact still re-renders (just without the block), never a KeyError.
        main_skill=str(raw.get("main_skill", "")),
        test_questions=_test_questions(raw.get("test_questions")),
    )


def _test_questions(value: Any) -> tuple[CheckQuestion, ...]:
    """Reconstruct the essence block's self-check questions from saved JSON (defensive).

    Missing/garbage -> empty, so a pre-block or hand-edited ``.json`` loads with no
    questions rather than crashing. Entries with no question text are skipped: the block
    and the answers section number off the SAME list, so a phantom entry would renumber
    the answers out of step with the questions.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        CheckQuestion(question=str(q.get("question", "")), answer=str(q.get("answer", "")))
        for q in value
        if isinstance(q, dict) and str(q.get("question", "")).strip()
    )


def _synthesis_sections(value: Any) -> tuple[SynthesisSection, ...]:
    """Reconstruct the TD-16 v2 synthesis sections from saved JSON (defensive).

    Missing/garbage -> empty, so a hand-edited or partial ``.json`` loads with no
    synthesis rather than crashing.
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
    """Append a validated ``[HH:MM:SS]`` anchor in parentheses (TD-16 v2), if present."""
    return f"{text}  ({anchor})" if anchor else text


def _decision_text(item: Decision) -> str:
    """A decision as one line: the decision, the rationale when present, then its anchor."""
    base = f"{item.decision} — {item.rationale}" if item.rationale else item.decision
    return _with_anchor(base, item.anchor)


def _action_text(item: ActionItem, lab: dict[str, str], *, unassigned: str = "") -> str:
    """An action item as one line: the task, then owner / labelled estimate when present.

    ``unassigned`` is the per-language 'no owner' placeholder (``Не назначено``); an
    owner equal to it — or blank — is dropped per item. A real owner is never hidden.
    The validated anchor (TD-16 v2) trails.
    """
    bits: list[str] = []
    if item.owner and item.owner != unassigned:
        bits.append(item.owner)
    if item.estimate:
        bits.append(f"{lab['estimate']}: {item.estimate}")
    base = f"{item.task} — {', '.join(bits)}" if bits else item.task
    return _with_anchor(base, item.anchor)


# --------------------------------------------------------------------------- #
# The essence block (core idea / key skill / self-check questions) — Markdown + PDF agree
# --------------------------------------------------------------------------- #
def _has_essence(summary: Summary) -> bool:
    """True if there is anything to put in the opening essence block.

    All three points are optional: material that teaches no skill leaves ``main_skill``
    empty, and a summary loaded from a pre-block ``.json`` has none of them. An empty
    block is skipped entirely rather than rendered as a bare heading.
    """
    return bool(summary.core_idea or summary.main_skill or summary.test_questions)


def _answered(summary: Summary) -> list[tuple[int, CheckQuestion]]:
    """The essence questions that have a reference answer, each with its DISPLAY number.

    The number is the question's position in the full block list, not in this filtered
    one, so the answers at the end of the document line up with the questions at the top
    even when one of them came back without an answer.
    """
    return [(i, q) for i, q in enumerate(summary.test_questions, 1) if q.answer.strip()]


def _one_line(text: str) -> str:
    """Collapse whitespace/newlines to a single line (a question, a heading, a title).

    ``load_summary`` is verbatim by design, so a newline in a model-written or
    hand-edited question would break out of its Markdown list item and inject structure.
    """
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# Paragraphing — break a long single-blob field into readable paragraphs
# --------------------------------------------------------------------------- #
# Sentences per paragraph when the model emits a phase's prose as one unbroken slab.
# Small, so the rendered prose breathes.
_SENTENCES_PER_PARAGRAPH = 3


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace, keeping the
    punctuation. Heuristic (no abbreviation handling) and language-agnostic — works
    for RU and EN alike; a missed split just yields a longer paragraph, never lost
    text."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _paragraphs(text: str) -> list[str]:
    """Reflow a long single-blob field (a phase's prose) into readable paragraphs.

    Respects blank-line breaks if the text already has them; otherwise groups
    sentences a few at a time. Pure text reflow — no word is added or dropped.
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
def _md_decisions_actions(summary: Summary, lab: dict[str, str]) -> list[str]:
    """The shared decisions/action-items Markdown block."""
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


def _md_essence(summary: Summary, lab: dict[str, str]) -> list[str]:
    """The opening essence block: core idea, key skill, the 3 self-check questions.

    The answers deliberately do NOT appear here — they render at the very end of the
    document (:func:`_md_answers`), so reading a question does not hand you its answer.
    """
    if not _has_essence(summary):
        return []
    out: list[str] = [f"## {lab['essence']}", ""]
    if summary.core_idea:
        out += [f"### {lab['core_idea']}", ""]
        out += [line for para in _paragraphs(summary.core_idea) for line in (para, "")]
    if summary.main_skill:
        out += [f"### {lab['main_skill']}", ""]
        out += [line for para in _paragraphs(summary.main_skill) for line in (para, "")]
    if summary.test_questions:
        out += [f"### {lab['test_questions']}", ""]
        out += [f"{i}. {_one_line(q.question)}" for i, q in enumerate(summary.test_questions, 1)]
        out += [""]
    return out


def _md_answers(summary: Summary, lab: dict[str, str]) -> list[str]:
    """The reference answers, last in the document — numbered to match the questions."""
    answered = _answered(summary)
    if not answered:
        return []
    out: list[str] = [f"## {lab['answers']}", ""]
    for i, q in answered:
        # The question is repeated (bold, not a heading — it stays out of the outline) so
        # each answer stands on its own without paging back to the block.
        out += [f"**{i}. {_one_line(q.question)}**", ""]
        out += [line for para in _paragraphs(q.answer) for line in (para, "")]
    return out


def _markdown(summary: Summary) -> str:
    """The TD-16 v2 readable document as Markdown: essence -> phases -> themes -> answers.

    The document is the opening essence block (core idea / key skill / self-check
    questions), the synthesized prose (heading + paragraphs + validated anchors), the
    de-noised decisions/actions, and last the reference answers. UTF-8, Cyrillic literal.
    """
    lab = _labels(summary.language)
    # Collapse whitespace/newlines in the title for the same reason as the section
    # headings below: load_summary is verbatim, so a hand-edited/model title with a
    # newline + `#` must not inject extra Markdown structure on the one `#` title line.
    title = _one_line(summary.title or _FALLBACK_TITLE)
    out: list[str] = [f"# {title}".rstrip(), ""]
    out += _md_essence(summary, lab)
    for s in summary.synthesis:
        # Collapse any whitespace/newlines in the heading: a heading is one line, and a
        # garbled/hand-edited transcript must not inject extra Markdown structure via a
        # newline + `#` in the model-supplied heading (the summarize side strips, but
        # load_summary is verbatim by design).
        heading = _one_line(s.heading)
        # K=1: the sole phase heading may BE the document title (reconcile returned none),
        # so don't print it twice. Also skip an empty heading rather than emit a bare "## ".
        if heading and heading != title:
            out += [f"## {heading}".rstrip(), ""]
        for para in _paragraphs(s.prose):
            out += [para, ""]
        # TD-19: no per-phase anchor footer. The validated timecodes are woven INLINE in the
        # prose (the citation a reader actually jumps from); the full ``anchors`` array stays
        # in the saved .json for the validation gate, not dumped as a redundant wall here.
    if summary.main_themes:
        out += [f"## {lab['main_themes']}", "", *(f"- {t}" for t in summary.main_themes), ""]
    out += _md_decisions_actions(summary, lab)
    out += _md_answers(summary, lab)  # last in the document, by design
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
        _pdf_synthesis_body(pdf, summary, lab)
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


# Typographic palette (TD-20). DejaVuSans (regular + bold) is the only embedded family,
# so hierarchy comes from SIZE, WEIGHT, COLOR and SPACING rhythm, not extra faces. Near-
# black ink (not pure #000) is easier on the eye for body text; the strong ink is for the
# title/headings; the muted tone is the hairline rule under the title.
_INK = (40, 40, 40)  # body text
_INK_STRONG = (15, 15, 15)  # title + section headings
_RULE = (200, 200, 200)  # hairline divider


# Every line is a full-width multi_cell that returns the cursor to the left margin
# on the next line (new_x/new_y) — fpdf2 otherwise parks x at the right margin, so a
# following multi_cell(w=0) would see ~zero width and raise "not enough horizontal
# space". Strings are coerced to XPos/YPos by fpdf2, so the enum import stays lazy.
def _line(pdf: Any, height: float, text: str) -> None:
    pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT")


def _rule(pdf: Any) -> None:
    """A hairline rule across the text column — a quiet divider under the title (TD-20)."""
    y = pdf.get_y() + 1.5
    pdf.set_draw_color(*_RULE)
    pdf.set_line_width(0.2)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_y(y)


def _title(pdf: Any, text: str) -> None:
    pdf.set_text_color(*_INK_STRONG)
    pdf.set_font(_FONT_FAMILY, "B", 20)
    _line(pdf, 9, text or _FALLBACK_TITLE)
    _rule(pdf)
    pdf.ln(5)


def _heading(pdf: Any, text: str) -> None:
    pdf.ln(4)
    pdf.set_text_color(*_INK_STRONG)
    pdf.set_font(_FONT_FAMILY, "B", 14)
    _line(pdf, 7, text)
    pdf.ln(1.5)


def _subheading(pdf: Any, text: str) -> None:
    """A second-level heading — the essence block's three points inside its one section."""
    pdf.ln(2.5)
    pdf.set_text_color(*_INK_STRONG)
    pdf.set_font(_FONT_FAMILY, "B", 12)
    _line(pdf, 6.5, text)
    pdf.ln(1)


def _body(pdf: Any, text: str) -> None:
    pdf.set_text_color(*_INK)
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6.5, text)  # generous leading so dense prose breathes


def _paragraphed_body(pdf: Any, text: str) -> None:
    """Body text broken into paragraphs with a small gap between them (a phase's prose)."""
    for i, para in enumerate(_paragraphs(text)):
        if i:
            pdf.ln(2.5)
        _body(pdf, para)


def _bullet(pdf: Any, text: str) -> None:
    pdf.set_text_color(*_INK)
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6.5, f"•  {text}")  # DejaVuSans carries U+2022, so no tofu bullet


def _numbered(pdf: Any, number: int, text: str) -> None:
    pdf.set_text_color(*_INK)
    pdf.set_font(_FONT_FAMILY, "", 11)
    _line(pdf, 6.5, f"{number}.  {text}")


def _pdf_essence(pdf: Any, summary: Summary, lab: dict[str, str]) -> None:
    """The opening essence block. The answers are NOT here — see :func:`_pdf_answers`."""
    if not _has_essence(summary):
        return
    _heading(pdf, lab["essence"])
    if summary.core_idea:
        _subheading(pdf, lab["core_idea"])
        _paragraphed_body(pdf, summary.core_idea)
    if summary.main_skill:
        _subheading(pdf, lab["main_skill"])
        _paragraphed_body(pdf, summary.main_skill)
    if summary.test_questions:
        _subheading(pdf, lab["test_questions"])
        for i, q in enumerate(summary.test_questions, 1):
            _numbered(pdf, i, _one_line(q.question))


def _pdf_answers(pdf: Any, summary: Summary, lab: dict[str, str]) -> None:
    """The reference answers, last on the page — numbered to match the questions."""
    answered = _answered(summary)
    if not answered:
        return
    _heading(pdf, lab["answers"])
    for i, q in answered:
        _subheading(pdf, f"{i}. {_one_line(q.question)}")
        _paragraphed_body(pdf, q.answer)


def _pdf_synthesis_body(pdf: Any, summary: Summary, lab: dict[str, str]) -> None:
    """The TD-16 v2 PDF body: essence -> phases (prose + anchors) -> themes -> answers."""
    _pdf_essence(pdf, summary, lab)
    title = _one_line(summary.title or _FALLBACK_TITLE)
    for s in summary.synthesis:
        heading = _one_line(s.heading)
        # K=1: heading may BE the title already rendered above — don't repeat it.
        if heading and heading != title:
            _heading(pdf, heading)
        _paragraphed_body(pdf, s.prose)
        # TD-19: no anchor footer — inline [HH:MM:SS] in the prose is the citation.
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
    _pdf_answers(pdf, summary, lab)  # last in the document, by design
