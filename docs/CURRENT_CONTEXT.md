# Current Context

**Updated:** 2026-09-04 — **the first full folder run finished: 7 lectures, 59 calls, $2.4003. Cost model
rebuilt on its numbers (TD-23, TD-24 closed), step 4 (оформление) shipped, and `batch`+`bulk` merged into
one `folder.py`. The model's three CJK slips are diagnosed and instrumented (TD-29 closed,
TD-28 halved).**
**Authority:** [CLAUDE.md](../CLAUDE.md) — the hard constraints and the rationale live there, not here.
**Max:** ≈ 60 lines.

## Pipeline — TD-16 v2 direct synthesis (v2.0.0)

**The only path:** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short = K=1) →
`synthesize_summary` ×K sequential forward-only (each phase sees its span + prior headings + the prior phase's
TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept / snap-2s / drop vs that phase's
own timecodes) → concatenate decisions/actions → reconcile (title + essence block + main_themes + normalized
headings — **always**, incl. K=1) → header validation → one doc. **Validated 2026-06-27** (Sonnet, 2:58:57 RU
lecture, K=4, $0.5237, 131/131 anchors resolve) — TD-16 entry.

## Live invariants from v2.3.0 / v2.2.0 (full entries in [CHANGELOG.md](../CHANGELOG.md))

- **Two channels out of `summarize`, not one** (TD-29). `log` = the ~60 phase lines, muted
  (`ui.detail`). `notice` = findings the operator must act on, loud (`ui.warn`): a DROPPED
  anchor, and a foreign-script slip. The anchor line routes itself on the dropped count.
  **Do not merge them** and do not sniff the message text in `menu._progress`.
- **The script check is an instrument, not a cosmetic fix** (`echogist/alphabet.py`,
  `summarize.report_foreign_scripts`, design at [docs/designs/script-check.md](./designs/script-check.md)).
  Haiku spliced Chinese morphemes into Russian words 3x in 7 lectures; we only noticed
  because the PDF font could not draw them. A same-language word swap would be invisible,
  so this is the one class of model drift that declares itself — keep it even after the
  prompt sentence appears to work, because it is what MEASURES whether the sentence works.
  Allowed scripts per language are a code dict in `summarize.py` next to `_LANGUAGE_NAMES`,
  not config: the operator never tunes "Russian is written in Cyrillic".
- **Name-claiming.** `dated_artifact_path` SELECTS a name without creating it, so colliding stems race.
  `folder._plan_output_paths` claims every mp3 name up front, single-threaded, **in memory** — an on-disk claim
  survived `kill -9` as a fake artifact that poisoned dedup forever, and `.part` cannot be the marker because
  dedup deliberately cannot see it. **One publish scheme (`.part`→`os.replace`); do not add a second.**
  *The resume file now keys on the resolved source path; transcripts and summary json do NOT yet claim.*
- **Ctrl-C stops the run, not the app** — the one local exception to the global Ctrl-C contract, carried on
  `folder.Cancelled` with the partial report. Cancel is bounded: SIGTERM, then `kill()` after 5s.
- **Menu keys are SEMANTIC** (`local`/`batch`/…) but number keys are POSITIONAL (`ui._bind_number_keys`), so
  inserting a row renumbers every row after it for the operator's fingers.
- **Essence block:** reconcile writes it from phase prose only, never a second transcript read; runs ALWAYS
  incl. K=1, so short input is **2 calls, not 1**. A video asks before keeping the MP3, an mp3 source is never
  asked per-run. **That asymmetry is intentional; do not "fix" it.**

## Config / behavior notes

- Tools `emit_phase`/`emit_reconcile`; the inline `[интерпретация]:` marker is plain text, surviving MD+PDF.
  **Default tier `economy` (Haiku)**. **Prompt is data** (`config/models.toml`), SCHEMAs stay in code.
