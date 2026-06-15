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

### TD-3 — GPU provisioning the launcher must automate

Severity: MEDIUM · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` (impl) · SoT: this file

**What.** `faster-whisper` 1.2 / `ctranslate2` 4.x do **not** auto-install cuDNN/cuBLAS.
Fix: pip wheels `nvidia-cudnn-cu12` + `nvidia-cublas-cu12`, loaded before
`import faster_whisper` — on Windows via `os.add_dll_directory(...)` of `nvidia\*\bin`
(`gpu.register_cuda_libraries`), on WSL/Linux via `LD_LIBRARY_PATH` of the wheel
`lib` dirs (`scripts/dev-loop`). `run.bat` provisions DLLs + model idempotently;
cmd-only (`.ps1` is execution-policy-blocked). cuDNN-major↔ctranslate2 skew is the #1
silent failure — `requirements.in` carries the tripwire comment; bumping ctranslate2
re-runs lock-deps + Windows cold run + GPU smoke first (done for 4.5→4.8, dropping the
`setuptools<81` shim). Full skew history in git.

**Remaining.** The cuDNN/cuBLAS load path is proven green on the 4060 (T3 gate b/c, in
WSL). The Windows `run.bat` GPU **preflight** live-run is still unverified.

**When to open.** Now (first-run-contract spec); close on the next Windows cold run.

### TD-4 — GPU transcription speed unmeasured

Severity: LOW · Created 2026-06-14 · Trigger: once TD-1 unblocks model access · SoT: this file

**What.** `realtime_factor` on the RTX 4060 (8 GB) for `large-v3` float16 was never
measured — blocked purely by TD-1 (no model bytes). The GPU env itself is proven
(CUDA visible, cuDNN DLLs load). Sizes the core UX (how long a 2 h video takes).

**Why deferred.** Not architecture-blocking; needs a warm, fixtured measurement.

**Update (2026-06-15, T3).** Unblocked (model access solved, T3 runs on the 4060). An
11 s clip showed `realtime_factor` ≈ 0.35, but that is warmup/model-load dominated
(`/mnt/c` 9p read, cold first inference) — **not** the measurement.

**When to open.** T11, via the committed `scripts/measure_model.py` harness (warm,
RU+EN fixtures, peak-VRAM, int8-vs-float16 A/B) — not ad-hoc.

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