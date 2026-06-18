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
3. **Closure.** On closing: status `closed`, commit reference.
   The record is never deleted — closed records are kept in the very compact
   format below; full verbose history lives in git .

**Uniform record templates:**

- *Open:* `### <ID> — <title>` + header line (Severity · Created ·
  Deadline/Trigger · SoT) + **What** / **Why deferred** / **When to open**.
- *Closed:* `### <ID> — <title> ✓ CLOSED` + header line (Severity (was) ·
  Created → Closed) + 2–3 sentences: the fork, the decision made, commits.

---

## Open debts

> Source: manual spike on 2026-06-14 (Windows + WSL2, RTX 4060). The spike's job
> was to find where the real pipeline breaks before any code. It found three
> hard problems the design's "retry-on-failure" logic would not have caught.
> They were gating inputs for `/plan-eng-review`. **Update (2026-06-14, eng-review):
> online/link ingestion was dropped from scope, so TD-2 is now obsolete (see Closed
> debts) and TD-3 no longer provisions deno.**

### TD-5 — Chunked map-reduce summarization deferred from v1

Severity: LOW · Created 2026-06-14 (eng-review) · Trigger: first real transcript that exceeds the single-pass context budget · SoT: this file

**What.** v1 summarizes the whole transcript in a single structured call. Token
math (eng-review): a 1.5-2.5h transcript is ~35-40K tokens and fits one call in
every tier (Haiku 200K, Sonnet/Opus 1M), so chunked map-reduce + per-chunk
checkpointing were cut from v1. v1 instead estimates transcript tokens against the
selected model's context and stops cleanly above a configurable safe budget
("too long for v1 single-pass; choose a larger-context model or wait for chunked
support"). Deferred: chunked map-reduce, per-chunk checkpointing, chunk-level cost
accounting, reduce/merge quality logic.

**Why deferred.** Builds rare-path machinery (~8h+ inputs) the common case never
exercises; cost of a single 40K-token call is ~$0.12, so "never re-pay" is pennies.

**When to open.** When a real input trips the overflow guard, or when very-long
material becomes a regular input. The coarse-stage job-state engine already leaves
a clean seam to add a summarization sub-stage without reshaping the pipeline.

### TD-6 — Summary title can leak the source language (not the target)

Severity: LOW · Created 2026-06-16 · Trigger: cross-language summarizing becomes a regular case · SoT: this file

**What.** The `[summarize]` prompt asks for the `title` field "in {language}", but on a
**German** smoke clip summarized to Russian, `claude-haiku-4-5` returned a German title
("Wenn es morgen nichts mehr gäbe") while the body was correctly Russian. The title field
anchors to the source language when source ≠ target. Observed once, on the economy tier,
via the T13 live-smoke path.

**Why deferred.** The supported summary languages are RU + EN; same-language cases (RU→RU,
EN→EN) are unaffected and the T10 live gate passes clean on both. Only cross-language
(e.g. EN audio → RU summary) is exposed, and only the title, not the content. Candidate fix
is a one-line prompt hardening ("the title MUST be in {language} even when the transcript is
in another language"), but it is an LLM-prompt change that wants a paid live re-validation,
not worth churning for an edge case yet.

**When to open.** When summarizing content whose audio language differs from the chosen
summary language becomes a normal input — then harden the title instruction and re-run the
live gate.

### TD-7 — Plain-input / non-TTY fallback UI deferred from v1.1

Severity: LOW · Created 2026-06-17 (v1.1 eng-review) · Trigger: a non-interactive (piped /
redirected) run of EchoGist is ever genuinely needed · SoT: this file

**What.** v1.1 replaces the input layer with questionary + rich, which require an interactive
TTY. The adapter detects a non-TTY at startup and exits cleanly with a clear message (v1.1
plan §6), rather than carrying a second `input()`/`print`-based UI implementation that would
let piped runs work numerically.

**Why deferred.** EchoGist is a single-operator interactive console tool; there is no piped /
CI-driven invocation in its real use. A full second adapter (build + test surface) to serve a
case that never occurs is over-engineering. Fail-loud is the right v1.1 behavior. (Outside
voice flagged the WSL-dev/Windows-ship rendering gap, which the capability-detected glyph
fallback already covers — that is separate from a full non-TTY input path.)

**When to open.** If a batch / scripted / headless invocation of the menu becomes a real need.
The `UI` Protocol seam already leaves a clean place to add a `PlainUI` implementation without
touching the flows.

### TD-8 — `setuptools<81` pin in the v1.1 plan does not exist in the lockfile

Severity: LOW · Created 2026-06-17 (v1.1 build) · Trigger: next `scripts/lock-deps` re-lock, or any ctranslate2 `pkg_resources` import failure on Windows · SoT: this file

**What.** The v1.1 plan §2 (and the saved checkpoint) instruct "PRESERVE the existing
`setuptools<81` pin" through the re-lock. There is no such pin: `requirements.in` never
constrained setuptools, and `requirements.lock` carries `setuptools==82.0.1` — which the
operator's 2026-06-16 Windows acceptance runs passed on. The T7 re-lock therefore preserved
82.0.1 (no downgrade introduced); the plan's premise was stale. The cuDNN↔ctranslate2 skew
tripwire (the real pin that matters) is untouched.

**Why deferred.** Introducing a `setuptools<81` pin now would be an unrequested downgrade
against a lock that already ships and passes on the 4060. ctranslate2 4.8 imports cleanly with
setuptools 82 on the target box (the live runs prove it), so there is nothing to fix — only a
doc/reality divergence to reconcile.

**When to open.** At the next re-lock: either confirm setuptools 82.x still imports clean and
strike the "<81" wording from the plan, or, if a real `pkg_resources` failure surfaces on a
future ctranslate2/setuptools combo, add the pin to `requirements.in` deliberately and re-run
the §12.2 Windows cold-run verify.

### TD-9 — Cheap-call "press Enter to summarize" beat dropped in v1.1

Severity: LOW · Created 2026-06-17 (v1.1 build) · Trigger: operator review of the v1.1 confirm UX · SoT: this file

**What.** v1.1 plan §5 says the below-threshold cost path should keep "a plain 'press Enter to
continue' beat that ALWAYS proceeds." The build instead shows the estimate via `ui.info` and
proceeds with **no** blocking keypress (`cost.confirm_proceed` returns `True` for cheap calls
without calling the confirm widget). The above-threshold gate (explicit confirm, default No)
and the exact `$0.50` threshold policy are preserved unchanged.

**Why deferred.** The narrow `confirm(prompt, default) -> bool` seam (the eng-reviewed shape)
cannot express a non-decision "acknowledge" beat without either a Y/n widget (which the plan
explicitly rejects, since `N` could decline a cheap call that always runs) or leaking a second
callable into `cost.py`. Dropping the forced keypress on every cheap summary is also less
friction in an arrow-key UI, where the displayed estimate is itself the acknowledgment.

**When to open.** If the operator wants the explicit pause-and-acknowledge on cheap calls back,
add a one-line `ui.text("Press Enter to summarize…")` beat in `menu._run_summary` before the
cheap-path proceed — no change to `cost.py` or the threshold policy.

### TD-10 — File input forces manual path typing (no picker / browse) — UX BLOCKER

Severity: HIGH · Created 2026-06-17 (v1.1 operator feedback) · Trigger: NEXT — before the tool is comfortable for daily use · SoT: this file

**What.** v1.1's "Local file" flow prompts `ui.text("Path to the audio/video file:")` — the
operator must type or paste an absolute path by hand. For the tool's single most-used action
that is unacceptable friction (operator feedback, 2026-06-17: "did you think I will type the
path manually?"). The arrow-key overhaul modernized every surface *except* the one where the
input is a file on disk.

**Why deferred.** Logged immediately after the v1.1 build; designed/ratified at office-hours
2026-06-18 (below). Not yet built — `/plan-eng-review` turns this into tasks/tests next.

**Ratified design (office-hours 2026-06-18).**
- **Primary — native OS "Open File" dialog** via `tkinter.filedialog.askopenfilename`
  (Tcl/Tk ships with the python.org Windows installer). Familiar Explorer picker opened at the
  resolved `initialdir`, audio/video `filetypes` filter, Cancel → return to menu. Killswitch-safe
  (offline stdlib). **Import must be lazy AND dual-guarded:** `tkinter` is absent from the WSL dev
  venv (verified — `ModuleNotFoundError`), so the adapter catches `ImportError` (tk not installed,
  the whole WSL/CI path) **and** `TclError` (tk present, no display) and falls back. Lazy import
  keeps the no-TTY/killswitch import purity; mypy still type-checks (typeshed bundles tk stubs).
- **Fallback / in-console — `questionary.path()`** (already a dep): Tab-completion path entry in
  the terminal, no GUI. Fires whenever the dialog is unavailable (tk-absent or no-display) or
  cancelled into the fallback. Drag-and-drop onto the console pastes a path into this prompt for free.
- **Seam shape:** add `UI.pick_file(prompt, *, filetypes) -> str | None`. `RichQuestionaryUI`
  → tkinter dialog with the `questionary.path()` fallback; `StubUI` → pops a queued path (CI stays
  offline/no-TTY/no-GUI). Cancel returns `None` at every level → clean return to menu, reusing the
  existing T9 cancel/EOF contract. No new interrupt contract. Rewire `_flow_local_file` (menu.py)
  to call `ui.pick_file` in place of `ui.text("Path to the audio/video file:")`; keep the existing
  `is_file()` F1 reject as the post-check.
- **Last-used directory (open Q1 — resolved A):** persisted to a **separate `config/state.json`**
  (single field `last_input_dir`), NOT added to `Settings`. Keeps `Settings` = operator choices
  only (no new field through `_validate_settings`, nothing in the Settings menu). Honors
  `$ECHOGIST_CONFIG_DIR` like `settings.json`; add `config/state.json` to `.gitignore` (only
  `settings.json` is ignored today). Read best-effort; write the chosen file's parent dir after a
  successful pick. This is a single-field UX convenience, not a job/history layer (CLAUDE.md intact).
- **First-run + stale default (open Q2 — resolved):** resolution ladder for `initialdir` —
  `last_input_dir` (if it still exists) → `~/Downloads` (if it exists) → `~` (home, always exists).
  Never cwd. The same ladder absorbs the stale-stored-dir edge (a vanished `last_input_dir` falls
  through, no crash).
- **Scope:** remember last-used directory is **IN**; a recent-files list is **OUT** (cut).
- **Dependencies:** no new locked deps (§12.2 untouched) — `tkinter` is stdlib on the python.org
  Windows install, `questionary` is already pinned.

**Verification split (WSL-dev / Windows-ship).** CI + the WSL dev loop exercise only the
`questionary.path()` fallback and the `StubUI` queued-path flows (tk is absent in WSL, so the dialog
branch never runs there). The live native dialog — Explorer picker, `initialdir`, `filetypes`
filter, Cancel-to-menu, glyph-free OS chrome — is verifiable **only at the Windows acceptance gate**,
the same split as the v1.1 menu adapter.

**When to open.** Now — it is the next build after v1.1 acceptance. Run `/plan-eng-review` on this
ratified section, then implement behind `UI.pick_file`.

**Implementation plan (eng-review 2026-06-18).** 5 findings resolved + 5 outside-voice hardening
points folded. Scope: ~6-7 files, 0 new classes, no new locked deps.

*Resolved design decisions:*
- **Seam:** `UI.pick_file(prompt, *, filetypes, initialdir=None) -> str | None` (added `initialdir`;
  the menu passes the resolved dir, `ui.py` stays stateless — StubUI needs no config/state).
- **IO + ladder live in `config.py`** (reuse `config_dir()` + the `save_settings`/`load_settings`
  json idiom): `STATE_FILENAME="state.json"`, `load_last_dir()`, `save_last_dir(dir)`,
  `resolve_initial_dir(last)`. `config/state.json` added to `.gitignore`. Reads AND writes
  best-effort: corrupt/missing → `None`; `save_last_dir` swallows `OSError` (a state-write failure
  must never mask a completed transcribe+summarize — `OSError` is in `menu._RECOVERABLE`).
- **Ladder (pure):** `last_input_dir` if it still exists → `~/Downloads` if it exists → `~`. Never
  cwd. `exists()` guarded against `OSError` (dead UNC/network path) and resolved at call time.
- **Cancel split:** dialog Cancel / empty `questionary.path()` → `None` → `_flow_local_file` prints
  "returning to the menu" and loops; Ctrl-C/Ctrl-D → `EOFError` → app exit (the existing contract).
  `pick_file` BYPASSES `_ask` and hand-rolls the per-backend map: tkinter `""` → `None`,
  questionary `None`/`""` → `None`, `KeyboardInterrupt`/`EOFError` propagates.
- **filetypes:** `[("Audio/Video", "*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus *.mp4 *.mkv *.mov
  *.webm *.ts"), ("All files", "*.*")]` — advisory; the "All files" entry prevents silently hiding a
  valid odd-extension input (the pipeline transcodes anything ffmpeg reads). Fallback ignores it.
- **tkinter root lifecycle (OV #3):** explicit `root = Tk(); root.withdraw();
  root.wm_attributes("-topmost", True); askopenfilename(...); root.destroy()` in a `finally` — avoids
  the ghost/flashing window, focus-behind-console, and stale 2nd-call state on Windows.
- **StubUI 3-state rule (documented):** queued `None` = soft cancel; empty queue = `EOFError`; no
  third state (a deliberate interrupt is not separately queueable). `pick_file` is the first StubUI
  method to legitimately return `None`.
- **DRY:** the picked path and the `questionary.path()` fallback both funnel through the existing
  `menu._resolve_typed_path` (fold "Check the path and try again" into its message).

*Tasks (sequence: seam before menu, per OV):*
- **T1 (P1) — `config.py` state + ladder.** `state.json` IO (best-effort) + `resolve_initial_dir`
  + `.gitignore` line. Tests in `test_config.py` (absent/valid/corrupt load; save round-trip +
  dir-create + `OSError` swallow; ladder: last-exists / Downloads / home; `OSError` on exists).
- **T2 (P1) — `ui.py` seam.** `pick_file` on `UI` Protocol + `StubUI` + `RichQuestionaryUI`
  (lazy dual-guard `ImportError`/`TclError` → `questionary.path()`; explicit Tk root mgmt; cancel
  map; bypass `_ask`). Tests `test_ui.py` (stub queued path / None / empty; monkeypatched
  ImportError + TclError → fallback; dialog path / "" cancel; Ctrl-C → EOFError).
- **T3 (P1) — `menu._flow_local_file` rewire.** resolve initialdir → `ui.pick_file` → `None`→menu;
  else `_resolve_typed_path`; on success `save_last_dir(source.parent)`. Tests `test_menu.py`
  (pick→transcribe→save called; cancel→menu, no transcribe/save; fallback bad path→F1). Depends T1+T2.
- **T4 (P2) — mypy gate.** Confirm `tkinter`/`filedialog` typeshed stubs resolve under `--strict` in
  the dev loop (don't assume). Depends T2.
- **T5 (P3, Windows acceptance gate only) — live dialog.** Real Explorer dialog, `initialdir`
  honored, filetypes dropdown, native Cancel→menu, Ctrl-C→exit, no ghost window, focus over the
  console, clean 2nd invocation. Not CI-testable. Depends T3.

*Parallelization:* Lane A = T1 (`config.py`), Lane B = T2 (`ui.py`) — independent, run in parallel
worktrees. Merge both, then T3 (`menu.py`, depends A+B). T4 after B. T5 manual after T3.

*NOT in scope:* recent-files list (cut, no TODO — low value for a single-operator tool);
`PlainUI`/non-TTY input (TD-7); converting the saved-transcript prompt to the picker (the existing
select-list + type fallback stays; `pick_file` reuse there is a later option); drag-and-drop (free
terminal paste into the fallback, no code).

*Failure modes — none silent-and-unhandled:* tk absent → `ImportError`→fallback (tested); no display
→ `TclError`→fallback (tested); ghost/focus on Windows → explicit root mgmt (live gate); corrupt
`state.json` → `None`→ladder (tested, silent-but-correct); read-only `config/` on save → swallowed
(tested, run already done); dead UNC initialdir → `OSError`-guarded→home (tested).

---

## Closed debts

### TD-3 — GPU provisioning the launcher must automate ✓ CLOSED

Severity (was): MEDIUM · Created 2026-06-14 → Closed 2026-06-16 (T13, win-smoke)

The fork was how to make cold-Windows GPU provisioning automated + verifiable rather
than a manual check. Decision: `run.bat` provisions DLLs + model idempotently;
`gpu.register_cuda_libraries` loads the cuDNN/cuBLAS wheels before `import faster_whisper`;
`scripts/win-smoke.bat` scripts the cold-run acceptance (provision → drive one clip →
assert artifacts). The operator's first `win-smoke.bat` pass on the 4060 closed it — and
caught a real bug the WSL path never hit: ctranslate2 lazily loads cuBLAS by bare name at
the first GEMM, a search that ignores `os.add_dll_directory`. Fixed by also prepending the
wheel `bin` dirs to `PATH` and pinning the entry DLLs resident via `ctypes.WinDLL`
(173541d). cuDNN-major↔ctranslate2 skew tripwire stays in `requirements.in`. Commits:
b024368 (win-smoke), 173541d (cuBLAS fix), e9a9cfb. Full skew history in git.

### TD-4 — GPU transcription speed unmeasured ✓ CLOSED

Severity (was): LOW · Created 2026-06-14 → Closed 2026-06-16 (T11, 4060 run)

The fork was whether `large-v3` on the 4060 is fast enough and whether int8 quantization
costs RU quality. Measured warm on the 4060 via `scripts/measure_model.py`:
`int8_float16` = **10.11x realtime, 3.46 GB VRAM** (bar 3.0x → PASS), `float16` = 10.27x,
5.39 GB. Decision (operator): **keep int8_float16** — same speed, ~2 GB less VRAM, and on
this run int8 RU quality beat the float16 control (float16 garbled the opening; int8 clean).
Report committed at `docs/measurements/large-v3-int8_float16-2026-06-16.md`. Commits:
b93fd72 (harness), 6bbb1fe (measurement + verdict).

### TD-1 — Whisper model runtime download ✓ CLOSED (root cause: Xet, not region)

Severity (was): MEDIUM/HIGH · Created 2026-06-14 → Closed 2026-06-15 (T3, gate c)

The fork was how to distribute the ~3 GB CT2 large-v3 when `snapshot_download` stalled.
Root cause was the **Xet transport** (hf_hub 1.x auto-routes large files through the Xet
CAS hosts, which stall on the operator's route), not RU region-blocking — proven by a
`HF_HUB_DISABLE_XET=1` pull at ~10.5 MB/s vs the Xet-host timeout (xet-core#446). Decision:
fetch vanilla `Systran/faster-whisper-large-v3` (float16) from HF with `HF_HUB_DISABLE_XET`
forced on for the classic LFS path; load `int8_float16` at runtime; pre-placed `local_dir`
as the offline fallback; the dormant self-host zip path was removed. Tradeoff (accepted):
the model arrives outside `requirements.lock` (HF checksums, not our hash pins). All three
gates green on the 4060: (a) HF download, (b) `int8_float16` load, (c) a clip transcribes
via the T3 `transcribe()` stage (autolang `en`, verbatim timecoded segment). Commits:
1f25ef1, d7b23c3, 0b350bb, 7cbc5dc, 2748196 (T3).

### TD-2 — YouTube ingestion subsystem ✓ CLOSED (obsolete)

Severity (was): HIGH · Created 2026-06-14 → Closed 2026-06-14 (eng-review, scope cut)

The fork was how to build a durable automated YouTube extraction path. Eng-review
research falsified the lead hypothesis: PO tokens (BotGuard) no longer bypass the
bot-check in the majority of cases (now bound per-video), yt-dlp native OAuth login
is gone, the only working cookie extraction is Firefox SQLite, and account cookies
expire ~2 weeks. The decision (operator): **drop all online/link ingestion entirely**
(YouTube + Vimeo + URL input) rather than carry an account/login-maintenance and
ban-risk subsystem. Primary and only ingestion is now local files + saved transcript,
giving a fully offline, zero-credential, no-maintenance tool. The whole TD-2 subsystem
(yt-dlp, bgutil PO-token provider, deno EJS solver, managed Firefox profile, throwaway
account, cookie handling, client fallback ladder) is removed from scope. Supersedes
decision 0929b279. TD-3 updated to drop deno provisioning.