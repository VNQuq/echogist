# Script check — make the model's language slips loud

**Status:** SHIPPED 2026-09-04 (operator-approved, /plan-eng-review CLEAR — report at the end)
**Closes:** TD-28 (the fidelity half), TD-29
**Touches:** LLM prompt + fidelity signalling — CLAUDE.md forbids skipping the review gate here.

## The observation

The first real folder run (7 lectures, `economy`/Haiku, 2026-09-04) produced three
Chinese morphemes spliced into the middle of Russian words:

| file | emitted | intended |
|---|---|---|
| `Путь к цели-…` | `как催化剂для перехода` | `как катализатор для перехода` |
| `Путь к переменам-…` | `материальной и技ической полноты` | `и технической полноты` |
| `Лекция- переформулирование…` | `Преподаватель描написывает` | `Преподаватель описывает` |

No fabricated content: the meaning survives, only the spelling breaks. Three words in
seven lectures.

## Why this is worth code

The severity is low. The category is not.

We only learned about it because the PDF font could not draw those glyphs and fpdf2
warned. Had the model swapped a Russian word for a *different Russian word*, the render
would be clean and nothing would have said a thing. So the CJK is not the bug — it is the
one class of model drift that **declares itself**, and it is free signal about how far the
cheap tier wanders. Removing the symptom (a prompt line) without keeping the instrument
would trade a visible defect for an invisible one.

## Design

### 1. A prompt line — the mitigation

One sentence in `[summarize].synthesis_system_prompt` and `reconcile_system_prompt`
(`config/models.toml`, data not code) telling the model to write in the target language's
script only. Cheap, and it probably reduces the rate. It is **not** the fix: a prompt is a
wish addressed to a sampler, and on its own it gives no way to know whether it worked.

### 2. `echogist/alphabet.py` — the instrument

A new local, offline module, ~40 lines, the exact shape of `validate_anchors`: a
deterministic check over the model's reply, reported, never silent.

```python
def foreign_findings(text: str, allowed: frozenset[str]) -> tuple[Finding, ...]
```

* A character that is not `str.isalpha()` (digits, punctuation, whitespace, symbols,
  the `[HH:MM:SS]` brackets) is always allowed — the rule is about *writing systems*,
  not vocabulary.
* An alphabetic character's script is the first token of its `unicodedata.name()`
  (`CYRILLIC SMALL LETTER A` → `cyrillic`, `CJK UNIFIED IDEOGRAPH-50AC` → `cjk`). No
  range tables to maintain, no dependency, and an unnameable character is reported
  rather than assumed fine.
* Consecutive offending characters collapse into one `Finding` carrying the run and a
  short window of surrounding text, so the operator reads `как催化剂для перехода`, not
  three separate reports of one character each.

### 3. The allowed-script table — a code dict, not config

```python
# echogist/summarize.py, next to _LANGUAGE_NAMES and _INTERPRETATION_LABELS
_ALLOWED_SCRIPTS = {
    "ru": frozenset({"cyrillic", "latin"}),
    "en": frozenset({"latin"}),
}
```

**Reversed during review (D1).** The first draft put this in `config/models.toml`. It does
not belong there: `theme.py:9-12` already states the project's line — *"a palette is
developer taste, not operator behavior; serializing it into the config would add a
parse/validate surface for values the solo operator never tunes"* — and which script a
language is written in is a fact about the language, not a preference. The two OTHER
per-language facts (`_LANGUAGE_NAMES`, `_INTERPRETATION_LABELS`) are already code dicts in
this exact spot, both fail-soft on an unknown code. Adding a language already edits them;
a third table in TOML would split one concept across two files and buy a `ConfigError`
path nobody needs. Cost of the reversal: three fewer files in the diff.

**Latin is allowed in `ru` deliberately.** A Russian lecture legitimately contains
`coach`, `MVP`, a book title. The check catches a change of *script*, not a foreign word.
An unlisted language allows everything (reports nothing) rather than flooding a language
nobody calibrated.

### 4. Where it runs, and what it does when it fires

**Once, at the end of `synthesize_summary`**, over the final Summary, after the last
`validate_anchors`. Not per phase: this way the reconcile pass's own prose (the title and
the essence block, the freest writing in the document) is covered too, and a RESUMED run —
whose earlier phases came back from disk and were never re-synthesized — is still checked
end to end. It walks every field that becomes prose (`_readable_text`) and never the
anchors, which are coordinates, already validated, and not language.

**It reports; it never fails the run and never edits the text.** Dropping a paid summary
over three characters would throw away money already spent for a defect the operator can
read straight past, and rewriting the model's word to guess the intended one would be
exactly the silent fabrication this pipeline exists to prevent. Same division as the
anchor gate: the machine states the fact, the human decides.

**Console only — no JSON stamp (D2).** The finding is provenance about the *run*, not part
of the *document*. Stamping it would add a `Summary` field that `render.load_summary`
(render.py:120) must read back and the renderer must decide whether to draw, for a value
nothing currently reads. The loud console line plus the per-file run report put it in
front of the operator while they can still act on it.

**Capped output.** The pure function returns every finding; the reporter quotes the first
five and then counts. A reply that switched language wholesale must not bury the run's own
result under hundreds of lines.

