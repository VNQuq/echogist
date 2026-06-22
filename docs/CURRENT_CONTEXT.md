# Current Context

**Updated:** 2026-06-23 (v1 + v1.1 shipped on `main`; Windows acceptance PASSED — TD-10/11/13/14
closed; only a cold/clean-deploy pass + T12 docs remain)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

EchoGist is **functionally complete and Windows-accepted on `main`** — both build phases shipped, and
the operator's 4060 Windows run passed (file picker, screen-clear, `← Back`, Explorer pop all
confirmed; TD-10/11/13/14 closed 2026-06-23). Only a cold/clean-deploy verification (T7, operator,
deferred) and the T12 first-run docs (WSL-doable) remain.

- **v1** ([archive/V1_ENGINEERING_PLAN.md](./archive/V1_ENGINEERING_PLAN.md)): pure-stage pipeline,
  artifact-based recovery (saved transcript = checkpoint), single-pass summarize. GUARD + cost are
  LOCAL/offline; `SUMMARIZE` is the one network stage. Earlier 4060 runs (2026-06-16) closed TD-3
  (win-smoke), TD-4 (→ `int8_float16`), and the T10 live gate (real Sonnet, RU+EN clear the bar).
- **v1.1** ([archive/V1.1_ENGINEERING_PLAN.md](./archive/V1.1_ENGINEERING_PLAN.md)): replaced the bare
  `input()`/numeric menu with **questionary + rich** (arrow-key nav, styled panels, %/ETA bar) behind
  a `UI` Protocol injected via `Deps` (prod `RichQuestionaryUI`, tests `StubUI`) — the seam keeps the
  killswitch CI offline/no-TTY. Files: `echogist/ui.py`, `echogist/theme.py`. Post-v1.1 UX debts
  (file picker, screen-clear, `← Back`, Explorer pop, trimmed `.mp3` menu, muted nav) all built and
  Windows-accepted — see [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md).

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds — every push to
`main` passes ruff + mypy --strict + tests. Current: **289 passed, 2 skipped** (the 2 live tests).

## Config / behavior notes

- **Default model tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings. T10 live gate is
  pinned to `balanced` (Sonnet).
- **Output layout:** F13 recovery `.json` → `output/summaries/raw/`; `output/summaries/` holds only
  the readable `.pdf`/`.md`. The triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`
  (`anthropic_api_key`) fallback — see `config/secrets.toml.example` (`53d0fa5`).
- **LLM prompt is data:** edit `config/models.toml` `[summarize] system_prompt` (only `{language}` is
  substituted); output schema is code in `summarize.py`.

## Next — two items left

- **T7 cold/clean-deploy (operator, deferred):** `pip install --require-hashes -r requirements.lock`
  on a fresh machine + cold first run (DLL/model provisioning). The accepted run was on an
  already-provisioned box; this is the one honest gap.
- **T12 docs (WSL-doable):** final first-run steps + the arrow-key menu (last deferred v1 task).

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
  [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): TD-5 chunked map-reduce · TD-6 title leaks source
  language · TD-7 non-TTY fallback UI · TD-9 dropped cheap-call Enter beat.
- **Closed:** TD-1/2/3/4/8/10/11/12/13/14.

## Relevant SoT

- Build specs (locked, archived): [v1](./archive/V1_ENGINEERING_PLAN.md) ·
  [v1.1](./archive/V1.1_ENGINEERING_PLAN.md).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md) ·
  operator playbook (RU): [`OPERATOR_TESTING_PLAYBOOK.md`](./archive/OPERATOR_TESTING_PLAYBOOK.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline +
  unit-testable against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