- **Model currency:** tiers pin floating aliases (`claude-haiku-4-5`, `-sonnet-5`, `-opus-4-8`) —
  always-latest, reproducibility intentionally dropped. `scripts/check-models.py` (standalone,
  killswitch-safe) reports retired/valid + context drift vs `GET /v1/models`, derives `context_window`, flags
  a tier `prices_unverified` on a generation bump. **Prices stay manual** — no pricing endpoint; while
  flagged `_run_summary` prints a one-time notice ($0.50 gate unchanged). `balanced` verified 2026-08-03.
- **Anchors are TEXTUAL, not links** — `[HH:MM:SS]` inline in prose (TD-19); the validator guarantees each
  resolves to a real transcript block.
- **Output:** recovery `.json` → `output/summaries/raw/`, resume partial → `raw/.resume/`, readable
  `.pdf`/`.md` → `output/summaries/`, one shared stem. `output/` is gitignored and machine-local, so a
  Windows-produced summary is only re-validatable in WSL against ITS transcript. **API key:**
  `ANTHROPIC_API_KEY` env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are Windows-only.

## bulk v3 — increments 0, 1, 2 SHIPPED ([docs/designs/bulk-v3.md](./designs/bulk-v3.md))

Approved 2026-09-04 after /office-hours (3 rounds: 7, 6, 7) and /plan-eng-review (CLEAR). The design doc carries
the spec; only what the code cannot tell you lives here.

- **Increment 0 ✓** resume keyed on `blake2s(resolved source path, casefolded)`. **Increment 1 ✓** `scan.py` +
  menu row 4 `Scan a folder`. A folder the walk cannot LIST is reported, not lost; cached numbers are INPUT and
  are range-bounded; Ctrl-C MERGES the scan cache while a completed run REPLACES it (so it holds ONE root).
- **Casefold is asymmetric ON PURPOSE.** `menu._resume_key` folds case (one file the operator picked twice, on
  Windows). `naming.resolve_source` does NOT (a dedup key across a tree — folding made `Lecture.mp4` and
  `lecture.mp4` one entry and dropped a real recording silently). Same word, opposite job; do not "unify" them.
- **The real folder (Windows, 2026-09-04):** 7 files, one per folder, 23h43m, 8.0 GB, **0 duplicate names,
  0 unreadable**. It killed the case for increment 2's naming machinery.
- **Increment 2 ✓ the folder run** (now `folder.py`, Folder module actions `Summary` / `Transcript`). **Two phases:** TRANSCRIBE the whole folder (local, free), then ONE exact quote over the real
  transcripts and ONE confirm, then the paid calls. No per-file gate; that is the babysitting it removes.
  The duration→token constants are off this path: it prices the REAL transcripts.
- **The collision machinery was NOT built.** `folder.plan_run` instead refuses the one operation that is wrong
  under a collision: reusing a saved transcript whose stem is ambiguous in either direction. Re-transcribing is
  free and visible; summarizing one lecture from another's transcript is paid and silent. The SUMMARY skip has
  no such limit — TD-22 joins it on the resolved source path, not a name.
- **The folder run DOES extract MP3s** (2026-09-04: sharing `_ACTION_CHOICES` made the label a contract, and
  the folder rows promised an artifact `extract` was never called for) and its plan is SORTED, not in
  `os.walk` order, so a 7-lecture course plays back 1..7 and reproduces between runs.
- **One module, two engines** (`folder.py`, merged 2026-09-04 — `batch`/`bulk` were synonyms and neither name
  said which one spent money). `convert_many` is the PARALLEL ffmpeg pool with its own `Cancellation`;
  `run_phase` is the strictly sequential one (one GPU job, one cost gate). They share `Item`/`Report`/
  `Cancelled`. **Do not push the sequential run through the pool** — the gate must see a whole folder.
- **Increment 1b (mp3 re-encode, `dec-c3adfe5b`)** — DRAFT only at
  [docs/designs/mp3-reencode-1b.md](./designs/mp3-reencode-1b.md), unreviewed and unimplemented. Two open
  questions plus a MiB/MB unit discrepancy (207 vs 197 kbps at 2.5h). With the real durations known, the
  222 MiB threshold works out to 206 kbps for the shortest lecture and 94 kbps for the longest.
