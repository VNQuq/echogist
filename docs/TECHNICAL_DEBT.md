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

### TD-5 — Chunked map-reduce summarization — SHIPPED (Unreleased, 2026-06-25)

Severity: LOW · Created 2026-06-14 (eng-review) · **Built 2026-06-25 (operator-directed, for idea-completeness on dense material)** · SoT: this file

**Built.** Map-reduce now triggers on a **QualityBudget** (`[chunk]` in models.toml — tokens OR
duration, below the ContextBudget on purpose: a single pass loses the middle of a long context well
before the window fills). `echogist/chunk.py` plans balanced, ~90s-overlapping chunks on block
boundaries; `summarize.summarize_chunked` MAPs each chunk and REDUCEs by concatenate + conservative
dedup (list fields are never re-summarized; only title/overview/core_idea are synthesized).
`summarize_auto` dispatches; cost reflects N+1 calls; the acceptance invariant (`extracted → after
dedup`) is logged per run. Supersedes the original single-pass principle (CLAUDE.md, operator-approved).

**Still cut (intentional).** Per-chunk checkpointing — a failed chunk fails the whole run loud and
retries from the saved transcript (artifact recovery, not a job engine — CLAUDE.md).

**Calibration — RESOLVED (2026-06-25, first paid run).** A 179-min RU lecture (flagship/opus-4-8) ran
clean: 7 chunks at `target_chunk_tokens`=12k with 90s overlap, no segment tripped the `max_tokens` guard
(8192/chunk held), 221 takeaways extracted → 221 after dedup (no collapse), actual **$1.46 vs the $2.35
estimate** (high-bias estimate confirmed). 12k / 90s / 8192 are validated for real RU material. Two live
bugs surfaced + fixed during the run: (a) the whole Claude 4.x family 400s on `temperature` → made it an
optional per-tier `models.toml` field, omitted by default (commit 1c49248); (b) the shared caller
extracted the tool block by a hardcoded `emit_summary`, so the REDUCE `emit_synthesis` reply was skipped
and failed loud with a false "no tool call" → now matches the forced tool from `tool_choice` (commit
53898f4). Map-stage remains lossy *within* a chunk; the reduce is not. **Output readability is now the
binding bottleneck (221-point flat wall) → TD-15.**

