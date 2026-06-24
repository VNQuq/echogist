#!/usr/bin/env python3
"""Publish a GitHub Release for the current (or a given) tag.

This is the permanent, self-service release helper: it resolves the GitHub token
the SAME way :mod:`echogist.config` resolves the Anthropic key — ``GITHUB_TOKEN``
environment variable first, then the gitignored ``config/secrets.toml`` (key
``github_token``). So once the token is in ``config/secrets.toml`` (copy
``config/secrets.toml.example``), a release is one command with no token in the
shell history and no secret in git.

Usage::

    python3 scripts/release.py            # tag = v<VERSION file>
    python3 scripts/release.py v1.0.1     # explicit tag

The annotated git tag must already exist on the remote (``/ship`` or a manual
``git push origin <tag>`` creates it). The release notes are the matching
``## [X.Y.Z]`` section of ``CHANGELOG.md``. Re-running for a tag that already has
a release is a no-op (GitHub returns 422, reported, exit 0).

The token is never printed and never committed; ``config/secrets.toml`` is
gitignored (see ``.gitignore``).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SECRETS = _REPO_ROOT / "config" / "secrets.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_VERSION = _REPO_ROOT / "VERSION"
_ENV_TOKEN = "GITHUB_TOKEN"
_SECRETS_TOKEN = "github_token"


class ReleaseError(Exception):
    """A human-readable, recoverable release failure."""


def _token_from_env() -> str | None:
    import os

    value = os.environ.get(_ENV_TOKEN, "").strip()
    return value or None


def _token_from_secrets(path: Path = _SECRETS) -> str | None:
    """The ``github_token`` from ``config/secrets.toml``, or None. A missing or
    malformed file is a clean None (never a crash) — the env var is the primary path."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return None
    token = data.get(_SECRETS_TOKEN)
    return token.strip() if isinstance(token, str) and token.strip() else None


def resolve_token() -> str:
    """``GITHUB_TOKEN`` env first, then ``config/secrets.toml`` — same precedence as the
    Anthropic key (env wins so a stray file can never shadow an explicit token)."""
    token = _token_from_env() or _token_from_secrets()
    if token is None:
        raise ReleaseError(
            f"No GitHub token found. Set {_ENV_TOKEN}, or add github_token to "
            f"{_SECRETS.relative_to(_REPO_ROOT)} (copy config/secrets.toml.example). "
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
