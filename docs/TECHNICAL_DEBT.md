# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

### TD-16 — v2 direct transcript synthesis (fidelity > completeness)

Severity: MEDIUM · Created 2026-06-26 (operator principle reversal, eng-reviewed) · Trigger: **active — first
paid reference run FAILED on coverage (2026-06-27); prompt fix applied; closes on a clean re-run** · SoT: `~/.claude/plans/elegant-prancing-journal.md` + this file

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

**Reference run #1 (2026-06-27) — FAILED on coverage (fidelity property #5).** Balanced/Sonnet, 2:58:57 RU
lecture, K=4 + reconcile, $0.39 actual. Anchors validated green (phase 2: 3 exact + 37 snapped, 0 dropped),
BUT **phase 2 (00:45:52→01:27:30) synthesized only its first ~2 min** and dropped ~34 min of substantive
talk (the author-named point, the decider typology, the four no-decision positions A/B/
C/D, the fit criterion criterion). ~22% of the lecture vanished while every automated check passed —
green anchors do NOT imply coverage. Root cause (code-confirmed, not a chunk/truncation bug): the
`synthesis_system_prompt` never compelled coverage, and the `PRIOR CONTEXT` "do not restate" block
over-fired. Phase 2 was the ONLY phase whose opening continued the prior phase's closing theme (the
catastrophe funnel) — the only one that collapsed (1-for-1 correlation). **Fix applied** (`config/models.toml`,
prompt-only, uncommitted): added an explicit ENTIRE-span coverage directive (length proportional to content)
+ narrowed do-not-restate ("continuity ≠ omission; new detail on a resumed theme MUST be synthesized in
full"). Verification = a re-run where phase 2 covers its span. `tests/test_summarize.py`+`test_config.py`
green (no prose pinned).

**Known limitation (accepted, decision #2).** Artifact-resume is keyed by `source_stem` — a failed run +
edited transcript reused under the same filename would splice old + new phase prose (no transcript
fingerprint; the transcript IS the checkpoint). Mitigation: delete `raw/.resume/` before re-summarizing an
edited transcript.

**Remaining.** (a) **Paid reference RE-RUN (operator, Windows; `ANTHROPIC_API_KEY` not in WSL):** re-run the
same ~3h transcript with the coverage fix; confirm phase 2 now covers 00:55→01:27, then check the 5 fidelity
properties by eye, jump each anchor, confirm ≤5 pp + readable — the closure gate. (b) **Tier-2 review cleanup
— DONE** (`52a36ef` Tier-1 + the Tier-2 pass; gate green 357). (c) **T8 (P3 follow-on):** offline LLM-judge
groundedness eval — trigger-gated, eval-suite only, never per-run (replaces the retired structural eval;
until it lands the manual paid run is the only quality gate). Polish surfaced by run #1 is tracked separately:
TD-18 (heading meta-frame), TD-19 (anchor footer dump), TD-20 (PDF polish), TD-21 (cost estimate margin) —
none block closure. **Parallel synthesis is OUT OF SCOPE** (operator, 2026-06-26).

**When to close.** When a clean paid re-run is operator-accepted. T8 + TD-18..21 are follow-ons, not blockers.

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

### TD-18 — Phase headings are local, with no document meta-frame

Severity: MEDIUM · Created 2026-06-27 (operator eyes-review, run #1) · Trigger: operator review of multi-phase
readability, or a synthesis-prompt revision · SoT: this file

Each `emit_phase` call writes its `heading` from a forward-only LOCAL view (it sees only prior headings as
context, never the document's title/core_idea), and the reconcile pass writes title/core_idea/main_themes but
NEVER normalizes the phase headings. Result: a flat document title + N independently-worded block headings
that describe their span by content but don't cohere into a meta-frame — the operator reads them as
disjointed ("заголовок блоков по смыслу, но не заголовок мета-рамки"). Options when reopened: (a) have the
reconcile pass also rewrite/normalize the phase headings into one hierarchy (cheap — reconcile already reads
all phase prose, no transcript re-read, no fidelity cost since headings aren't anchored claims); (b) feed the
running title/frame forward into each phase call (raises per-phase coupling). (a) is the lighter touch.

### TD-19 — Per-phase anchor footer dump is redundant noise

Severity: LOW · Created 2026-06-27 (operator eyes-review, run #1) · Trigger: render polish (pairs with TD-20)
· SoT: this file

`render._anchor_line` (PDF) / the `*· · ·*` line (Markdown) print the WHOLE validated `anchors` array as a
small footer under each phase's prose — e.g. 37 timecodes in a row for phase 4. But the prose already carries
its `[HH:MM:SS]` anchors INLINE (validated by the same pass), so the footer is a redundant wall of timecodes
the operator flagged as ugly. Fix when render is next touched: drop the footer entirely (inline anchors are
the citation), OR show only anchors NOT already present inline in that phase's prose. Validation logic is
untouched either way — this is presentation only.

### TD-20 — PDF visual polish (beauty + human readability)

Severity: LOW · Created 2026-06-27 (operator eyes-review, run #1) · Trigger: a render-polish pass · SoT: this file

The current `_render_pdf` is functional but plain (single DejaVuSans family, flat 18/13/11/9pt sizes, minimal
spacing rhythm). Operator wants it more readable + visually finished. Scope when reopened: typographic
hierarchy (heading weight/size/spacing rhythm), paragraph leading, anchor styling (quieter/greyed), margins,
and section separation. Pairs with TD-19 (the anchor footer is part of the same readability pass). Markdown
path is unaffected. Open-ended — no single correct answer; needs a design eye, not a one-line fix.

### TD-21 — Pre-call cost estimate runs ~2.4× the actual bill

Severity: MEDIUM · Created 2026-06-27 (operator eyes-review, run #1) · Trigger: operator-approved (wants
~15% over actual, not a 2× ceiling) · SoT: this file

`cost.estimate_cost_synthesis` is a deliberate worst-case CEILING: it prices every call's output at the full
`max_output_tokens` cap (`output_cap × (K+1)` = 8192×5 = 40,960 on run #1) AND adds a phantom
`K × output_cap` reconcile-input (32,768). Run #1: estimate 112,965 in + 40,960 out = $0.95 vs actual 75,172
in + 11,079 out = $0.39 — **2.4×**, and it tripped the $0.55 threshold confirm unnecessarily. Operator wants
the estimate ~15% above the real bill, not double. Fix: project output from a realistic per-call figure
(observed output is a small fraction of the cap on a faithful-prose contract — e.g. base on
`[guard].output_tokens_estimate` per call, or a measured per-phase mean) and replace the `K × output_cap`
reconcile-input with the actual phase-prose size (it's known locally before the reconcile call). NOTE: this is
a conscious shift from "true ceiling, never below the bill" to "tight estimate + margin" — keep a modest
high-bias (~15%) so the quote still rarely undershoots. The GUARD's Cyrillic-high INPUT/overflow estimate is
SEPARATE and stays (CLAUDE.md "estimate Cyrillic high" is about catching over-long transcripts, not cost).

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
