# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

### TD-16 — v2 direct transcript synthesis (fidelity > completeness)

Severity: MEDIUM · Created 2026-06-26 (operator principle reversal, eng-reviewed) · Trigger: **active — code
complete + reviewed; closes on operator paid reference-run acceptance** · SoT: `~/.claude/plans/elegant-prancing-journal.md` + this file

**What.** The transcript is ground truth, read DIRECTLY into a faithful synthesis (one hop) — no
map-extraction, coverage checklist, or grouping. `chunk.plan_phases` (computed K, contiguous, overlap=0;
short=K=1) → `synthesize_summary` ×K sequential forward-only (prior headings + prior phase's tail prose as
do-not-restate context) → per-phase `validate_anchors` (accept/snap-2s/drop vs that phase's real timecodes;
strips inline `[HH:MM:SS]`) → concatenate decisions/actions → reconcile (title/core_idea/main_themes +
contradiction flag, K>1) → header anchor-validation → one readable doc. The CLAUDE.md principle + 5 fidelity
properties + "every anchor resolves" are the acceptance bar. Supersedes TD-5 + TD-15 (both closed + deleted).

**Shipped.** `7ad2185` T1–T4 (config/prompts, `plan_phases`, `SynthesisSection`/anchors/`emit_phase`/
`emit_reconcile`/`validate_anchors`, render) · `1fdd3de` T5-A (`summarize_auto` rewired, cost preview = K
(+1 reconcile if K>1), artifact-resume to `raw/.resume/<stem>.json`) · `43b7daf` T5-B (isolated/git-revertable:
deleted single-pass+map-reduce+grouping + old eval + `regroup.py`, ~2.5k LOC) · `2b8ab6f` T6/T7 docs.

**Reviewed (`/review`, 2026-06-27, multi-agent). Tier-1 fixed** (gate green): cross-phase merge dropped
(could collapse distinct same-worded points → violated fidelity #4); per-phase (not global) anchor
validation; inline `[HH:MM:SS]` in prose + reconcile header now validated; resume accepts `len==K` (no
all-phase re-pay on a reconcile crash); MD title injection-collapsed.

**Known limitation (accepted, decision #2).** Artifact-resume is keyed by `source_stem` — a failed run +
edited transcript reused under the same filename would splice old + new phase prose (no transcript
fingerprint; the transcript IS the checkpoint). Mitigation: delete `raw/.resume/` before re-summarizing an
edited transcript.

**Remaining.** (a) **Paid reference acceptance (operator, Windows; `ANTHROPIC_API_KEY` not in WSL):** one
~3h run; check the 5 fidelity properties by eye, jump each anchor, confirm ≤5 pp + readable — the closure
gate. (b) **Tier-2 review cleanup, before v2.0.0:** remove dead `cost.estimate_cost`/`estimate_cost_chunked`
(+ tests) + `smoke_run`'s deleted-path `guard.check_overflow`; fix ~8 stale docstrings (`plan_chunks`/
`build_request`/`section_timecodes`/`{unassigned}`/"per-section bullets"); suppress the K=1 duplicate heading
(title == sole section heading); inject the `{unassigned}` owner label per-language (RU docs leak the English
placeholder). (c) **T8 (P3 follow-on):** offline LLM-judge groundedness eval — trigger-gated, eval-suite
only, never per-run (replaces the retired structural eval; until it lands the manual paid run is the only
quality gate). **Parallel synthesis is OUT OF SCOPE** (operator, 2026-06-26).

**When to close.** When the paid reference run is operator-accepted. Tier-2 cleanup ships with v2.0.0; T8 is
a separate follow-on, not a blocker.

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

### TD-12 — "What should EchoGist produce?" menu is misleading + has a gap (REOPENED)

Severity: MEDIUM · Reopened 2026-06-27 (operator-raised; first closed 2026-06-21) · Trigger: **active — operator
design call needed** · SoT: this file

The action menu (`menu.py:333-410`) misrepresents what happens. Non-mp3 `_ACTION_CHOICES` = Summary / MP3 only /
Both (MP3 + summary) / ← Back; mp3 `_MP3_ACTION_CHOICES` = Summary / Transcript only / ← Back. Real semantics:
"Summary" always extracts audio internally to transcribe + summarize, discards the MP3, and always saves the
transcript checkpoint; "Both" = same but KEEPS the MP3; "MP3 only" = extract + keep MP3, stop. So the labels imply
"Summary" needs no audio (nonsense for video — extraction is always required to transcribe); the only real
distinction is whether the MP3 is KEPT. GAP: non-mp3 inputs have NO "Transcript only" option (mp3 does) — you
can't transcribe-and-stop on a video without paying for a summary. Redesign to be honest about what's KEPT and
cover all variants (likely Summary / Transcript only / MP3 only / MP3 + summary); remove the misleading framing.
Needs an operator design call on the exact wording/option set (consider `/office-hours`).

### TD-14 — Folder-open hierarchy reveals the wrong directory (REOPENED)

Severity: LOW · Reopened 2026-06-27 (operator-raised; first closed 2026-06-23) · Trigger: **active** · SoT: this file

`reveal_dir` is once-per-launch (`self._revealed`). `_transcribe_to_checkpoint` (`menu.py:323`) reveals
`output/transcripts`; MP3-only (`menu.py:404`) reveals `output/audio`; `_run_summary` reveals nothing — so after a
summary the TRANSCRIPTS folder pops, which is wrong. Operator wants: NEVER open transcripts; reveal
`output/summaries` (highest priority) or `output/audio`, priority summaries > audio. FIX: remove the transcripts
reveal from `_transcribe_to_checkpoint`; reveal `output/summaries` after a successful summary in `_run_summary`;
keep the audio reveal for MP3-only; once-per-launch + summaries-wins-over-audio.

---

## Closed debts (compact — verbose history in git)

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
- **TD-14 — Open Explorer at the saved folder** ✓ CLOSED 2026-06-23, ⚠ REOPENED 2026-06-27 (see Open debts — wrong
  reveal hierarchy). Original close: `UI.reveal_dir` (`nt`-guarded, once/launch); extended 06-25 to MP3-only flow +
  no-focus-steal `ShellExecuteW(SW_SHOWNOACTIVATE)`. `f4fd2ca`.
- **TD-12 — `.mp3` input offered no-op MP3/Both actions** ✓ CLOSED 2026-06-21, ⚠ REOPENED 2026-06-27 (see Open
  debts — menu misrepresents what's kept + non-mp3 gap). Original close: mp3 gets a trimmed menu
  (Summary / Transcript only / ← Back) via `extract.is_mp3`. `f4fd2ca`, revised `738cf9e`.
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
