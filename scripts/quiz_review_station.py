#!/usr/bin/env python3
"""Build a content-rich, local-only Quiz Binder review station.

The station is a static reader over ``coursecraft.quiz/1`` and the canonical
entity/occurrence projection.  It does not parse an export, contact
Brightspace, or infer that an extracted question can be rebuilt.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
import zipfile

from quiz_binder_strings import LABELS, match_label, value_label
from quiz_review_projection import build_quiz_review_projection


FORMAT = "coursecraft.quiz_review_station/0"
SHARE_CLASSIFICATION = "local_course_evidence"
ARTIFACT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
QUESTION_SHARD_SIZE = 1
OCCURRENCE_SHARD_SIZE = 200
CATALOG_SHARD_SIZE = 250
PLACEHOLDER_QUESTION_TITLES = {
    "",
    "question",
    "question title",
    "untitled",
    "untitled question",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _meaningful_question_title(value: Any) -> str | None:
    title = " ".join(str(value or "").split())
    return title if title.casefold() not in PLACEHOLDER_QUESTION_TITLES else None


def _canonical_question_title(entity: Mapping[str, Any]) -> str:
    title = _meaningful_question_title(entity.get("title"))
    if title:
        return title
    prompt = _as_dict(entity.get("prompt"))
    content = str(prompt.get("content") or "")
    if str(prompt.get("format") or "").casefold() == "html":
        content = re.sub(r"<[^>]*>", " ", content)
    preview = " ".join(html.unescape(content).split())
    if len(preview) > 96:
        preview = preview[:93].rstrip() + "…"
    return preview or "Question detail"


def _placement_question_title(
    question: Mapping[str, Any], row: Mapping[str, Any]
) -> str:
    title = _meaningful_question_title(question.get("title"))
    if title:
        return title
    ordinal = row.get("question_ordinal")
    return f"Question {ordinal}" if ordinal is not None else "Question reference"


def _occurrence_needs_source_review(row: Mapping[str, Any]) -> bool:
    return bool(_occurrence_review_lanes(row))


def _occurrence_review_lanes(row: Mapping[str, Any]) -> set[str]:
    lanes: set[str] = set()
    match_code = str(row.get("match_status_code") or "")
    relationship_status = str(row.get("source_status") or "")
    if match_code in {
        "unresolved",
        "unmatched",
        "no_safe_library_match",
    } or relationship_status in {
        "incomplete",
        "unresolved",
    }:
        lanes.add("no_safe")
    if match_code in {
        "inferred_similarity",
        "ambiguous",
        "probable_content_match",
    } or (
        row.get("match_explanation")
        and "more than one" in str(row.get("match_explanation")).lower()
    ):
        lanes.add("probable")
    return lanes


def _section_display_title(
    title: Any, ordinal: Any, *, fallback_ordinal: int | None = None
) -> str:
    number = ordinal if ordinal is not None else fallback_ordinal
    source_title = " ".join(str(title or "").split())
    if source_title.casefold() in {"", "section", "quiz section"}:
        return f"Section {number}" if number is not None else "Unsectioned questions"
    if number is None:
        return source_title
    return f"Section {number} · {source_title}"


def _local_ref(value: Any) -> str | None:
    """Return a display-safe reference without leaking an absolute local path."""

    if value is None:
        return None
    text = str(value)
    if not text:
        return text
    parsed = urlsplit(text)
    if parsed.scheme == "file":
        name = Path(parsed.path).name
        return (
            f"[absolute local path withheld]/{name}"
            if name
            else "[absolute local path withheld]"
        )
    if Path(text).is_absolute() or PureWindowsPath(text).is_absolute():
        name = Path(text.replace("\\", "/")).name
        return (
            f"[absolute local path withheld]/{name}"
            if name
            else "[absolute local path withheld]"
        )
    return text


def _formatted_content(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "format": str(value.get("format") or "unknown"),
        "content": str(value.get("content") or ""),
    }


def _safe_identity(identity: Any) -> dict[str, Any]:
    row = _as_dict(identity)
    aliases = []
    for alias in _as_list(row.get("source_aliases")):
        alias = _as_dict(alias)
        aliases.append(
            {
                "namespace": alias.get("namespace"),
                "value": alias.get("value"),
                "scope": alias.get("scope"),
            }
        )
    fingerprints = []
    for fingerprint in _as_list(row.get("content_fingerprints")):
        fingerprint = _as_dict(fingerprint)
        fingerprints.append(
            {
                "algorithm": fingerprint.get("algorithm"),
                "digest": fingerprint.get("digest"),
                "basis": fingerprint.get("basis"),
            }
        )
    return {
        "strategy": row.get("strategy"),
        "permanent_code": row.get("permanent_code"),
        "source_aliases": aliases,
        "content_fingerprints": fingerprints,
    }


def _safe_evidence(evidence: Any) -> dict[str, Any]:
    row = _as_dict(evidence)
    return {
        "evidence_key": row.get("evidence_key"),
        "kind": row.get("kind"),
        "source_ref": _local_ref(row.get("source_ref")),
        "locator": row.get("locator"),
        "content_ref": _local_ref(row.get("content_ref")),
        "sha256": row.get("sha256"),
        "extraction_method": row.get("extraction_method"),
    }


def _safe_lineage(lineage: Any) -> dict[str, Any]:
    row = _as_dict(lineage)
    return {
        "lineage_id": row.get("lineage_id"),
        "stage": row.get("stage"),
        "method": row.get("method"),
        "status": row.get("status"),
        "predecessor_entity_keys": _as_list(row.get("predecessor_entity_keys")),
        "input_evidence_keys": _as_list(row.get("input_evidence_keys")),
        "actor": row.get("actor"),
        "receipt_ref": _local_ref(row.get("receipt_ref")),
        "notes": _as_list(row.get("notes")),
    }


def _safe_diagnostic(diagnostic: Any) -> dict[str, Any]:
    row = _as_dict(diagnostic)
    return {
        "diagnostic_id": row.get("diagnostic_id"),
        "severity": row.get("severity"),
        "code": row.get("code"),
        "message": row.get("message"),
        "status": row.get("status"),
        "entity_keys": _as_list(row.get("entity_keys")),
        "evidence_keys": _as_list(row.get("evidence_keys")),
        "details": _as_dict(row.get("details")),
    }


def _safe_asset(asset: Any) -> dict[str, Any]:
    row = _as_dict(asset)
    fingerprint = _as_dict(row.get("fingerprint"))
    return {
        "entity_key": row.get("entity_key"),
        "source_path": _local_ref(row.get("source_path")),
        "package_path": _local_ref(row.get("package_path")),
        "media_type": row.get("media_type"),
        "status": row.get("status"),
        "fingerprint": (
            {
                "algorithm": fingerprint.get("algorithm"),
                "digest": fingerprint.get("digest"),
            }
            if fingerprint
            else None
        ),
        "diagnostic_ids": _as_list(row.get("diagnostic_ids")),
    }


def _response_view(payload: Any) -> dict[str, Any]:
    row = _as_dict(payload)
    options = []
    for option in _as_list(row.get("options")):
        option = _as_dict(option)
        options.append(
            {
                "option_key": option.get("option_key"),
                "content": _formatted_content(option.get("content")),
                "correct": option.get("correct"),
                "weight": option.get("weight"),
                "source_evidence_keys": _as_list(option.get("source_evidence_keys")),
            }
        )
    accepted = []
    for answer in _as_list(row.get("accepted_responses")):
        answer = _as_dict(answer)
        accepted.append(
            {
                "value": answer.get("value"),
                "case_sensitive": answer.get("case_sensitive"),
                "weight": answer.get("weight"),
            }
        )
    blanks = []
    for blank in _as_list(row.get("blanks")):
        blank = _as_dict(blank)
        blank_answers = []
        for answer in _as_list(blank.get("accepted_responses")):
            answer = _as_dict(answer)
            blank_answers.append(
                {
                    "value": answer.get("value"),
                    "case_sensitive": answer.get("case_sensitive"),
                    "weight": answer.get("weight"),
                }
            )
        blanks.append(
            {
                "blank_key": blank.get("blank_key"),
                "accepted_responses": blank_answers,
                "source_evidence_keys": _as_list(blank.get("source_evidence_keys")),
            }
        )
    pairs = []
    for pair in _as_list(row.get("match_pairs")):
        pair = _as_dict(pair)
        pairs.append(
            {
                "left_key": pair.get("left_key"),
                "right_key": pair.get("right_key"),
                "weight": pair.get("weight"),
                "source_evidence_keys": _as_list(pair.get("source_evidence_keys")),
            }
        )
    order = []
    for item in _as_list(row.get("correct_order")):
        item = _as_dict(item)
        order.append(
            {
                "option_key": item.get("option_key"),
                "position": item.get("position"),
                "source_evidence_keys": _as_list(item.get("source_evidence_keys")),
            }
        )
    raw_models = []
    for raw in _as_list(row.get("raw_response_models")):
        raw = _as_dict(raw)
        raw_models.append(
            {
                "source_kind": raw.get("source_kind"),
                "payload": raw.get("payload"),
                "source_evidence_keys": _as_list(raw.get("source_evidence_keys")),
            }
        )
    return {
        "options": options,
        "accepted_responses": accepted,
        "blanks": blanks,
        "match_pairs": pairs,
        "correct_order": order,
        "manual_answer_key": _formatted_content(row.get("manual_answer_key")),
        "raw_response_models": raw_models,
    }


def _feedback_view(feedback: Any) -> list[dict[str, Any]]:
    rows = []
    for item in _as_list(feedback):
        item = _as_dict(item)
        rows.append(
            {
                "channel": item.get("channel"),
                "source_kind": item.get("source_kind"),
                "content": _formatted_content(item.get("content")),
                "source_evidence_keys": _as_list(item.get("source_evidence_keys")),
            }
        )
    return rows


def _display_identifier(question: Mapping[str, Any]) -> str:
    identity = _as_dict(question.get("identity"))
    code = identity.get("permanent_code")
    if code:
        return str(code)
    aliases = _as_list(identity.get("source_aliases"))
    if aliases and isinstance(aliases[0], dict) and aliases[0].get("value"):
        return str(aliases[0]["value"])
    return str(question.get("entity_key") or "unknown-question")


def _match_state(question: Mapping[str, Any]) -> str:
    extensions = _as_dict(question.get("extensions"))
    return str(
        extensions.get("coursecraft.binder.match_state")
        or extensions.get("coursecraft.source_match_status")
        or "not_recorded"
    )


def _projection_entity_key(row: Mapping[str, Any]) -> str | None:
    value = (
        row.get("question_key")
        or row.get("entity_key")
        or row.get("question_entity_key")
    )
    return str(value) if value else None


def _occurrence_value(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return default


def _path_titles(path: Any) -> list[str]:
    if isinstance(path, str) and path:
        return [path]
    titles: list[str] = []
    for item in _as_list(path):
        if isinstance(item, dict):
            value = item.get("title") or item.get("label") or item.get("entity_key")
        else:
            value = item
        if value is not None:
            titles.append(str(value))
    return titles


def _match_candidates(row: Mapping[str, Any]) -> list[str]:
    memberships = [_as_dict(value) for value in _as_list(row.get("pool_memberships"))]
    question_candidates = [
        _as_dict(candidate)
        for membership in memberships
        for candidate in _as_list(membership.get("candidates"))
    ]
    if question_candidates:
        return _dedupe(
            [
                "{}{}".format(
                    candidate.get("qmd_displayid")
                    or candidate.get("question_label")
                    or candidate.get("qmd_globalid")
                    or candidate.get("question_ident")
                    or "Unnamed library candidate",
                    (
                        f" (score {candidate.get('score')})"
                        if candidate.get("score") is not None
                        else ""
                    ),
                )
                for candidate in question_candidates
            ]
        )
    return _dedupe(
        [
            str(
                membership.get("pool_title")
                or membership.get("pool_path")
                or membership.get("pool_key")
            )
            for membership in memberships
            if membership.get("pool_title")
            or membership.get("pool_path")
            or membership.get("pool_key")
        ]
    )


def _match_explanation(row: Mapping[str, Any], match_code: str) -> str:
    projection_codes = {
        str(_as_dict(value).get("code") or "")
        for value in _as_list(row.get("projection_diagnostics"))
    }
    source_codes = {
        str(_as_dict(value).get("code") or "")
        for value in _as_list(row.get("source_diagnostics"))
    }
    candidate_scores = [
        float(candidate.get("score"))
        for membership in (
            _as_dict(value) for value in _as_list(row.get("pool_memberships"))
        )
        for candidate in (
            _as_dict(value) for value in _as_list(membership.get("candidates"))
        )
        if isinstance(candidate.get("score"), (int, float))
    ]
    best_score_tie = (
        bool(candidate_scores) and candidate_scores.count(max(candidate_scores)) > 1
    )
    ambiguous = (
        match_code == "ambiguous"
        or bool(
            (projection_codes | source_codes)
            & {
                "occurrence_pool_ambiguous",
                "draw_pool_context_ambiguous",
                "ambiguous_library_match",
            }
        )
        or best_score_tie
    )
    if ambiguous:
        return (
            "More than one library candidate remains plausible. A person must "
            "choose or decline; the Binder will not guess."
        )
    if match_code in {"unresolved", "unmatched", "no_safe_library_match"}:
        return (
            "No defensible question-library location was resolved. The question "
            "remains visible for review."
        )
    if match_code in {"inferred_similarity", "probable_content_match"}:
        return (
            "This is a content-based proposal rather than a direct library link. "
            "A person must confirm or decline it."
        )
    notes = _dedupe(
        [
            str(_as_dict(value).get("note"))
            for value in _as_list(row.get("pool_memberships"))
            if _as_dict(value).get("note")
        ]
    )
    return " | ".join(notes)


def _normalize_occurrence(
    row: Mapping[str, Any],
    question: Mapping[str, Any] | None,
    ordinal: int,
    *,
    diagnostic_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    context = _as_dict(row.get("context"))
    quiz = _as_dict(row.get("quiz"))
    section = _as_dict(row.get("section"))
    pool = _as_dict(row.get("pool"))
    draw = _as_dict(row.get("draw"))
    question_key = _occurrence_value(
        row,
        "referenced_question_key",
        "observed_question_entity_key",
        "question_entity_key",
        "entity_key",
        "question_key",
    )
    question = question or {}
    section_path = _path_titles(
        _occurrence_value(
            row, "section_path", "structure_path", default=context.get("section_path")
        )
    )
    quiz_title = _occurrence_value(
        row,
        "quiz_title",
        default=quiz.get("title") or context.get("quiz_title"),
    )
    section_title = _occurrence_value(
        row,
        "section_title",
        default=section.get("title")
        or context.get("section_title")
        or (section_path[-1] if section_path else None),
    )
    pool_title = _occurrence_value(
        row,
        "pool_title",
        default=pool.get("title") or context.get("pool_title"),
    )
    pool_memberships = [
        _as_dict(value) for value in _as_list(row.get("pool_memberships"))
    ]
    primary_pool = pool_memberships[0] if pool_memberships else {}
    pool_title = pool_title or primary_pool.get("pool_title")
    selection_context = _as_dict(row.get("selection_context"))
    draw_mode = _occurrence_value(
        row,
        "selection_mode",
        "draw_mode",
        default=draw.get("mode")
        or context.get("selection_mode")
        or selection_context.get("mode"),
    )
    diagnostics = _dedupe(_as_list(row.get("diagnostic_ids")))
    source_status = str(
        _occurrence_value(
            row,
            "source_status",
            "resolution_status",
            "relationship_status",
            "status",
            default="not_recorded",
        )
    )
    projected_match_status = str(
        _occurrence_value(
            row, "library_match_status", "match_status", default="not_recorded"
        )
    )
    entity_match_status = _match_state(question) if question else "not_recorded"
    display_match_status = (
        projected_match_status
        if projected_match_status not in {"", "unknown", "not_recorded"}
        else entity_match_status
    )
    candidates = _match_candidates(row)
    diagnostic_lookup = diagnostic_by_id or {}
    explanation_row = dict(row)
    explanation_row["source_diagnostics"] = [
        diagnostic_lookup[diagnostic_id]
        for diagnostic_id in _as_list(row.get("source_diagnostic_ids"))
        if diagnostic_id in diagnostic_lookup
    ]
    explanation_row["projection_diagnostics"] = [
        diagnostic_lookup[diagnostic_id]
        for diagnostic_id in _as_list(row.get("projection_diagnostic_ids"))
        if diagnostic_id in diagnostic_lookup
    ]
    match_explanation = _match_explanation(explanation_row, display_match_status)
    return {
        "occurrence_key": str(
            _occurrence_value(
                row,
                "occurrence_key",
                "relationship_key",
                default=f"occurrence-{ordinal}",
            )
        ),
        "question_entity_key": question_key,
        "quiz_entity_key": _occurrence_value(
            row,
            "quiz_key",
            "quiz_entity_key",
            default=quiz.get("entity_key") or context.get("quiz_entity_key"),
        ),
        "quiz_title": quiz_title or "Unplaced / library-only",
        "quiz_ordinal": _occurrence_value(
            row, "quiz_order", "quiz_ordinal", default=context.get("quiz_ordinal")
        ),
        "section_entity_key": _occurrence_value(
            row,
            "section_key",
            "section_entity_key",
            "structure_entity_key",
            default=section.get("entity_key") or context.get("section_entity_key"),
        ),
        "section_title": section_title or "No section recorded",
        "section_path": section_path,
        "section_ordinal": _occurrence_value(
            row,
            "section_order",
            "section_ordinal",
            default=section.get("ordinal") or context.get("section_ordinal"),
        ),
        "question_ordinal": _occurrence_value(
            row,
            "quiz_occurrence_order",
            "quiz_projection_order",
            "section_occurrence_order",
            "question_ordinal",
            "ordinal",
            default=context.get("question_ordinal"),
        ),
        "source_kind": _occurrence_value(
            row,
            "placement_kind",
            "source_kind",
            default=context.get("source_kind") or "not_recorded",
        ),
        "source_status": source_status,
        "source_status_label": value_label(source_status),
        "match_status": match_label(display_match_status),
        "match_status_code": display_match_status,
        "occurrence_match_status": match_label(projected_match_status),
        "occurrence_match_status_code": projected_match_status,
        "entity_match_status": match_label(entity_match_status),
        "entity_match_status_code": entity_match_status,
        "match_candidates": candidates,
        "match_explanation": match_explanation,
        "pool_entity_key": _occurrence_value(
            row,
            "pool_entity_key",
            default=pool.get("entity_key")
            or context.get("pool_entity_key")
            or primary_pool.get("pool_key"),
        ),
        "pool_title": pool_title,
        "selection_mode": draw_mode or "not_recorded",
        "draw_count": _occurrence_value(
            row,
            "draw_count",
            "requested_count",
            default=draw.get("requested_count")
            or context.get("requested_count")
            or selection_context.get("draw_count"),
        ),
        "candidate_pool_size": _occurrence_value(
            row,
            "candidate_pool_size",
            "available_count",
            default=draw.get("available_count")
            or context.get("available_count")
            or selection_context.get("candidate_pool_size")
            or primary_pool.get("available_count"),
        ),
        "points": _occurrence_value(
            row,
            "points",
            "maximum_points",
            default=None,
        ),
        "diagnostic_ids": diagnostics,
        "source_diagnostic_ids": _dedupe(_as_list(row.get("source_diagnostic_ids"))),
        "projection_diagnostic_ids": _dedupe(
            _as_list(row.get("projection_diagnostic_ids"))
        ),
        "pool_memberships": pool_memberships,
        "selection_context": selection_context,
        "source_evidence_keys": _dedupe(_as_list(row.get("source_evidence_keys"))),
    }


def _action_queue(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in _as_list(model.get("diagnostics")):
        row = _as_dict(row)
        if row.get("status") == "open":
            actions.append(
                {
                    "action_key": f"diagnostic:{row.get('diagnostic_id')}",
                    "category": "diagnostic",
                    "severity": row.get("severity"),
                    "code": row.get("code"),
                    "message": row.get("message"),
                    "entity_keys": _as_list(row.get("entity_keys")),
                }
            )
    for row in _as_list(model.get("relationships")):
        row = _as_dict(row)
        if row.get("status") in {"ambiguous", "incomplete", "proposed"}:
            actions.append(
                {
                    "action_key": f"relationship:{row.get('relationship_key')}",
                    "category": "relationship",
                    "severity": "warning",
                    "code": f"relationship_{row.get('status')}",
                    "message": f"Review {row.get('kind') or 'relationship'}: {row.get('status')}",
                    "entity_keys": [
                        value
                        for value in [
                            row.get("from_entity_key"),
                            row.get("to_entity_key"),
                        ]
                        if value
                    ],
                }
            )
    for row in _as_list(model.get("settings_observations")):
        row = _as_dict(row)
        if row.get("state") in {"absent", "unknown", "unresolved", "unsupported"}:
            actions.append(
                {
                    "action_key": f"setting:{row.get('target_entity_key')}:{row.get('name')}",
                    "category": "setting",
                    "severity": "warning",
                    "code": f"setting_{row.get('state')}",
                    "message": f"Review {row.get('name') or 'setting'}: {row.get('state')}",
                    "entity_keys": (
                        [row.get("target_entity_key")]
                        if row.get("target_entity_key")
                        else []
                    ),
                }
            )
    for row in _as_list(model.get("assets")):
        row = _as_dict(row)
        if row.get("status") != "resolved":
            actions.append(
                {
                    "action_key": f"asset:{row.get('entity_key')}",
                    "category": "asset",
                    "severity": "warning",
                    "code": f"asset_{row.get('status') or 'unknown'}",
                    "message": f"Review asset: {row.get('status') or 'unknown'}",
                    "entity_keys": (
                        [row.get("entity_key")] if row.get("entity_key") else []
                    ),
                }
            )
    return _dedupe(actions)


def _review_queues(
    model: Mapping[str, Any],
    occurrences: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    projection_diagnostics: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build named reviewer lanes without flattening distinct evidence states."""

    no_safe: list[dict[str, Any]] = []
    probable: list[dict[str, Any]] = []
    entity_by_key = {
        str(row.get("entity_key")): row for row in entities if row.get("entity_key")
    }
    for row in occurrences:
        question = entity_by_key.get(str(row.get("question_entity_key")), {})
        item = {
            "item_key": row.get("occurrence_key"),
            "occurrence_key": row.get("occurrence_key"),
            "question_entity_key": row.get("question_entity_key"),
            "display_identifier": question.get("display_identifier"),
            "title": question.get("title"),
            "quiz_title": row.get("quiz_title"),
            "section_title": row.get("section_title"),
            "match_label": row.get("match_status"),
            "match_code": row.get("match_status_code"),
            "explanation": row.get("match_explanation"),
            "candidates": _as_list(row.get("match_candidates")),
        }
        lanes = _occurrence_review_lanes(row)
        if "no_safe" in lanes:
            no_safe.append(item)
        if "probable" in lanes:
            probable.append(item)

    settings = [
        {
            "item_key": f"setting:{row.get('target_entity_key')}:{row.get('name')}",
            "target_entity_key": row.get("target_entity_key"),
            "code": row.get("name"),
            "state": row.get("state"),
            "message": f"{value_label(row.get('name'))}: {value_label(row.get('state'))}",
        }
        for row in (
            _as_dict(value) for value in _as_list(model.get("settings_observations"))
        )
        if row.get("state") in {"absent", "unknown", "unresolved", "unsupported"}
    ]
    assets = [
        {
            "item_key": f"asset:{row.get('entity_key')}",
            "entity_key": row.get("entity_key"),
            "status": row.get("status"),
            "message": f"{_local_ref(row.get('source_path') or row.get('package_path') or row.get('entity_key'))}: {value_label(row.get('status'))}",
        }
        for row in (_as_dict(value) for value in _as_list(model.get("assets")))
        if row.get("status") != "resolved"
    ]
    unsupported = [
        {
            "item_key": f"question:{row.get('entity_key')}",
            "question_entity_key": row.get("entity_key"),
            "display_identifier": row.get("display_identifier"),
            "title": row.get("title"),
            "level": _as_dict(row.get("build_support")).get("level"),
            "level_label": _as_dict(row.get("build_support")).get("level_label"),
            "message": "Review only — this question is not admitted to the current build route.",
        }
        for row in entities
        if _as_dict(row.get("build_support")).get("level")
        in {"extraction_only", "unsupported", "unknown", "not_recorded", None}
    ]
    findings = [
        {
            "item_key": f"diagnostic:{row.get('diagnostic_id')}",
            "diagnostic_id": row.get("diagnostic_id"),
            "severity": row.get("severity"),
            "code": row.get("code"),
            "message": row.get("message"),
            "entity_keys": _as_list(row.get("entity_keys")),
        }
        for row in [
            *(
                _safe_diagnostic(value)
                for value in _as_list(model.get("diagnostics"))
                if _as_dict(value).get("status") == "open"
            ),
            *projection_diagnostics,
        ]
        if row.get("severity") in {"warning", "error"}
    ]
    return {
        "no_safe_match": _dedupe(no_safe),
        "probable_match": _dedupe(probable),
        "settings_review": _dedupe(settings),
        "asset_review": _dedupe(assets),
        "unsupported_kinds": _dedupe(unsupported),
        "findings": _dedupe(findings),
    }


