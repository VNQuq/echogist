# Current Context

**Updated:** 2026-06-17 (v1.1 menu/UX overhaul planned: office-hours APPROVED + eng-review CLEARED)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**v1.1 — menu/UX overhaul (planned, not yet built) — [`docs/V1.1_ENGINEERING_PLAN.md`](./V1.1_ENGINEERING_PLAN.md)**

Replace the bare `input()`/numeric menu with **questionary + rich** (arrow-key nav,
styled output, a `%/ETA` transcription bar). UX-layer only — transcribe/summarize/render
logic frozen. Office-hours design APPROVED + `/plan-eng-review` ENG CLEARED (2026-06-17);
9 tasks T1–T9 ready. Load-bearing decision: a `UI` Protocol injected via `Deps` (production
= questionary+rich adapter, tests = scripted `StubUI`) keeps the killswitch CI offline/no-TTY.

**v1 — complete (per [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md))**

Phase 0 done; design APPROVED, ENG + DEVEX CLEARED; scope + architecture locked.
Architecture = pure-stage pipeline, artifact-based recovery, single-pass; GUARD/cost
LOCAL; online ingestion dropped (TD-2 closed).

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds:
every push to `main` passes ruff + mypy + tests.

## Build status — v1 functionally complete

All WSL-side tasks (T1–T11, T13) are code-complete on `main`; the three operator
4060/Windows acceptance runs all passed (2026-06-16). The pipeline runs end-to-end on
the target hardware. Per-task detail lives in git history + the build spec; the headline:

- **Pipeline stages** T1 config · T2 launcher/provisioning · T3 transcribe · T4 extract ·
  T5 guard · T6 summarize (the ONE network stage) · T7 render (PDF/MD) · T8 cost ·
  T9 menu · T10 eval · T11 measure harness · T13 dev/ship loop. Gate: **225 passed,
  2 skipped** (the 2 live tests).
- **Operator runs (2026-06-16):** Run 1 win-smoke closed **TD-3** (caught + fixed a real
  cuBLAS lazy-load bug, 173541d); Run 2 measurement closed **TD-4** (`int8_float16` =
  10.11x realtime, 3.46 GB VRAM on the 4060 → verdict: keep int8_float16,
  `docs/measurements/large-v3-int8_float16-2026-06-16.md`); Run 3 the T10 live gate
  (real Sonnet call, RU+EN clear the golden bar).
- **Default model tier = `economy` (Haiku)** for everyday use (1e45bdf); `balanced`/
  `flagship` selectable in Settings. The T10 live gate stays pinned to `balanced` (Sonnet).
- **Output layout (2026-06-17):** the F13 recovery `.json` now writes to
  `output/summaries/raw/`; `output/summaries/` holds only the readable `.pdf`/`.md`.
  The triplet still shares one stem (render reuses `json_path.stem`).

## Next

- **v1.1 build (T1–T9)** — per [`docs/V1.1_ENGINEERING_PLAN.md`](./V1.1_ENGINEERING_PLAN.md).
  Lanes: T7 deps + T3 theme + T1 transcribe-helper in parallel → T2 ui → T5 cost → T4 menu →
  T8/T9 → T6 test migration. T7 lock-verify + the interactive render are operator/Windows-side.
- **T12 docs** — document final first-run steps post-build (last v1 build task, deferred
  2026-06-16); fold the new arrow-key menu in once v1.1 lands.
- **TD-6 (logged, not actioned)** — summary title can leak the source language on
  cross-language input. LOW; opens when cross-language summarizing becomes normal.

## Dev env

WSL `.venv` (python3.12), GPU stack installed, RTX 4060 visible. Linux loads
cuDNN/cuBLAS via `LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim.
PyPI + GitHub reachable from WSL; huggingface.co is not (no VPN). `ANTHROPIC_API_KEY`
not set here (live gate needs it: `ECHOGIST_LIVE_EVAL=1 .venv/bin/pytest -m live`).
`anthropic` + `fpdf2` installed in `.venv` (match the lock); both lazy-imported so the
killswitch invariant holds. Telemetry off, PROACTIVE false.

## Relevant SoT

- **v1.1 build spec (locked):** [`docs/V1.1_ENGINEERING_PLAN.md`](./V1.1_ENGINEERING_PLAN.md) —
  menu/UX overhaul (UI seam, theme, progress bar, tasks T1..T9).
- **v1 build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) —
  stack/pipeline/provisioning/tasks (T1..T13).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md)
  (local files + saved transcript only).
- Operator testing playbook (RU): [`docs/archive/OPERATOR_TESTING_PLAYBOOK.md`](./archive/OPERATOR_TESTING_PLAYBOOK.md).

## Open blockers

- **None.** TD-1 (the only standing blocker) is closed.

## Open debts

- **TD-5** chunked map-reduce deferred (opens on the first transcript that trips the
  overflow guard) · **TD-6** title can leak source language on cross-language input (LOW,
  logged) · **TD-7** plain-input/non-TTY fallback UI deferred from v1.1 (LOW; opens if a
  non-interactive run is ever needed). **TD-1, TD-2, TD-3, TD-4 closed.**
  → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
