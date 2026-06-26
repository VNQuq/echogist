# Technical Debt Registry

**Type:** living document
**Purpose:** the single place for all deliberate deferred decisions. Every debt
has a **deadline** or **trigger** for closure.

---

## Registry principles

1. **Debt ≠ forgotten.** Every item is deliberately deferred with a fixed
   date/condition for closure.
2. **Severity** = HIGH / MEDIUM / LOW. HIGH blocks the next phase. MEDIUM
   degrades quality. LOW — cosmetics.
3. **Closure.** On closing: status `closed`, commit reference. Closed records are
   kept in the very compact format below; full verbose history lives in git.

**Record templates:** *Open:* `### <ID> — <title>` + header (Severity · Created ·
Deadline/Trigger · SoT) + **What** / **Why deferred** / **When to open**.
*Closed:* `### <ID> — <title> ✓ CLOSED` + header (Severity (was) · Created → Closed)
+ 2–3 sentences: the fork, the decision, commits.

---

## Open debts

### TD-16 — v2 direct transcript synthesis (fidelity > completeness)

Severity: MEDIUM · Created 2026-06-26 (operator principle reversal, eng-reviewed) · Trigger: **active — T1–T7 shipped; closes on operator paid reference-run acceptance** · SoT: `~/.claude/plans/elegant-prancing-journal.md` + this file

