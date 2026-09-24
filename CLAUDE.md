# CLAUDE.md

## Identity

EchoGist: a single-`.bat` Windows console tool. Input is a local audio/video file
or a saved transcript. Output is an MP3 track and/or a structured RU/EN summary
(PDF default, Markdown optional). Transcription is local GPU Whisper; summarization
is cloud Anthropic calls (one per phase + one reconcile). No online sources, no logins,
no history/analytics. Personal-use, Windows-only; developed in WSL2.

## Hard Constraints

**IMPORTANT: Non-negotiable. Stop and ask the operator before violating any.**

- **No destructive shell commands without explicit operator approval.**
  `rm -rf`, `git reset --hard`, `git clean -fd`, DDL `DROP` statements.
- **API key from `ANTHROPIC_API_KEY` env or local config file only. Never in code,
  never committed.**
- **Killswitch.** Live LLM calls are forbidden without a killswitch. `SUMMARIZE` is
  the only network stage; every stage left of it (incl. the token GUARD and cost
  estimate) is local and offline — no `count_tokens` or any network call. The
  pipeline must run end-to-end against a stub summarizer, locally, before every push.
- **Every push to `main` passes: ruff + ruff format + mypy + tests.** One exception: a
  docs-only commit may land without the gate — nothing under `echogist/`, `tests/`, `scripts/`
  or `config/` touched. A commit that mixes docs with code is a code commit and passes the gate.
  CI (`.github/workflows/ci.yml`) re-runs the same four on Windows and Linux, Python 3.11.
- **No manual workarounds.** Provisioning, fetching, and recovery must be automated
  and idempotent.
- **English is the language of the application.** Commit messages (subject and body),
  code, comments, docstrings, console strings, menu labels and log output are all English.
  Russian is reserved for the FINAL user-facing documents: `README.md`, `docs/USAGE.md`,
  `CHANGELOG.md`, and the generated summary artifacts (whose language is the operator's
  `summary_language` setting).

## Principles

- **Artifact-based recovery, not a job engine.** Durable state = the saved artifacts
  (`output/{audio,transcripts,summaries}`). The saved transcript is the checkpoint.
  No `job.json`, no history layer.
- **Pure stages.** Each pipeline stage is pure over an in-memory object so the whole
  pipeline is unit-testable with `SUMMARIZE` mocked.
- **Fidelity > completeness; the transcript is ground truth, read directly (TD-16 v2).**
  The transcript is split into a computed number of balanced, contiguous, NON-overlapping
  phases (`chunk.plan_phases`; short material = K=1, the whole transcript in one call), and
  each phase is synthesized DIRECTLY into faithful prose — one hop, no map-extracted
  intermediate, no coverage checklist, no grouping. Phases run sequentially and
  forward-only (each sees prior headings + the prior phase's real tail prose as
  do-not-restate context); a reconcile pass (ALWAYS, incl. K=1) writes the title, the
  ESSENCE BLOCK (главная мысль / главный навык / 3 проверочных вопроса + their answers)
  and main_themes from the phase prose, and never re-reads the transcript. Every emitted
  `[HH:MM:SS]` anchor is validated offline against the real block timecodes (accept / snap
  within 2s / drop) — that, plus the operator's manual re-check against the recording, IS
  the fidelity gate. No LLM-judge, no automated coverage signal. *Operator-approved
  2026-06-26; supersedes the TD-5 single-pass/map-reduce rule and TD-15 group-keep-all. A
  failed phase fails the whole run loud; re-run from the saved transcript SKIPS phases
  already persisted to disk (artifact-resume, not a job engine).*
- **Five fidelity properties (the v2 acceptance criterion).** A v2 summary must be:
  (1) **grounded** — every sentence traceable to the transcript; (2) **no fabrication** —
  nothing added that the author did not say (a bridge beyond the text is marked inline with
  `[интерпретация]:`); (3) **faithful stance** — no inversion or softening, caveats kept;
  (4) **no merged distinctions** — two distinct points never collapsed into one;
  (5) **coverage** — every point reflected, or consciously dropped (the manual gate).
  Plus the load-bearing invariant: **every anchor resolves to a real transcript timecode.**
- **Config is data, not code.** Model IDs, prices, model source URL, settings are
  editable without a code change.
- **Fail loud, return to menu.** Every error path = human-readable message + clean
  return to the main menu; never a crash, never silent truncation or skip.
- **Estimate Cyrillic high.** The local token estimate is language-aware and biased
  high so an over-long transcript is always caught.

## State

- **Current status:** `docs/CURRENT_CONTEXT.md`

## Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## Release

Releases are cut with `scripts/release.py` + a GitHub PAT — **never `/ship`** (the gstack ship
skill is not used here). Flow for `vX.Y.Z`: bump `VERSION`, write the `## [X.Y.Z]` `CHANGELOG.md`
section, commit + push, push the annotated tag `vX.Y.Z`, then `python3 scripts/release.py`. The PAT
lives only in the `ECHOGIST_GITHUB_TOKEN` env var (`~/.bashrc`, "Contents: Read and write"), never
in git.

## Context Loading Policy

Read on demand, not up front: `docs/archive/ENGINEERING_PLAN.md` for pipeline/console
implementation, `docs/TECHNICAL_DEBT.md` for active debt.

**At session start:**
1. Read `docs/CURRENT_CONTEXT.md`
2. Report: active deliverable, blockers, git state (branch + dirty files)
3. Do not read further docs until the task requires them
4. If docs conflict with operator instruction — stop and ask

## State Update Protocol

`docs/CURRENT_CONTEXT.md` is **owned by the assistant** (operator instruction, 2026-09-06).
Write it, trim it, and keep it true without asking. Never put a question about its contents,
its shape or its status to the operator — not "may I update it", not "is this still current",
not "which line should go". If a fact in it turns out to be wrong, fix it. The operator still
edits it whenever they want, and their version wins.

**ONE trim per update, and it lands under the ~88-line cap.** Add and cut in the SAME edit:
count the lines before writing, and if the result is over, cut in that write — never commit an
over-cap file and trim it in a follow-up. A second trim pass means the first was not a decision,
and it spends two commits saying what one says. Cut the paraphrase, the note that has become the
steady state, and anything CLAUDE.md or the CHANGELOG already carries; keep what only this file
knows.

Upon task completion, before closing:
- Update `docs/CURRENT_CONTEXT.md` (silently — see above)
- Flag any new technical debt for `docs/TECHNICAL_DEBT.md`

## Workflow (gstack)

Apply gstack skills at these gates:
- `/office-hours` — before any ambiguous or new-scope task
- `/plan-eng-review` — before any ADR-triggering implementation
- `/review` — before every commit touching non-trivial code

Do not skip review gates for: DB, LLM prompts, evaluation, safety, CI/deploy.

## Source-of-Truth Priority

When documents conflict:
1. Explicit operator instruction in current session
2. This CLAUDE.md (guardrails take precedence over all docs)
3. `docs/archive/ENGINEERING_PLAN.md` (locked build spec — authoritative for architecture/stack/scope)
4. `docs/CURRENT_CONTEXT.md` (live status — authoritative for priorities/blockers)
