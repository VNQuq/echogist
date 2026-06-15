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

### TD-1 — Whisper model runtime download (root cause: Xet transport, not region) ✓ CLOSED

Severity (was): MEDIUM/HIGH · Created 2026-06-14 → Closed 2026-06-15 (T3, gate c) · SoT: this file

**Closed.** All three acceptance gates green on the 4060. Gate (c) passed via the
T3 `transcribe()` stage: the vanilla CT2 large-v3 loaded at `int8_float16`, autolang
detected the clip, and a verbatim timecoded segment came back — proving the model +
CUDA stack + transcribe path end to end. Full open-debt writeup retained below for
git history; severity/trigger fields are historical. (This record may be relocated to
the Closed section in a later compaction.)

**What.** `faster-whisper`/`ctranslate2` pull the model from HuggingFace on first
use, and `snapshot_download` stalled — `tiny` (~75 MB) hung at ~2.6 MB, `large-v3`
(~3 GB) hung after its small files. The original read was "HF region-blocked from
RU/MSK". **That read was wrong** (2026-06-15).

**Root cause (2026-06-15, evidence-backed).** Not the region — the **Xet transport**.
`requirements.lock` pins `huggingface-hub==1.19.0` + `hf-xet==1.5.1`; in hf_hub 1.x,
if `hf_xet` is installed it is used automatically, routing large-file transfers
through the Xet CAS hosts (`cas-bridge` / `transfer.xethub.hf.co`). Those hosts
stall on the operator's route; plain `huggingface.co` (the classic LFS path a
browser uses) works. Evidence: a browser download of `model.bin` over the system
VPN completed fine; from WSL (no VPN) the Xet data hosts timed out; and with
`HF_HUB_DISABLE_XET=1` the Python `snapshot_download` pulled `model.bin` at a steady
~10.5 MB/s with no stall. This is a known hf_xet bug class (xet-core#446,
huggingface_hub#3440), not a regional throttle.

**Fix (landed).** `model_asset.fetch_from_hf` sets `HF_HUB_DISABLE_XET` (via
`os.environ.setdefault`, so an operator on a Xet-reachable route can opt back in
with `HF_HUB_DISABLE_XET=0`) **before** importing huggingface_hub, forcing the
classic LFS path. Covered by `test_fetch_from_hf_disables_xet` +
`test_fetch_from_hf_respects_explicit_xet_optin`. Ruff + mypy + 48 tests green.

**Why deferred.** Needs an architectural decision, not a patch.

**Decision (2026-06-15, operator).** Provisioning fetches the vanilla CT2
`Systran/faster-whisper-large-v3` (float16 on disk) **direct from Hugging Face**
via `huggingface_hub.snapshot_download` into `local_dir`; T3 loads it with
`compute_type=int8_float16` (quantized at load — keep full large-v3, no
downscaling, no fine-tune). Implemented in `model_asset.fetch_from_hf` /
`ensure_model`; the pre-placed `local_dir/model.bin` escape hatch keeps it
offline-safe (the operator's actual fallback: browser-download the 5 files, drop
them in `local_dir`). The earlier self-host zip-from-URL path was **removed**
(2026-06-15) once gate (a) went green — `download_resumable`/`verify_checksum`/
`_extract_zip` + `[model_asset].source_url`/`sha256` are gone; `model_asset.py`
dropped 193→108 lines.

**Tradeoff (accepted, logged).** The model arrives **outside** `requirements.lock`
— integrity is HF's checksums, not our hash pins. Acceptable for a personal tool.

**Acceptance gate (TD-1 closes when ALL pass).** On the operator's Windows + 4060:
(a) HF download of Systran large-v3 succeeds with zero manual hosting — **✅ PASSED
2026-06-15** (Xet disabled, classic path, ~10.5 MB/s, no stall); (b) it loads with
`compute_type=int8_float16` on the 4060 — **✅ PASSED 2026-06-15** (`WhisperModel(...)`
printed `ok`, no cuDNN/cuBLAS DLL error; this also exercised the F8 DLL shim); (c) a
short clip transcribes (T3) — **✅ PASSED 2026-06-15** (the T3 `transcribe()` stage on
the 4060: large-v3 `int8_float16`, autolang `en`, verbatim segment with a correct
`[HH:MM:SS]` timecode; run in WSL against the pre-placed model via `/mnt/c`). The
pre-placed `local_dir/model.bin` escape hatch stays (operator used it, offline-safe)
and is now the sole fallback if HF ever fails. **TD-1 CLOSED** with T3.

### TD-3 — GPU provisioning the launcher must automate

Severity: MEDIUM · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` (impl) · SoT: this file

**What.** Confirmed concrete (sharpens the design's flagged cuDNN asterisk):
`faster-whisper` 1.2 / `ctranslate2` 4.x do **not** auto-install cuDNN/cuBLAS.
On Windows the working fix is the pip wheels `nvidia-cudnn-cu12` +
`nvidia-cublas-cu12` plus registering their `...\nvidia\*\bin` dirs via
`os.add_dll_directory(...)` **before** importing `faster_whisper` (proven: DLLs
registered cleanly on the 4060, no cuDNN load error). The launcher must provision —
automatically, idempotently — cuDNN/cuBLAS DLLs plus the model (TD-1). Must be
cmd/`.bat` (PowerShell `.ps1` is blocked by execution policy by default — hit twice
in the spike). **Update (eng-review):** deno provisioning is removed — it was only
needed for yt-dlp's JS challenge, and online ingestion was dropped from scope.
**Update (2026-06-15, gate b):** a second skew landmine surfaced and is fixed —
`ctranslate2` 4.5 imports `pkg_resources` but declares only unbounded `setuptools`;
setuptools 81 removed `pkg_resources`, so the resolver's latest (82) broke
`import ctranslate2`. Pinned `setuptools<81` in `requirements.in` (lock → 80.10.2).
**Update (2026-06-15, dep hygiene, `0b350bb`):** the `setuptools<81` shim is **removed** —
`ctranslate2` bumped 4.5→4.8, which replaced `import pkg_resources` with
`importlib.resources`, so setuptools rides latest (82.0.1) again. This exercised the
cuDNN-ABI tripwire (the one pin that matters): CHANGELOG confirmed no cuDNN-major change
4.5→4.8, and the bump was verified on a verify branch (`deps/ct2-4.8`) via a cold
`run.bat` `--require-hashes` install + int8_float16 GPU smoke on the 4060 **before**
fast-forwarding to `main`. Other runtime deps were already latest.

**Why deferred.** Provisioning belongs in the launcher/installer design.

**When to open.** Now (eng-review), as part of the first-run-contract spec.

### TD-4 — GPU transcription speed unmeasured

Severity: LOW · Created 2026-06-14 · Trigger: once TD-1 unblocks model access · SoT: this file

**What.** `realtime_factor` on the RTX 4060 (8 GB) for `large-v3` float16 was never
measured — blocked purely by TD-1 (no model bytes). The GPU env itself is proven
(CUDA visible, cuDNN DLLs load). Sizes the core UX (how long a 2 h video takes).

**Why deferred.** Cannot run without the model; not architecture-blocking.

**Update (2026-06-15, T3).** Unblocked — model access is solved (TD-1 closed) and the
T3 stage runs on the 4060. A first end-to-end run on an 11 s English clip showed
`realtime_factor` ≈ 0.35, but that is **warmup/model-load dominated** (3 GB `model.bin`
read over the `/mnt/c` 9p mount, cold first inference) and is **not** the measurement.
The real number needs T11's committed RU+EN fixtures via `scripts/measure_model.py`
(§12.3), warm, with peak-VRAM + the int8-vs-float16 A/B.

**When to open.** T11, via the committed measurement harness (not ad-hoc).

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

---

## Closed debts

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