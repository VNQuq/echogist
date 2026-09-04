# Design: the mp3 re-encode rule (bulk v3, increment 1b)

Drafted 2026-09-04, unattended, from `dec-c3adfe5b` and Premise 4 of
[bulk-v3.md](./bulk-v3.md)
Branch: main
Repo: echogist (local, personal-use, Windows runtime / WSL2 dev)
Status: **DRAFT — NOT APPROVED, NOT IMPLEMENTED.** No code was written for this document.
Gate: needs `/office-hours` or at least `/plan-eng-review` before any implementation
(CLAUDE.md, "Workflow (gstack)").

## Why this is a separate document

Premise 4 of the bulk v3 design modifies the **single-file** flow. It has nothing to do
with walking a folder, so it carries its own risk budget rather than riding along on an
increment that is read-only and free. The forward reference lives in bulk-v3.md; the
reasoning lives here.

## Problem Statement

`extract_audio` re-encodes whatever it is given to mp3 VBR `-q:a 2` (~190 kbps). Two menu
paths reach it with a source that is ALREADY an mp3:

- `_MP3_ACTION_CHOICES` → `("mp3", "Re-encode to a smaller MP3")` (`menu.py:465`), which
  exists precisely to shrink an oversized file (operator request, reversing TD-12);
- the batch flow, which asks once before re-encoding any mp3s in the selection.

Nothing measures whether re-encoding would actually make the file smaller. Re-encoding a
128 kbps lecture at `-q:a 2` produces a file that is **larger and twice-lossy**. The menu
item's own label ("to a smaller MP3") is then false, and the operator has no way to know
before pressing it.

## The rule (dec-c3adfe5b, operator-decided)

Re-encode an mp3 when:

```
(size > 222 MiB  OR  audio_kbps >= 320)  AND  audio_kbps > TARGET_KBPS
```

The second clause is the anti-inflation guard and is the whole point of the decision.

### Why size alone is not enough

Size is bitrate x duration, so a size-only threshold cannot tell a high-bitrate file from
a merely long one. Every number below was computed, not recalled:

The bitrate at which a file of duration H first crosses 222 MiB:

| duration | crosses 222 MiB at |
|---|---|
| 2h 00m | 259 kbps |
| 2h 30m | 207 kbps |
| 3h 00m | **172 kbps** |

Past roughly 2h 50m the crossover drops BELOW the ~190 kbps encoder target, so from there
on the size rule alone starts selecting files that are already smaller than what the
encoder would produce. Concretely, for a 128 kbps lecture:

| duration | size | over 222 MiB? | re-encoded at ~190 kbps |
|---|---|---|---|
| 2h 00m | 110 MiB | no | — |
| 2h 30m | 137 MiB | no | — |
| 4h 00m | 220 MiB | no, just under | — |
| 5h 00m | **275 MiB** | **yes** | grows to **408 MiB**, second lossy pass |

The operator's own validation lecture is 2:58:57, right at the point where the crossover
passes under the target. EchoGist's own output past ~2.6h self-triggers the rule on a
second run.

**Unit discrepancy, found while checking the above.** `dec-c3adfe5b` and bulk-v3.md both
say **222 MiB**, but the "2.5h lands near 197 kbps" figure that justified the threshold is
only true for 222 **MB** (decimal). In MiB the same duration lands at 207 kbps. The 10
kbps gap does not change the conclusion, and it does not change the anti-inflation guard,
but the spec and its own rationale are currently using different units for the same
number. Worth settling before this is implemented, because whichever unit is chosen has to
be the one written in the config.

### Why `audio_kbps` and not the container bitrate

Already settled and already shipped: `extract.probe_media` reads the per-stream `Audio:`
line, not the `bitrate:` field on the `Duration:` line. On a video file the latter is
dominated by the picture track and reads 1500-4000 kbps, which would trip the >= 320
trigger on essentially every lecture video. Increment 1 probes and caches `audio_kbps`
and `has_video` for exactly this rule — that carry-forward was accepted there, and this is
what collects on it.

### `audio_kbps is None` means never re-encode

`probe_media` returns None when no audio stream carries a `kb/s` figure (common for VBR in
some containers, e.g. opus in mkv). The bulk-v3 design is explicit: **None must never be
treated as zero and never as a licence to guess.** A file whose bitrate is unknown is left
alone. The size-derived fallback (`size * 8 / duration`) applies only when the probe shows
no video stream, and it is the container average even then — good enough to compare
against a threshold, not good enough to act on when it is absent.

## Constraints inherited from CLAUDE.md

- **One shared predicate, two policies.** The rule is a pure function; the two callers
  differ only in what they do with the answer. A second copy of the arithmetic in `batch`
  would drift from the one in `menu` within a release.
