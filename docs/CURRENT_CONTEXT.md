# Current Context

**Updated:** 2026-08-11 — **batch video→MP3 landed on `main`, unreleased and not yet run on Windows.**
**Authority:** [CLAUDE.md](../CLAUDE.md) — the hard constraints and the rationale live there, not here.
**Max:** ≈ 60 lines.

## Pipeline — TD-16 v2 direct synthesis (v2.0.0)

**The only path:** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short = K=1) →
`synthesize_summary` ×K sequential forward-only (each phase sees its span + prior headings + the prior phase's
TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept / snap-2s / drop vs that phase's
own timecodes) → concatenate decisions/actions → reconcile (title + essence block + main_themes + normalized
headings — **always**, incl. K=1) → header validation → one doc. **Validated 2026-06-27** (Sonnet, 2:58:57 RU
lecture, K=4, $0.5237, 131/131 anchors resolve) — TD-16 entry.

## Unreleased on `main` — batch video → MP3 (`echogist/batch.py`, 2026-08-11)

Menu entry #2, offline and free — MP3 is the whole deliverable, no transcript/summary/wire. `pick_files`
(native `askopenfilenames`, Shift/Ctrl/Ctrl+A) → `expand_selection` → `convert_many` over a `ThreadPoolExecutor`
of `settings.batch_workers` (1-16, seeded `min(4, cpu_count)`; **1 = the sequential path, same code**) → one
aggregate bar counting FILES → report. Operator decisions: worker pool; multi-select only (**no folder picker,
cut as over-scope**); one `confirm` before re-encoding mp3s, default NO; **Ctrl-C stops the batch, not the
app** — the one local exception to the global Ctrl-C contract, carried on `BatchCancelled` with the partial
report. One bad file never kills the batch (broad per-file catch → failure table).

**Load-bearing:** `dated_artifact_path` SELECTS a name without creating it, so colliding stems
(`лекция.mp4`+`.mkv`) would race and one mp3 would silently overwrite the other. `_plan_output_paths` claims
every name up front, single-threaded, **in memory** (`dedup_path(taken=...)`), then hands each worker an
explicit `extract_audio(out_path=...)`. It writes nothing — an on-disk claim survived `kill -9` as a fake
artifact that poisoned dedup forever. `.part` could NOT be the claim marker: dedup deliberately cannot see it.
**One publish scheme (`.part`→`os.replace`); do not add a second.** Cancel is bounded — SIGTERM, then `kill()`
after 5s, so a deaf child can't hang the console. Main-menu keys are now SEMANTIC (`local`/`batch`/…) like the
submenus (TD-12); the operator still presses digits — `ui._bind_number_keys` is positional.

## Shipped in v2.2.0 (2026-08-05) — full entry in [CHANGELOG.md](../CHANGELOG.md)

**Essence block** (reconcile writes it from phase prose only, never a second transcript read; doc opens with
«Суть», closes with «Ориентиры для ответов» so a question never hands you its answer — reconcile runs ALWAYS
incl. K=1, so short input is **2 calls, not 1**) and the **menu-#1 MP3 options**, where a video asks before
keeping the MP3 but an mp3 source is never asked per-run. **That asymmetry is intentional; do not "fix" it.**

## Config / behavior notes

- Tools `emit_phase`/`emit_reconcile`; the inline `[интерпретация]:` marker is plain text, surviving MD+PDF.
  **Default tier `economy` (Haiku)**. **Prompt is data** (`config/models.toml`), SCHEMAs stay in code.
- **Model currency:** tiers pin floating aliases (`claude-haiku-4-5`, `-sonnet-5`, `-opus-4-8`) —
  always-latest, reproducibility intentionally dropped. `scripts/check-models.py` (standalone,
  killswitch-safe) reports retired/valid + context drift vs `GET /v1/models`, derives `context_window`, flags
  a tier `prices_unverified` on a generation bump. **Prices stay manual** — no pricing endpoint; while
  flagged `_run_summary` prints a one-time notice ($0.50 gate unchanged). `balanced` verified 2026-08-03.
- **Anchors are TEXTUAL, not links** — `[HH:MM:SS]` inline in prose (TD-19); the validator guarantees each
  resolves to a real transcript block.
- **Output:** recovery `.json` → `output/summaries/raw/`, resume partial → `raw/.resume/`, readable
  `.pdf`/`.md` → `output/summaries/`, one shared stem. `output/` is gitignored and machine-local, so a
  Windows-produced summary is only re-validatable in WSL against ITS transcript. **API key:**
  `ANTHROPIC_API_KEY` env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are Windows-only.

## Next

1. **Run the batch on Windows** — the only untested surface is the one WSL cannot reach: native
   `askopenfilenames` multi-select + real ffmpeg under a worker pool. Everything else is gate-covered.
2. **First live run of `check-models.py`** on a box with a key: seeds `config/model_names.json`, confirms the
   real `GET /v1/models` shape. Run one is a pure baseline; the signal starts on run two.
3. **T8 (P3):** offline LLM-judge groundedness eval — trigger-gated, eval-suite only, never per-run.

## Blockers / debts / SoT

- **Blockers:** none. **Debts:** [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md) **fully closed** — TD-1..21, shut
  2026-08-03 (TD-7 non-TTY UI + TD-20 PDF polish WONTFIX).
- **SoT:** locked build spec [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates,
  operator-approved) · original SOW [`ТЗ`](./archive/ТЗ_аудио_резюме_приложение.md) · plan
  `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared).