def _safe_artifact_links(
    artifact_links: Mapping[str, str] | None,
    *,
    output_dir: Path | None = None,
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for name, href in sorted((artifact_links or {}).items()):
        if not ARTIFACT_NAME_RE.fullmatch(str(name)):
            raise ValueError(f"unsafe artifact link name: {name!r}")
        text = str(href)
        parsed = urlsplit(text)
        if (
            not text
            or parsed.scheme
            or parsed.netloc
            or text.startswith(("/", "\\"))
            or "\\" in text
            or PureWindowsPath(text).is_absolute()
        ):
            raise ValueError(f"artifact link must be a relative local path: {text!r}")
        path = PurePosixPath(parsed.path)
        if (
            not parsed.path
            or path.is_absolute()
            or any(part in {"", "."} for part in path.parts)
        ):
            raise ValueError(f"artifact link is not normalized: {text!r}")
        if output_dir is None and ".." in path.parts:
            raise ValueError(
                "parent artifact links require an output directory safety boundary"
            )
        if output_dir is not None:
            run_root = output_dir.resolve().parent
            resolved = (output_dir.resolve() / Path(*path.parts)).resolve()
            try:
                resolved.relative_to(run_root)
            except ValueError as exc:
                raise ValueError(
                    f"artifact link escapes the local run: {text!r}"
                ) from exc
        label = name.replace("_", " ").strip().title()
        links.append({"name": str(name), "label": label, "href": text})
    return links


def build_quiz_review_station(
    model: dict[str, Any],
    *,
    artifact_links: Mapping[str, str] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Project a rich, local-only station from a canonical quiz model."""

    if model.get("schema") != "coursecraft.quiz/1":
        raise ValueError("review station input must use schema coursecraft.quiz/1")

    projection = build_quiz_review_projection(model)
    projected_entities = _as_list(projection.get("question_entities"))
    projected_by_key = {
        key: row
        for row in projected_entities
        if isinstance(row, dict)
        for key in [_projection_entity_key(row)]
        if key
    }
    projected_keys = [
        key
        for row in projected_entities
        if isinstance(row, dict)
        for key in [_projection_entity_key(row)]
        if key
    ]
    questions_by_key = {
        str(row.get("entity_key")): row
        for row in _as_list(model.get("questions"))
        if isinstance(row, dict)
        and row.get("entity_key")
        and not _as_dict(row.get("extensions")).get("library_only")
    }
    ordered_keys = _dedupe([*projected_keys, *questions_by_key.keys()])
    source_diagnostic_catalog = [
        {**_safe_diagnostic(row), "catalog_scope": "source_model"}
        for row in _as_list(model.get("diagnostics"))
        if isinstance(row, dict) and row.get("diagnostic_id")
    ]
    projection_diagnostics = [
        {**_safe_diagnostic(row), "catalog_scope": "review_projection"}
        for row in _as_list(projection.get("diagnostics"))
    ]
    diagnostic_catalog = _dedupe([*source_diagnostic_catalog, *projection_diagnostics])
    diagnostics_by_id = {
        str(row.get("diagnostic_id")): row
        for row in diagnostic_catalog
        if row.get("diagnostic_id")
    }
    asset_catalog = [
        _safe_asset(row)
        for row in _as_list(model.get("assets"))
        if isinstance(row, dict) and row.get("entity_key")
    ]
    assets_by_key = {str(row.get("entity_key")): row for row in asset_catalog}
    evidence_catalog = [
        _safe_evidence(row)
        for row in _as_list(model.get("evidence"))
        if isinstance(row, dict) and row.get("evidence_key")
    ]
    lineage_catalog = [
        _safe_lineage(row)
        for row in _as_list(model.get("lineage"))
        if isinstance(row, dict) and row.get("lineage_id")
    ]
    asset_keys_by_question: dict[str, list[str]] = {}
    for relationship in _as_list(model.get("relationships")):
        relationship = _as_dict(relationship)
        if relationship.get("kind") == "uses_asset" and relationship.get(
            "from_entity_key"
        ):
            asset_keys_by_question.setdefault(
                str(relationship["from_entity_key"]), []
            ).append(str(relationship.get("to_entity_key")))

    raw_occurrences = _as_list(projection.get("question_occurrences"))
    occurrences: list[dict[str, Any]] = []
    for index, row in enumerate(raw_occurrences, start=1):
        row = _as_dict(row)
        key = (
            row.get("referenced_question_key")
            or row.get("observed_question_entity_key")
            or _projection_entity_key(row)
        )
        occurrences.append(
            _normalize_occurrence(
                row,
                questions_by_key.get(str(key)),
                index,
                diagnostic_by_id=diagnostics_by_id,
            )
        )
    occurrence_counts: dict[str, int] = {}
    occurrences_by_question: dict[str, list[dict[str, Any]]] = {}
    for row in occurrences:
        key = row.get("question_entity_key")
        if key:
            question_key = str(key)
            occurrence_counts[question_key] = (
                occurrence_counts.get(question_key, 0) + 1
            )
            occurrences_by_question.setdefault(question_key, []).append(row)

    entities = []
    for key in ordered_keys:
        question = questions_by_key.get(str(key), {})
        projected = _as_dict(projected_by_key.get(str(key)))
        diag_ids = _as_list(question.get("diagnostic_ids"))
        evidence_keys = _as_list(question.get("source_evidence_keys"))
        lineage_ids = [
            _as_dict(row).get("lineage_id")
            for row in _as_list(model.get("lineage"))
            if _as_dict(row).get("subject_entity_key") == key
            and _as_dict(row).get("lineage_id")
        ]
        asset_keys = _dedupe(
            asset_key
            for asset_key in asset_keys_by_question.get(str(key), [])
            if asset_key in assets_by_key
        )
        scoring = _as_dict(question.get("scoring"))
        build_support = _as_dict(question.get("build_support"))
        variant_member_keys = (
            _as_list(projected.get("variant_class_member_keys")) or [key]
        )
        variant_members = []
        for member_key_value in variant_member_keys:
            member_key = str(member_key_value)
            member_question = questions_by_key.get(member_key, {})
            member_occurrences = occurrences_by_question.get(member_key, [])
            variant_members.append(
                {
                    "entity_key": member_key,
                    "display_identifier": _display_identifier(member_question),
                    "point_values": _dedupe(
                        occurrence.get("points")
                        for occurrence in member_occurrences
                        if occurrence.get("points") is not None
                    ),
                    "placement_count": len(member_occurrences),
                }
            )
        entities.append(
            {
                "entity_key": key,
                "display_identifier": _display_identifier(question),
                "identity": _safe_identity(question.get("identity")),
                "title": question.get("title") or "Untitled question",
                "kind": question.get("kind") or "unknown",
                "source_kind": question.get("source_kind"),
                "prompt": _formatted_content(question.get("prompt")),
                "responses": _response_view(question.get("type_payload")),
                "scoring": {
                    "state": scoring.get("state"),
                    "mode": scoring.get("mode"),
                    "maximum_points": scoring.get("maximum_points"),
                    "rules": _as_list(scoring.get("rules")),
                    "extensions": {
                        "observed_maximum_points": _as_list(
                            _as_dict(scoring.get("extensions")).get(
                                "observed_maximum_points"
                            )
                        )
                    },
                },
                "feedback": _feedback_view(question.get("feedback")),
                "authored_variant": {
                    "digest": projected.get("authored_variant_digest"),
                    "class_key": projected.get("variant_class_key") or key,
                    "member_keys": variant_member_keys,
                    "members": variant_members,
                    "member_count": len(variant_member_keys),
                    "reused": len(variant_member_keys) > 1,
                    "is_representative": bool(
                        projected.get("is_variant_class_representative", True)
                    ),
                },
                "extraction_fidelity": {
                    "match_status": _match_state(question),
                    "match_label": match_label(_match_state(question)),
                    "occurrence_count": occurrence_counts.get(str(key), 0),
                    "source_evidence_count": len(evidence_keys),
                    "diagnostic_count": len(diag_ids),
                },
                "build_support": {
                    "level": build_support.get("level") or "not_recorded",
                    "level_label": value_label(
                        build_support.get("level") or "not_recorded"
                    ),
                    "receipt_refs": [
                        _local_ref(value)
                        for value in _as_list(build_support.get("receipt_refs"))
                    ],
                    "notes": _as_list(build_support.get("notes")),
                },
                "fidelity": _entity_fidelity(question),
                "diagnostic_ids": _dedupe(diag_ids),
                "asset_keys": asset_keys,
                "source_evidence_keys": _dedupe(evidence_keys),
                "lineage_ids": _dedupe(lineage_ids),
            }
        )

    source = _as_dict(model.get("source"))
    fingerprint = _as_dict(source.get("fingerprint"))
    projection_summary = _as_dict(projection.get("summary"))
    quiz_count = len(_as_list(model.get("quizzes")))
    unique_count = int(projection_summary.get("unique_question_count", len(entities)))
    occurrence_count = int(
        projection_summary.get("question_occurrence_count", len(occurrences))
    )
    queues = _review_queues(model, occurrences, entities, projection_diagnostics)
    actions = _action_queue(model)
    actions.extend(
        {
            "action_key": f"projection:{row.get('diagnostic_id')}",
            "category": "projection",
            "severity": row.get("severity"),
            "code": row.get("code"),
            "message": row.get("message"),
            "entity_keys": _as_list(row.get("entity_keys")),
        }
        for row in projection_diagnostics
        if row.get("severity") in {"warning", "error"}
    )
    actions = _dedupe(actions)
    queue_summary = {
        "no_safe_match_count": len(queues["no_safe_match"]),
        "probable_match_count": len(queues["probable_match"]),
        "settings_review_count": len(queues["settings_review"]),
        "asset_review_count": len(queues["asset_review"]),
        "unsupported_kind_count": len(queues["unsupported_kinds"]),
        "finding_count": len(queues["findings"]),
    }
    return {
        "format": FORMAT,
        "share_safety": {
            "classification": SHARE_CLASSIFICATION,
            "share_safe": False,
            "reason": "Contains authored prompts, response keys, feedback, and course provenance.",
            "label": "Local course evidence — not share-safe by default",
        },
        "boundary": {
            "input_schema": "coursecraft.quiz/1",
            "reads_normalized_model_only": True,
            "parses_xml": False,
            "uses_network": False,
            "uploads_content": False,
            "contacts_brightspace": False,
            "makes_build_claims": False,
            "html_renders_authored_html_as_text": True,
        },
        "model": {
            "model_id": model.get("model_id"),
            "run_id": model.get("run_id"),
            "source_key": source.get("source_key"),
            "source_lineage_key": source.get("source_lineage_key"),
            "source_kind": source.get("source_kind"),
            "source_fingerprint": {
                "algorithm": fingerprint.get("algorithm"),
                "digest": fingerprint.get("digest"),
                "scope": fingerprint.get("scope"),
            },
            "source_references": [
                _local_ref(value) for value in _as_list(source.get("references"))
            ],
        },
        "summary": {
            "quiz_count": quiz_count,
            "unique_question_count": unique_count,
            "authored_variant_class_count": int(
                projection_summary.get("authored_variant_class_count", unique_count)
            ),
            "reused_authored_variant_class_count": int(
                projection_summary.get("reused_authored_variant_class_count", 0)
            ),
            "occurrence_count": occurrence_count,
            "action_needed_count": sum(queue_summary.values()),
            "diagnostic_count": len(_as_list(model.get("diagnostics"))),
        },
        "question_entities": entities,
        "question_occurrences": occurrences,
        "catalogs": {
            "assets": asset_catalog,
            "evidence": evidence_catalog,
            "diagnostics": diagnostic_catalog,
            "lineage": lineage_catalog,
        },
        "queue_summary": queue_summary,
        "review_queues": queues,
        "action_queue": actions,
        "projection_diagnostics": projection_diagnostics,
        "artifacts": _safe_artifact_links(artifact_links, output_dir=output_dir),
    }


def _formatted_html(value: Any, *, label: str) -> str:
    row = _as_dict(value)
    if not row:
        return '<p class="empty">Not recorded.</p>'
    content = row.get("content") or ""
    return (
        f'<div class="authored" aria-label="{_e(label)}">'
        f'<div class="content-label">{_e(label)} · {_e(row.get("format") or "unknown format")}</div>'
        f'<div class="content-text">{_e(content)}</div></div>'
    )


def _answer_meta(row: Mapping[str, Any]) -> str:
    parts = []
    if row.get("case_sensitive") is True:
        parts.append("case-sensitive")
    elif row.get("case_sensitive") is False:
        parts.append("case-insensitive")
    else:
        parts.append("case handling not recorded")
    if row.get("weight") is not None:
        parts.append(f"weight {row.get('weight')}")
    return "; ".join(parts)


def _entity_fidelity(question: Mapping[str, Any]) -> dict[str, Any]:
    """Flag an entity whose folded rows describe different authored questions."""
    from quiz_normalization import authored_variant_row_digest

    digests = {
        authored_variant_row_digest(record["payload"])
        for record in _as_list(_as_dict(question.get("type_payload")).get("raw_response_models"))
        if str(_as_dict(record).get("source_kind", "")).startswith(
            "canonical-extractor-row:"
        )
        and isinstance(_as_dict(record).get("payload"), dict)
    }
    return {
        "authored_variant_count": len(digests),
        "variant_conflict": len(digests) > 1,
    }


def _point_text(value: Any) -> str:
    text = str(value).strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    return format(number.normalize(), "f")


def _points_display(scoring: Mapping[str, Any]) -> str:
    observed = _dedupe(
        _point_text(value)
        for value in _as_list(
            _as_dict(scoring.get("extensions")).get("observed_maximum_points")
        )
    )
    if len(observed) > 1:
        return " / ".join(observed) + " (varies by placement)"
    value = scoring.get("maximum_points")
    return _point_text(value) if value is not None else "not recorded"


def _placement_points_note(scoring: Mapping[str, Any]) -> str:
    observed = _dedupe(
        _point_text(value)
        for value in _as_list(
            _as_dict(scoring.get("extensions")).get("observed_maximum_points")
        )
    )
    if len(observed) < 2:
        return ""
    return (
        '<p class="match-explanation"><strong>Points differ by placement.</strong> '
        "In Brightspace the weight belongs to the quiz item, so this question is "
        "worth a different amount in different quizzes. Each placement above shows "
        "its own value.</p>"
    )


def _variant_conflict_note(entity: Mapping[str, Any]) -> str:
    """Refuse to present one authoritative answer key for a collapsed entity."""
    if not _as_dict(entity.get("fidelity")).get("variant_conflict"):
        return ""
    count = _as_dict(entity.get("fidelity")).get("authored_variant_count")
    return (
        '<p class="match-difference"><strong>Do not approve this answer key.</strong> '
        f"This record holds {_e(count)} materially different authored questions that "
        "were folded together, so the key shown below belongs to only one of them. "
        "Re-extract with corrected question identity before approving anything.</p>"
    )


def _responses_html(entity: Mapping[str, Any]) -> str:
    response = _as_dict(entity.get("responses"))
    sections: list[str] = []
    options = _as_list(response.get("options"))
    if options:
        items = []
        for option in options:
            option = _as_dict(option)
            correct = option.get("correct")
            state = (
                "Keyed response"
                if correct is True
                else "Not keyed" if correct is False else "Key state unknown"
            )
            weight = (
                f" · weight {_e(option.get('weight'))}"
                if option.get("weight") is not None
                else ""
            )
            items.append(
                '<li><div class="response-heading">'
                f'<code>{_e(option.get("option_key"))}</code> · <strong>{_e(state)}</strong>{weight}</div>'
                f'{_formatted_html(option.get("content"), label="Authored option")}</li>'
            )
        sections.append(
            '<section class="response-group"><h4>Options and answer key</h4><ol class="response-list">'
            + "".join(items)
            + "</ol></section>"
        )
    accepted = _as_list(response.get("accepted_responses"))
    if accepted:
        items = "".join(
            f'<li><span class="answer-value">{_e(_as_dict(row).get("value"))}</span><span>{_e(_answer_meta(_as_dict(row)))}</span></li>'
            for row in accepted
        )
        sections.append(
            '<section class="response-group"><h4>Accepted responses</h4><ul class="answer-list">'
            + items
            + "</ul></section>"
        )
    blanks = _as_list(response.get("blanks"))
    if blanks:
        items = []
        for blank in blanks:
            blank = _as_dict(blank)
            answers = "".join(
                f'<li><span class="answer-value">{_e(_as_dict(row).get("value"))}</span><span>{_e(_answer_meta(_as_dict(row)))}</span></li>'
                for row in _as_list(blank.get("accepted_responses"))
            )
            items.append(
                f'<li><strong>{_e(blank.get("blank_key"))}</strong><ul class="answer-list">{answers or "<li>Accepted responses not recorded.</li>"}</ul></li>'
            )
        sections.append(
            '<section class="response-group"><h4>Blanks and accepted responses</h4><ol>'
            + "".join(items)
            + "</ol></section>"
        )
    pairs = _as_list(response.get("match_pairs"))
    if pairs:
        rows = "".join(
            "<tr>"
            f'<td><code>{_e(_as_dict(row).get("left_key"))}</code></td>'
            f'<td><code>{_e(_as_dict(row).get("right_key"))}</code></td>'
            f'<td>{_e(_as_dict(row).get("weight"))}</td></tr>'
            for row in pairs
        )
        sections.append(
            '<section class="response-group"><h4>Matching answer key</h4><div class="table-wrap" tabindex="0"><table><thead><tr><th>Left</th><th>Right</th><th>Weight</th></tr></thead><tbody>'
            + rows
            + "</tbody></table></div></section>"
        )
    order = sorted(
        [_as_dict(row) for row in _as_list(response.get("correct_order"))],
        key=lambda row: (row.get("position") is None, row.get("position") or 0),
    )
    if order:
        items = "".join(
            f'<li value="{_e(row.get("position") or index)}"><code>{_e(row.get("option_key"))}</code></li>'
            for index, row in enumerate(order, start=1)
        )
        sections.append(
            '<section class="response-group"><h4>Correct order</h4><ol>'
            + items
            + "</ol></section>"
        )
    manual = response.get("manual_answer_key")
    if manual:
        sections.append(
            '<section class="response-group"><h4>Manual answer key / evaluator guidance</h4>'
            + _formatted_html(manual, label="Authored answer key")
            + "</section>"
        )
    raw = _as_list(response.get("raw_response_models"))
    if raw:
        items = "".join(
            "<li><strong>"
            + _e(_as_dict(row).get("source_kind"))
            + '</strong><pre class="code-block">'
            + _e(
                json.dumps(
                    _as_dict(row).get("payload"),
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            + "</pre></li>"
            for row in raw
        )
        sections.append(
            '<details class="source-facts"><summary>Source response facts</summary><ul class="plain-list">'
            + items
            + "</ul></details>"
        )
    return (
        "".join(sections)
        or '<p class="empty">No typed response projection was recorded.</p>'
    )


def _feedback_html(entity: Mapping[str, Any]) -> str:
    rows = _as_list(entity.get("feedback"))
    if not rows:
        return '<p class="empty">No feedback was recorded.</p>'
    return (
        '<div class="feedback-list">'
        + "".join(
            '<article><div class="content-label">'
            + _e(_as_dict(row).get("channel") or "feedback")
            + " · "
            + _e(_as_dict(row).get("source_kind") or "source kind not recorded")
            + "</div>"
            + _formatted_html(_as_dict(row).get("content"), label="Authored feedback")
            + "</article>"
            for row in rows
        )
        + "</div>"
    )


def _diagnostics_html(rows: Any) -> str:
    diagnostics = _as_list(rows)
    if not diagnostics:
        return '<p class="empty">No question-scoped diagnostics.</p>'
    return (
        '<ul class="diagnostic-list">'
        + "".join(
            '<li><span class="severity severity-'
            + _e(_as_dict(row).get("severity") or "unknown")
            + '">'
            + _e(_as_dict(row).get("severity") or "unknown")
            + "</span><div><strong>"
            + _e(_as_dict(row).get("code"))
            + "</strong><br>"
            + _e(_as_dict(row).get("message"))
            + "</div></li>"
            for row in diagnostics
        )
        + "</ul>"
    )


def _assets_html(rows: Any) -> str:
    assets = _as_list(rows)
    if not assets:
        return '<p class="empty">No linked asset records.</p>'
    return (
        '<ul class="plain-list">'
        + "".join(
            "<li><code>"
            + _e(
                _as_dict(row).get("source_path")
                or _as_dict(row).get("package_path")
                or _as_dict(row).get("entity_key")
            )
            + "</code> · "
            + _e(_as_dict(row).get("media_type") or "media type unknown")
            + " · <strong>"
            + _e(_as_dict(row).get("status") or "status unknown")
            + "</strong></li>"
            for row in assets
        )
        + "</ul>"
    )


def _provenance_html(entity: Mapping[str, Any]) -> str:
    provenance = _as_dict(entity.get("provenance"))
    evidence = _as_list(provenance.get("source_evidence"))
    lineage = _as_list(provenance.get("lineage"))
    evidence_html = (
        "".join(
            "<li><code>"
            + _e(_as_dict(row).get("evidence_key"))
            + "</code><br>"
            + _e(_as_dict(row).get("source_ref") or "source reference not recorded")
            + (
                " · " + _e(_as_dict(row).get("locator"))
                if _as_dict(row).get("locator")
                else ""
            )
            + "</li>"
            for row in evidence
        )
        or "<li>No source evidence rows were joined.</li>"
    )
    lineage_html = (
        "".join(
            "<li><code>"
            + _e(_as_dict(row).get("lineage_id"))
            + "</code> · "
            + _e(_as_dict(row).get("stage"))
            + " / "
            + _e(_as_dict(row).get("status"))
            + " · "
            + _e(_as_dict(row).get("method"))
            + "</li>"
            for row in lineage
        )
        or "<li>No lineage rows were joined.</li>"
    )
    return (
        '<details class="provenance"><summary>Provenance details</summary>'
        '<div class="two-col"><div><h5>Source evidence</h5><ul>'
        + evidence_html
        + "</ul></div><div><h5>Lineage</h5><ul>"
        + lineage_html
        + "</ul></div></div></details>"
    )


def _reference_links_html(
    keys: Any,
    hrefs: Mapping[str, str],
    *,
    empty: str,
) -> str:
    values = [str(value) for value in _as_list(keys) if value]
    if not values:
        return f'<p class="empty">{_e(empty)}</p>'
    return (
        '<ul class="plain-list reference-list">'
        + "".join(
            (
                '<li><a href="'
                + _e(hrefs.get(value, ""))
                + '"><code>'
                + _e(value)
                + "</code></a></li>"
                if hrefs.get(value)
                else "<li><code>" + _e(value) + "</code></li>"
            )
            for value in values
        )
        + "</ul>"
    )


def _authored_variant_html(
    entity: Mapping[str, Any],
    question_hrefs: Mapping[str, str] | None = None,
) -> str:
    """Explain exact authored reuse while keeping every source item visible."""
    variant = _as_dict(entity.get("authored_variant"))
    if not variant.get("reused"):
        return ""
    member_count = int(variant.get("member_count") or 0)
    members = [_as_dict(row) for row in _as_list(variant.get("members"))]
    hrefs = question_hrefs or {}
    member_items = []
    point_signatures: set[tuple[str, ...]] = set()
    for member in members:
        member_key = str(member.get("entity_key") or "")
        display_identifier = member.get("display_identifier") or member_key
        href = hrefs.get(member_key)
        label = (
            f'<a href="{_e(href)}"><code>{_e(display_identifier)}</code></a>'
            if href
            else f"<code>{_e(display_identifier)}</code>"
        )
        # A placement whose item declares no weight of its own yields an empty
        # value rather than a missing one.  Both must read as "not recorded"
        # instead of rendering an empty field.
        normalized_points = _dedupe(
            _point_text(value)
            for value in _as_list(member.get("point_values"))
            if str(value).strip()
        )
        if normalized_points:
            point_text = " / ".join(normalized_points)
            point_signatures.add(tuple(normalized_points))
        else:
            point_text = "not recorded"
        member_items.append(
            f"<li>{label} · <strong>Placement points:</strong> {_e(point_text)}"
            f" · {_e(member.get('placement_count') or 0)} placement(s)</li>"
        )
    if not member_items:
        member_items.append("<li>No authored-variant members were recorded.</li>")
    points_note = ""
    if len(point_signatures) > 1:
        points_note = (
            '<p class="match-difference"><strong>Placement points differ across '
            "these source records.</strong> Brightspace scoring belongs to each quiz "
            "item and is not part of authored-content equivalence.</p>"
        )
    role = (
        "Stable display representative for this reuse class."
        if variant.get("is_representative")
        else "Member of the reuse class shown by the stable representative."
    )
    return (
        '<section class="question-section reuse-panel"><h4>Authored reuse</h4>'
        f"<p><strong>Same authored content across {_e(member_count)} source "
        "question records.</strong> Each record remains separate so its Brightspace "
        "identity, placements, points, and evidence stay inspectable.</p>"
        f"{points_note}<p class=\"scope-note\">{_e(role)}</p>"
        f"<ul class=\"plain-list reference-list\">{''.join(member_items)}</ul></section>"
    )


def _question_card(
    entity: Mapping[str, Any],
    *,
    reference_hrefs: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    scoring = _as_dict(entity.get("scoring"))
    fidelity = _as_dict(entity.get("extraction_fidelity"))
    support = _as_dict(entity.get("build_support"))
    notes = _as_list(support.get("notes"))
    receipt_refs = _as_list(support.get("receipt_refs"))
    support_detail = "".join(f"<li>{_e(value)}</li>" for value in notes)
    support_detail += "".join(
        f"<li>Receipt reference: <code>{_e(value)}</code></li>"
        for value in receipt_refs
        if value
    )
    if not support_detail:
        support_detail = "<li>No support notes or receipt references recorded.</li>"
    if reference_hrefs is None:
        diagnostic_html = _diagnostics_html(entity.get("diagnostics"))
        asset_html = _assets_html(entity.get("assets"))
        provenance_html = _provenance_html(entity)
        authored_variant_html = _authored_variant_html(entity)
    else:
        diagnostic_html = _reference_links_html(
            entity.get("diagnostic_ids"),
            reference_hrefs.get("diagnostics", {}),
            empty="No question-scoped diagnostics.",
        )
        asset_html = _reference_links_html(
            entity.get("asset_keys"),
            reference_hrefs.get("assets", {}),
            empty="No linked asset records.",
        )
        evidence_html = _reference_links_html(
            entity.get("source_evidence_keys"),
            reference_hrefs.get("evidence", {}),
            empty="No source evidence rows were joined.",
        )
        lineage_html = _reference_links_html(
            entity.get("lineage_ids"),
            reference_hrefs.get("lineage", {}),
            empty="No lineage rows were joined.",
        )
        provenance_html = (
            '<details class="provenance"><summary>Provenance references</summary>'
            '<div class="two-col"><div><h5>Source evidence</h5>'
            + evidence_html
            + "</div><div><h5>Lineage</h5>"
            + lineage_html
            + "</div></div></details>"
        )
        authored_variant_html = _authored_variant_html(
            entity, reference_hrefs.get("questions", {})
        )
    evidence_count = fidelity.get("source_evidence_count")
    if evidence_count is None:
        evidence_count = len(_as_list(fidelity.get("source_evidence_keys")))
    diagnostic_count = fidelity.get("diagnostic_count")
    if diagnostic_count is None:
        diagnostic_count = len(_as_list(fidelity.get("diagnostic_ids")))
    return f"""<article class="question-card" id="question-{_e(entity.get('entity_key'))}">
<div class="question-heading"><div><p class="kicker">{_e(entity.get('kind'))} · <code>{_e(entity.get('display_identifier'))}</code></p><h3>{_e(_canonical_question_title(entity))}</h3></div><span class="count-pill">{_e(fidelity.get('occurrence_count'))} occurrence(s)</span></div>
<section class="question-section"><h4>Authored prompt</h4>{_formatted_html(entity.get('prompt'), label='Authored local course evidence')}</section>
{authored_variant_html}
<section class="question-section sensitive"><h4>Responses and answer evidence</h4><p class="scope-note">Local course evidence. This section may reveal answer keys.</p>{_variant_conflict_note(entity)}{_responses_html(entity)}</section>
<section class="question-section"><h4>Scoring</h4><dl class="inline-dl"><div><dt>State</dt><dd>{_e(scoring.get('state') or 'not recorded')}</dd></div><div><dt>Mode</dt><dd>{_e(scoring.get('mode') or 'not recorded')}</dd></div><div><dt>Maximum points</dt><dd>{_e(_points_display(scoring))}</dd></div></dl>{_placement_points_note(scoring)}</section>
<section class="question-section"><h4>Feedback</h4>{_feedback_html(entity)}</section>
<div class="separated-states"><section class="fidelity-panel"><h4>Extraction fidelity</h4><p><strong>Library match:</strong> {_e(fidelity.get('match_label'))}</p><p><strong>Match code:</strong> <code>{_e(fidelity.get('match_status'))}</code></p><p><strong>Evidence rows:</strong> {_e(evidence_count)} · <strong>Diagnostics:</strong> {_e(diagnostic_count)}</p><p class="scope-note">This describes what was preserved or resolved from the source.</p></section><section class="support-panel"><h4>Build support — separate claim</h4><p><strong>Level:</strong> {_e(support.get('level_label'))}</p><p><strong>Build-support code:</strong> <code>{_e(support.get('level'))}</code></p><ul>{support_detail}</ul><p class="scope-note">Extraction does not make this question upload-ready or buildable.</p></section></div>
<section class="question-section"><h4>Diagnostics</h4>{diagnostic_html}</section>
<section class="question-section"><h4>Linked assets</h4>{asset_html}<p class="scope-note">References are shown as evidence; this page does not load external assets.</p></section>
{provenance_html}
</article>"""


def _filter_options(values: Iterable[Any]) -> str:
    rows = sorted({str(value) for value in values if value not in {None, ""}})
    return "".join(
        f'<option value="{_e(value.lower())}">{_e(value)}</option>' for value in rows
    )


def _occurrence_card(
    row: Mapping[str, Any],
    *,
    question: Mapping[str, Any] | None = None,
    question_href: str | None = None,
) -> str:
    question = question or {}
    display_identifier = question.get("display_identifier") or row.get(
        "question_entity_key"
    )
    title = _placement_question_title(question, row)
    kind = question.get("kind") or "question reference"
    search_text = " ".join(
        str(value)
        for value in [
            display_identifier,
            title,
            kind,
            row.get("quiz_title"),
            row.get("section_title"),
            row.get("pool_title"),
            row.get("source_kind"),
            row.get("source_status"),
            row.get("match_status"),
            row.get("match_status_code"),
            row.get("occurrence_match_status"),
            row.get("entity_match_status"),
            " ".join(str(value) for value in _as_list(row.get("diagnostic_ids"))),
        ]
        if value is not None
    ).lower()
    draw = ""
    if row.get("pool_title") or row.get("selection_mode") not in {None, "not_recorded"}:
        draw = (
            "<p><strong>Pool / draw:</strong> "
            + _e(row.get("pool_title") or "Pool title not recorded")
            + " · "
            + _e(row.get("selection_mode") or "mode not recorded")
            + " · draw "
            + _e(
                row.get("draw_count")
                if row.get("draw_count") is not None
                else "not recorded"
            )
            + " from "
            + _e(
                row.get("candidate_pool_size")
                if row.get("candidate_pool_size") is not None
                else "not recorded"
            )
            + "</p>"
        )
    diagnostics = _as_list(row.get("diagnostic_ids"))
    diagnostic_text = (
        ", ".join(str(value) for value in diagnostics)
        if diagnostics
        else "None recorded"
    )
    explanation = ""
    if row.get("match_explanation"):
        explanation = (
            '<p class="match-explanation"><strong>Why review may be needed:</strong> '
            + _e(row.get("match_explanation"))
            + "</p>"
        )
    candidates = _as_list(row.get("match_candidates"))
    candidate_html = ""
    if candidates:
        candidate_html = (
            "<p><strong>Library candidates:</strong> "
            + _e(" | ".join(str(value) for value in candidates))
            + "</p>"
        )
    match_detail = (
        "<details><summary>Library-match evidence and machine codes</summary>"
        "<p><strong>Occurrence match:</strong> "
        + _e(row.get("occurrence_match_status"))
        + " · <code>"
        + _e(row.get("occurrence_match_status_code"))
        + "</code></p><p><strong>Question match:</strong> "
        + _e(row.get("entity_match_status"))
        + " · <code>"
        + _e(row.get("entity_match_status_code"))
        + "</code></p>"
        + candidate_html
        + "</details>"
    )
    occurrence_match_code = row.get("occurrence_match_status_code")
    entity_match_code = row.get("entity_match_status_code")
    if (
        occurrence_match_code != entity_match_code
        and occurrence_match_code not in {None, "", "not_recorded"}
        and entity_match_code not in {None, "", "not_recorded"}
    ):
        match_detail = (
            '<p class="match-difference"><strong>Two evidence levels are present:</strong> occurrence '
            + _e(row.get("occurrence_match_status"))
            + "; question "
            + _e(row.get("entity_match_status"))
            + ". Review the evidence detail below.</p>"
            + match_detail
        )
    question_key = row.get("question_entity_key")
    if question_href is None and question_key:
        question_href = f"#question-{question_key}"
    detail_link = (
        f'<a class="detail-link" href="{_e(question_href)}">Question details</a>'
        if question_key and question_href
        else '<span class="detail-link">Question reference unresolved</span>'
    )
    memberships = _as_list(row.get("pool_memberships"))
    membership_html = ""
    if memberships:
        membership_html = (
            "<h4>Pool memberships</h4><ul>"
            + "".join(
                "<li>"
                + _e(
                    _as_dict(value).get("pool_title")
                    or _as_dict(value).get("pool_path")
                    or _as_dict(value).get("pool_key")
                    or "Unresolved pool"
                )
                + " · "
                + _e(match_label(_as_dict(value).get("match_status")))
                + " (<code>"
                + _e(_as_dict(value).get("match_status") or "not_recorded")
                + "</code>)"
                + " · "
                + _e(
                    _as_dict(value).get("relationship_status")
                    or "relationship status not recorded"
                )
                + "</li>"
                for value in memberships
            )
            + "</ul>"
        )
    return f"""<article class="occurrence-card" id="occurrence-{_e(row.get('occurrence_key'))}" data-search="{_e(search_text)}" data-quiz="{_e(str(row.get('quiz_title') or '').lower())}" data-section="{_e(row.get('_section_filter_key') or str(row.get('section_title') or '').lower())}" data-kind="{_e(str(kind).lower())}" data-source="{_e(str(row.get('source_status') or '').lower())}" data-match="{_e(str(row.get('match_status') or '').lower())}" data-pool="{_e(str(row.get('pool_title') or '').lower())}" data-review="{'yes' if _occurrence_needs_source_review(row) else 'no'}">
<div class="occurrence-main"><div><p class="kicker">Quiz {_e(row.get('quiz_ordinal') if row.get('quiz_ordinal') is not None else '—')} · section {_e(row.get('section_ordinal') if row.get('section_ordinal') is not None else '—')} · question {_e(row.get('question_ordinal') if row.get('question_ordinal') is not None else '—')}</p><h3>{_e(title)}</h3><p><code>{_e(display_identifier)}</code> · {_e(kind)}</p></div>{detail_link}</div>
<div class="context-grid"><p><strong>Quiz:</strong> {_e(row.get('quiz_title'))}</p><p><strong>Section:</strong> {_e(row.get('_section_display_title') or row.get('section_title'))}</p><p><strong>Points here:</strong> {_e(row.get('points') if row.get('points') is not None else 'not recorded')}</p><p><strong>Placement:</strong> {_e(value_label(row.get('source_kind')))} · {_e(row.get('source_status_label'))}</p><p><strong>Library match:</strong> {_e(row.get('match_status'))}</p></div>{explanation}{match_detail}{draw}
<details><summary>Occurrence evidence</summary><p><strong>Occurrence key:</strong> <code>{_e(row.get('occurrence_key'))}</code></p><p><strong>Question entity reference:</strong> <code>{_e(question_key or 'unresolved')}</code></p><p><strong>Points:</strong> {_e(row.get('points') if row.get('points') is not None else 'not recorded')}</p><p><strong>Diagnostics:</strong> {_e(diagnostic_text)}</p><p><strong>Source evidence keys:</strong> {_e(', '.join(str(value) for value in _as_list(row.get('source_evidence_keys'))) or 'None recorded')}</p>{membership_html}</details>
</article>"""


def _action_queue_html(rows: Any) -> str:
    actions = _as_list(rows)
    if not actions:
        return '<p class="empty">No unresolved or action-needed records were projected.</p>'
    return (
        '<ul class="action-list">'
        + "".join(
            '<li><span class="severity severity-'
            + _e(_as_dict(row).get("severity") or "unknown")
            + '">'
            + _e(_as_dict(row).get("category") or "review")
            + "</span><div><strong>"
            + _e(_as_dict(row).get("code"))
            + "</strong><br>"
            + _e(_as_dict(row).get("message"))
            + "<br><code>"
            + _e(
                ", ".join(
                    str(value) for value in _as_list(_as_dict(row).get("entity_keys"))
                )
            )
            + "</code></div></li>"
            for row in actions
        )
        + "</ul>"
    )


def _queue_lane_html(title: str, description: str, rows: Any, *, empty: str) -> str:
    items = _as_list(rows)
    if not items:
        content = f'<p class="empty">{_e(empty)}</p>'
    else:
        rendered: list[str] = []
        for value in items:
            row = _as_dict(value)
            occurrence_key = row.get("occurrence_key")
            question_key = row.get("question_entity_key")
            href = (
                f"#occurrence-{_e(occurrence_key)}"
                if occurrence_key
                else f"#question-{_e(question_key)}" if question_key else ""
            )
            heading = (
                row.get("display_identifier")
                or row.get("code")
                or row.get("diagnostic_id")
                or row.get("message")
                or row.get("item_key")
                or "Review item"
            )
            link = (
                f'<a href="{href}">{_e(heading)}</a>'
                if href
                else f"<strong>{_e(heading)}</strong>"
            )
            message = (
                row.get("explanation") or row.get("message") or "Review this item."
            )
            candidates = _as_list(row.get("candidates"))
            candidate_text = (
                "<br><strong>Candidates:</strong> "
                + _e(" | ".join(str(candidate) for candidate in candidates))
                if candidates
                else ""
            )
            code = row.get("match_code") or row.get("code") or row.get("level")
            code_text = f" <code>{_e(code)}</code>" if code else ""
            rendered.append(
                f"<li><div>{link}{code_text}<br>{_e(message)}{candidate_text}</div></li>"
            )
        content = '<ul class="queue-list">' + "".join(rendered) + "</ul>"
    return (
        '<article class="queue-lane"><h3>'
        + _e(title)
        + "</h3><p>"
        + _e(description)
        + "</p>"
        + content
        + "</article>"
    )


def render_quiz_review_station_html(data: Mapping[str, Any]) -> str:
    summary = _as_dict(data.get("summary"))
    model = _as_dict(data.get("model"))
    occurrences = [_as_dict(row) for row in _as_list(data.get("question_occurrences"))]
    entities = [_as_dict(row) for row in _as_list(data.get("question_entities"))]
    artifacts = _as_list(data.get("artifacts"))
    catalogs = _as_dict(data.get("catalogs"))
    assets_by_key = {
        str(_as_dict(row).get("entity_key")): _as_dict(row)
        for row in _as_list(catalogs.get("assets"))
        if _as_dict(row).get("entity_key")
    }
    evidence_by_key = {
        str(_as_dict(row).get("evidence_key")): _as_dict(row)
        for row in _as_list(catalogs.get("evidence"))
        if _as_dict(row).get("evidence_key")
    }
    diagnostics_by_id = {
        str(_as_dict(row).get("diagnostic_id")): _as_dict(row)
        for row in _as_list(catalogs.get("diagnostics"))
        if _as_dict(row).get("diagnostic_id")
    }
    lineage_by_id = {
        str(_as_dict(row).get("lineage_id")): _as_dict(row)
        for row in _as_list(catalogs.get("lineage"))
        if _as_dict(row).get("lineage_id")
    }
    entity_by_key = {
        str(row.get("entity_key")): row for row in entities if row.get("entity_key")
    }
    artifact_html = (
        "".join(
            f'<li><a href="{_e(_as_dict(row).get("href"))}">{_e(_as_dict(row).get("label"))}</a><br><code>{_e(_as_dict(row).get("href"))}</code></li>'
            for row in artifacts
        )
        or "<li>No artifact links were supplied.</li>"
    )
    occurrence_html = "".join(
        _occurrence_card(
            row,
            question=entity_by_key.get(str(row.get("question_entity_key"))),
        )
        for row in occurrences
    )
    hydrated_entities: list[dict[str, Any]] = []
    for row in entities:
        hydrated = dict(row)
        hydrated["diagnostics"] = [
            diagnostics_by_id[key]
            for key in _as_list(row.get("diagnostic_ids"))
            if key in diagnostics_by_id
        ]
        hydrated["assets"] = [
            assets_by_key[key]
            for key in _as_list(row.get("asset_keys"))
            if key in assets_by_key
        ]
        hydrated["provenance"] = {
            "source_evidence": [
                evidence_by_key[key]
                for key in _as_list(row.get("source_evidence_keys"))
                if key in evidence_by_key
            ],
            "lineage": [
                lineage_by_id[key]
                for key in _as_list(row.get("lineage_ids"))
                if key in lineage_by_id
            ],
        }
        hydrated_entities.append(hydrated)
    question_html = "".join(_question_card(row) for row in hydrated_entities)
    queues = _as_dict(data.get("review_queues"))
    queue_html = "".join(
        [
            _queue_lane_html(
                LABELS["no_safe_match"],
                "Questions kept visible because no defensible library location was resolved.",
                queues.get("no_safe_match"),
                empty="No questions are waiting in this lane.",
            ),
            _queue_lane_html(
                LABELS["probable_match"],
                "Strong or ambiguous content matches that still require a person to confirm or decline.",
                queues.get("probable_match"),
                empty="No probable or ambiguous matches are waiting.",
            ),
            _queue_lane_html(
                LABELS["settings_review"],
                "Quiz settings whose source state is absent, unknown, unresolved, or unsupported.",
                queues.get("settings_review"),
                empty="No settings need a decision.",
            ),
            _queue_lane_html(
                LABELS["asset_review"],
                "Image and media references that were not fully resolved in the local evidence set.",
                queues.get("asset_review"),
                empty="No assets need a decision.",
            ),
            _queue_lane_html(
                "Review only — cannot be rebuilt yet",
                "Question kinds or instances outside the current admitted build route.",
                queues.get("unsupported_kinds"),
                empty="No extraction-only question kinds were recorded.",
            ),
            _queue_lane_html(
                "Extraction and projection findings",
                "Open source or projection findings. These do not turn a completed Unbind into a failed extraction.",
                queues.get("findings"),
                empty="No open warning or error findings were recorded.",
            ),
        ]
    )
    quiz_options = _filter_options(row.get("quiz_title") for row in occurrences)
    section_options = _filter_options(row.get("section_title") for row in occurrences)
    kind_options = _filter_options(
        entity_by_key.get(str(row.get("question_entity_key")), {}).get("kind")
        for row in occurrences
    )
    source_options = _filter_options(row.get("source_status") for row in occurrences)
    match_options = _filter_options(row.get("match_status") for row in occurrences)
    pool_options = _filter_options(row.get("pool_title") for row in occurrences)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; font-src 'none'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<title>Quiz Binder — Reading Room</title>
<style>
:root{{--ink:#17212b;--muted:#52616f;--paper:#f6f4ef;--card:#fff;--line:#c8d0d7;--accent:#185a78;--focus:#b74900;--soft:#eaf3f6;--warn:#8b4b08;--danger:#942c2c;--good:#356644;--key:#fff2c8}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:var(--accent)}}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,.table-wrap:focus-visible{{outline:3px solid var(--focus);outline-offset:3px}}code,.content-text,.answer-value{{overflow-wrap:anywhere;word-break:break-word}}button,input,select{{font:inherit}}.skip{{position:absolute;top:-8rem;left:1rem;background:#fff;padding:.75rem;z-index:10}}.skip:focus{{top:1rem}}
header,main,footer{{width:min(1240px,calc(100% - 2rem));margin-inline:auto}}header{{padding:2rem 0 1rem}}h1{{font-size:clamp(2.1rem,6vw,4.5rem);line-height:.98;margin:.35rem 0 1rem;max-width:12ch}}h2{{font-size:clamp(1.5rem,3vw,2.2rem);margin-top:0}}h3{{margin:.2rem 0}}h4{{margin:.2rem 0 .6rem}}h5{{font-size:1rem;margin-bottom:.25rem}}.lede{{max-width:76ch;color:var(--muted)}}.evidence-banner{{border:2px solid var(--warn);background:#fff8e7;padding:.75rem 1rem;font-weight:750;display:inline-block}}.eyebrow,.kicker,.content-label{{font-weight:750;text-transform:uppercase;letter-spacing:.07em;color:var(--accent);font-size:.78rem}}nav ul,.plain-list{{list-style:none;padding:0}}nav ul{{display:flex;flex-wrap:wrap;gap:.5rem}}nav a,button{{display:inline-block;border:1px solid var(--line);background:#fff;border-radius:.35rem;padding:.55rem .75rem;text-decoration:none}}
.panel,.question-card,.occurrence-card{{background:var(--card);border:1px solid var(--line);border-radius:.65rem;padding:1rem;box-shadow:0 2px 8px #17212b12}}.panel{{margin:1rem 0 1.5rem}}.metrics,.context-grid,.two-col,.separated-states{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:1rem}}.metric{{border-left:4px solid var(--accent);padding-left:.8rem}}.metric strong{{display:block;font-size:2rem;line-height:1.1}}.scope-note,.empty,.preview{{color:var(--muted)}}.artifact-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.75rem;list-style:none;padding:0}}.artifact-list li{{border:1px solid var(--line);padding:.75rem}}.queue-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:1rem}}.queue-lane{{border:1px solid var(--line);padding:1rem;background:#fff}}.queue-list{{list-style:none;padding:0;display:grid;gap:.7rem}}.queue-list li{{border-top:1px solid var(--line);padding-top:.65rem}}.match-explanation,.match-difference{{border-left:4px solid var(--warn);background:#fff8e7;padding:.65rem}}
.filters{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.75rem;padding:1rem;background:var(--soft);border:1px solid var(--line);margin-bottom:1rem}}.filter-field label{{display:block;font-weight:700;margin-bottom:.25rem}}.filter-field input,.filter-field select{{width:100%;padding:.5rem;border:1px solid #7c8993;background:#fff}}.filter-actions{{display:flex;align-items:end}}.result-status{{font-weight:700}}
.occurrence-list,.question-list{{display:grid;gap:1rem}}.occurrence-main,.question-heading{{display:flex;justify-content:space-between;gap:1rem;align-items:start}}.detail-link,.count-pill{{white-space:nowrap;border:1px solid var(--line);padding:.35rem .55rem;border-radius:999px}}.context-grid{{gap:.25rem 1rem}}.context-grid p{{margin:.25rem 0}}.preview{{border-left:3px solid var(--line);padding-left:.75rem}}details{{margin-top:.75rem}}summary{{cursor:pointer;font-weight:700}}.question-card{{scroll-margin-top:1rem}}.question-section{{margin:1rem 0;padding-top:.75rem;border-top:1px solid var(--line)}}.sensitive{{background:var(--key);padding:.8rem;border:1px solid #dbc878}}.authored{{border-left:4px solid var(--accent);background:#f9fbfc;padding:.7rem}}.content-text{{white-space:pre-wrap}}.response-list,.answer-list{{display:grid;gap:.5rem}}.response-list>li,.answer-list>li{{border-bottom:1px solid var(--line);padding-bottom:.5rem}}.answer-list{{list-style:none;padding:0}}.answer-list li{{display:flex;justify-content:space-between;gap:1rem}}.answer-value{{font-weight:750}}.feedback-list{{display:grid;gap:.75rem}}.feedback-list article{{border:1px solid var(--line);padding:.6rem}}.inline-dl{{display:flex;flex-wrap:wrap;gap:1rem 2rem}}.inline-dl div{{min-width:140px}}.inline-dl dt{{font-weight:700}}.inline-dl dd{{margin:0}}.fidelity-panel,.support-panel{{padding:.75rem;border:2px solid}}.fidelity-panel{{border-color:var(--accent);background:#f3fafc}}.support-panel{{border-color:var(--warn);background:#fff9ef}}.diagnostic-list,.action-list{{list-style:none;padding:0;display:grid;gap:.65rem}}.diagnostic-list li,.action-list li{{display:flex;gap:.7rem;border-bottom:1px solid var(--line);padding:.5rem 0}}.severity{{align-self:start;border:1px solid currentColor;border-radius:999px;padding:.15rem .45rem;font-size:.8rem;font-weight:750}}.severity-error{{color:var(--danger)}}.severity-warning{{color:var(--warn)}}.severity-info{{color:var(--accent)}}.provenance{{border-top:1px solid var(--line);padding-top:.75rem}}.source-facts{{background:#f3f3f3;padding:.6rem}}.code-block{{white-space:pre-wrap;overflow:auto;background:#20262d;color:#fff;padding:.7rem}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);padding:.5rem;text-align:left}}th{{background:var(--soft)}}.boundary{{border-left:6px solid var(--warn)}}footer{{padding:1rem 0 3rem;color:var(--muted)}}[hidden]{{display:none!important}}
@media(max-width:650px){{.occurrence-main,.question-heading,.answer-list li{{display:grid}}.detail-link,.count-pill{{white-space:normal;width:max-content}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}*,*::before,*::after{{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}}}
@media print{{button,.filters,nav,.skip{{display:none!important}}body{{background:#fff}}.panel,.question-card,.occurrence-card{{box-shadow:none;break-inside:avoid}}}}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to review content</a>
<header>
<p class="evidence-banner">Local course evidence — not share-safe by default</p>
<p class="eyebrow">Quiz Binder · local authored-content review</p>
<h1>Reading Room</h1>
<p class="lede">Review every projected question occurrence without cross-comparing workbook tabs. Authored prompts, answers, and feedback are included here as local evidence. Build support is shown separately and never upgrades extraction fidelity. Use the linked reviewer workbook to add Marginalia and record proposer/approver decisions; this page does not overwrite source evidence.</p>
<nav aria-label="Reading Room sections"><ul><li><a href="#snapshot">Snapshot</a></li><li><a href="#queues">Review queues</a></li><li><a href="#occurrences">Occurrences</a></li><li><a href="#question-details">Question details</a></li><li><a href="#provenance">Run provenance</a></li></ul></nav>
</header>
<main id="main" tabindex="-1">
<section class="panel" id="snapshot" aria-labelledby="snapshot-title"><h2 id="snapshot-title">Review snapshot</h2><div class="metrics"><div class="metric"><strong>{_e(summary.get('quiz_count'))}</strong><span>Quizzes</span></div><div class="metric"><strong>{_e(summary.get('unique_question_count'))}</strong><span>Source question records</span></div><div class="metric"><strong>{_e(summary.get('authored_variant_class_count'))}</strong><span>Authored variants</span></div><div class="metric"><strong>{_e(summary.get('reused_authored_variant_class_count'))}</strong><span>Reused variant groups</span></div><div class="metric"><strong>{_e(summary.get('occurrence_count'))}</strong><span>Question occurrences</span></div><div class="metric"><strong>{_e(summary.get('action_needed_count'))}</strong><span>Action-needed records</span></div></div><p><strong>Why three question counts?</strong> Source records keep each Brightspace identity distinct; authored variants group records with identical authored content; occurrences represent every use across quizzes, sections, or pools.</p><h3>Related local artifacts</h3><ul class="artifact-list">{artifact_html}</ul></section>
<section class="panel" id="queues" aria-labelledby="queues-title"><h2 id="queues-title">Review queues</h2><p>Each lane keeps a distinct evidence or decision state. Machine codes appear beside the adopted plain labels for traceability.</p><div class="queue-grid">{queue_html}</div></section>
<section class="panel" id="occurrences" aria-labelledby="occurrences-title"><h2 id="occurrences-title">Question occurrence explorer</h2><p>Filter by placement, type, source resolution, pool, or diagnostics. Occurrences carry placement evidence and link to the one canonical question record.</p><form class="filters" id="filters" role="search"><div class="filter-field"><label for="search">Search</label><input id="search" name="search" type="search" autocomplete="off"></div><div class="filter-field"><label for="quiz-filter">Quiz</label><select id="quiz-filter"><option value="">All quizzes</option>{quiz_options}</select></div><div class="filter-field"><label for="section-filter">Section</label><select id="section-filter"><option value="">All sections</option>{section_options}</select></div><div class="filter-field"><label for="kind-filter">Question type</label><select id="kind-filter"><option value="">All types</option>{kind_options}</select></div><div class="filter-field"><label for="source-filter">Source status</label><select id="source-filter"><option value="">All source states</option>{source_options}</select></div><div class="filter-field"><label for="match-filter">Match status</label><select id="match-filter"><option value="">All match states</option>{match_options}</select></div><div class="filter-field"><label for="pool-filter">Pool</label><select id="pool-filter"><option value="">All pools</option>{pool_options}</select></div><div class="filter-field"><label for="diagnostic-filter">Diagnostics</label><select id="diagnostic-filter"><option value="">All</option><option value="yes">Has diagnostics</option><option value="no">No diagnostics</option></select></div><div class="filter-actions"><button id="reset-filters" type="reset">Reset filters</button></div></form><p id="result-status" class="result-status" aria-live="polite">Showing {len(occurrences)} of {len(occurrences)} occurrences.</p><div class="occurrence-list" id="occurrence-list">{occurrence_html or '<p class="empty">No quiz occurrences were projected. Library-only questions may still appear below.</p>'}</div></section>
<section class="panel" id="question-details" aria-labelledby="details-title"><h2 id="details-title">Unique question details</h2><p class="lede">Each normalized question is shown once. Authored HTML and formula markup are displayed as source text; this static reader does not execute it or load embedded resources.</p><div class="question-list">{question_html or '<p class="empty">No question entities were projected.</p>'}</div></section>
<section class="panel" id="provenance" aria-labelledby="provenance-title"><h2 id="provenance-title">Run provenance</h2><dl class="inline-dl"><div><dt>Model ID</dt><dd><code>{_e(model.get('model_id'))}</code></dd></div><div><dt>Run ID</dt><dd><code>{_e(model.get('run_id'))}</code></dd></div><div><dt>Source kind</dt><dd>{_e(model.get('source_kind'))}</dd></div><div><dt>Source lineage key</dt><dd><code>{_e(model.get('source_lineage_key'))}</code></dd></div><div><dt>Source fingerprint</dt><dd><code>{_e(_as_dict(model.get('source_fingerprint')).get('digest'))}</code></dd></div></dl></section>
<section class="panel boundary" aria-labelledby="boundary-title"><h2 id="boundary-title">What this station does not do</h2><p>This file reads a normalized model only. It does not parse XML, start a server, use the network, upload content, contact Brightspace, or claim that extracted questions are buildable. A hosted upload flow would require a separate privacy, retention, authentication, and threat-model decision.</p></section>
</main>
<footer>Static format <code>{_e(data.get('format'))}</code>. Safe to open directly with <code>file://</code>; no server is required.</footer>
<script>
(function(){{
  'use strict';
  const form=document.getElementById('filters');
  const cards=Array.from(document.querySelectorAll('.occurrence-card'));
  const status=document.getElementById('result-status');
  const fields={{search:document.getElementById('search'),quiz:document.getElementById('quiz-filter'),section:document.getElementById('section-filter'),kind:document.getElementById('kind-filter'),source:document.getElementById('source-filter'),match:document.getElementById('match-filter'),pool:document.getElementById('pool-filter'),diagnostic:document.getElementById('diagnostic-filter')}};
  function applyFilters(){{
    const values={{search:fields.search.value.trim().toLowerCase(),quiz:fields.quiz.value,section:fields.section.value,kind:fields.kind.value,source:fields.source.value,match:fields.match.value,pool:fields.pool.value,diagnostic:fields.diagnostic.value}};
    let shown=0;
    cards.forEach(function(card){{const match=(!values.search||card.dataset.search.includes(values.search))&&(!values.quiz||card.dataset.quiz===values.quiz)&&(!values.section||card.dataset.section===values.section)&&(!values.kind||card.dataset.kind===values.kind)&&(!values.source||card.dataset.source===values.source)&&(!values.match||card.dataset.match===values.match)&&(!values.pool||card.dataset.pool===values.pool)&&(!values.diagnostic||card.dataset.diagnostic===values.diagnostic);card.hidden=!match;if(match)shown+=1;}});
    status.textContent='Showing '+shown+' of '+cards.length+' occurrences.';
  }}
  form.addEventListener('input',applyFilters);
  form.addEventListener('change',applyFilters);
  form.addEventListener('reset',function(){{window.setTimeout(applyFilters,0);}});
}})();
</script>
</body>
</html>
"""


_SHARD_STYLE = """
:root{--ink:#17212b;--muted:#52616f;--paper:#f6f4ef;--card:#fff;--line:#c8d0d7;--accent:#185a78;--accent-dark:#12445b;--focus:#b74900;--soft:#eaf3f6;--warn:#8b4b08;--danger:#942c2c;--good:#356644;--key:#fff2c8}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--accent)}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,.table-wrap:focus-visible{outline:3px solid var(--focus);outline-offset:3px}code,.content-text,.answer-value{overflow-wrap:anywhere;word-break:break-word}button,input,select{font:inherit}.skip{position:absolute;top:-8rem;left:1rem;background:#fff;padding:.75rem;z-index:10}.skip:focus{top:1rem}header,main,footer{width:min(1180px,calc(100% - 2rem));margin-inline:auto}header{padding:1.5rem 0 .5rem}h1{font-size:clamp(2rem,5vw,3.5rem);line-height:1;margin:.35rem 0 1rem}h2{margin-top:0}h3{margin:.25rem 0 .5rem}.lede,.scope-note,.empty{color:var(--muted)}.evidence-banner{border:2px solid var(--warn);background:#fff8e7;padding:.65rem .85rem;font-weight:750;display:inline-block}.eyebrow,.kicker,.content-label{font-weight:750;text-transform:uppercase;letter-spacing:.07em;color:var(--accent);font-size:.78rem}.page-nav,.section-nav,.button-row{display:flex;flex-wrap:wrap;gap:.6rem;margin:1rem 0}.page-nav a,.section-nav a,.detail-link,.count-pill,.button-link{border:1px solid var(--line);background:#fff;border-radius:.4rem;padding:.5rem .7rem;text-decoration:none}.button-link.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:750;padding:.75rem 1rem}.button-link.primary:hover{background:var(--accent-dark)}.panel,.question-card,.occurrence-card,.catalog-card,.quiz-card{background:var(--card);border:1px solid var(--line);border-radius:.65rem;padding:1rem;margin:1rem 0;box-shadow:0 2px 8px #17212b12}.export-panel{background:#eef8fb;border:2px solid var(--accent);padding:1.25rem}.quiz-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:1rem}.quiz-card{display:flex;flex-direction:column;margin:0}.quiz-card .button-row{margin-top:auto}.quiz-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.4rem .8rem;margin:.8rem 0}.quiz-meta span{color:var(--muted)}.metrics,.context-grid,.two-col,.separated-states{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:1rem}.metric{border-left:4px solid var(--accent);padding-left:.8rem}.metric strong{display:block;font-size:2rem;line-height:1.1}.question-list,.occurrence-list,.catalog-list{display:grid;gap:1rem}.occurrence-main,.question-heading{display:flex;justify-content:space-between;gap:1rem;align-items:start}.question-section{margin:1rem 0;padding-top:.75rem;border-top:1px solid var(--line)}.sensitive{background:var(--key);padding:.8rem;border:1px solid #dbc878}.authored{border-left:4px solid var(--accent);background:#f9fbfc;padding:.7rem}.content-text{white-space:pre-wrap}.response-list,.answer-list{display:grid;gap:.5rem}.answer-list,.plain-list{list-style:none;padding:0}.answer-list li{display:flex;justify-content:space-between;gap:1rem}.answer-value{font-weight:750}.feedback-list{display:grid;gap:.75rem}.feedback-list article{border:1px solid var(--line);padding:.6rem}.inline-dl{display:flex;flex-wrap:wrap;gap:1rem 2rem}.inline-dl div{min-width:140px}.inline-dl dt{font-weight:700}.inline-dl dd{margin:0}.fidelity-panel,.support-panel{padding:.75rem;border:2px solid}.fidelity-panel{border-color:var(--accent);background:#f3fafc}.support-panel{border-color:var(--warn);background:#fff9ef}.match-explanation,.match-difference{border-left:4px solid var(--warn);background:#fff8e7;padding:.65rem}.source-facts{background:#f3f3f3;padding:.6rem}.code-block,.catalog-json{white-space:pre-wrap;overflow:auto;background:#20262d;color:#fff;padding:.7rem}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:.5rem;text-align:left}th{background:var(--soft)}.reference-list{display:grid;gap:.35rem}.boundary{border-left:6px solid var(--warn)}.filters{display:grid;grid-template-columns:2fr repeat(2,minmax(150px,1fr)) auto;gap:.75rem;padding:1rem;background:var(--soft);border:1px solid var(--line);margin:1rem 0}.filter-field label{display:block;font-weight:700;margin-bottom:.25rem}.filter-field input,.filter-field select{width:100%;padding:.5rem;border:1px solid #7c8993;background:#fff}.filter-actions{display:flex;align-items:end}.filter-actions button{padding:.55rem .75rem;background:#fff;border:1px solid var(--line);border-radius:.35rem}.result-status{font-weight:700}.section-group{scroll-margin-top:1rem}.section-heading{border-bottom:2px solid var(--accent);padding-bottom:.45rem;margin-top:2rem}.placement-list{display:grid;gap:.5rem;list-style:none;padding:0}.placement-list li{border-left:3px solid var(--line);padding-left:.7rem}.review-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem}.review-summary div{background:#fff;border:1px solid var(--line);padding:.75rem}.review-summary strong{display:block;font-size:1.5rem}.technical{border-top:1px solid var(--line);padding-top:.75rem}.technical summary{font-size:1.05rem}.technical-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}.technical-grid section{border:1px solid var(--line);padding:.75rem}.local-note{border-left:4px solid var(--good);background:#f0f7f2;padding:.75rem}.boundary{border-left:6px solid var(--warn)}footer{padding:1rem 0 3rem;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:760px){.filters{grid-template-columns:1fr}.occurrence-main,.question-heading,.answer-list li{display:grid}.quiz-meta{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
@media print{.page-nav,.section-nav,.filters,.button-row{display:none!important}body{background:#fff}.panel,.question-card,.occurrence-card,.catalog-card,.quiz-card{box-shadow:none;break-inside:avoid}}
"""


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _anchor_id(prefix: str, key: Any) -> str:
    token = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{token}"


def _page_nav(
    *, home_href: str, previous_href: str | None, next_href: str | None
) -> str:
    links = [f'<a href="{_e(home_href)}">Reading Room home</a>']
    if previous_href:
        links.append(f'<a href="{_e(previous_href)}">Previous</a>')
    if next_href:
        links.append(f'<a href="{_e(next_href)}">Next</a>')
    return (
        '<nav class="page-nav" aria-label="Page navigation">'
        + "".join(links)
        + "</nav>"
    )


def _shard_page(
    *,
    title: str,
    eyebrow: str,
    lede: str,
    body: str,
    home_href: str = "../index.html",
    previous_href: str | None = None,
    next_href: str | None = None,
    script: str = "",
) -> str:
    navigation = _page_nav(
        home_href=home_href,
        previous_href=previous_href,
        next_href=next_href,
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"><title>{_e(title)} — Quiz Binder Reading Room</title><style>{_SHARD_STYLE}</style></head>
<body><a class="skip" href="#main">Skip to main content</a><header><p class="evidence-banner">Local course evidence — not share-safe by default</p><p class="eyebrow">{_e(eyebrow)}</p><h1>{_e(title)}</h1><p class="lede">{_e(lede)}</p>{navigation}</header><main id="main" tabindex="-1">{body}</main><footer>Static local Reading Room page. Open directly with <code>file://</code>; no server or network is required.</footer>{script}</body></html>
"""


def _catalog_key(kind: str, row: Mapping[str, Any]) -> str | None:
    field = {
        "assets": "entity_key",
        "evidence": "evidence_key",
        "diagnostics": "diagnostic_id",
        "lineage": "lineage_id",
    }[kind]
    value = row.get(field)
    return str(value) if value else None


def _catalog_record_html(kind: str, row: Mapping[str, Any]) -> str:
    key = _catalog_key(kind, row) or "unkeyed-record"
    return (
        f'<article class="catalog-card" id="{_e(_anchor_id(kind, key))}" data-catalog-key="{_e(key)}">'
        f'<p class="kicker">{_e(kind.rstrip("s"))}</p><h2><code>{_e(key)}</code></h2>'
        '<pre class="catalog-json">'
        + _e(json.dumps(dict(row), indent=2, ensure_ascii=False, sort_keys=True))
        + "</pre></article>"
    )


def _write_text_artifact(
    output_dir: Path,
    relative_path: str,
    content: str,
    *,
    kind: str,
    record_count: int,
) -> dict[str, Any]:
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    payload = content.encode("utf-8")
    return {
        "kind": kind,
        "path": relative_path,
        "record_count": record_count,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_binary_descriptor(
    output_dir: Path,
    relative_path: str,
    *,
    kind: str,
    record_count: int,
) -> dict[str, Any]:
    payload = (output_dir / relative_path).read_bytes()
    return {
        "kind": kind,
        "path": relative_path,
        "record_count": record_count,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_workbook_export(
    output_dir: Path, artifacts: list[dict[str, Any]]
) -> dict[str, Any] | None:
    workbook_names = ("detailed_workbook", "reviewer_workbook")
    by_name = {str(row.get("name")): row for row in artifacts if row.get("name")}
    selected: list[tuple[Path, str]] = []
    run_root = output_dir.resolve().parent
    for name in workbook_names:
        row = by_name.get(name)
        if not row:
            continue
        href = str(row.get("href") or "")
        parsed = urlsplit(href)
        candidate = output_dir / Path(*PurePosixPath(parsed.path).parts)
        if candidate.is_symlink():
            raise ValueError(f"workbook export may not read a symlink: {href!r}")
        source = candidate.resolve()
        try:
            source.relative_to(run_root)
        except ValueError as exc:
            raise ValueError(f"workbook export escapes the local run: {href!r}") from exc
        if source.is_file():
            selected.append((source, source.name))
    if not selected:
        return None

    relative_path = "exports/quiz-review-workbooks.zip"
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        for source, filename in selected:
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    descriptor = _write_binary_descriptor(
        output_dir,
        relative_path,
        kind="export:workbooks",
        record_count=len(selected),
    )
    return {
        **descriptor,
        "filenames": [filename for _source, filename in selected],
    }


def _quiz_groups(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for source_index, row in enumerate(occurrences, start=1):
        title = str(row.get("quiz_title") or "Unassigned placements")
        key = str(
            row.get("quiz_entity_key")
            or f"unresolved:{row.get('quiz_ordinal')}:{title}"
        )
        group = groups.setdefault(
            key,
            {
                "quiz_entity_key": row.get("quiz_entity_key"),
                "title": title,
                "ordinal": row.get("quiz_ordinal"),
                "rows": [],
                "question_keys": [],
                "section_keys": [],
                "pool_titles": [],
                "review_occurrence_count": 0,
                "source_index": source_index,
            },
        )
        group["rows"].append(row)
        question_key = row.get("question_entity_key")
        if question_key and question_key not in group["question_keys"]:
            group["question_keys"].append(question_key)
        section_key = row.get("section_entity_key") or row.get("section_title")
        if section_key and section_key not in group["section_keys"]:
            group["section_keys"].append(section_key)
        pool_title = row.get("pool_title")
        if pool_title and pool_title not in group["pool_titles"]:
            group["pool_titles"].append(pool_title)
        if _occurrence_needs_source_review(row):
            group["review_occurrence_count"] += 1
    result = sorted(
        groups.values(),
        key=lambda row: (
            row.get("ordinal") is None,
            row.get("ordinal") or 10**9,
            row.get("source_index") or 10**9,
            str(row.get("title")),
        ),
    )
    for index, group in enumerate(result, start=1):
        group["href"] = f"quizzes/quiz-{index:04d}.html"
        group["occurrence_count"] = len(group["rows"])
        group["unique_question_count"] = len(group["question_keys"])
        group["section_count"] = len(group["section_keys"])
        group["pool_count"] = len(group["pool_titles"])
    return result


def _placement_context_html(
    rows: list[dict[str, Any]], occurrence_hrefs: Mapping[str, str]
) -> str:
    if not rows:
        return '<p class="empty">This question is not placed in a quiz.</p>'
    items = []
    for row in rows:
        href = occurrence_hrefs.get(str(row.get("occurrence_key")))
        section_title = _section_display_title(
            row.get("section_title"), row.get("section_ordinal")
        )
        label = " · ".join(
            str(value)
            for value in (
                row.get("quiz_title") or "Quiz not resolved",
                section_title,
                (
                    f"question {row.get('question_ordinal')}"
                    if row.get("question_ordinal") is not None
                    else "question order not recorded"
                ),
            )
        )
        if href:
            items.append(f'<li><a href="{_e(href)}">{_e(label)}</a></li>')
        else:
            items.append(f"<li>{_e(label)}</li>")
    return '<ul class="placement-list">' + "".join(items) + "</ul>"


def _quiz_filter_script() -> str:
    return """<script>
(function(){
  'use strict';
  const form=document.getElementById('quiz-filters');
  if(!form){return;}
  const cards=Array.from(document.querySelectorAll('.occurrence-card'));
  const sections=Array.from(document.querySelectorAll('.section-group'));
  const status=document.getElementById('result-status');
  const search=document.getElementById('search');
  const section=document.getElementById('section-filter');
  const kind=document.getElementById('kind-filter');
  const review=document.getElementById('review-filter');
  function applyFilters(){
    const query=search.value.trim().toLowerCase();
    let shown=0;
    cards.forEach(function(card){
      const matches=(!query||card.dataset.search.includes(query))&&(!section.value||card.dataset.section===section.value)&&(!kind.value||card.dataset.kind===kind.value)&&(!review.value||card.dataset.review===review.value);
      card.hidden=!matches;
      if(matches){shown+=1;}
    });
    sections.forEach(function(group){group.hidden=!group.querySelector('.occurrence-card:not([hidden])');});
    status.textContent='Showing '+shown+' of '+cards.length+' placements.';
  }
  form.addEventListener('input',applyFilters);
  form.addEventListener('change',applyFilters);
  form.addEventListener('reset',function(){window.setTimeout(applyFilters,0);});
})();
</script>"""


def _render_quiz_page(
    group: Mapping[str, Any],
    *,
    entity_by_key: Mapping[str, Mapping[str, Any]],
    question_hrefs: Mapping[str, str],
    previous_href: str | None,
    next_href: str | None,
) -> str:
    rows = [_as_dict(row) for row in _as_list(group.get("rows"))]
    sections: dict[str, dict[str, Any]] = {}
    for source_index, row in enumerate(rows, start=1):
        title = str(row.get("section_title") or "Unsectioned questions")
        key = str(
            row.get("section_entity_key")
            or f"ordinal:{row.get('section_ordinal')}:{title}"
        )
        section = sections.setdefault(
            key,
            {
                "title": title,
                "ordinal": row.get("section_ordinal"),
                "rows": [],
                "source_index": source_index,
                "anchor": _anchor_id("section", key),
            },
        )
        section["rows"].append(row)
    section_rows = sorted(
        sections.values(),
        key=lambda row: (
            row.get("ordinal") is None,
            row.get("ordinal") or 10**9,
            row.get("source_index") or 10**9,
        ),
    )
    for display_index, section in enumerate(section_rows, start=1):
        display_title = _section_display_title(
            section.get("title"),
            section.get("ordinal"),
            fallback_ordinal=display_index,
        )
        section["display_title"] = display_title
        section["filter_key"] = str(section.get("anchor"))
        for row in section["rows"]:
            row["_section_display_title"] = display_title
            row["_section_filter_key"] = section["filter_key"]
    section_nav = "".join(
        f'<a href="#{_e(section.get("anchor"))}">{_e(section.get("display_title"))}</a>'
        for section in section_rows
    )
    section_html = []
    for section in section_rows:
        cards = "".join(
            _occurrence_card(
                row,
                question=entity_by_key.get(str(row.get("question_entity_key"))),
                question_href=question_hrefs.get(str(row.get("question_entity_key"))),
            )
            for row in section["rows"]
        )
        section_html.append(
            f'<section class="section-group" id="{_e(section.get("anchor"))}"><h2 class="section-heading">{_e(section.get("display_title"))}</h2><div class="occurrence-list">{cards}</div></section>'
        )
    section_options = "".join(
        f'<option value="{_e(section.get("filter_key"))}">{_e(section.get("display_title"))}</option>'
        for section in section_rows
    )
    kind_options = _filter_options(
        entity_by_key.get(str(row.get("question_entity_key")), {}).get("kind")
        for row in rows
    )
    body = f"""<section class="panel"><h2>Quiz overview</h2><div class="metrics"><div class="metric"><strong>{_e(group.get('occurrence_count'))}</strong><span>Placements</span></div><div class="metric"><strong>{_e(group.get('unique_question_count'))}</strong><span>Unique questions</span></div><div class="metric"><strong>{_e(group.get('section_count'))}</strong><span>Sections</span></div><div class="metric"><strong>{_e(group.get('pool_count'))}</strong><span>Pools</span></div></div><p>{_e(group.get('review_occurrence_count'))} placement(s) have unresolved, probable, or diagnostic evidence worth reviewing.</p></section>
<nav class="section-nav" aria-label="Quiz sections">{section_nav}</nav>
<section class="panel"><h2>Find a question</h2><form class="filters" id="quiz-filters" role="search"><div class="filter-field"><label for="search">Search titles, types, pools, or identifiers</label><input id="search" type="search" autocomplete="off"></div><div class="filter-field"><label for="section-filter">Section</label><select id="section-filter"><option value="">All sections</option>{section_options}</select></div><div class="filter-field"><label for="kind-filter">Question type</label><select id="kind-filter"><option value="">All types</option>{kind_options}</select></div><div class="filter-field"><label for="review-filter">Review state</label><select id="review-filter"><option value="">All placements</option><option value="yes">Needs source review</option><option value="no">No source-review flag</option></select></div><div class="filter-actions"><button type="reset">Reset</button></div></form><p class="result-status" id="result-status" aria-live="polite">Showing {len(rows)} of {len(rows)} placements.</p></section>
{''.join(section_html)}"""
    return _shard_page(
        title=str(group.get("title") or "Quiz"),
        eyebrow="Quiz review",
        lede="Review the quiz in its source order. Open a question to inspect its prompt, answer evidence, feedback, assets, and provenance.",
        body=body,
        previous_href=previous_href,
        next_href=next_href,
        script=_quiz_filter_script(),
    )


def _render_station_index(manifest: Mapping[str, Any]) -> str:
    summary = _as_dict(manifest.get("summary"))
    queues = _as_dict(manifest.get("queue_summary"))
    artifacts = [_as_dict(row) for row in _as_list(manifest.get("artifacts"))]
    artifacts_by_name = {str(row.get("name")): row for row in artifacts}
    workbook_rows = [
        artifacts_by_name[name]
        for name in ("detailed_workbook", "reviewer_workbook")
        if name in artifacts_by_name
    ]
    workbook_export = _as_dict(_as_dict(manifest.get("exports")).get("workbooks"))
    export_primary = (
        f'<a class="button-link primary" href="{_e(workbook_export.get("path"))}" download>Download all workbooks (.zip)</a>'
        if workbook_export.get("path")
        else ""
    )
    workbook_links = "".join(
        f'<a class="button-link" href="{_e(row.get("href"))}" download>{_e(row.get("label"))}</a>'
        for row in workbook_rows
    )
    quiz_cards = "".join(
        f'<article class="quiz-card"><p class="kicker">Quiz {_e(row.get("ordinal") if row.get("ordinal") is not None else "—")}</p><h3>{_e(row.get("title"))}</h3><div class="quiz-meta"><span><strong>{_e(row.get("occurrence_count"))}</strong> placements</span><span><strong>{_e(row.get("unique_question_count"))}</strong> unique questions</span><span><strong>{_e(row.get("section_count"))}</strong> sections</span><span><strong>{_e(row.get("pool_count"))}</strong> pools</span></div><p>{_e(row.get("review_occurrence_count"))} placement(s) flagged for review.</p><div class="button-row"><a class="button-link primary" href="{_e(row.get("href"))}">Open quiz</a></div></article>'
        for row in _as_list(manifest.get("quizzes"))
    ) or '<p class="empty">No quiz placements were projected.</p>'
    decision_count = sum(
        int(queues.get(key) or 0)
        for key in (
            "no_safe_match_count",
            "probable_match_count",
            "settings_review_count",
            "asset_review_count",
            "finding_count",
        )
    )
    queue_summary = f"""<div class="review-summary"><div><strong>{_e(queues.get('no_safe_match_count') or 0)}</strong>No safe match</div><div><strong>{_e(queues.get('probable_match_count') or 0)}</strong>Probable match</div><div><strong>{_e(queues.get('settings_review_count') or 0)}</strong>Settings review</div><div><strong>{_e(queues.get('asset_review_count') or 0)}</strong>Asset review</div><div><strong>{_e(queues.get('finding_count') or 0)}</strong>Extraction findings</div><div><strong>{_e(queues.get('unsupported_kind_count') or 0)}</strong>Review-only build support</div></div>"""
    technical_artifacts = [
        row
        for row in artifacts
        if row.get("name") not in {"detailed_workbook", "reviewer_workbook"}
    ]
    artifact_html = "".join(
        f'<li><a href="{_e(row.get("href"))}">{_e(row.get("label"))}</a></li>'
        for row in technical_artifacts
    ) or "<li>No technical artifact links were supplied.</li>"
    catalog_sections = []
    for kind, label in (
        ("assets", "Assets"),
        ("evidence", "Source evidence"),
        ("diagnostics", "Diagnostics"),
        ("lineage", "Lineage"),
    ):
        catalog = _as_dict(_as_dict(manifest.get("catalogs")).get(kind))
        paths = _as_list(catalog.get("paths"))
        link = (
            f'<a href="{_e(paths[0])}">{_e(label)} ({_e(catalog.get("record_count"))})</a>'
            if paths
            else f"{_e(label)} (0)"
        )
        catalog_sections.append(f"<li>{link}</li>")
    first_question = next(iter(_as_list(manifest.get("question_entities"))), {})
    question_link = (
        f'<a href="{_e(_as_dict(first_question).get("href"))}">Browse all canonical questions</a>'
        if _as_dict(first_question).get("href")
        else "No canonical question pages"
    )
    body = f"""<section class="panel export-panel"><p class="kicker">Take the work with you</p><h2>Download the review workbooks</h2><p>The detailed workbook preserves the full extraction record. The reviewer workbook provides the cleaner working surface for comments, revisions, and approval.</p><div class="button-row">{export_primary}{workbook_links}</div></section>
<section class="panel"><h2>Review snapshot</h2><div class="metrics"><div class="metric"><strong>{_e(summary.get('quiz_count'))}</strong><span>Quizzes</span></div><div class="metric"><strong>{_e(summary.get('unique_question_count'))}</strong><span>Source question records</span></div><div class="metric"><strong>{_e(summary.get('authored_variant_class_count'))}</strong><span>Authored variants</span></div><div class="metric"><strong>{_e(summary.get('reused_authored_variant_class_count'))}</strong><span>Reused variant groups</span></div><div class="metric"><strong>{_e(summary.get('occurrence_count'))}</strong><span>Placements</span></div><div class="metric"><strong>{_e(decision_count)}</strong><span>Review queue entries</span></div></div><p><strong>Why three question counts?</strong> Source records keep each Brightspace identity distinct; authored variants group records with identical authored content; placements show every use.</p><p class="local-note">Everything here runs from local files. A browser displays the HTML, but no server, internet connection, or Brightspace session is required.</p></section>
<section class="panel"><h2>Choose a quiz</h2><p>Start with the quiz you want to inspect. Questions remain in source order and retain their section, pool, draw, points, and library-placement context.</p><div class="quiz-grid">{quiz_cards}</div></section>
<section class="panel"><h2>Review queues</h2><p>These counts separate source-review decisions from build-support limitations. Review-only question types are informative; they do not make extraction incomplete.</p>{queue_summary}</section>
<section class="panel"><details class="technical"><summary>Evidence and technical files</summary><div class="technical-grid"><section><h3>Run artifacts</h3><ul>{artifact_html}</ul></section><section><h3>Evidence catalogs</h3><ul>{''.join(catalog_sections)}</ul></section><section><h3>Canonical records</h3><p>{question_link}</p><p class="scope-note">Storage page names, byte counts, receipts, and machine identifiers live here instead of in the primary review path.</p></section></div></details></section>
<section class="panel boundary"><h2>Local evidence boundary</h2><p>This Reading Room contains authored prompts, answers, feedback, and source provenance. It uses local links only, performs no fetches, and is not share-safe by default.</p></section>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"><title>Quiz Binder — Reading Room</title><style>{_SHARD_STYLE}</style></head><body><a class="skip" href="#main">Skip to main content</a><header><p class="evidence-banner">Local course evidence — not share-safe by default</p><p class="eyebrow">Quiz Binder · local review</p><h1>Reading Room</h1><p class="lede">Choose a quiz, review its questions in context, and download both workbooks without digging through machine artifacts.</p></header><main id="main" tabindex="-1">{body}</main><footer>Static format <code>{_e(manifest.get('format'))}</code>. Open locally with <code>file://</code>; no server is required.</footer></body></html>
"""


def write_quiz_review_station(
    model_path: Path,
    output_dir: Path,
    *,
    artifact_links: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"review station output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_quiz_review_station(
        model,
        artifact_links=artifact_links,
        output_dir=output_dir,
    )
    entities = [_as_dict(row) for row in _as_list(data.get("question_entities"))]
    occurrences = [_as_dict(row) for row in _as_list(data.get("question_occurrences"))]
    catalogs = _as_dict(data.get("catalogs"))
    entity_by_key = {
        str(row.get("entity_key")): row for row in entities if row.get("entity_key")
    }

    question_chunks = _chunks(entities, QUESTION_SHARD_SIZE)
    question_paths = [
        f"questions/questions-{index:04d}.html"
        for index in range(1, len(question_chunks) + 1)
    ]

    question_hrefs: dict[str, str] = {}
    for relative_path, rows in zip(question_paths, question_chunks):
        for row in rows:
            key = str(row.get("entity_key"))
            question_hrefs[key] = f"../{relative_path}#question-{key}"

    catalog_chunks: dict[str, list[list[dict[str, Any]]]] = {}
    catalog_paths: dict[str, list[str]] = {}
    catalog_hrefs: dict[str, dict[str, str]] = {}
    for kind in ("assets", "evidence", "diagnostics", "lineage"):
        rows = [_as_dict(row) for row in _as_list(catalogs.get(kind))]
        chunks = _chunks(rows, CATALOG_SHARD_SIZE)
        paths = [
            f"catalogs/{kind}-{index:04d}.html" for index in range(1, len(chunks) + 1)
        ]
        catalog_chunks[kind] = chunks
        catalog_paths[kind] = paths
        hrefs: dict[str, str] = {}
        for relative_path, chunk in zip(paths, chunks):
            for row in chunk:
                key = _catalog_key(kind, row)
                if key:
                    hrefs[key] = f"../{relative_path}#{_anchor_id(kind, key)}"
        catalog_hrefs[kind] = hrefs

    shard_descriptors: list[dict[str, Any]] = []
    for kind in ("assets", "evidence", "diagnostics", "lineage"):
        paths = catalog_paths[kind]
        for index, (relative_path, rows) in enumerate(zip(paths, catalog_chunks[kind])):
            body = (
                '<div class="catalog-list">'
                + "".join(_catalog_record_html(kind, row) for row in rows)
                + "</div>"
            )
            content = _shard_page(
                title=f"{kind.title()} catalog {index + 1} of {len(paths)}",
                eyebrow="Canonical shared-record catalog",
                lede="Each catalog record is stored here once; question and occurrence records link to it by key.",
                body=body,
                previous_href=Path(paths[index - 1]).name if index else None,
                next_href=(
                    Path(paths[index + 1]).name if index + 1 < len(paths) else None
                ),
            )
            shard_descriptors.append(
                _write_text_artifact(
                    output_dir,
                    relative_path,
                    content,
                    kind=f"catalog:{kind}",
                    record_count=len(rows),
                )
            )

    quiz_groups = _quiz_groups(occurrences)
    occurrence_href_by_key: dict[str, str] = {}
    question_occurrences: dict[str, list[dict[str, Any]]] = {}
    question_occurrence_hrefs: dict[str, str] = {}
    for group in quiz_groups:
        for row in group["rows"]:
            occurrence_key = str(row.get("occurrence_key"))
            question_key = str(row.get("question_entity_key") or "")
            occurrence_href_by_key[occurrence_key] = (
                f"{group['href']}#occurrence-{occurrence_key}"
            )
            question_occurrence_hrefs[occurrence_key] = (
                f"../{group['href']}#occurrence-{occurrence_key}"
            )
            if question_key:
                question_occurrences.setdefault(question_key, []).append(row)

    for index, (relative_path, rows) in enumerate(zip(question_paths, question_chunks)):
        question_sections = []
        question_reference_hrefs = {**catalog_hrefs, "questions": question_hrefs}
        for row in rows:
            key = str(row.get("entity_key"))
            question_sections.append(
                '<section class="panel"><h2>Where this question is used</h2>'
                + _placement_context_html(
                    question_occurrences.get(key, []), question_occurrence_hrefs
                )
                + "</section>"
                + _question_card(row, reference_hrefs=question_reference_hrefs)
            )
        if len(rows) == 1:
            page_title = _canonical_question_title(rows[0])
        else:
            page_title = (
                f"Questions {index * QUESTION_SHARD_SIZE + 1}–"
                f"{index * QUESTION_SHARD_SIZE + len(rows)}"
            )
        content = _shard_page(
            title=page_title,
            eyebrow="Canonical question review",
            lede="Review the authored prompt, answer evidence, scoring, feedback, assets, and provenance. This question record is stored once and linked from every placement.",
            body='<div class="question-list">' + "".join(question_sections) + "</div>",
            previous_href=Path(question_paths[index - 1]).name if index else None,
            next_href=(
                Path(question_paths[index + 1]).name
                if index + 1 < len(question_paths)
                else None
            ),
        )
        shard_descriptors.append(
            _write_text_artifact(
                output_dir,
                relative_path,
                content,
                kind="questions",
                record_count=len(rows),
                )
            )

    for index, group in enumerate(quiz_groups):
        relative_path = str(group["href"])
        content = _render_quiz_page(
            group,
            entity_by_key=entity_by_key,
            question_hrefs=question_hrefs,
            previous_href=(
                Path(str(quiz_groups[index - 1]["href"])).name if index else None
            ),
            next_href=(
                Path(str(quiz_groups[index + 1]["href"])).name
                if index + 1 < len(quiz_groups)
                else None
            ),
        )
        shard_descriptors.append(
            _write_text_artifact(
                output_dir,
                relative_path,
                content,
                kind="quizzes",
                record_count=int(group["occurrence_count"]),
            )
        )

    workbook_export = _write_workbook_export(
        output_dir, [_as_dict(row) for row in _as_list(data.get("artifacts"))]
    )
    if workbook_export:
        shard_descriptors.append(workbook_export)
    manifest = {
        "format": data.get("format"),
        "share_safety": data.get("share_safety"),
        "boundary": data.get("boundary"),
        "model": data.get("model"),
        "summary": data.get("summary"),
        "queue_summary": data.get("queue_summary"),
        "artifacts": data.get("artifacts"),
        "storage": {
            "mode": "reference_shards",
            "authored_question_content": "question_html_once",
            "occurrences": "quiz_pages_with_placement_evidence_and_question_reference_only",
            "shared_records": "catalog_html_once",
            "navigation": "quiz_first",
            "workbook_export": "verified_local_zip_when_available",
            "file_protocol_fallback": True,
            "requires_host": False,
            "requires_javascript": False,
        },
        "exports": {
            "workbooks": (
                {
                    "path": workbook_export.get("path"),
                    "file_count": workbook_export.get("record_count"),
                    "filenames": workbook_export.get("filenames"),
                    "bytes": workbook_export.get("bytes"),
                    "sha256": workbook_export.get("sha256"),
                }
                if workbook_export
                else None
            )
        },
        "quizzes": [
            {
                "quiz_entity_key": row.get("quiz_entity_key"),
                "title": row.get("title"),
                "ordinal": row.get("ordinal"),
                "href": row.get("href"),
                "occurrence_count": row.get("occurrence_count"),
                "unique_question_count": row.get("unique_question_count"),
                "section_count": row.get("section_count"),
                "pool_count": row.get("pool_count"),
                "review_occurrence_count": row.get("review_occurrence_count"),
            }
            for row in quiz_groups
        ],
        "question_entities": [
            {
                "entity_key": row.get("entity_key"),
                "occurrence_count": _as_dict(row.get("extraction_fidelity")).get(
                    "occurrence_count"
                ),
                "authored_variant": row.get("authored_variant"),
                "href": question_hrefs.get(str(row.get("entity_key")), "").removeprefix(
                    "../"
                ),
            }
            for row in entities
        ],
        "question_occurrences": [
            {
                "occurrence_key": row.get("occurrence_key"),
                "question_entity_key": row.get("question_entity_key"),
                "match_status": row.get("match_status"),
                "match_status_code": row.get("match_status_code"),
                "quiz_entity_key": row.get("quiz_entity_key"),
                "quiz_title": row.get("quiz_title"),
                "href": occurrence_href_by_key.get(str(row.get("occurrence_key"))),
            }
            for row in occurrences
        ],
        "catalogs": {
            kind: {
                "record_count": sum(
                    descriptor["record_count"]
                    for descriptor in shard_descriptors
                    if descriptor["kind"] == f"catalog:{kind}"
                ),
                "paths": catalog_paths[kind],
            }
            for kind in ("assets", "evidence", "diagnostics", "lineage")
        },
        "shards": shard_descriptors,
    }
    (output_dir / "station.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(
        _render_station_index(manifest), encoding="utf-8"
    )
    return data


def _parse_artifact_links(values: list[str]) -> dict[str, str]:
    links: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("artifact links must use NAME=RELATIVE_PATH")
        name, href = value.split("=", 1)
        if name in links:
            raise ValueError(f"duplicate artifact link name: {name}")
        links[name] = href
    return links


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a content-rich, local-only quiz review station."
    )
    parser.add_argument("model", help="coursecraft.quiz/1 JSON model")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--artifact-link",
        action="append",
        default=[],
        metavar="NAME=RELATIVE_PATH",
        help="Optional link kept within the local run directory (repeatable).",
    )
    args = parser.parse_args()
    try:
        links = _parse_artifact_links(args.artifact_link)
        data = write_quiz_review_station(
            Path(args.model), Path(args.output_dir), artifact_links=links
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "format": data["format"],
                "output": "index.html",
                "unique_question_count": data["summary"]["unique_question_count"],
                "occurrence_count": data["summary"]["occurrence_count"],
                "share_safe": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
