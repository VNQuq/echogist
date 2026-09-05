# Current Context

**Updated:** 2026-09-05 · v2.3.0 + 10 unreleased commits. Two calibration fixtures: run 1 (7 RU
lectures, Haiku, 59 calls, **$2.4003**) seeded the cost model; run 2 (`КУРС2025`, 6 RU
lectures, 18h18m, **balanced**, 39 paid calls, **$3.7246**) is the first Sonnet folder run and the
first with **0 CJK slips and 0 undrawable characters** — the prompt sentence and TD-28 both held
against real material. Reading its log opened TD-32 (closed same day) and TD-33. **Authority:**
[CLAUDE.md](../CLAUDE.md) for the v2 pipeline rule, TECHNICAL_DEBT TD-16 for its validation.
**Max: 60 lines** — only what the code and the CHANGELOG cannot tell you. Cut, don't append.

## Live invariants — break one and something regresses silently

- **The folder preview is AUTOMATIC and the three actions must agree with it.** `mp3` gets that
  walk's file LIST, not the root (`expand_selection` walks one level and silently converted only
  the top of a course tree). An interrupted walk is silent about the folder, never a verdict;
  an empty PLAN means "already done" only if there were files to plan.
- **Two channels, from `summarize` AND `render`** (TD-29, TD-28). `log` = chatter, muted;
  `notice` = act on it, loud: a DROPPED anchor, a phase that cited NOTHING over a timecoded
  transcript, a foreign script, an unfont-able character, an empty reconcile title. **Do not
  merge them**, and pass `notice=` to EVERY `validate_anchors` call — the per-phase one drops.
- **The script check is an instrument** ([design](./designs/script-check.md)): it MEASURES
  whether the prompt sentence works, so keep it. Notation (µ, ℓ, 𝑥, Greek) is not a script (TD-30).
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
- **Ctrl-C stops the run, not the app** — `folder.Cancelled` carries the partial report; SIGTERM
  then `kill()` after 5s. The WHOLE loop iteration is guarded (BaseException), not just the work.
  A resume partial is keyed by path + language + tier, so a tier switch re-runs, never mixes.
- **Essence block** is phase prose only; reconcile ALWAYS runs (short input = 2 calls); every
  emitted string is anchor-validated, title included — and an EMPTY title now says so.
- **Cost model** projects output as `ratio x that call's input`, clamped, floor under reconcile
  only. **Re-seed a tier from its own "Actual cost" line: output/input, +~5%.** economy 0.39
  (1.07x); balanced 0.24 is n=1, run 2's 5 clean files say 0.2805 — UNRESOLVED, that log was
  stitched across a tier switch, so re-measure on ONE clean run first. `[scan]` 135 wpm x 6.5,
  1.31x. Token estimate is 3 rates (ASCII < non-Latin < CJK).
- **Output:** raw `.json` → `summaries/raw/`, resume → `raw/.resume/`, `.pdf`/`.md` →
  `summaries/`, one shared stem. Key: env, then gitignored `config/secrets.toml` — absent in WSL,
  so paid runs are Windows-only; `/mnt/c/Users/operator/Documents/echogist/output/` reads that tree.

## Next — the first three are the operator's call, not the next session's

1. **TD-31 (HIGH) — stamp the source path inside the transcript.** Course B's lecture 1 is
   summarized, paid, from course A's transcript. Changes an artifact format; **decides TD-27**:
   once transcripts carry identity the two flat indexes can be recursive and any layout is safe.
2. **TD-27 (HIGH) — the `output/` release unit.** 24 path sites in `echogist/`; 18 mechanical,
   6 are flat-pool INDEXES and those are the work. TD-31 first. Named COURSE is the worst option.
3. **Cut v2.4.0** — nothing changed an artifact format or a config contract.
4. **TD-33 (MEDIUM)** — a repetitive block is dropped WHOLE; 21 blocks / ~21 min of run 2 went,
   heads measured 0.80-1.00 unique. Reporting half FIXED; sub-block trimming is an operator call.
5. TD-30 (needs TD-31's format); streaming; 1b.
