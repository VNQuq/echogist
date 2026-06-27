# Current Context

**Updated:** 2026-06-27 (**TD-16 v2 reviewed; Tier-1 fixes shipped (`52a36ef`); Tier-2 cleanup DONE; TD-12 menu
redesign IMPLEMENTED + multi-agent /review fixes — gate GREEN (357 passed)**). Next: operator paid reference run (TD-16 closure gate) →
**v2.0.0** (breaking; v1.1.0 was last release). TD-14 folder-open fixed; TD-12 (MP3-as-baseline video menu) DONE.
**Authority:** [CLAUDE.md](../CLAUDE.md) · **Max length:** ≤ 2 pages (≈ 60 lines).

---

## Active scope — TD-16 v2: direct transcript synthesis

**Principle (operator, eng-reviewed, in CLAUDE.md):** fidelity > completeness. The transcript is ground
truth, read DIRECTLY into a faithful synthesis (one hop) — no map-extraction, no coverage checklist, no
grouping. The manual operator re-check against the recording + deterministic anchor validation is the
fidelity gate (no LLM-judge). Supersedes TD-5 (map-reduce) and TD-15 (group-keep-all), both closed + deleted.

**Pipeline (the only path):** transcript → `chunk.plan_phases` (computed K, contiguous, overlap=0; short =
K=1) → `summarize.synthesize_summary` ×K sequential forward-only (each phase reads its span + prior headings
+ prior phase's TAIL PROSE as do-not-restate context) → per-phase `validate_anchors` (accept/snap-2s/drop vs
that phase's real timecodes; strips inline `[HH:MM:SS]` too) → concatenate decisions/actions → reconcile
(title/core_idea/main_themes + contradiction flag, K>1 only) → header anchor-validation → one readable doc
(~3–5 pp). ≈5 calls/3h, 1 for short. The 5 fidelity properties + "every anchor resolves to a real timecode"
are the v2 acceptance bar (CLAUDE.md).

## Status

- **T1–T7 shipped on `main`:** `7ad2185` (T1–T4 config/prompts/`plan_phases`/`synthesize_summary`/
  `validate_anchors`/render), `1fdd3de` (T5-A: `summarize_auto` rewired + cost preview + artifact-resume),
  `43b7daf` (T5-B isolated/git-revertable: deleted single-pass+map-reduce+grouping + old eval, ~2.5k LOC),
  `2b8ab6f` (T6/T7 docs).
- **Multi-agent `/review` + Tier-1 fixes shipped (`52a36ef`):** 6 passes (testing/maintainability/security/
  performance/red-team + Claude adversarial; Codex not installed). Tier-1: dropped the cross-phase decision/
  action merge (it could collapse distinct same-worded points → fidelity #4); per-phase anchor validation
  (was global — a coincidental cross-phase match passed); inline `[HH:MM:SS]` in prose + reconcile header now
  validated; resume accepts `len==K` (a reconcile-crash no longer re-pays all K phases); MD title collapsed.
  Same commit compacted CURRENT_CONTEXT + TECHNICAL_DEBT.
- **Tier-2 cleanup DONE — gate GREEN (345 passed).** Removed dead `cost.estimate_cost` +
  `estimate_cost_chunked` (+ their tests; baseline 350 → 345); `guard.overflow_message` wording → v2-neutral;
  schema owner `unassigned`→"leave empty if unstated"; K=1 duplicate heading suppressed in
  `render._markdown`/`_pdf_synthesis_body`; stale docstrings; CHANGELOG `[Unreleased]` rewritten to the v2.0.0
  BREAKING entry; CLAUDE.md `## Release` section + `release.py` docstring de-`/ship`-ed; TD-12/TD-14 reopened
  in TECHNICAL_DEBT.

## Config / behavior notes

- Tool names `emit_phase`/`emit_reconcile`. `{interpretation}` substituted per-language; inline
  `[интерпретация]:` marker is plain text, survives MD+PDF.
- **Default tier = `economy` (Haiku)**; `balanced`/`flagship` in Settings.
- **Output:** recovery `.json` → `output/summaries/raw/`; resume partial → `raw/.resume/`; readable
  `.pdf`/`.md` → `output/summaries/`; triplet shares one stem.
- **API key:** `ANTHROPIC_API_KEY` env first, then gitignored `config/secrets.toml`. Not in WSL — paid runs
  are operator-run on Windows.
- **Prompt is data** (`config/models.toml`); tool SCHEMAs stay in `summarize.py`.

## Next

1. **Paid reference acceptance (operator, Windows) — the TD-16 closure gate:** one ~3h-lecture run; check the
   5 fidelity properties by eye, jump each anchor to the recording, confirm ≤5 pp + readable.
2. **TD-12 — DONE (implemented 2026-06-27, eng-reviewed design).** Video menu = `MP3 only / Summary / Transcript /
   ← Back` with semantic keys; MP3 is the BASELINE (kept on every video Summary+Transcript run; MP3-only failure
   FATAL, Summary/Transcript DEGRADE — warn+continue, catching BOTH ExtractError AND bare OSError per the
   /review red-team finding). `.mp3` menu unchanged. Container decoded directly; MP3 is an
   added artifact. Reveal priority `REVEAL_SUMMARY(3) > REVEAL_TRANSCRIPT(2) > REVEAL_AUDIO(1)`; transcript-only now
   pops `transcripts`. Ships with v2.0.0. Design doc: `~/.gstack/projects/echogist/pc-main-design-20260627-104405.md`.
   - **TD-14 — DONE** (`reveal_dir(priority)`; the "transcripts never revealed" rule was superseded by TD-12).
3. **v2.0.0 release** (breaking — Summary shape changed; VERSION 1.1.0 → 2.0.0). Cut it via the release
   script, NOT `/ship`: bump `VERSION` + write the `## [2.0.0]` `CHANGELOG.md` section, `/cp`, push the
   annotated tag `v2.0.0`, then `python3 scripts/release.py` (PAT in `ECHOGIST_GITHUB_TOKEN`). **T8** offline
   LLM-judge eval = P3 follow-on. **Parallel synthesis = OUT OF SCOPE** (operator, 2026-06-26).

## Dev env

WSL `.venv` (py3.12), GPU stack installed, RTX 4060 visible. Gate: `bash scripts/dev-loop` (ruff + mypy
--strict + pytest) — **GREEN: 357 passed**. `ANTHROPIC_API_KEY`
not set here; network libs lazy/offline so the killswitch holds.
Telemetry off, PROACTIVE false. `/cp` is standing commit+push authorization.

## Blockers / debts / SoT

- **Blockers:** none in-repo — gate green. Closure gate is the operator paid reference run (Windows; `ANTHROPIC_API_KEY` not in WSL).
- **Open debts** → [TECHNICAL_DEBT.md](./TECHNICAL_DEBT.md): **TD-16** (active; closes on paid reference
  acceptance; T8 eval is in its orbit) · TD-7 non-TTY UI (LOW) · TD-9 cheap-call Enter beat (LOW) ·
  TD-17 progress bar 100% on failed stage (LOW).
  **Closed:** TD-1..6, 8, 10, 11, 12, 13, 14, 15.
- **SoT:** plan `~/.claude/plans/elegant-prancing-journal.md` (eng-cleared); build spec (locked)
  [ENGINEERING_PLAN.md](./archive/ENGINEERING_PLAN.md) (TD-16 deviates — operator-approved reversal); original
  SOW [`ТЗ_аудио_резюме_приложение.md`](./archive/ТЗ_аудио_резюме_приложение.md).

## Hard constraints (carry-over)

- API key from env / local config only; never in code/committed.
- Killswitch: `SUMMARIZE` (synthesis + reconcile) is the only network stage; everything left is offline +
  stub-testable. Phase-split, anchor validation, render are local.
- Every push to `main` passes ruff + mypy + tests. (Development is on `main` directly.)
