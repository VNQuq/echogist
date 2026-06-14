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

**Workflow:** develop directly on `main` (operator decision 2026-06-15 — branch-first
rule dropped). Gate still holds: every push to `main` passes ruff + mypy + tests.

**Done — on `main` (T1+T2 landed at `24524c2`):**

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
- **`requirements.lock` generated** (hash-pinned, §12.2) via `scripts/lock-deps` =
  `uv pip compile --universal` (one pass, no Windows box). 49 reqs, all hashed; verified
  cp311 `win_amd64` + Linux wheels both covered; dry-run-resolves under `--require-hashes`
  for Windows/cp311 + Linux/cp312. Acceptance gate = first Windows cold-run install.

**Next:**

- **Windows cold-run install** of the lock (`run.bat` → `pip install --require-hashes`)
  + §12.3 GPU smoke — the T2 sign-off. Only a real Windows box proves it end-to-end.
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

- **Lock unverified on real Windows** — `requirements.lock` exists and dry-run-resolves for
  Windows/cp311, but the actual `pip install --require-hashes` cold run hasn't been done on a
  Windows box yet (T2 sign-off). uv universal removed the need for a Windows compile pass.
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
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
