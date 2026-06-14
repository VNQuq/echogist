# Current Context

**Updated:** 2026-06-15
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**Phase 1 — Implementation (v1 per [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md))**

Phase 0 complete: design APPROVED (`/office-hours`), ENG CLEARED (`/plan-eng-review`),
DEVEX CLEARED (`/plan-devex-review`); scope + architecture locked. Detail in the plan
+ checkpoints (see SoT). Architecture = pure-stage pipeline, artifact-based recovery;
single-pass; GUARD/cost LOCAL; online ingestion dropped (TD-2 closed).

**Done — branch `feat/t1-config`, commit `5e1c868`, pushed to `origin` (PR not opened):**

- **T1 — config.** `echogist/config.py` + `config/models.toml` (config-as-data: tiers/
  IDs/prices, overflow-guard budget, configurable model-asset source URL). JSON settings
  + validation; F5 deprecated/unknown-tier guidance; env-only API key read.
- **T2 — launcher/provisioning.** `run.bat` (cmd-only, idempotent: py3.11 check → venv →
  `--require-hashes` install → provision → app). `echogist/gpu.py` = the one `win32` DLL
  shim + GPU preflight with F8 diagnostics. `model_asset.py` = TD-1 resumable fetch +
  checksum + pre-placed-dir fallback (F14), BadZipFile/corrupt-`.part` hardened.
  `provision.py` orchestrator. `requirements.in` + `scripts/lock-deps`; `scripts/dev-loop`
  + `win-smoke.bat` (T13 scaffolding).
- `/review` run: 2 fail-loud bugs auto-fixed. Gates green: **ruff + mypy + 50 tests**.
- Dev env: WSL `.venv` on **python3.12** (3.11+; has `tomllib`). RTX 4060 visible in WSL.

**Next:**

- **Generate `requirements.lock`** (hash-pinned, §12.2) — the one blocker for `run.bat`
  on Windows. Needs `pip-compile` + cross-platform reconcile (WSL + Windows wheels).
- **T3 transcribe / T4 extract** — install `ctranslate2`/`faster-whisper`/`imageio-ffmpeg`
  into the WSL `.venv`. T3 unblocks TD-1 (first real model fetch) + TD-4 (speed/int8 RU
  quality, measured in T11).
- Then T5 guard → T6 summarize → T7 render / T8 cost → T9 menu → T10 eval / T11 measure.

## Relevant SoT

- **Build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) —
  authoritative for stack/pipeline/provisioning/tasks (T1..T13).
- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (local files +
  saved transcript only).
- Approved design: `~/.gstack/projects/echogist/pc-main-design-20260614-024256.md`
  (link-source + chunking parts SUPERSEDED by the eng plan; premises still valid).

## Open blockers

- **`requirements.lock` not generated** — `run.bat` cannot `pip install` on Windows until
  it exists (errors clearly pointing at `scripts/lock-deps`). Cross-platform reconcile needed.
- **TD-1 (mitigated):** model fetch via configurable source URL + pre-placed-dir fallback is
  coded in T2; validate on the first real fetch in T3 and on a cold Windows run.

## Open debts

- **TD-1** model distribution (coded, validate at T3/cold-Windows) · **TD-3** provisioning
  (run.bat + GPU preflight landed in T2; cold-Windows run still pending) · **TD-4** GPU speed
  + int8 RU quality unmeasured (T11) · **TD-5** chunked map-reduce deferred. TD-2 closed.

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code, never committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a pipeline stage.)
- Every push to `main` must pass: ruff + mypy + tests. Branch before committing code to main.
