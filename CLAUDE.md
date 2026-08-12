# CLAUDE.md

## Identity

EchoGist: a single-`.bat` Windows console tool. Input is a local audio/video file
or a saved transcript. Output is an MP3 track and/or a structured RU/EN summary
(PDF default, Markdown optional). Transcription is local GPU Whisper; summarization
is one cloud Anthropic call. No online sources, no logins, no history/analytics.
Personal-use, Windows-only; developed in WSL2.

The authoritative build spec is `docs/archive/ENGINEERING_PLAN.md` (the locked reference for
architecture/stack/scope, covering the full v1.0 build). Do not deviate from it without
operator approval.

## Hard Constraints

**IMPORTANT: Non-negotiable. Stop and ask the operator before violating any.**

- **No destructive shell commands without explicit operator approval.**
  `rm -rf`, `git reset --hard`, `git clean -fd`, DDL `DROP` statements.
- **API key from `ANTHROPIC_API_KEY` env or local config file only. Never in code,
  never committed.**
- **Killswitch.** Live LLM calls are forbidden without a killswitch. `SUMMARIZE` is
  the only network stage; every stage left of it (incl. the token GUARD and cost
  estimate) is local and offline — no `count_tokens` or any network call. The
  pipeline must run end-to-end in CI against a stub summarizer.
- **Every push to `main` passes: ruff + mypy + tests.** No exceptions.
- **No manual workarounds.** Provisioning, fetching, and recovery must be automated
  and idempotent.
- **Commit messages are English only** — subject and body. User-facing artifacts keep
  their own language (`CHANGELOG.md` and console strings are Russian by design).

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

## Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

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

Upon task completion, before closing:
- Propose an update to `docs/CURRENT_CONTEXT.md`
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
