# EchoGist — v1 Engineering Plan

**Status:** locked by `/plan-eng-review` 2026-06-14
**Architecture:** pure-stage pipeline with artifact-based recovery (the agreed
single-pass realization of Approach C; not a persisted job engine)
**Authority:** [CLAUDE.md](../CLAUDE.md) · supersedes link-source parts of the
approved office-hours design (online ingestion dropped from scope)

No implementation code in this phase. This document is the build spec.

---

## 1. Scope (locked)

A single-`.bat` Windows console tool. Input is a **local audio/video file** or a
**saved transcript**. Output is an MP3 track and/or a structured RU/EN summary as
PDF (default) or Markdown. Transcription is local GPU Whisper; summarization is one
cloud Anthropic call. Fully offline except the summarization step. No online
sources, no logins, no analytics/history.

Cut from the original design (see TECHNICAL_DEBT TD-2 closed): all YouTube/Vimeo/URL
ingestion and its subsystem. Deferred (TD-5): chunked map-reduce + per-chunk
checkpointing — v1 is single-pass with a hard overflow guard.

---

## 2. Stack (locked)

| Concern | Choice | Note |
|---|---|---|
| Language | Python 3.11 (Windows ship, WSL dev) | manual prereq; launcher prints install steps if absent |
| Transcription | `faster-whisper` 1.2 / `ctranslate2` 4.x | GPU, timestamped, auto-detect RU/EN |
| GPU DLLs | `nvidia-cudnn-cu12` + `nvidia-cublas-cu12` (pip wheels) | `os.add_dll_directory(...)` BEFORE `import faster_whisper` |
| Model | large-v3 CT2 **int8_float16** (~1.5 GB) | default tier; from GitHub Release asset + `HF_HUB_OFFLINE=1` (TD-1) |
| Summarization | `anthropic` SDK (>=0.109) | single structured call; `output_config.format` |
| Token estimate | **local, language-aware** (bundled small tokenizer or per-script ratio) | offline; Cyrillic costs more tokens/char than Latin — estimate RU **high** |
| PDF | `fpdf2` + embedded **DejaVuSans** | Cyrillic, no tofu (spike-verified) |
| ffmpeg | **`imageio-ffmpeg`** (bundled static binary) | supersedes the design's winget flow — see §6 |
| Launcher | `run.bat` (cmd only) | `.ps1` blocked by execution policy |

**Editable model config (seeded, data not code — updatable without a code change):**

| Tier | Model ID | Context | Input $/MTok | Output $/MTok |
|---|---|---|---|---|
| economy | `claude-haiku-4-5` | 200K | 1 | 5 |
| balanced | `claude-sonnet-4-6` | 1M | 3 | 15 |
| flagship | `claude-opus-4-8` | 1M | 5 | 25 |

Verified against the Claude API reference at build time. Deprecated/unknown model
→ guide the user to re-select in settings (do not crash).

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
   output/summaries/<meaningful-title>.json      ← raw structured result, written on a
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

