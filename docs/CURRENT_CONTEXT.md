# Current Context

**Updated:** 2026-09-04 · v2.3.0. The first full folder run is the calibration fixture for
everything below: 7 RU lectures, 23h43m, Haiku, 59 calls, **$2.4003**, 0 failures, all anchors
validated. Since it: cost model rebuilt (TD-23/24), оформление shipped, `batch`+`bulk` merged
into `folder.py`, 3 CJK slips diagnosed (TD-29 closed, TD-28 halved). **Authority:**
[CLAUDE.md](../CLAUDE.md) for the v2 pipeline rule, TECHNICAL_DEBT TD-16 for its validation.
**Max: 60 lines** — only what the code and the CHANGELOG cannot tell you. Cut, don't append.

## Live invariants — break one and something regresses silently

- **Two channels out of `summarize`** (TD-29). `log` = ~60 phase lines, muted (`ui.detail`);
  `notice` = what the operator must act on, loud (`ui.warn`): a DROPPED anchor, a foreign
  script. **Do not merge them**; do not sniff message text in `menu._progress`.
- **The script check is an instrument, not a cosmetic fix** (`alphabet.py`,
  `summarize.report_foreign_scripts`, [design](./designs/script-check.md)). Haiku spliced
  Chinese morphemes into Russian words 3x/7 and we only noticed because the PDF font choked; a
  same-language swap would be invisible. Keep it even after the prompt sentence works — it is
  what MEASURES the sentence. Allowed scripts: a code dict by `_LANGUAGE_NAMES`, not config.
- **One module, two engines** (`folder.py`): `convert_many` = parallel ffmpeg pool with its own
  `Cancellation`, `run_phase` = strictly sequential (one GPU job, one cost gate per folder),
  sharing `Item`/`Report`/`Cancelled`. **Do not push the run through the pool** — the gate must
  price a whole folder. Its plan is SORTED, not `os.walk` order.
- **Name-claiming.** `dated_artifact_path` selects without creating, so stems race;
  `folder._plan_output_paths` claims every mp3 name up front, single-threaded, **in memory** (an
  on-disk claim survived `kill -9` as a fake artifact and poisoned dedup forever, and `.part`
  cannot be the marker because dedup deliberately cannot see it). **One publish scheme
  (`.part`→`os.replace`).** Transcripts and summary json do NOT yet claim.
- **Casefold is asymmetric ON PURPOSE.** `menu._resume_key` folds (one file picked twice on
  Windows); `naming.resolve_source` does NOT (folding made `Lecture.mp4`/`lecture.mp4` one entry
  and dropped a real recording). Do not "unify" them.
- **`plan_run` refuses to reuse a transcript whose stem is ambiguous** instead of carrying
  collision machinery: re-transcribing is free and visible, summarizing lecture A from B's
  transcript is paid and silent. The SUMMARY skip has no such limit (TD-22 joins on path).
- **Ctrl-C stops the run, not the app** — the one exception, carried on `folder.Cancelled` with
  the partial report; bounded by SIGTERM, then `kill()` after 5s.
- **Menu keys are SEMANTIC, number keys POSITIONAL** — inserting a row renumbers the rest.
- **Essence block** comes from phase prose only, and reconcile runs ALWAYS, so short input is
  **2 calls, not 1**. A video asks before keeping the MP3, an mp3 source never does: keep that.
- **Cost model** projects output per call as `tier.output_per_input_ratio x that call's input`,
  clamped, with a floor under reconcile only; the gate quotes 1.07x the real bill (was 0.93x)
  and no longer moves with the phase split. **Re-seed a tier from any finished run's "Actual
  cost" line: output/input, plus ~5%** — no code, no transcript. economy 0.39, balanced 0.24,
  flagship unmeasured; `[scan]` = 135 wpm x 6.5 chars, lands 1.31x.
- **Model currency:** tiers pin floating aliases (always-latest, reproducibility dropped);
  `scripts/check-models.py` reports retired/valid + context drift. **Prices stay manual.**
- **Output:** raw `.json` → `summaries/raw/`, resume → `raw/.resume/`, `.pdf`/`.md` →
  `summaries/`, one shared stem; `output/` is machine-local, so a Windows summary is only
  re-validatable in WSL against ITS transcript. Key: `ANTHROPIC_API_KEY`, then gitignored
  `config/secrets.toml` — absent in WSL, so paid runs are Windows-only.

## Next

1. **TD-27 (HIGH) — decide the `output/` release unit** (dated RUN / named COURSE / flat pool)
   and land a real `paths` module. Trigger: the second real course. Now close.
2. **TD-28 (LOW, renderer half)** — a glyph the font cannot draw still reaches the PDF with a
   warning that scrolls past. Trigger: the next change to `render`'s font handling.
3. **Measure the prompt sentence** on the next real folder run: did the CJK slip rate drop.
4. Hand-picked subsets as a menu row (awaiting demand); streaming the reply; increment 1b
   ([draft](./designs/mp3-reencode-1b.md), NOT approved); first live `check-models.py` run.