- **Fail loud, return to menu.** Neither policy may silently skip: bulk records a reason in
  the report, the single-file flow says what it found before asking.
- **Config is data, not code.** `TARGET_KBPS` must not be a literal in two places.
- **English console.**

## The encoder target is currently a literal

`extract._conversion_argv:258` hardcodes `"-q:a", "2"`. `-q:a 2` is LAME VBR at roughly
190 kbps for speech, but the number 190 appears nowhere in the code — it is a property of
the quality setting. The rule needs to compare a measured bitrate against the encoder's
own target, so the two must read one number.

**Proposal:** move the pair into config data — an `[audio]` table in `models.toml` (or a
new `config/audio.toml` if `models.toml` is judged the wrong home) carrying
`lame_quality = 2` and `target_kbps = 190`, with `_conversion_argv` reading the first and
the predicate reading the second. They are two facets of one decision and must move
together; a comment saying so is not enough.

**Open question for review:** is `target_kbps` derivable from `lame_quality` instead of
stored beside it? A lookup table of the LAME VBR presets would remove the chance of the
two drifting, at the cost of a table nobody asked for. My read: store both, document the
coupling, revisit if a third consumer appears. Not my call to make alone.

## Proposed shape (NOT implemented)

```text
extract.py
    should_reencode(size_bytes, audio_kbps, *, target_kbps) -> ReencodeVerdict
        a pure predicate + the reason, no I/O, no config loading
```

`ReencodeVerdict` carries the boolean and a human-readable reason, because both callers
need to SAY why, not just branch. A bare bool would push the message into two places and
guarantee they diverge.

Callers:

- **Single-file flow** (`menu._flow_local_file`, the `action == "mp3"` branch): probe the
  source, and when the verdict is negative say what was measured and confirm before going
  ahead. The menu item stays usable — the operator can still override, which is what
  `dec-c3adfe5b` chose over a hard refusal inside `extract_audio`.
  Sketch: `"lecture.mp3 is 128 kbps, already below the ~190 kbps target. Re-encoding would
  make it LARGER and lose quality twice. Re-encode anyway?"` default **No**.
- **Bulk (increment 2, not built)**: the verdict IS the rule. Skip, and record the reason
  in the report. No question, because a folder run cannot stop forty times to ask.

## What this costs

One probe on the single-file mp3 path that does not happen today. `ffmpeg -i` on a local
file is 0.1-0.3s and the flow is already about to spend minutes in a conversion, so the
cost is invisible. On the bulk path the probe is free: the scanner already cached it.

## Tests this needs

- the predicate at the four corners: below both thresholds; over 222 MiB but already at or
  under target; at/over 320 kbps; over size AND over target;
- `audio_kbps is None` never re-encodes, whatever the size;
- the anti-inflation guard specifically: a 5h 128 kbps file over 222 MiB must NOT
  re-encode, which is the case a size-only rule gets wrong;
- the single-file flow asks before an inflating re-encode and honours both answers;
- `_conversion_argv` and the predicate read the SAME configured number (one test that
  changes the config value and asserts both move).

## Risks

- **The 222 MiB threshold may be redundant now.** With the anti-inflation guard in place,
  a file over 222 MiB that is also above ~190 kbps is caught by the bitrate clause anyway
  in every case the table above covers. The size clause then only fires for a file that is
  large, above target, and below 320 kbps — a real set, but a narrow one. The question is
  whether it earns its keep or is one threshold too many. I did not drop it: removing an
  operator-facing number unasked is a scope call that belongs to the operator.
- **Its units are ambiguous** (see the discrepancy above). Settle MiB vs MB before writing
  it into config; a 10 kbps shift in the crossover is small but it is not nothing.
- **`-q:a 2` is VBR, so ~190 kbps is an average, not a ceiling.** A file measured at 195
  kbps may or may not shrink. The rule will be slightly wrong in a narrow band around the
  target, in the harmless direction (it declines to re-encode).
- **Untestable in WSL for real.** Like the rest of this project, the verdict is exercised
  against captured ffmpeg stderr, never a real encode.

## Not in this document

Increment 2 (the paid bulk), which is gated on running the scanner against the real
lecture folder. This rule is written so that increment 2 can adopt it unchanged, but it
does not depend on increment 2 existing: the single-file half stands alone and is where
the operator actually hits the bug today.

## Status of the decision record

`dec-c3adfe5b` is logged and operator-approved as a DECISION. This document is one
agent's reading of it into a spec, drafted without the operator present. It has had no
adversarial review, and the two open questions above (the `target_kbps` derivation, the
fate of the 222 MiB threshold) are real, not rhetorical.

**Do not implement from this document without a review pass.**
