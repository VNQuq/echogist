# Current Context

**Updated:** 2026-06-25 (**v1.0.1 released** — patch over v1.0.0: a %/ETA progress bar for video→MP3
conversion, and the Explorer reveal extended to the MP3-only flow with a background no-focus-steal
open. VERSION + CHANGELOG bumped to 1.0.1; `v1.0.1` tag pushed and the GitHub Release published
(`scripts/release.py`). Gate green: 303 passed, 2 skipped. Pending a 4060 Windows verification of
both fixes + the deferred cold/clean-deploy pass)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

EchoGist is **released as v1.0.0** — both build phases of the
[engineering plan](./archive/ENGINEERING_PLAN.md) shipped, `release/v1.0` merged to `main` (`ecb31d7`,
`--no-ff`), tag `v1.0.0` pushed, and the GitHub Release published with notes from
[CHANGELOG.md](../CHANGELOG.md). The operator's 4060 Windows run passed (file picker, screen-clear,
`← Back`, Explorer pop all confirmed; TD-10/11/13/14 closed 2026-06-23). The first-run/usage guide
shipped ([USAGE.md](./USAGE.md)); only a cold/clean-deploy verification (T7, operator, deferred)
remains.

- **Phase 1 — pipeline** (ENGINEERING_PLAN §1–§5): pure-stage pipeline, artifact-based recovery
  (saved transcript = checkpoint), single-pass summarize. GUARD + cost are LOCAL/offline;
  `SUMMARIZE` is the one network stage. Earlier 4060 runs (2026-06-16) closed TD-3 (win-smoke), TD-4
  (→ `int8_float16`), and the T10 live gate (real Sonnet, RU+EN clear the bar).
- **Phase 2 — console UX** (ENGINEERING_PLAN §6): replaced the bare `input()`/numeric menu with
  **questionary + rich** (arrow-key nav, styled panels, %/ETA bar) behind a `UI` Protocol injected
  via `Deps` (prod `RichQuestionaryUI`, tests `StubUI`) — the seam keeps the killswitch CI
  offline/no-TTY. Files: `echogist/ui.py`, `echogist/theme.py`. The follow-on UX debts (file picker,
  screen-clear, `← Back`, Explorer pop, trimmed `.mp3` menu, muted nav, hidden 1-9 quick-select) all
  built and Windows-accepted — see [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md).

**Post-release fixes (2026-06-25, on `main`):** (1) **Bug #1** — `extract_audio` now drives a %/ETA
bar: it probes duration (`ffmpeg -i` stderr; no ffprobe in imageio-ffmpeg) then streams
`-progress pipe:1` `out_time_us` via a new `stream_runner` seam; no callback / unknown duration falls
back to the plain runner (no fake %). (2) **Bug #2** — `reveal_dir` opens in the background
(`ShellExecuteW(SW_SHOWNOACTIVATE)` → `os.startfile` fallback) and the menu reveals `output/audio`
for MP3-only vs `output/transcripts` for transcribe flows, one folder per launch. Both verified via
seams/unit tests only (WSL `os.name != nt`) — need a 4060 Windows pass.

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds — every push to
`main` passes ruff + mypy --strict + tests. Current: **303 passed, 2 skipped** (the 2 live tests).

## Config / behavior notes

- **Default model tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings. T10 live gate is
  pinned to `balanced` (Sonnet).
- **Output layout:** F13 recovery `.json` → `output/summaries/raw/`; `output/summaries/` holds only
  the readable `.pdf`/`.md`. The triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`
  (`anthropic_api_key`) fallback — see `config/secrets.toml.example` (`53d0fa5`).
- **LLM prompt is data:** edit `config/models.toml` `[summarize] system_prompt` (only `{language}` is
  substituted); output schema is code in `summarize.py`.

## Next

- **v1.1.0 feature on `main` (uncommitted):** summary schema gained `decisions` ({decision,
  rationale}) and `action_items` ({task, owner, estimate}) — meeting/planning output with effort
  estimates; rendered (PDF+MD) under localized RU/EN headings, empty for non-meeting material. Same
  change hardened the `title` prompt (TD-6). Offline gate green (309 passed, 2 skipped); **needs the
  paid live gate (T10) for RU/EN quality + the TD-6 cross-language title re-check.** VERSION still
  1.0.1 — bump to 1.1.0 at release (operator, via `/ship` or `scripts/release.py`).
- **Verify the two 2026-06-25 fixes on the 4060 Windows box:** does the bar advance during a real
  video→MP3, and does Explorer pop *behind* the console for MP3-only (`output/audio`) and stay put on
  Both (`output/transcripts`)?
- **T7 cold/clean-deploy (operator, deferred):** `pip install --require-hashes -r requirements.lock`
  on a fresh machine + cold first run (DLL/model provisioning). The accepted run was on an
  already-provisioned box; this is the one honest gap.

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Linux loads cuDNN/cuBLAS via
`LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim. PyPI + GitHub reachable from WSL;
huggingface.co is not (no VPN). `ANTHROPIC_API_KEY` not set here (live gate:
`ECHOGIST_LIVE_EVAL=1 .venv/bin/pytest -m live`). `anthropic` + `fpdf2` + `questionary==2.1.1` +
`rich==15.0.0` installed (match the lock); all lazy/offline so the killswitch holds. Telemetry off,
PROACTIVE false.

## Open blockers / debts

- **Blockers: none.**
- **Open debts** (all LOW, trigger-gated, nothing blocking) →
  [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): TD-5 chunked map-reduce · TD-6 title-language **prompt
  fix landed 2026-06-25** (pending paid cross-language live re-check) · TD-7 non-TTY fallback UI ·
  TD-9 dropped cheap-call Enter beat.
- **Closed:** TD-1/2/3/4/8/10/11/12/13/14.

## Relevant SoT

- Build spec (locked): [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (pipeline + console UX).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md) ·
  operator playbook (RU): [`OPERATOR_TESTING_PLAYBOOK.md`](./archive/OPERATOR_TESTING_PLAYBOOK.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
