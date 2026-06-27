# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

### TD-17 — Progress bar shows 100% on a failed stage

Severity: LOW · Created 2026-06-27 (/review red-team finding, deferred) · Trigger: operator review of the
failure UX, or a UI-seam refactor · SoT: this file

The `with ui.progress(...) as bar:` context fills the bar to 100% via `handle.done()` in its `finally` even
when the stage raised mid-way (e.g. an ffmpeg/extract or transcribe failure): the operator sees a full bar
flash immediately before the warning/error panel. Cosmetic only — the error path is correct (warn + degrade or
fatal return to menu); no state is wrong. It's shared `ProgressHandle` behavior, so it affects the transcribe
bar too, not just the new TD-12 MP3 conversion bar — out of scope for the TD-12 menu change. Fix when the UI
seam is next touched: have `done()` (or the context exit) skip the fill when leaving via an exception, or pass
the bar's last-known fraction through instead of forcing 1.0.

### TD-20 — PDF visual polish (beauty + human readability)

Severity: LOW · Created 2026-06-27 (operator eyes-review, run #1) · Trigger: a render-polish pass · SoT: this file

The current `_render_pdf` is functional but plain (single DejaVuSans family, flat 18/13/11/9pt sizes, minimal
spacing rhythm). Operator wants it more readable + visually finished. Scope when reopened: typographic
hierarchy (heading weight/size/spacing rhythm), paragraph leading, anchor styling (quieter/greyed), margins,
and section separation. Pairs with TD-19 (the anchor footer is part of the same readability pass). Markdown
path is unaffected. Open-ended — no single correct answer; needs a design eye, not a one-line fix.

**IMPLEMENTED 2026-06-27 (first pass):** a typographic pass — title 20pt + hairline rule, headings 14pt, body
11pt at 6.5 leading, near-black ink (`_INK`/`_INK_STRONG`) with a muted rule tone, larger spacing rhythm. Plain
but readable now. **Operator accepted the rendered PDF on the 2026-06-27 re-run** ("reads well; a lot of text
but fine for a 3h lecture — leave it"). Stays open at LOW for an optional future iteration; no longer blocks.

### TD-7 — Plain-input / non-TTY fallback UI deferred from v1.0

Severity: LOW · Created 2026-06-17 · Trigger: a non-interactive (piped/redirected) run is ever needed · SoT: this file

The console UX (questionary+rich) needs a TTY; the adapter detects a non-TTY and exits cleanly
(`NotInteractiveError`) rather than carrying a second `input()`/`print` UI for a case that never occurs in a
single-operator interactive tool. The `UI` Protocol seam leaves a clean place to add a `PlainUI` if a
batch/scripted invocation ever becomes real.

### TD-9 — Cheap-call "press Enter to summarize" beat dropped in v1.0

Severity: LOW · Created 2026-06-17 · Trigger: operator review of the confirm UX · SoT: this file

Plan §6.4 wanted an acknowledge beat on the below-threshold path; the build shows the estimate via `ui.info`
and proceeds (`cost.confirm_proceed` returns `True` for cheap calls) — the narrow `confirm(prompt, default)`
seam can't express a non-decision pause without a Y/n widget (which could decline a call that always runs).
The above-threshold gate (explicit confirm, default No) + `$0.50` threshold are unchanged. Reopen by adding a
one-line `ui.text(...)` in `menu._run_summary` — no `cost.py` change.

---

## Closed debts (compact — verbose history in git)

- **TD-16 — v2 direct transcript synthesis (fidelity > completeness)** ✓ CLOSED 2026-06-27 (was MEDIUM, created
  06-26; operator principle reversal, eng-reviewed). The transcript is ground truth, read DIRECTLY into faithful
  prose (one hop): `plan_phases` (computed K, contiguous, overlap=0) → `synthesize_summary` ×K forward-only →
  per-phase `validate_anchors` (accept/snap-2s/drop) → reconcile header (K>1) → one readable doc. Supersedes TD-5
  + TD-15 (deleted in `43b7daf`). Shipped `7ad2185`/`1fdd3de`/`43b7daf`/`2b8ab6f` + `/review` Tier-1/2 (`52a36ef`).
  **Reference run #1 (06-27, $0.39) FAILED coverage** — phase 2 dropped ~22% of the lecture while every automated
  check passed (green anchors ≠ coverage); root cause was a prompt that never compelled span coverage + an
  over-firing "do not restate" block. Coverage-fix prompt landed `ba1b973`. **Re-run (06-27, balanced/Sonnet,
  2:58:57 RU, K=4, $0.5237) operator-ACCEPTED:** phase 2 covers its full 00:44→01:31 span (decider types,
  the four no-decision positions, fit criterion restored); anchors 131/131 + 9/9 resolve (validator log "0 dropped",
  re-checked offline); fidelity spot-check clean (12/12 sampled claims grounded, faithful negative stance, no
  fabrication). "the author-named point" absent because Whisper garbled the surname upstream (the concept — V1 / decision
  speed — IS covered); not a synthesis miss. T8 offline LLM-judge eval remains a P3 follow-on. Parallel synthesis
  OUT OF SCOPE (operator). Known limit (accepted): artifact-resume is keyed by `source_stem` — delete
  `raw/.resume/` before re-summarizing an edited transcript.
- **TD-18 — Phase headings had no document meta-frame** ✓ CLOSED 2026-06-27 (was MEDIUM). `emit_reconcile` returns
  a `phase_headings` array; `_apply_normalized_headings` swaps the forward-only headings into one coherent outline,
  fail-soft on count/empty mismatch; K=1 keeps its heading; `validate_anchors` strips inline timecodes from
  headings too. Validated on the re-run — the 4 headings read as one arc (`5d0df0c`).
- **TD-19 — Per-phase anchor footer dump** ✓ CLOSED 2026-06-27 (was LOW). Dropped the footer in PDF + Markdown;
  the prompt weaves a handful of `[HH:MM:SS]` INLINE in prose, the full `anchors` array stays in the `.json`.
  Validated on the re-run (131 inline timecodes, all resolve) (`5d0df0c`).
- **TD-21 — Pre-call cost estimate margin** ✓ CLOSED 2026-06-27 (was MEDIUM). `estimate_cost_synthesis` projects
  output from `per_call_output_tokens` (= `[guard].output_tokens_estimate`), not the `max_output_tokens` cap, and
  drops the phantom `K × cap` reconcile-input — killing the old ~2.4× ceiling. The re-run exposed the TD-21 value
  `2800` (fit to truncated run #1) as an UNDERSHOOT — estimate ran 0.91× the bill, breaking the high bias.
  Recalibrated `output_tokens_estimate` 2800 → 4600 (~25% over the re-run's ~3.7k/call mean): estimate now ~1.2×
  ($0.6330 vs $0.5237). Local offline arithmetic, no new paid run needed. `5d0df0c`, `230deee`.
- **TD-15 — Summary readability: hierarchical grouping** ✓ CLOSED 2026-06-26 (was MEDIUM, created 06-25).
  Superseded by TD-16: grouping navigated a 221-point map-reduce wall that direct synthesis never produces;
  the grouping code was deleted in `43b7daf`. Phase 1 render wins (paragraphing, owner-suppression) survive.
- **TD-5 — Chunked map-reduce summarization** ✓ CLOSED 2026-06-26 (was LOW, created 06-14). Shipped + paid-
  validated (179-min RU, $1.46, 221→221) but output was an unreadable flat wall; operator reversed to
  fidelity-over-completeness (TD-16). Machinery deleted in `43b7daf`. The two live bugs it surfaced (Claude
  4.x `temperature` 400; forced-tool extraction match) survive in the v2 caller.
- **TD-6 — Title can leak the source language** ✓ CLOSED 2026-06-25 (was LOW). Hardened the `title`
  instruction + schema desc to force the target language; paid T10 live gate confirmed. `94d7e93`, `5c8fbd4`.
- **TD-10 — File input forced manual path typing** ✓ CLOSED 2026-06-23 (was HIGH). `UI.pick_file` native
  tkinter dialog → `questionary.path()` fallback; last-dir persisted; operator Windows run confirmed.
  `8629ec2`/`8c67b68`/`9e8d768`.
- **TD-11 — Accumulated menu chrome** ✓ CLOSED 2026-06-23 (was MEDIUM). `UI.clear()` on flow entry (not
  menu-loop-top); full TUI ruled out of scope. `f4fd2ca`.
- **TD-13 — Submenu back-navigation** ✓ CLOSED 2026-06-23 (was MEDIUM). Explicit `← Back` entries; ESC stays
  exit (distinguishing it would risk the `_ask` cancel/exit contract). `f4fd2ca`.
- **TD-14 — Open Explorer at the saved folder** ✓ CLOSED 2026-06-23, REOPENED + RE-CLOSED 2026-06-27. Reopen: the
  once-per-launch `_revealed` bool popped `transcripts` after a summary (transcribe revealed first; `_run_summary`
  revealed nothing). Fix: `reveal_dir(path, *, priority)` — a higher priority supersedes a lower one already shown
  this launch; MP3-only pops `audio`, `_run_summary` pops `summaries` (wins over audio). NOTE: the original
  "transcripts are never revealed" rule was reversed by TD-12 — a transcript-only run now pops `transcripts`
  (`REVEAL_SUMMARY(3) > REVEAL_TRANSCRIPT(2) > REVEAL_AUDIO(1)`); the reveal lives in the transcript branch of
  `_flow_local_file`, never in `_transcribe_to_checkpoint`. Original close: `nt`-guarded once/launch + no-focus-steal
  `ShellExecuteW(SW_SHOWNOACTIVATE)`. `f4fd2ca`, 06-25 MP3-only ext.
- **TD-12 — "What should EchoGist produce?" menu misleading + gap** ✓ CLOSED 2026-06-21, REOPENED + RE-CLOSED
  2026-06-27 (design /office-hours, eng-reviewed). Reopen: non-mp3 menu (Summary / MP3 only / Both / Back) implied
  Summary needed no audio and had NO transcript-only path. Fix (MP3-as-baseline): video menu = `MP3 only / Summary /
  Transcript / Back` with semantic keys (`mp3`/`summary`/`transcript`); every video branch extracts + KEEPS the MP3
  in `output/audio` (MP3-only failure FATAL, Summary/Transcript DEGRADE — warn + continue, catching both
  ExtractError and bare OSError so a locked-folder `os.replace` can't abort the run; /review red-team fix). `.mp3` menu unchanged
  (`Summary / Transcript only`). Transcript-only now reveals `transcripts` (see TD-14). Source decodes the container
  directly (transcript quality unchanged); the MP3 is an added artifact. Design doc:
  `~/.gstack/projects/echogist/pc-main-design-20260627-104405.md`. Original close: mp3 trimmed menu via
  `extract.is_mp3`. `f4fd2ca`, `738cf9e`.
- **TD-8 — `setuptools<81` pin** ✓ CLOSED 2026-06-21 (premise dismissed). No such pin ever existed; lockfile
  ships `setuptools==82.0.1` and passes. The cuDNN↔ctranslate2 tripwire (the pin that matters) is untouched.
- **TD-3 — GPU provisioning automation** ✓ CLOSED 2026-06-16 (was MEDIUM). `run.bat` provisions idempotently;
  `gpu.register_cuda_libraries` + `PATH`/`ctypes.WinDLL` pin fixed ctranslate2's bare-name cuBLAS load on the
  4060. `b024368`, `173541d`, `e9a9cfb`.
- **TD-4 — GPU transcription speed unmeasured** ✓ CLOSED 2026-06-16 (was LOW). Warm: `int8_float16` =
  10.11x realtime / 3.46 GB (kept — same speed, ~2 GB less VRAM, better RU). `b93fd72`, `6bbb1fe`.
- **TD-1 — Whisper model runtime download** ✓ CLOSED 2026-06-15 (was MEDIUM/HIGH). Root cause = Xet transport
  (not RU region); fetch vanilla `Systran/faster-whisper-large-v3` with `HF_HUB_DISABLE_XET=1`, load
  `int8_float16`. Model arrives outside the lockfile (accepted). `1f25ef1`…`2748196`.
- **TD-2 — YouTube ingestion** ✓ CLOSED 2026-06-14 (was HIGH, obsolete). Eng-review falsified the bypass
  hypothesis; dropped all online/link ingestion — local files + saved transcript only, offline, no-credential.
