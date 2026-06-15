# Current Context

**Updated:** 2026-06-15
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**Phase 1 — Implementation (v1 per [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md))**

Phase 0 done: design APPROVED, ENG + DEVEX CLEARED; scope + architecture locked.
Architecture = pure-stage pipeline, artifact-based recovery; single-pass; GUARD/cost
LOCAL; online ingestion dropped (TD-2 closed).

**Workflow:** develop directly on `main` (operator decision 2026-06-15 — branch-first
rule dropped). Gate holds: every push to `main` passes ruff + mypy + tests.

**Done — on `main`:**

- **T1 — config** (`echogist/config.py` + `config/models.toml`): config-as-data tiers/
  prices, overflow-guard budget; JSON settings + validation; env-only API key.
- **T2 — launcher/provisioning**: `run.bat` (idempotent: py3.11 → venv →
  `--require-hashes` install → provision → app), `gpu.py` (`win32` DLL shim + F8
  preflight), `model_asset.py` (HF fetch + pre-placed-dir fallback), `provision.py`.
  `requirements.lock` hash-pinned, **cold-Windows verified**.
- **TD-1 closed (Xet root cause, not region)**: HF fetch of vanilla
  `Systran/faster-whisper-large-v3` with `HF_HUB_DISABLE_XET` forcing classic LFS;
  T3 loads `int8_float16`. Full saga in git + TECHNICAL_DEBT.
- **T3 — transcribe** (`echogist/transcribe.py`, commit `2748196`): pure half
  (`Segment`/`Transcript`, `format_timecode`, `render_transcript` → `[HH:MM:SS] text`
  checkpoint, `save_transcript` → `output/transcripts/<date>-<stem>.txt` with `-2/-3`
  dedup) + lazy-import GPU adapter `transcribe()` (`int8_float16`, autolang,
  segment-progress, fail-loud `TranscribeError`). **Gate (c) PASSED on the 4060** (WSL,
  model via `/mnt/c`): autolang `en`, verbatim timecoded JFK segment.
- **T4 — extract** (`echogist/extract.py` + `echogist/naming.py`, uncommitted): F9 naming
  promoted into shared `naming.py` (transcribe now routes through it). `extract_audio()`
  runs the bundled ffmpeg (`-vn -acodec libmp3lame -q:a 2 -f mp3`) → **atomic** `.part`→
  `os.replace` into `output/audio/<date>-<stem>.mp3`, deduped; injectable `ffmpeg_exe`+
  `runner` seams; `is_mp3()` predicate; F11 binary guard; fail-loud `ExtractError`.
  Multi-agent review applied (3 agents): utf-8 stderr decode (Windows crash), atomic
  partial-mp3 cleanup, `-nostdin` hang guard, + 5 new T3/T4 failure-path tests (model-load
  F8, mid-stream, no-speech, int8_float16 default lock). Real-ffmpeg smoke green (cyrillic+`:`
  mkv→mono mp3, no video, no `.part`). ruff + mypy + **90 tests** (was 63).

**Next:**

- **T5 — guard**: local language-aware token estimate vs `safe_budget(model)`; clean
  overflow stop (§4); NO `count_tokens` (killswitch). Reads `Transcript.text`.
- Then T6 summarize → T7 render / T8 cost → T9 menu → T10 eval / T11 measure.
- **T7 naming note (from review):** summaries are `<meaningful-title>` (NO date prefix),
  need title truncation + base-dedup across `.pdf`/`.md`/`.json` together. Use
  `naming.sanitize_stem`/`dedup_path` primitives directly — `dated_artifact_path` is
  for the dated audio/transcript artifacts only, not T7. Add the two helpers then (YAGNI now).
- **TD-4 (T11)**: realtime_factor + int8-vs-float16 RU quality via committed
  `scripts/measure_model.py` (now unblocked — model access solved).

## Dev env

WSL `.venv` (python3.12) with the GPU stack installed; RTX 4060 visible. Linux loads
cuDNN/cuBLAS via `LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the `win32`
`add_dll_directory` shim. PyPI + github reachable from WSL; huggingface.co is not (no
VPN there). Telemetry off, PROACTIVE false.

## Relevant SoT

- **Build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) —
  authoritative for stack/pipeline/provisioning/tasks (T1..T13).
- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (local files +
  saved transcript only).

## Open blockers

- **None.** TD-1 (the only standing blocker) is closed.

## Open debts

- **TD-3** provisioning (lock-install proven on cold Windows; GPU preflight still pending
  the live Windows run — though the WSL T3 run exercised cuDNN/cuBLAS load green) ·
  **TD-4** GPU speed + int8 RU quality unmeasured (T11) · **TD-5** chunked map-reduce
  deferred. **TD-1, TD-2 closed.** Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
