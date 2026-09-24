# TD-31 — a saved transcript carries the identity of its recording

**Status:** decided by the operator 2026-09-05 · **Severity:** HIGH
**Decides:** TD-27 (the `output/` release unit) · **Unblocks:** TD-30, TD-29b
**Supersedes:** the first draft of this file, which proposed a metadata header INSIDE the
`.txt`. Rejected — see §4.

---

## 1. The defect

`folder.plan_run` decides "does this file need transcribing" by **name**: the sanitized
stem of the source filename. The transcript pool is flat and global, so nothing verifies
the transcript came from THIS recording.

```
  output/transcripts/                          КУРС2026\
    2026-09-05-Модуль Основы, занятие 1.txt      Модуль Основы, занятие 1.mp4
    (course 2025)          └──── stem matches ────► transcript reused

  Result: a PAID summary of the 2026 lecture, written from the 2025 recording's words.
          Every [HH:MM:SS] anchor validates — the timecodes are real, just from the
          wrong recording — so the pipeline's only fidelity gate cannot see it.
```

Not hypothetical. On disk today:

```
КУРС2024\...\[VideoSite.org] Модуль «Основы», занятие 1 05.03.24.mp4
КУРС2025\...\Модуль Основы, занятие 1.mp4
```

The 2024 stems differ only because those files happen to carry a release-group prefix. The
2025 course is named cleanly, so the next course named the same way collides exactly.

## 2. The decision

**Identity is CONTENT, and it lives in the transcript FILENAME.**

```
output/transcripts/<date>-<sanitized stem>-<fingerprint>.txt
```

Two consequences, both deliberate:

1. **The transcript body is untouched, byte for byte.** No header, no parser, no new
   reader, no `chunk._blocks` change. See §4 for why this is the load-bearing half.
2. **The join is on the fingerprint, not the path and not the name.** See §3.

## 3. What the fingerprint is — and what it is not

```
fp = sha256( size || head(1 MiB) || tail(1 MiB) )[:16]
```

`scan.MediaFile` already carries `size` and the walk already calls `path.stat()`, so the
size half is free; the sample is two 1 MiB reads next to an `ffmpeg -i` probe that already
runs per file.

**It is a dedup HEURISTIC sufficient for this pool, not a proof of identity.** Two distinct
recordings with identical byte length whose first and last mebibyte both match would
collide. For a personal library of lecture recordings that does not happen; for a general
content-addressed store it would be an unacceptable claim. Say the weaker true thing.

**Why not the resolved path** (which is what `summary_index` joins on today): a path is a
location, not an identity. It dies on a tree move, on a folder rename, and on the same disk
read from WSL (`/mnt/c/...`) versus Windows (`C:\...`). Worse, it would hand TD-27 a
constraint — "you may not move transcripts, that costs hours of GPU" — at exactly the
moment TD-27 has to choose a layout. Content identity hands TD-27 freedom instead.

### MANDATORY CONTRACT — the fingerprint is taken once and inherited forward

**The fingerprint is computed from the ORIGINAL recording and travels with it. A derived
file NEVER recomputes its own.**

The planned increment 1b (MP3 re-encode) is the case this exists for: a re-encode changes
every byte, so a recomputed fingerprint would read as a new recording and **silently re-buy
a summary already paid for**. The re-encode step knows its parent; it carries the parent's
fingerprint forward.

Without this rule the scheme is worse than paths, because a path at least survives a
re-encode. This is not advice. It is the condition under which the design is correct.

Mechanically the rule is already enforceable: nothing in the pipeline recomputes a
fingerprint. It is computed once, at scan time, and passed as a value
(`folder.plan_run(..., fingerprints=...)`). A future re-encode adds its output to that
mapping under the parent's value.

## 4. Why the identity is NOT inside the file

The first draft put a `# source: ...` header at the top of the `.txt`. It was rejected, and
the reason is worth keeping because it nearly shipped.

`chunk._blocks` (`chunk.py:68`) takes **any** non-blank line. A line with no parseable
timecode inherits the previous block's start, and the very first such line is anchored at
`0.0`:

```python
start = _block_start_seconds(raw)
if start is None:
    start = last  # ← a header would land here, last == 0.0
```

So a header becomes block #1 at `[00:00:00]`, ships into the phase prompt as lecture text,
and can be quoted back with an anchor that **passes validation** — the timecode is real.
The fix for TD-31 would have recreated TD-31's own failure class one layer up.

It was closeable (strip the header in one reader). But the whole class disappears for free
by never putting data in the file the model reads. The filename is not prompt text.

## 5. One scheme, both joins

`summarize` stamps the source path today (TD-22). That becomes the same fingerprint, so
there is ONE identity in the system rather than two.

