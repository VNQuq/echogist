# Current Context

**Updated:** 2026-06-26 (**TD-16 v2 "direct transcript synthesis" — T1–T7 shipped on `main`**
(`7ad2185` T1–T4, `1fdd3de` T5-A, `43b7daf` T5-B, this commit T6/T7 docs). Map-reduce + grouping
are DELETED. Only the operator paid reference run remains to close TD-16. v1.1.0 is the last release.)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope — TD-16 v2: direct transcript synthesis

**Principle reversal (operator, eng-reviewed, now in CLAUDE.md):** fidelity > completeness. The
TRANSCRIPT is ground truth, read directly (one hop). A faithful SYNTHESIS is the product; the manual
operator re-check against the recording (+ deterministic anchor validation) is the fidelity gate — no
LLM-judge, no coverage checker, no map-extraction. Supersedes TD-5 (map-reduce) and TD-15 (group-keep-all).

**Pipeline (the only path now):** transcript → deterministic phase-split (`chunk.plan_phases`, computed
K, contiguous, overlap=0; short = K=1) → `summarize.synthesize_summary` ×K sequential forward-only (each
phase reads its span + prior headings + the prior phase's TAIL PROSE in a "do-not-restate" section) →
`_merge_decisions`/`_merge_action_items` (anchor-preserving) → reconcile pass (title/core_idea/
main_themes + contradiction flag, K>1 only) → deterministic `validate_anchors` (accept/snap-2s/drop vs
real block timecodes — the only live fidelity check) → one readable doc (~3–5 pp). ≈5 calls for 3h, 1 for
short. The 5 fidelity properties + "every anchor resolves to a real timecode" are the v2 acceptance bar
(CLAUDE.md).

## Shipped (T1–T7)

- **T1–T4 (`7ad2185`, additive):** synthesis/reconcile config + prompts; `plan_phases` + shared
  `_bin_count`/`_bin_blocks` binning; `SynthesisSection` + `Summary.synthesis`/`main_themes` +
  `Decision`/`ActionItem.anchor`; `emit_phase`/`emit_reconcile`; `build_synthesis_request`/
  `build_reconcile_request`; `synthesize_summary`; `validate_anchors`; synthesis MD/PDF render.
- **T5 commit A (`1fdd3de`):** `summarize_auto` rewired to phase-split + synthesis for ALL material;
  menu cost preview = K (+1 fixed reconcile when K>1) via `cost.estimate_cost_synthesis`; per-phase F6
  oversize guard; **artifact-resume** — each phase persists to `raw/.resume/<stem>.json`, a re-run
  reloads the valid prefix and skips done phases (no job engine).
- **T5 commit B (`43b7daf`, isolated, `git revert`-able — decision #9):** deleted single-pass +
  map-reduce + grouping (functions, `emit_summary`/`emit_synthesis`/`emit_grouping` schemas, retired
  `Summary` fields + `SectionMarker`/`PointGroup`/`SectionGroup`, `chunk.plan_chunks`/`needs_chunking`,
  the `[chunk]` QualityBudget/overlap knobs + models.toml prompts), retired the old structural-quality
  eval (`test_eval.py`/`eval_quality.py`/old goldens) and `regroup.py`. Net ~2.5k LOC removed.
- **T6/T7 (docs, this commit):** CLAUDE.md principle reversal + 5 fidelity properties; TECHNICAL_DEBT
  closes TD-5 + TD-15, opens TD-16.

## Config / behavior notes

- Tool names `emit_phase`/`emit_reconcile`. `{interpretation}` substituted per-language; inline
  `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default model tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings.
- **Output layout:** F13 recovery `.json` → `output/summaries/raw/`; resume partial →
  `output/summaries/raw/.resume/`; readable `.pdf`/`.md` → `output/summaries/`; triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not in WSL — paid
  runs are operator-run on Windows.
- **LLM prompt is data:** edit `config/models.toml`; the tool SCHEMAs stay in `summarize.py`.

## Next

- **Reference acceptance (operator, Windows) — the TD-16 closure gate.** One ~3h-lecture paid run; check
  the 5 fidelity properties by eye, jump each anchor to the recording, confirm ≤5 pp + readable.
- **T8 (P3, TODO):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run
  (replaces the retired structural eval; transcripts in `tests/fixtures/` kept for it). Until it lands,
  the manual paid run is the only quality gate.
- **Parallel synthesis: OUT OF SCOPE — not required** (operator, 2026-06-26). Sequential ships and stays.
- **Cold/clean-deploy (deferred):** `pip install --require-hashes` on a fresh box + cold first run.

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Gate: `bash scripts/dev-loop` (ruff +
mypy --strict + pytest). Current: **350 passed, 0 skipped** (the live-eval tests were retired with the
old eval). `ANTHROPIC_API_KEY` not set here. All network libs lazy/offline so the killswitch holds.
Telemetry off, PROACTIVE false. `/cp` is standing commit+push authorization.

## Open blockers / debts

- **Blockers: none.**
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): **TD-16 v2 (active — T1–T7 shipped; closes
  on operator paid reference acceptance; T8 LLM-judge eval is a separate follow-on)** · TD-7 non-TTY
  fallback UI (LOW) · TD-9 dropped cheap-call Enter beat (LOW).
- **Closed:** TD-1/2/3/4/5/6/8/10/11/12/13/14/15.

## Relevant SoT

- Current plan: `~/.claude/plans/elegant-prancing-journal.md` (TD-16 v2, eng-cleared).
- Build spec (locked): [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) — note TD-16 deviates from
  its single-pass/map-reduce model (operator-approved reversal).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` (incl. synthesis/reconcile) is the only network stage; everything left of it
  is offline + unit-testable against a stub. Phase-split, anchor validation, render are local.
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
