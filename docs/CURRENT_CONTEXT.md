# Current Context

**Updated:** 2026-06-14
**Authority:** [CLAUDE.md](../CLAUDE.md)
**Max length:** ≤ 2 pages (≈ 60–70 lines).

---

## Active scope

**Phase 1 — <TBD>**

Done:

1. <TBD>

Next:

- <TBD>

## Relevant SoT

- <TBD>

## Open blockers

- None.

## Open debts

- None.

Details → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md)

## Hard constraints (carry-over)

- No private keys in code.
- Live LLM calls — forbidden without killswitch.
- Every push to `main` must pass CI gates: ruff + mypy + tests.