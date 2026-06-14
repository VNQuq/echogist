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

Next:

- **Run `/plan-eng-review`** with the approved design doc. It MUST resolve, as gating
  decisions: **TD-1** (model distribution — HF blocked in region), **TD-2** (YouTube
  ingestion as a maintained subsystem incl. non-expiring auth), **TD-3** (launcher
  provisioning of cuDNN/cuBLAS + deno + model, cmd/`.bat` only). Operator constraint:
  **no manual workarounds** — these need automated, durable solutions.
- In eng-review also lock: stack, job-state + chunk-checkpoint file format, chunking
  thresholds, PDF-font + model-config impl; seed editable model config (econ/balanced/
  flagship) with the verified IDs/prices above.

## Relevant SoT

- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (stack is
  deliberately UNLOCKED — chosen in `/plan-eng-review`).
- Approved design: `~/.gstack/projects/echogist/pc-main-design-20260614-024256.md`.
- Session checkpoint:
  `~/.gstack/projects/echogist/checkpoints/20260614-030541-echogist-office-hours-design-approved.md`.

## Open blockers

- **TD-1 (HIGH):** Whisper model un-downloadable at runtime — HF region-blocked
  (stalled on sandbox AND user's machine; mirror unreachable). Blocks transcription.
- **TD-2 (HIGH):** YouTube ingestion broken end-to-end (bot-check + JS-runtime +
  EJS solver + fast-expiring cookies; browser-direct cookies dead via DPAPI). Blocks
  the primary input source. Both must be solved in `/plan-eng-review` before code.

## Open debts

- **TD-1** model distribution · **TD-2** YouTube ingestion subsystem · **TD-3**
  launcher provisioning (cuDNN/cuBLAS + deno + model, cmd/`.bat`) · **TD-4** GPU
  transcription speed unmeasured (blocked by TD-1).

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- No private keys in code (API key from `ANTHROPIC_API_KEY` env or local config file only).
- Live LLM calls — forbidden without killswitch (architecture must be testable without
  live API calls; job-state/pure-stage design supports this).
- Every push to `main` must pass CI gates: ruff + mypy + tests.
