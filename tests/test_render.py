"""Render-stage tests (T7) — PDF (fpdf2 + bundled DejaVuSans) / Markdown / dedup.

Render is local and offline, so everything runs for real: Markdown is pure string
output, and the PDF path renders an actual file with fpdf2 (installed in the dev
venv) and asserts the bundled Unicode font is embedded — the structural proxy for
"Cyrillic, no tofu" (the visual confirmation is the §12 Windows smoke). Coverage:
format dispatch + dedup, base-stem grouping with the F13 ``.json``, localized
headings, the F13 ``load_summary`` round-trip, Cyrillic survival, and the
missing-font / bad-format failure paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from echogist import render, summarize
from echogist.render import RenderError
from echogist.summarize import SectionMarker, Summary


def _summary(title: str = "AI in 2026", language: str = "en") -> Summary:
    return Summary(
        title=title,
        overview="A talk about where AI is heading.",
        key_takeaways=("Models got cheaper.", "Local inference matters."),
        section_timecodes=(
            SectionMarker("[00:00:00]", "Intro"),
            SectionMarker("[00:12:30]", "Costs"),
        ),
        recurring_themes=("efficiency", "access"),
        core_idea="AI is becoming infrastructure.",
        language=language,
    )


# --------------------------------------------------------------------------- #
# Markdown — pure, no dependency
# --------------------------------------------------------------------------- #
def test_markdown_has_title_headings_and_bullets(tmp_path: Path) -> None:
    path = render.render(_summary(), tmp_path, "md", log=lambda _m: None)
    assert path == tmp_path / "AI in 2026.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# AI in 2026\n")
    assert "## Key takeaways" in text
    assert "- Models got cheaper." in text
    assert "- `[00:00:00]` Intro" in text
    assert text.endswith("\n")


def test_markdown_uses_russian_headings_for_ru_summary(tmp_path: Path) -> None:
    path = render.render(_summary(title="Состояние ИИ", language="ru"), tmp_path, "md")
    text = path.read_text(encoding="utf-8")
    assert "## Обзор" in text  # localized heading, not "Overview"
    assert "## Ключевые выводы" in text
    assert "Состояние ИИ" in text  # Cyrillic stays literal, not \\u-escaped


def test_markdown_skips_empty_sections(tmp_path: Path) -> None:
    bare = Summary(
        title="Bare",
        overview="just an overview",
        key_takeaways=(),
        section_timecodes=(),
        recurring_themes=(),
        core_idea="",
        language="en",
    )
    text = render.render(bare, tmp_path, "md").read_text(encoding="utf-8")
    assert "## Overview" in text
    assert "## Key takeaways" not in text  # empty arrays produce no heading
    assert "## Core idea" not in text


# --------------------------------------------------------------------------- #
# Dispatch, dedup, base-stem grouping
# --------------------------------------------------------------------------- #
def test_unknown_format_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="Unknown output format"):
        render.render(_summary(), tmp_path, "docx")


def test_dedup_adds_numeric_suffix(tmp_path: Path) -> None:
    p1 = render.render(_summary(), tmp_path, "md")
    p2 = render.render(_summary(), tmp_path, "md")
    assert p1.name == "AI in 2026.md"
    assert p2.name == "AI in 2026-2.md"


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
    assert loaded == original  # exact reconstruction incl. tuples + markers


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
    assert loaded.key_takeaways == ()
    assert loaded.section_timecodes == ()


def test_load_summary_filters_non_dict_markers(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(
        '{"title":"T","section_timecodes":'
        '[{"timecode":"[00:00:00]","title":"Intro"},"junk",{"title":"NoTime"}]}',
        encoding="utf-8",
    )
    loaded = render.load_summary(p)
    assert len(loaded.section_timecodes) == 2  # the bare string is dropped
    assert loaded.section_timecodes[0] == SectionMarker("[00:00:00]", "Intro")
    assert loaded.section_timecodes[1].timecode == ""  # missing key coerces to ""


# --------------------------------------------------------------------------- #
# Empty-title fallback parity (load_summary can rebuild an empty title)
# --------------------------------------------------------------------------- #
def test_markdown_empty_title_uses_fallback(tmp_path: Path) -> None:
    s = Summary("", "ov", (), (), (), "", "en")
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
