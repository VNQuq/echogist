# Current Context

**Updated:** 2026-09-25 · **v3.1.1 released** (tag `v3.1.1`), the first public version: the
empty-phase retry offer (`b22bba7`), MIT, CI on Windows + Linux, the 3.11 token-estimate fix.
**Published from a rewritten history** as `VNQuq/echogist`; the original is `echogist-private`.
3.1.0 is the answer to its own first real run — TD-34, TD-35, TD-30, TD-29b and TD-36's announce
half, MINOR because no artifact format moved; 3.0.0 carried TD-31's and TD-27's format breaks.
Calibration, four runs: run 1 (7 RU lectures, Haiku, **$2.4003**) seeded the cost model; run 2
(`КУРС2025`, 6, **balanced**, **$3.7246**) closed TD-31 + TD-33; **run 3** (same 6, 18h18m,
first 3.0 run, **$4.4634**) is the clean measurement — 0 CJK slips, 6/6 transcripts reused by
fingerprint, 1 loop block dropped where the old rule dropped 39 per 13 files; run 4 spent **$0**.
**Authority:** [CLAUDE.md](../CLAUDE.md); TECHNICAL_DEBT TD-16 for the v2 rule's validation.
**Max: ~88 lines** — only what the code and the CHANGELOG cannot. Cut, don't append.

## Live invariants — break one and something regresses silently

- **The folder preview is AUTOMATIC and the three actions must agree with it.** `mp3` gets that
  walk's file LIST, not the root. An interrupted walk is silent about the folder, never a verdict;
  an empty PLAN means "already done" only if there were files to plan.
- **The loop detector measures REPETITION SHAPE, not vocabulary** (TD-33). A stuck decoder repeats
  one phrase verbatim (3-gram coverage 0.60-1.00); this lecturer repeats to teach (never above
  0.20). The old unique-word ratio deleted 5172 words of real lecture. WHOLE blocks only, still.
- **Two channels, from `summarize` AND `render`** (TD-29, TD-28). `log` = chatter, muted;
  `notice` = act on it, loud: a DROPPED anchor, a phase citing NOTHING over a timecoded
  transcript, a foreign script, a wrong-language document, an unfont-able character, an empty
  title or essence field. **Do not merge them**; pass `notice=` to EVERY `validate_anchors`
  call — the per-phase one drops.
- **The script check has TWO halves** ([design](./designs/script-check.md)): `foreign_findings`
  asks "is this script used here at all", `script_shares` "what SHARE is not the primary one"
  (TD-30) — the first cannot see a wholesale switch, since `en` allows Cyrillic so quotations stay
  silent. 0.25 is MEASURED from BOTH sides: run 3's six RU documents sit at 0.0013-0.0088, the
  first real EN-over-RU document at 0.0042. Notation is not a script.
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
  head/tail 1 MiB)[:16]`; both joins key on it, so a move or rename costs nothing and two courses
  with identical filenames never share a transcript. **Taken ONCE from the original and inherited
  forward** — a re-encode (1b) carries its parent's value or it re-buys the summary. Never put
  metadata IN the .txt: `chunk._blocks` anchors it at `[00:00:00]`, citable past validation.
- **Ctrl-C stops the run, not the app** — `folder.Cancelled` carries the partial report, the WHOLE
  loop iteration is guarded (BaseException), and a resume partial is keyed by path + language +
  tier, so a tier switch re-runs and never mixes.
- **Essence block** is phase prose only; reconcile ALWAYS runs; every emitted string is
  anchor-validated. **Two different answers to a short reply, on purpose:** an empty PHASE is a
  paid call whose stretch of the recording would go missing, so it FAILS the file loud (TD-34,
  costing one file — `folder.run_phase` carries on and `on_phase` already persisted the rest);
  an empty title or essence field is one short reconcile call over intact prose, so it is a
  `notice` and the run finishes (TD-36). **The failure then ends in ONE question, AFTER the
  cycle** — at the failure an 18h run would sit on a y/N for hours. Scoped by TYPE
  (`EmptyPhaseError`, a `SummarizeError` subclass so `_RECOVERABLE` is unchanged), never by
  message text; **default YES**, alone among the paid gates; asked once, retries only the lost
  files, and its report prices the retry, not the run.
- **Cost model** projects output as `ratio x that call's input`. The input leg is near-exact over
  a whole folder (0.9987x) but runs ~1.10x on ONE file, so the output ratio carries most, not all,
  of the bias. **Re-seed a tier from its own "Actual cost" line: output/input, +~5%.** economy
  0.39; balanced **0.33 since 2026-09-06** (TD-35): run 3 gave 0.29513 over 46 calls, the 7th
  measurement 0.29825, and 0.33 quoted 1.17x over it. **The duration projection is ~1.5x the bill**
  (it multiplies the same ratio, so it rose with it). Token estimate: 3 rates by script.
- **Key:** env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are
  Windows-only. **`/mnt/c/Users/operator/Documents/echogist/output/` reads that tree from here, and
  reading the saved `.json` beats reading the log** — run 3's loudest WARN named the wrong cause.

## Next

1. **TD-36 is the only debt with work in it, deferred by the operator 2026-09-06, and the size
   hypothesis is already dead.** The 7th document (lecture 1 alone, EN) lost its title on a
   reconcile input of ~21.5k tokens, SMALLER than all six of run 3 (25.3k-33.3k). Title-drop rate
   3 of 7. The live lead: lecture 1 kept its title in RU and lost it in EN, same material — so the
   next experiment varies the LANGUAGE, not the length, and it is one ~$0.80 re-summarize.
2. `win-smoke.bat` runs off `tests/fixtures/audio/smoke.mp3` (gitignored): no paid run needed.
3. **CI runs the gate** (`ci.yml`: windows + ubuntu, 3.11, full lock), the only 3.11-on-Windows
   run (dev is 3.12/WSL); its first caught a float-sum drift and 16 posix-only tests. Streaming; 1b.
