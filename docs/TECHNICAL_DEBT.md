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

### TD-1 — Whisper model cannot be downloaded at runtime (HF region-blocked)

Severity: HIGH · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` before any transcription code · SoT: this file

**What.** `faster-whisper`/`ctranslate2` pull the model from HuggingFace on first
use. From the target region (RU/MSK) HF is throttled to a crawl and the standard
mirror (`hf-mirror.com`) was unreachable too. Confirmed in BOTH the dev sandbox
and on the user's real Windows machine: `tiny` (~75 MB) stalled at ~2.6 MB and did
not advance; `large-v3` is ~3 GB. The CPU `int8` fallback is moot — it needs the
same un-downloadable model.

**Why deferred.** Needs an architectural decision, not a patch.

**Decision (2026-06-15, operator).** Provisioning fetches the vanilla CT2
`Systran/faster-whisper-large-v3` (float16 on disk) **direct from Hugging Face**
via `huggingface_hub.snapshot_download` into `local_dir`; T3 loads it with
`compute_type=int8_float16` (quantized at load — keep full large-v3, no
downscaling, no fine-tune). Implemented in `model_asset.fetch_from_hf` /
`ensure_model`; the pre-placed `local_dir/model.bin` escape hatch keeps it
offline-safe. The earlier **self-host zip-from-URL path is retained but dormant**
(`model_asset.py` "DORMANT" block + `[model_asset].source_url`/`sha256`), to be
removed only after the gate below is green.

**RETAINED RISK — this reverses the original "HF region-blocked" finding above.**
That finding was confirmed on the user's real Windows machine (tiny stalled at
~2.6 MB). The HF route is therefore unproven from the target region; the cold-run
gate is exactly what retires (or refutes) it. If HF stalls on the live run, the
dormant self-host path is the fallback — do NOT delete it until the gate passes.

**Tradeoff (accepted, logged).** The model now arrives **outside**
`requirements.lock` — integrity is HF's checksums, not our hash pins. Acceptable
for a personal tool.

**Acceptance gate (TD-1 closes when ALL pass).** On a cold Windows run, zero
manual hosting: (a) HF download of Systran large-v3 succeeds; (b) it loads on the
4060 with `compute_type=int8_float16` (`cuda devices: 1`, no cuDNN/cuBLAS DLL
error); (c) a short clip transcribes (show the log). Then remove the dormant
self-host fields + code.

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

**Why deferred.** Provisioning belongs in the launcher/installer design.

**When to open.** Now (eng-review), as part of the first-run-contract spec.

### TD-4 — GPU transcription speed unmeasured

Severity: LOW · Created 2026-06-14 · Trigger: once TD-1 unblocks model access · SoT: this file

**What.** `realtime_factor` on the RTX 4060 (8 GB) for `large-v3` float16 was never
measured — blocked purely by TD-1 (no model bytes). The GPU env itself is proven
(CUDA visible, cuDNN DLLs load). Sizes the core UX (how long a 2 h video takes).

**Why deferred.** Cannot run without the model; not architecture-blocking.

**When to open.** First run after TD-1 is resolved.

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