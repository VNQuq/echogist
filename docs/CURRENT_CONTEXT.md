# Current Context

**Updated:** 2026-06-14
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**Phase 0 — Product design (no code yet)**

Done:

1. `/office-hours` complete → design APPROVED. Architecture: Approach C (job-state engine
   + thin CLI, chunk-checkpointing in v1). Detail in the design doc + checkpoint (see SoT).
2. **Manual spike complete (2026-06-14, Windows + WSL2, RTX 4060).** Proved on-paper /
   visually: **Cyrillic→PDF** (fpdf2 + embedded DejaVuSans, no tofu); **cost math**
   (Haiku $1/$5, Sonnet $3/$15, Opus $5/$25 per MTok → ~$0.04–$0.20 per 2 h video,
   negligible; maps to econ/balanced/flagship); **GPU env on Windows** (CUDA visible,
   cuDNN/cuBLAS DLLs register via `os.add_dll_directory` before import — no load error).
   Found three hard problems → TD-1/TD-2/TD-3 (see Open debts). Spike scripts:
   `~/echogist-spike/` (throwaway, not in repo).

3. **`/plan-eng-review` complete (2026-06-14) → ENG CLEARED.** Major scope cut +
   architecture locked. See plan: [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md).
   - **Online ingestion DROPPED entirely** (YouTube/Vimeo/URL). TD-2 subsystem killed
     (PO-token cookie-free default falsified by 2026 research; operator rejects account/
     login maintenance + ban risk). Primary+only input = **local files + saved transcript**.
   - **Single-pass summarization only** (no chunked map-reduce / per-chunk checkpointing —
     TD-5). 1.5-2.5h ≈ 40K tokens fits one call in all tiers. Hard overflow guard instead.
   - Architecture = **pure-stage pipeline with artifact-based recovery** (not a persisted
     job engine; no job.json). Saved transcript IS the checkpoint.
   - Stack locked; GUARD/cost are LOCAL (no count_tokens) to keep the killswitch.
   - Spec + README + TECHNICAL_DEBT updated to match. Outside-voice hardening absorbed.

4. **`/plan-devex-review` complete (2026-06-14) → DEVEX CLEARED.** Scoped to **maintainer
   DX** (EchoGist has no third-party dev surface; persona/competitive/magical-moment passes
   skipped as theater). Added **§12** to the plan: (1) WSL-dev→Windows-ship inner loop (one
   `win32` platform shim, WSL-GPU fast loop, batched `win-smoke`); (2) hash-pinned lockfile
   with the ctranslate2↔cuDNN-major tripwire + update protocol; (3) TD-4/T11 measurement
   harness as the model "definition of done" (int8-vs-float16 DoD gate). **Ratified** ffmpeg=
   imageio-ffmpeg + model default=int8_float16; flagged the **float16 (~3 GB > GitHub 2 GB
   asset limit) fallback** distribution. Fixed §8 `count_tokens`→local estimate. New task T13
   (dev/ship loop); T2/T11 sharpened.

Next:

- **Implement v1 per `docs/V1_ENGINEERING_PLAN.md`** (tasks T1..T13). Build order:
  T1 config → T2 launcher/provisioning (+ T13 loop scaffolding) → (T3 transcribe, T4 extract)
  → T5 guard → T6 summarize → T7 render / T8 cost → T9 menu → T10 eval / T11 TD-4 measure.
- First real milestone unblocks **TD-1** (model fetch) and **TD-4** (speed + int8 RU quality).

## Relevant SoT

- **Build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) — stack,
  pipeline, provisioning, edge cases, test matrix, tasks. Authoritative for implementation.
- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (updated: local
  files + saved transcript only; link handling removed).
- Approved design: `~/.gstack/projects/echogist/pc-main-design-20260614-024256.md` (link-source
  + chunking parts SUPERSEDED by the eng plan; premises/first-run-contract still valid).
- Session checkpoint:
  `~/.gstack/projects/echogist/checkpoints/20260614-030541-echogist-office-hours-design-approved.md`.

## Open blockers

- **TD-1 (HIGH→mitigated):** Whisper model un-downloadable at runtime — HF region-blocked.
  Eng-review fix: bundle large-v3 int8_float16 (~1.5 GB) via a **configurable** source URL
  (default GitHub Release) with resume+checksum, plus a **pre-placed local model dir**
  fallback; `HF_HUB_OFFLINE=1`. No HF call at runtime. Validate on first real build (T2).
- **TD-2: CLOSED** (obsolete — online ingestion dropped from scope).

## Open debts

- **TD-1** model distribution (mitigated, validate in T2) · **TD-3** launcher provisioning
  (cuDNN/cuBLAS + model + GPU preflight; **deno dropped**) · **TD-4** GPU speed + int8 RU
  quality unmeasured (T11) · **TD-5** chunked map-reduce deferred (trigger: input trips the
  overflow guard). TD-2 closed.

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- No private keys in code (API key from `ANTHROPIC_API_KEY` env or local config file only).
- Live LLM calls — forbidden without killswitch (architecture must be testable without
  live API calls; job-state/pure-stage design supports this).
- Every push to `main` must pass CI gates: ruff + mypy + tests.
