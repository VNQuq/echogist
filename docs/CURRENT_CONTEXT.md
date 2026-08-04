# Current Context

**Updated:** 2026-08-04 (**ESSENCE BLOCK on `main`, unreleased** — the summary document now opens with a
1-2 page block: главная мысль / главный навык / 3 проверочных вопроса, answers at the very end. Last
release: **v2.1.0** — model-currency automation + the technical-debt registry closed, TD-1..21).
**Authority:** [CLAUDE.md](../CLAUDE.md) · **Max length:** ≤ 2 pages (≈ 60 lines).

---

## Pipeline — TD-16 v2 direct synthesis (released in v2.0.0)

**Principle (in CLAUDE.md):** fidelity > completeness. The transcript is ground truth, read DIRECTLY into a
faithful synthesis (one hop) — no map-extraction, no coverage checklist, no grouping. The manual operator
re-check + deterministic anchor validation is the fidelity gate (no LLM-judge). Supersedes TD-5/TD-15.

**The only path:** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short = K=1) →
`synthesize_summary` ×K sequential forward-only (each phase reads its span + prior headings + prior phase's
TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept/snap-2s/drop vs that phase's own
timecodes; strips inline `[HH:MM:SS]`) → concatenate decisions/actions → reconcile (title + ESSENCE BLOCK +
main_themes + normalized headings — **always**, incl. K=1) → header anchor-validation → one readable doc.

**Validated 2026-06-27** (balanced/Sonnet, 2:58:57 RU lecture, K=4, actual $0.5237) — full evidence in the
TD-16 closed entry: coverage restored, 131/131 anchors resolve, headings cohere, PDF accepted, cost ~1.2×.

## Essence block (operator-requested 2026-08-04, on `main`, NOT yet released)

The document OPENS with «Суть» — **главная мысль** (`core_idea`, ~250-350 words), **главный навык**
(`main_skill`, ~150-200 words, empty when the material teaches none), **3 проверочных вопроса**
(`test_questions`) — and CLOSES with «Ориентиры для ответов». Questions and answers sit at opposite ends by
design: seeing a question must not hand you its answer. Written by the **reconcile** pass from phase prose
only (never a second transcript read), and passed through `validate_anchors` like `core_idea`, so a timecode
copied into the block still resolves to a real block or is dropped.

**Cost change:** reconcile now runs ALWAYS, incl. K=1 (operator decision — skipping the block exactly when
material is short would make the feature silently absent). Short material is **2 cloud calls, not 1**;
`cost.estimate_cost_synthesis` and the menu copy match. Prompt word budgets hold the block to 1-2 pages
(~500 words per rendered PDF page). Gate green: ruff + mypy --strict + **410 tests**. Released through
`v2.1.0` (`712890c`); this sits on top and needs a version bump.

## Config / behavior notes

- Tool names `emit_phase`/`emit_reconcile`. `{interpretation}` substituted per-language; inline
  `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings. **Prompt is data**
  (`config/models.toml`); tool SCHEMAs stay in `summarize.py`.
- **Model currency (2026-08-03):** all tiers pin **floating aliases** (`claude-haiku-4-5`,
  `claude-sonnet-5`, `claude-opus-4-8`) — always-latest, reproducibility intentionally dropped.
  `scripts/check-models.py` (standalone, offline-of-the-pipeline, killswitch-safe like `release.py`)
  reports retired/valid + context drift against `GET /v1/models`, **derives** `context_window`, and
  sets per-tier `prices_unverified` on a generation bump (display_name change vs the
  `config/model_names.json` cache; gitignored). **Prices stay manual** — no pricing endpoint. While a
  tier is `prices_unverified`, `_run_summary` prints a one-time non-blocking notice; the `$0.50` gate
  is unchanged. `balanced` prices verified 2026-08-03 (Sonnet 5 = $3/$15, same as 4.6); flag cleared.
- **Anchors are TEXTUAL references, not links** — `[HH:MM:SS]` woven inline in prose (TD-19) point to a moment
  in the recording/MP3; nothing to click. The validator guarantees each resolves to a real transcript block; a
  manual content spot-check against the recording is optional, not a required gate step.
- **Output:** recovery `.json` → `output/summaries/raw/`; resume partial → `raw/.resume/`; readable `.pdf`/`.md`
  → `output/summaries/`; triplet shares one stem. **Transcripts are gitignored (`output/`), machine-local** — a
  Windows-produced summary can only be re-validated in WSL against ITS transcript, not a stale copy.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not in WSL — paid runs are
  operator-run on Windows.

## Next

1. **Paid validation run of the essence block** (Windows — WSL has no key). Judge the three points against
   the recording: is the навык the one the author actually teaches, are the questions answerable only by
   someone who followed the material, and does the block land inside 1-2 pages? The word budgets in
   `reconcile_system_prompt` are the knob if it over/under-runs. Then cut a release (minor bump → v2.2.0).
2. **First live run of `check-models.py`** on a box with an API key. It seeds `config/model_names.json` and
   confirms the real `GET /v1/models` shape. First run is a pure baseline — the cache is empty, so nothing
   is reported as new and nothing is flagged; the signal starts on run two.
3. **T8 (P3 follow-on):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. v2.1.0 shipped; the essence block is unreleased on `main`.
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): **NONE.** Registry fully closed 2026-08-03 —
  TD-9 (acknowledge beat, Settings flag `auto_accept_under_threshold`, default True) + TD-17 (failure-aware
  progress bar) implemented; TD-7 (non-TTY UI) + TD-20 (PDF polish) WONTFIX. **Closed: TD-1..21.**
- **SoT:** plan `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared); build spec (locked)
  [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates — operator-approved reversal); original
  SOW [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` (synthesis + reconcile) is the only network stage; everything left is offline +
  stub-testable. Phase-split, anchor validation, cost estimate, render are local.
- Every push to `main` passes ruff + mypy + tests. (Development is on `main` directly.)
