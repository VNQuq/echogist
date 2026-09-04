# Current Context

**Updated:** 2026-09-04 — **bulk v3 increments 0 and 1 shipped; the scanner is live and unrun on real data.**
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

## bulk v3 — increments 0 and 1 SHIPPED ([docs/designs/bulk-v3.md](./designs/bulk-v3.md))

Approved 2026-09-04 after /office-hours (3 rounds: 7, 6, 7) and /plan-eng-review (CLEAR). The design doc carries
the spec; only what the code cannot tell you lives here.

- **Increment 0 ✓** resume keyed on `blake2s(resolved source path, casefolded)`, old stem-keyed partials swept by
  shape. **Increment 1 ✓** `scan.py` + menu row 4 `Scan a folder` (POSITIONAL keys — Settings/Exit renumbered).
- **Casefold is asymmetric ON PURPOSE.** `menu._resume_key` folds case (one file the operator picked twice, on
  Windows). `scan._resolve` does NOT (a dedup key across a tree — folding made `Lecture.mp4` and `lecture.mp4`
  one entry and dropped a real recording silently). Same word, opposite job; do not "unify" them.
- **Ctrl-C MERGES the scan cache, a completed run REPLACES it.** A cancel must never delete entries it had not
  reached; a complete run rewriting from live results IS the eviction policy. Consequence, accepted: the cache
  holds ONE scan root, so alternating folders re-probes.
- **Increment 2 (the paid bulk) is NOT started and is GATED** on running the scanner against the real lecture
  folder. Its file count and collision report decide whether the paid bulk is worth building at all.
- **Increment 1b (mp3 re-encode rule, `dec-c3adfe5b`)** has a DRAFT spec at
  [docs/designs/mp3-reencode-1b.md](./designs/mp3-reencode-1b.md) — written unattended, NOT reviewed, NOT
  implemented. Two open questions in it need the operator: whether `target_kbps` is stored or derived from
  `lame_quality`, and whether the 222 MiB clause still earns its keep next to the bitrate clause. It also
  records a unit discrepancy: the threshold is specified in MiB but its justifying figure was computed in
  decimal MB (207 vs 197 kbps at 2.5h).
- **Both CRITICAL regressions are pinned:** cross-source summary prose (inc 0), cost estimate biased low (inc 1 —
  the prompt overhead must land K times, not once). 554 tests green.

## Next

1. **Run the scanner on the real lecture folder (Windows).** The one gate on everything downstream: its file
   count, hours, dollar figure and collision report decide whether increment 2 is worth building, and its first
   run against a folder that already has a transcript calibrates TD-23's two unmeasured constants.
2. **First live run of `check-models.py`** on a box with a key: seeds `config/model_names.json`, confirms the
   real `GET /v1/models` shape. Run one is a pure baseline; the signal starts on run two.
3. **T8 (P3):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. **Debts:** [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md) — **TD-22, TD-23 open** (a summary has no
  back-link to its source; `save_raw_result:311` names it from the LLM title, so bulk cannot skip already-paid
  work — closes at the start of increment 2; **TD-23** the scan projection constants are unmeasured, calibrated by the
  first real scan). TD-1..21 shut 2026-08-03 (TD-7, TD-20 WONTFIX).
- **Language rule (2026-09-04):** English is the language of the application — console, code, comments,
  commits. Russian only for `README.md`, `docs/USAGE.md`, `CHANGELOG.md` and the generated summaries.
- **SoT:** locked build spec [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates,
  operator-approved) · original SOW [`ТЗ`](./archive/ТЗ_аудио_резюме_приложение.md) · plan
  `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared).
