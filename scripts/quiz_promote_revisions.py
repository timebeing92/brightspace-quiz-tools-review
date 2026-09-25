#!/usr/bin/env python3
"""Deterministically promote accepted workbook revisions into a model copy.

The source model is never written. Capability eligibility comes from the live
registry, and Stage 1 records a local-route receipt without claiming new L4
evidence for the strict-model path.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from quiz_contracts import validate_contract
from quiz_review_workbook_reingest import FORMAT as OVERLAY_FORMAT, sha256_file


FORMAT = "quiz-binder-local-promotion-receipt-v0"
ACTIVATION_BASE_COMMIT = "051180117ecc097cf5fe27c248977bab34a69246"
PERMANENT_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,127}$")


class PromotionError(ValueError):
    pass


def _digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _parse_json_value(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"{label} must be valid JSON for deterministic promotion.") from exc


def _apply_options(question: dict[str, Any], value: Any) -> None:
    revised = _parse_json_value(value, "revised_response_options")
    if not isinstance(revised, list) or not revised:
        raise PromotionError("revised_response_options must be a non-empty JSON list.")
    current = question["type_payload"].get("options", [])
    if all(isinstance(item, str) for item in revised):
        if len(revised) != len(current):
            raise PromotionError("revised_response_options must preserve option count.")
        for option, content in zip(current, revised):
            option["content"]["content"] = content
        return
    if not all(isinstance(item, dict) for item in revised):
        raise PromotionError("revised_response_options must contain only strings or objects.")
    by_key = {str(option["option_key"]): option for option in current}
    if {str(item.get("option_key", "")) for item in revised} != set(by_key):
        raise PromotionError("revised_response_options must preserve every option_key.")
    for item in revised:
        content = item.get("content")
        if not isinstance(content, str):
            raise PromotionError("Each revised option object needs string content.")
        by_key[str(item["option_key"])]["content"]["content"] = content


def _apply_answer_key(question: dict[str, Any], value: Any) -> None:
    payload = question["type_payload"]
    if question.get("kind") == "long_answer" or "manual_answer_key" in payload:
        if not isinstance(value, str):
            raise PromotionError("Long-answer revised_answer_key must be text.")
        existing = payload.get("manual_answer_key") or {
            "format": "plain_text",
            "content": "",
            "extensions": {},
        }
        existing["content"] = value
        payload["manual_answer_key"] = existing
        return
    if payload.get("options"):
        revised = _parse_json_value(value, "revised_answer_key")
        if not isinstance(revised, dict) or not isinstance(
            revised.get("correct_option_keys"), list
        ):
            raise PromotionError(
                "Option answer keys require JSON: {\"correct_option_keys\": [\"A\"]}."
            )
        correct = {str(item) for item in revised["correct_option_keys"]}
        available = {str(item["option_key"]) for item in payload["options"]}
        if not correct or not correct <= available:
            raise PromotionError("revised_answer_key names an unknown or empty option set.")
        for option in payload["options"]:
            is_correct = str(option["option_key"]) in correct
            option["correct"] = is_correct
            option["weight"] = 100 if is_correct else 0
        return
    if payload.get("accepted_responses"):
        revised = _parse_json_value(value, "revised_answer_key")
        if not isinstance(revised, list) or not all(isinstance(item, str) for item in revised):
            raise PromotionError("Accepted responses require a JSON list of strings.")
        payload["accepted_responses"] = [
            {"value": item, "case_sensitive": None, "weight": None, "extensions": {}}
            for item in revised
        ]
        return
    raise PromotionError("This question kind has no deterministic Stage 1 answer-key mapping.")


def _apply_annotation(question: dict[str, Any], annotation: dict[str, Any]) -> None:
    field_path = annotation["field_path"]
    if field_path == "/identity/permanent_code":
        value = annotation["value"]
        if not isinstance(value, str) or not PERMANENT_CODE_PATTERN.fullmatch(value):
            raise PromotionError(
                "proposed_permanent_code must start with a letter and contain 3-128 "
                "letters, numbers, periods, underscores, or hyphens."
            )
        question["identity"]["permanent_code"] = value
        question["identity"].setdefault("extensions", {})[
            "coursecraft.binder.code_assignment"
        ] = {
            "annotation_id": annotation["annotation_id"],
            "actor": annotation.get("actor"),
            "timestamp": annotation.get("timestamp"),
        }
    elif field_path == "/scoring/mode":
        value = annotation["value"]
        if question.get("kind") != "multi_select" or value != "all_or_nothing":
            raise PromotionError(
                "proposed_scoring_mode currently supports only all_or_nothing multi-select."
            )
        question["scoring"]["mode"] = value
        question["scoring"].setdefault("extensions", {})[
            "coursecraft.binder.scoring_assignment"
        ] = {
            "annotation_id": annotation["annotation_id"],
            "actor": annotation.get("actor"),
            "timestamp": annotation.get("timestamp"),
        }
    elif field_path == "/prompt/content":
        if not isinstance(annotation["value"], str):
            raise PromotionError("revised_question_text must be text.")
        if question.get("prompt") is None:
            question["prompt"] = {
                "format": "plain_text",
                "content": annotation["value"],
                "extensions": {},
            }
        else:
            question["prompt"]["content"] = annotation["value"]
    elif field_path == "/type_payload/options":
        _apply_options(question, annotation["value"])
    elif field_path == "/type_payload/answer_key":
        _apply_answer_key(question, annotation["value"])
    elif field_path == "/relationships/member_of":
        raise PromotionError(
            "Library-location revisions require a stable target entity key and remain review-only."
        )
    else:
        raise PromotionError(f"Unsupported Stage 1 revision field: {field_path}")


def promote_revisions(
    model_path: Path,
    overlay_path: Path,
    registry_path: Path,
    activation_base_commit: str = ACTIVATION_BASE_COMMIT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_before = model_path.read_bytes()
    model = json.loads(source_before)
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if overlay.get("format") != OVERLAY_FORMAT:
        raise PromotionError("Unsupported decision overlay format.")
    model_sha = hashlib.sha256(source_before).hexdigest()
    if overlay.get("source_model", {}).get("sha256") != model_sha:
        raise PromotionError("Decision overlay is not bound to this exact source model.")
    if overlay.get("source_model", {}).get("model_id") != model.get("model_id"):
        raise PromotionError("Decision overlay model_id does not match the source model.")

    issues = validate_contract(model, mode="transform")
    if issues:
        raise PromotionError(f"Source model is invalid: {issues[0].render()}")

    output = deepcopy(model)
    questions = {row["entity_key"]: row for row in output.get("questions", [])}
    minimum_support = set(registry.get("policy", {}).get("minimum_question_build_support", []))
    approved = [
        row
        for row in overlay.get("annotations", [])
        if row.get("kind") == "approved_change" and row.get("status") == "accepted"
    ]
    seen_targets: dict[tuple[str, str], str] = {}
    applied: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    # A collapsed entity holds several authored questions behind one answer key,
    # so a reviewer decision cannot be attributed to any one of them.
    from quiz_normalization import variant_collision_report

    collided_entity_keys = {
        row["entity_key"] for row in variant_collision_report(model)["entities"]
    }
    applied_ids: set[str] = set()
    touched_questions: set[str] = set()

    permanent_codes = {
        str(question.get("identity", {}).get("permanent_code"))
        for question in questions.values()
        if question.get("identity", {}).get("permanent_code")
    }
    proposed_codes: dict[str, str] = {}
    for annotation in approved:
        if annotation.get("field_path") != "/identity/permanent_code":
            continue
        value = annotation.get("value")
        target_key = str(annotation.get("target_entity_key") or "")
        if not isinstance(value, str) or not PERMANENT_CODE_PATTERN.fullmatch(value):
            raise PromotionError(
                "proposed_permanent_code must start with a letter and contain 3-128 "
                "letters, numbers, periods, underscores, or hyphens."
            )
        current = questions.get(target_key, {}).get("identity", {}).get("permanent_code")
        if value in permanent_codes and value != current:
            raise PromotionError(f"Duplicate permanent question code: {value}")
        owner = proposed_codes.get(value)
        if owner is not None and owner != target_key:
            raise PromotionError(f"Duplicate permanent question code: {value}")
        proposed_codes[value] = target_key

    for annotation in sorted(approved, key=lambda row: row["annotation_id"]):
        target_key = annotation["target_entity_key"]
        field_path = annotation["field_path"]
        conflict_key = (target_key, field_path)
        value_digest = _digest(annotation.get("value"))
        if conflict_key in seen_targets and seen_targets[conflict_key] != value_digest:
            raise PromotionError(f"Conflicting accepted changes for {target_key} {field_path}.")
        seen_targets[conflict_key] = value_digest
        question = questions.get(target_key)
        if question is None:
            excluded.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": "target_question_not_found",
                }
            )
            continue
        if target_key in collided_entity_keys:
            excluded.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": "question_variant_collision_unresolved",
                }
            )
            continue
        if field_path in {"/identity/permanent_code", "/scoring/mode"}:
            if field_path == "/scoring/mode":
                kind = question.get("kind", "unknown")
                allowed_modes = (
                    registry.get("question_kinds", {})
                    .get(kind, {})
                    .get("constraints", {})
                    .get("scoring_modes", [])
                )
                if annotation.get("value") not in allowed_modes:
                    raise PromotionError(
                        "proposed_scoring_mode is not allowed by the capability registry."
                    )
            _apply_annotation(question, annotation)
            applied.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                }
            )
            applied_ids.add(annotation["annotation_id"])
            touched_questions.add(target_key)
            continue
        kind = question.get("kind", "unknown")
        capability = registry.get("question_kinds", {}).get(kind, {})
        capability_status = capability.get("status", "unregistered")
        if capability_status not in minimum_support:
            excluded.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": f"question_kind_{capability_status}",
                }
            )
            continue
        if question.get("build_support", {}).get("level") not in minimum_support:
            excluded.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": "question_instance_not_build_approved",
                }
            )
            continue
        try:
            _apply_annotation(question, annotation)
        except PromotionError as exc:
            excluded.append(
                {
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": str(exc),
                }
            )
            continue
        applied.append(
            {
                "annotation_id": annotation["annotation_id"],
                "target_entity_key": target_key,
                "field_path": field_path,
            }
        )
        applied_ids.add(annotation["annotation_id"])
        touched_questions.add(target_key)

    existing_ids = {row["annotation_id"] for row in output.get("annotations", [])}
    for annotation in overlay.get("annotations", []):
        if annotation["annotation_id"] in existing_ids:
            continue
        copied = deepcopy(annotation)
        if copied["annotation_id"] in applied_ids:
            copied["status"] = "applied"
        output["annotations"].append(copied)
        existing_ids.add(copied["annotation_id"])

    overlay_sha = sha256_file(overlay_path)
    promotion_token = _digest(
        {
            "model_sha256": model_sha,
            "overlay_sha256": overlay_sha,
            "registry_sha256": sha256_file(registry_path),
            "applied": applied,
            "excluded": excluded,
        }
    )
    output["model_id"] = f"cc:model:quiz-binder:{promotion_token[:24]}"
    output["run_id"] = f"cc:run:quiz-binder:{promotion_token[:24]}"
    output.setdefault("extensions", {})["coursecraft.binder_stage1"] = {
        "route": "strict_model_stage1",
        "route_status": "local_only",
        "activation_base_commit": activation_base_commit,
        "source_model_sha256": model_sha,
        "decision_overlay_sha256": overlay_sha,
        "materialized_view_fingerprint": overlay.get("materialized_view_fingerprint"),
        "promotion_token": promotion_token,
    }
    for target_key in sorted(touched_questions):
        output["lineage"].append(
            {
                "lineage_id": f"lin.promotion.{promotion_token[:12]}.{_digest(target_key)[:12]}",
                "subject_entity_key": target_key,
                "stage": "approved",
                "method": "human_revision",
                "status": "lossless",
                "predecessor_entity_keys": [target_key],
                "input_evidence_keys": [],
                "actor": "scripts.quiz_promote_revisions",
                "timestamp": None,
                "receipt_ref": f"quiz-binder-promotion:{promotion_token}",
                "notes": ["Approved marginalia applied to a model copy; source bytes unchanged."],
                "extensions": {"coursecraft.binder.route_status": "local_only"},
            }
        )

    output_issues = validate_contract(output, mode="transform")
    if output_issues:
        raise PromotionError(f"Promoted model is invalid: {output_issues[0].render()}")
    if model_path.read_bytes() != source_before:
        raise PromotionError("Source model changed during promotion.")

    receipt = {
        "format": FORMAT,
        "activation_base_commit": activation_base_commit,
        "route": "strict_model_stage1",
        "route_status": "local_only",
        "source_model_sha256": model_sha,
        "decision_overlay_sha256": overlay_sha,
        "materialized_view_fingerprint": overlay.get("materialized_view_fingerprint"),
        "registry_sha256": sha256_file(registry_path),
        "promoted_model_id": output["model_id"],
        "promoted_model_fingerprint": _digest(output),
        "promotion_token": promotion_token,
        "applied": applied,
        "excluded": excluded,
        "summary": {
            "accepted_change_count": len(approved),
            "applied_change_count": len(applied),
            "excluded_change_count": len(excluded),
        },
        "content_minimized": True,
    }
    return output, receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote accepted Quiz Binder revisions locally.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output-model")
    parser.add_argument("--output-receipt")
    parser.add_argument(
        "--activation-base-commit",
        default=ACTIVATION_BASE_COMMIT,
        help="Activation commit governing this promotion run.",
    )
    args = parser.parse_args()
    try:
        output, receipt = promote_revisions(
            Path(args.model),
            Path(args.overlay),
            Path(args.registry),
            args.activation_base_commit,
        )
    except (OSError, json.JSONDecodeError, PromotionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output_model:
        Path(args.output_model).write_text(
            json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.output_receipt:
        Path(args.output_receipt).write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