**Review residuals (2026-06-25 `/review`, deferred LOW).** Four findings fixed in-branch (per-chunk
overflow guard restored on the chunked path; `_merge_sections` keyed on timecode alone; merge dedup
prefers the non-empty rationale/owner; `estimate_cost_chunked` made a true ceiling at `output_cap`).
Five left open as LOW: (1) `section_timecodes.bullets` is schema-`required` — may nudge the model to
invent bullets on a short overlap fragment, mitigated by prompt guidance; revisit if the live run shows
bloated short-section bullets. (2) The overlap walk in `plan_chunks` spans all earlier blocks within
`overlap_seconds`, not just the previous bin (harmless at the default 90s/~60s blocks; the inline "previous
bin" comment overstates the bound) — only bites under a low `block_seconds` / high `overlap_seconds`
config. (3) Wrapped PDF sub-bullets (`render._subbullet`) lose their indent on the continuation line
(fpdf2 wraps to LMARGIN) — cosmetic, eyeball it in the first live PDF. (4) The chunking decision is
computed twice (menu cost preview + `summarize_auto`) from the same deterministic inputs — no drift
today, but a future estimator/overhead change must touch both; a shared `plan` threaded through `Deps`
would make it one computation. (5) Exact-match dedup + the REDUCE dropping per-chunk overview/core_idea
mean a near-duplicate overlap takeaway or a connecting idea that lived only in a chunk overview can
survive/vanish — accepted as the conservative-over-lossy tradeoff, flagged here for honesty. **(5)
confirmed by the 2026-06-25 run — exact-match dedup let near-dup themes survive (`внутренняя свобода` ⊂
`…независимо от обстоятельств`; `хочу / надо / могу` vs `хочу/могу/надо`); being tightened to
substring/word-order near-dupes in TD-15 Phase 1.** Open any of the rest if a later run surfaces it.

### TD-15 — Summary readability: hierarchical grouping + format pass (TD-5 follow-up)

Severity: MEDIUM · Created 2026-06-25 (operator read of the first paid TD-5 run) · Trigger: **active — Phase 1 next** · SoT: this file

**What.** The TD-5 map-reduce hit its completeness goal but the output is a flat wall: 221 takeaways
(~10+ PDF pages), a 5024-char single-paragraph overview, 69 micro-sections, 63 flat themes with visible
near-dupes, and `Не назначено` on all 30 action items (solo lecture, no owners). Operator read: "easier
to listen to the whole thing myself." Extraction is no longer the bottleneck; **presentation is.**
Operator-approved direction (decision brief 2026-06-25): **group, keep all — NOT compress.** Three phases:

- **Phase 1 — quick wins (NO paid call; re-renders from the saved `raw/*.json`, F13).** (a) drop the
  `owner` line in render when every action item is unassigned; (b) render the overview as real
  paragraphs, not one slab; (c) tighten dedup to catch substring-containment + word-order near-dupes
  (still mechanical, still never-re-summarize). Render + pure-logic only.
- **Phase 2 — hierarchical grouping (NEEDS live calls; ADR below).** Nest every extracted point under
  ~10–15 headings; consolidate the 69 micro-sections into ~10–15 time-ordered macro-sections; cluster the
  63 themes. `Summary` gains additive `takeaway_groups` / `theme_groups`. ADR-trigger (data-model + new
  LLM prompt).
- **Phase 3 — PDF/MD format pass (NO paid call; re-renders from saved JSON).** Render the new hierarchy
  cleanly, typographic polish, the paragraphed overview, de-noised action items. Operator-mandated.

**ADR — Phase 2 grouping (approved 2026-06-25, index-assignment).** Grouping must **assign** points,
never **rewrite** them — else it silently re-summarizes and breaks the TD-5 completeness guarantee.
Decision:
- A new forced-tool reduce sub-call (`emit_grouping`) receives the **numbered** flat point list and
  returns only **headings + the indices** of the points under each — never the point text.
- Each group is **reconstructed verbatim by index** from the original flat list. The model's text is used
  for headings only; points are never taken from the model's output.
- **Completeness invariant** (mechanical, logged like `extracted → after dedup`): every index `1..N`
  appears under exactly one heading. Any unplaced index → a **`Прочее`** catch-all (fail-soft, logged —
  a forgotten point is never lost and a paid run is never nuked). Log line, e.g.
  `Grouped 221 points into 13 sections (0 orphaned)`.
- **Additive overlay with flat-list fallback.** The flat, verified `key_takeaways` tuple stays the
  canonical complete list (the completeness guarantee is unchanged); `takeaway_groups` is a presentation
  layer alongside it. Render prefers the groups; if grouping returns empty/garbage, render falls back to
  the flat list. Grouping structurally cannot endanger completeness.
- Cost: adds 1–2 small reduce calls (index-list output is tiny); the "N+1 cloud calls" UX copy updates.

**Why deferred / sequenced.** Phase 1 + 3 ship readability wins with zero extra API spend (re-render the
existing 221-point JSON). Phase 2 is the ADR-trigger (`Summary` change ripples into `save_raw_result`
JSON, render T7, and the test suite) and the only phase needing live calls — gated on this ADR per
CLAUDE.md ("do not skip review gates for LLM prompts"). Phase order **1 → 2 → 3 approved**.

**When to open / close.** Active now. Close when all three phases land and a re-render (Phase 1+3) plus
one paid grouped run (Phase 2) are operator-accepted. Stays inside the CLAUDE.md completeness principle —
no amendment needed (group-keep-all, never re-summarize).

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