### 5. TD-29 — the channel that makes it loud

None of the above is worth anything if it prints in the same muted grey as the sixty
phase lines (which is exactly what shipped in `91d4c77`). `summarize.Logger` is
`Callable[[str], object]` and cannot say "this one matters".

**Chosen:** a second channel, `notice: Notice = print`, alongside `log` on
`summarize_auto` / `synthesize_summary` / `validate_anchors`. `log` is progress chatter
(menu wires `ui.detail`), `notice` is what the operator must see (menu wires `ui.warn`).
The anchor line routes itself: `(notice if dropped else log)(...)`, so `0 dropped` stays
scenery and `4 dropped` does not.

**Rejected:** widening `Logger` to carry a level. Seven modules define that alias
independently, `print` does not accept a level kwarg, and the blast radius buys nothing
over two named channels — there are exactly two audiences here, not a spectrum.

## Killswitch

`alphabet.py` imports `unicodedata` and nothing else; it never reaches the wire. The
prompt change is data. The whole check runs in CI against the stub summarizer.

## What is NOT in scope

The PDF renderer's silent glyph hole stays open in TD-28. After this change the operator
learns about a bad character from a loud console line before ever opening the document,
so it drops from silent corruption to cosmetic.

## Verification

Not a fixture: the finished implementation was run against the operator's seven real
summaries from the 2026-09-04 folder run (`output/summaries/raw/*.json`, Windows), through
the real `RichQuestionaryUI`.

**Exactly 3 of 7 flagged, and they are exactly the three known defects. Zero findings on
the other four.** The console reads:

```
─────── Путь к переменам- цели, люди и опора на себя ───────
Synthesizing phase 7/7 (02:31:04-02:52:18)...                        (dim)
Reconciling the phases into a document header + essence block...     (dim)
Foreign script in the summary (cjk), 1 place — the model slipped out (yellow)
of the target language. The text is kept as written; check it:       (yellow)
  ...лощение требует материальной и技ической полноты. Основной навы... (yellow)
Summary ready: Путь к переменам: цели, люди и опора на с  (cyan)
```

That run is also the false-positive test that matters: four real Russian lecture summaries,
full of Latin borrowings and timecodes, produced nothing.

## GSTACK REVIEW REPORT

| Runs | Status | Findings |
|---|---|---|
| Scope challenge (Step 0) | complete | 1 (9 files → 6; the TOML table was the excess) |
| 1. Architecture | complete | 3 (2 escalated to the operator, both reversed the draft) |
| 2. Code quality | complete | 2 (1 flagged-not-fixed, 1 acted on) |
| 3. Tests | complete | 23 tests added; every new branch covered |
| 4. Performance | complete | 1 (acted on) |
| Outside voice (codex) | skipped | `codex_reviews: disabled`; no subagent fallback per skill rule |

**Findings**

- `[P1] (9/10) docs/designs/script-check.md §3` — allowed-script table placed in
  `config/models.toml`, contradicting `theme.py:9-12`'s own config-vs-code line while the
  two sibling per-language maps (`summarize.py:62`, `summarize.py:72`) are code.
  **Operator chose: code dict.** Diff dropped from 9 files to 6.
- `[P2] (8/10) docs/designs/script-check.md §4` — "after `validate_anchors`" was ambiguous
  between the per-phase and the final call; per-phase would miss the reconcile header and
  skip restored phases on a resume. **Fixed:** one check, at the end, over the final Summary.
- `[P2] (7/10) render.py:120` — stamping the finding into the raw `.json` adds a `Summary`
  field the loader must read back and the renderer must decide about, for a value nothing
  reads. **Operator chose: console only.**
- `[P2] (9/10) summarize.py:57, extract.py:30, render.py:51, transcribe.py:31,
  provision.py:32, model_asset.py:33, menu.py:104` — `Logger = Callable[[str], object]` is
  defined seven times. Pre-existing DRY violation. **Flagged, not fixed** (CLAUDE.md
  surgical rule); it is also the reason `notice` is a second channel rather than a level
  argument, which would have had to land in all seven.
- `[P2] (8/10) menu.py:112` — `SummarizeFn = Callable[..., SummarizeResult]`, so a new
  `notice` kwarg is invisible to mypy at the call site and would be swallowed by a test
  stub's `**_kw`, leaving the suite green with the channel unwired. **Fixed:** the stub
  takes `notice` explicitly and a test pins that a finding lands on `warn`, not `detail`.
- `[Perf] (8/10) alphabet.py:script_of` — `unicodedata.name()` per character is ~40k C
  calls on a long summary. **Fixed:** `lru_cache`, so it costs one lookup per DISTINCT
  character. No range tables.
- `[P3] (9/10)` — Greek and math-alphanumeric letters will also be flagged. Verified by
  probe. Acceptable because the check only reports; the message is worded as "check this",
  not "this is broken".

**Eval scope:** this repo has no eval suite and no eval harness, so the prompt sentence has
no automated verification. That is the point of shipping the check alongside it: the check
is the instrument that measures whether the prompt helped, and the next real folder run is
the measurement.

**VERDICT: CLEAR** — shipped with all findings resolved or explicitly deferred.

NO UNRESOLVED DECISIONS
