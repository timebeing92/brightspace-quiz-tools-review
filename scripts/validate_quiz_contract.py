#!/usr/bin/env python3
"""Validate CourseCraft quiz-family JSON contracts and semantic joins."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from quiz_contracts import check_schema_documents, validate_contract


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path, help="JSON record paths")
    parser.add_argument(
        "--mode",
        choices=("inspect", "transform"),
        default="inspect",
        help="Unknown versions warn during inspection but fail before transformation.",
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Return a non-zero status when warnings are present.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_schema_documents()
    issue_count = 0
    warning_count = 0
    for path in args.records:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: invalid_json at {path}: {exc}")
            issue_count += 1
            continue
        issues = validate_contract(record, mode=args.mode)
        if not issues:
            print(f"ok: {path} ({record.get('schema', 'unknown')})")
            continue
        print(f"issues: {path} ({record.get('schema', 'unknown')})")
        for issue in issues:
            print(f"  {issue.render()}")
            if issue.severity == "error":
                issue_count += 1
            else:
                warning_count += 1
    if issue_count or (args.warnings_as_errors and warning_count):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