```
                       ┌──────────────────────────────┐
   scan.MediaFile ─────┤ fingerprint (computed ONCE)  │
                       └──────────────┬───────────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               ▼                      ▼                      ▼
   transcripts/<d>-<stem>-<fp>.txt   plan_run join    summaries/raw/*.json
        (names the artifact)        (reuse or not)     source_fingerprint
                                                       (already paid? skip)
```

## 6. Delete, don't keep

Per the operator's clean-slate rule for 3.0, there is no backward compatibility. Stem
matching goes entirely:

- `scan._TRANSCRIPT_NAME` / `_DATE_PREFIX` — the two-readings `-N` machinery added
  2026-09-04 (`4d6070f`). A fixed-width fingerprint anchored at the end of the name is
  unambiguous by construction, so the ambiguity it defended against cannot arise.
- `scan.transcript_files` / `scan.transcript_index` — replaced by `transcript_sources`,
  keyed by fingerprint.
- `folder.plan_run`'s `stem_counts` and its both-directions ambiguity rule.

An unstamped (pre-3.0) transcript is not matched and is re-transcribed. Free, local,
visible. No fallback, no orphan notice.

**`scan.stem_key` SURVIVES, narrowed to one caller:** `scan.collisions`, the duplicate-name
warning in the scan report. That is not a join — it tells the operator two source files will
fight over one *summary* filename, which is still true and still worth saying.

**Transcript reuse itself stays.** The operator restarted a run `economy`→`balanced` this
week and reuse saved hours. Killing reuse was considered and rejected.

## 7. Tests

Offline, no key, no GPU — the pipeline is pure over in-memory objects.

The three that carry the decision:

1. **The live collision.** Two courses, identical stems, different folders: course B's
   source never receives course A's transcript. This is the actual bug.
2. **Tree move.** Move the whole `output/` tree and re-run: the join survives and nothing
   re-transcribes. This is the property path identity could not give us.
3. **Re-encode inheritance.** A derived file carrying its parent's fingerprint is seen as
   already summarized and does not re-buy. This pins the §3 contract at the join, which is
   where breaking it costs money.

Plus the ordinary coverage:

- `test_naming.py` — fingerprint is stable across reads; differs on a changed byte; a file
  smaller than the 2 MiB sample window hashes whole; an unreadable file raises rather than
  returning a value that would join to everything.
- `test_scan.py` — `transcript_sources` parses the name back to a fingerprint, skips a file
  that has none, survives a stem that itself ends in hex, never raises on a missing dir.
  Fingerprint is cached under the existing size+mtime key and recomputed when bytes change.
- `test_transcribe.py` — the saved name is `<date>-<stem>-<fp>.txt`; the body is
  byte-identical to what `render_transcript` produced (the regression guard for §4).
- `test_summarize.py` — `summary_index` returns fingerprints; a `.json` without one is
  skipped rather than joining to everything.
- `test_folder.py` — the three above, plus: same recording under two different filenames is
  reused (the property the stem rule could never give us).

## 8. What TD-27 inherits

Freedom, not a constraint. With content identity, `transcript_sources` can walk any layout
and `summary_index` already joins on a value rather than a location. Dated run folders,
named course folders, or the current flat pool are all safe, and moving artifacts between
layouts costs nothing. TD-27 becomes a pure ergonomics decision.

## 9. NOT in scope

- **TD-27 itself** — the layout and the `paths` module. This unblocks it; it does not do it.
- **TD-30 (source language)** — the header slot that would have carried it no longer
  exists. TD-30 needs its own home; it is not decided here.
- **TD-33 (sub-block trimming)** — unrelated. Readable now from
  `/mnt/c/Users/operator/Documents/echogist/output/transcripts/`, not blocked.
- **Increment 1b (MP3 re-encode)** — not built. §3's contract is written down and pinned by
  test 3 at the join, so 1b lands against a stated rule instead of discovering it.
- **Backfilling identity into the 13 existing transcripts.** Nothing on disk knows their
  source. All 13 belong to sources that already have summaries, so the summary skip catches
  them before the transcript lookup is ever reached: dropping the stem rule costs zero
  re-transcription on the real disk. Measured, not assumed.

## 10. What already exists and is reused

- `naming.resolve_source` — stays as the scan's dedup key. It is no longer the *identity*.
- `naming.publish_text` — the atomic `.part`→`os.replace` publish. Untouched.
- `scan._cache_key` (`<resolved path>|<size>|<mtime_ns>`) — the fingerprint is cached under
  it. Correct by construction: changed bytes change size or mtime, which changes the key,
  which forces a recompute. Note it is a *cache* key, not an identity — it is path- and
  mtime-derived, which is precisely what §3 rejects for identity.
- `scan.MediaFile.size` and the walk's existing `path.stat()` — the size half of the
  fingerprint costs nothing new.
- `summarize.save_raw_result` / `summary_index` — the TD-22 back-link machinery. The field
  changes from a path to a fingerprint; the mechanism is unchanged.