**What.** The principle reversal: the transcript is ground truth, read DIRECTLY into a faithful
synthesis (one hop) — no map-extraction, no coverage checklist, no grouping. Deterministic phase-split
(`chunk.plan_phases`, computed K, contiguous, overlap=0; short = K=1) → `summarize.synthesize_summary`
×K sequential forward-only (prior headings + prior phase's real tail prose as do-not-restate context) →
anchor-preserving `_merge_decisions`/`_merge_action_items` → reconcile pass (title/core_idea/main_themes
+ contradiction flag, K>1 only) → deterministic `validate_anchors` (accept/snap-2s/drop vs real block
timecodes — the only live fidelity check). Supersedes TD-5 (map-reduce) and TD-15 (group-keep-all); both
are CLOSED + their code deleted. The CLAUDE.md principle + the 5 fidelity properties are recorded.

**Shipped.** **T1–T4** (`7ad2185`, additive): synthesis/reconcile config + prompts, `plan_phases` +
shared binning, `SynthesisSection`/anchors/`emit_phase`/`emit_reconcile`/`validate_anchors`, synthesis
render. **T5 commit A** (`1fdd3de`): `summarize_auto` rewired to phase-split + synthesis for ALL
material; cost preview = K (+1 fixed reconcile when K>1); artifact-resume (each completed phase persists
to `raw/.resume/<stem>.json`, a re-run reloads the valid prefix and skips done phases — no job engine).
**T5 commit B** (`43b7daf`, isolated + git-revertable, decision #9): deleted single-pass/map-reduce/
grouping (functions, schemas, retired `Summary` fields + `SectionMarker`/`PointGroup`/`SectionGroup`,
chunk/config knobs, models.toml prompts) + the old structural-quality eval + `regroup.py`. **T6/T7**
(docs): this entry + the CLAUDE.md principle reversal & 5 fidelity properties.

**Known limitation (accepted, matches decision #2).** Artifact-resume is keyed by `source_stem`, so a
*failed run + edited transcript reused under the same filename* would splice old phase prose with new
phases. The transcript IS the checkpoint; there is no transcript-fingerprint invalidation (no job
engine). Mitigation: delete `output/summaries/raw/.resume/` before re-summarizing an edited transcript.

**Remaining.** (a) **Paid reference acceptance (operator, Windows — `ANTHROPIC_API_KEY` not in WSL):**
one ~3h-lecture run; check the 5 fidelity properties by eye, jump each anchor to the recording, confirm
≤5 pp and readable. This is the closure gate. (b) **T8 (P3, TODO):** offline LLM-judge groundedness
eval — trigger-gated, eval-suite only, never per-run (it replaces the retired structural eval; until it
lands the manual paid run is the only quality gate). **Parallel synthesis is OUT OF SCOPE — not
required** (operator, 2026-06-26): sequential synthesis ships and stays.

**When to close.** When the paid reference run is operator-accepted. T8 is a separate follow-on debt, not
a blocker for closing TD-16.

### TD-7 — Plain-input / non-TTY fallback UI deferred from v1.0

Severity: LOW · Created 2026-06-17 (console-UX eng-review) · Trigger: a non-interactive (piped/redirected) run is ever genuinely needed · SoT: this file

**What.** The console UX layer's questionary+rich needs an interactive TTY. The adapter detects a non-TTY at
startup and exits cleanly with a message (`NotInteractiveError`), rather than carrying a second
`input()`/`print` UI for piped runs.

**Why deferred.** EchoGist is a single-operator interactive console tool; there is no piped/CI
invocation in its real use. A full second adapter to serve a case that never occurs is over-eng.

**When to open.** If a batch/scripted/headless menu invocation becomes a real need. The `UI` Protocol
seam already leaves a clean place to add a `PlainUI` without touching the flows.

### TD-9 — Cheap-call "press Enter to summarize" beat dropped in v1.0

Severity: LOW · Created 2026-06-17 (console-UX build) · Trigger: operator review of the confirm UX · SoT: this file

**What.** The engineering plan §6.4 wanted a "press Enter to continue" beat on the below-threshold
cost path. The build shows the estimate via `ui.info` and proceeds with no blocking keypress
(`cost.confirm_proceed` returns `True` for cheap calls). The above-threshold gate (explicit confirm,
default No) and the `$0.50` threshold policy are unchanged.

**Why deferred.** The narrow `confirm(prompt, default) -> bool` seam can't express a non-decision
"acknowledge" beat without a Y/n widget (rejected — N could decline a cheap call that always runs).
Dropping the forced keypress is also less friction in an arrow-key UI.

**When to open.** If the operator wants the explicit pause back, add a one-line `ui.text("Press
Enter to summarize…")` in `menu._run_summary` before the cheap-path proceed — no `cost.py` change.

---

## Closed debts

### TD-15 — Summary readability: hierarchical grouping ✓ CLOSED (superseded)

Severity (was): MEDIUM · Created 2026-06-25 → Closed 2026-06-26 (superseded by TD-16 v2)

The fork was how to make the 221-point map-reduce wall readable: group-keep-all via an index-assignment
`emit_grouping` sub-call (Phase 1 shipped `d13a373`; Phase 2 code done `53b8bbb`, paid-validation pending).
Before Phase 2 was validated, the operator reversed the underlying principle (TD-16 v2): grouping
navigated a wall that direct synthesis never produces, so the whole approach was retired. The grouping
code (`group_summary`/`_assign_by_index`/`PointGroup`/`SectionGroup`/`emit_grouping` + `regroup.py`) was
deleted in TD-16 T5 commit B (`43b7daf`). Phase 1's still-relevant render wins (paragraphing, per-item
unassigned-owner suppression) survive in the v2 render path.

### TD-5 — Chunked map-reduce summarization ✓ CLOSED (superseded + deleted)

Severity (was): LOW · Created 2026-06-14 → Closed 2026-06-26 (superseded by TD-16 v2)

The fork was completeness on long/dense material: QualityBudget-triggered map-reduce (MAP each chunk,
REDUCE by concatenate + conservative dedup, never re-summarize). It shipped and a 179-min RU paid run
validated it ($1.46, 221→221 no collapse), but the output was an unreadable flat wall and the operator
reversed the principle to fidelity-over-completeness (TD-16 v2): direct transcript synthesis, one hop, no
map-extraction. The map-reduce machinery (`summarize`/`summarize_chunked`/the map+reduce builders +
`emit_summary`/`emit_synthesis` schemas, `chunk.plan_chunks`/`needs_chunking`, the `[chunk]`
QualityBudget/overlap knobs) was deleted in TD-16 T5 commit B (`43b7daf`, git-revertable if the v2 paid
reference run fails). The two live bugs it surfaced (the Claude 4.x `temperature` 400; the forced-tool
extraction match) live on in the v2 caller.

### TD-6 — Summary title can leak the source language (not the target) ✓ CLOSED

Severity (was): LOW · Created 2026-06-16 → Closed 2026-06-25 (v1.1.0 T10 live gate)

The fork was that the `[summarize]` prompt asked for `title` "in {language}", but a German clip
summarized to Russian returned a German title with a correctly-Russian body (`claude-haiku-4-5`, T13
smoke) — the title anchored to the source language when source ≠ target. Decision: harden the `title`
instruction in `config/models.toml` and the schema field description in `summarize.py` — the title must
be written in the target language even if the material is spoken in another language; never copy a
source-language phrase. The paid T10 live gate on real Sonnet (`2 passed`, cross-language input)
confirmed the fix and shipped in v1.1.0. Commits: 94d7e93, 5c8fbd4.

### TD-10 — File input forced manual path typing (no picker) ✓ CLOSED

Severity (was): HIGH · Created 2026-06-17 → Closed 2026-06-23 (operator Windows acceptance)

The fork was how to kill the friction of hand-typing the audio/video path on the most-used action.
Decision: `UI.pick_file` opens a native tkinter "Open File" dialog (lazy, dual-guarded
`ImportError`/`TclError`) → `questionary.path()` fallback; last-used dir persisted to gitignored
`config/state.json`; pure `resolve_initial_dir` ladder (`last → ~/Downloads → ~`, never cwd). Built
`8629ec2`/`8c67b68`/`9e8d768` (T1–T4, WSL/CI exercises fallback + StubUI). The T5 live-dialog gate
closed on the operator's Windows run: the native picker opened, browsed + selected, and Cancel
returned to the menu cleanly.

### TD-11 — Console accumulated menu chrome ✓ CLOSED

Severity (was): MEDIUM · Created 2026-06-21 → Closed 2026-06-23 (operator Windows acceptance)

The fork was how to stop answered prompts and repeated `Current settings` tables piling up in the
scrollback. Decision (pragmatic 80/20, office-hours `pc-main-design-20260621-201432.md`): `UI.clear()`
on entry to each flow (`_flow_local_file`/`_flow_saved_transcript`/`_flow_settings`), not a
menu-loop-top clear (which would wipe a finished flow's result line). The full two-region TUI was
ruled out of scope (questionary won't live inside a `rich.Live`). Built `f4fd2ca`; `console.clear()`
confirmed rendering cleanly on the operator's Windows run.

### TD-13 — Submenu back-navigation ✓ CLOSED

Severity (was): MEDIUM · Created 2026-06-21 → Closed 2026-06-23 (operator Windows acceptance)

The fork was how to give one-way submenus a way back. Decision: explicit `← Back` entries on the
action menu, transcript picker, and Settings (one consistent back vocabulary), built `f4fd2ca` and
confirmed working on the operator's Windows run. Residual note (not debt): ESC still maps to exit
(questionary returns the same `None` as Ctrl-C/Ctrl-D, documented in `run_menu`); distinguishing
ESC-as-back needs custom prompt_toolkit bindings that would risk the load-bearing `_ask` cancel/exit
contract — open only if `← Back` ever proves insufficient.

### TD-14 — Open Explorer at the transcript folder after first save ✓ CLOSED

Severity (was): LOW · Created 2026-06-21 → Closed 2026-06-23 (operator Windows acceptance)

The fork was whether to surface saved transcripts in Explorer automatically. Decision: `UI.reveal_dir`
— `os.startfile` guarded on `os.name == "nt"`, wrapped in `suppress(OSError)`, fired once per launch
(`self._revealed`) from `_transcribe_to_checkpoint` after the "Saved transcript:" line (a NEW save
pops, a re-summarize does not). Built `f4fd2ca`; the live Explorer pop confirmed on the operator's
Windows run. **Extended 2026-06-25:** the reveal now also fires for the MP3-only flow
(`output/audio`) — transcript flows still pop `output/transcripts`, one folder per launch by menu
choice — and the once-deferred no-focus-steal open landed: `reveal_dir` uses
`ShellExecuteW(SW_SHOWNOACTIVATE)` (falls back to `os.startfile`, and logs a `Folder ready:` line on
failure instead of swallowing it). 4060-Windows-verified 2026-06-25 — the folder pops behind the console.

### TD-12 — `.mp3` input offered MP3/Both actions (extraction is a no-op there) ✓ CLOSED

Severity (was): LOW · Created 2026-06-21 → Closed 2026-06-21 (`f4fd2ca`, revised `738cf9e`)

The fork was what an mp3 input should offer, since MP3-only/Both are no-ops there. First cut
(`f4fd2ca`) skipped the prompt entirely and went straight to summary; the operator then asked to
keep a choice, so `738cf9e` gives an mp3 a *trimmed* menu — **Summary / Transcript only / ← Back**
(MP3-only and Both dropped). `_flow_local_file` branches on `extract.is_mp3(source)` to pick the
menu; "Transcript only" saves the checkpoint and stops short of the network call. Logic-only,
tested (`test_mp3_source_summary`, `test_mp3_source_transcript_only`). Fully closed.

### TD-8 — `setuptools<81` pin the engineering plan references never existed ✓ CLOSED

Severity (was): LOW · Created 2026-06-17 → Closed 2026-06-21 (no code change — premise dismissed)

The engineering plan §2 / U7 says to "preserve the existing `setuptools<81` pin", but no such pin
ever existed: `requirements.in` never constrained setuptools and `requirements.lock` ships
`setuptools==82.0.1`, which the 4060 runs pass on. The doc premise was stale, so there is nothing
to fix — no defect in the code
or the lockfile. The cuDNN↔ctranslate2 skew tripwire (the pin that actually matters) was always
untouched. Residual note, not debt: if a ctranslate2 `pkg_resources` import failure ever surfaces on
Windows, a deliberate `setuptools` pin in `requirements.in` + re-lock is the lever.

### TD-3 — GPU provisioning the launcher must automate ✓ CLOSED

Severity (was): MEDIUM · Created 2026-06-14 → Closed 2026-06-16 (T13, win-smoke)

The fork was how to make cold-Windows GPU provisioning automated + verifiable. Decision: `run.bat`
provisions DLLs + model idempotently; `gpu.register_cuda_libraries` loads cuDNN/cuBLAS before
`import faster_whisper`; `scripts/win-smoke.bat` scripts the cold-run acceptance. The operator's
first pass on the 4060 closed it — and caught a real bug the WSL path never hit: ctranslate2 lazily
loads cuBLAS by bare name at the first GEMM, ignoring `os.add_dll_directory`. Fixed by prepending the
wheel `bin` dirs to `PATH` and pinning the entry DLLs resident via `ctypes.WinDLL` (173541d). Commits:
b024368, 173541d, e9a9cfb.

### TD-4 — GPU transcription speed unmeasured ✓ CLOSED

Severity (was): LOW · Created 2026-06-14 → Closed 2026-06-16 (T11, 4060 run)

The fork was whether `large-v3` on the 4060 is fast enough and whether int8 costs RU quality.
Measured warm: `int8_float16` = **10.11x realtime, 3.46 GB VRAM** (bar 3.0x → PASS), `float16` =
10.27x, 5.39 GB. Decision: **keep int8_float16** — same speed, ~2 GB less VRAM, and int8 RU quality
beat the float16 control on this run. Report at `docs/measurements/large-v3-int8_float16-2026-06-16.md`.
Commits: b93fd72, 6bbb1fe.

### TD-1 — Whisper model runtime download ✓ CLOSED (root cause: Xet, not region)

Severity (was): MEDIUM/HIGH · Created 2026-06-14 → Closed 2026-06-15 (T3, gate c)

The fork was how to distribute the ~3 GB CT2 large-v3 when `snapshot_download` stalled. Root cause
was the **Xet transport** (hf_hub 1.x auto-routes large files through Xet CAS hosts, which stall on
the operator's route), not RU region-blocking — proven by a `HF_HUB_DISABLE_XET=1` pull at ~10.5 MB/s
(xet-core#446). Decision: fetch vanilla `Systran/faster-whisper-large-v3` (float16) with Xet forced
off; load `int8_float16` at runtime; pre-placed `local_dir` as the offline fallback. Tradeoff
(accepted): the model arrives outside `requirements.lock` (HF checksums, not our hash pins). Commits:
1f25ef1, d7b23c3, 0b350bb, 7cbc5dc, 2748196.

### TD-2 — YouTube ingestion subsystem ✓ CLOSED (obsolete)

Severity (was): HIGH · Created 2026-06-14 → Closed 2026-06-14 (eng-review, scope cut)

The fork was how to build a durable automated YouTube extraction path. Eng-review research falsified
the lead hypothesis (PO tokens no longer bypass the bot-check, yt-dlp OAuth gone, only Firefox-SQLite
cookies work and expire ~2 weeks). Decision: **drop all online/link ingestion** (YouTube + Vimeo + URL)
rather than carry an account/login-maintenance and ban-risk subsystem. Primary and only ingestion is
now local files + saved transcript — fully offline, zero-credential, no-maintenance. Supersedes
decision 0929b279.
