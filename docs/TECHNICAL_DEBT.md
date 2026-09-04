# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

- **TD-23 — The scan projection constants are unmeasured** · LOW · created 2026-09-04
  (bulk v3 increment 1). `config.ScanConfig` turns a file's DURATION into a token count with
  `words_per_minute = 150` and `chars_per_word = 7` (`config/models.toml`, `[scan]`), because at
  scan time nothing has been transcribed. Neither number is measured. Both are seeded HIGH on
  purpose — CLAUDE.md requires the estimate to run high, and the figure is labelled an upper bound
  in the console — but the SIZE of the margin is unknown, and a quote several times the real bill
  is nearly as useless as one that undershoots. Real Russian lecture speech runs roughly 110-130
  wpm at ~6 chars/word, so the shipped pair may be biased high by ~1.5x on top of the
  all-Cyrillic token rate. **Trigger for closure:** the first scan of a folder that already holds
  a transcript — compare the projected character count against `len(transcript_text)` and reseed
  both values from the ratio. Cheap, offline, and it needs no code change, only the two numbers in
  `models.toml`. **The 2026-09-04 real-folder scan did NOT close this**, contrary to the plan:
  all seven folders reported 0 transcript candidates, so the trigger condition was never met.
  An arithmetic cross-check against the validated 2026-06-27 run (Sonnet, 2h58m57s, $0.5237;
  economy is exactly a third of those prices, so ~$0.175) against the scan's own $0.093/hour
  puts the projection ~1.6x high — inside the band this entry predicted, but not a measurement.
  **The folder run does not depend on these constants**: it gates on the real transcripts
  (`bulk.folder_estimate`), so only the pre-transcription scan quote is still affected.
  **MEASURED 2026-09-04** against the first real transcript (lecture 4, 3h22m57s, 198 blocks):
  153,365 speech characters, 24,698 words = **122 wpm, 6.21 chars/word** — almost exactly the
  110-130 / ~6 band this entry predicted. The shipped 150 x 7 = 1050 chars/min against an actual
  756 is a **1.39x** high bias, which independently corroborates the folder run's own numbers
  (scan $2.20 vs the real-transcript gate $1.5244 = 1.44x). **Deliberately NOT reseeded yet:**
  this is n=1, the variance across the other six lectures is unmeasured, and the error direction
  that matters (under-quoting) is the one that spends unagreed money. Reseed to ~135 wpm /
  ~6.5 chars/word once a second and third transcript agree; until then the margin stays.

- **TD-24 — the scan quote is labelled an UPPER BOUND that the arithmetic does not guarantee** ·
  LOW · created 2026-09-04 (found by the increment-1 review army). `scan.project_file` prices
  output at `guard.output_tokens_estimate`, whose own docstring in `cost.py` calls it "a REALISTIC
  per-call output projection... NOT the API max_tokens cap", sized ~25% above the observed mean
  (TD-21). The INPUT side is biased high several ways over (all-Cyrillic token rate, 150 wpm, 7
  chars/word — see TD-23), so in practice the total almost certainly overshoots. But "upper bound"
  is a guarantee, and formally the output half does not make it: a run whose phases come back
  unusually verbose can exceed the quote. The console says UPPER BOUND in two places
  (`scan.totals_rows`, `menu._report_scan`). Two honest fixes, and the choice is the operator's:
  price the scan's output side at a real ceiling (diverges from the reviewed design, which
  specified `per_call_output_tokens=output_tokens_estimate`), or soften the label to "projected"
  and say what it is biased on. **Not urgent:** this figure is display-only. The actual spend gate
  (`cost.confirm_proceed`, `menu.py`) recomputes from the real transcript text, so a scan quote
  can mislead the folder-level preview but cannot let money out the door unseen.
  **2026-09-04, the first real run made this concrete and widened it.** `output_tokens_estimate`
  (4,600) is applied FLAT per call, independent of phase size, and both halves of that are now
  measurably wrong: (a) on the `economy` tier a ~22k-token phase produced MORE than the 8,192 cap
  and was truncated, so the projection ran UNDER the bill — the exact direction the "upper bound"
  label forbids; (b) because the model is flat, halving the phase size looks like it multiplies
  total output (K+1 calls x 4,600) when the real prose over the same material is roughly constant,
  so the quote now over-penalizes the very split that makes the run safe (this file: $0.2204 at
  K=4 vs $0.3062 at K=7, a delta that mostly is not real). The honest fix is to make the output
  projection PROPORTIONAL to each phase's input tokens with a per-tier ratio (Haiku >=0.39
  measured as a truncated lower bound, Sonnet ~0.18 from the 2026-06-27 run) instead of a flat
  constant. **Trigger for closure:** the first folder run that completes end to end on real
  material — its `Actually spent` total against the gate quote gives the per-tier ratio directly.

