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
import posixpath
import re
import sys
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from html import unescape

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


def _rewrite_replacement_uri(content: str, original_path: str, replacement_path: str) -> tuple[str, int]:
    from quiz_build_support import package_member_path

    pattern = re.compile(
        r"(?P<prefix>\b(?:src|href|xlink:href|data|poster|background)\s*=\s*)"
        r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", re.I | re.S)
    count = 0
    original_member = package_member_path(original_path).as_posix()
    encoded_replacement = quote(replacement_path, safe="/._-")

    def replace(match):
        nonlocal count
        raw = unescape(match.group("value"))
        parsed = urlsplit(raw)
        try:
            member = package_member_path(parsed.path).as_posix()
        except ValueError:
            return match.group(0)
        if member != original_member:
            return match.group(0)
        count += 1
        value = urlunsplit(("", "", encoded_replacement, parsed.query, parsed.fragment))
        quote_char = match.group("quote")
        return match.group("prefix") + quote_char + value + quote_char
    return pattern.sub(replace, content), count


def _replace_question_asset(model: dict[str, Any], question: dict[str, Any],
                            annotation: dict[str, Any], packet_root: Path) -> None:
    value = annotation.get("value")
    if not isinstance(value, dict):
        raise PromotionError("Image replacement annotation needs an explicit asset and file record.")
    asset_key = value.get("asset_entity_key")
    original_path = value.get("original_package_path")
    relative = value.get("replacement_file")
    source = Path(relative or "")
    if (not isinstance(asset_key, str) or not isinstance(original_path, str)
            or source.is_absolute() or ".." in source.parts
            or not source.parts or source.parts[0] != "Replacement Images"):
        raise PromotionError("Image replacement path or source asset identity is unsafe.")
    source = packet_root / source
    if source.is_symlink() or not source.is_file():
        raise PromotionError("Image replacement file is missing or symlinked.")
    if sha256_file(source) != value.get("sha256"):
        raise PromotionError("Image replacement file checksum changed after review.")
    old_asset = next((a for a in model.get("assets", []) if a["entity_key"] == asset_key), None)
    if old_asset is None or old_asset.get("package_path") != original_path:
        raise PromotionError("Image replacement does not match the exact source asset in this model.")
    from quiz_review_projection import build_quiz_review_projection
    occurrences = build_quiz_review_projection(model).get("question_occurrences", [])
    matching_occurrences = [row for row in occurrences
        if row.get("occurrence_key") == value.get("occurrence_key")
        and row.get("referenced_question_key") == question["entity_key"]
        and row.get("quiz_key") == value.get("quiz_entity_key")]
    other_quizzes = {row["quiz_key"] for row in occurrences
        if row.get("referenced_question_key") == question["entity_key"]
        and row.get("quiz_key") != value.get("quiz_entity_key")}
    if len(matching_occurrences) != 1 or other_quizzes:
        raise PromotionError("Image replacement must map to one occurrence and a question used only by that quiz.")
    media = {".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
             ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
             ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
             ".gif": (b"GIF8", "image/gif")}
    signature = media.get(source.suffix.lower())
    if not signature or not source.read_bytes().startswith(signature[0]) or signature[1] != value.get("media_type"):
        raise PromotionError("Replacement image bytes do not match a supported PNG, JPEG or GIF file.")
    references = []
    contents = []
    prompt = question.get("prompt")
    if isinstance(prompt, dict) and prompt.get("format") in {"html", "xhtml"}:
        contents.append(("prompt", prompt, "content"))
    payload = question.get("type_payload", {})
    for index, option in enumerate(payload.get("options", [])):
        content = option.get("content")
        if isinstance(content, dict) and content.get("format") in {"html", "xhtml"}:
            contents.append((f"option-{index}", content, "content"))
    manual = payload.get("manual_answer_key")
    if isinstance(manual, dict) and manual.get("format") in {"html", "xhtml"}:
        contents.append(("manual-key", manual, "content"))
    for index, feedback in enumerate(question.get("feedback", [])):
        content = feedback.get("content")
        if isinstance(content, dict) and content.get("format") in {"html", "xhtml"}:
            contents.append((f"feedback-{index}", content, "content"))
    total = 0
    for _label, content, key in contents:
        rewritten, hits = _rewrite_replacement_uri(
            content.get(key, ""), original_path,
            f"review-replacements/{value['sha256'][:20]}-{source.name}")
        if hits:
            content[key] = rewritten
            total += hits
    if total == 0:
        raise PromotionError("Source image reference was not found in projected question content.")

    relations = [r for r in model.get("relationships", [])
                     if r["kind"] == "uses_asset"
                     and r["from_entity_key"] == question["entity_key"]
                     and r["to_entity_key"] == asset_key
                     and r["status"] == "resolved"]
    if not relations:
        raise PromotionError("Question has no resolved relationship to the selected source image.")
    old_asset_digest = old_asset.get("fingerprint", {}).get("digest")
    new_asset_key = "cc:asset:review-replacement:" + hashlib.sha256(
        f"{question['entity_key']}\0{asset_key}\0{value['sha256']}".encode()).hexdigest()[:28]
    if any(a["entity_key"] == new_asset_key for a in model["assets"]):
        raise PromotionError("This replacement image has already been applied to the question.")
    new_package_path = f"review-replacements/{value['sha256'][:20]}-{source.name}"
    new_asset = deepcopy(old_asset)
    new_asset.update({
        "entity_key": new_asset_key,
        "identity": {"strategy": "assigned", "permanent_code": None, "source_aliases": [],
                     "content_fingerprints": [], "extensions": {}},
        "source_path": relative,
        "package_path": new_package_path,
        "media_type": signature[1],
        "fingerprint": {"algorithm": "sha256", "digest": value["sha256"],
                        "basis": "reviewer supplied replacement bytes", "extensions": {}},
        "extensions": {"review_copy_path": relative,
                       "coursecraft.replacement": {
                           "source_asset_entity_key": asset_key,
                           "source_asset_sha256": old_asset_digest,
                           "annotation_id": annotation["annotation_id"],
                           "replacement_sha256": value["sha256"],
                       }},
    })
    model["assets"].append(new_asset)
    for relation in relations:
        new_relation = deepcopy(relation)
        new_relation.update({
            "relationship_key": "cc:relationship:review-replacement:" + hashlib.sha256(
                f"{annotation['annotation_id']}|{relation['relationship_key']}".encode()).hexdigest()[:24],
            "to_entity_key": new_asset_key,
            "source_kind": "accepted reviewer image replacement",
            "attributes": {**relation.get("attributes", {}), "raw_ref": new_package_path},
            "source_evidence_keys": [], "diagnostic_ids": [],
            "extensions": {"coursecraft.replacement_annotation_id": annotation["annotation_id"]},
        })
        model["relationships"].remove(relation)
        model["relationships"].append(new_relation)


def _add_question_to_pool(model: dict[str, Any], annotation: dict[str, Any]) -> str:
    value = annotation.get("value")
    if not isinstance(value, dict) or not isinstance(value.get("question"), dict):
        raise PromotionError("New-question decision needs a validated draft question and target pool.")
    quiz_key = value.get("target_quiz_entity_key")
    pool_key = value.get("target_pool_entity_key")
    quiz = next((q for q in model.get("quizzes", []) if q["entity_key"] == quiz_key), None)
    pool = next((s for s in model.get("structures", [])
                 if s["entity_key"] == pool_key and s.get("kind") in {"pool", "bank"}), None)
    if quiz is None or pool is None:
        raise PromotionError("New question target quiz or pool does not exist in the reviewed source model.")
    relations = model["relationships"]
    reachable = {quiz_key}
    while True:
        added = {r["to_entity_key"] for r in relations
                 if r["kind"] == "contains" and r["status"] == "resolved"
                 and r["from_entity_key"] in reachable}
        if added <= reachable:
            break
        reachable |= added
    draws = {r["from_entity_key"] for r in relations
             if r["kind"] == "draws_from" and r["status"] == "resolved"
             and r["to_entity_key"] == pool_key}
    if pool_key not in reachable and not draws.intersection(reachable):
        raise PromotionError("Target pool is not part of the selected quiz.")
    question = deepcopy(value["question"])
    code = question.get("identity", {}).get("permanent_code")
    if not isinstance(code, str) or not PERMANENT_CODE_PATTERN.fullmatch(code):
        raise PromotionError("New question needs a valid stable permanent code.")
    if any(q.get("identity", {}).get("permanent_code") == code for q in model["questions"]):
        raise PromotionError(f"New question code already exists: {code}")
    question_key = "cc:question:review-addition:" + hashlib.sha256(
        f"{quiz_key}\0{pool_key}\0{code}".encode()).hexdigest()[:28]
    if any(q["entity_key"] == question_key for q in model["questions"]):
        raise PromotionError("New question entity key collides with an existing question.")
    question["entity_key"] = question_key
    question["source_kind"] = "accepted quiz review draft"
    question.setdefault("build_support", {"level": "extraction_only", "receipt_refs": [],
                                           "notes": ["Fresh question; no import evidence."],
                                           "extensions": {}})
    question["build_support"]["level"] = "extraction_only"
    question.setdefault("build_support", {}).setdefault("notes", []).append(
        "Added to the selected quiz pool through an accepted review draft.")
    question.setdefault("extensions", {})["coursecraft.review_addition"] = {
        "annotation_id": annotation["annotation_id"],
        "source_draft_model_sha256": value.get("draft_model_sha256"),
    }
    model["questions"].append(question)
    ordinals = [r.get("ordinal") or 0 for r in relations
                if r["kind"] == "member_of" and r["to_entity_key"] == pool_key]
    relations.append({
        "relationship_key": "cc:relationship:review-addition:" + hashlib.sha256(
            annotation["annotation_id"].encode()).hexdigest()[:24],
        "kind": "member_of", "source_kind": "accepted quiz review draft",
        "from_entity_key": question_key, "to_entity_key": pool_key,
        "status": "resolved", "ordinal": max(ordinals, default=0) + 1,
        "attributes": {}, "candidates": [], "source_evidence_keys": [],
        "diagnostic_ids": [], "extensions": {"coursecraft.review_addition_annotation_id": annotation["annotation_id"]},
    })
    if isinstance(pool.get("selection", {}).get("available_count"), int):
        pool["selection"]["available_count"] += 1
    for draw_key in draws:
        draw = next((s for s in model["structures"] if s["entity_key"] == draw_key), None)
        if draw and draw_key in reachable and isinstance(draw.get("selection", {}).get("available_count"), int):
            draw["selection"]["available_count"] += 1
    return question_key


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
    # Keep the collision exclusion reason visible here for regression checks:
    # question_variant_collision_unresolved
    applied_ids: set[str] = set()
    touched_questions: set[str] = set()
    added_question_keys: set[str] = set()

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
        if field_path.startswith("/questions/add/"):
            added_key = _add_question_to_pool(output, annotation)
            questions[added_key] = output["questions"][-1]
            added_question_keys.add(added_key)
            applied.append({"annotation_id": annotation["annotation_id"],
                            "target_entity_key": target_key, "field_path": field_path,
                            "added_question_entity_key": added_key})
            applied_ids.add(annotation["annotation_id"])
            touched_questions.add(added_key)
            continue
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
        if field_path.startswith("/assets/replacements/"):
            try:
                _replace_question_asset(output, question, annotation, overlay_path.parent)
            except (OSError, ValueError, PromotionError) as exc:
                excluded.append({
                    "annotation_id": annotation["annotation_id"],
                    "target_entity_key": target_key,
                    "field_path": field_path,
                    "reason": str(exc),
                })
                continue
            applied.append({"annotation_id": annotation["annotation_id"],
                            "target_entity_key": target_key, "field_path": field_path})
            applied_ids.add(annotation["annotation_id"])
            touched_questions.add(target_key)
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
        # Review acceptance permits an edit to an extracted model copy before
        # its first sandbox import. Keep build_support unchanged: readiness and
        # generation still require evidence or an exact candidate authorization.
        editable_instance_levels = minimum_support | {"extraction_only"}
        if question.get("build_support", {}).get("level") not in editable_instance_levels:
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
