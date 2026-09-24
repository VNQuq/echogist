# EchoGist — Engineering Plan (v1.0)

**Status:** locked — pipeline reviewed by `/plan-eng-review` + `/plan-devex-review` 2026-06-14;
console UX layer reviewed by `/plan-eng-review` 2026-06-17. Both ENG CLEARED; cross-model
outside voice absorbed in each pass.
**Architecture:** pure-stage pipeline with artifact-based recovery (single-pass realization of
Approach C; not a persisted job engine), fronted by a dependency-injected `UI` Protocol.
**Authority:** [CLAUDE.md](../../CLAUDE.md) · supersedes the link-source parts of the approved
office-hours design (online ingestion dropped from scope).

This document is the locked build spec for the v1.0 release. It contains no implementation code.

---

## 0. How this plan is organized

v1.0 was built in two development phases, one release:

- **Phase 1 — the pipeline** (§1–§5, §7–§13): the offline core, ingest → extract → transcribe →
  guard → summarize → render, with artifact-based recovery and first-run provisioning. This is
  the foundation; stack and data flow are defined here.
- **Phase 2 — the console UX layer** (§6): an arrow-key, styled console (`questionary` + `rich`)
  that replaces only the input/output seam. It is **not** a pipeline rearchitecture —
  transcribe/summarize/render outputs are byte-for-byte unchanged. It rides a dependency-injected
  `UI` Protocol so the killswitch CI invariant (offline, no-TTY) survives.

Sections that span both phases (stack, test matrix, out-of-scope) are merged and note which phase
a row belongs to where it matters.

---

## 1. Scope (locked)

A single-`.bat` Windows console tool. Input is a **local audio/video file** or a **saved
transcript**. Output is an MP3 track and/or a structured RU/EN summary as PDF (default) or
Markdown. Transcription is local GPU Whisper; summarization is one cloud Anthropic call. Fully
offline except the summarization step. No online sources, no logins, no analytics/history.

Cut from the original design (see TECHNICAL_DEBT TD-2 closed): all YouTube/Vimeo/URL ingestion and
its subsystem. Deferred (TD-5): chunked map-reduce + per-chunk checkpointing — v1.0 is single-pass
with a hard overflow guard.

The Phase 2 console UX replaces the bare `input()` / numeric-prompt loop (~2/10 UX) with a modern
arrow-key, styled console targeting ~9/10. Six operator-facing surfaces change; pipeline logic
does not (§6).

---

## 2. Stack (locked)

| Concern | Choice | Phase | Note |
|---|---|---|---|
| Language | Python 3.11 (Windows ship, WSL dev) | 1 | manual prereq; launcher prints install steps if absent |
| Transcription | `faster-whisper` 1.2 / `ctranslate2` 4.x | 1 | GPU, timestamped, auto-detect RU/EN |
| GPU DLLs | `nvidia-cudnn-cu12` + `nvidia-cublas-cu12` (pip wheels) | 1 | `os.add_dll_directory(...)` BEFORE `import faster_whisper` |
| Model | large-v3 CT2, **float16 on disk** loaded `int8_float16` | 1 | vanilla `Systran/faster-whisper-large-v3` from Hugging Face + pre-placed-dir escape hatch (TD-1) |
| Summarization | `anthropic` SDK (>=0.109) | 1 | single structured call; `output_config.format` |
| Token estimate | **local, language-aware** (bundled small tokenizer or per-script ratio) | 1 | offline; Cyrillic costs more tokens/char than Latin — estimate RU **high** |
| PDF | `fpdf2` + embedded **DejaVuSans** | 1 | Cyrillic, no tofu (spike-verified) |
| ffmpeg | **`imageio-ffmpeg`** (bundled static binary) | 1 | supersedes the design's winget flow — see §7 |
| Launcher | `run.bat` (cmd only) | 1 | `.ps1` blocked by execution policy |
| Navigation / input | `questionary==2.*` (pulls `prompt_toolkit`, `wcwidth`) | 2 | arrow-key select, text, confirm; needs a TTY |
| Output / formatting | `rich` (lock-deps-resolved major — 13 or 14) | 2 | panels, tables, Progress, spinners; capability detection built-in |
| Windows color shim | `colorama ; sys_platform == 'win32'` (transitive) | 2 | marker-guarded by the universal resolver |

The Phase 2 packages are **pure-Python, no native/ABI component** — they do not interact with the
`ctranslate2 ↔ cuDNN` skew the lockfile (§13.2) guards. The re-lock that adds them must not disturb
the GPU wheel pins.

**Editable model config (seeded, data not code — updatable without a code change):**

