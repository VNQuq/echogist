"""Render-stage tests (T7 / TD-16 v2) — synthesis document, PDF / Markdown / dedup.

Render is local and offline, so everything runs for real: Markdown is pure string
output, and the PDF path renders an actual file with fpdf2 (installed in the dev
venv) and asserts the bundled Unicode font is embedded — the structural proxy for
"Cyrillic, no tofu" (the visual confirmation is the §12 Windows smoke). Coverage:
the v2 synthesis document (phases + anchors + main themes), decisions/actions
formatting, format dispatch + dedup, base-stem grouping with the F13 ``.json``, the
F13 ``load_summary`` round-trip, Cyrillic survival, and the failure paths.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from echogist import render, summarize
from echogist.render import RenderError
from echogist.summarize import ActionItem, CheckQuestion, Decision, Summary, SynthesisSection


def _summary(title: str = "The Talk", language: str = "en") -> Summary:
    """A representative TD-16 v2 summary: essence block + phases + anchors + decisions."""
    return Summary(
        title=title,
        core_idea="AI is becoming infrastructure.",
        decisions=(Decision("Ship local first.", "Lower cost.", anchor="[00:00:00]"),),
        action_items=(ActionItem("Benchmark int8.", "Pat", "1 day", anchor="[00:10:00]"),),
        language=language,
        synthesis=(
            SynthesisSection("Intro", "First idea here.", ("[00:00:00]",)),
            SynthesisSection("Body", "Second idea here.", ("[00:10:00]",)),
        ),
        main_themes=("efficiency", "access"),
        main_skill="Size the model to the machine.",
        test_questions=(
            CheckQuestion("Why does int8 help here?", "It halves the VRAM the weights need."),
            CheckQuestion("When would you not quantize?", "When accuracy matters more."),
            CheckQuestion("What distinction is missed?", "Load-time quantization is not storage."),
        ),
    )


# --------------------------------------------------------------------------- #
# Markdown — the v2 synthesis document
# --------------------------------------------------------------------------- #
def test_markdown_synthesis_renders_phases_anchors_and_themes(tmp_path: Path) -> None:
    text = render.render(_summary(), tmp_path, "md", log=lambda _m: None).read_text(
        encoding="utf-8"
    )
    assert text.startswith("# The Talk\n")
    assert "### Core idea" in text and "AI is becoming infrastructure." in text
    assert "## Intro" in text and "First idea here." in text  # phase heading + prose
    assert "*[00:00:00]*" not in text  # TD-19: no per-phase anchor footer wall
    assert "## Main themes" in text and "- efficiency" in text
    # decisions/actions carry their anchor in parentheses (TD-16 v2)
    assert "- Ship local first. — Lower cost.  ([00:00:00])" in text
    assert "- Benchmark int8. — Pat, estimate: 1 day  ([00:10:00])" in text
    assert text.endswith("\n")


def test_markdown_synthesis_uses_russian_headings(tmp_path: Path) -> None:
    text = render.render(_summary(title="Состояние ИИ", language="ru"), tmp_path, "md").read_text(
        encoding="utf-8"
    )
    assert "## Суть" in text  # the essence block
    assert "### Главная мысль" in text  # essence point 1
    assert "### Главный навык" in text  # essence point 2
    assert "### Проверочные вопросы" in text  # essence point 3
    assert "## Ориентиры для ответов" in text  # the answers, at the end
    assert "## Основные темы" in text  # main themes
    assert "## Принятые решения" in text  # decisions
    assert "## Пункты к выполнению" in text  # action items
    assert "Состояние ИИ" in text  # Cyrillic stays literal, not \\u-escaped


def test_markdown_essence_block_opens_and_answers_close_the_document(tmp_path: Path) -> None:
    # The block is the first thing after the title; the answers are the LAST thing in the
    # document, deliberately far from their questions so a question does not give itself
    # away. Questions are numbered, and the answers repeat the number to match.
    text = render.render(_summary(), tmp_path, "md", log=lambda _m: None).read_text(
        encoding="utf-8"
    )
    assert text.index("## Essence") < text.index("## Intro")  # block precedes the phases
    assert text.index("## Reference answers") > text.index("## Action items")  # ...answers last
    assert "### Key skill" in text and "Size the model to the machine." in text
    assert "1. Why does int8 help here?" in text  # numbered question in the block
    assert "**1. Why does int8 help here?**" in text  # repeated over its answer at the end
    assert "It halves the VRAM the weights need." in text
    # The answer text must NOT appear next to its question in the block.
    block = text[text.index("## Essence") : text.index("## Intro")]
    assert "It halves the VRAM" not in block


def test_markdown_answers_keep_the_question_numbering_when_one_has_no_answer(
    tmp_path: Path,
) -> None:
    # A question that came back without an answer still renders in the block (keeping its
    # number) and is simply absent from the answers section — the two never drift apart.
    s = replace(
        _summary(),
        test_questions=(
            CheckQuestion("First?", ""),  # no answer
            CheckQuestion("Second?", "Because of Y."),
        ),
    )
    text = render.render(s, tmp_path, "md", log=lambda _m: None).read_text(encoding="utf-8")
    assert "1. First?" in text and "2. Second?" in text  # both numbered in the block
    answers = text[text.index("## Reference answers") :]
    assert "**2. Second?**" in answers and "Because of Y." in answers
    assert "**1. First?**" not in answers  # nothing to answer with -> no entry


def test_markdown_omits_the_essence_block_when_the_summary_predates_it(tmp_path: Path) -> None:
    # A .json saved before the block existed loads with all three points empty; it must
    # re-render as a valid document with no bare "Essence" heading and no answers section.
    s = replace(_summary(), core_idea="", main_skill="", test_questions=())
    text = render.render(s, tmp_path, "md", log=lambda _m: None).read_text(encoding="utf-8")
    assert "## Essence" not in text
    assert "## Reference answers" not in text
    assert "## Intro" in text  # the rest of the document is unaffected


def test_markdown_collapses_a_multiline_question_onto_one_list_item(tmp_path: Path) -> None:
    # load_summary is verbatim, so a newline in a hand-edited question would break out of
    # its numbered list item and inject structure into the document.
    s = replace(_summary(), test_questions=(CheckQuestion("Why\n\n## Injected?", "Because."),))
    text = render.render(s, tmp_path, "md", log=lambda _m: None).read_text(encoding="utf-8")
    assert "1. Why ## Injected?" in text
    assert "\n## Injected?" not in text


def test_markdown_decision_without_anchor_has_no_parens(tmp_path: Path) -> None:
    s = Summary(
        title="T",
        core_idea="",
        decisions=(Decision("Adopt int8.", "Halves VRAM."),),  # no anchor
        action_items=(),
        language="en",
        synthesis=(SynthesisSection("Phase", "Prose.", ()),),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    assert "- Adopt int8. — Halves VRAM.\n" in text  # no trailing "  (...)" without an anchor


def test_markdown_action_item_omits_empty_owner_and_estimate(tmp_path: Path) -> None:
    s = Summary(
        title="T",
        core_idea="",
        decisions=(),
        action_items=(ActionItem("Lone task", "", ""),),
        language="en",
        synthesis=(SynthesisSection("Phase", "Prose.", ()),),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    assert "- Lone task\n" in text  # no trailing " — " when owner+estimate empty


def test_markdown_drops_owner_when_all_action_items_unassigned(tmp_path: Path) -> None:
    # Solo lecture: every owner is the per-language placeholder -> the column is noise.
    s = Summary(
        title="Лекция",
        core_idea="",
        decisions=(),
        action_items=(
            ActionItem("Перечитать главу", "Не назначено", "1 ч"),
            ActionItem("Сделать заметки", "Не назначено", ""),
        ),
        language="ru",
        synthesis=(SynthesisSection("Фаза", "Текст.", ()),),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    assert "Не назначено" not in text  # placeholder owner suppressed
    assert "- Перечитать главу — оценка: 1 ч" in text  # estimate still shown
    assert "- Сделать заметки\n" in text  # no owner, no estimate -> bare task


def test_markdown_suppresses_placeholder_owner_per_item_keeping_real_names(tmp_path: Path) -> None:
    # Mixed list: the real name is kept; the placeholder row sheds 'Не назначено'.
    s = Summary(
        title="Планёрка",
        core_idea="",
        decisions=(),
        action_items=(
            ActionItem("Задача А", "Анна", ""),
            ActionItem("Задача Б", "Не назначено", ""),
        ),
        language="ru",
        synthesis=(SynthesisSection("Фаза", "Текст.", ()),),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    assert "- Задача А — Анна" in text  # real owner kept
    assert "Не назначено" not in text  # placeholder dropped per item
    assert "- Задача Б\n" in text  # placeholder row -> bare task


def test_markdown_paragraphs_a_long_single_blob_phase(tmp_path: Path) -> None:
    blob = "Первое предложение. Второе предложение! Третье предложение? Четвёртое."
    s = Summary(
        title="T",
        core_idea="",
        decisions=(),
        action_items=(),
        language="ru",
        synthesis=(SynthesisSection("Фаза", blob, ()),),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    # >3 sentences -> split into >=2 paragraphs separated by a blank line; no text lost.
    body = text.split("## Фаза\n\n", 1)[1]
    paras = [p for p in body.split("\n\n") if p.strip()]
    assert len(paras) >= 2
    assert "Первое предложение." in text and "Четвёртое." in text


def test_markdown_skips_empty_sections(tmp_path: Path) -> None:
    bare = Summary(
        title="Bare",
        core_idea="",
        decisions=(),
        action_items=(),
        language="en",
        synthesis=(SynthesisSection("Only phase", "Just prose.", ()),),
    )
    text = render.render(bare, tmp_path, "md").read_text(encoding="utf-8")
    assert "## Only phase" in text
    assert "## Core idea" not in text  # empty core_idea -> no heading
    assert "## Main themes" not in text
    assert "## Decisions" not in text
    assert "## Action items" not in text


def test_markdown_no_anchor_footer_inline_anchor_carries_citation(tmp_path: Path) -> None:
    # TD-19: the per-phase anchor footer wall is gone. The validated timecode woven INLINE in
    # the prose is the citation; the bulk anchors array no longer renders as a footer line.
    s = replace(
        _summary(),
        synthesis=(
            SynthesisSection(
                "Phase", "He opened [00:00:00] then moved on.", ("[00:00:00]", "[00:10:00]")
            ),
        ),
    )
    text = render.render(s, tmp_path, "md").read_text(encoding="utf-8")
    assert "## Phase" in text and "He opened [00:00:00] then moved on." in text
    assert "[00:00:00]" in text  # the inline anchor in the prose survives
    assert "*[" not in text  # no italic anchor footer line at all, even with anchors present
    assert "· [00:10:00]" not in text  # the bulk anchors array is not dumped


# --------------------------------------------------------------------------- #
# PDF — the v2 synthesis document renders a real file
# --------------------------------------------------------------------------- #
def test_pdf_synthesis_renders_a_file(tmp_path: Path) -> None:
    pytest.importorskip("fpdf")
    path = render.render(_summary(), tmp_path, "pdf", log=lambda _m: None)
    assert path.exists() and path.stat().st_size > 0
    assert path.read_bytes().startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# Dispatch, dedup, base-stem grouping
# --------------------------------------------------------------------------- #
def test_unknown_format_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="Unknown output format"):
        render.render(_summary(), tmp_path, "docx")


def test_dedup_adds_numeric_suffix(tmp_path: Path) -> None:
    p1 = render.render(_summary(), tmp_path, "md")
    p2 = render.render(_summary(), tmp_path, "md")
    assert p1.name == "The Talk.md"
    assert p2.name == "The Talk-2.md"


def test_base_argument_groups_with_the_saved_json(tmp_path: Path) -> None:
    # The pipeline passes base=saved_json.stem so the triplet shares one name even
    # when the .json itself was deduped to "Talk-2".
    summarize.save_raw_result(_summary(title="Talk"), tmp_path)  # occupies "Talk.json"
    json_path2 = summarize.save_raw_result(_summary(title="Talk"), tmp_path)
    assert json_path2.stem == "Talk-2"
    pdf_like = render.render(_summary(title="Talk"), tmp_path, "md", base=json_path2.stem)
    assert pdf_like.name == "Talk-2.md"


def test_long_title_is_truncated_consistently_across_json_and_render(tmp_path: Path) -> None:
    long_title = "word " * 60  # ~300 chars, well over the MAX_PATH-safe cap
    s = _summary(title=long_title)
    json_path = summarize.save_raw_result(s, tmp_path)
    md_path = render.render(s, tmp_path, "md", base=json_path.stem)
    assert len(json_path.stem) <= 100
    assert md_path.stem == json_path.stem  # same truncated base -> grouped triplet


# --------------------------------------------------------------------------- #
# F13 — load_summary round-trips the saved .json (re-render, no re-pay)
# --------------------------------------------------------------------------- #
def test_load_summary_round_trips_a_saved_json(tmp_path: Path) -> None:
    original = _summary(title="Состояние ИИ", language="ru")
    json_path = summarize.save_raw_result(original, tmp_path)
    loaded = render.load_summary(json_path)
    assert loaded == original  # exact reconstruction incl. synthesis + anchors


def test_load_summary_round_trips_the_td22_source_back_link(tmp_path: Path) -> None:
    """The back-link must survive disk, or a re-rendered summary stops matching its
    source and the next bulk run re-pays for a lecture already summarized."""
    src = tmp_path / "Lecture.mp4"
    src.write_bytes(b"x")
    json_path = summarize.save_raw_result(_summary(title="Talk"), tmp_path / "raw", source_path=src)
    assert render.load_summary(json_path).source_path == str(src.resolve())


def test_load_summary_of_a_pre_td22_json_has_an_empty_back_link(tmp_path: Path) -> None:
    json_path = summarize.save_raw_result(_summary(title="Old"), tmp_path)
    assert render.load_summary(json_path).source_path == ""


def test_load_summary_bad_json_fails_loud(tmp_path: Path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RenderError, match="Could not read the saved summary"):
        render.load_summary(bad)


def test_load_summary_non_object_fails_loud(tmp_path: Path) -> None:
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(RenderError, match="expected a JSON object"):
        render.load_summary(arr)


def test_load_summary_tolerates_missing_fields(tmp_path: Path) -> None:
    partial = tmp_path / "p.json"
    partial.write_text('{"title": "Only a title"}', encoding="utf-8")
    loaded = render.load_summary(partial)
    assert loaded.title == "Only a title"
    assert loaded.synthesis == ()
    assert loaded.decisions == () and loaded.main_themes == ()


def test_load_summary_filters_non_dict_synthesis(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(
        '{"title":"T","synthesis":'
        '[{"heading":"Intro","prose":"p","anchors":["[00:00:00]"]},"junk",{"prose":"x"}]}',
        encoding="utf-8",
    )
    loaded = render.load_summary(p)
    assert len(loaded.synthesis) == 2  # the bare string is dropped
    assert loaded.synthesis[0] == SynthesisSection("Intro", "p", ("[00:00:00]",))
    assert loaded.synthesis[1].heading == ""  # missing key coerces to ""


# --------------------------------------------------------------------------- #
# Empty-title fallback parity (load_summary can rebuild an empty title)
# --------------------------------------------------------------------------- #
def test_markdown_empty_title_uses_fallback(tmp_path: Path) -> None:
    s = Summary("", "", (), (), "en", (SynthesisSection("Phase", "Prose.", ()),))
    text = render.render(s, tmp_path, "md", base="x").read_text(encoding="utf-8")
    assert text.startswith("# Summary\n")  # not a bare "# "


# --------------------------------------------------------------------------- #
# base is sanitized defensively (a raw/unsafe base can't escape the dir)
# --------------------------------------------------------------------------- #
def test_render_sanitizes_an_unsafe_base(tmp_path: Path) -> None:
    out = render.render(_summary(), tmp_path, "md", base="../../etc/passwd")
    assert out.parent == tmp_path  # no path traversal — stays in out_dir
    assert "/" not in out.name and "\\" not in out.name


# --------------------------------------------------------------------------- #
# PDF — real fpdf2 render; assert the bundled Cyrillic font is embedded
# --------------------------------------------------------------------------- #
def test_pdf_renders_a_real_file_with_embedded_font(tmp_path: Path) -> None:
    pytest.importorskip("fpdf")  # real render needs fpdf2; lean dev/CI venv may omit it
    path = render.render(_summary(title="Состояние ИИ", language="ru"), tmp_path, "pdf")
    assert path == tmp_path / "Состояние ИИ.pdf"
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 5000  # an embedded font subset makes this far from trivial
    assert b"DejaVu" in data  # the bundled Unicode font is embedded (no tofu)


def test_pdf_bundled_fonts_are_present_in_the_package() -> None:
    # The "embedded DejaVuSans" guarantee (plan §6) depends on these files shipping.
    assert render._FONT_REGULAR.is_file()
    assert render._FONT_BOLD.is_file()


def test_pdf_missing_font_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fpdf")  # the font check sits after the lazy fpdf import
    monkeypatch.setattr(render, "_FONT_REGULAR", tmp_path / "nope.ttf")
    with pytest.raises(RenderError, match="bundled PDF font is missing"):
        render.render(_summary(), tmp_path, "pdf")


def test_pdf_layout_failure_fails_loud_and_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fpdf2 raises FPDFException (subclasses Exception, NOT OSError) on unrenderable
    # layout. It MUST become a clean RenderError with no half-written .pdf left behind.
    fpdf = pytest.importorskip("fpdf")

    def boom(self: object, *a: object, **k: object) -> None:
        raise fpdf.errors.FPDFException("Not enough horizontal space")

    monkeypatch.setattr(fpdf.FPDF, "output", boom)
    with pytest.raises(RenderError, match="Could not write the PDF"):
        render.render(_summary(title="Boom"), tmp_path, "pdf")
    assert not (tmp_path / "Boom.pdf").exists()  # cleanup ran


def test_pdf_missing_fpdf_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **k: object) -> object:
        if name == "fpdf" or name.startswith("fpdf."):
            raise ImportError("no fpdf")
        return real_import(name, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RenderError, match="fpdf2 is not installed"):
        render.render(_summary(), tmp_path, "pdf")
