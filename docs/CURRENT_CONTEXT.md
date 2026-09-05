# Current Context

**Updated:** 2026-09-06 · **v3.0.0 released**, carrying TD-31's and TD-27's artifact-format
breaks. **Released with TD-34 + TD-35 (HIGH) and TD-36 (MEDIUM) open, on the operator's call** —
none is a regression against v2.3.0; run 3's own new `notice` lines are what made two of them
visible. Calibration: run 1 (7 RU lectures, Haiku, **$2.4003**) seeded the cost model; run 2
(`КУРС2025`, 6 lectures, **balanced**, **$3.7246**) closed TD-31 + TD-33 off its 13
transcripts; **run 3** (same 6 lectures, 18h18m, first run of the 3.0 build, **$4.4634**) is the
clean unstitched measurement — **0 CJK slips, 0 undrawable chars**, 6/6 transcripts reused by
fingerprint, 1 loop block dropped where the old rule dropped 39 per 13 files.
**Authority:** [CLAUDE.md](../CLAUDE.md); TECHNICAL_DEBT TD-16 for the v2 rule's validation.
**Max: ~88 lines** — only what the code and the CHANGELOG cannot tell you. Cut, don't append.

## Live invariants — break one and something regresses silently

- **The folder preview is AUTOMATIC and the three actions must agree with it.** `mp3` gets that
  walk's file LIST, not the root. An interrupted walk is silent about the folder, never a
  verdict; an empty PLAN means "already done" only if there were files to plan.
- **The loop detector measures REPETITION SHAPE, not vocabulary** (TD-33). A stuck decoder
  repeats one phrase verbatim (3-gram coverage 0.60-1.00); this lecturer repeats phrases to
  teach (never above 0.20). The old unique-word ratio could not tell them apart and deleted
  39 blocks / 5172 words of real lecture per 13 files. WHOLE blocks only, still.
- **Two channels, from `summarize` AND `render`** (TD-29, TD-28). `log` = chatter, muted;
  `notice` = act on it, loud: a DROPPED anchor, a phase citing NOTHING over a timecoded
  transcript, a foreign script, an unfont-able character, an empty title. **Do not merge them**;
  pass `notice=` to EVERY `validate_anchors` call — the per-phase one drops.
- **The script check is an instrument** ([design](./designs/script-check.md)): it MEASURES
  whether the prompt sentence works, so keep it. Notation is not a script (TD-30).
- **One module, two engines** (`folder.py`): `convert_many` = parallel pool, `run_phase` =
  strictly sequential. **Do not push the run through the pool** — the gate must price a whole
  folder. Its plan is SORTED, not `os.walk` order.
- **Name-claiming** is in memory (`folder._plan_output_paths`) — an on-disk claim survived
  `kill -9` as a fake artifact and poisoned dedup forever. One publish scheme,
  `naming.publish_text`, on EVERY durable write. **Casefold is asymmetric ON PURPOSE:**
  `_resume_key` folds, `naming.resolve_source` does not (folding dropped a recording).
- **Grouping lives in the artifact's NAME, and the tree stays FLAT** (TD-27). A summary is
  `<date>-<source stem>-<title>`; the date is the RUN's, taken ONCE per folder run (an 18h run
  crosses midnight and would split a course in two). The source part truncates from the HEAD —
  a download's noise is the site tag in front, the discriminator is the lecture number at the back.
  The total cap is `naming._MAX_SUMMARY_STEM` and cannot rise alone: `render` re-runs
  `summary_stem` over the base, so a longer stem splits the triplet silently. **Do not add a
  subdirectory** — `transcript_sources`, `summary_index`, `_pick_transcript` and
  `_sweep_stale_resumes` are all single-level `glob` and would silently find nothing.
  `paths` is the ONE definition of the layout; it computes and never creates.
- **Identity is CONTENT and lives in the transcript's NAME** (TD-31). `fp = sha256(size +
  head/tail 1 MiB)[:16]`; both joins key on it, so a move or rename costs nothing and two
  courses with identical filenames never share a transcript. **Taken ONCE from the original and
  inherited forward** — a re-encode (1b) carries its parent's value or it re-buys the summary.
  Never put metadata IN the .txt: `chunk._blocks` anchors it at `[00:00:00]` and the model
  could cite it past validation.
- **Ctrl-C stops the run, not the app** — `folder.Cancelled` carries the partial report. The
  WHOLE loop iteration is guarded (BaseException). A resume partial is keyed by path + language
  + tier, so a tier switch re-runs, never mixes.
- **Essence block** is phase prose only; reconcile ALWAYS runs; every emitted string is
  anchor-validated, title included — an EMPTY title says so.
- **Cost model** projects output as `ratio x that call's input`. **Re-seed a tier from its own
  "Actual cost" line: output/input, +~5%.** economy 0.39; **balanced 0.24 is now MEASURED WRONG**
  (TD-35): run 3 gives 0.29513 aggregate, 0.2828-0.3008 across 6 files, so the gate quotes 0.89x
  its bill. The input leg is near-exact (0.9987x) — the whole miss is this one number. Token
  estimate: 3 rates by script.
- **Key:** env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are
  Windows-only; `/mnt/c/Users/operator/Documents/echogist/output/` reads that tree from here.
  **The real pool is migrated (2026-09-05):** 13/13 summaries and 13/13 transcripts carry
  their fingerprint, both indexes agree on all 13, nothing on disk is pre-TD-31 any more.

## Next

1. **TD-35 first — it is a two-number `config/models.toml` edit and every `balanced` run until
   then quotes under its bill.** The measurement is done; only the pick is open: 0.31 (the
   CLAUDE.md +5% rule, quotes 1.03x) or 0.33 (1.07x, also covers the worst file ever seen,
   0.3171). Operator's call, deliberately not made.
2. **TD-34 + TD-36 together via `/plan-eng-review`** — one root: the anchor validator proves every
   emitted string RESOLVES, nothing proves a required field EXISTS. ADR-triggering.
3. **The summary-skip join is still unexercised on a real run.** `summaries/raw/` was cleared
   before run 3, so nothing was skippable. The 6 JSONs now carry fingerprints matching their
   transcripts exactly, so the next run over the same folder is the test — it should skip all 6
   and spend $0.
4. **The stub pipeline still cannot run in WSL** (no clip fixture, no Whisper model), so it did
   not gate 3.0.0; run 3 on Windows — 46 paid calls, 6 documents — stood in for it.
5. TD-30 (needs a home now the header idea is dead); TD-29b — **now louder**, a folder run
   pointed at `output/` writes triple-dated names; TD-29c; streaming; 1b.
