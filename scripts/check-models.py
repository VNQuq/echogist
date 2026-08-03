#!/usr/bin/env python3
"""Model-currency check for ``config/models.toml`` — a report-only maintenance tool.

Goal: maximum freshness, minimum manual steps. This script keeps the model config
current against Anthropic's live model list so the operator does not hand-track
deprecations or context-window changes.

**Killswitch.** This is a STANDALONE maintenance script, like ``scripts/release.py``.
It is NOT part of the pipeline and NOT run by the killswitch CI job. Nothing under
``echogist/`` imports it, and its ONE network call (the Anthropic model list) happens
inside :func:`fetch_models`, called only from :func:`main` — never at import time. So
the killswitch contract ("SUMMARIZE is the only network stage; everything left of it is
offline") is untouched. Run it by hand, or from cron:

    python3 scripts/check-models.py              # report + apply the safe updates
    python3 scripts/check-models.py --dry-run    # report only, write nothing

It reads the key from ``ANTHROPIC_API_KEY`` (then ``config/secrets.toml``), exactly
like the app.

**What it reports, per tier**
  * still valid, or missing from the live list (= retired / deprecated);
  * context drift — ``context_window`` in the config vs ``max_input_tokens`` from the API;
  * a generation bump — the model's ``display_name`` changed since last seen.

**What it writes (never prices)**
  * ``context_window`` — DERIVED from ``max_input_tokens``; this field is no longer
    hand-maintained.
  * ``prices_unverified = true`` on a tier whose model had a generation bump — the
    summarize flow then prints a one-time notice until a human verifies the prices and
    clears the flag by hand.
  * ``config/model_names.json`` — a local cache of each alias's last-seen display_name,
    the memory used to detect the next generation bump.

It MUST NEVER touch ``price_in_per_mtok`` / ``price_out_per_mtok``: there is no pricing
API endpoint, so prices stay a manual verify-against-the-pricing-page step by design.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODELS_TOML = _REPO_ROOT / "config" / "models.toml"
_NAME_CACHE = _REPO_ROOT / "config" / "model_names.json"

_API_URL = "https://api.anthropic.com/v1/models"
_API_VERSION = "2023-06-01"
_ENV_API_KEY = "ANTHROPIC_API_KEY"
_HTTP_TIMEOUT_S = 30

_SECTION_RE = re.compile(r"^\s*\[")
_TIER_HEADER_RE = re.compile(r"^\s*\[tiers\.([A-Za-z0-9_.-]+)\]\s*$")


class CheckError(Exception):
    """A human-readable, recoverable failure. Printed to stderr; non-zero exit."""


# --------------------------------------------------------------------------- #
# Data shapes (all pure — no IO)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ApiModel:
    """One entry from ``GET /v1/models``. Token limits are optional: older API
    responses omit them, so absence means "unknown", never zero."""

    id: str
    display_name: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class TierFinding:
    """The per-tier result of diffing the config against the live model list."""

    name: str
    model_id: str
    status: str  # "valid" | "retired"
    matched_id: str | None
    display_name: str | None
    config_context: int
    api_input_tokens: int | None
    api_output_tokens: int | None
    context_drift: bool
    generation_bump: bool
    cached_display_name: str | None


@dataclass
class TierWrite:
    """A planned edit to one ``[tiers.<name>]`` block. Prices are never here."""

    context_window: int | None = None
    set_prices_unverified: bool = False

    def is_empty(self) -> bool:
        return self.context_window is None and not self.set_prices_unverified


# --------------------------------------------------------------------------- #
# Pure parsing / matching / diff
# --------------------------------------------------------------------------- #
def _coerce_int(value: Any) -> int | None:
    """A POSITIVE int from the API JSON, or None for absent/malformed/zero.

    Zero is treated as "unknown", exactly like an absent field, and never as a real
    limit. That is load-bearing: a 0 here would look like context drift and get
    written to ``context_window``, which ``echogist/config.py`` then rejects
    (``must be > 0``) on every subsequent app start. This tool must never be able
    to write a config its own app refuses to load.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def parse_models_response(raw: list[dict[str, Any]]) -> dict[str, ApiModel]:
    """Turn the ``data`` array of ``GET /v1/models`` into ``{id: ApiModel}``.

    Tolerant on purpose: an entry without ``max_input_tokens`` / ``max_tokens``
    still parses (limits become None), and a malformed entry (no ``id``) is
    skipped rather than crashing the whole run.
    """
    models: dict[str, ApiModel] = {}
    for item in raw:
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        display = item.get("display_name")
        models[model_id] = ApiModel(
            id=model_id,
            display_name=display if isinstance(display, str) and display else model_id,
            max_input_tokens=_coerce_int(item.get("max_input_tokens")),
            # The API names the output cap "max_tokens"; accept the alt spelling too.
            max_output_tokens=_coerce_int(item.get("max_tokens"))
            if item.get("max_tokens") is not None
            else _coerce_int(item.get("max_output_tokens")),
        )
    return models


