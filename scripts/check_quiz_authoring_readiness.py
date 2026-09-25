#!/usr/bin/env python3
"""Report whether a reviewed coursecraft.quiz/1 model is ready to build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from quiz_authoring_readiness import (
    analyze_authoring_readiness,
    render_readiness_markdown,
    safe_label,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="Reviewed coursecraft.quiz/1 JSON model.")
    parser.add_argument("--quiz-entity-key", default="", help="Quiz key when the model contains multiple quizzes.")
    parser.add_argument("--settings", type=Path, default=None, help="Optional coursecraft.quiz_settings/1 receipt.")
    parser.add_argument("--asset-root", type=Path, default=None, help="Root for model asset source paths.")
    parser.add_argument("--promotion-receipt", type=Path, default=None, help="Verified Quiz Binder promotion receipt for strict-route chaining.")
    parser.add_argument("--phase5-candidate-authorization", type=Path, default=None, help="Exact local-only Phase 5 candidate authorization.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "workspace" / "review",
        help="Directory for JSON and Markdown reports.",
    )
    parser.add_argument("--fail-if-not-ready", action="store_true", help="Exit 1 when readiness errors exist.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    model_path = args.model.expanduser().resolve()
    report = analyze_authoring_readiness(
        model_path,
        quiz_entity_key=args.quiz_entity_key,
        settings_path=args.settings.expanduser().resolve() if args.settings else None,
        asset_root=args.asset_root.expanduser().resolve() if args.asset_root else None,
        promotion_receipt_path=(
            args.promotion_receipt.expanduser().resolve()
            if args.promotion_receipt
            else None
        ),
        trial_authorization_path=(
            args.phase5_candidate_authorization.expanduser().resolve()
            if args.phase5_candidate_authorization
            else None
        ),
    )
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_label(model_path.stem)}__quiz_authoring_readiness"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_readiness_markdown(report), encoding="utf-8")
    print(f"ready: {'yes' if report['ready'] else 'no'}")
    print(f"errors: {report['summary']['error_count']}")
    print(f"warnings: {report['summary']['warning_count']}")
    print(f"report: {markdown_path}")
    print(f"json: {json_path}")
    return 1 if args.fail_if_not_ready and not report["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
