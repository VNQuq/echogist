# Current Context

**Updated:** 2026-09-04 — **bulk v3 increments 0, 1 and 2 shipped; real folder scanned; folder run unrun on real data.**
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
  *The resume file now keys on the resolved source path; transcripts and summary json do NOT yet claim.*
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
  0 unreadable, 0 transcript candidates**, $2.20 upper bound at `economy`. It killed the case for increment 2's
  naming machinery and did NOT calibrate TD-23 (no transcript to compare against — see that entry).
- **Increment 2 ✓ the folder run** (`bulk.py`, menu row 5 `Summarize a folder`; POSITIONAL keys — Settings/Exit
  are now 6/7). **Two phases:** TRANSCRIBE the whole folder (local, free), then ONE exact quote over the real
  transcripts and ONE confirm, then the paid calls. No per-file gate; that is the babysitting it removes.
  TD-23's duration→token constants are off this path; **TD-24 is NOT dissolved by it** — the output side is
  still `output_tokens_estimate`, exactly as the single-file flow prices it.
- **The collision machinery was NOT built.** `bulk.plan_run` instead refuses the one operation that is wrong
  under a collision: reusing a saved transcript whose stem is ambiguous in either direction. Re-transcribing is
  free and visible; summarizing one lecture from another's transcript is paid and silent. The SUMMARY skip has
  no such limit — TD-22 joins it on the resolved source path, not a name.
- **The folder run does not extract MP3s** (TD-12's kept-MP3 baseline is a SINGLE-file rule) and its plan is
  SORTED, not in `os.walk` order, so a 7-lecture course plays back 1..7 and reproduces between runs.
- **Increment 1b (mp3 re-encode, `dec-c3adfe5b`)** — DRAFT only at
  [docs/designs/mp3-reencode-1b.md](./designs/mp3-reencode-1b.md), unreviewed and unimplemented. Two open
  questions plus a MiB/MB unit discrepancy (207 vs 197 kbps at 2.5h). With the real durations known, the
  222 MiB threshold works out to 206 kbps for the shortest lecture and 94 kbps for the longest.
- **Pinned regressions:** cross-source summary prose (inc 0); cost estimate biased low (inc 1); the folder
  gate asked ONCE not per file, and `folder_estimate` summing per-file quotes rather than flattening them into
  one reconcile (inc 2 — flattening under-quotes by `files - 1` calls). 618 tests green.

## Next

1. **Run the folder run on the real lecture folder (Windows).** Everything is shipped and green
   in CI against stubs; it has never touched a real recording or the real API. Start it, let the
   7 transcripts build, then read the ONE quote before confirming. Expect it near $1.40, not the
   scan's $2.20 (that quote projects from duration; this one is computed from the real text).
2. **Calibrate TD-23 from the first transcript** — compare the scan's projected character count
   against `len(transcript_text)` and reseed `words_per_minute`/`chars_per_word` in
   `config/models.toml`. Two numbers, no code change.
3. **Decide TD-24** (soften the "UPPER BOUND" label, or price the scan's output at a real
   ceiling) and **TD-25** (`output/` pruned by name, so a junction leaks). Both triggers fired.
4. **Review the increment 1b draft** at `docs/designs/mp3-reencode-1b.md` — NOT approved, NOT
   implemented, two open questions plus a MiB/MB unit discrepancy.
5. **First live run of `scripts/check-models.py`** on a box with a key. Open from before.
