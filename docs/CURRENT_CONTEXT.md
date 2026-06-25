# Current Context

**Updated:** 2026-06-25 (**v1.1.0 RELEASED** — VERSION 1.1.0, CHANGELOG `[1.1.0] — 2026-06-25`, tag
`v1.1.0` pushed, GitHub Release published via `scripts/release.py`. The paid **T10 live gate passed**
(`2 passed` — RU/EN quality + TD-6 cross-language title, real Sonnet). The two 2026-06-25 console
fixes are **4060-Windows-verified**. Only the deferred T7 cold/clean-deploy pass remains.)
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

EchoGist is **released through v1.1.0** — both build phases of the
[engineering plan](./archive/ENGINEERING_PLAN.md) shipped on `main`, all GitHub Releases published with
notes from [CHANGELOG.md](../CHANGELOG.md). The 4060 Windows acceptance passed. Only a cold/clean-deploy
verification (T7, operator, deferred) remains.

- **Phase 1 — pipeline** (ENGINEERING_PLAN §1–§5): pure-stage pipeline, artifact-based recovery (saved
  transcript = checkpoint), single-pass summarize. GUARD + cost are LOCAL/offline; `SUMMARIZE` is the one
  network stage.
- **Phase 2 — console UX** (ENGINEERING_PLAN §6): questionary + rich (arrow-key nav, styled panels, %/ETA
  bar) behind a `UI` Protocol injected via `Deps` (prod `RichQuestionaryUI`, tests `StubUI`) — the seam
  keeps the killswitch CI offline/no-TTY. Files: `echogist/ui.py`, `echogist/theme.py`. File picker,
  screen-clear, `← Back`, Explorer pop, trimmed `.mp3` menu all built and Windows-accepted.
- **v1.1.0** ships decisions + action_items in the summary plus a hardened `[summarize]` system prompt
  (degraded-path, fixed `{unassigned}` label, `temperature=0`, TD-6 title language). The 3–7/2–6 list
  bounds it shipped were **removed in `[Unreleased]`** (see below).

**Workflow:** develop directly on `main` (operator decision 2026-06-15). Gate holds — every push to
`main` passes ruff + mypy --strict + tests. Current: **349 passed, 2 skipped** (the 2 live tests).

**Unreleased (CHANGELOG `[Unreleased]`), two changes:**
1. **Timecodes coarsened** — Whisper segments grouped into ~60s blocks (`[transcript] block_seconds`),
   one `[HH:MM:SS]` per paragraph (−95% lines). Same fix aligns both SUMMARIZE paths: the fresh run now
   summarizes the saved checkpoint verbatim (was timecode-free `transcript.text`), so `section_timecodes`
   are citeable on fresh runs too. (Committed `105da22`.)
2. **No-upper-limit summary + section bullets (anti-truncation).** Removed the 3–7/2–6/2–4/1–2 element
   caps from both the schema (`summarize.py`) and the prompt (`models.toml`) — model must emit ALL
   concepts (lower bounds kept). Added structural `bullets: list[str]` to `SectionMarker` (schema /
   dataclass / parser / save-load / PDF+MD render): 3–5 theses per section for >20-min material.
   `max_output_tokens` 4096→8192, `[guard] output_tokens_estimate` 2000→4000. No beta header needed at
   this size. Archived `ENGINEERING_PLAN.md §135` ("summary length is roughly constant") is now a
   deliberate deviation, left unedited (frozen spec).
