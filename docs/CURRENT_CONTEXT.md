# Current Context

**Updated:** 2026-06-21 (v1 + v1.1 built on `main`; menu/UX debts TD-11..14 closed in code —
only operator-side Windows acceptance remains; engineering plans archived)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

EchoGist is **functionally complete on `main`** — both build phases shipped. The only open work is
the operator-side Windows acceptance pass; everything WSL-buildable is done and green.

**v1 — complete** ([`docs/archive/V1_ENGINEERING_PLAN.md`](./archive/V1_ENGINEERING_PLAN.md)).
Pure-stage pipeline, artifact-based recovery (saved transcript = checkpoint), single-pass summarize.
GUARD + cost are LOCAL/offline; `SUMMARIZE` is the one network stage. Three operator 4060/Windows
acceptance runs passed (2026-06-16): win-smoke (closed TD-3), measurement (closed TD-4 →
`int8_float16`), and the T10 live gate (real Sonnet, RU+EN clear the golden bar).

**v1.1 — menu/UX overhaul, built**
([`docs/archive/V1.1_ENGINEERING_PLAN.md`](./archive/V1.1_ENGINEERING_PLAN.md)). Replaced the bare
`input()`/numeric menu with **questionary + rich** (arrow-key nav, styled panels, a %/ETA
transcription bar) behind a `UI` Protocol injected via `Deps` (production `RichQuestionaryUI`, tests
`StubUI`) — the seam keeps the killswitch CI offline/no-TTY. New: `echogist/ui.py`, `echogist/theme.py`.

**Post-v1.1 UX debts — closed in code (2026-06-21):**
- **TD-10 file picker** (T1–T4): `UI.pick_file` — native tkinter dialog → `questionary.path()`
  fallback; last-used dir in gitignored `config/state.json`. Only **T5** (live Windows dialog) left.
- **TD-11/12/13/14** (`f4fd2ca`, office-hours pragmatic 80/20): `UI.clear()` on flow entry (no more
  stacked chrome); `.mp3` skips the action menu → summary; `← Back` entries (ESC stays = exit); 
  `UI.reveal_dir()` pops the transcript folder once per launch (Windows). TD-12 fully closed.

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds — every push to
`main` passes ruff + mypy --strict + tests. Current: **287 passed, 2 skipped** (the 2 live tests).

## Config / behavior notes

- **Default model tier = `economy` (Haiku)** for everyday use; `balanced`/`flagship` selectable in
  Settings. The T10 live gate stays pinned to `balanced` (Sonnet).
- **Output layout:** the F13 recovery `.json` writes to `output/summaries/raw/`; `output/summaries/`
  holds only the readable `.pdf`/`.md`. The triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then a gitignored `config/secrets.toml`
  (`anthropic_api_key`) fallback — `config/secrets.toml.example` shows the format (`53d0fa5`).
- **LLM prompt is data:** edit `config/models.toml` `[summarize] system_prompt` (only `{language}`
  is substituted); the output schema is code in `summarize.py`.

## Next — operator-side Windows acceptance (the only open work)

One Windows pass on the 4060 covers all remainders (WSL is blind to `isatty`/`legacy_windows`/key
handling / tkinter / `os.startfile`):
- **v1.1 menu:** arrow-key nav + settings, emoji-vs-ASCII glyph fallback, the `%/ETA` bar, Ctrl-C
  clean exit, confirm defaults (No above threshold, proceed below), result/error panels.
- **T7 cold-install:** `pip install --require-hashes -r requirements.lock` clean cold run.
- **TD-10 T5:** native picker dialog — `initialdir`, filetypes, native Cancel→menu, no ghost window,
  clean 2nd invocation.
- **TD-11 / TD-14:** `console.clear()` renders cleanly (conhost vs Windows Terminal); Explorer pops
  once after a transcript save.
- **T12 docs:** final first-run steps + the arrow-key menu (last deferred v1 build task).

## Dev env

WSL `.venv` (python3.12), GPU stack installed, RTX 4060 visible. Linux loads cuDNN/cuBLAS via
`LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim. PyPI + GitHub reachable from WSL;
huggingface.co is not (no VPN). `ANTHROPIC_API_KEY` not set here (live gate needs it:
`ECHOGIST_LIVE_EVAL=1 .venv/bin/pytest -m live`). `anthropic` + `fpdf2` + `questionary==2.1.1` +
`rich==15.0.0` installed (match the lock); all lazy/offline so the killswitch holds. Telemetry off,
PROACTIVE false.

## Open blockers

- **None.** TD-1 (the only standing blocker) is closed.

## Open debts → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

- **TD-10** file picker — T1–T4 built, only the T5 Windows live-dialog gate left (**HIGH**, operator).
- **TD-11 / TD-14** menu/UX built (`f4fd2ca`); Windows-acceptance remainder only (clear-render,
  Explorer pop). **TD-13** `← Back` built; ESC-as-back deferred (LOW).
- **TD-5** chunked map-reduce · **TD-6** title leaks source language · **TD-7** non-TTY fallback UI ·
  **TD-8** stale `setuptools<81` plan wording · **TD-9** dropped cheap-call Enter beat — all LOW.
- **Closed:** TD-1, TD-2, TD-3, TD-4, TD-12.

## Relevant SoT

- **Build specs (locked, archived):** [v1](./archive/V1_ENGINEERING_PLAN.md) ·
  [v1.1](./archive/V1.1_ENGINEERING_PLAN.md).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).
- Operator testing playbook (RU): [`OPERATOR_TESTING_PLAYBOOK.md`](./archive/OPERATOR_TESTING_PLAYBOOK.md).

## Hard constraints (carry-over)

- API key from `ANTHROPIC_API_KEY` env / local config file only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