- **TD-25 — `output/` pruning is by NAME, so a junction under another name leaks** · LOW ·
  created 2026-09-04 (increment-1 review army). `scan._keep_dir` prunes a directory when
  `path.name.lower() == "output"`. On Windows an NTFS junction named anything else but pointing AT
  the real `output/` tree is walked through, and EchoGist's own artifacts are counted as sources —
  which is exactly what the prune exists to prevent, and what would make increment 2 re-process
  its own output. Not reachable on the Linux dev box (`os.walk` does not follow symlinks by
  default); reproduced by the reviewer only by forcing `followlinks=True` to simulate junction
  transparency. Fix: prune on the RESOLVED path against the known `output/` directory rather than
  on the name, which means `walk` has to learn where that is. **Trigger for closure:** the first
  real scan on Windows, or the start of increment 2 — whichever comes first, since increment 2 is
  where re-processing own output actually costs money. **Both triggers have now fired** (the
  real Windows scan ran 2026-09-04; the folder run shipped the same day) and it is still open.
  The scan found no evidence it bites on the real tree — exactly seven lecture files, no
  EchoGist artifacts counted — so it is a live risk only if the run is pointed at a folder
  containing a junction to `output/`.

- **TD-26 — Whisper hallucinates filler over non-speech, and it is summarized as content** ·
  MEDIUM · created 2026-09-04 (found while calibrating TD-23 on the first real transcript).
  The real lecture-4 transcript opens with SIX consecutive minute-blocks of
  `Субтитры создавал SubsAuthor` / `Добро пожаловать на наш канал!` — a well-known Whisper
  failure mode where the decoder emits training-set subtitle boilerplate over silence, music, or
  an intro screen. Twelve of the file's 198 blocks (~6%) carry it. This is not cosmetic: the
  synthesis prompt says the transcript is ground truth and instructs the model to cover every
  point and NOT to drop material, so the phase covering 00:00-00:06 will faithfully summarize a
  YouTube greeting that the author never said. That collides head-on with fidelity properties
  (1) grounded and (2) no fabrication — the fabrication enters via the transcript, upstream of
  the LLM, where none of the current gates look. The anchor validator does not catch it either:
  the timecodes are real, only the words are invented. **Fix candidates:** drop leading/trailing
  blocks whose body is a repeated known-boilerplate phrase; or a general run-length filter on
  identical consecutive block bodies (the top repeats here are 4x identical lines), which is
  content-agnostic and catches the whole class. **Trigger for closure:** before the next paid
  folder run — every one of the seven lectures is likely to carry the same intro artifact.
  **Not yet measured on the other six transcripts.**

The registry was fully closed on 2026-08-03 (TD-9 and TD-17 implemented, TD-7 and TD-20 WONTFIX,
branch `chore/close-tech-debt`); TD-22 through TD-25 are the entries since, of which TD-22 is
now closed and TD-23/24/25/26 remain open. The other open forward item is
T8 (offline LLM-judge groundedness eval), tracked in `docs/CURRENT_CONTEXT.md` as a P3 enhancement,
not debt.

---

## Closed debts (compact — verbose history in git)

- **TD-22 — A summary has no back-link to its source file** · CLOSED 2026-09-04 (bulk v3
  increment 2, commit `7000af4`). `Summary.source_path` carries the resolved source, stamped
  by `save_raw_result` at save time rather than by the summarize call (the back-link is
  provenance, not model output, and the tool schema never sets it). `summarize.summary_index`
  reads the stamps back and `bulk.plan_run` uses it to skip a source that already has a
  summary, so a second run over the same folder costs nothing — verified end to end against
  seven finished lectures: zero paid calls. The resolve rule moved to `naming.resolve_source`
  so the stamp and the scan's dedup key cannot drift apart. Older `.json` files have no field
  and read as "unknown source", never as "covered".