| Tier | Model ID | Context | Input $/MTok | Output $/MTok |
|---|---|---|---|---|
| economy | `claude-haiku-4-5` | 200K | 1 | 5 |
| balanced | `claude-sonnet-4-6` | 1M | 3 | 15 |
| flagship | `claude-opus-4-8` | 1M | 5 | 25 |

Verified against the Claude API reference at build time. Deprecated/unknown model → guide the user
to re-select in settings (do not crash).

---

## 3. Pipeline + data flow

```
MAIN MENU (loops; explicit exit only)
 ├ 1. Local file (audio/video)
 ├ 2. Saved transcript
 ├ 3. Settings   (summary language · output format PDF/MD · model tier · confirm threshold)
 └ 4. Exit

 SOURCE              PURE STAGES  (each pure over the in-memory pipeline object)
 ─────────           ─────────────────────────────────────────────────────────────
 local VIDEO ─▶ INGEST ─▶ EXTRACT_AUDIO ─▶ TRANSCRIBE ─▶ [GUARD] ─▶ SUMMARIZE ─▶ RENDER
                classify    ffmpeg→mp3       faster-      token est   one struct.   PDF+DejaVu
                            (per action)     whisper GPU  vs ctx;     title+        / MD
 local AUDIO ─▶ INGEST ─(opt mp3 convert ──▶ ts, auto-   stop if     summary
                         if not mp3)          lang        over budget  call
 saved TXT   ─▶ INGEST ──────────────────────────────────▶ [GUARD] ─▶ SUMMARIZE ─▶ RENDER
                pick from output/transcripts/ or typed path

 ARTIFACTS (canonical, kept = the recovery state):
   output/audio/<date>-<title>.mp3
   output/transcripts/<date>-<title>.txt        ← checkpoint between TRANSCRIBE and SUMMARIZE
   output/summaries/<meaningful-title>.(pdf|md)
   output/summaries/raw/<meaningful-title>.json  ← raw structured result, written on a
                                                   SUCCESSFUL paid call BEFORE render

 SUMMARIZE→RENDER ordering: on a successful call, persist the raw structured result
   (title + sections) to disk FIRST, then RENDER reads from it. A render failure
   (fpdf2 edge case, filename collision) never costs a re-pay — re-render from the
   saved .json. (outside-voice #6)
 RECOVERY: artifact-based. Summarize failed → menu option 2 re-summarizes the saved
           transcript (current settings). No job.json, no history layer.
 KILLSWITCH: SUMMARIZE is the only network stage; the GUARD/cost estimate is LOCAL
             (no count_tokens call), so everything left of SUMMARIZE is offline,
             key-free, and unit-testable against a stub summarizer (CLAUDE.md rule).
```

