# CLAUDE.md

## Identity

<TBD>

## Hard Constraints

**IMPORTANT: Non-negotiable. Stop and ask the operator before violating any.**

- **No destructive shell commands without explicit operator approval.**
  `rm -rf`, `git reset --hard`, `git clean -fd`, DDL `DROP` statements.

## Principles

<TBD>

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
3. `docs/CURRENT_CONTEXT.md`
