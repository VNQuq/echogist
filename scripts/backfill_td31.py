#!/usr/bin/env python3
"""One-time migration: give pre-TD-31 artifacts their recording's content identity.

TD-31 changed how an artifact is joined to its recording. It used to be a path (in a
summary's ``source_path``) or a filename stem (for a transcript); it is now the
recording's content fingerprint, in the summary's ``source_fingerprint`` field and in the
transcript's filename. Nothing in the pipeline reads the old shapes any more, by design —
the operator's clean-slate rule for 3.0.

The consequence is data, not code: every artifact written before TD-31 is orphaned.
``summary_index`` over a real pre-TD-31 ``summaries/raw/`` returns ZERO, so the next
folder run re-transcribes every lecture AND re-buys every summary. On the operator's two
courses that is ~41 hours of GPU and ~$6 of paid calls, spent silently.

This script closes that gap once. It is a MIGRATION, not a compatibility layer: run it,
check the report, then delete the script. Nothing in ``echogist/`` imports it.

    python3 scripts/backfill_td31.py --output-dir /mnt/c/Users/.../echogist/output
    python3 scripts/backfill_td31.py --output-dir ... --apply

Without ``--apply`` it only reports. Both passes are idempotent: an artifact that already
carries its identity is skipped, so re-running changes nothing.

**It never guesses.** A summary whose source file is gone is reported and left alone. A
transcript is matched to a recording by its filename stem — the very join TD-31 deleted —
which is safe HERE and only here, because the candidate set is the handful of recordings
this pool's own summaries name, a stem claimed by two of them is refused outright, and the
result is printed for the operator to check before ``--apply`` writes anything. The
alternative to a refused transcript is re-transcribing it: free, local and visible.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from echogist import naming, scan  # noqa: E402


@dataclass(frozen=True)
class Plan:
    """What the migration would do, decided before anything is written."""

    #: summary .json -> the fingerprint to stamp into it
    summaries: dict[Path, str]
    #: transcript .txt -> the name it should carry
    transcripts: dict[Path, Path]
    #: artifact -> why it was left alone (reported, never guessed at)
    skipped: dict[Path, str]
    #: already migrated, so a re-run is a no-op
    done: int


def windows_path_to_local(raw: str) -> Path:
    """A stored ``C:\\...`` source path, readable from wherever this runs.

    Summaries were written on the Windows box, so their paths are Windows paths. The same
    disk is reachable from WSL as ``/mnt/c/...`` — the operator reads it that way already
    — and this script is just as likely to be run from there. On Windows the path is
    already correct and nothing is translated.
    """
    if sys.platform != "win32" and len(raw) > 2 and raw[1] == ":" and raw[2] == "\\":
        return Path(f"/mnt/{raw[0].lower()}/" + raw[3:].replace("\\", "/"))
    return Path(raw)


def _plan_summaries(raw_dir: Path) -> tuple[dict[Path, str], dict[Path, str], int, dict[str, str]]:
    """Fingerprint every summary's recording. Returns (to stamp, skipped, done, stem map).

    The stem map is the by-product the transcript pass needs: it is built ONLY from
    recordings this pool's summaries actually name, which is what keeps the transcript
    match bounded rather than a guess against the whole filesystem.
    """
    stamp: dict[Path, str] = {}
    skipped: dict[Path, str] = {}
    done = 0
    by_stem: dict[str, set[str]] = defaultdict(set)

    for entry in sorted(raw_dir.glob("*.json")):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            skipped[entry] = f"unreadable JSON ({exc})"
            continue
        if not isinstance(data, dict):
            skipped[entry] = "not a JSON object"
            continue
        if data.get("source_fingerprint"):
            done += 1
            continue
        source_path = data.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            skipped[entry] = "no source_path — nothing on disk says which recording this is"
            continue
        source = windows_path_to_local(source_path)
        try:
            fingerprint = naming.source_fingerprint(source)
        except OSError as exc:
            skipped[entry] = f"source is gone or unreadable: {source} ({exc.strerror or exc})"
            continue
        stamp[entry] = fingerprint
        by_stem[scan.stem_key(source)].add(fingerprint)

    # A stem two different recordings answer to is evidence for neither. This is TD-31's
    # own rule, applied to the one place a stem is still allowed to decide anything.
    stem_map = {stem: next(iter(fps)) for stem, fps in by_stem.items() if len(fps) == 1}
    return stamp, skipped, done, stem_map


def _plan_transcripts(
    transcripts_dir: Path, stem_map: dict[str, str]
) -> tuple[dict[Path, Path], dict[Path, str], int]:
    """Rename each transcript to carry its recording's fingerprint."""
    rename: dict[Path, Path] = {}
    skipped: dict[Path, str] = {}
    done = 0

    for path in sorted(transcripts_dir.glob("*.txt")):
        if scan.transcript_fingerprint(path) is not None:
            done += 1
            continue
        stem = path.stem
        if len(stem) < 11 or stem[4] != "-" or stem[7] != "-" or stem[10] != "-":
            skipped[path] = "not a dated transcript name"
            continue
        date_prefix, source_stem = stem[:10], stem[11:]
        fingerprint = stem_map.get(source_stem)
        if fingerprint is None:
            skipped[path] = (
                f"no summary in this pool names a recording with the stem {source_stem!r}"
                " — re-transcribing it is free, local and visible"
            )
            continue
        target = path.with_name(f"{date_prefix}-{source_stem}-{fingerprint}.txt")
        if target.exists():
            skipped[path] = f"{target.name} already exists"
            continue
        rename[path] = target

    return rename, skipped, done


