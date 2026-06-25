#!/usr/bin/env python3
"""TD-15 Phase 2 grouping validator — group a SAVED summary for pennies, no re-pay.

The map-reduce that produced a long summary cost ~$1.5 (N map calls + reduce). The
grouping step (:func:`echogist.summarize.group_summary`) is ONE small extra call:
numbered lists in, headings + indices out. So grouping can be iterated against the
already-saved ``output/summaries/raw/<title>.json`` without re-running — exactly the
cheap validation loop TD-15 wants before grouping is wired into the pipeline.

Run (standalone; needs a key, like the smoke/eval live gates)::

    python scripts/regroup.py "output/summaries/raw/<title>.json"
    python scripts/regroup.py "<...>.json" --tier flagship

Writes the grouped ``<stem>-grouped.json`` and re-renders ``.pdf`` + ``.md`` beside
the source. The completeness invariant is logged (placed vs orphaned); the flat lists
are untouched, so a bad grouping can never lose a point. This is operator-run, never
CI — the killswitch is unaffected (it reuses the same one network call seam).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:  # allow `python scripts/regroup.py` to import echogist
    sys.path.insert(0, str(_APP_ROOT))

from echogist import config, render, summarize  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Group a saved summary (TD-15 Phase 2).")
    parser.add_argument("json_path", type=Path, help="A saved output/summaries/raw/<title>.json")
    parser.add_argument("--tier", default=None, help="Model tier (default: the Settings tier).")
    args = parser.parse_args(argv)

    json_path: Path = args.json_path
    if not json_path.is_file():
        print(f"No such summary JSON: {json_path}", file=sys.stderr)
        return 2

    api_key = config.get_api_key()
    if not api_key:
        print(
            "No API key. Set ANTHROPIC_API_KEY or config/secrets.toml — this makes one "
            "small live grouping call.",
            file=sys.stderr,
        )
        return 2

    model_config = config.load_model_config()
    settings = config.load_settings()
    tier = model_config.tier(args.tier or settings.model_tier)

    summary = render.load_summary(json_path)

    # Dump the model's RAW grouping output (headings + however it expressed indices)
    # before reconstruction — the diagnostic for tuning the prompt / spotting a model
    # that emitted indices in an unexpected shape.
    raw_path = json_path.with_name(f"{json_path.stem}-grouping-raw.json")

    def _dump_raw(spec: dict[str, object]) -> None:
        raw_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote raw model output {raw_path}")

    result = summarize.group_summary(
        summary, tier, model_config.summarize, api_key=api_key, raw_sink=_dump_raw
    )

    cost = (
        result.input_tokens / 1_000_000 * tier.price_in_per_mtok
        + result.output_tokens / 1_000_000 * tier.price_out_per_mtok
    )
    print(
        f"Grouping call: {result.input_tokens} in / {result.output_tokens} out tokens "
        f"on {tier.model_id} (~${cost:.4f})."
    )

    grouped = result.summary
    out_json = json_path.with_name(f"{json_path.stem}-grouped.json")
    out_json.write_text(
        json.dumps(asdict(grouped), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_json}")

    out_dir = json_path.parent
    for fmt in ("md", "pdf"):
        path = render.render(grouped, out_dir, fmt, base=out_json.stem)
        print(f"Rendered {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
