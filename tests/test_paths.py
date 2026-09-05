"""The output layout has ONE definition (TD-27).

These pin the two things a second definition used to be able to break: that the
directories stages write into are exactly the ones provisioning creates, and that the
scan's self-exclusion floor is the tree's own name.

The literal ``"output"`` spellings elsewhere in the suite are deliberate and stay: the
layout contract is asserted from the outside, or ``paths`` would only ever be tested
against itself.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from echogist import paths, provision, scan


def test_every_accessor_is_a_directory_provisioning_creates(tmp_path: Path) -> None:
    """The failure this module exists to prevent: a stage writing where provisioning
    never created, or provisioning creating a directory nobody writes to."""
    created = set(paths.all_dirs(tmp_path))
    written = {
        paths.audio(tmp_path),
        paths.transcripts(tmp_path),
        paths.summaries(tmp_path),
        paths.summaries_raw(tmp_path),
        paths.logs(tmp_path),
    }

    assert written == created


def test_ensure_output_dirs_creates_exactly_all_dirs(tmp_path: Path) -> None:
    made = provision.ensure_output_dirs(tmp_path)

    assert made == paths.all_dirs(tmp_path)
    assert all(d.is_dir() for d in made)


def test_the_scan_prunes_on_the_layouts_own_name() -> None:
    """TD-27's coupling in one assertion: ``scan._keep_dir`` prunes by the literal name,
    and that name is the layout's. They can no longer drift apart — a rename that missed
    the walker would let a scan re-process its own extracted mp3s."""
    assert paths.output(Path("/anywhere")).name == paths.OUTPUT_DIR_NAME
    assert not scan._keep_dir(Path("/anywhere") / paths.OUTPUT_DIR_NAME, set())


def test_the_probe_cache_sits_at_the_tree_root(tmp_path: Path) -> None:
    """Not under a subdirectory: the cache belongs to the whole tree, and the root is what
    the walk prunes."""
    assert paths.scan_cache(tmp_path).parent == paths.output(tmp_path)
    assert paths.scan_cache(tmp_path).name == scan.CACHE_FILENAME


def test_the_resume_dir_is_hidden_under_raw(tmp_path: Path) -> None:
    """It holds within-run partials, not artifacts, so it stays out of the operator's
    eye-line — and it is NOT in ``SUBDIRS``: the run that writes there creates it."""
    assert paths.resume(tmp_path).parent == paths.summaries_raw(tmp_path)
    assert paths.resume(tmp_path) not in paths.all_dirs(tmp_path)


def test_session_log_is_one_file_per_launch(tmp_path: Path) -> None:
    first = paths.session_log(tmp_path)

    assert first.parent == paths.logs(tmp_path)
    assert first.suffix == ".log"
    # Sortable, so the newest session is the last one in the folder listing.
    assert re.match(r"^\d{4}-\d\d-\d\d-\d{6}$", first.stem)


def test_session_log_accepts_an_injected_timestamp(tmp_path: Path) -> None:
    path = paths.session_log(tmp_path, datetime(2026, 9, 5, 14, 3, 9))

    assert path.name == "2026-09-05-140309.log"
