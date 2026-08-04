# Current Context

**Updated:** 2026-08-05 — two unreleased features on `main`, the ESSENCE BLOCK and the MP3 conversion options.
Last release **v2.1.0** (model currency + the TD registry closed, TD-1..21). **Authority:**
[CLAUDE.md](../CLAUDE.md) — the hard constraints and the rationale live there, not here. **Max:** ≈ 60 lines.

## Pipeline — TD-16 v2 direct synthesis (v2.0.0)

**The only path:** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short = K=1) →
`synthesize_summary` ×K sequential forward-only (each phase sees its span + prior headings + the prior
phase's TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept / snap-2s / drop vs that
phase's own timecodes; strips inline `[HH:MM:SS]`) → concatenate decisions/actions → reconcile (title +
essence block + main_themes + normalized headings — **always**, incl. K=1) → header validation → one doc.
**Validated 2026-06-27** (Sonnet, 2:58:57 RU lecture, K=4, $0.5237, 131/131 anchors resolve) — TD-16 entry.

## Unreleased on `main` (both ride the v2.2.0 bump)

**Essence block** (2026-08-04). Opens with «Суть» — `core_idea` ~250-350 words, `main_skill` ~150-200 (empty
when the material teaches none), 3 `test_questions` — and CLOSES with «Ориентиры для ответов»: answers sit at
the far end because seeing a question must not hand you its answer. Written by **reconcile** from phase prose
only (never a second transcript read), passed through `validate_anchors`; prompt word budgets are the knob for
the 1-2 page cap (~500 words per PDF page). **Cost:** reconcile now runs ALWAYS incl. K=1 — skipping it when
material is short would make the feature silently absent — so short input is **2 calls, not 1**.

**MP3 options in menu #1** (written 2026-08-03, unmerged by mistake, landed 2026-08-05). Both reverse TD-12,
operator-approved, offline: a video Summary/Transcript run now asks `confirm("Also save the converted MP3?",
default=True)` instead of writing it silently; an mp3 source gains "Re-encode to a smaller MP3" (VBR ~q2,
same `_convert_to_mp3` path) but its Summary/Transcript run asks nothing — shrinking an mp3 is a deliberate
action, not a per-run prompt (operator, 2026-08-05).

## Config / behavior notes

- Tools `emit_phase`/`emit_reconcile`; the inline `[интерпретация]:` marker is plain text, surviving MD+PDF.
  **Default tier `economy` (Haiku)**. **Prompt is data** (`config/models.toml`), SCHEMAs stay in code.
- **Model currency:** tiers pin floating aliases (`claude-haiku-4-5`, `-sonnet-5`, `-opus-4-8`) —
  always-latest, reproducibility intentionally dropped. `scripts/check-models.py` (standalone,
  killswitch-safe) reports retired/valid + context drift vs `GET /v1/models`, derives `context_window`, flags
  a tier `prices_unverified` on a generation bump. **Prices stay manual** — no pricing endpoint; while
  flagged `_run_summary` prints a one-time notice ($0.50 gate unchanged). `balanced` verified 2026-08-03.
- **Anchors are TEXTUAL, not links** — `[HH:MM:SS]` inline in prose (TD-19), nothing to click; the validator
  guarantees each resolves to a real transcript block.
- **Output:** recovery `.json` → `output/summaries/raw/`, resume partial → `raw/.resume/`, readable
  `.pdf`/`.md` → `output/summaries/`, one shared stem. `output/` is gitignored and machine-local, so a
  Windows-produced summary is only re-validatable in WSL against ITS transcript. **API key:**
  `ANTHROPIC_API_KEY` env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs happen on
  Windows.

## Next

1. **Paid validation run of the essence block** (Windows — WSL has no key). Against the recording: is the
   навык the one the author actually teaches, are the 3 questions answerable only by someone who followed the
   material, does the block fit 1-2 pages? Then cut **v2.2.0** — essence block AND the MP3 options.
2. **First live run of `check-models.py`** on a box with a key: seeds `config/model_names.json`, confirms the
   real `GET /v1/models` shape. Run one is a pure baseline; the signal starts on run two.
3. **T8 (P3):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. **Debts:** [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md) **fully closed** — TD-1..21, shut
  2026-08-03 (TD-7 non-TTY UI + TD-20 PDF polish WONTFIX).
- **SoT:** locked build spec [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates,
  operator-approved) · original SOW [`ТЗ`](./archive/ТЗ_аудио_резюме_приложение.md) · plan
  `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared).
