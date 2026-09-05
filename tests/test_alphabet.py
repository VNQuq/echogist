"""Script-check tests — the offline "did the model stay in the language" gate.

Pure functions over strings: no model, no key, no network, no fixture. Every case here
is either one of the three real defects the first folder run produced (2026-09-04) or a
false positive that would make the check unusable if it fired.
"""

from __future__ import annotations

from echogist import alphabet

RU = frozenset({"cyrillic", "latin"})


def test_script_of_names_the_writing_system() -> None:
    assert alphabet.script_of("а") == "cyrillic"
    assert alphabet.script_of("a") == "latin"
    assert alphabet.script_of("催") == "cjk"
    assert alphabet.script_of("β") == "greek"


def test_non_letters_belong_to_no_script() -> None:
    """Digits, punctuation, whitespace and the anchor brackets are shared by every
    language — flagging them would make every summary a finding."""
    for char in "0 9 . , — – ! ? : ; ( ) [ ] / \\ \n \t «»":
        assert alphabet.script_of(char) is None, char


def test_the_three_real_defects_are_all_caught() -> None:
    """The verbatim slips from the 2026-09-04 folder run. If any of these stops being
    reported, the check has lost the exact thing it was built for."""
    for text, run in (
        ("Привлечь коуча как催化剂для перехода к книге.", "催化剂"),
        ("...требует материальной и技ической полноты.", "技"),
        ("Преподаватель描написывает альтернативное видение.", "描"),
    ):
        findings = alphabet.foreign_findings(text, RU)
        assert len(findings) == 1
        assert findings[0].run == run
        assert findings[0].script == "cjk"


def test_a_run_is_one_finding_not_one_per_character() -> None:
    """催化剂 is three characters of one mistake. Reported per character it would read as
    three separate problems and triple the noise of every real slip."""
    findings = alphabet.foreign_findings("как催化剂для", RU)
    assert [f.run for f in findings] == ["催化剂"]


def test_two_adjacent_foreign_scripts_are_two_findings() -> None:
    """A stretch that changes script mid-run must not be reported under one label — each
    finding has to name the script it actually is."""
    findings = alphabet.foreign_findings("слово催βконец", RU)
    assert [(f.script, f.run) for f in findings] == [("cjk", "催"), ("greek", "β")]


def test_latin_inside_russian_is_not_a_finding() -> None:
    """The rule catches a change of SCRIPT, not a foreign word: a Russian lecture
    legitimately says coach, MVP, or an English book title. If this fires, every real
    summary becomes a false positive and the operator learns to ignore the channel."""
    text = "Привлечь coach как MVP-практику из книги The Art of Learning."
    assert alphabet.foreign_findings(text, RU) == ()


def test_english_does_not_allow_cyrillic() -> None:
    """The table is per language, not one global allowlist: Cyrillic is legitimate in a
    Russian summary and a defect in an English one."""
    assert alphabet.foreign_findings("a Русское слово here", frozenset({"latin"}))


def test_an_empty_allowlist_allows_everything() -> None:
    """Fail-soft for a language nobody calibrated: silence, not a finding on every
    character of a summary the check knows nothing about."""
    assert alphabet.foreign_findings("催化剂 βeta текст", frozenset()) == ()


def test_the_finding_quotes_enough_context_to_recognize_the_sentence() -> None:
    text = "Задача курса — воспитать мышление. Преподаватель描написывает видение мира."
    (finding,) = alphabet.foreign_findings(text, RU)
    assert "Преподаватель描написывает" in finding.context
    assert "\n" not in finding.context, "a finding has to survive as one console line"


def test_newlines_in_the_prose_do_not_split_the_quote() -> None:
    """Prose carries hard newlines; a context that keeps them would print as three
    ragged lines and lose the sentence the operator is meant to read."""
    (finding,) = alphabet.foreign_findings("первая строка\nвторая催третья\nчетвёртая", RU)
    assert finding.context.count("\n") == 0
    assert "вторая催третья" in finding.context


def test_a_clean_summary_reports_nothing() -> None:
    assert alphabet.foreign_findings("Обычный русский текст с anchor [00:12:34].", RU) == ()


def test_findings_come_back_in_document_order() -> None:
    """The operator reads them top to bottom against the document; out of order they are
    a puzzle rather than a report."""
    findings = alphabet.foreign_findings("раз催два技три描", RU)
    assert [f.run for f in findings] == ["催", "技", "描"]


def test_the_whole_text_being_foreign_is_reported_not_swallowed() -> None:
    """The runaway case: a reply that switched language wholesale is still ONE run, so
    the caller gets one finding to cap rather than thousands."""
    findings = alphabet.foreign_findings("催化剂技描", RU)
    assert len(findings) == 1
    assert findings[0].run == "催化剂技描"


def test_notation_is_not_a_change_of_writing_system() -> None:
    """Every one of these is ``isalpha()`` and none is a language slip.

    ``unicodedata.name()``'s first token reads MICRO SIGN as the "micro" script and
    MATHEMATICAL ITALIC SMALL X as the "mathematical" one. A Russian technical summary
    produces all of them legitimately, and an instrument that cries at correct text is
    one the operator learns to scroll past — which costs the CJK detection it exists for.
    """
    allowed = frozenset({"cyrillic", "latin", "greek"})

    for text in ("сопротивление 3 Ω", "частота 5 µс", "объём 2 ℓ", "формула 𝑥 = 𝑎", "1ª поправка"):
        assert alphabet.foreign_findings(text, allowed) == (), text


def test_the_measured_defect_is_still_caught_including_a_single_character() -> None:
    """The three real slips from the operator's own seven lectures.

    One of them is a single Han character inside a Russian word, so no "ignore short
    runs" rule can be used to quiet the false positives above.
    """
    allowed = frozenset({"cyrillic", "latin", "greek"})

    assert [f.run for f in alphabet.foreign_findings("как催化剂для перехода", allowed)] == [
        "催化剂"
    ]
    assert [f.run for f in alphabet.foreign_findings("Преподаватель描написывает", allowed)] == [
        "描"
    ]
