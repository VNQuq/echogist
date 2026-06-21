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

### TD-5 — Chunked map-reduce summarization deferred from v1

Severity: LOW · Created 2026-06-14 (eng-review) · Trigger: first real transcript that exceeds the single-pass context budget · SoT: this file

**What.** v1 summarizes the whole transcript in one structured call. A 1.5–2.5h transcript
(~35–40K tokens) fits one call in every tier, so chunked map-reduce + per-chunk checkpointing
were cut. v1 instead estimates tokens against the model context and stops cleanly above a safe
budget (F6). Deferred: chunked map-reduce, per-chunk checkpointing, chunk-level cost, reduce/merge.

**Why deferred.** Builds rare-path machinery (~8h+ inputs) the common case never exercises; a
single 40K-token call costs ~$0.12, so "never re-pay" is pennies.

**When to open.** When a real input trips the overflow guard, or very-long material becomes regular.

### TD-6 — Summary title can leak the source language (not the target)

Severity: LOW · Created 2026-06-16 · Trigger: cross-language summarizing becomes a regular case · SoT: this file

**What.** The `[summarize]` prompt asks for `title` "in {language}", but a German clip summarized
to Russian returned a German title with a correctly-Russian body (`claude-haiku-4-5`, T13 smoke).
The title anchors to the source language when source ≠ target — content is unaffected.

**Why deferred.** Supported languages are RU + EN; same-language cases (RU→RU, EN→EN) are clean and
the T10 live gate passes both. Fix is a one-line prompt hardening but wants a paid live re-validation.

**When to open.** When audio-language ≠ summary-language becomes a normal input — harden the title
instruction and re-run the live gate.

### TD-7 — Plain-input / non-TTY fallback UI deferred from v1.1

Severity: LOW · Created 2026-06-17 (v1.1 eng-review) · Trigger: a non-interactive (piped/redirected) run is ever genuinely needed · SoT: this file

**What.** v1.1's questionary+rich layer needs an interactive TTY. The adapter detects a non-TTY at
startup and exits cleanly with a message (`NotInteractiveError`), rather than carrying a second
`input()`/`print` UI for piped runs.

**Why deferred.** EchoGist is a single-operator interactive console tool; there is no piped/CI
invocation in its real use. A full second adapter to serve a case that never occurs is over-eng.

**When to open.** If a batch/scripted/headless menu invocation becomes a real need. The `UI` Protocol
seam already leaves a clean place to add a `PlainUI` without touching the flows.

### TD-8 — `setuptools<81` pin in the v1.1 plan does not exist in the lockfile

Severity: LOW · Created 2026-06-17 (v1.1 build) · Trigger: next `scripts/lock-deps` re-lock, or any ctranslate2 `pkg_resources` import failure on Windows · SoT: this file

**What.** The (now-archived) v1.1 plan §2 says "PRESERVE the existing `setuptools<81` pin". There is
no such pin: `requirements.in` never constrained it and `requirements.lock` ships `setuptools==82.0.1`,
which the operator's Windows runs passed on. The plan's premise was stale; the cuDNN↔ctranslate2
skew tripwire (the pin that matters) is untouched.

**Why deferred.** Adding `setuptools<81` now would be an unrequested downgrade against a lock that
ships and passes on the 4060. ctranslate2 4.8 imports clean with setuptools 82 there.

**When to open.** At the next re-lock: confirm setuptools 82.x still imports clean and strike the
"<81" wording, or add the pin deliberately if a real `pkg_resources` failure surfaces.

### TD-9 — Cheap-call "press Enter to summarize" beat dropped in v1.1

Severity: LOW · Created 2026-06-17 (v1.1 build) · Trigger: operator review of the v1.1 confirm UX · SoT: this file

**What.** The archived v1.1 plan §5 wanted a "press Enter to continue" beat on the below-threshold
cost path. The build shows the estimate via `ui.info` and proceeds with no blocking keypress
(`cost.confirm_proceed` returns `True` for cheap calls). The above-threshold gate (explicit confirm,
default No) and the `$0.50` threshold policy are unchanged.

**Why deferred.** The narrow `confirm(prompt, default) -> bool` seam can't express a non-decision
"acknowledge" beat without a Y/n widget (rejected — N could decline a cheap call that always runs).
Dropping the forced keypress is also less friction in an arrow-key UI.

**When to open.** If the operator wants the explicit pause back, add a one-line `ui.text("Press
Enter to summarize…")` in `menu._run_summary` before the cheap-path proceed — no `cost.py` change.

### TD-10 — File input forces manual path typing (no picker) — UX BLOCKER

Severity: HIGH · Created 2026-06-17 (v1.1 operator feedback) · **Status: T1–T4 BUILT on `main`; only the T5 Windows live-dialog gate remains (operator-side).** · SoT: this file

