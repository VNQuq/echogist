# Current Context

**Updated:** 2026-06-27 (**TD-16 v2 code-complete + multi-agent `/review` done; Tier-1 review fixes
applied, gate green**). Next: commit → operator paid reference run (TD-16 closure gate) → Tier-2 cleanup →
**v2.0.0** (breaking; v1.1.0 was last release).
**Authority:** [CLAUDE.md](../CLAUDE.md) · **Max length:** ≤ 2 pages (≈ 60 lines).

---

## Active scope — TD-16 v2: direct transcript synthesis

**Principle (operator, eng-reviewed, in CLAUDE.md):** fidelity > completeness. The transcript is ground
truth, read DIRECTLY into a faithful synthesis (one hop) — no map-extraction, no coverage checklist, no
grouping. The manual operator re-check against the recording + deterministic anchor validation is the
fidelity gate (no LLM-judge). Supersedes TD-5 (map-reduce) and TD-15 (group-keep-all), both closed + deleted.

**Pipeline (the only path):** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short =
K=1) → `summarize.synthesize_summary` ×K sequential forward-only (each phase reads its span + prior headings
+ prior phase's TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept/snap-2s/drop vs
that phase's real timecodes; strips inline `[HH:MM:SS]` too) → concatenate decisions/actions → reconcile
(title/core_idea/main_themes + contradiction flag, K>1 only) → header anchor-validation → one readable doc
(~3–5 pp). ≈5 calls/3h, 1 for short. The 5 fidelity properties + "every anchor resolves to a real timecode"
are the v2 acceptance bar (CLAUDE.md).

## Status

- **T1–T7 shipped on `main`:** `7ad2185` (T1–T4 config/prompts/`plan_phases`/`synthesize_summary`/
  `validate_anchors`/render), `1fdd3de` (T5-A: `summarize_auto` rewired + cost preview + artifact-resume),
  `43b7daf` (T5-B isolated/git-revertable: deleted single-pass+map-reduce+grouping + old eval, ~2.5k LOC),
  `2b8ab6f` (T6/T7 docs).
- **Multi-agent `/review` (2026-06-27):** 6 passes (testing/maintainability/security/performance/red-team +
  Claude adversarial; Codex not installed). **Tier-1 fixed (uncommitted, gate green):** dropped the
  cross-phase decision/action merge (it could collapse distinct same-worded points → fidelity #4); per-phase
  anchor validation (was global — a coincidental cross-phase match passed); inline `[HH:MM:SS]` in prose +
  reconcile header now validated; resume accepts `len==K` (a reconcile-crash no longer re-pays all K phases);
  MD title whitespace-collapsed. **Tier-2 deferred** → TECHNICAL_DEBT (TD-16 Remaining).

## Config / behavior notes

- Tool names `emit_phase`/`emit_reconcile`. `{interpretation}` substituted per-language; inline
  `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings.
- **Output:** recovery `.json` → `output/summaries/raw/`; resume partial → `raw/.resume/`; readable
  `.pdf`/`.md` → `output/summaries/`; triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not in WSL — paid runs
  are operator-run on Windows.
- **Prompt is data** (`config/models.toml`); tool SCHEMAs stay in `summarize.py`.

## Next

1. Commit the Tier-1 review fixes (`/cp`).
2. **Paid reference acceptance (operator, Windows) — the TD-16 closure gate:** one ~3h-lecture run; check the
   5 fidelity properties by eye, jump each anchor to the recording, confirm ≤5 pp + readable.
3. **Tier-2 review cleanup before v2.0.0** (TD-16 Remaining): dead code, stale docstrings, K=1 duplicate
   heading, per-language `{unassigned}` label.
4. **v2.0.0 release** (`/ship`; breaking — Summary shape changed). **T8** offline LLM-judge eval = P3 follow-on.
   **Parallel synthesis = OUT OF SCOPE** (operator, 2026-06-26).

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Gate: `bash scripts/dev-loop` (ruff + mypy
--strict + pytest) — **350 passed, 0 skipped**. `ANTHROPIC_API_KEY` not set here; network libs lazy/offline
so the killswitch holds. Telemetry off, PROACTIVE false. `/cp` is standing commit+push authorization.

## Blockers / debts / SoT

- **Blockers: none.**
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): **TD-16** (active; closes on paid reference
  acceptance; Tier-2 cleanup + T8 eval are in its orbit) · TD-7 non-TTY UI (LOW) · TD-9 cheap-call Enter beat
  (LOW). **Closed:** TD-1..6, 8, 10..15.
- **SoT:** plan `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared); build spec (locked)
  [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates — operator-approved reversal); original
  SOW [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` (synthesis + reconcile) is the only network stage; everything left is offline +
  stub-testable. Phase-split, anchor validation, render are local.
- Every push to `main` passes ruff + mypy + tests. (Development is on `main` directly.)