**Per-source action menus:**
- local video → {MP3 only · summary only · both}
- local audio → {summary} (+ optional "convert to MP3" if source isn't MP3)
- `.mp3` input → {summary · transcript only} (extraction is a no-op; TD-12)
- saved transcript → {summary}

**Summary structure (required minimum):** overview · key takeaways · section timecodes
(`[HH:MM:SS]` carried from Whisper segments → suppresses hallucinated timecodes) · recurring
themes · core idea. Title comes from the SAME structured call, sanitized for Windows-illegal chars
`\ / : * ? " < > |`, truncated, deduped per-artifact with `-2`/`-3`. No usable title → source-stem
+ date.

**Cost flow:** `[GUARD]` estimates transcript tokens **locally** (language-aware,
conservative-high for Cyrillic — no network call), shows estimated cost from the price config:
input from the local estimate, **output as a small fixed absolute (~2K tokens)**, not a fraction of
input (summary length is roughly constant regardless of transcript length — outside-voice #3).
Always shown; acknowledge for cheap material, explicit y/n past the configurable cost-or-length
threshold. After the call, show **actual** cost from `response.usage` (the exact, audited number).

---

## 4. Overflow guard (replaces chunking in v1.0)

Before SUMMARIZE: `est_tokens = local_estimate(transcript + prompt)` — **local and language-aware**,
biased to estimate **high** (so an over-long transcript is caught, never slips past). If
`est_tokens > safe_budget(model)` (configurable; default e.g. 80% of the tier's context window),
STOP cleanly:

> "This transcript is too long for single-pass summarization. Choose a larger-context model in
> Settings, or wait for chunked summarization support."

Return to menu; transcript already saved. No silent truncation.

---

## 5. First-run provisioning (TD-3)

`run.bat` (idempotent, cmd-only) does, in order:
1. **Python check** — if `python` not in PATH or wrong major/minor, print exact install
   instructions and stop (manual prereq, never auto-installed).
2. **venv** — create/reuse a local `.venv`; call its python by absolute path.
3. **deps** — `pip install` the §2 set with **pinned, mutually-compatible versions** (cudnn/cublas
   wheels matched to the exact ctranslate2 4.x build — version skew is the #1 silent first-run
   failure, outside-voice #4). Pin in a lockfile, not `-U`.
4. **model fetch (TD-1)** — if the local model dir is absent, fetch the vanilla CT2
   `Systran/faster-whisper-large-v3` (float16 on disk) **from Hugging Face** via
   `huggingface_hub.snapshot_download` into `local_dir`; T3 loads it with
   `compute_type=int8_float16` (quantized at load — int8 speed/VRAM, full large-v3). The launcher
   also **accepts a pre-placed local model dir** as a drop-in escape hatch (`local_dir/model.bin`
   present → no fetch, offline-safe). The download is decoupled from the CUDA stack (no ctranslate2
   import) so the GPU preflight owns those diagnostics. **TD-1 (resolved):** the download stall was
   the **Xet transport**, not the region — `fetch_from_hf` forces `HF_HUB_DISABLE_XET` to take the
   classic LFS path. The fallback if HF ever fails is the pre-placed dir (browser-download the
   files, drop them in `local_dir`). Tradeoff: the model arrives **outside `requirements.lock`**
   (HF's checksums, not our hash pins) — accepted for a personal tool.
5. **output dirs** — create `output/{audio,transcripts,summaries}`.
6. **GPU preflight + DLLs** — register the `nvidia/*/bin` dirs via `os.add_dll_directory(...)`
   **before** importing `faster_whisper`, then run a tiny CUDA op as a **preflight**. On failure,
   print a *specific* diagnostic (which DLL failed to load, NVIDIA driver too old, version skew) —
   never a cryptic import crash (outside-voice #4). The driver itself is a manual prereq (step 1's
   class): detect, guide, don't auto-install.
7. **API key** — validate `ANTHROPIC_API_KEY` only before a summarization action; MP3-only
   extraction must work with no key. (The GUARD is local, so it too runs key-free.)

Failure of any auto step → clear, copy-pasteable guidance, clean stop. No `.ps1`.

---

## 6. Console UX layer — the UI seam (Phase 2, locked)

**Lineage:** office-hours design (kept in the operator's local gstack store, not published)
(APPROVED) → this phase. Decisions logged in the gstack decision store.

### 6.1 Goal

Replace the bare `input()` / numeric-prompt loop with arrow-key, styled console I/O using
**questionary** (navigation/input) + **rich** (output/formatting). Six surfaces change:

1. **Main menu** — `questionary.select`, arrow-key navigation, icons per item, under a `rich.Panel`
   "EchoGist" banner.
2. **Settings** — current settings as a colored `rich.Table`; edit via nested `questionary.select`;
   threshold via `questionary.text` with numeric `>= 0` validation.
3. **Transcription progress** (operator's #1 screen) — `rich.Progress` `%/elapsed/ETA` bar;
   spinners for the non-progress waits (provision, model load, summary call); ✓/✗ colored status
   lines replacing bare "GPU preflight OK" text.
4. **Paid-call confirm** — surfaces the local cost estimate; below threshold an acknowledgment that
   proceeds, above threshold an explicit gate defaulting to No. Must not weaken the killswitch or
   the `$0.50` threshold logic.
5. **Result / error** — final `rich.Panel` on success; rich-rendered errors, not tracebacks.
6. **Polish** — spinners on all wait stages; centralized theme.

### 6.2 The seam

The menu is fully dependency-injected: every prompt goes through `Deps.reader`, every line through
`Deps.log`. The CI killswitch invariant rides on this: tests pipe stdin and stub `log` to run the
whole menu with **no TTY, no key, no network**. questionary/prompt_toolkit **require a TTY** and
break under piped stdin. So the overhaul keeps the seam and routes I/O through a `UI` Protocol.

```
                         echogist/menu.py  (pure flows — logic unchanged)
                                   │ calls ui.select/text/confirm/info/success/
                                   │       warn/error/table/banner/progress/spinner
                                   ▼
                         ┌───────────────────────┐
            Deps.ui ───▶ │   UI  (Protocol)       │   ← typed seam
                         └───────────┬───────────┘
              ┌──────────────────────┴──────────────────────┐
              ▼                                              ▼
  RichQuestionaryUI (production)                  StubUI (tests only)
   rich.Console + questionary.Style                records (method, plain message)
   + theme glyphs; touches the TTY                 select/text/confirm pop a queued answer
   progress()/spinner() → rich-backed              progress()/spinner() → NO-OP handles
   handles (.advance_to / .done)                   (.advance_to = pass, .done → .messages)
              │                                              │
              └────────── both satisfy ProgressHandle / SpinnerHandle (Protocols) ──────────┘

KILLSWITCH: StubUI has no TTY, no rich, no network → the full menu runs in CI offline.
            Production RichQuestionaryUI imports rich/questionary (offline libs); anthropic
            stays lazy inside summarize. Import-time network-free invariant holds.
```

**Why handles are Protocol-typed (not rich-native yields).** `progress()`/`spinner()` return
`ProgressHandle`/`SpinnerHandle` (Protocols). Yielding rich-native `Progress`/`Status` objects would
force `StubUI` to fake a rich-compatible context manager, puncturing the no-TTY purity the seam
exists for. The stub returns a trivial no-op handle; swapping rich later touches only the adapter.

**New source types:** `UI`, `RichQuestionaryUI`, `ProgressHandle`, `SpinnerHandle` (+ test-only
`StubUI`). `Choice` is NOT a new type — use a tuple / `questionary.Choice`.

### 6.3 Theme & Windows fallback

- **Theme module** `echogist/theme.py` — one `rich.Theme`, one `questionary.Style`, and the
  fancy/ASCII glyph tables, as the centralized source. NOT `models.toml`: a palette is developer
  taste, not operator behavior; TOML-serializing it would add a parse/validate surface for values
  the solo operator never tunes.
- **Capability detection** — read `rich.Console` capabilities (`encoding`, `legacy_windows`), set
  one `fancy` flag, swap a glyph table: fancy (`✓`, `✗`, `→`, Unicode box) vs ASCII (`OK`, `X`, `>`,
  ASCII box). Honor `NO_COLOR`. rich auto-degrades color/box-drawing but does NOT strip emoji placed
  in your own strings — the glyph table closes that gap on legacy cmd (cp437/cp1251).

```
detect_caps(console) → fancy?  ── utf-8 + TTY + not legacy_windows + NO_COLOR unset ──▶ FANCY glyphs
                                └─ else (cp437/cp1251, NO_COLOR, legacy) ────────────▶ ASCII glyphs
```

### 6.4 Pipeline-adjacent seams (the only stage touches)

**Progress (`transcribe.py`).** Add `progress: Callable[[float], None] | None = None`.

- `total > 0` → `progress(raw.end/total)` → `%/elapsed/ETA` bar.
- `total == 0` (zero-duration / unprobeable; TTY guaranteed by §6.5) → a determinate-by-work readout
  (segments processed + elapsed, **no %/ETA**) with an explicit ✓ at completion — honest no-ETA
  progress, never an infinite spinner. No div-by-zero.
- The per-segment `log` line is **removed** (the bar replaces it). The pre-loop language+duration
  line feeds the progress UI's **label/header**.
- **Extract a pure `_collect_segments(stream, total, progress) -> tuple[Segment, ...]`** from the GPU
  adapter so the fraction math + `total==0` readout + empty stream are unit-testable with fake raw
  segments (no GPU). Matches the project's pure-stage boundary.

**Confirm (`cost.py`).** `confirm_proceed` keeps the threshold policy in `cost.py`; swap `reader=`
for a narrow `confirm: Callable[[str, bool], bool]` (prompt, default) so `cost.py` stays decoupled
from the full `UI` Protocol.

- **Below threshold** is an ACKNOWLEDGMENT, not a gate: show the cost via `ui.info` and proceed
  (faithful to today's unconditional proceed) — NOT a `confirm` Y/n widget (which would let `N`
  decline a cheap call that always ran). See TD-9 for the dropped explicit-keypress beat.
- **Above threshold** → `confirm(default=False)`.
- Preserves the exact `$0.50`-threshold UX; killswitch-safe (local, no wire).

### 6.5 Never-crash / exit contract

The loop relies on `EOFError` == clean exit and "fail loud, return to menu."
questionary/prompt_toolkit do NOT raise `EOFError` on cancel (Ctrl-C returns `None` / raises
`KeyboardInterrupt`). The adapter MUST:

1. At construction, if stdin/stdout is **not a TTY**, print one clear line and exit cleanly BEFORE
   the menu loop (no prompt_toolkit traceback).
2. Translate questionary cancel (`None` / Ctrl-C / Ctrl-D) into the loop's clean-exit (raise
   `EOFError` or a sentinel the loop already exits on); catch `KeyboardInterrupt` so Ctrl-C never
   crashes. Keep the two `except EOFError` arms live in production.
3. The outermost `except` in `run_menu` falls back to builtin `print` if `ui.error()` itself raises
   (a broken UI can't report its own failure through itself).

### 6.6 Post-ship UX refinements (all built, Windows-accepted)

The console layer grew four operator-driven refinements after the first ship; all are closed in
TECHNICAL_DEBT: native file picker (TD-10), per-flow screen-clear (TD-11), `← Back` submenu
navigation (TD-13), Explorer pop after first transcript save (TD-14), and the trimmed `.mp3` action
menu (TD-12). A hidden 1–9 quick-select rides every `select` menu, advertised in the muted
"(Use arrow keys or 1-N)" instruction.

---

## 7. Decisions ratified (was "react if wrong")

Both items below were **ratified by the operator in `/plan-devex-review` (2026-06-14)**.

- **ffmpeg = bundled `imageio-ffmpeg` static binary** ✓ ratified, superseding the design's "detect
  on PATH / guided `winget install ffmpeg`" flow. Removes the user winget step and the "fresh winget
  shim isn't on the running session's PATH" problem; aligns with "no manual workarounds". Net: MP3
  extract/convert just works on first run.
- **Model default = large-v3 int8_float16** ✓ ratified (not float16, not distil). int8_float16 is a
  load-time `compute_type` on the **one float16 `model.bin`** pulled from HF, not a
  separately-distributed artifact (TD-1 decision). The TD-4 measurement run confirmed the default
  (int8 RU quality beat the float16 control on the 4060; same speed, ~2 GB less VRAM) — see §13.3.
  The float16 fallback (same model, different `compute_type`, no re-download) and the pre-placed
  model dir remain the escape hatches.

---

## 8. Edge cases & failure modes

| # | Failure | Test? | Error handling | User sees |
|---|---|---|---|---|
| F1 | Bad path / unsupported format | unit | classify→reject | clear msg, back to menu |
| F2 | No internet during SUMMARIZE | unit (mock) | catch APIConnectionError | "summarization step failed; your transcript/audio are saved" |
| F3 | Missing `ANTHROPIC_API_KEY` | unit | pre-check before summarize | guided steps; MP3-only still works |
| F4 | 429 / insufficient credit | unit (mock) | catch RateLimitError/billing | "retry from saved transcript later" |
| F5 | Deprecated/unknown model | unit (mock 404) | catch NotFoundError | "pick another model in Settings" (no crash) |
| F6 | Transcript over context budget | unit | overflow guard (§4) | clean "too long" message |
| F7 | Model download fails/unreachable (TD-1) | unit | HF fetch wrapped → ProvisionError; pre-placed-dir escape hatch | "HF download failed, check network / pre-place the model" |
| F8 | cuDNN/CUDA load error / wheel skew / old driver | manual | GPU preflight (§5.6) catches before use | specific diagnostic (which DLL / driver), not a crash |
| F9 | Title sanitization collision | unit | per-artifact numeric suffix | deduped filename |
| F10 | Model returns no title | unit | fallback source-stem + date | named output, no crash |
| F11 | ffmpeg binary missing/corrupt | unit | imageio-ffmpeg presence check | clear msg, never silently skip |
| F12 | Interrupted mid-transcription | manual | no partial marker (accepted) | re-transcribe that file from scratch |
| F13 | RENDER fails after a paid summarize call | unit | raw result saved as .json BEFORE render (§3) | re-render from saved .json; never re-pay |
| F14 | Model download stalls (Xet transport) | auto+manual | `HF_HUB_DISABLE_XET` forces classic LFS path (§5.4); pre-placed model dir escape hatch | drop the model files into local_dir |

**Critical-gap check:** none of F1-F14 is both untested AND unhandled AND silent. F12 is a known,
accepted limitation (Whisper has no mid-file checkpoint), not a gap.

---

## 9. Test matrix (coverage target = 100% of paths)

### 9.1 Pipeline (Phase 1)

```
CODE PATHS                                        USER FLOWS
[+] ingest.py                                     [+] Local video → both
  ├ classify_source()                               ├ [→ test] mp3 + summary RU end-to-end
  │  ├ video / audio / transcript / bad             ├ [→ test] summary only (no mp3)
[+] extract.py (imageio-ffmpeg)                     └ [→ test] mp3 only (no API key set)
  ├ video→mp3 / audio→mp3(if !mp3) / dedup          [+] Local audio → summary
[+] transcribe.py (faster-whisper)                  ├ [→ test] non-mp3 audio + convert
  ├ register DLLs → load → segment stream           └ [→ test] mp3 audio, summary only
  ├ autolang RU/EN / timestamped save               [+] Saved transcript → summary
[+] guard.py                                        ├ [→ test] re-summarize after F2/F4
  ├ local est (lang-aware) / under / OVER (F6)       └ [→ test] typed path + menu-pick
  │  └ NO count_tokens — offline, killswitch-safe
[+] summarize.py (anthropic)                        [+] Settings
  ├ structured call / title+sections                 ├ [→ test] language RU↔EN
  ├ F2 / F4 / F5 error branches                       ├ [→ test] format PDF↔MD
  ├ no-title fallback (F10)                            └ [→ test] model tier econ/bal/flag
[+] render.py (fpdf2 / md)                          [+] Cost flow
  ├ PDF Cyrillic+DejaVu / MD / dedup (F9)             ├ [→ test] under threshold → acknowledge
[+] cost.py                                           └ [→ test] over threshold → y/n
  ├ estimate / threshold / actual                   [+] First-run provisioning
[+] config.py (model IDs+prices, settings)           ├ [manual] cold run on Windows+4060
  ├ load / deprecated-model guidance (F5)             └ [→ test] missing key path (F3)
[+] menu.py (loop, all returns)

EVAL: [→EVAL] summarization prompt — structure/quality on a real RU + EN transcript
      (overview/takeaways/timecodes/themes/core-idea present; timecodes plausible).
COVERAGE TARGET: every stage branch + every source×action flow + F1–F11 unit-tested;
                 F8/F12 + cold provisioning are manual (hardware/OS-bound).
```

### 9.2 Console UX (Phase 2)

| Area | Coverage | Tool |
|---|---|---|
| `theme.detect_caps` | utf-8 TTY → fancy; cp437/non-utf8 → ascii; `NO_COLOR` → ascii | unit `test_theme.py` |
| `theme.glyph` | fancy vs ascii table selection | unit |
| `transcribe._collect_segments` | fraction values; `total==0` readout; empty stream | unit `test_transcribe.py` |
| `RichQuestionaryUI` no-TTY guard | fake non-TTY → clean exit message | unit `test_ui.py` |
| Cancel → clean exit | StubUI simulates cancel/Ctrl-C → loop exits, no crash | unit `test_ui.py` |
| Number quick-select | digit → row position; cap at 9; single-choice hint | unit `test_ui.py` |
| `cost.confirm_proceed` reshape | below-threshold acknowledge-proceeds; above-threshold default No | unit `test_cost.py` |
| `menu.py` flows (REGRESSION) | every F1/F3/F6/F13 + settings + exit + source×action path stays green under `StubUI` | `test_menu.py` |

Stage purity makes SUMMARIZE mockable, so the whole pipeline runs in CI with a stub summarizer (no
live API, killswitch-safe). The live `RichQuestionaryUI` adapter is TTY-bound →
**operator-verified on Windows**, no WSL unit test. CI gate (CLAUDE.md): ruff + mypy + tests.

---

## 10. NOT in scope (deferred, with rationale)

- **Chunked map-reduce / per-chunk checkpointing** (TD-5) — single-pass covers 1.5-2.5h; build when
  a real input trips the overflow guard.
- **Online ingestion** (TD-2 closed) — YouTube/Vimeo/URL permanently out.
- **`job.json` / persisted job engine** — artifacts are the recovery state.
- **Batch API** — 50% cheaper but async; wrong for interactive estimate-then-run.
- **Prompt caching** — negligible benefit for one-shot single-pass calls.
- **CPU-only fallback as a supported path** — possible but slow; not a v1.0 promise.
- **Plain-input / non-TTY fallback UI** (TD-7) — a personal interactive tool never hits it;
  fail-loud (§6.5) is enough.
- **Palette-as-data in `models.toml`** — a palette is dev taste; the theme module suffices.
- **Textual / full TUI framework** — questionary+rich is the local input-layer replacement.
- **DOCX/HTML export, prompt presets, GUI, batch list** — future work.

---

## 11. What already existed at the start

Nothing in-repo (greenfield). Throwaway spike scripts at `~/echogist-spike/` (step1_pull was
YouTube — now obsolete; step2_transcribe DLL registration, step3 token/cost+one call, step4
Cyrillic PDF are reference for transcribe/cost/render). The patterns were reused, not the spike code.

---

## 12. Implementation tasks

P1 blocks a working build; P2 same-branch; P3 follow-up. Effort where noted = human / CC-compressed.

### 12.1 Pipeline tasks (Phase 1)

- [x] **T1 (P1)** — config — editable model/price config + settings (lang/format/tier/threshold),
  deprecated-model guidance (F5).
- [x] **T2 (P1)** — launcher — `run.bat` provisioning: venv, **hash-pinned lockfile** deps
  (`uv pip compile --universal --generate-hashes`; `pip install --require-hashes`; ctranslate2↔cudnn
  major pin documented — §13.2), model fetch via **HF `snapshot_download` (Systran large-v3,
  `HF_HUB_DISABLE_XET`) + pre-placed-dir fallback** (F14), output dirs, key check, **GPU preflight
  diagnostic** (F8), cmd-only.
- [x] **T3 (P1)** — transcribe — faster-whisper GPU, DLL registration before import, timestamped
  autolang, save transcript; segment progress.
- [x] **T4 (P1)** — extract — imageio-ffmpeg video→mp3 / audio→mp3, dedup naming (F9, F11).
- [x] **T5 (P1)** — guard — **local language-aware** token estimate (conservative-high) vs context
  budget, clean overflow stop (F6).
- [x] **T6 (P1)** — summarize — single structured title+summary call; **persist raw result .json
  BEFORE render** (F13); F2/F4/F5/F10 branches, killswitch stub.
- [x] **T7 (P1)** — render — fpdf2 PDF + embedded DejaVuSans, MD, dedup (F9).
- [x] **T8 (P1)** — cost — estimate + threshold + actual from usage.
- [x] **T9 (P1)** — menu — colored loop, sources × actions, all returns-to-menu (F1-F7).
- [x] **T10 (P2)** — eval — RU + EN summarization quality eval (structure + plausible timecodes).
- [x] **T11 (P2)** — measure realtime_factor + int8 RU quality on the 4060 via the committed harness
  (§13.3); int8-vs-float16 A/B on the RU reference. Closed TD-4.
- [x] **T13 (P2)** — maintainer dev/ship loop (§13.1) — `win32`-only DLL shim; `scripts/dev-loop`
  (WSL pytest + optional real-GPU transcribe) and `scripts/win-smoke` (scripted cold-Windows
  acceptance). Closed TD-3.
- [x] **T12 (P3)** — first-run/usage docs ([USAGE.md](../USAGE.md)).

Pipeline build order: T1 → T2 → (T3, T4) → T5 → T6 → T7/T8 → T9 → T10/T11 → T13.

### 12.2 Console UX tasks (Phase 2)

- [x] **U1 (P1)** — transcribe: extract pure `_collect_segments(stream,total,progress)`.
- [x] **U2 (P1)** — ui: `UI` Protocol + `ProgressHandle`/`SpinnerHandle` Protocols +
  `RichQuestionaryUI` adapter + `StubUI`; `isatty()` fail-loud guard; print-fallback backstop.
- [x] **U3 (P1)** — theme: `theme.py` (rich.Theme + questionary.Style + glyph tables +
  `detect_caps`, honor `NO_COLOR`).
- [x] **U4 (P1)** — menu: rewire flows to `ui.*`; remove dead ANSI helpers.
- [x] **U5 (P1)** — cost: reshape `confirm_proceed` to a narrow `confirm` callable; keep threshold
  policy; below=acknowledge-proceed, above=default False.
- [x] **U6 (P1)** — tests: migrate `test_menu.py`→`StubUI` and `test_cost.py`→stub confirm.
- [x] **U7 (P1)** — deps: add `questionary==2.*` + `rich`; `scripts/lock-deps`; review the lock diff;
  Windows cold-run `--require-hashes` verify.
- [x] **U8 (P2)** — ui: `total==0` readout (segments+elapsed, no %/ETA, ✓); progress label carries
  the language+duration header.
- [x] **U9 (P1)** — ui: translate questionary cancel (None/Ctrl-C/Ctrl-D) → clean-exit; catch
  `KeyboardInterrupt`; keep `except EOFError` arms live.

Console build order:
```
Lane A: U7 deps        (independent — do first; deps must be importable)
Lane B: U3 theme       (independent)
Lane C: U1 transcribe  (independent — different module)
   └─ converge ─▶ U2 ui (needs U3+U7) ─▶ U5 cost ─▶ U4 menu ─▶ U8/U9 ─▶ U6 test migration
```
Conflict flag: U2, U8, U9 all touch `ui.py` → keep that chain sequential.

---

## 13. Maintainer DX (added by `/plan-devex-review`, 2026-06-14)

Scope: the **maintainer's** experience (the only developer), not a third-party developer surface —
EchoGist ships no SDK/API. Three areas.

### 13.1 WSL-dev → Windows-ship inner loop

The pain: code lives in WSL2 (Linux); it ships and runs on Windows (the `.bat`, the
`os.add_dll_directory` shim, Windows CUDA). Cold-running Windows on every change is the slow path.
Fix = make the OS boundary thin and run the fast loop in WSL.

- **One platform shim, everything else portable.** The only OS-specific lines are (a) the
  cuDNN/cuBLAS DLL registration and (b) `run.bat` provisioning. Gate the DLL step behind
  `if sys.platform == "win32": os.add_dll_directory(...)`. On WSL2 the same `nvidia-*-cu12` wheels
  are found via the linker (RPATH/`LD_LIBRARY_PATH`). Result: all pure stages import and run
  **identically** in WSL and Windows.
- **Fast loop = WSL with the real GPU.** WSL2 exposes the 4060 (CUDA via the Windows driver +
  `/usr/lib/wsl/lib`). Real GPU transcription runs in WSL for ~95% of iteration. `pytest` the pure
  stages with a **stub summarizer** (killswitch-safe, sub-second, no key, no Windows). This is
  `scripts/dev-loop`.
- **Slow loop = batched Windows cold runs.** Reserve Windows for the `.bat` provisioning sequence
  and the bundled-binary smoke. Run `scripts/win-smoke` (one `run.bat` + one canned ~2-min clip →
  mp3 + summary) as the "ship target still works" gate before a release-worthy commit.
- **Never share a `.venv` across the boundary.** Linux vs Windows wheels differ. Each OS keeps its
  own `.venv`; the **lockfile is the shared contract** (§13.2).

### 13.2 Pinned-deps lockfile (kills the #1 silent first-run failure)

Version skew between `ctranslate2` 4.x and the `nvidia-cudnn-cu12` / `nvidia-cublas-cu12` wheels is
the top silent failure (cuDNN major ABI mismatch → cryptic DLL load error).

- **Hash-pinned lockfile, not `>=`.** A tiny `requirements.in` → `requirements.lock` via
  `uv pip compile --universal --generate-hashes` (`scripts/lock-deps`). The lock pins exact versions
  + `--hash=sha256:...` for the full transitive closure. `run.bat` installs with `pip install
  --require-hashes -r requirements.lock` — a substituted/corrupt wheel fails loudly.
- **The one pin that matters: cuDNN major ↔ ctranslate2 build.** ctranslate2's binary is built
  against a specific cuDNN major (4.4+ → cuDNN 9; earlier 4.x → cuDNN 8). Pin the cudnn/cublas wheel
  majors to match, with a `# WHY` comment in `requirements.in`. This single comment is the skew
  tripwire.
- **Two platforms, ONE pass (uv universal).** `uv pip compile --universal` resolves a single version
  set valid across platforms and emits `--hash` lines covering every wheel; OS-only deps are
  marker-guarded. `--python-version 3.11` targets the Windows ship runtime. Acceptance gate stays the
  first Windows cold run (`pip install --require-hashes`).
- **Update protocol.** Bumping ctranslate2 = re-run `scripts/lock-deps` → Windows cold run + the
  §13.3 GPU smoke **before** committing the new lock. Never bump cudnn/cublas independently of
  ctranslate2. Keep Dependabot/Renovate **off** for these three.

### 13.3 The model "definition of done"

The int8 default was a hypothesis until measured; measurement is a repeatable gate.

- **A committed harness.** `scripts/measure_model.py` takes a fixed RU + EN clip and emits
  `realtime_factor` (`audio_sec / wall_sec`), peak VRAM, and the produced transcript + summary,
  writing `docs/measurements/<model>-<date>.md` so numbers are durable and diffable.
- **Fixed, reproducible fixtures.** One ~3-5 min RU clip + one EN clip; one command reproduces the
  whole measurement.
- **DoD gate.** `int8_float16` is "default-ratified" only when the harness shows (a) `realtime_factor
  ≥ bar` on the 4060 and (b) RU quality ≥ a **float16 control** on the same RU clip. The A/B is a
  `compute_type` flip on the one float16 `model.bin` — no second download, no second artifact.
- **Result (TD-4 closed).** Measured warm: `int8_float16` = **10.11x realtime, 3.46 GB VRAM**
  (bar 3.0x → PASS), `float16` = 10.27x, 5.39 GB. Verdict: **keep int8_float16** — same speed,
  ~2 GB less VRAM, int8 RU quality beat the float16 control. Report at
  `docs/measurements/large-v3-int8_float16-2026-06-16.md`.

---

## Appendix — review history (condensed)

| Phase | Review | Gate | Status |
|---|---|---|---|
| Pipeline | `/plan-eng-review` (2026-06-14) | Architecture & tests | CLEAR — scope-reduced; 0 unresolved, 0 critical gaps |
| Pipeline | `/plan-devex-review` (2026-06-14) | Maintainer DX | CLEAR — §13 added, 2 decisions ratified, 1 fix |
| Pipeline | `/codex review` | Independent 2nd opinion | 6 raised, 4 absorbed (#2 local estimate, #3 fixed output, #4 GPU preflight+pins, #5 model source+fallback, #6 persist raw before render), 2 settled (#1 single-pass premise, #7 local-GPU constraint) |
| Console UX | `/plan-eng-review` (2026-06-17) | Architecture & tests | ENG CLEARED — cross-model outside voice absorbed; UI seam keeps the killswitch CI offline/no-TTY |

CEO / design reviews were operator-scoped out (no GUI; scope set by operator).

**NO UNRESOLVED DECISIONS.**