- **Pinned regressions:** cross-source summary prose (inc 0); cost estimate biased low (inc 1); the folder
  gate asked ONCE not per file, and `folder_estimate` summing per-file quotes rather than flattening them into
  one reconcile (inc 2). Plus the cost model's own calibration test, which reconstructs the 2026-09-04 run
  from its published numbers and fails if a quote ever drops under that bill again. 659 tests green.

## Next

**The run that changed things (2026-09-04).** Seven RU lectures, 23h43m, `economy`/Haiku, 59 cloud
calls, **$2.4003 actually spent**, 0 failures, every phase's anchors validated. It is the first end-to-end
paid folder run and it is now the calibration fixture for everything below.

- **The cost model was rebuilt on it (TD-24 closed).** The flat per-call output projection
  (`[guard].output_tokens_estimate = 4600`) is GONE. Output is projected per call as
  `tier.output_per_input_ratio x that call's input`, clamped by `max_output_tokens`, with a floor under
  the reconcile call only (`[summarize].reconcile_output_floor_tokens = 2500` — the one real fixed
  per-call cost; without it a folder of short clips quotes like one long file). The gate quoted **0.93x**
  the bill before, **1.07x** now, and it no longer moves when the phase split changes.
  **Re-seed a tier from any finished run's own "Actual cost" line: output/input, plus ~5%.** No code, no
  transcript. Measured: economy 0.3707 -> seeded 0.39; balanced 0.2225 -> 0.24; flagship unmeasured.
- **TD-23 closed:** `[scan]` reseeded 150x7 -> 135x6.5. The scan quote for that same course was ALSO
  under the bill (~$2.20 vs $2.4003); it now lands 1.31x, and the console label went from "UPPER BOUND"
  to "PROJECTION, biased high" with the margin named.
- **The stray folder picker is fixed.** After the run finished, the main menu launched `Scan a folder`
  on its own. A menu prompt reads whatever was typed while a flow held the console with nothing to
  answer, so `ui.drain_input()` now flushes the console buffer after every flow returns, before the menu
  re-opens. Type-ahead into a visible prompt still works. **The keystroke's origin was never proven** —
  only the mechanism, which the fix closes regardless.
- **TD-28 opened (MEDIUM):** three summaries contain Chinese characters (`描`, `技`, `催化剂`) that the
  PDF font cannot draw, and `render` emits the PDF anyway. The font gap is the small half; text the
  author never said, unmarked, is the half that matters. Needs the operator to read those passages.

**Step 4 — оформление, SHIPPED.** Three changes, all sized by the seven-file run rather than by taste.
A `ui.rule()` section header per file, so ~60 flat lines become sections the eye skips through. The
phase-by-phase synthesis lines print MUTED (`ui.detail`), so they stop competing with the result.
The end-of-run report gained a row per file with what it cost, plus Time and the quote next to the bill
with the ratio between them — that ratio is the only feedback the cost model gets, and it is what
`output_per_input_ratio` is re-seeded from. One thing only a LOOK could find: rich's repr highlighter
was recoloring numbers, paths and times INSIDE our own styled strings, so a muted line came out with
bright cyan digits. Every print now passes `highlight=False` as well as `markup=False`.

1. **Read the three flagged passages** (TD-28) and say whether the CJK is a gloss the model invented.
2. **TD-29 (MEDIUM)** — a dropped anchor prints in the same voice as a clean one, and muting the
   synthesis channel made it dimmer. Needs the `log` seam to carry a severity; own review.
3. **TD-27 (HIGH) — decide the `output/` artifact-release unit** and land a real `paths` module.
   Trigger: before the SECOND real course goes through the folder run. That trigger is now close.
4. **Restore hand-picked file subsets as a menu row** — awaiting whether the operator uses it.
5. **Streaming the model reply** — own change, own review. Older open items unchanged: the increment 1b
   draft (`docs/designs/mp3-reencode-1b.md`, NOT approved), and the first live `scripts/check-models.py`
   run on a box with a key.
