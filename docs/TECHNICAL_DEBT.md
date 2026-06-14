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
> All are gating inputs for `/plan-eng-review`.

### TD-1 — Whisper model cannot be downloaded at runtime (HF region-blocked)

Severity: HIGH · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` before any transcription code · SoT: this file

**What.** `faster-whisper`/`ctranslate2` pull the model from HuggingFace on first
use. From the target region (RU/MSK) HF is throttled to a crawl and the standard
mirror (`hf-mirror.com`) was unreachable too. Confirmed in BOTH the dev sandbox
and on the user's real Windows machine: `tiny` (~75 MB) stalled at ~2.6 MB and did
not advance; `large-v3` is ~3 GB. The CPU `int8` fallback is moot — it needs the
same un-downloadable model.

**Why deferred.** Needs an architectural decision, not a patch.

**When to open.** Now (eng-review). Candidate directions: (a) **bundle the model
in the installer/dist** (largest, most reliable); (b) **self-hosted / region-
reachable mirror with resumable download + retry/verify**; (c) configurable model
source + offline mode (`HF_HUB_OFFLINE`). Manual download is rejected (automated
app). Decision must also cover model choice vs size (large-v3 ~3 GB vs
distil/medium) under the download constraint.

### TD-2 — YouTube ingestion is a maintained subsystem, not a retry

Severity: HIGH · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` · SoT: this file
**Most product-critical of the open debts** — the main input depends on this path.

**Inputs (priority).** YouTube and local files are the **primary, expected** inputs;
Vimeo is secondary and rare (nice-to-have). Local files carry no ingestion risk, so
**YouTube is the fragile half of the main path** — its fragility is a **core product
risk, not an edge case**.

**What.** Downloading a YouTube link now requires a *stack* of cooperating pieces,
each of which breaks on YouTube's cadence: (1) **bot-check** needs authenticated
cookies; (2) **JS runtime** (deno) for the player; (3) **EJS challenge-solver
script distribution** for the `n`/signature challenge — without it only storyboard
images are offered ("Requested format is not available"); (4) **cookies**.
Browser-direct cookie reading (`--cookies-from-browser`) is **dead on modern
Chromium/Windows** (Brave/Chrome ≥127 app-bound encryption → `Failed to decrypt
with DPAPI`, yt-dlp #10927). A manually exported `cookies.txt` got past the
bot-check but cookies **expire fast** and manual re-export is rejected (automated app).

**Why deferred.** No clean automated path chosen yet; needs design + research (below).

**When to open.** Now (eng-review). The design's "self-update yt-dlp + retry once"
does NOT address any of this. Candidate directions: bundled + auto-updated yt-dlp;
bundled deno + auto-fetched EJS solver; an **automated, non-expiring auth path**
(managed logged-in browser profile / scheduled headless cookie refresh, or an
alternative extraction path), weighing ToS/2FA fragility.

**Research before deciding.** Many third-party YouTube downloader sites/services
work reliably in the target region — strong evidence a viable automated extraction
path exists, so do NOT rush a fragile cookie-based workaround. In `/plan-eng-review`,
first survey how working downloaders solve bot-check / n-signature / auth, then
evaluate those approaches before committing to one.

### TD-3 — GPU + JS runtime provisioning the launcher must automate

Severity: MEDIUM · Created 2026-06-14 · Trigger: resolve in `/plan-eng-review` (impl) · SoT: this file

**What.** Confirmed concrete (sharpens the design's flagged cuDNN asterisk):
`faster-whisper` 1.2 / `ctranslate2` 4.x do **not** auto-install cuDNN/cuBLAS.
On Windows the working fix is the pip wheels `nvidia-cudnn-cu12` +
`nvidia-cublas-cu12` plus registering their `...\nvidia\*\bin` dirs via
`os.add_dll_directory(...)` **before** importing `faster_whisper` (proven: DLLs
registered cleanly on the 4060, no cuDNN load error). Separately, yt-dlp needs a
**deno** runtime. The launcher must provision — automatically, idempotently —
cuDNN/cuBLAS DLLs, deno, plus the model (TD-1). Must be cmd/`.bat` (PowerShell
`.ps1` is blocked by execution policy by default — hit twice in the spike).

**Why deferred.** Provisioning belongs in the launcher/installer design.

**When to open.** Now (eng-review), as part of the first-run-contract spec.

### TD-4 — GPU transcription speed unmeasured

Severity: LOW · Created 2026-06-14 · Trigger: once TD-1 unblocks model access · SoT: this file

**What.** `realtime_factor` on the RTX 4060 (8 GB) for `large-v3` float16 was never
measured — blocked purely by TD-1 (no model bytes). The GPU env itself is proven
(CUDA visible, cuDNN DLLs load). Sizes the core UX (how long a 2 h video takes).

**Why deferred.** Cannot run without the model; not architecture-blocking.

**When to open.** First run after TD-1 is resolved.

---

## Closed debts

<TBD>