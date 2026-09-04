# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

- **TD-28 — a character the PDF cannot draw still reaches the PDF** · LOW (was MEDIUM) ·
  created 2026-09-04, HALVED the same day. Three of the seven summaries made `fpdf` report
  a missing glyph: `'描'`, `'技'`, `'催化剂'`. Two problems sat behind that one warning.
  1. **RESOLVED — it is not a gloss, and it is not fabrication.** The passages were read
     (the operator's `output/summaries/raw/*.json`): `как催化剂для перехода`,
     `материальной и技ической полноты`, `Преподаватель描написывает`. The model did not add
     a term — it substituted a Chinese morpheme for the RUSSIAN one, mid-word, three times
     in seven lectures. The meaning survives; the spelling breaks. So no
     `[интерпретация]:` obligation was skipped and no content was invented: this is
     language drift on a cheap tier, not a fidelity violation. Closed by the deterministic
     script check (`echogist/alphabet.py` + `summarize.report_foreign_scripts`) plus a
     prompt sentence, both shipped — see `docs/designs/script-check.md`.
  2. **STILL OPEN — the renderer degrades silently.** `render` prints the missing-glyph
     line and emits the PDF anyway, so the operator gets a document with holes and a
     warning that scrolls past. The script check now catches the cause BEFORE the document
     is opened, which drops this from silent corruption to cosmetic, but a character that
     cannot be drawn still should not reach the PDF unannounced.
  **Trigger for closure:** the next change that touches `render`'s font handling, or the
  first missing glyph that is NOT a language slip (a real symbol the material needs).
  **Blocks nothing.**

- **TD-27 — `output/` has no artifact-release concept; it is a flat dumping ground** ·
  **HIGH** · created 2026-09-04 (operator call, mid-session). The tree is fixed and flat:
  `output/{audio,transcripts,summaries}`, with `summaries/raw/` and `summaries/raw/.resume`
  underneath. Every run of every kind pours into the same three buckets, so a seven-lecture
  course, a one-off recording and last month's experiment are indistinguishable once saved.
  The operator asked mid-session for the folder run to write into a dated `date_bulk`
  directory; that change was **deliberately CANCELLED and escalated to this entry** rather
  than shipped, because the layout is load-bearing in ways a one-line path change would
  quietly break, and the right fix is a decision about what an EchoGist *artifact release*
  IS, not a new subdirectory.

  **What makes it load-bearing** (why this is HIGH and not a cosmetic tidy):
  1. **The transcript pool IS the checkpoint.** CLAUDE.md's recovery principle is
     "artifact-based recovery, not a job engine" — durable state is the saved artifact. Re-runs
     find prior work by looking in the FLAT `output/transcripts`. Move transcripts under a
     per-run folder and the next run stops finding them: on the operator's real folder that is
     ~3 hours of GPU time silently repeated.
  2. **The summary skip joins on resolved source path, not location** (TD-22,
     `summarize.summary_index`). It walks a known summaries directory. Any per-run split
     requires the index to search ACROSS run folders or the "already summarized, not re-paid
     for" guarantee — the one that protects money — silently stops holding.
  3. **26 hardcoded path expressions.** `deps.base / "output" / "<sub>"` is written inline
     across `menu.py` (12 sites), plus `provision.OUTPUT_SUBDIRS` and `scan._OUTPUT_DIR_NAME`.
     There is no path module. Any layout change is a 26-site edit today, and `scan._keep_dir`
     still prunes on the literal name `output` as its floor (TD-25 added the authoritative
     resolved-path check on top, but did not remove the string), so the layout and the
     self-exclusion rule are still coupled by a name a `paths` module should own.

  **The actual question to answer** (this is a design decision, not a refactor): what is the
  unit of release — a RUN (dated), a SOURCE COURSE (named after the input folder), or the
  current flat pool with grouping left to the file manager? Each answers re-run semantics
  differently: a dated unit re-transcribes or needs a cross-folder index; a named unit merges
  two different courses that share a folder name; the flat pool is what exists and scales badly
  past a few dozen files. Whatever wins, it must keep both guarantees above intact and should
  land a real `paths` module so the layout has ONE definition.

  **Trigger for closure:** before the operator's second real course goes through the folder
  run — that is the point where a flat `summaries/` stops being navigable and the decision can
  no longer be deferred. **Blocks nothing today**; the current flat layout is correct, just
  unscalable. Sequence it AFTER the menu/logging work, since a `paths` module is easier to
  land once the two-module menu has settled which flows exist.

The registry was fully closed on 2026-08-03 (TD-9 and TD-17 implemented, TD-7 and TD-20 WONTFIX,
branch `chore/close-tech-debt`); TD-22 through TD-28 are the entries since, of which TD-27 and TD-28 (its renderer half only) remain open: TD-22, TD-25
and TD-26 closed on 2026-09-04, and TD-23/TD-24 closed the day after the first full folder
run, off that run's own audited numbers. TD-29 closed 2026-09-04 with the `notice` channel, and
TD-28's fidelity half closed with it. The other open forward item is
T8 (offline LLM-judge groundedness eval), tracked in `docs/CURRENT_CONTEXT.md` as a P3 enhancement,
not debt.

---

## Closed debts (compact — verbose history in git)

- TD-29 — a dropped anchor printed in the same voice as a clean one · closed 2026-09-04 · `summarize` grew a SECOND channel, `notice`, alongside `log`: `log` is the sixty phase lines the operator scrolls past (menu wires `ui.detail`), `notice` is what they must act on (menu wires `ui.warn`). The anchor line routes itself — `(notice if dropped else log)(...)` — so `0 dropped` stays scenery and `4 dropped` does not. Two named channels rather than a severity argument on `Logger`: that alias is redefined in seven modules and `print` takes no level kwarg, and there are exactly two audiences here, not a spectrum. String-sniffing the message in `menu._progress` stays rejected. The channel is pinned by a test asserting a finding lands on `warn` and not `detail` — necessary because `SummarizeFn` is `Callable[..., ...]` and a stub's `**_kw` would otherwise swallow an unwired `notice` with the suite still green.
- TD-23 — the scan projection constants were unmeasured · closed 2026-09-04 · reseeded `150 wpm x 7 chars/word` -> `135 x 6.5` in `config/models.toml`. The shipped pair was a guess; measurement (lecture 4, 198 blocks, direct count) gives 122 wpm / 6.21 chars/word, corroborated across all seven lectures by the run's own gate. 150 x 7 = 1050 chars/min against a real ~760 was a 1.4x bias nobody had sized. The new pair keeps a deliberate ~1.15x margin over measured speech, and the whole-course scan quote now lands 1.31x the real $2.4003 bill — high, as required, but a KNOWN margin. Recalibration stays a two-number config edit.
- TD-24 — the scan quote was labelled an UPPER BOUND the arithmetic did not guarantee · closed 2026-09-04 · **both halves fixed, and the label was the smaller half.** The first full folder run proved the quote could run UNDER the bill: the gate said $2.2394 against $2.4003 (0.93x), and the scan said ~$2.20 against the same bill. Cause: `[guard].output_tokens_estimate`, a FLAT 4,600 output tokens for every call regardless of that call's size. Removed. Output is now projected per call as `tier.output_per_input_ratio x that call's input`, clamped by `[summarize].max_output_tokens` and floored, for the reconcile call only, at `reconcile_output_floor_tokens` (the one genuinely fixed per-call cost — without it a folder of short clips quotes like a single long file). The ratio is per-tier because it is a property of the model (Haiku 0.3707 measured over 59 calls; Sonnet 0.2225 over 5) and is re-seeded from any finished run's own "Actual cost" line: output/input, plus ~5%. Consequences: the quote no longer moves with the phase split (K=4 vs K=7 on the same lecture quoted $0.2204 vs $0.3062 for prose that is the same size either way), and the same run now quotes 1.07x its bill. The display label went from "UPPER BOUND" to "PROJECTION, biased high" with the measured margin named — the arithmetic is biased high in every term but a projection from DURATION is not a guarantee, and saying so is the honest half of the fix.

- TD-25 — `output/` pruning was by NAME, so a junction under another name leaked · closed 2026-09-04 · `scan.walk`/`scan_tree` take an `exclude` directory and prune any subtree whose RESOLVED path is it (compared through `os.path.normcase`, since Windows `resolve` does not fold case); `menu` passes `deps.base / "output"` at both scan sites. The literal-name rule stays as the floor for callers that do not know the layout (`batch.expand_selection`). The regression test does not need a junction: any artifact tree not literally named `output` reproduces it.
- TD-26 — Whisper boilerplate reached the summary · closed 2026-09-04 · `chunk.drop_degenerate_blocks` trims the synthesis INPUT only (the saved transcript stays verbatim ground truth); two measured, content-agnostic rules — a unique-word-ratio floor for the repetition loop, plus adjacency for the short credit line touching it. Drops 10 of 198 blocks on the real lecture with no false positives, and is reported to the operator rather than skipped silently.

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
