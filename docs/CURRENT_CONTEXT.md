# Current Context

**Updated:** 2026-09-05 · v2.3.0 + 12 unreleased commits. Calibration: run 1 (7 RU lectures,
Haiku, **$2.4003**) seeded the cost model; run 2 (`КУРС2025`, 6 lectures, 18h18m,
**balanced**, **$3.7246**) is the first Sonnet folder run, **0 CJK slips, 0 undrawable chars**.
Its 13 saved transcripts are now the measurement corpus (2451 blocks) and closed TD-31 + TD-33.
**Authority:** [CLAUDE.md](../CLAUDE.md); TECHNICAL_DEBT TD-16 for the v2 rule's validation.
**Max: 60 lines** — only what the code and the CHANGELOG cannot tell you. Cut, don't append.

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
  "Actual cost" line: output/input, +~5%.** economy 0.39; balanced 0.24 is n=1, run 2's 5 clean
  files say 0.2805 — UNRESOLVED (stitched log). Token estimate: 3 rates by script.
- **Key:** env, then gitignored `config/secrets.toml` — absent in WSL, so paid runs are
  Windows-only; `/mnt/c/Users/operator/Documents/echogist/output/` reads that tree from here.

## Next

1. **Backfill the 13 existing summaries.** They carry `source_path`; the index reads
   `source_fingerprint`, so `summary_index` over the real `raw/` returns **0** — a re-run would
   re-transcribe 41h AND re-buy all 13 (~$6.12). All 13 sources are on disk: one offline script
   (`source_path` → fingerprint → write field, rename transcripts) fixes it. Data, not code.
2. **Stub pipeline on Windows before the next push** (CLAUDE.md). WSL has no clip fixture and
   no Whisper model, so it cannot run here.
3. **TD-27** is no longer a design question (TD-31 removed both index constraints): what is
   left is a `paths` module over 26 hardcoded expressions.
4. **v3.0, not v2.4.0** — 3.0 begins when the registry empties, and TD-31 already broke both
   artifact formats, so nothing is releasable in between.
5. TD-30 (needs a home now the header idea is dead); TD-29b; TD-29c; streaming; 1b.
