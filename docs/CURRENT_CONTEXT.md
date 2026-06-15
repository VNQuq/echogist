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
  shim + GPU preflight with F8 diagnostics. `model_asset.py` = TD-1 HF fetch
  (`snapshot_download`, Xet disabled) + pre-placed-dir fallback (F14).
  `provision.py` orchestrator. `requirements.in` + `scripts/lock-deps`; `scripts/dev-loop`
  + `win-smoke.bat` (T13 scaffolding).
- `/review` run: 2 fail-loud bugs auto-fixed. Gates green: **ruff + mypy + 50 tests**.
- Dev env: WSL `.venv` on **python3.12** (3.11+; has `tomllib`). RTX 4060 visible in WSL.
- **`requirements.lock` generated + Windows-verified** (hash-pinned, §12.2) via
  `scripts/lock-deps` = `uv pip compile --universal`. 49 reqs, all hashed; **a cold
  Windows `run.bat` installed it cleanly under `--require-hashes`** (Python 3.11.9) — the
  lock blocker is closed.
- **`run.bat` humane exit** — pauses only when double-clicked (not when `call`ed/automated).
- **TD-1 reworked → HF auto-fetch** (operator 2026-06-15): provisioning pulls vanilla
  `Systran/faster-whisper-large-v3` (float16) from HF via `snapshot_download`; T3 loads
  `compute_type=int8_float16`. Decision reverses the original "HF region-blocked" TD-1
  finding — the cold run proved the real cause was Xet (see below), not the region.

- **TD-1 root cause found + fixed (2026-06-15).** The download stall was **Xet, not
  region**. hf_hub 1.x auto-uses `hf-xet` (in the lock) → routes `model.bin` through
  the Xet CAS hosts, which stall on the operator's route; classic `huggingface.co`
  works. Proven: browser (over system VPN) downloaded fine; WSL (no VPN) timed out on
  the Xet hosts; with `HF_HUB_DISABLE_XET=1` `snapshot_download` pulled `model.bin` at
  ~10.5 MB/s, no stall. Fix landed: `fetch_from_hf` sets `HF_HUB_DISABLE_XET` (via
  `setdefault`, overridable) before importing huggingface_hub. Gate step (a) HF download
  **PASSED**. Then refactor: the dormant zip-from-URL self-host path was **removed**
  (`download_resumable`/`verify_checksum`/`_extract_zip` + `source_url`/`sha256` config);
  `model_asset.py` 193→108 lines. Pre-placed `local_dir` escape hatch stays as the sole
  fallback. ruff + mypy + **48 tests** green. Gate (a) HF download **PASSED**; gate (b)
  `int8_float16` load on the 4060 **PASSED** (`WhisperModel(...)` → `ok`, no DLL error).
- **setuptools skew fixed → then root-caused away (dep hygiene, `0b350bb`).** The gate-b
  landmine was `ctranslate2` 4.5 importing `pkg_resources` (setuptools 81 removed it).
  First patched with `setuptools<81`; then resolved properly by bumping `ctranslate2`
  **4.5→4.8** (4.8 switched to `importlib.resources`, no `pkg_resources`), so the shim is
  **gone** and setuptools rides latest (82.0.1). cuDNN major unchanged (still 9 per
  CHANGELOG 4.5→4.8); existing `cudnn==9.*`/`cublas==12.*` pins already covered it. The
  cuDNN-ABI tripwire was honored: cold `run.bat` install under `--require-hashes` +
  int8_float16 GPU smoke **both re-verified on the 4060** before the bump landed on `main`.
  All other deps were already latest. ruff + mypy + 48 tests green.

- **T3 — transcribe (DONE, closes TD-1).** `echogist/transcribe.py`: pure half
  (`Segment`/`Transcript`, `format_timecode` → `HH:MM:SS`, `render_transcript` →
  `[HH:MM:SS] text` lines, `save_transcript` → `output/transcripts/<date>-<stem>.txt`
  with `-2/-3` dedup) + a lazy-import GPU adapter `transcribe()` (registers DLLs before
  `import faster_whisper`, `compute_type=int8_float16`, autolang, segment-progress log,
  fail-loud `TranscribeError`). 13 new unit tests (pure half + missing-file guard).
  **Gate (c) PASSED end-to-end on the 4060** (WSL, model via `/mnt/c`): large-v3
  `int8_float16`, autolang `en`, verbatim timecoded segment on the JFK sample. GPU stack
  (`faster-whisper 1.2.1`/`ctranslate2 4.8`/`imageio-ffmpeg 0.6`/cudnn9/cublas12)
  installed in the WSL `.venv`. Linux needs the nvidia wheel `lib` dirs on
  `LD_LIBRARY_PATH` (handled in `scripts/dev-loop`; Windows uses the `win32`
  `add_dll_directory` shim). ruff + mypy + **61 tests** green. **Not yet committed.**

**Next:**

- **T4 — extract** — `imageio-ffmpeg` video→mp3 / non-mp3 audio→mp3, dedup naming
  (F9/F11; the `_dedup_path`/`_sanitize_stem` helpers in `transcribe.py` are the seam
  to share or mirror). Verify: video + audio inputs.
- Then T5 guard → T6 summarize → T7 render / T8 cost → T9 menu → T10 eval / T11 measure.
- **TD-4 (T11):** realtime_factor + int8-vs-float16 RU quality via the committed
  `scripts/measure_model.py` harness. Now unblocked (model access solved).

## Relevant SoT

- **Build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) —
  authoritative for stack/pipeline/provisioning/tasks (T1..T13).
- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (local files +
  saved transcript only).
- Approved design: `~/.gstack/projects/echogist/pc-main-design-20260614-024256.md`
  (link-source + chunking parts SUPERSEDED by the eng plan; premises still valid).

## Open blockers

- **None on model download.** TD-1's "HF unreachable from RU" blocker is **resolved** —
  root cause was the Xet transport, fixed via `HF_HUB_DISABLE_XET`; the HF classic path
  works from the operator's box (gate a green). Remaining TD-1 work (gate b/c) is normal
  forward progress, not a blocker.

## Open debts

- **TD-3** provisioning (run.bat lock-install proven on cold Windows; GPU preflight
  still pending the live Windows run — though the WSL T3 run exercised the cuDNN/cuBLAS
  load path green) · **TD-4** GPU speed + int8 RU quality unmeasured, now unblocked,
  measured at T11 via `scripts/measure_model.py` · **TD-5** chunked map-reduce deferred.
  **TD-1 closed** (T3 gate c) · TD-2 closed.

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code, never committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a pipeline stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