def build_plan(output_dir: Path) -> Plan:
    """Decide the whole migration before writing a byte of it."""
    stamp, skipped, summaries_done, stem_map = _plan_summaries(output_dir / "summaries" / "raw")
    rename, more_skipped, transcripts_done = _plan_transcripts(output_dir / "transcripts", stem_map)
    return Plan(
        summaries=stamp,
        transcripts=rename,
        skipped={**skipped, **more_skipped},
        done=summaries_done + transcripts_done,
    )


def apply_plan(plan: Plan) -> None:
    """Write the plan. Summaries first: a half-done run must never lose the back-link.

    The summary carries the identity; the transcript name is a convenience that saves GPU
    time. Stamping summaries first means an interruption costs re-transcription (free)
    rather than re-payment (not free).
    """
    for entry, fingerprint in plan.summaries.items():
        data = json.loads(entry.read_text(encoding="utf-8"))
        # Replaced, not kept alongside: a fresh run writes only source_fingerprint, and a
        # migrated artifact that carries both would be a second shape to reason about.
        data.pop("source_path", None)
        data["source_fingerprint"] = fingerprint
        naming.publish_text(entry, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    for old, new in plan.transcripts.items():
        old.rename(new)


def report(plan: Plan, *, applied: bool) -> None:
    verb = "Stamped" if applied else "Would stamp"
    moved = "Renamed" if applied else "Would rename"
    print(f"{verb} {len(plan.summaries)} summary/summaries with a source fingerprint:")
    for entry, fingerprint in plan.summaries.items():
        print(f"  {fingerprint}  {entry.name}")
    print(f"\n{moved} {len(plan.transcripts)} transcript(s):")
    for old, new in plan.transcripts.items():
        print(f"  {old.name}\n    -> {new.name}")
    if plan.done:
        print(f"\nAlready migrated, untouched: {plan.done} artifact(s).")
    if plan.skipped:
        print(f"\nLeft alone ({len(plan.skipped)}) — nothing here is guessed at:")
        for path, why in plan.skipped.items():
            print(f"  {path.name}: {why}")
    if not applied and (plan.summaries or plan.transcripts):
        print("\nThis was a dry run. Re-run with --apply to write it.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "output",
        help="the EchoGist output/ directory holding summaries/ and transcripts/",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the migration (default: report only)"
    )
    args = parser.parse_args(argv)

    if not args.output_dir.is_dir():
        print(f"No such output directory: {args.output_dir}", file=sys.stderr)
        return 1

    plan = build_plan(args.output_dir)
    if args.apply:
        apply_plan(plan)
    report(plan, applied=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
