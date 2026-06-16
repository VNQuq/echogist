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