def match_model(model_id: str, api_models: dict[str, ApiModel]) -> ApiModel | None:
    """Resolve a configured id to a live model, or None if it is retired.

    Two cases, because ``models.toml`` pins floating aliases (``claude-sonnet-5``):
    the alias may be listed directly, OR only its dated snapshots are
    (``claude-sonnet-5-20250930``). An exact id wins; otherwise the NEWEST snapshot
    matches, where a snapshot is strictly ``<alias>-YYYYMMDD``.

    The date suffix is REQUIRED, not decoration. A bare ``startswith(alias + "-")``
    also swallows the next generation, because Anthropic's ids nest: a tier pinned to
    ``claude-sonnet-4`` would match ``claude-sonnet-4-5-20250929``, which even sorts
    last ("5-2025…" > "20250514"). A retired tier would then report [ok] against a
    DIFFERENT model and derive its context_window — the exact drift this tool exists
    to catch, inverted into a false all-clear.
    """
    exact = api_models.get(model_id)
    if exact is not None:
        return exact
    snapshot_re = re.compile(rf"{re.escape(model_id)}-\d{{8}}$")
    snapshots = sorted(
        (m for mid, m in api_models.items() if snapshot_re.match(mid)), key=lambda m: m.id
    )
    return snapshots[-1] if snapshots else None


def build_report(
    tiers: dict[str, dict[str, Any]],
    api_models: dict[str, ApiModel],
    cached_names: dict[str, str],
) -> list[TierFinding]:
    """Diff each configured tier against the live list + the display-name cache.

    Pure: given the parsed config tables, the parsed API models, and the cache, it
    computes every finding with no IO. A generation bump requires a PRIOR cached
    name that differs — a first sighting (no cache entry) is recorded, not flagged.
    """
    findings: list[TierFinding] = []
    for name, table in tiers.items():
        model_id = str(table.get("model_id", ""))
        config_context = table.get("context_window")
        config_context = int(config_context) if isinstance(config_context, int) else 0
        matched = match_model(model_id, api_models)
        if matched is None:
            findings.append(
                TierFinding(
                    name=name,
                    model_id=model_id,
                    status="retired",
                    matched_id=None,
                    display_name=None,
                    config_context=config_context,
                    api_input_tokens=None,
                    api_output_tokens=None,
                    context_drift=False,
                    generation_bump=False,
                    cached_display_name=cached_names.get(model_id),
                )
            )
            continue
        cached = cached_names.get(model_id)
        drift = matched.max_input_tokens is not None and matched.max_input_tokens != config_context
        bump = cached is not None and cached != matched.display_name
        findings.append(
            TierFinding(
                name=name,
                model_id=model_id,
                status="valid",
                matched_id=matched.id,
                display_name=matched.display_name,
                config_context=config_context,
                api_input_tokens=matched.max_input_tokens,
                api_output_tokens=matched.max_output_tokens,
                context_drift=drift,
                generation_bump=bump,
                cached_display_name=cached,
            )
        )
    return findings


def plan_writes(findings: list[TierFinding]) -> dict[str, TierWrite]:
    """The toml edits implied by the findings: derive ``context_window`` from the
    API on drift, and set ``prices_unverified`` on a generation bump. Never prices."""
    writes: dict[str, TierWrite] = {}
    for f in findings:
        if f.status != "valid":
            continue
        w = TierWrite()
        if f.context_drift and f.api_input_tokens is not None:
            w.context_window = f.api_input_tokens
        if f.generation_bump:
            w.set_prices_unverified = True
        if not w.is_empty():
            writes[f.name] = w
    return writes


def plan_cache(findings: list[TierFinding], cached_names: dict[str, str]) -> dict[str, str]:
    """The new display-name cache: carry existing entries forward, refresh every
    valid tier's alias to its current display_name. A retired tier keeps its old
    entry (it may come back) but is never refreshed."""
    updated = dict(cached_names)
    for f in findings:
        if f.status == "valid" and f.display_name is not None:
            updated[f.model_id] = f.display_name
    return updated


