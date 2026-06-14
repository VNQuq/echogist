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

Next:

- **Manual spike (real-world, before any code):** on Windows, one real 1.5–2.5h video →
  yt-dlp pull → faster-whisper **GPU** transcribe (confirm GPU, time it) → one Anthropic
  summarize call (check token counts vs cost math) → render a Cyrillic string to PDF
  (confirm not blank boxes). Record where it breaks.
- Run `/plan-eng-review` with the approved design doc → lock stack, job-state +
  chunk-checkpoint file format, chunking thresholds, PDF-font + model-config impl.
- Verify current Anthropic model IDs + per-MTok prices (incl. cache/batch) against the
  Claude API reference; seed editable config (econ / balanced / flagship).

## Relevant SoT

- Spec: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md) (stack is
  deliberately UNLOCKED — chosen in `/plan-eng-review`).
- Approved design: `~/.gstack/projects/echogist/pc-main-design-20260614-024256.md`.
- Session checkpoint:
  `~/.gstack/projects/echogist/checkpoints/20260614-030541-echogist-office-hours-design-approved.md`.

## Open blockers

- None.

## Open debts

- GPU transcription has a first-run asterisk: `faster-whisper` on Windows needs
  cuDNN/CUDA DLLs a `.bat` can't silently install. Resolve in `/plan-eng-review`.

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- No private keys in code (API key from `ANTHROPIC_API_KEY` env or local config file only).
- Live LLM calls — forbidden without killswitch (architecture must be testable without
  live API calls; job-state/pure-stage design supports this).
- Every push to `main` must pass CI gates: ruff + mypy + tests.
