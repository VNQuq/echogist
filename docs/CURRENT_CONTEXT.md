# Current Context

**Updated:** 2026-06-27 (**v2.0.0 RELEASED** — tag `v2.0.0` cut via `scripts/release.py`). The TD-16
coverage fix + TD-18/19/21 follow-ons were validated on the paid re-run; cost recalibrated (`230deee`);
usage docs (README + USAGE) rewritten and de-personalised for v2. **Authority:** [CLAUDE.md](../CLAUDE.md)
· **Max length:** ≤ 2 pages (≈ 60 lines).

---

## Active scope — TD-16 v2: direct transcript synthesis (DONE, pending release)

**Principle (operator, eng-reviewed, in CLAUDE.md):** fidelity > completeness. The transcript is ground truth,
read DIRECTLY into a faithful synthesis (one hop) — no map-extraction, no coverage checklist, no grouping. The
manual operator re-check against the recording + deterministic anchor validation is the fidelity gate (no
LLM-judge). Supersedes TD-5 (map-reduce) and TD-15 (group-keep-all), both closed + deleted.

**Pipeline (the only path):** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short = K=1)
→ `summarize.synthesize_summary` ×K sequential forward-only (each phase reads its span + prior headings + prior
phase's TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept/snap-2s/drop vs that
phase's real timecodes; strips inline `[HH:MM:SS]`) → concatenate decisions/actions → reconcile (title/
core_idea/main_themes + normalized phase headings, K>1 only) → header anchor-validation → one readable doc.

## Status — RELEASE-READY

- **Code complete on `main`**, gate green (`bash scripts/dev-loop`: ruff + mypy --strict + 360 tests). Last
  commits: `5d0df0c` (TD-18/19/20/21), `ba1b973` (TD-16 coverage fix), `0df39b7` (docs), `230deee` (TD-21 cost).
- **Paid re-run VALIDATED 2026-06-27** (balanced/Sonnet, transcript `output/transcripts/2026-06-27-Лекция 3
  01.06.26-2.txt`, 2:58:57, K=4, actual $0.5237) — full evidence in the TD-16 closed entry. Every gate item
  passed: coverage restored (phase 2 covers 00:44→01:31); anchors 131/131 + 9/9 + 131/131 resolve (log "0
  dropped", re-verified offline); headings cohere (TD-18); PDF operator-accepted (TD-20); cost recalibrated to
  ~1.2× (TD-21, `output_tokens_estimate` 2800→4600); fidelity spot-check clean (12/12 grounded, no fabrication).
  "the author-named point" absent only because Whisper garbled the surname upstream (concept covered).

## Config / behavior notes

- Tool names `emit_phase`/`emit_reconcile`. `{interpretation}` substituted per-language; inline
  `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings. **Prompt is data**
  (`config/models.toml`); tool SCHEMAs stay in `summarize.py`.
- **Anchors are TEXTUAL references, not links** — `[HH:MM:SS]` woven inline in prose (TD-19) point to a moment
  in the recording/MP3; nothing to click. The validator guarantees each resolves to a real transcript block; a
  manual content spot-check against the recording is optional, not a required gate step.
- **Output:** recovery `.json` → `output/summaries/raw/`; resume partial → `raw/.resume/`; readable `.pdf`/`.md`
  → `output/summaries/`; triplet shares one stem. **Transcripts are gitignored (`output/`), machine-local** — a
  Windows-produced summary can only be re-validated in WSL against ITS transcript, not a stale copy.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not in WSL — paid runs are
  operator-run on Windows.

## Next

1. **T8 (P3 follow-on):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. v2.0.0 shipped.
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): TD-20 (PDF iteration, LOW, operator-accepted as-is)
  · TD-7 non-TTY UI (LOW) · TD-9 cheap-call Enter beat (LOW) · TD-17 progress bar 100% on failed stage (LOW).
  **Closed:** TD-1..6, 8, 10..16, 18, 19, 21.
- **SoT:** plan `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared); build spec (locked)
  [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates — operator-approved reversal); original
  SOW [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` (synthesis + reconcile) is the only network stage; everything left is offline +
  stub-testable. Phase-split, anchor validation, cost estimate, render are local.
- Every push to `main` passes ruff + mypy + tests. (Development is on `main` directly.)