# --------------------------------------------------------------------------- #
# Pure toml rewrite (string in, string out — no IO, unit-tested)
# --------------------------------------------------------------------------- #
def _set_kv(line: str, key: str, value: str) -> str:
    """Replace the value of ``key = ...`` on ``line``, preserving indentation, any
    inline ``# comment``, and the trailing newline. Returns ``line`` unchanged if it
    is not that assignment."""
    nl = "\n" if line.endswith("\n") else ""
    body = line[:-1] if nl else line
    m = re.match(rf"^(\s*{re.escape(key)}\s*=\s*)([^#]*?)(\s*#.*)?$", body)
    if not m:
        return line
    return f"{m.group(1)}{value}{m.group(3) or ''}{nl}"


def _split_segments(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split file lines into (tier_name | None, block-lines) segments at each
    section header. The preamble before the first header is a ``None`` segment;
    a non-tier section (``[guard]`` …) is also ``None``.

    Multi-line strings are skipped over, never scanned for headers. models.toml holds
    the prompts as data (``synthesis_system_prompt = \"\"\"…\"\"\"``) and that prose talks
    about ``[HH:MM:SS]`` anchors and the ``[интерпретация]:`` marker — a prompt line
    starting with ``[`` must not be mistaken for a section header.
    """
    segments: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current: list[str] = []
    started = False
    open_delim: str | None = None
    for line in lines:
        if open_delim is not None:
            current.append(line)
            if open_delim in line:
                open_delim = None
            continue
        for delim in ('"""', "'''"):
            if line.count(delim) % 2 == 1:
                open_delim = delim
                break
        if open_delim is None and _SECTION_RE.match(line):
            if started or current:
                segments.append((current_name, current))
            m = _TIER_HEADER_RE.match(line)
            current_name = m.group(1) if m else None
            current = [line]
            started = True
        else:
            current.append(line)
    if started or current:
        segments.append((current_name, current))
    return segments


def _apply_block(block: list[str], upd: TierWrite) -> list[str]:
    """Apply one tier's planned edits to its block of lines. Flips an existing
    ``prices_unverified`` line, or inserts one right after ``model_id`` if absent."""
    has_flag = any(re.match(r"^\s*prices_unverified\s*=", ln) for ln in block)
    out: list[str] = []
    insert_pos: int | None = None
    last_content = 0
    for line in block:
        if upd.context_window is not None and re.match(r"^\s*context_window\s*=", line):
            line = _set_kv(line, "context_window", str(upd.context_window))
        elif upd.set_prices_unverified and re.match(r"^\s*prices_unverified\s*=", line):
            line = _set_kv(line, "prices_unverified", "true")
        out.append(line)
        if line.strip():
            last_content = len(out)
        if re.match(r"^\s*model_id\s*=", line):
            insert_pos = len(out)
    if upd.set_prices_unverified and not has_flag:
        # Fall back to "after the last non-blank line" when the block has no model_id.
        # Index 0 would put the key ABOVE the [tiers.<name>] header — i.e. into the
        # PREVIOUS table. Unreachable today (a tier with no model_id resolves to
        # "retired" and plan_writes skips it), but the default must not corrupt the file.
        at = insert_pos if insert_pos is not None else last_content
        out.insert(at, "prices_unverified = true\n")
    return out


def rewrite_toml(text: str, updates: dict[str, TierWrite]) -> str:
    """Return ``text`` with the planned per-tier edits applied, comments and layout
    preserved. Pure string transform (no ``tomllib`` round-trip, which would drop the
    comments). A tier named in ``updates`` but absent from the text is skipped."""
    if not updates:
        return text
    segments = _split_segments(text.splitlines(keepends=True))
    rebuilt: list[str] = []
    for name, block in segments:
        upd = updates.get(name) if name is not None else None
        rebuilt.extend(_apply_block(block, upd) if upd is not None else block)
    return "".join(rebuilt)


# --------------------------------------------------------------------------- #
# IO edges (config read, cache read/write, the ONE network call)
# --------------------------------------------------------------------------- #
def load_tier_tables(path: Path = _MODELS_TOML) -> dict[str, dict[str, Any]]:
    """The ``[tiers.*]`` tables from ``models.toml`` (read-only, ``tomllib``)."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise CheckError(f"Could not read {path}: {exc}") from exc
    tiers = raw.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise CheckError(f"{path}: no [tiers.<name>] tables found.")
    return {str(k): v for k, v in tiers.items() if isinstance(v, dict)}


def load_name_cache(path: Path = _NAME_CACHE) -> dict[str, str]:
    """The alias→display_name cache, or ``{}`` on first run / any read problem
    (fail-soft — a missing cache just means "no prior sighting", not an error)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def save_name_cache(names: dict[str, str], path: Path = _NAME_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(names, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_api_key() -> str | None:
    """``ANTHROPIC_API_KEY`` env first, then ``config/secrets.toml`` — same order as
    the app. No network; safe to call anywhere."""
    env = os.environ.get(_ENV_API_KEY, "").strip()
    if env:
        return env
    try:
        data = tomllib.loads((_REPO_ROOT / "config" / "secrets.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    raw = data.get("anthropic_api_key")
    return raw.strip() or None if isinstance(raw, str) else None


def fetch_models(api_key: str, url: str = _API_URL) -> list[dict[str, Any]]:
    """The ONE network call: GET the full model list (paginated). Called only from
    :func:`main`, never at import time — the killswitch stays intact."""
    out: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        query = "?limit=1000" + (f"&after_id={after}" if after else "")
        req = urllib.request.Request(
            url + query,
            method="GET",
            headers={
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
                "User-Agent": "echogist-check-models",
            },
        )
        try:
            # Explicit timeout: urlopen's default is the global socket timeout, which
            # is None — a hung connection would block forever, and this tool is meant
            # to be runnable from cron.
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise CheckError(f"Anthropic API {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CheckError(f"Network error reaching the model list: {exc.reason}.") from exc
        except TimeoutError as exc:
            # A read timeout surfaces bare, not wrapped in URLError — catch it here so
            # the tool still fails loud with a human-readable line, never a traceback.
            raise CheckError(
                f"Timed out after {_HTTP_TIMEOUT_S}s reaching the model list."
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            out.extend(item for item in data if isinstance(item, dict))
        if not (isinstance(payload, dict) and payload.get("has_more")):
            break
        last = payload.get("last_id")
        if not isinstance(last, str) or not last:
            break
        after = last
    return out


# --------------------------------------------------------------------------- #
# Report + orchestration
# --------------------------------------------------------------------------- #
@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    retired: bool = False


def format_report(findings: list[TierFinding], writes: dict[str, TierWrite]) -> Report:
    """Human-readable per-tier report + an overall retired flag (for the exit code)."""
    report = Report()
    for f in findings:
        if f.status == "retired":
            report.retired = True
            report.lines.append(
                f"  [RETIRED] {f.name}: '{f.model_id}' is not in the live model list. "
                "Pick a current model in config/models.toml."
            )
            continue
        report.lines.append(f"  [ok] {f.name}: '{f.model_id}' -> {f.matched_id} ({f.display_name})")
        if f.context_drift:
            report.lines.append(
                f"        context_window {f.config_context:,} -> {f.api_input_tokens:,} "
                "(will update from the API)"
            )
        if f.generation_bump:
            report.lines.append(
                f"        generation bump: display_name '{f.cached_display_name}' -> "
                f"'{f.display_name}' — setting prices_unverified (VERIFY PRICES manually)"
            )
    if not writes:
        report.lines.append("  No config changes needed.")
    return report


def apply_changes(
    writes: dict[str, TierWrite],
    new_cache: dict[str, str],
    *,
    models_path: Path = _MODELS_TOML,
    cache_path: Path = _NAME_CACHE,
) -> None:
    """Persist the planned config edits + the refreshed name cache. Only reached in
    a non-dry run and only after the report has printed."""
    if writes:
        original = models_path.read_text(encoding="utf-8")
        models_path.write_text(rewrite_toml(original, writes), encoding="utf-8")
    save_name_cache(new_cache, cache_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="check-models.py",
        description="Report + refresh config/models.toml against the live Anthropic model list.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report only; write no config or cache changes"
    )
    args = parser.parse_args(argv)

    try:
        tiers = load_tier_tables()
        key = resolve_api_key()
        if key is None:
            raise CheckError(
                f"No API key. Set {_ENV_API_KEY} or config/secrets.toml (anthropic_api_key)."
            )
        api_models = parse_models_response(fetch_models(key))
        cached = load_name_cache()
    except CheckError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    findings = build_report(tiers, api_models, cached)
    writes = plan_writes(findings)
    new_cache = plan_cache(findings, cached)
    report = format_report(findings, writes)

    print(f"Model currency check ({len(tiers)} tiers vs {len(api_models)} live models):")
    for line in report.lines:
        print(line)

    if args.dry_run:
        print("\n--dry-run: no changes written.")
    else:
        apply_changes(writes, new_cache)
        if writes:
            print(f"\nUpdated config/models.toml ({len(writes)} tier(s)).")
        print(f"Cache written: {_NAME_CACHE}")

    return 1 if report.retired else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