**What / built.** v1.1 made the operator type the audio/video path by hand — unacceptable friction on
the most-used action. Replaced by `UI.pick_file(prompt, *, filetypes, initialdir=None) -> str | None`:
a native tkinter "Open File" dialog (lazy, dual-guarded `ImportError`/`TclError`) → `questionary.path()`
fallback; last-used dir persisted to gitignored `config/state.json`; pure `resolve_initial_dir` ladder
(`last → ~/Downloads → ~`, never cwd). **T1–T2** config IO + `ui.py` seam (`8629ec2`); **T3**
`menu._flow_local_file` rewire + `save_last_dir` after a valid pick (`8c67b68`); **T4** verified
tkinter stubs type-check under mypy --strict (`9e8d768`). WSL/CI exercises the fallback + StubUI paths.

**When to open / remaining.** **T5 (operator, Windows-only):** live native dialog — `initialdir`
honored, filetypes dropdown, native Cancel→menu, Ctrl-C→exit, no ghost window, focus over console,
clean 2nd invocation. Not CI-testable (tk absent in WSL). Folds into the v1.1 Windows acceptance pass.

### TD-11 — Console accumulated menu chrome — BUILT (pragmatic), Windows-acceptance remainder

Severity: MEDIUM · Created 2026-06-21 (operator Windows run) · **Status: BUILT on `main` 2026-06-21 (`f4fd2ca`); Windows-acceptance remainder only.** · SoT: this file

**What / built.** Answered prompts and repeated `Current settings` tables piled up in the scrollback.
Shipped the pragmatic 80/20 (ratified at office-hours 2026-06-21, design
`pc-main-design-20260621-201432.md`): added `UI.clear()` to the seam, called on entry to each flow
(`_flow_local_file`/`_flow_saved_transcript`/`_flow_settings`). Chosen over a menu-loop-top clear,
which would wipe a finished flow's result line before it could be read. The full ephemeral/durable
two-region TUI was ratified **out of scope** (questionary won't live inside a `rich.Live`).

**When to open / remaining.** Windows-acceptance only: confirm `console.clear()` (ANSI) renders
cleanly on conhost vs Windows Terminal. Not WSL-verifiable; folds into the v1.1 Windows pass.

### TD-13 — Submenu back-navigation — BUILT (`← Back`); ESC-as-back deferred

Severity: MEDIUM (back nav done) / LOW (ESC stretch) · Created 2026-06-21 (operator bug report) · **Status: `← Back` BUILT on `main` (`f4fd2ca`); ESC keybinding deferred.** · SoT: this file

**What / built.** Submenus were one-way. Added explicit `← Back` entries to the action menu, the
transcript picker, and Settings (one consistent back vocabulary). ESC stays = exit — questionary maps
ESC to the same `None` as Ctrl-C/Ctrl-D, documented in the `run_menu` docstring.

**When to open / remaining (LOW).** The ESC-as-back stretch was deliberately not built: telling ESC
apart from Ctrl-C needs custom prompt_toolkit key bindings that would risk the load-bearing `_ask`
cancel/exit contract (TD-10). Open only if the operator still wants ESC after living with `← Back`.

### TD-14 — Open Explorer at the transcript folder after first save — BUILT, Windows-acceptance remainder

Severity: LOW · Created 2026-06-21 (operator feature request) · **Status: BUILT on `main` 2026-06-21 (`f4fd2ca`); live Explorer pop is Windows-acceptance only.** · SoT: this file

**What / built.** Added `UI.reveal_dir(path)` — `os.startfile` guarded on `os.name == "nt"`, wrapped
in `suppress(OSError)`, gated by a `self._revealed` flag so it fires once per launch. Called from
`_transcribe_to_checkpoint` after the "Saved transcript:" line, so a NEW save pops the folder but a
re-summarize does not. `StubUI` mirrors the once-guard (tested offline).

**When to open / remaining.** Windows-acceptance: confirm the live Explorer pop (nt-only). Known
limitation (documented, not chased): `os.startfile` may foreground Explorer — true no-focus-steal
would need a `ShellExecute(SW_SHOWNOACTIVATE)` ctypes call, out of scope.

---

## Closed debts

### TD-12 — `.mp3` input offered MP3/Both actions (extraction is a no-op there) ✓ CLOSED

Severity (was): LOW · Created 2026-06-21 → Closed 2026-06-21 (`f4fd2ca`)

The fork was whether an mp3 input should still ask Summary/MP3/Both. Decision: skip the prompt —
`_flow_local_file` branches on `extract.is_mp3(source)` and goes straight to summary in place (no
lossy re-encode, no copy into `output/audio/`); non-mp3 inputs still choose. Logic-only, no
Windows-specific behavior, tested (`test_mp3_source_skips_action_menu`). Fully closed.

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
