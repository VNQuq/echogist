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

## Principles

- **Artifact-based recovery, not a job engine.** Durable state = the saved artifacts
  (`output/{audio,transcripts,summaries}`). The saved transcript is the checkpoint.
  No `job.json`, no history layer.
- **Pure stages.** Each pipeline stage is pure over an in-memory object so the whole
  pipeline is unit-testable with `SUMMARIZE` mocked.
- **Single-pass below the QualityBudget; map-reduce above it (TD-5).** Material under
  the QualityBudget (tokens *and* duration) is summarized in one structured call.
  Long/dense material that crosses it is split into balanced, overlapping chunks
  (MAP); the extracted points are concatenated and conservatively deduped — never
  re-summarized — and only title/overview/core_idea come from a synthesis call
  (REDUCE). Completeness over a single shallow pass. *Operator-approved 2026-06-25;
  supersedes the original single-pass-only rule. Per-chunk checkpointing stays cut —
  a failed chunk fails the whole run loud and retries from the saved transcript (no
  job engine).*
- **Config is data, not code.** Model IDs, prices, model source URL, settings are
  editable without a code change.
- **Fail loud, return to menu.** Every error path = human-readable message + clean
  return to the main menu; never a crash, never silent truncation or skip.
- **Estimate Cyrillic high.** The local token estimate is language-aware and biased
  high so an over-long transcript is always caught.

## State

- **Current status:** `docs/CURRENT_CONTEXT.md`

## Context Loading Policy

The File Map below is a routing table, not a preload list.

**At session start:**
1. Read `docs/CURRENT_CONTEXT.md`
2. Report: active deliverable, blockers, git state (branch + dirty files)
3. Do not read further docs until the task requires them
4. If docs conflict with operator instruction — stop and ask

## File Map

| Task | Read |
|------|------|
| Current priorities / blockers | `docs/CURRENT_CONTEXT.md` |
| Implementation — pipeline + console UX (architecture, stack, tasks, tests) | `docs/archive/ENGINEERING_PLAN.md` |
| Active technical debt | `docs/TECHNICAL_DEBT.md` |

## State Update Protocol

Upon task completion, before closing:
- Propose an update to `docs/CURRENT_CONTEXT.md`
- Flag any new technical debt for `docs/TECHNICAL_DEBT.md`

## Workflow (gstack)

Apply gstack skills at these gates:
- `/office-hours` — before any ambiguous or new-scope task
- `/plan-eng-review` — before any ADR-triggering implementation
- `/review` — before every commit touching non-trivial code
- `/context-save` — at end of every session
- `/context-restore` — at start of every resumed session

Do not skip review gates for: DB, LLM prompts, evaluation, safety, CI/deploy.

## Source-of-Truth Priority

When documents conflict:
1. Explicit operator instruction in current session
2. This CLAUDE.md (guardrails take precedence over all docs)
3. `docs/archive/ENGINEERING_PLAN.md` (locked build spec — authoritative for architecture/stack/scope)
4. `docs/CURRENT_CONTEXT.md` (live status — authoritative for priorities/blockers)
