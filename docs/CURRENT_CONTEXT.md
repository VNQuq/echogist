# Current Context

**Updated:** 2026-09-04 — **bulk v3 designed and eng-cleared; no code written yet.**
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

- **Name-claiming.** `dated_artifact_path` SELECTS a name without creating it, so colliding stems race.
  `batch._plan_output_paths` claims every mp3 name up front, single-threaded, **in memory** — an on-disk claim
  survived `kill -9` as a fake artifact that poisoned dedup forever, and `.part` cannot be the marker because
  dedup deliberately cannot see it. **One publish scheme (`.part`→`os.replace`); do not add a second.**
  *Transcripts, summary json and the resume file do NOT yet do this — see bulk v3 increment 0.*
- **Ctrl-C stops the batch, not the app** — the one local exception to the global Ctrl-C contract, carried on
  `BatchCancelled` with the partial report. Cancel is bounded: SIGTERM, then `kill()` after 5s.
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

## Designed, eng-cleared, NOT yet implemented — bulk v3 ([docs/designs/bulk-v3.md](./designs/bulk-v3.md))

Point EchoGist at a folder, walk it recursively, run the full core. Approved 2026-09-04 after /office-hours
(3 rounds of spec review: 7, 6, 7) and /plan-eng-review (6 findings, 0 critical gaps, CLEAR). **Split into
three increments; the design doc carries the executable spec, the 8 tasks and 4 accepted risks.**

- **Increment 0 (do first — a LIVE bug).** `.resume/<stem>.json` is keyed on the bare source stem with no
  date and no dedup (`menu.py:308`), and `_load_resume:187` never checks which transcript made the partial.
  If file A dies mid-summary, a later same-stem file B resumes A's phases and **B's PAID summary carries A's
  prose and A's timecodes** — the anchor validator passes it, because the timecodes are real, just from the
  wrong recording. Fix: `_run_summary` gains `source_path: Path` (both call sites, `menu.py:521`/`:730`),
  key = `blake2s(str(resolved).casefold())`, sweep `.resume/*.json` not matching `^[0-9a-f]{16}$`.
- **Increment 1 — `scan.py`, read-only, offline, free.** Recursive walk (prunes `output/`, junction-safe
  `seen`), one `ffmpeg -i` per file behind a `(path,size,mtime_ns)` sidecar cache, per-folder rows, upper-bound
  cost projection, duplicate-stem report, unreadable + cloud-placeholder rows. Ships as its own menu entry.
- **Increment 2 — the paid bulk.** Streams extract+transcribe per file, ONE global barrier (the exact
  token-derived gate) before the summarize pool. Build only after the scanner runs on the real folder.

**Eng-review calls, all folded in:** two dataclasses not four (`MediaFile`, `ScanResult`); `_default_runner:80`
gains a **timeout** (today a file on a dead share hangs any multi-file flow forever); scan cache publishes via
`.part`→`os.replace`; **one tree walker** — `batch.expand_selection` delegates at `recursive=False`, its five
existing tests are the regression harness; `menu._human_size`→`ui.human_size` (menu imports scan, so scan
cannot import menu back); transcript detection is a one-pass index, not `O(files × transcripts)`.
**Two CRITICAL regression tests are mandatory:** cross-source summary prose, and the cost estimate biased low.

## Next

1. **Increment 0** (T1): resume key + regression test. ~20 min, independent, fixes the live bug.
2. **Increment 1** (T2-T7 + T8 tests): the scanner. Then run it on the real lecture folder — its file count
   and collision report decide whether increment 2 is worth building at all.
3. **First live run of `check-models.py`** on a box with a key: seeds `config/model_names.json`, confirms the
   real `GET /v1/models` shape. Run one is a pure baseline; the signal starts on run two.
4. **T8 (P3):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. **Debts:** [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md) — **TD-22 open** (a summary has no
  back-link to its source; `save_raw_result:311` names it from the LLM title, so bulk cannot skip already-paid
  work — closes at the start of increment 2). TD-1..21 shut 2026-08-03 (TD-7, TD-20 WONTFIX).
- **Language rule (2026-09-04):** English is the language of the application — console, code, comments,
  commits. Russian only for `README.md`, `docs/USAGE.md`, `CHANGELOG.md` and the generated summaries.
- **SoT:** locked build spec [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates,
  operator-approved) · original SOW [`ТЗ`](./archive/ТЗ_аудио_резюме_приложение.md) · plan
  `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared).