**Per-source action menus** (spec §5):
- local video → {MP3 only · summary only · both}
- local audio → {summary} (+ optional "convert to MP3" if source isn't MP3)
- saved transcript → {summary}

**Summary structure (spec §7, required minimum):** overview · key takeaways ·
section timecodes (`[HH:MM:SS]` carried from Whisper segments → suppresses
hallucinated timecodes) · recurring themes · core idea. Title comes from the SAME
structured call, sanitized for Windows-illegal chars `\ / : * ? " < > |`, truncated,
deduped per-artifact with `-2`/`-3`. No usable title → source-stem + date.

**Cost flow:** `[GUARD]` estimates transcript tokens **locally** (language-aware,
conservative-high for Cyrillic — no network call), shows estimated cost from the
price config: input from the local estimate, **output as a small fixed absolute
(~2K tokens)**, not a fraction of input (summary length is roughly constant
regardless of transcript length — outside-voice #3). Always shown; Enter for cheap
material, explicit y/n past the configurable cost-or-length threshold. After the
call, show **actual** cost from `response.usage` (the exact, audited number).

---

## 4. Overflow guard (replaces chunking in v1)

Before SUMMARIZE: `est_tokens = local_estimate(transcript + prompt)` — **local and
language-aware**, biased to estimate **high** (so an over-long transcript is caught,
never slips past). If `est_tokens > safe_budget(model)` (configurable; default e.g.
80% of the tier's context window), STOP cleanly:

> "This transcript is too long for v1 single-pass summarization. Choose a
> larger-context model in Settings, or wait for chunked summarization support."

Return to menu; transcript already saved. No silent truncation.

---

## 5. First-run provisioning (TD-3)

`run.bat` (idempotent, cmd-only) does, in order:
1. **Python check** — if `python` not in PATH or wrong major/minor, print exact
   install instructions and stop (manual prereq, never auto-installed).
2. **venv** — create/reuse a local `.venv`; call its python by absolute path.
3. **deps** — `pip install` the §2 set with **pinned, mutually-compatible
   versions** (cudnn/cublas wheels matched to the exact ctranslate2 4.x build —
   version skew is the #1 silent first-run failure, outside-voice #4). Pin in a
   lockfile, not `-U`.
4. **model fetch (TD-1)** — if the local model dir is absent, download large-v3
   int8_float16 with **resume + checksum verify**. Source is a **configurable URL**
   (default: the GitHub Release asset) and the launcher also **accepts a
   pre-placed local model dir** as a drop-in escape hatch — so a GitHub block from
   RU has an automated fallback (point the config at a mirror you host, or drop the
   files in) without any per-run manual step (outside-voice #5). On success set
   `HF_HUB_OFFLINE=1` for all runs. HF is never contacted.
5. **output dirs** — create `output/{audio,transcripts,summaries}`.
6. **GPU preflight + DLLs** — register the `nvidia/*/bin` dirs via
   `os.add_dll_directory(...)` **before** importing `faster_whisper`, then run a
   tiny CUDA op as a **preflight**. On failure, print a *specific* diagnostic
   (which DLL failed to load, NVIDIA driver too old, version skew) — never a cryptic
   import crash (outside-voice #4). The driver itself is a manual prereq (step 1's
   class): detect, guide, don't auto-install.
7. **API key** — validate `ANTHROPIC_API_KEY` only before a summarization action;
   MP3-only extraction must work with no key. (The GUARD is local, so it too runs
   key-free.)

Failure of any auto step → clear, copy-pasteable guidance, clean stop. No `.ps1`.

---

## 6. Decisions flagged for awareness (react if wrong)

Both items below were **ratified by the operator in `/plan-devex-review` (2026-06-14)** —
no longer "react if wrong", they are confirmed. The float16 fallback caveat was
added at ratification.

- **ffmpeg = bundled `imageio-ffmpeg` static binary** ✓ ratified, superseding the
  design's "detect on PATH / guided `winget install ffmpeg`" flow. Rationale: removes
  the user winget step and the "fresh winget shim isn't on the running session's PATH"
  problem the design itself flagged; aligns with your "no manual workarounds"
  stance. Net: MP3 extract/convert just works on first run.
- **Model default = large-v3 int8_float16** ✓ ratified (not float16, not distil).
  int8_float16 is ~1.5 GB (fits GitHub's 2 GB per-asset limit), expected near-identical
  RU quality, runs on the 4060. RU-quality of int8 is a **hypothesis** confirmed-or-
  overturned by the TD-4 measurement run (§12.3 / T11); distil is excluded
  (English-distilled, weak on RU).
  - **float16 fallback path (flagged):** if TD-4 shows int8 RU quality is not good
    enough, the fallback is large-v3 CT2 **float16 (~3 GB)**. 3 GB **exceeds GitHub's
    2 GB per-asset limit**, so float16 cannot ship as a single GitHub Release asset.
    The fallback distribution is therefore: a **split/multi-part asset** reassembled
    by `run.bat`, an **operator-hosted mirror**, or the **pre-placed model dir**
    escape hatch — all already covered by the configurable-source-URL + pre-placed-dir
    design (§5.4 / F14). No new architecture needed; just a heavier artifact. This is
    the only reason the source must stay configurable rather than a hardcoded GitHub URL.

---

## 7. Edge cases & failure modes (spec §12 + design)

| # | Failure | Test? | Error handling | User sees |
|---|---|---|---|---|
| F1 | Bad path / unsupported format | unit | classify→reject | clear msg, back to menu |
| F2 | No internet during SUMMARIZE | unit (mock) | catch APIConnectionError | "summarization step failed; your transcript/audio are saved" |
| F3 | Missing `ANTHROPIC_API_KEY` | unit | pre-check before summarize | guided steps; MP3-only still works |
| F4 | 429 / insufficient credit | unit (mock) | catch RateLimitError/billing | "retry from saved transcript later" |
| F5 | Deprecated/unknown model | unit (mock 404) | catch NotFoundError | "pick another model in Settings" (no crash) |
| F6 | Transcript over context budget | unit | overflow guard (§4) | clean "too long" message |
| F7 | Model download fails/unreachable (TD-1) | unit | resumable retry + checksum | "model download failed, retry / check connection" |
| F8 | cuDNN/CUDA load error / wheel skew / old driver | manual | GPU preflight (§5.6) catches before use | specific diagnostic (which DLL / driver), not a crash |
| F9 | Title sanitization collision | unit | per-artifact numeric suffix | deduped filename |
| F10 | Model returns no title | unit | fallback source-stem + date | named output, no crash |
| F11 | ffmpeg binary missing/corrupt | unit | imageio-ffmpeg presence check | clear msg, never silently skip |
| F12 | Interrupted mid-transcription | manual | no partial marker (accepted) | re-transcribe that file from scratch |
| F13 | RENDER fails after a paid summarize call | unit | raw result saved as .json BEFORE render (§3) | re-render from saved .json; never re-pay |
| F14 | Model source (GitHub) blocked from RU | manual | configurable source URL + pre-placed dir (§5.4) | point config at mirror / drop in files |

**Critical-gap check:** none of F1-F14 is both untested AND unhandled AND silent.
F12 is a known, accepted limitation (Whisper has no mid-file checkpoint), not a gap.

---

## 8. Test matrix (coverage target = 100% of v1 paths)

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
  ├ PDF Cyrillic+DejaVu / MD / dedup (F9)             ├ [→ test] under threshold → Enter
[+] cost.py                                           └ [→ test] over threshold → y/n
  ├ estimate / threshold Enter|y/n / actual         [+] First-run provisioning
[+] config.py (model IDs+prices, settings)           ├ [manual] cold run on Windows+4060
  ├ load / deprecated-model guidance (F5)             └ [→ test] missing key path (F3)
[+] menu.py (loop, all §12 returns)

EVAL: [→EVAL] summarization prompt — structure/quality on a real RU + EN transcript
      (overview/takeaways/timecodes/themes/core-idea present; timecodes plausible).
COVERAGE TARGET: every stage branch + every source×action flow + F1–F11 unit-tested;
                 F8/F12 + cold provisioning are manual (hardware/OS-bound).
```

Stage purity makes SUMMARIZE mockable, so the whole pipeline runs in CI with a stub
summarizer (no live API, killswitch-safe). CI gate (CLAUDE.md): ruff + mypy + tests.

---

## 9. NOT in scope (deferred, with rationale)

- **Chunked map-reduce / per-chunk checkpointing** (TD-5) — single-pass covers
  1.5-2.5h; build when a real input trips the overflow guard.
- **Online ingestion** (TD-2 closed) — YouTube/Vimeo/URL permanently out.
- **`job.json` / persisted job engine** — artifacts are the recovery state.
- **Batch API** — 50% cheaper but async; wrong for interactive estimate-then-run.
  Revisit only if a batch/queue mode is ever added.
- **Prompt caching** — negligible benefit for one-shot single-pass calls.
- **CPU-only fallback as a supported path** — possible but slow; not a v1 promise.
- **DOCX/HTML export, prompt presets, GUI, batch list** — spec §14 future work.

## 10. What already exists

Nothing in-repo (greenfield). Throwaway spike scripts at `~/echogist-spike/`
(step1_pull was YouTube — now obsolete; step2_transcribe DLL registration,
step3 token/cost+one call, step4 Cyrillic PDF are reference for transcribe/cost/
render). Reuse the patterns, not the spike code.

---

## 11. Implementation Tasks

Synthesized from this review. P1 blocks a working v1; P2 same-branch; P3 follow-up.

- [ ] **T1 (P1)** — config — editable model/price config + settings (lang/format/tier/threshold), deprecated-model guidance (F5). Verify: load + bad-model path.
- [ ] **T2 (P1)** — launcher — `run.bat` provisioning: venv, **hash-pinned lockfile** deps (`uv pip compile --universal --generate-hashes`; `pip install --require-hashes`; ctranslate2↔cudnn major pin documented — §12.2), model fetch w/ resume+checksum + **configurable source URL + pre-placed-dir fallback** (F14), output dirs, key check, **GPU preflight diagnostic** (F8), cmd-only (§5/TD-3). Verify: cold Windows run.
- [ ] **T3 (P1)** — transcribe — faster-whisper GPU, DLL registration before import, timestamped autolang, save transcript; segment progress. Verify: real 1.5-2.5h file (TD-4).
- [ ] **T4 (P1)** — extract — imageio-ffmpeg video→mp3 / audio→mp3, dedup naming (F9, F11). Verify: video+audio inputs.
- [ ] **T5 (P1)** — guard — **local language-aware** token estimate (conservative-high) vs context budget, clean overflow stop (F6). Verify: under/over budget, RU vs EN ratio.
- [ ] **T6 (P1)** — summarize — single structured title+summary call; **persist raw result .json BEFORE render** (F13); F2/F4/F5/F10 branches, killswitch stub. Verify: mocked errors + live RU/EN + render-fails-after-call.
- [ ] **T7 (P1)** — render — fpdf2 PDF + embedded DejaVuSans, MD, dedup (F9). Verify: Cyrillic renders, no tofu.
- [ ] **T8 (P1)** — cost — estimate + threshold Enter/y-n + actual from usage. Verify: both threshold sides.
- [ ] **T9 (P1)** — menu — colored loop, 3 sources × actions, all §12 returns-to-menu (F1-F7). Verify: each path returns cleanly.
- [ ] **T10 (P2)** — eval — RU + EN summarization quality eval (structure + plausible timecodes). Verify: eval suite passes.
- [ ] **T11 (P2)** — TD-4 — measure realtime_factor + int8 RU quality on the 4060 once T2/T3 land, **via the committed measurement harness (§12.3)**; int8-vs-float16 A/B on the RU reference. **DoD gate:** int8 default is ratified only when the harness records (a) realtime_factor ≥ the agreed bar and (b) RU quality ≥ the float16 control; else trigger the float16 fallback (§6). Verify: `docs/measurements/<model>-<date>.md` committed with numbers + verdict; closes TD-4.
- [ ] **T13 (P2)** — maintainer dev/ship loop (§12.1) — platform shim so the DLL-registration is `win32`-only and the pure stages import unchanged under WSL; `scripts/dev-loop` (WSL pytest + optional real-GPU transcribe of a fixture) and `scripts/win-smoke` (scripted cold-Windows acceptance: `run.bat` + one canned clip → mp3 + summary). Verify: pure-stage suite green in WSL; one win-smoke pass.
- [ ] **T12 (P3)** — README/spec already updated; document final first-run steps post-build.

Build order: T1 → T2 → (T3, T4) → T5 → T6 → T7/T8 → T9 → T10/T11 → T13 (loop scaffolding can land alongside T2).

---

## 12. Maintainer DX (added by `/plan-devex-review`, 2026-06-14)

Scope of this review: the **maintainer's** experience (you, the only developer), not
a third-party developer surface — EchoGist ships no SDK/API. Three areas.

### 12.1 WSL-dev → Windows-ship inner loop

The pain: code lives in WSL2 (Linux); it ships and runs on Windows (the `.bat`, the
`os.add_dll_directory` shim, Windows CUDA). Cold-running Windows on every change is
the slow path. Fix = make the OS boundary thin and run the fast loop in WSL.

- **One platform shim, everything else portable.** The only OS-specific lines are
  (a) the cuDNN/cuBLAS DLL registration and (b) `run.bat` provisioning. Gate the DLL
  step behind `if sys.platform == "win32": os.add_dll_directory(...)`. On WSL2 the
  same `nvidia-*-cu12` wheels are found via the linker (RPATH/`LD_LIBRARY_PATH`), no
  `add_dll_directory` needed. Result: `ingest/extract/transcribe/guard/summarize/`
  `render/cost/config/menu` import and run **identically** in WSL and Windows.
- **Fast loop = WSL with the real GPU.** WSL2 exposes the 4060 (CUDA via the Windows
  driver + `/usr/lib/wsl/lib`; `nvidia-smi` works). So real GPU transcription runs in
  WSL for ~95% of iteration. `pytest` the pure stages with a **stub summarizer**
  (killswitch-safe, sub-second, no key, no Windows). This is `scripts/dev-loop`.
- **Slow loop = batched Windows cold runs.** Reserve Windows for what only Windows
  validates: the `.bat` provisioning sequence (venv, `--require-hashes` install,
  model fetch, GPU preflight, execution-policy/PATH behavior) and the bundled-binary
  smoke (imageio-ffmpeg, DejaVu). Don't cold-run per change — run `scripts/win-smoke`
  (one `run.bat` + one canned ~2-min clip → mp3 + summary) as the "ship target still
  works" gate before a release-worthy commit.
- **Never share a `.venv` across the boundary.** Linux vs Windows wheels differ
  (the CUDA wheels especially). Each OS keeps its own `.venv`; the **lockfile is the
  shared contract** (§12.2). Reading the same tree via `\\wsl$\` is fine; copying
  `.venv` is not.

### 12.2 Pinned-deps lockfile (kills the #1 silent first-run failure)

Version skew between `ctranslate2` 4.x and the `nvidia-cudnn-cu12` / `nvidia-cublas-cu12`
wheels is the top silent failure (cuDNN major ABI mismatch → cryptic DLL load error).
Concrete approach:

- **Hash-pinned lockfile, not `>=`.** A tiny `requirements.in` → `requirements.lock`
  via `uv pip compile --universal --generate-hashes` (`scripts/lock-deps`). The lock
  pins exact versions + `--hash=sha256:...` for the full transitive closure: `ctranslate2`,
  `faster-whisper`, `nvidia-cudnn-cu12`, `nvidia-cublas-cu12`, `anthropic`, `fpdf2`,
  `imageio-ffmpeg`. `run.bat` installs with `pip install --require-hashes -r
  requirements.lock` — a substituted/corrupt wheel fails loudly, never silently.
- **The one pin that matters: cuDNN major ↔ ctranslate2 build.** ctranslate2's binary
  is built against a specific cuDNN major (4.4+ → cuDNN 9; earlier 4.x → cuDNN 8).
  Pin the cudnn/cublas wheel majors to match, with a `# WHY` comment in
  `requirements.in` so a future `pip install -U ctranslate2` can't quietly pull an
  incompatible cuDNN. This single comment is the skew tripwire.
- **Two platforms, ONE pass (uv universal).** CUDA wheels are platform-specific
  (`win_amd64` vs `manylinux`). `uv pip compile --universal` resolves a single version
  set valid across platforms and emits `--hash` lines covering every wheel of each
  pinned version; OS-only deps (e.g. `colorama ; sys_platform == 'win32'`) are
  marker-guarded. No second compile on Windows, no manual reconcile. `--python-version
  3.11` targets the Windows ship runtime (run.bat enforces 3.11) so the cp311 wheel
  tags align. Verified here: the lock dry-run-resolves under `--require-hashes` for
  both Windows/cp311 and Linux/cp312, with cp311 `win_amd64` hashes present for
  ctranslate2 + the cudnn/cublas stack. Acceptance gate stays the first Windows cold
  run (`pip install --require-hashes`) — uv gets platform-complete without a Windows box,
  but the cold-run install is the T2 sign-off.
- **Update protocol (closes the debt, keeps it closed).** Bumping ctranslate2 = re-run
  `scripts/lock-deps` → Windows cold run + the §12.3 GPU smoke **before** committing the new
  lock. Never bump cudnn/cublas independently of ctranslate2. Keep Dependabot/Renovate
  **off** for these three (or grouped + gated behind the GPU smoke) — an auto patch
  bump of cuDNN is exactly the skew trap.

### 12.3 TD-4 / T11 measurement = the model "definition of done"

The int8 default is a hypothesis until measured. Make measurement a repeatable gate,
not a one-off terminal session.

- **A committed harness.** `scripts/measure_model.py` (or a `@pytest.mark.bench` case)
  takes a fixed RU reference clip + a fixed EN clip and emits: `realtime_factor`
  (`audio_sec / wall_sec`), peak VRAM, and the produced transcript + summary for a
  quality read. It writes `docs/measurements/<model>-<date>.md` so numbers are durable
  and diffable, not lost in scrollback.
- **Fixed, reproducible fixtures.** One ~3-5 min RU clip + one EN clip, committed (LFS
  or a documented fetch). One command reproduces the whole measurement.
- **DoD gate.** `int8_float16` is "default-ratified" only when the harness shows
  (a) `realtime_factor ≥ <bar>` on the 4060 and (b) RU quality ≥ a **float16 control**
  run on the same RU clip. The harness runs **both** int8 and float16 once = the A/B
  that either confirms the §6 default or triggers the float16 fallback. `<bar>` is the
  one number to set with the operator at T11 (suggested starting point: ≥ 1.5× real
  time, i.e. a 2 h video transcribes in under ~80 min — confirm against the real
  measured value, don't hardcode blind).
- **TD-4 closes** when `docs/measurements/...` exists with numbers + an explicit
  verdict (keep int8 / switch to float16). That doc is T11's deliverable.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run (scope set by operator) |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 6 raised, 4 absorbed, 2 settled |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | scope-reduced; 0 unresolved, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | n/a (CLI console tool, no GUI) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR | scoped to maintainer DX (no external dev surface); §12 added, 2 decisions ratified, 1 fix |

- **CODEX:** outside voice ran via Claude subagent (Codex CLI not installed). 6 findings.
  Absorbed into the plan: #2 (count_tokens is a network call → local offline estimate),
  #3 (output as fixed ~2K, not a fraction), #4 (GPU preflight diagnostic + pinned wheels),
  #5 (configurable model source + pre-placed-dir fallback), #6 (persist raw summary before
  render). Settled, not actioned: #1 (single-pass premise — mitigated by 1M tiers + guard,
  operator decided twice), #7 (local-GPU vs cloud — fixed product constraint).
- **CROSS-MODEL:** one tension (token counting) resolved by operator → local language-aware
  estimate. All other outside-voice findings were additive hardening, folded in.
- **DEVEX (`/plan-devex-review`, 2026-06-14):** applicability-scoped — EchoGist has no
  third-party developer surface, so the persona/competitive/magical-moment passes were
  skipped (would be theater for a solo personal tool). Reviewed **maintainer DX** in 3
  areas, all written to §12: (1) WSL-dev→Windows-ship inner loop (one `win32` platform
  shim + WSL-GPU fast loop + batched `win-smoke`); (2) hash-pinned lockfile with the
  ctranslate2↔cuDNN-major tripwire + update protocol; (3) TD-4/T11 measurement harness
  as the model "definition of done" with an int8-vs-float16 DoD gate. Ratified 2 eng-review
  initiatives (imageio-ffmpeg ffmpeg; int8_float16 default) and flagged the float16
  (~3 GB > GitHub's 2 GB asset limit) fallback distribution path in §6. Fixed the §8
  matrix `count_tokens` → local language-aware estimate (no network call in GUARD). Added
  tasks T13 (dev/ship loop) and sharpened T2/T11.
- **VERDICT:** ENG CLEARED + DEVEX CLEARED — ready to implement. CEO/design reviews
  optional (operator-scoped, no GUI).

NO UNRESOLVED DECISIONS
