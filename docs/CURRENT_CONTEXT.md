# Current Context

**Updated:** 2026-09-05 · v2.3.0 + 5 unreleased commits, unpushed. The calibration fixture for
everything below is the first full folder run: 7 RU lectures, 23h43m, Haiku, 59 calls,
**$2.4003**, 0 failures, all anchors validated. Since it: cost model rebuilt, PDF styling,
`folder.py` merge, 3 CJK slips diagnosed, then a 4-agent review of the range whose fixes are
the 4 commits after `cd50e43`. **Authority:** [CLAUDE.md](../CLAUDE.md) for the v2 pipeline
rule, TECHNICAL_DEBT TD-16 for its validation.
**Max: 60 lines** — only what the code and the CHANGELOG cannot tell you. Cut, don't append.

## Live invariants — break one and something regresses silently

- **The folder preview is AUTOMATIC and the three actions must agree with it.** `mp3` gets that
  walk's file LIST, not the root (`expand_selection` walks one level and silently converted only
  the top of a course tree). An interrupted walk is silent about the folder, never a verdict;
  an empty PLAN means "already done" only if there were files to plan.
- **Two channels out of `summarize`** (TD-29). `log` = phase chatter, muted; `notice` = what
  the operator must act on, loud: a DROPPED anchor, a foreign script. **Do not merge them**,
  do not sniff text in `menu._progress`, and pass `notice=` to EVERY `validate_anchors` call —
  the per-phase one is where anchors are actually dropped.
- **The script check is an instrument** ([design](./designs/script-check.md)): it MEASURES
  whether the prompt sentence works, so keep it after the sentence lands. Notation (µ, ℓ, 𝑥,
  Greek) is not a script — TD-30.
- **One module, two engines** (`folder.py`): `convert_many` = parallel pool, `run_phase` =
  strictly sequential (one GPU job, one cost gate per folder). **Do not push the run through
  the pool** — the gate must price a whole folder. Its plan is SORTED, not `os.walk` order.
- **Name-claiming** is single-threaded and **in memory** (`folder._plan_output_paths`) — an
  on-disk claim survived `kill -9` as a fake artifact and poisoned dedup forever. **One publish
  scheme, `naming.publish_text` / `.part`→`os.replace`, now on EVERY durable write.**
- **Casefold is asymmetric ON PURPOSE.** `_resume_key` folds, `naming.resolve_source` does not
  (folding merged `Lecture.mp4`/`lecture.mp4` and dropped a recording). Menu keys are SEMANTIC,
  number keys POSITIONAL. Do not "unify" either pair.
- **A transcript reused on a name is evidence, not proof** — re-transcribing is free and
  visible, summarizing lecture A from B's transcript is paid and silent. `plan_run` refuses an
  ambiguous stem; `transcript_files` refuses a name claimable two ways (`-N`). Cross-COURSE
  identical names are still wrong and need TD-31. The SUMMARY skip joins on path (TD-22).
- **Ctrl-C stops the run, not the app** — carried on `folder.Cancelled` with the partial
  report, bounded by SIGTERM then `kill()` after 5s. The WHOLE loop iteration is guarded, not
  just the work: it is a BaseException. A resume partial is keyed by path + language + tier.
- **Essence block** is phase prose only; reconcile ALWAYS runs (short input = 2 calls), and
  every emitted string is anchor-validated, the title included.
- **Cost model** projects output as `ratio x that call's input`, clamped, floor under reconcile
  only; quotes 1.07x the bill, independent of the phase split. **Re-seed a tier from any run's
  "Actual cost" line: output/input, +~5%.** economy 0.39, balanced 0.24, flagship unmeasured;
  `[scan]` = 135 wpm x 6.5 chars, 1.31x. Token estimate is 3 rates (ASCII < non-Latin < CJK).
- **Output:** raw `.json` → `summaries/raw/`, resume → `raw/.resume/`, `.pdf`/`.md` →
  `summaries/`, one shared stem. Key: env, then gitignored `config/secrets.toml` — absent in
  WSL, so paid runs are Windows-only; that `output/` is readable at
  `/mnt/c/Users/operator/Documents/echogist/output/`.

## Next — the first three are the operator's call, not the next session's

1. **TD-31 (HIGH) — stamp the source path inside the transcript.** Course B's lecture 1 is
   summarized, paid, from course A's transcript. Changes an artifact format; **decides TD-27**:
   once transcripts carry identity the two flat indexes can be recursive and any layout is safe.
2. **TD-27 (HIGH) — the `output/` release unit.** 24 path sites in `echogist/`; 18 mechanical,
   6 are flat-pool INDEXES and those are the work. TD-31 first. Named COURSE is the worst option.
3. **Cut v2.4.0** — nothing changed an artifact format or a config contract. (TD-29c closed
   down to MINOR: the gate is manual, verified never skipped, and CLAUDE.md now says so.)
4. Prompt-sentence measurement next real run; TD-28 renderer half; TD-30; streaming; 1b.
