#!/usr/bin/env python3
"""Publish a GitHub Release for the current (or a given) tag.

This is the permanent, self-service release helper. It reads the GitHub token
from a SINGLE, repo-specific environment variable: ``ECHOGIST_GITHUB_TOKEN``.
The name is deliberately namespaced (not the generic ``GITHUB_TOKEN``) so that,
on a machine with several projects, it is unambiguous which token belongs to
which repo and nothing accidentally grabs another project's token.

Set it once in your shell profile (``~/.bashrc``)::

    export ECHOGIST_GITHUB_TOKEN="github_pat_..."   # Contents: Read and write

Then a release is one command, with no token in shell history and no secret in
the repo (the token lives only in your profile, never in git):

    python3 scripts/release.py            # tag = v<VERSION file>
    python3 scripts/release.py v1.0.1     # explicit tag

The annotated git tag must already exist on the remote — create and push it
manually (``git tag -a <tag> -m "..." && git push origin <tag>``). The release
notes are the matching
``## [X.Y.Z]`` section of ``CHANGELOG.md``. Re-running for a tag that already has
a release is a no-op (GitHub returns 422, reported, exit 0).

The token is read from the environment only and is never printed or committed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_VERSION = _REPO_ROOT / "VERSION"
_ENV_TOKEN = "ECHOGIST_GITHUB_TOKEN"


class ReleaseError(Exception):
    """A human-readable, recoverable release failure."""


def resolve_token() -> str:
    """The token from the repo-specific ``ECHOGIST_GITHUB_TOKEN`` env var.

    Intentionally namespaced (not generic ``GITHUB_TOKEN``) so a multi-project
    machine never publishes with the wrong project's token."""
    token = os.environ.get(_ENV_TOKEN, "").strip()
    if not token:
        raise ReleaseError(
            f"No GitHub token found. Set {_ENV_TOKEN} in your shell profile "
            f'(~/.bashrc): export {_ENV_TOKEN}="github_pat_...". '
            "The token needs 'Contents: Read and write' on this repo."
        )
    return token


def resolve_repo() -> str:
    """``owner/name`` parsed from the ``origin`` remote (https or ssh GitHub URL)."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ReleaseError("Could not read the 'origin' git remote.") from exc

    slug = url
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    slug = slug.removesuffix(".git")
    if slug.count("/") != 1 or not all(slug.split("/")):
        raise ReleaseError(f"Could not parse owner/name from origin URL: {url}.")
    return slug


def resolve_tag(argv: list[str]) -> str:
    """The explicit ``argv[1]`` tag, or ``v<VERSION>``. A bare ``1.2.3`` gets the ``v``."""
    if len(argv) > 1 and argv[1].strip():
        tag = argv[1].strip()
    else:
        try:
            tag = "v" + _VERSION.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ReleaseError("No tag given and VERSION file is unreadable.") from exc
    return tag if tag.startswith("v") else f"v{tag}"


def changelog_notes(tag: str, changelog: Path = _CHANGELOG) -> str:
    """The body of the ``## [X.Y.Z]`` CHANGELOG section matching ``tag`` (the ``v`` and the
    trailing date are ignored), or a sensible fallback that links the full changelog."""
    version = tag.removeprefix("v")
    fallback = f"See [CHANGELOG.md](https://github.com/{resolve_repo()}/blob/main/CHANGELOG.md)."
    try:
        lines = changelog.read_text(encoding="utf-8").splitlines()
    except OSError:
        return fallback

    body: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            if capturing:  # reached the next version section — stop
                break
            capturing = f"[{version}]" in line
            continue
        if capturing:
            body.append(line)

    text = "\n".join(body).strip()
    return f"{text}\n\n{fallback}" if text else fallback


def publish(repo: str, tag: str, notes: str, token: str) -> str:
    """POST the release; return its ``html_url``. A 422 (release already exists for the
    tag) is treated as success — the URL is reconstructed from the tag."""
    payload = json.dumps(
        {
            "tag_name": tag,
            "name": f"EchoGist {tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "echogist-release",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
            url = data.get("html_url", "")
            return str(url)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 422:  # release already exists for this tag
            return f"https://github.com/{repo}/releases/tag/{tag} (already published)"
        raise ReleaseError(f"GitHub API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ReleaseError(f"Network error reaching GitHub: {exc.reason}.") from exc


def main(argv: list[str]) -> int:
    try:
        repo = resolve_repo()
        tag = resolve_tag(argv)
        token = resolve_token()
        notes = changelog_notes(tag)
        print(f"Publishing {repo} release for {tag} ...")
        print(publish(repo, tag, notes, token))
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
