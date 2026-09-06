# Current Context

**Updated:** 2026-09-06 · **v3.0.0 released** (TD-31 + TD-27 artifact-format breaks). It shipped
with TD-34 + TD-35 (HIGH) and TD-36 (MEDIUM) open on the operator's call; **the next day, on
`main` and unreleased, five debts closed** — TD-34, TD-35, TD-30, TD-29b and TD-36's announce
half (`5539ee9`, `c058d76`, this one). Calibration: run 1 (7 RU lectures, Haiku, **$2.4003**)
seeded the cost model; run 2 (`КУРС2025`, 6 lectures, **balanced**, **$3.7246**) closed
TD-31 + TD-33 off its 13 transcripts; **run 3** (same 6, 18h18m, first 3.0 run, **$4.4634**) is
the clean unstitched measurement — 0 CJK slips, 0 undrawable chars, 6/6 transcripts reused by
fingerprint, 1 loop block dropped where the old rule dropped 39 per 13 files.
**Authority:** [CLAUDE.md](../CLAUDE.md); TECHNICAL_DEBT TD-16 for the v2 rule's validation.
**Max: ~88 lines** — only what the code and the CHANGELOG cannot. Cut, don't append.

## Live invariants — break one and something regresses silently

- **The folder preview is AUTOMATIC and the three actions must agree with it.** `mp3` gets that
  walk's file LIST, not the root. An interrupted walk is silent about the folder, never a verdict;
  an empty PLAN means "already done" only if there were files to plan.
- **The loop detector measures REPETITION SHAPE, not vocabulary** (TD-33). A stuck decoder
  repeats one phrase verbatim (3-gram coverage 0.60-1.00); this lecturer repeats to teach (never
  above 0.20). The old unique-word ratio deleted 39 blocks / 5172 words of real lecture per 13
  files. WHOLE blocks only, still.
- **Two channels, from `summarize` AND `render`** (TD-29, TD-28). `log` = chatter, muted;
  `notice` = act on it, loud: a DROPPED anchor, a phase citing NOTHING over a timecoded
  transcript, a foreign script, a wrong-language document, an unfont-able character, an empty
  title or essence field. **Do not merge them**; pass `notice=` to EVERY `validate_anchors`
  call — the per-phase one drops.
- **The script check has TWO halves** ([design](./designs/script-check.md)): `foreign_findings`
  asks "is this script used here at all", `script_shares` "what SHARE is not the primary one"
  (TD-30) — the first cannot see a wholesale switch, since `en` allows Cyrillic so quotations stay
  silent. 0.25 is MEASURED: run 3's six documents sit at 0.0013-0.0088. Notation is not a script.
- **One module, two engines** (`folder.py`): `convert_many` = parallel pool, `run_phase` =
  strictly sequential. **Do not push the run through the pool** — the gate must price a whole
  folder. Its plan is SORTED, not `os.walk` order. **The walk prunes `output` from `dirnames` and
  never sees the ROOT**, so `_flow_folder` refuses a root that IS the artifact tree (TD-29b) — by
  resolved path only: a folder the operator named `output` is theirs.
- **Name-claiming** is in memory (`folder._plan_output_paths`) — an on-disk claim survived
  `kill -9` as a fake artifact and poisoned dedup forever. One publish scheme,
  `naming.publish_text`, on EVERY durable write. **Casefold is asymmetric ON PURPOSE:**
  `_resume_key` folds, `naming.resolve_source` does not (folding dropped a recording).
- **Grouping lives in the artifact's NAME, and the tree stays FLAT** (TD-27). A summary is
  `<date>-<source stem>-<title>`; the date is the RUN's, taken ONCE per folder run (an 18h run
  crosses midnight and would split a course in two). The source part truncates from the HEAD —
  a download's noise is the site tag in front, the discriminator the lecture number at the back. The
  cap `naming._MAX_SUMMARY_STEM` cannot rise alone: `render` re-runs `summary_stem` over the base,
  so a longer stem splits the triplet silently. **Do not add a subdirectory** —
  `transcript_sources`, `summary_index`, `_pick_transcript` and `_sweep_stale_resumes` are all
  single-level `glob` and would find nothing. `paths` is the ONE layout definition; it computes
  and never creates.
- **Identity is CONTENT and lives in the transcript's NAME** (TD-31). `fp = sha256(size +
  head/tail 1 MiB)[:16]`; both joins key on it, so a move or rename costs nothing and two
  courses with identical filenames never share a transcript. **Taken ONCE from the original and
  inherited forward** — a re-encode (1b) carries its parent's value or it re-buys the summary.
  Never put metadata IN the .txt: `chunk._blocks` anchors it at `[00:00:00]` and the model
  could cite it past validation.
- **Ctrl-C stops the run, not the app** — `folder.Cancelled` carries the partial report, the WHOLE
  loop iteration is guarded (BaseException), and a resume partial is keyed by path + language +
  tier, so a tier switch re-runs and never mixes.
- **Essence block** is phase prose only; reconcile ALWAYS runs; every emitted string is
  anchor-validated. **Two different answers to a short reply, on purpose:** an empty PHASE is a
  paid call whose stretch of the recording would go missing, so it FAILS the file loud (TD-34,
  costing one file — `folder.run_phase` carries on and `on_phase` already persisted the rest);
  an empty title or essence field is one short reconcile call over intact prose, so it is a
  `notice` and the run finishes (TD-36).
- **Cost model** projects output as `ratio x that call's input`; the input leg needs no margin
  (0.9987x, read off the real transcripts), so that ONE number carries the whole bias. **Re-seed a
  tier from its own "Actual cost" line: output/input, +~5%.** economy 0.39; balanced **0.33 since
  2026-09-06** (TD-35), above the rule so a SINGLE file at the worst ratio ever seen (0.3171) is
  still quoted over — run 3 gave 0.29513 over 46 calls. Token estimate: 3 rates by script.
- **Key:** env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are
  Windows-only. **`/mnt/c/Users/operator/Documents/echogist/output/` reads that tree from here, and
  reading the saved `.json` beats reading the log** — run 3's loudest WARN named the wrong cause.
  The real pool is migrated: 13/13 summaries and transcripts carry their fingerprint (2026-09-05).

## Next

1. **Run 4 is the test of everything above, and of three things at once.** Point a folder run at
   `КУРС2025` again: (a) the **summary-skip join** is still unexercised — the 6 JSONs in
   `summaries/raw/` carry fingerprints matching their transcripts exactly, so it should skip all
   6 and spend **$0**; (b) any real work it does quotes at 0.33 and the "Actual cost" line says
   whether that margin is right; (c) an empty phase or essence field now announces itself, which
   is how TD-36's rate gets measured without opening the `.json`.
2. **TD-36's second half is the only debt left with work in it** — WHY reconcile drops fields on
   half the documents at this size. Collect the rate from run 4's notices; do not guess it from six
   samples. TD-29c stays open on its own trigger (a second committer), declined 2026-09-06.
3. **The stub pipeline still cannot gate a release from WSL** (no clip fixture, no Whisper model).
   `scripts/win-smoke.bat` is the Windows entry point and now holds its window open when
   double-clicked; it needs a short clip at `tests/fixtures/audio/smoke.*` (not committed) or
   `--clip PATH`. Until one is placed, a real run stands in for the killswitch gate.
4. Streaming; 1b. **TD-30 and TD-29b closed 2026-09-06** — every remaining debt waits on
   evidence, none on a decision.
