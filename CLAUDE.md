# CLAUDE.md

## Identity

EchoGist: a single-`.bat` Windows console tool. Input is a local audio/video file
or a saved transcript. Output is an MP3 track and/or a structured RU/EN summary
(PDF default, Markdown optional). Transcription is local GPU Whisper; summarization
is one cloud Anthropic call. No online sources, no logins, no history/analytics.
Personal-use, Windows-only; developed in WSL2.

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

The File Map below is a routing table, not a preload list.

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
