# Current Context

**Updated:** 2026-06-21 (TD-10 picker: T1–T4 BUILT on `main`; only T5 Windows live-dialog gate left)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**v1.1 — menu/UX overhaul (BUILT T1–T9, WSL gate green) — [`docs/V1.1_ENGINEERING_PLAN.md`](./V1.1_ENGINEERING_PLAN.md)**

Replaced the bare `input()`/numeric menu with **questionary + rich** (arrow-key nav, styled
output, a `%/ETA` transcription bar). UX-layer only — transcribe/summarize/render logic frozen.
All 9 tasks built on `main`; `bash scripts/dev-loop` = **251 passed, 2 skipped** (was 225).
Load-bearing decision held: a `UI` Protocol injected via `Deps` (production `RichQuestionaryUI`,
tests scripted `StubUI`) keeps the killswitch CI offline/no-TTY. New source: `echogist/ui.py`
(UI/ProgressHandle/SpinnerHandle Protocols + RichQuestionaryUI + StubUI), `echogist/theme.py`
(rich.Theme + questionary.Style + fancy/ASCII glyphs + `detect_caps`); `transcribe._collect_segments`
extracted; `cost.confirm_proceed` reshaped to a narrow `confirm` callable; `menu.py` rewired to the
seam. **Pending (operator/Windows-side):** T7 `--require-hashes` cold-install verify + the
interactive acceptance run (arrow nav, emoji-vs-ASCII fallback, %/ETA bar, Ctrl-C clean exit,
confirm defaults). Two deliberate plan divergences logged: **TD-8** (stale `setuptools<81`) and
**TD-9** (dropped cheap-call Enter beat).

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
  T9 menu · T10 eval · T11 measure harness · T13 dev/ship loop. Gate after v1.1: **251 passed,
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

- **TD-10 — file picker (T1–T4 BUILT on `main`, HIGH; only the T5 Windows gate left).** v1.1 used
  to make the operator type the audio/video path by hand — a UX blocker on the primary flow; the
  picker replaces it. Seam: `UI.pick_file(prompt, *, filetypes, initialdir=None) -> str | None`
  (eng-review added `initialdir` so the ladder stays a pure `config` fn and `ui.py` stays stateless).
  **T1 DONE** — `config.load_last_dir`/`save_last_dir` (fail-soft `config/state.json`, swallows
  `OSError`) + pure `resolve_initial_dir` ladder `last → ~/Downloads → ~` (never cwd, `OSError`-guarded).
  **T2 DONE** — `pick_file` on `UI`/`RichQuestionaryUI`/`StubUI`: native `tkinter` dialog, lazy +
  dual-guarded (`ImportError` tk-absent / `TclError` no-display) → `questionary.path()` fallback;
  explicit Tk root lifecycle (withdraw/topmost/destroy-in-finally); cancel split (dialog/blank → menu,
  Ctrl-C → exit). **T3 DONE** (`8c67b68`) — `menu._flow_local_file` rewired: `resolve_initial_dir(
  load_last_dir())` → `pick_file` → `None`→menu, else `_resolve_typed_path` (F1) → `save_last_dir(
  source.parent)` after a valid pick (covers all 3 actions, not just transcribe); `_AV_FILETYPES`
  constant; "Check the path…" folded into `_resolve_typed_path`. **T4 DONE** (`9e8d768`) — verified
  (`reveal_type`) tkinter/`filedialog` stubs resolve as real types under `mypy --strict`, not `Any`;
  documented at the lazy import. Gate green: **275 passed, 2 skipped** (was 251). **T5 NEXT (operator,
  Windows-only)** — live native dialog: `initialdir` honored, filetypes dropdown, native Cancel→menu,
  Ctrl-C→exit, no ghost window, focus over console, clean 2nd invocation. Not CI-testable.
  Commits `8c67b68`+`9e8d768` on `main`, NOT pushed. Full plan + findings in
  [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md) TD-10.
- **v1.1 Windows acceptance (operator-side)** — T7 `pip install --require-hashes -r
  requirements.lock` cold-run verify, then the interactive run: arrow-key menu + settings,
  emoji-vs-ASCII glyph fallback, the `%/ETA` transcription bar, Ctrl-C clean exit, confirm
  defaults (No above threshold, proceed below), result/error panels. The WSL dev loop is blind
  to `isatty`/`legacy_windows`/key handling — only this gate exercises them.
- **T12 docs** — document final first-run steps; fold in the new arrow-key menu now that v1.1
  is built (last v1 build task, deferred 2026-06-16).
- **TD-6 (logged, not actioned)** — summary title can leak the source language on
  cross-language input. LOW; opens when cross-language summarizing becomes normal.

## Dev env

WSL `.venv` (python3.12), GPU stack installed, RTX 4060 visible. Linux loads
cuDNN/cuBLAS via `LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim.
PyPI + GitHub reachable from WSL; huggingface.co is not (no VPN). `ANTHROPIC_API_KEY`
not set here (live gate needs it: `ECHOGIST_LIVE_EVAL=1 .venv/bin/pytest -m live`).
API key now resolves from `ANTHROPIC_API_KEY` env first, then a gitignored
`config/secrets.toml` (`anthropic_api_key`) fallback — `config/secrets.toml.example` shows the format (`53d0fa5`).
`anthropic` + `fpdf2` installed in `.venv` (match the lock); both lazy-imported so the
killswitch invariant holds. v1.1 adds `questionary==2.1.1` + `rich==15.0.0` (+ `prompt_toolkit`,
`wcwidth`) — pure-Python, offline, no ABI tie to the GPU pins. Telemetry off, PROACTIVE false.

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
  non-interactive run is ever needed) · **TD-8** stale `setuptools<81` plan wording vs the
  82.0.1 lock (LOW; reconcile at next re-lock) · **TD-9** cheap-call "press Enter" beat dropped
  in v1.1 (LOW; restore on operator request) · **TD-10** file picker — T1–T4 BUILT on `main`,
  only the T5 Windows live-dialog gate left (**HIGH**, operator-side). **TD-1, TD-2, TD-3, TD-4 closed.**
  → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