- **TD-9 — Cheap-call "press Enter to summarize" beat** ✓ CLOSED 2026-08-03 (was LOW, created 06-17;
  `chore/close-tech-debt`). `_run_summary` branches on `cost.requires_explicit_confirmation`: above threshold the
  explicit y/N gate (default No) is unchanged; at/below threshold behavior is now operator-controlled by a new
  Settings flag **`auto_accept_under_threshold` (default True)**. Default (on) → the cheap call proceeds
  immediately, the shown estimate is the acknowledgment. Off → a non-decision `ui.text("Press Enter to summarize,
  or Ctrl-C to cancel")` acknowledge beat first, so the operator can Ctrl-C out (→ the loop's clean EOFError)
  before spending. Auto-accept never affects the above-threshold path (that always confirms). The flag is
  optional in `settings.json` (absent → True, so an older file loads) and editable in menu #3. No `cost.py`
  change; the `$0.50` threshold is untouched. Tests: default skips the beat, off shows it once, above-threshold
  confirms regardless, and the toggle persists.
- **TD-17 — Progress bar shows 100% on a failed stage** ✓ CLOSED 2026-08-03 (was LOW, created 06-27 /review
  red-team; `chore/close-tech-debt`). `ProgressHandle` gained `fail()`; the `progress()` context manager (both
  `RichQuestionaryUI` and `StubUI`) now completes the bar only on a clean exit (`else`) and calls `fail()` on any
  `BaseException` (`stop_task` — freeze at the last real fraction, never snap to 100%) before re-raising. Fixed
  once on the shared handle, so it covers the transcribe bar as well as the MP3 conversion bar. The exception
  still propagates, so warn/degrade and fatal-return-to-menu are unchanged. Tests: fatal path records `["fail"]`,
  degrade path fails its bar then the next bar completes, clean run never records `fail`.
- **TD-7 — Plain-input / non-TTY fallback UI** ✗ WONTFIX 2026-08-03 (was LOW, created 06-17;
  `chore/close-tech-debt`). EchoGist is a single-operator INTERACTIVE tool — no batch/piped/scripted use case has
  ever materialised, and the adapter already exits cleanly on a non-TTY (`NotInteractiveError` → one-line message,
  no traceback). Carrying a second `PlainUI` for a case that never occurs is speculative. The `UI` Protocol seam
  stays available, so a `PlainUI` can be added later if a real batch need appears; that would be a fresh entry, not
  this debt.
- **TD-20 — PDF visual polish** ✗ WONTFIX 2026-08-03 (was LOW, created 06-27; `chore/close-tech-debt`). The first
  typographic pass (title 20pt + hairline rule, 14pt headings, 11pt body at 6.5 leading, near-black ink, spacing
  rhythm) was implemented and **operator-accepted on the re-run** ("reads well; a lot of text but fine for a 3h
  lecture — leave it"). With no concrete complaint and no completion criterion, further polish is a wish, not
  debt. A specific future readability gripe gets its own fresh entry.
- **TD-16 — v2 direct transcript synthesis (fidelity > completeness)** ✓ CLOSED 2026-06-27 (was MEDIUM, created
  06-26; operator principle reversal, eng-reviewed). The transcript is ground truth, read DIRECTLY into faithful
  prose (one hop): `plan_phases` (computed K, contiguous, overlap=0) → `synthesize_summary` ×K forward-only →
  per-phase `validate_anchors` (accept/snap-2s/drop) → reconcile header (K>1) → one readable doc. Supersedes TD-5
  + TD-15 (deleted in `43b7daf`). Shipped `7ad2185`/`1fdd3de`/`43b7daf`/`2b8ab6f` + `/review` Tier-1/2 (`52a36ef`).
  **Reference run #1 (06-27, $0.39) FAILED coverage** — phase 2 dropped ~22% of the lecture while every automated
  check passed (green anchors ≠ coverage); root cause was a prompt that never compelled span coverage + an
  over-firing "do not restate" block. Coverage-fix prompt landed `ba1b973`. **Re-run (06-27, balanced/Sonnet,
  2:58:57 RU, K=4, $0.5237) operator-ACCEPTED:** phase 2 covers its full 00:44→01:31 span (decider types,
  the four no-decision positions, fit criterion restored); anchors 131/131 + 9/9 resolve (validator log "0 dropped",
  re-checked offline); fidelity spot-check clean (12/12 sampled claims grounded, faithful negative stance, no
  fabrication). "the author-named point" absent because Whisper garbled the surname upstream (the concept — V1 / decision
  speed — IS covered); not a synthesis miss. T8 offline LLM-judge eval remains a P3 follow-on. Parallel synthesis
  OUT OF SCOPE (operator). Known limit (accepted, NARROWED by bulk v3 increment 0): artifact-resume is now
  keyed on a hash of the resolved source PATH, so two different files can no longer share a partial. Editing a
  transcript IN PLACE still reuses the same key — delete `raw/.resume/` before re-summarizing an edited
  transcript whose previous run died mid-way.
- **TD-18 — Phase headings had no document meta-frame** ✓ CLOSED 2026-06-27 (was MEDIUM). `emit_reconcile` returns
  a `phase_headings` array; `_apply_normalized_headings` swaps the forward-only headings into one coherent outline,
  fail-soft on count/empty mismatch; K=1 keeps its heading; `validate_anchors` strips inline timecodes from
  headings too. Validated on the re-run — the 4 headings read as one arc (`5d0df0c`).
- **TD-19 — Per-phase anchor footer dump** ✓ CLOSED 2026-06-27 (was LOW). Dropped the footer in PDF + Markdown;
  the prompt weaves a handful of `[HH:MM:SS]` INLINE in prose, the full `anchors` array stays in the `.json`.
  Validated on the re-run (131 inline timecodes, all resolve) (`5d0df0c`).
- **TD-21 — Pre-call cost estimate margin** ✓ CLOSED 2026-06-27 (was MEDIUM). `estimate_cost_synthesis` projects
  output from `per_call_output_tokens` (= `[guard].output_tokens_estimate`), not the `max_output_tokens` cap, and
  drops the phantom `K × cap` reconcile-input — killing the old ~2.4× ceiling. The re-run exposed the TD-21 value
  `2800` (fit to truncated run #1) as an UNDERSHOOT — estimate ran 0.91× the bill, breaking the high bias.
  Recalibrated `output_tokens_estimate` 2800 → 4600 (~25% over the re-run's ~3.7k/call mean): estimate now ~1.2×
  ($0.6330 vs $0.5237). Local offline arithmetic, no new paid run needed. `5d0df0c`, `230deee`.
- **TD-15 — Summary readability: hierarchical grouping** ✓ CLOSED 2026-06-26 (was MEDIUM, created 06-25).
  Superseded by TD-16: grouping navigated a 221-point map-reduce wall that direct synthesis never produces;
  the grouping code was deleted in `43b7daf`. Phase 1 render wins (paragraphing, owner-suppression) survive.
- **TD-5 — Chunked map-reduce summarization** ✓ CLOSED 2026-06-26 (was LOW, created 06-14). Shipped + paid-
  validated (179-min RU, $1.46, 221→221) but output was an unreadable flat wall; operator reversed to
  fidelity-over-completeness (TD-16). Machinery deleted in `43b7daf`. The two live bugs it surfaced (Claude
  4.x `temperature` 400; forced-tool extraction match) survive in the v2 caller.
- **TD-6 — Title can leak the source language** ✓ CLOSED 2026-06-25 (was LOW). Hardened the `title`
  instruction + schema desc to force the target language; paid T10 live gate confirmed. `94d7e93`, `5c8fbd4`.
- **TD-10 — File input forced manual path typing** ✓ CLOSED 2026-06-23 (was HIGH). `UI.pick_file` native
  tkinter dialog → `questionary.path()` fallback; last-dir persisted; operator Windows run confirmed.
  `8629ec2`/`8c67b68`/`9e8d768`.
- **TD-11 — Accumulated menu chrome** ✓ CLOSED 2026-06-23 (was MEDIUM). `UI.clear()` on flow entry (not
  menu-loop-top); full TUI ruled out of scope. `f4fd2ca`.
- **TD-13 — Submenu back-navigation** ✓ CLOSED 2026-06-23 (was MEDIUM). Explicit `← Back` entries; ESC stays
  exit (distinguishing it would risk the `_ask` cancel/exit contract). `f4fd2ca`.
- **TD-14 — Open Explorer at the saved folder** ✓ CLOSED 2026-06-23, REOPENED + RE-CLOSED 2026-06-27. Reopen: the
  once-per-launch `_revealed` bool popped `transcripts` after a summary (transcribe revealed first; `_run_summary`
  revealed nothing). Fix: `reveal_dir(path, *, priority)` — a higher priority supersedes a lower one already shown
  this launch; MP3-only pops `audio`, `_run_summary` pops `summaries` (wins over audio). NOTE: the original
  "transcripts are never revealed" rule was reversed by TD-12 — a transcript-only run now pops `transcripts`
  (`REVEAL_SUMMARY(3) > REVEAL_TRANSCRIPT(2) > REVEAL_AUDIO(1)`); the reveal lives in the transcript branch of
  `_flow_local_file`, never in `_transcribe_to_checkpoint`. Original close: `nt`-guarded once/launch + no-focus-steal
  `ShellExecuteW(SW_SHOWNOACTIVATE)`. `f4fd2ca`, 06-25 MP3-only ext.
- **TD-12 — "What should EchoGist produce?" menu misleading + gap** ✓ CLOSED 2026-06-21, REOPENED + RE-CLOSED
  2026-06-27 (design /office-hours, eng-reviewed). Reopen: non-mp3 menu (Summary / MP3 only / Both / Back) implied
  Summary needed no audio and had NO transcript-only path. Fix (MP3-as-baseline): video menu = `MP3 only / Summary /
  Transcript / Back` with semantic keys (`mp3`/`summary`/`transcript`); every video branch extracts + KEEPS the MP3
  in `output/audio` (MP3-only failure FATAL, Summary/Transcript DEGRADE — warn + continue, catching both
  ExtractError and bare OSError so a locked-folder `os.replace` can't abort the run; /review red-team fix). `.mp3` menu unchanged
  (`Summary / Transcript only`). Transcript-only now reveals `transcripts` (see TD-14). Source decodes the container
  directly (transcript quality unchanged); the MP3 is an added artifact. Design doc:
  `~/.gstack/projects/echogist/pc-main-design-20260627-104405.md`. Original close: mp3 trimmed menu via
  `extract.is_mp3`. `f4fd2ca`, `738cf9e`.
- **TD-8 — `setuptools<81` pin** ✓ CLOSED 2026-06-21 (premise dismissed). No such pin ever existed; lockfile
  ships `setuptools==82.0.1` and passes. The cuDNN↔ctranslate2 tripwire (the pin that matters) is untouched.
- **TD-3 — GPU provisioning automation** ✓ CLOSED 2026-06-16 (was MEDIUM). `run.bat` provisions idempotently;
  `gpu.register_cuda_libraries` + `PATH`/`ctypes.WinDLL` pin fixed ctranslate2's bare-name cuBLAS load on the
  4060. `b024368`, `173541d`, `e9a9cfb`.
- **TD-4 — GPU transcription speed unmeasured** ✓ CLOSED 2026-06-16 (was LOW). Warm: `int8_float16` =
  10.11x realtime / 3.46 GB (kept — same speed, ~2 GB less VRAM, better RU). `b93fd72`, `6bbb1fe`.
- **TD-1 — Whisper model runtime download** ✓ CLOSED 2026-06-15 (was MEDIUM/HIGH). Root cause = Xet transport
  (not RU region); fetch vanilla `Systran/faster-whisper-large-v3` with `HF_HUB_DISABLE_XET=1`, load
  `int8_float16`. Model arrives outside the lockfile (accepted). `1f25ef1`…`2748196`.
- **TD-2 — YouTube ingestion** ✓ CLOSED 2026-06-14 (was HIGH, obsolete). Eng-review falsified the bypass
  hypothesis; dropped all online/link ingestion — local files + saved transcript only, offline, no-credential.
