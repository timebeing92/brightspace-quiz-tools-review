#!/usr/bin/env python3
"""Pure, content-minimizing Quiz Binder review-queue projections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from quiz_binder_strings import ClaimError, capability_claim


FORMAT = "quiz-binder-local-queue-projection-v0"
MATCH_STATE_KEY = "coursecraft.binder.match_state"


def _question_ref(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_key": question["entity_key"],
        "permanent_code": question.get("identity", {}).get("permanent_code"),
        "kind": question.get("kind"),
    }


def _relationship_question_keys(model: dict[str, Any], statuses: set[str]) -> set[str]:
    question_keys = {row["entity_key"] for row in model.get("questions", [])}
    return {
        row["from_entity_key"]
        for row in model.get("relationships", [])
        if row.get("from_entity_key") in question_keys
        and row.get("kind") in {"member_of", "itemref"}
        and row.get("status") in statuses
    }


def _diagnostic_question_keys(model: dict[str, Any], codes: set[str]) -> set[str]:
    question_keys = {row["entity_key"] for row in model.get("questions", [])}
    return {
        entity_key
        for row in model.get("diagnostics", [])
        if row.get("status") == "open" and row.get("code") in codes
        for entity_key in row.get("entity_keys", [])
        if entity_key in question_keys
    }


def project_review_queues(
    model: dict[str, Any], registry: dict[str, Any]
) -> dict[str, Any]:
    questions = model.get("questions", [])
    incomplete_keys = _relationship_question_keys(model, {"incomplete"})
    ambiguous_keys = _relationship_question_keys(model, {"ambiguous", "proposed"})
    no_safe_diagnostic_keys = _diagnostic_question_keys(
        model, {"no_safe_library_match"}
    )

    no_safe = []
    probable = []
    blockers = []
    claims = []
    for question in questions:
        key = question["entity_key"]
        match_state = question.get("extensions", {}).get(MATCH_STATE_KEY)
        if (
            match_state == "no_safe_library_match"
            or key in incomplete_keys
            or key in no_safe_diagnostic_keys
        ):
            no_safe.append(_question_ref(question))
        if match_state == "probable_content_match" or key in ambiguous_keys:
            probable.append(_question_ref(question))

        kind = str(question.get("kind") or "unknown")
        capability = registry.get("question_kinds", {}).get(kind, {})
        status = capability.get("status", "unregistered")
        if status in {"extraction_only", "unsupported", "unregistered"}:
            blockers.append({**_question_ref(question), "reason": f"question_kind_{status}"})
        try:
            claims.append(
                capability_claim(
                    registry,
                    "question_kinds",
                    kind,
                    route="strict_model_stage1",
                )
            )
        except ClaimError as exc:
            claims.append(
                {
                    "family": "question_kinds",
                    "capability": kind,
                    "renderable": False,
                    "reason": str(exc),
                    "route": "strict_model_stage1",
                }
            )

    settings = [
        {
            "target_entity_key": row["target_entity_key"],
            "name": row["name"],
            "state": row["state"],
        }
        for row in model.get("settings_observations", [])
        if row.get("state") in {"absent", "unknown", "unresolved", "unsupported"}
    ]
    assets = [
        {
            "entity_key": row["entity_key"],
            "status": row["status"],
            "media_type": row.get("media_type"),
        }
        for row in model.get("assets", [])
        if row.get("status") != "resolved"
    ]
    diagnostics = [
        {
            "diagnostic_id": row["diagnostic_id"],
            "severity": row["severity"],
            "code": row["code"],
            "entity_keys": row.get("entity_keys", []),
        }
        for row in model.get("diagnostics", [])
        if row.get("status") == "open"
    ]

    return {
        "format": FORMAT,
        "model_id": model.get("model_id"),
        "source_fingerprint": model.get("source", {}).get("fingerprint", {}).get("digest"),
        "summary": {
            "no_safe_match_count": len(no_safe),
            "probable_match_count": len(probable),
            "settings_review_count": len(settings),
            "asset_review_count": len(assets),
            "blocker_count": len(blockers) + len(diagnostics),
        },
        "queues": {
            "no_safe_match": no_safe,
            "probable_match": probable,
            "settings_review": settings,
            "asset_review": assets,
            "blockers": blockers + diagnostics,
        },
        "capability_claims": claims,
        "content_minimized": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Project local Quiz Binder review queues.")
    parser.add_argument("model")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    result = project_review_queues(model, registry)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