3. **Map-reduce for long/dense material (TD-5) — completeness guarantee.** New `echogist/chunk.py`
   (pure/local): a transcript over the **QualityBudget** (`[chunk]` in models.toml — >40k tok OR >60 min,
   below ContextBudget on purpose: single-pass loses the middle of a long context) is split into balanced,
   ~90s-overlapping chunks on block boundaries. `summarize.summarize_chunked` MAPs each chunk (full
   emit_summary contract, "segment N/M" note) then REDUCEs: list fields concatenated + conservatively
   deduped (never re-summarized), a small `emit_synthesis` call writes only title/overview/core_idea.
   `summarize_auto` dispatches single vs chunked; the menu shows chunk count + N+1-call cost and logs the
   acceptance invariant (`extracted → after dedup`). The old single-pass "too long" refusal is retired.
   Per-chunk checkpointing stays cut (fail-loud, retry from transcript — no job engine). Files touched:
   `chunk.py` (new), `summarize.py`, `config.py` (`ChunkConfig` + `[chunk]` + reduce/map prompts),
   `cost.py` (`estimate_cost_chunked`), `menu.py`, `config/models.toml`, `CLAUDE.md` (Single-pass
   principle superseded, operator-approved), + tests (`test_chunk.py` new, summarize/config/menu).

**Calibration gap (both #2 and #3 need ONE paid run on a real lecture, killswitch blocks live test):**
`max_output_tokens=8192` per chunk, `target_chunk_tokens=12000`, and the two QualityBudgets are
unverified on real RU material. The first real chunked run prints the `extracted → after dedup` numbers
and per-call token usage — read those to confirm 12k is right and dedup isn't collapsing ideas; if a
segment trips the loud `max_tokens` guard, raise `max_output_tokens` (and switch SUMMARIZE to streaming
above ~16k). Map-stage is still lossy *within* a chunk; the reduce is not (that is the guarantee).

## Config / behavior notes

- **Default model tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings. T10 live gate is pinned
  to `balanced` (Sonnet).
- **Output layout:** F13 recovery `.json` → `output/summaries/raw/`; `output/summaries/` holds only the
  readable `.pdf`/`.md`. The triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml` (`anthropic_api_key`)
  fallback — see `config/secrets.toml.example`.
- **LLM prompt is data:** edit `config/models.toml` `[summarize] system_prompt` (two tokens substituted:
  `{language}` and `{unassigned}`); output schema is code in `summarize.py`. `build_request` pins
  `temperature=0` so the title (the artifact filename stem) is stable across re-runs.

## Next

- **No release work outstanding.**
- **T7 cold/clean-deploy (operator, deferred):** `pip install --require-hashes -r requirements.lock` on a
  fresh machine + cold first run (DLL/model provisioning). The accepted run was on an already-provisioned
  box; this is the one honest gap.

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Linux loads cuDNN/cuBLAS via
`LD_LIBRARY_PATH` (`scripts/dev-loop`); Windows via the win32 shim. PyPI + GitHub reachable from WSL;
huggingface.co is not (no VPN). `ANTHROPIC_API_KEY` not set here (live gate:
`ECHOGIST_LIVE_EVAL=1 .venv/bin/pytest -m live`). `anthropic` + `fpdf2` + `questionary==2.1.1` +
`rich==15.0.0` installed (match the lock); all lazy/offline so the killswitch holds. Telemetry off,
PROACTIVE false.

## Open blockers / debts

- **Blockers: none.**
- **Open debts** (all LOW, trigger-gated, nothing blocking) → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md):
  TD-5 chunked map-reduce · TD-7 non-TTY fallback UI · TD-9 dropped cheap-call Enter beat.
- **Closed:** TD-1/2/3/4/6/8/10/11/12/13/14 (**TD-6 closed 2026-06-25** — title-language prompt fix
  confirmed by the v1.1.0 live gate).

## Relevant SoT

- Build spec (locked): [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (pipeline + console UX).
- Original SOW: [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md) ·
  operator playbook (RU): [`OPERATOR_TESTING_PLAYBOOK.md`](./archive/OPERATOR_TESTING_PLAYBOOK.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` is the only network stage; everything left of it is offline + unit-testable
  against a stub. (Model fetch is one-time provisioning, not a stage.)
- Every push to `main` must pass: ruff + mypy + tests. (Development is on `main` directly.)
