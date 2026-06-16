# Current Context

**Updated:** 2026-06-16 (operator runs complete — TD-3 + TD-4 closed, live gate green)
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
  in `naming`.
- **T8 cost** (`cost.py`) — `CostEstimate` (input/output × tier per-MTok prices);
  pre-call **estimate** reuses the GUARD's `est_input_tokens` + fixed `output_tokens_estimate`,
  post-call **actual** from `SummarizeResult` usage. `confirm_proceed` = Enter for cheap /
  explicit y-N past `confirm_threshold_usd` (injectable reader). Pure, offline (killswitch AST-tested).
- **T9 menu** (`menu.py`) — `run_menu()` loop: local file {summary·MP3·both} · saved
  transcript (recovery path, re-summarize w/o re-transcribe) · Settings · Exit. Shared
  `_run_summary`: GUARD(F6)→cost/threshold→one paid call→save `.json` BEFORE render(F13)→render.
  Fail-loud return-to-menu for F1/F2/F3/F4/F5/F13 + broad backstop; EOF exits clean. Every
  GPU/wire/ffmpeg collaborator injected via frozen `Deps` → offline-testable (killswitch AST-tested).
  ruff + mypy + **188 tests**. On `main` @ `b55f18c`.

- **T10 eval** (`tests/eval_quality.py` + `tests/fixtures/` RU+EN transcripts/golden
  summaries + `tests/test_eval.py`) — pure offline scorer: structure present +
  timecodes plausible (each `[HH:MM:SS]` appears in the transcript) + language
  plausible (RU Cyrillic / EN none). Offline gate runs in CI (golden refs clear the
  bar; scorer shown to REJECT broken/hallucinated/wrong-lang; real `summarize()` driven
  via stub caller). Live gate (`test_live_*`, the ONE real call) is skipped unless
  `ECHOGIST_LIVE_EVAL` + a key — that gate IS the killswitch. ruff + mypy + **197 tests,
  2 live skipped**. On `main` @ `2e0da3c`.

- **T11 harness** (`scripts/measure_model.py` + `tests/test_measure.py` +
  `tests/fixtures/audio/README.md`) — the GPU-free half of TD-4, finished + CI-tested in
  WSL (operator steer: split by GPU dependency). Pure core (`realtime_factor`,
  `speed_verdict`, `render_report`) unit-tested; warm GPU A/B (`int8_float16` vs `float16`
  via a `compute_type` flip on the one HF `model.bin`, no re-download) runs operator-side on
  the 4060. `--bar` optional: the report ALWAYS surfaces the measured realtime_factor; the
  operator sets the bar from it. mypy now covers the script. ruff + mypy + **209 tests, 2 skipped**.

- **T13 dev/ship loop** (`scripts/smoke_run.py` + `tests/test_smoke.py` + completed
  `scripts/win-smoke.bat` + `scripts/dev-loop --gpu` + `run.bat --provision-only`) — the
  GPU-free half, finished + CI-tested in WSL (same GPU-split as T11). The win32 platform
  shim (`gpu.register_cuda_libraries`) and the base `dev-loop` already landed with T2.
  `smoke_run.py` drives one clip through the REAL pipeline (extract→mp3, transcribe→
  transcript, then summary) mirroring the menu's order; its pure core (killswitch gate,
  artifact check, clip resolution) is unit-tested. **Killswitch:** extract+transcribe always
  run (offline, the Windows-only validation); the paid SUMMARIZE runs only with
  `ECHOGIST_LIVE_SMOKE` + a key (the `ECHOGIST_LIVE_EVAL` shape). `win-smoke.bat` now
  provisions via `run.bat --provision-only` (no menu block) then drives the clip + asserts
  artifacts; `dev-loop --gpu` reuses the one driver for the WSL fixture transcribe. mypy
  covers the driver. ruff + mypy + **222 tests, 2 skipped**. On `main` @ `b024368`.

**Build status:** all WSL-side build tasks (T1–T11, T13) are code-complete on `main`. What
remains is operator-only — the three 4060/Windows runs below — plus the T12 docs pass. No
WSL-side implementation work is outstanding.

**Operator runs — ALL DONE (2026-06-16).** The three 4060/Windows acceptance runs passed,
closing TD-3 + TD-4 and exercising the live gate (playbook: `docs/OPERATOR_TESTING_PLAYBOOK.md`):

- **Run 1 — win-smoke (closes TD-3).** Cold-Windows `scripts\win-smoke.bat` passed: provision
  → extract → real-GPU transcribe → artifacts, plus the live-summary variant (full pipeline
  incl. the paid call + PDF render). It caught + fixed a real cuBLAS lazy-load bug (173541d).
- **Run 2 — measurement (closes TD-4).** `int8_float16` = 10.11x realtime, 3.46 GB VRAM on the
  4060; verdict **keep int8_float16** (beat the float16 control on RU quality). Report at
  `docs/measurements/large-v3-int8_float16-2026-06-16.md`.
- **Run 3 — T10 live gate.** `ECHOGIST_LIVE_EVAL=1 pytest -m live` → 2 passed (real Sonnet call;
  RU+EN summaries clear the golden bar). Runs in the WSL dev env (the ship venv has no pytest).

**Default model tier is now `economy` (Haiku)** for everyday use (1e45bdf); `balanced`/`flagship`
remain selectable in Settings. The T10 live gate stays pinned to `balanced` (Sonnet).

**Next:**

- **T12 docs** — document final first-run steps post-build (last remaining build task).
- **Title-language prompt tweak (optional, minor).** On a German-source clip, Haiku produced a
  German *title* on a Russian summary (body was correctly Russian). Real RU→RU / EN→EN are fine;
  the gap is cross-language (source≠target). Candidate: harden the `[summarize]` title instruction
  to force the target `{language}`. LLM-prompt change → wants a live re-validation if done.

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

- **TD-5** chunked map-reduce deferred (opens on the first transcript that trips the overflow
  guard). **TD-1, TD-2, TD-3, TD-4 closed.** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
