"""The one definition of the ``output/`` layout (TD-27).

Every artifact directory was written inline as ``base / "output" / "<sub>"`` across
``menu`` (17 sites), ``provision`` and ``scripts/smoke_run.py``, and the tree's own name
was spelled a second time in ``scan`` as the walk's self-exclusion floor. Any layout
change was therefore a 24-site edit, and the layout and the self-exclusion rule could
drift apart silently — a scan that stopped pruning the artifact tree would re-process its
own extracted mp3s. This module owns both.

**It computes paths and never creates them.** Creation is a provisioning action and stays
:func:`echogist.provision.ensure_output_dirs`, which reads :func:`all_dirs` from here — so
"where a stage writes" and "what the launcher creates" cannot disagree.

Imports nothing from the package, deliberately: ``scan``, ``provision`` and ``menu`` all
import this, so anything imported back would be a cycle.

Not here on purpose: :func:`echogist.provision.app_root`. It answers "where is the
install", not "what is the output layout" — the ``base`` every function here takes. Moving
it would churn ``menu.Deps`` and its tests for nothing.

The layout itself is FLAT and stays flat (TD-27, operator 2026-09-05): grouping lives in
the artifact's FILENAME, not in a directory — see :func:`echogist.naming.transcript_path`
and :func:`echogist.naming.summary_artifact_stem`. Four readers of this tree
(:func:`echogist.scan.transcript_sources`, :func:`echogist.summarize.summary_index`,
``menu._pick_transcript``, ``menu._sweep_stale_resumes``) list a single directory with
``glob``, so a subdirectory added here would silently find zero files and re-buy summaries
already paid for. Adding one means fixing those four first.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

#: The artifact tree's directory name. Also the ``scan`` walk's self-exclusion floor
#: (``scan._keep_dir``), which is why it lives with the layout rather than beside the walk.
OUTPUT_DIR_NAME = "output"

#: The probe cache, directly under the tree root rather than in a subdirectory: it belongs
#: to the whole tree, not to one kind of artifact. Aliased as ``scan.CACHE_FILENAME``,
#: which owns its schema.
SCAN_CACHE_NAME = ".scan-cache.json"

#: Every directory provisioning creates, relative to the tree root. ``summaries/raw`` holds
#: the F13 recovery ``.json`` out of the operator's eye-line; the readable ``.pdf``/``.md``
#: stay directly under ``summaries``. ``.resume`` is absent deliberately — it is created on
#: demand by the run that writes into it and swept when spent.
SUBDIRS: tuple[str, ...] = (
    "audio",
    "transcripts",
    "summaries",
    "summaries/raw",
    "logs",
)


def output(base: Path) -> Path:
    """The artifact tree root."""
    return base / OUTPUT_DIR_NAME


def audio(base: Path) -> Path:
    """Extracted MP3s (T4)."""
    return output(base) / "audio"


def transcripts(base: Path) -> Path:
    """Saved transcripts — the checkpoint durable state is made of (T3)."""
    return output(base) / "transcripts"


def summaries(base: Path) -> Path:
    """The readable documents: ``.pdf``/``.md`` (T7)."""
    return output(base) / "summaries"


def summaries_raw(base: Path) -> Path:
    """The raw summary ``.json`` (F13) — re-render without re-paying."""
    return summaries(base) / "raw"


def resume(base: Path) -> Path:
    """Within-run phase partials, deleted once the durable ``.json`` exists."""
    return summaries_raw(base) / ".resume"


def logs(base: Path) -> Path:
    """One console transcript per launch (``ui.RunLog``)."""
    return output(base) / "logs"


def scan_cache(base: Path) -> Path:
    """The probe cache file."""
    return output(base) / SCAN_CACHE_NAME


def session_log(base: Path, when: datetime | None = None) -> Path:
    """This launch's console transcript: ``logs/<YYYY-MM-DD-HHMMSS>.log``.

    One file per launch, not one rolling file: a folder run is hours long and its report
    has to be re-read on its own without slicing a shared log apart. Nothing prunes these —
    they are small plain text next to artifacts measured in gigabytes, and silently
    deleting the record of a paid run is a worse failure than a folder with too many files
    in it.
    """
    stamp = when or datetime.now()
    return logs(base) / f"{stamp:%Y-%m-%d-%H%M%S}.log"


def all_dirs(base: Path) -> list[Path]:
    """Every directory :func:`echogist.provision.ensure_output_dirs` creates."""
    return [output(base) / sub for sub in SUBDIRS]
