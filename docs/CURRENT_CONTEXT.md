# Current Context

**Updated:** 2026-06-26 (**TD-16 v2 "direct transcript synthesis" is the active build.** Plan
locked + eng-reviewed (`~/.claude/plans/elegant-prancing-journal.md`); **T1–T4 shipped additively**
on `main` (`7ad2185`). Next: **T5 menu wire-in + the isolated map-reduce deletion commit.**
v1.1.0 remains the last release.)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope — TD-16 v2: direct transcript synthesis

**Principle reversal (operator, eng-reviewed):** fidelity > completeness. The TRANSCRIPT is ground
truth, read directly (one hop). A faithful SYNTHESIS is the product; the manual operator re-check
against the recording is the fidelity gate — no LLM-judge, no coverage checker, no map-extraction.
This supersedes the TD-5 "single-pass-below / map-reduce-above" rule and TD-15 "group, keep-all".

**Pipeline:** transcript → deterministic phase-split (`chunk.plan_phases`, computed K, contiguous,
overlap=0; short = K=1) → `summarize.synthesize_summary` ×K sequential forward-only (each phase reads
its span + prior headings + the prior phase's TAIL PROSE in a "do-not-restate" section) → reuse
`_merge_decisions`/`_merge_action_items` (anchor-preserving) → reconcile pass (title/core_idea/
main_themes + contradiction flag, K>1 only) → deterministic `validate_anchors` (accept/snap/drop vs
real block timecodes — the only live fidelity check) → one readable doc (~3–5 pp). ≈5 calls for 3h,
≈1 for short. Cheaper than map-reduce.

**Shipped T1–T4 (`7ad2185`, additive — old paths still live until T5):**
- **T1** `config/models.toml` + `config.py`: `synthesis_system_prompt` + `reconcile_system_prompt`
  ({language}/{interpretation} tokens), defaulted + loader-wired.
- **T2** `chunk.py`: shared `_bin_count`/`_bin_blocks` binning core; `plan_chunks` refactored onto it
  (behavior identical); new standalone `Phase` + `plan_phases` (overlap=0); `block_timecodes`;
  `[chunk] phase_target_tokens = 24000` (~4 phases for the validated 84k-token 3h lecture).
- **T3** `summarize.py`: `SynthesisSection`; `Summary.synthesis`/`main_themes`; `Decision`/`ActionItem`
  `.anchor`; `emit_phase`/`emit_reconcile` schemas; `_interpretation_label`
  (`интерпретация`/`interpretation`); `build_synthesis_request`/`build_reconcile_request`;
  `synthesize_summary` (max_tokens fail-loud per phase, `on_phase` resume seam); `validate_anchors`.
- **T4** `render.py`: `_markdown_synthesis` + `_pdf_synthesis_body` (core idea → phases w/ anchor line
  → main themes → anchored decisions/actions); flat/grouped fallback kept; `load_summary` round-trips
  the new fields; FPDFException catch retained.
- **Review fix (`/review` adversarial, P1):** `_merge_*` back-fill rebuilt the dataclass without
  `anchor`, silently zeroing it before `validate_anchors`; fixed + regression tests.

## Config / behavior notes

- New tool names `emit_phase`/`emit_reconcile` are distinct from the soon-deleted
  `emit_summary`/`emit_synthesis`/`emit_grouping` — no collision during the T5 transition.
- `{interpretation}` is substituted exactly like `{unassigned}` (per-language label dict in
  `summarize.py`); the inline `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default model tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings.
- **Output layout:** F13 recovery `.json` → `output/summaries/raw/`; readable `.pdf`/`.md` →
  `output/summaries/`; triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not set in WSL —
  paid reference runs are operator-run on Windows.
- **LLM prompt is data:** edit `config/models.toml`; the tool SCHEMAs stay in `summarize.py`.

## Next

- **T5 (P2) menu wire-in + isolated deletion.** Wire `summarize_auto` (all tiers) to phase-split +
  synthesis; cost preview = K (+1 fixed reconcile allowance when K>1); artifact-resume via `on_phase`
  (skip phases already on disk). Then the SEPARATE, git-revertable commit deleting map-reduce +
  grouping + the retired single-pass `emit_summary` (decision #1/#9: revert if reference-run
  acceptance fails). Update `cost.py` cost copy.
- **T6 (P2)** `CLAUDE.md` + `TECHNICAL_DEBT`: record the principle reversal + the 5 fidelity
  properties + "every anchor resolves to a real timecode"; close TD-15 keep-all, open/close TD-16.
- **T7–T9 (P3, TODO):** offline LLM-judge eval; parallel synthesis (ThreadPool); confirm map retired.
- **Reference acceptance (operator, Windows):** one 3h-lecture paid run; check the 5 fidelity
  properties by eye, jump each anchor to the recording, confirm ≤5 pp.
- **T7 cold/clean-deploy (deferred):** `pip install --require-hashes` on a fresh box + cold first run.

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Gate: `bash scripts/dev-loop` (ruff +
mypy --strict + pytest). Current: **419 passed, 2 skipped** (the 2 live tests). `ANTHROPIC_API_KEY`
not set here. All network libs lazy/offline so the killswitch holds. Telemetry off, PROACTIVE false.
`/cp` is standing commit+push authorization.

## Open blockers / debts

- **Blockers: none.**
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): **TD-16 v2 (active — T1–T4 shipped, T5
  next)** supersedes TD-15 (readability) and the TD-5 map-reduce direction · TD-7 non-TTY fallback UI
  (LOW) · TD-9 dropped cheap-call Enter beat (LOW).
- **Closed:** TD-1/2/3/4/6/8/10/11/12/13/14.

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
