# Current Context

**Updated:** 2026-06-16
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**Phase 1 — Implementation (v1 per [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md))**

Phase 0 done: design APPROVED, ENG + DEVEX CLEARED; scope + architecture locked.
Architecture = pure-stage pipeline, artifact-based recovery, single-pass; GUARD/cost
LOCAL; online ingestion dropped (TD-2 closed).

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds:
every push to `main` passes ruff + mypy + tests.

**Done — on `main` (detail in git history + the build spec):**

- **T1 config** (`config.py` + `models.toml`) — config-as-data tiers/prices, guard
  budget, JSON settings + validation, env-only API key.
- **T2 launcher/provisioning** — idempotent `run.bat`, `gpu.py` (win32 DLL shim + F8
  preflight), `model_asset.py`/`provision.py`, hash-pinned `requirements.lock`,
  cold-Windows verified.
- **TD-1 closed** — HF fetch of `Systran/faster-whisper-large-v3` with
  `HF_HUB_DISABLE_XET` (classic LFS); T3 loads `int8_float16`.
- **T3 transcribe** (`transcribe.py`) — pure `Transcript`/`[HH:MM:SS]` checkpoint +
  lazy GPU adapter; gate (c) PASSED on the 4060.
- **T4 extract** (`extract.py`) — bundled ffmpeg → atomic `.part`→`os.replace` mp3;
  injectable `ffmpeg_exe`/`runner` seams; F11 guard.
- **T5 guard** (`guard.py`) — offline language-aware token estimate biased high
  (Cyrillic 0.60), NO `count_tokens`/network (killswitch AST-tested). T8 reuses it.
- **T6 summarize** (`summarize.py`) — the ONE network stage. Forced tool-use (schema
  in CODE) → `Summary`; prompt TEXT in `[summarize]` (data), `{language}` injected.
  `max_output_tokens` cap separate from the ~2K cost projection; truncation fails
  loud. Lazy `import anthropic` behind injectable `caller`; F2/F4/F5 + bad-key map;
  F10 title fallback; F13 `save_raw_result()` → `summaries/<title>.json` BEFORE render.
- **T7 render** (`render.py` + vendored `assets/fonts/DejaVuSans[-Bold].ttf` +
  `naming.summary_stem`) — `Summary` → PDF (fpdf2, embedded DejaVuSans, Cyrillic
  verified no-tofu) or Markdown; localized RU/EN headings. `load_summary()` rebuilds
  from the `.json` (re-render, never re-pay). `base` = json stem → `.json/.pdf/.md`
  share one name; `summary_stem` sanitizes + truncates (Windows MAX_PATH). `/review`
  (4 subagents) hardened it: catch `FPDFException` (subclasses `Exception`, would've
  crashed); reserved device names (`CON`→`_CON`), trailing dots, control chars stripped
  in `naming`. ruff + mypy + **154 tests**.

**Next:**

- **T8 cost** — pre-call estimate (reuse `guard.estimate_input_tokens` +
  `[guard].output_tokens_estimate` + tier prices) with Enter / y-n threshold friction;
  post-call **actual** from `SummarizeResult` token counts (`response.usage`).
- Then **T9 menu** (loop, per-source action menus, §12 returns) → **T10 eval**
  (RU+EN summarization quality — the prompt eval gate) / **T11 measure** (TD-4).
- **No live-API smoke yet** (killswitch CI only) — first real summarize call is the
  operator run / T10 prompt eval; that eval is the gate, not a unit test.

## Dev env

WSL `.venv` (python3.12), GPU stack installed, RTX 4060 visible. Linux loads
cuDNN/cuBLAS via `LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim.
PyPI + GitHub reachable from WSL; huggingface.co is not (no VPN). `ANTHROPIC_API_KEY`
not set here. Telemetry off, PROACTIVE false. `fpdf2` now installed in `.venv` (matches
the lock) so the PDF render path runs for real locally.

## Relevant SoT

- **Build spec (locked):** [`docs/V1_ENGINEERING_PLAN.md`](./V1_ENGINEERING_PLAN.md) —
  stack/pipeline/provisioning/tasks (T1..T13).
- Original ТЗ: [`ТЗ_аудио_резюме_приложение.md`](../ТЗ_аудио_резюме_приложение.md)
  (local files + saved transcript only).

## Open blockers

- **None.** TD-1 (the only standing blocker) is closed.

## Open debts

- **TD-3** GPU preflight pending the live Windows run (WSL T3 exercised cuDNN/cuBLAS
  green) · **TD-4** GPU speed + int8 RU quality unmeasured (T11) · **TD-5** chunked
  map-reduce deferred. **TD-1, TD-2 closed.** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
