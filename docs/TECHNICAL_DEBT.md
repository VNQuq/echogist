# Technical Debt Registry

**Type:** living document · **Purpose:** the single place for all deliberate deferred decisions; every debt
has a deadline or trigger for closure.

**Principles.** (1) Debt ≠ forgotten — each item is deferred with a fixed date/condition. (2) Severity =
HIGH (blocks next phase) / MEDIUM (degrades quality) / LOW (cosmetic) / MINOR (wording). (3) On closing: status `closed` +
commit ref, kept in the compact one-liner form below; verbose history lives in git.

---

## Open debts

- **TD-37 — the Windows-only branches are type-checked nowhere** · LOW · created 2026-09-24.
  mypy pins `platform = "linux"` so one verdict holds on both CI runners; the price is that code
  behind `sys.platform != "win32"` / `os.name == "nt"` (the DLL shim in `gpu.py`, `msvcrt` and
  `os.startfile` in `ui.py`) is unreachable to it, and its `type: ignore`s can never be flagged
  unused. It was already so: dev has only ever run mypy on Linux. The fix is a second
  `mypy --platform win32` on the Windows job, which means rewriting ~9 guards so both passes are
  clean. **Trigger: the next change to `gpu.py` or to a Windows branch of `ui.py`.**

- **TD-38 — the dev venv is not the CI venv** · LOW · created 2026-09-24. CI installs the locks
  on 3.11; the WSL `.venv` is 3.12 with hand-installed packages (`anthropic` 1.3.0 against the
  lock's 0.109.1), and the lock cannot go into 3.12 — the CUDA wheels are cp311. A green local
  gate is therefore a strong hint, not a verdict; CI is the verdict. **Trigger: a local-green /
  CI-red disagreement, or rebuilding the dev venv for any other reason (rebuild it on 3.11 from
  both locks then).**

- **TD-36 — reconcile returns PARTIAL output, and an empty essence field is announced nowhere** ·
  MEDIUM · created 2026-09-06. Same run, 3 of 6 files lost something from the reconcile call: file 1
  the phase-heading outline, files 2 and 6 the title, and file 2 ALSO `main_skill` (`''`). The
  title losses were announced correctly — that is the 2026-09-05 empty-title fix working, twice —
  and every fallback is right. The defect is the unannounced one: CLAUDE.md defines the ESSENCE
  BLOCK as главная мысль / главный навык / 3 проверочных вопроса, and file 2 shipped with главный
  навык blank while the run reported success. One of three load-bearing elements went out empty and
  nothing on either channel said so. The partial-failure RATE is the second half of the entry: 1 of
  6 titles missing on run 2, 2 of 6 plus two other losses here, so reconcile is getting less
  reliable at this document size, not more. **Not fixed here: released as-is.** Fix has two parts,
  and the first is cheap: route every empty essence field to `notice`, the way the empty title
  already is. The second — why a Sonnet reconcile call drops fields on half the documents — is an
  investigation, not a patch, and should not be guessed at from six samples. Shares TD-34's root:
  nothing asserts a required field is non-empty. **First half CLOSED 2026-09-06 (`5539ee9`):**
  every empty essence field — core idea, key skill, self-check questions — is now announced on
  `notice`, so a blank главный навык can no longer ship under a run that reports success. It stays
  a notice and not a failure because the K paid phases of prose are intact; only the single
  reconcile call came back short, and re-running it is the operator's call against the saved
  transcript. **What remains open is the second half: WHY reconcile drops fields on half the
  documents at this size.** The rate is the evidence to collect — 1 of 6 titles on run 2, 2 of 6
  plus two other losses on run 3 — and the announcements added here are what will make the next
  run's rate readable without opening the `.json`. **Trigger:** the next paid folder run's notice
  lines, then `/plan-eng-review` if the rate holds.
  **2026-09-06, a 7th data point, and it kills the obvious hypothesis.** Lecture 1 re-summarized
  alone (EN, from the saved transcript) lost the title again — announced correctly this time — and
  its reconcile input was **~21,503 tokens, SMALLER than every one of run 3's six** (25,348–33,263).
  Ordered by reconcile input, the losses now fall at 21.5k (title), 30.2k (title) and 33.3k (title
  + `main_skill`), with 25.3k / 27.5k / 28.4k / 30.4k clean — so "reconcile drops fields when the
  document gets long" does not survive contact with the smallest document of the seven. Title-drop
  rate is **3 of 7**, and the essence fields are otherwise intact (`core_idea` 2,036 chars,
  `main_skill` 1,190, 3 questions, 8 themes). What is NOT ruled out and is now the cheapest thing
  to vary: **the title is the only field whose loss repeats on the same material** — lecture 1 kept
  its title in run 3 (RU) and lost it here (EN), one lecture, two languages, two outcomes.

The registry was fully closed on 2026-08-03 (TD-9 and TD-17 implemented, TD-7 and TD-20 WONTFIX,
branch `chore/close-tech-debt`); TD-22 through TD-28 are the entries since, and all of them are now
closed: TD-22, TD-25 and TD-26 on 2026-09-04, TD-23/TD-24 the day after the first full folder
run off that run's own audited numbers, and TD-27 on 2026-09-05. TD-29 closed 2026-09-04 with the
`notice` channel, which TD-28 then reused for its own renderer half on 2026-09-05. The other open
forward item is T8 (offline LLM-judge groundedness eval), tracked in `docs/CURRENT_CONTEXT.md` as a
P3 enhancement, not debt. TD-32 and TD-33 both came out of the operator's read of the six-file
`КУРС2025` run on 2026-09-05 and both closed the same week; TD-31 closed 2026-09-05 with
`dbdda98` and took TD-27's two load-bearing constraints with it, which is what left TD-27 answerable
as an ergonomics question rather than a design one. That state lasted one day: the third
`КУРС2025` run, on 2026-09-05, was the first exercise of the post-TD-31/TD-27/TD-33 build on
real material and opened TD-34, TD-35 and TD-36 off its log and its saved artifacts. v3.0 was cut
with all three open, deliberately and on the operator's call — the release carries TD-31's and
TD-27's artifact-format breaks, which is what it exists for, and none of the three was a regression
against v2.3.0: TD-35 was a stale constant that predates it, and TD-34 and TD-36 are pre-existing
gaps that this run's own new `notice` lines are what made visible. All three were answered the next
day, 2026-09-06 (`5539ee9`), before the next paid run: TD-34 and TD-35 closed, TD-36's first half
with them. **Open now: TD-36's second half (MEDIUM), TD-37 and TD-38 (LOW)** — TD-30 and TD-29b
closed 2026-09-06, the registry's two LOW entries answered the same day as its two HIGH ones;
TD-29c closed 2026-09-24 and CI opened TD-37 and TD-38 the same day.

---

## Closed debts (compact — verbose history in git)

- **TD-29c — no machine enforces the gate** · MINOR · closed 2026-09-24 (`d48ff07`). Its trigger
  was a second committer; publication is a second READER, and a public gate is only a claim until
  a machine runs it. `.github/workflows/ci.yml` runs ruff, ruff format, mypy and pytest on
  `windows-latest` and `ubuntu-latest`, both on 3.11, over the FULL hash-locked runtime lock plus a
  new `requirements-dev.lock` (the gate tools, pinned, resolved against it). The entry's premise
  that CI would skip the GPU wheels was dropped: the lock installs on a GPU-less runner, so the
  suite runs against the versions run.bat ships. **Reproducing its command locally before the first
  push earned it on day one:** dev
  had only ever run 3.12, whose float `sum` is compensated; 3.11's is not, so `guard` estimated
  `100 x 0.3` as 31 tokens and four guard tests failed on the ship target. Fixed with `math.fsum`.
  The same command on native Windows 3.11, the suite's first run on the ship OS, failed 16 more: 15 tests assumed posix
  (`/` in expected paths, `termios`, a console prompt_toolkit could open, the host's encoding) and
  one was the product — a folder row printed `Course-1\bonus/` there. mypy pins `platform = linux`
  so the nt branches' ignores read the same on both runners.

- **TD-30 — the script check trades EN drift detection for quiet** · LOW ·
  closed 2026-09-06. `_ALLOWED_SCRIPTS["en"]` allows Cyrillic so a faithful quotation of a
  Russian lecturer's own words is not a finding — which also means an EN summary that reverted
  wholesale to Russian is legal in every single character, i.e. the check was blind to the
  LARGEST version of the defect it exists to catch. **Closed with the cheaper half of the entry's
  own answer.** The entry proposed keying allowed scripts on (summary language, SOURCE language),
  which needs the detected language persisted — and metadata cannot go in the `.txt` (`chunk._blocks`
  would anchor it at `[00:00:00]`), so it would have to go in the transcript NAME: a third
  artifact-format break in a week, touching both joins and four single-level `glob` readers.
  **The proportional question needs no new data at all.** A quotation and a language switch are
  the same characters; only the SHARE of them tells the two apart. `alphabet.script_shares` counts
  letters by script and `summarize.report_language_drift` announces the largest non-primary share
  once it crosses `_DRIFT_SHARE`. **The threshold is measured, not chosen:** across run 3's six
  real RU documents — 253,289 letters — the non-primary share runs 0.0013–0.0088, and a wholesale
  switch measures above 0.95, so 0.25 sits ~28x over the loudest real document and ~4x under a
  drift. Reports and never raises, like its sibling; an uncalibrated language stays silent.
  **Re-measured 2026-09-06 on the first real EN-over-RU summary** (lecture 1, `language: en`, the
  full 3h05m document): the Cyrillic share is **0.0042** — the direction expected to quote the
  heaviest turns out to sit in the same band as the RU documents' incidental Latin, ~60x under the
  line. The threshold is now confirmed from BOTH sides, and it fired on neither, which is the
  correct outcome for a document that really is in the language it was asked for. The
  source-language pairing stays available if the share ever proves too blunt.

- **TD-29b — pointing a folder run at `output/` processes EchoGist's own artifacts** ·
  LOW · closed 2026-09-06. `scan.walk` applies its prune rule to `dirnames` only, never to the
  walk ROOT, so both the literal-name floor and the TD-25 resolved-path `exclude` were bypassed
  when the operator picked `output/` or `output/audio` in the folder picker — a plausible
  "summarize the MP3s I already made" gesture, and the picker opens on the last directory used.
  Result: every artifact re-transcribed, every summary re-bought (a re-encode has its own
  fingerprint, so the TD-31 join cannot recognize it), saved under doubly-dated names.
  **Its trigger fired twice** — TD-31 closed, and it happened. Closed the way the entry framed it,
  as a scope call decided by the operator (2026-09-06): **refuse the folder loud** and name the
  flow that does what the gesture meant. `scan.is_own_artifact_tree` is the predicate,
  `menu._flow_folder` refuses before the preview, so nothing is walked, counted or priced.
  **Resolved-path only, deliberately:** the name floor in `_keep_dir` exists for callers that do
  not know where the artifacts live, and applying it to the ROOT would also refuse an unrelated
  media folder the operator happens to have called `output`. Junction-proof through the same
  `_norm` key the walk's own exclusion uses. **Rejected:** warn-and-continue, which keeps the
  gesture but leaves the re-purchase and the triple-dating one keypress away.

- **TD-34 — a synthesis phase can come back EMPTY and take its stretch of the lecture with it** ·
  HIGH · closed 2026-09-06 (`5539ee9`). Run 3, file 1, phase 7/7 (02:40:57–03:05:34) returned
  `{"heading": "", "prose": "", "anchors": []}` — 43 bytes against 6,517–10,691 for its six
  siblings. The last 25 minutes of a 3h05m lecture, ~13% of the material, absent from the
  document; the call paid for; the run reporting success. The fix is the narrow one the entry
  called for: `synthesize_summary` refuses a phase whose `prose` is empty and fails the file loud,
  naming the phase and its span. **Not a widened anchor message** — zero anchors has two causes
  with two different operator actions (uncited prose: go read it; NO prose: go re-run the phase),
  and one line cannot name both. **Cheap to fail because nothing else is lost:** `on_phase` has
  already persisted every phase before it, so a retry from the saved transcript resumes and
  re-pays only the empty one, and `folder.run_phase` records the file `failed` and carries on
  with the rest of the folder — a bad phase costs one file, never an 18-hour run.

- **TD-35 — the `balanced` output/input ratio is stale, so the gate quote ran UNDER the bill** ·
  HIGH · closed 2026-09-06 (`5539ee9`). Run 3 quoted **$3.9616**, spent **$4.4634** — 0.89x, the
  TD-24 failure mode one day after TD-24 closed. Decomposed to a single number: the input estimate
  was 0.9987x (it is read off the real transcripts), the output estimate 0.812x, and 144,068 /
  600,193 = 0.2400 exactly — the whole miss WAS `[tiers.balanced].output_per_input_ratio`. Run 3
  is the clean unstitched whole-course measurement that retires 0.24 as an n=1 June guess:
  **0.29513** aggregate over 46 calls, per file 0.2828–0.3008, with run 2's 0.2805 as a second
  independent aggregate and the trend upward. **Re-seeded to 0.33, above the CLAUDE.md +5% rule
  (0.31) on the operator's call:** 0.31 quotes 1.03x the folder and would still under-quote a
  SINGLE file at the highest ratio ever measured (0.3171, run 2); 0.33 quotes 1.07x, the same
  margin economy carries, and the gate exists to be biased high. Config only — no code changed.
  The duration-based scan quote is unaffected and still high ($5.16 vs $4.4634); raising the ratio
  lifts it too, so it needs no separate fix.

- **TD-27 — `output/` has no artifact-release concept; it is a flat dumping ground** ·
  HIGH · closed 2026-09-05 (`5106175`, `383da25`, `61de114`). Opened as a design question —
  what IS an EchoGist artifact release — after a mid-session request for a dated `date_bulk`
  directory was cancelled rather than shipped. TD-31 removed two of its three constraints;
  reading the REAL pool on disk answered the third and reframed the whole entry.
  `transcripts/` reads perfectly, because TD-31 put the date, the source stem and the
  identity in its NAME. `summaries/` did not read at all: 13 files named by the model's title
  alone, no date, no lecture number, no way to tell two passes over one course apart, sorted
  alphabetically by a Russian LLM title. **The machine's working files were better organised
  than the operator's deliverables, and the entire difference was the filename.**
  So the answer is: **the tree stays FLAT and the grouping lives in the NAME** — the same
  move TD-31 made for the checkpoint. `naming.summary_artifact_stem` builds
  `<date>-<source>-<title>`, and because render is handed `base=json_path.stem` that one call
  names the whole `.json`/`.pdf`/`.md` triplet. **The source part truncates from the HEAD**,
  the opposite of the title: a downloaded lecture is `[VideoSite.org] Модуль «Основы», занятие 2
  12.03.24`, where the noise is an identical site tag at the front and the only discriminator
  is at the back — a tail cut would give six lectures of a course one name, the exact defect
  this closes. The total cap did not move off `_MAX_SUMMARY_STEM`, which is load-bearing
  beyond MAX_PATH: `render` re-runs `summary_stem` over the base it is handed, so a longer
  stem would come back SHORTER for the `.pdf` than the `.json` and split the triplet silently.
  The date is the RUN's, taken once per folder run — these runs have gone 18 hours and one
  started before midnight would otherwise split a course into two dated blocks.
  **Rejected, and why.** *Subdirectories* (per course or per dated run): all four readers of
  this tree — `scan.transcript_sources`, `summarize.summary_index`, `menu._pick_transcript`,
  `menu._sweep_stale_resumes` — are single-level `glob`, so they would find zero files and
  re-buy summaries already paid for; also the course name is carried nowhere (`root.name`
  appears once, in a console prompt). *single vs bulk*: splits by how a run was launched, not
  by what the artifact is — both paths call the same functions and write the same names, one
  course lands on both sides the moment a file is run alone, and it fixes none of the
  readability problems (all 13 files came from bulk runs).
  The refactor half landed too: `paths` owns the layout, replacing 24 inline expressions and
  the second spelling of `output` that `scan._keep_dir` used as its pruning floor — layout and
  self-exclusion can no longer drift apart. It computes and never creates; provisioning reads
  `all_dirs` from it. **No backfill:** the 13 existing summaries keep their names and
  self-segregate anyway, since their stems start with a letter and every new one with a digit.
  The joins were verified against the real pool AFTER the rename — `summary_index` 13,
  `transcript_sources` 13, agreeing on 13 — because they key on the fingerprint stamped inside
  the `.json`, never on a name.

- **TD-33 — the loop detector could not tell a stuck decoder from a lecturer making a point** ·
  MEDIUM · closed 2026-09-05. Opened believing the unit was too coarse and the fix was sub-block
  trimming. Measuring the 13 saved transcripts (2451 blocks) showed the opposite: there is almost
  nothing to trim — exactly ONE block in 13 lectures is genuinely mixed — and the real defect was
  the metric. `_unique_word_ratio` conflated a Whisper decoder loop with rhetorical repetition,
  which this lecturer uses constantly: a Socratic drill answered "Сил, опыта, терпения"
  five times scored 0.550 against the 0.55 floor and was deleted, as was a student's 181-word
  confession at 0.547. **The rule was destroying 39 blocks of genuine lecture (5172 words, ~39
  minutes) to catch 18 loops.** Replaced by `_loop_coverage`: the share of a block's word
  POSITIONS blanketed by repeats of its most repeated 3-gram. A decoder loop repeats one phrase
  verbatim (measured 0.60-1.00); rhetorical repetition is scattered among varied sentences (never
  above 0.20). At `max_loop_coverage = 0.50` the corpus gives 18 loops caught and 0 real blocks
  lost, TD-26's original `Субтитры сделал <name>` still caught at 1.00. The "WHOLE blocks only"
  decision was NOT reversed — it never needed to be. Calibrated on ONE speaker in ONE language.
  Residual: the single mixed block (215 words, ~37% loop) now reaches the model intact; keeping
  135 words of real speech was judged the better side of that trade.
- **TD-31 — a transcript could not say which recording it came from** · HIGH · closed 2026-09-05
  (`dbdda98`). Identity is now the recording's CONTENT (`naming.source_fingerprint`: sha256 over
  size + first and last mebibyte, 16 hex) and it lives in the transcript's FILENAME, never inside
  the file — the body is the exact text the summarizer reads, and a metadata line in it would
  become block #1 at `[00:00:00]` and could be quoted back with an anchor that passes validation.
  One scheme for both joins: `Summary.source_path` became `source_fingerprint`, so the
  already-paid-for skip keys on the same value. Both joins now survive a tree move, a rename and a
  WSL-vs-Windows read. **Mandatory contract, pinned by a test:** the fingerprint is taken once
  from the original recording and inherited forward — a re-encode (increment 1b) must carry its
  parent's value, never recompute, or it re-buys a summary. Stem matching deleted entirely per the
  clean-slate rule for 3.0, including the `-N` ambiguity machinery of 2026-09-04. Design doc:
  `docs/designs/td-31-transcript-identity.md`. Also closed the adjacent half: the recovery flow
  stamped the `.txt` path as the summary's source, so such a summary was invisible to the next
  folder run and the lecture was paid for twice.

- TD-32 — the per-file section rule wore the same cyan as ordinary output · closed 2026-09-05 · `rule` held the bare `cyan` that `info` already owned, so `---- [3/6] lecture.mp4 ----` was drawn in the colour and weight of the `Extracting audio ->` lines it separates: width without rank, and an hour-old 18-hour folder run had to be read line by line to find where a file began — the exact wall `UI.rule` exists to break. Closed by splitting the style in two, which the entry named as the fallback and which turned out to be the better shape outright: `rule` (`#005f87`, dark steel-blue, used by nothing else) draws the line, `rule.title` (`bold #005f87`) draws the file name. The name keeps its legibility through WEIGHT rather than brightness — a brighter title would have undone the separation the darker line just bought. `UI.rule` handed one style name to both the `Text` and the `Rule`, which is what coupled them. One theme edit plus one call site; no behaviour, artifact or config contract moved, and `NO_COLOR`/legacy-`cmd` consoles are untouched (rich drops colour there and the ASCII `-` glyph already carries the separation). Pinned by three tests: the rule colour differs from `info` and `heading`, the title shares the line's colour and adds bold, and `UI.rule` passes the two names separately.
- TD-28 — a character the PDF font cannot draw reached the PDF unannounced · closed 2026-09-05 · **both halves now closed.** The fidelity half closed 2026-09-04 (the passages were read: a Chinese morpheme substituted for a Russian one mid-word, three times in seven lectures — language drift, not fabrication; the deterministic script check plus the prompt sentence answer it). The renderer half closes here: `render` reads the bundled font's cmap with `fontTools` (fpdf2's own locked dependency, so no new install) and announces every DISTINCT undrawable character, with its codepoint and one quote of where it sits, on the loud `notice` channel — the same channel as a dropped anchor, wired to `ui.warn` — BEFORE the file is written. fpdf2 does notice the missing glyph, but it says so through its own `logging` warning at output time, off EchoGist's channels and after layout, which is exactly how it scrolled past. Reports and returns: the character is never substituted (that would be silent rewriting) and the PDF is still written (refusing it would throw away a summary already paid for) — the same division as `report_foreign_scripts`. Coverage is the INTERSECTION of the regular and bold faces, the corpus is the Markdown rendering of the same document, and an unreadable font yields an empty charset that reports nothing (fail-soft, like an empty `allowed`). Wired at the seam with a test, because `RenderFn` is `Callable[..., Path]` and an unpassed `notice` would fall back to `print` with mypy and the suite both green.
- TD-29 — a dropped anchor printed in the same voice as a clean one · closed 2026-09-04 · `summarize` grew a SECOND channel, `notice`, alongside `log`: `log` is the sixty phase lines the operator scrolls past (menu wires `ui.detail`), `notice` is what they must act on (menu wires `ui.warn`). The anchor line routes itself — `(notice if dropped else log)(...)` — so `0 dropped` stays scenery and `4 dropped` does not. Two named channels rather than a severity argument on `Logger`: that alias is redefined in seven modules and `print` takes no level kwarg, and there are exactly two audiences here, not a spectrum. String-sniffing the message in `menu._progress` stays rejected. The channel is pinned by a test asserting a finding lands on `warn` and not `detail` — necessary because `SummarizeFn` is `Callable[..., ...]` and a stub's `**_kw` would otherwise swallow an unwired `notice` with the suite still green.
- TD-23 — the scan projection constants were unmeasured · closed 2026-09-04 · reseeded `150 wpm x 7 chars/word` -> `135 x 6.5` in `config/models.toml`. The shipped pair was a guess; measurement (lecture 4, 198 blocks, direct count) gives 122 wpm / 6.21 chars/word, corroborated across all seven lectures by the run's own gate. 150 x 7 = 1050 chars/min against a real ~760 was a 1.4x bias nobody had sized. The new pair keeps a deliberate ~1.15x margin over measured speech, and the whole-course scan quote now lands 1.31x the real $2.4003 bill — high, as required, but a KNOWN margin. Recalibration stays a two-number config edit.
- TD-24 — the scan quote was labelled an UPPER BOUND the arithmetic did not guarantee · closed 2026-09-04 · **both halves fixed, and the label was the smaller half.** The first full folder run proved the quote could run UNDER the bill: the gate said $2.2394 against $2.4003 (0.93x), and the scan said ~$2.20 against the same bill. Cause: `[guard].output_tokens_estimate`, a FLAT 4,600 output tokens for every call regardless of that call's size. Removed. Output is now projected per call as `tier.output_per_input_ratio x that call's input`, clamped by `[summarize].max_output_tokens` and floored, for the reconcile call only, at `reconcile_output_floor_tokens` (the one genuinely fixed per-call cost — without it a folder of short clips quotes like a single long file). The ratio is per-tier because it is a property of the model (Haiku 0.3707 measured over 59 calls; Sonnet 0.2225 over 5) and is re-seeded from any finished run's own "Actual cost" line: output/input, plus ~5%. Consequences: the quote no longer moves with the phase split (K=4 vs K=7 on the same lecture quoted $0.2204 vs $0.3062 for prose that is the same size either way), and the same run now quotes 1.07x its bill. The display label went from "UPPER BOUND" to "PROJECTION, biased high" with the measured margin named — the arithmetic is biased high in every term but a projection from DURATION is not a guarantee, and saying so is the honest half of the fix.

- TD-25 — `output/` pruning was by NAME, so a junction under another name leaked · closed 2026-09-04 · `scan.walk`/`scan_tree` take an `exclude` directory and prune any subtree whose RESOLVED path is it (compared through `os.path.normcase`, since Windows `resolve` does not fold case); `menu` passes `deps.base / "output"` at both scan sites. The literal-name rule stays as the floor, but its last caller is gone: `folder.expand_selection` walks one level with no `exclude`, and since 2026-09-05 the folder module hands it the scan's own file list instead of a directory, so every folder action now prunes by resolved path. The regression test does not need a junction: any artifact tree not literally named `output` reproduces it.
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
