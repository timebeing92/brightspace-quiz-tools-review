#!/usr/bin/env python3
"""Project a normalized quiz model into linked reviewer-facing read models.

``coursecraft.quiz/1`` intentionally stores unique question entities separately
from relationship facts.  A reviewer, however, needs to see both the unique
question and every place it occurs.  This module builds that additive read
model without changing, repairing, or otherwise mutating the source model.

The projection identifier remains version ``0`` while the Unbind contract is
under review.  It is an internal read-model marker, not a promoted contract.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Iterable


PROJECTION_SCHEMA = "coursecraft.quiz_review_projection/0"
SOURCE_SCHEMA = "coursecraft.quiz/1"
AUTHORED_VARIANT_BASIS = "authored variant"
AUTHORED_VARIANT_EQUIVALENCE = "authored_variant_equivalence"
LIBRARY_VARIANT_EQUIVALENCE = "library_variant_equivalence"
_QUIZ_ITEM_LOCATOR = re.compile(r"^quiz item\s+(\d+)$", re.IGNORECASE)
_PLACEMENT_KINDS = {"contains", "itemref"}
_SECTION_KINDS = {"quiz_root", "section", "draw", "unknown"}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.isdigit():
            return int(stripped)
    return None


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _entity_aliases(entity: dict[str, Any], namespace: str) -> list[str]:
    return [
        _as_text(alias.get("value"))
        for alias in _list(_dict(entity.get("identity")).get("source_aliases"))
        if isinstance(alias, dict)
        and alias.get("namespace") == namespace
        and _as_text(alias.get("value"))
    ]


def _section_source_alias(
    structure: dict[str, Any],
) -> tuple[str | None, str | None]:
    for value in _entity_aliases(structure, "brightspace.quiz_section_path"):
        if "::" in value:
            quiz_file, path = value.split("::", 1)
            return quiz_file or None, path or None
    return None, None


def _occurrence_key(relationship_key: str) -> str:
    if relationship_key.startswith("cc:relationship:"):
        return "cc:occurrence:" + relationship_key.removeprefix("cc:relationship:")
    token = hashlib.sha256(relationship_key.encode("utf-8")).hexdigest()[:24]
    return f"cc:occurrence:{token}"


def _projection_diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    occurrence_key: str | None = None,
    entity_keys: Iterable[str] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail_value = details or {}
    entity_key_values = list(entity_keys)
    basis = json.dumps(
        [code, occurrence_key, sorted(entity_key_values), detail_value],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    token = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return {
        "diagnostic_id": f"projection.{code}.{token}",
        "severity": severity,
        "code": code,
        "message": message,
        "occurrence_key": occurrence_key,
        "entity_keys": _stable_unique(entity_key_values),
        "details": detail_value,
    }


def _require_model_shape(model: dict[str, Any]) -> None:
    if not isinstance(model, dict) or model.get("schema") != SOURCE_SCHEMA:
        actual = (
            model.get("schema") if isinstance(model, dict) else type(model).__name__
        )
        raise ValueError(
            f"Quiz review projection requires {SOURCE_SCHEMA}; received {actual!r}."
        )
    for field in (
        "quizzes",
        "structures",
        "questions",
        "assets",
        "relationships",
        "evidence",
        "diagnostics",
    ):
        if not isinstance(model.get(field), list):
            raise ValueError(
                f"Quiz review projection requires model.{field} to be an array."
            )


def _canonical_occurrence_records(
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for question in questions:
        question_key = _as_text(question.get("entity_key"))
        type_payload = _dict(question.get("type_payload"))
        for raw_model in _list(type_payload.get("raw_response_models")):
            if not isinstance(raw_model, dict):
                continue
            source_kind = _as_text(raw_model.get("source_kind"))
            if not source_kind.startswith("canonical-extractor-row:"):
                continue
            payload = raw_model.get("payload")
            if not isinstance(payload, dict):
                continue
            records.append(
                {
                    "question_key": question_key,
                    "quiz_file": _as_text(payload.get("quiz_file")),
                    "quiz_item_number": _as_int(payload.get("quiz_item_number")),
                    "section_item_number": _as_int(
                        payload.get("quiz_section_item_number")
                    ),
                    "section_path": _as_text(payload.get("section_path")),
                    "source_evidence_keys": _stable_unique(
                        str(value)
                        for value in _list(raw_model.get("source_evidence_keys"))
                    ),
                    "payload": payload,
                }
            )
    return records


def _relationship_occurrence_locator(
    relationship: dict[str, Any], evidence_by_key: dict[str, dict[str, Any]]
) -> tuple[str | None, int | None, list[str]]:
    occurrence_evidence = _stable_unique(
        str(value)
        for value in _list(relationship.get("source_evidence_keys"))
        if str(value).startswith("ev.occurrence.")
    )
    source_files: set[str] = set()
    item_numbers: set[int] = set()
    for evidence_key in occurrence_evidence:
        evidence = evidence_by_key.get(evidence_key, {})
        source_ref = _as_text(evidence.get("source_ref"))
        if source_ref:
            source_files.add(source_ref)
        locator = _as_text(evidence.get("locator"))
        match = _QUIZ_ITEM_LOCATOR.match(locator)
        if match:
            item_numbers.add(int(match.group(1)))
    source_file = next(iter(source_files)) if len(source_files) == 1 else None
    item_number = next(iter(item_numbers)) if len(item_numbers) == 1 else None
    return source_file, item_number, occurrence_evidence


def _select_canonical_record(
    records: list[dict[str, Any]],
    *,
    target_question_key: str | None,
    source_file: str | None,
    item_number: int | None,
    occurrence_evidence: list[str],
    section_item_number: int | None,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = records
    if source_file and item_number is not None:
        exact = [
            record
            for record in candidates
            if record["quiz_file"] == source_file
            and record["quiz_item_number"] == item_number
        ]
        if len(exact) == 1:
            return exact[0], "evidence_locator"
        if target_question_key:
            targeted = [
                record
                for record in exact
                if record["question_key"] == target_question_key
            ]
            if len(targeted) == 1:
                return targeted[0], "evidence_locator_and_question"
        if len(exact) > 1:
            return None, "ambiguous_evidence_locator"

    evidence_set = set(occurrence_evidence)
    by_evidence = [
        record
        for record in candidates
        if evidence_set.intersection(record["source_evidence_keys"])
    ]
    if target_question_key:
        targeted = [
            record
            for record in by_evidence
            if record["question_key"] == target_question_key
        ]
        if len(targeted) == 1:
            return targeted[0], "occurrence_evidence_and_question"
    if len(by_evidence) == 1:
        return by_evidence[0], "occurrence_evidence"

    if target_question_key and section_item_number is not None:
        by_ordinal = [
            record
            for record in candidates
            if record["question_key"] == target_question_key
            and record["section_item_number"] == section_item_number
        ]
        if len(by_ordinal) == 1:
            return by_ordinal[0], "question_and_section_ordinal"
    return None, "ambiguous" if by_evidence else "unavailable"


def _relationship_pool_descriptor(
    relationship: dict[str, Any], structure_by_key: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    pool_key = relationship.get("to_entity_key")
    pool = structure_by_key.get(pool_key, {}) if isinstance(pool_key, str) else {}
    selection = _dict(pool.get("selection"))
    legacy_pool = _dict(_dict(pool.get("extensions")).get("legacy_pool"))
    attributes = _dict(relationship.get("attributes"))
    evidence_level = _as_text(attributes.get("evidence_level"))
    return {
        "pool_key": pool_key if isinstance(pool_key, str) else None,
        "pool_title": pool.get("title"),
        "pool_path": legacy_pool.get("pool_path"),
        "pool_ident": legacy_pool.get("pool_ident"),
        "relationship_key": relationship.get("relationship_key"),
        "relationship_status": relationship.get("status"),
        "source_kind": relationship.get("source_kind"),
        "evidence_level": evidence_level or None,
        "match_status": _library_match_status(
            evidence_level, _as_text(relationship.get("status"))
        ),
        "match_score": attributes.get("match_score"),
        "note": attributes.get("note"),
        "candidates": deepcopy(
            [
                row
                for row in _list(attributes.get("candidates"))
                if isinstance(row, dict)
            ]
        ),
        "available_count": selection.get("available_count"),
        "legacy_question_count": legacy_pool.get("question_count"),
        "basis": "member_of_relationship",
    }


def _variant_classes(
    questions: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Group questions into authored-variant classes.

    Identity stays per source item; sameness of authored content is read back
    from the recorded ``same_as`` equivalence rather than recomputed here, so
    the reviewer-facing grouping and the model agree by construction.
    """
    representative_by_key: dict[str, str] = {}
    for question in questions:
        key = _as_text(question.get("entity_key"))
        if key:
            representative_by_key[key] = key
    for relationship in relationships:
        if relationship.get("kind") != "same_as":
            continue
        if relationship.get("source_kind") != AUTHORED_VARIANT_EQUIVALENCE:
            continue
        member = _as_text(relationship.get("from_entity_key"))
        representative = _as_text(relationship.get("to_entity_key"))
        if member in representative_by_key and representative:
            representative_by_key[member] = representative
    members_by_representative: dict[str, list[str]] = defaultdict(list)
    for key, representative in representative_by_key.items():
        members_by_representative[representative].append(key)
    return representative_by_key, {
        representative: sorted(members)
        for representative, members in members_by_representative.items()
    }


def _authored_variant_digest(question: dict[str, Any]) -> str | None:
    for fingerprint in _list(_dict(question.get("identity")).get("content_fingerprints")):
        if isinstance(fingerprint, dict) and fingerprint.get("basis") == (
            AUTHORED_VARIANT_BASIS
        ):
            return _as_text(fingerprint.get("digest")) or None
    return None


def _library_match_status(evidence_level: str, relationship_status: str) -> str:
    if relationship_status == "ambiguous":
        return "ambiguous"
    return {
        "source_evidence": "direct",
        "inferred_exact": "inferred_exact",
        "inferred_similarity": "inferred_similarity",
        "unmatched": "unresolved",
    }.get(evidence_level, "unknown")


def _pool_index(
    structures: list[dict[str, Any]],
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, list[str]],
]:
    by_ident: dict[str, list[str]] = defaultdict(list)
    by_title: dict[str, list[str]] = defaultdict(list)
    by_path: dict[str, list[str]] = defaultdict(list)
    for structure in structures:
        if structure.get("kind") not in {"pool", "bank"}:
            continue
        key = _as_text(structure.get("entity_key"))
        legacy_pool = _dict(_dict(structure.get("extensions")).get("legacy_pool"))
        ident = _as_text(legacy_pool.get("pool_ident"))
        title = _as_text(structure.get("title")) or _as_text(
            legacy_pool.get("pool_title")
        )
        path = _as_text(legacy_pool.get("pool_path"))
        if ident:
            by_ident[ident].append(key)
        if title:
            by_title[title].append(key)
        if path:
            by_path[path].append(key)
    return by_ident, by_title, by_path


def _raw_pool_keys(
    raw_row: dict[str, Any],
    by_ident: dict[str, list[str]],
    by_title: dict[str, list[str]],
    by_path: dict[str, list[str]],
) -> tuple[list[str], str | None]:
    ident = _as_text(raw_row.get("pool_ident"))
    title = _as_text(raw_row.get("pool_title"))
    path = _as_text(raw_row.get("pool_path"))
    for value, index, basis in (
        (ident, by_ident, "pool_ident"),
        (path, by_path, "pool_path"),
        (title, by_title, "pool_title"),
    ):
        if value and value in index:
            return _stable_unique(index[value]), basis
    return [], None


def _raw_pool_descriptor(
    raw_row: dict[str, Any],
    pool_key: str | None,
    pool: dict[str, Any],
    *,
    basis: str,
) -> dict[str, Any]:
    selection = _dict(pool.get("selection"))
    legacy_pool = _dict(_dict(pool.get("extensions")).get("legacy_pool"))
    evidence_level = _as_text(raw_row.get("evidence_level"))
    raw_candidates = raw_row.get("match_candidates_json")
    candidates: list[dict[str, Any]] = []
    if isinstance(raw_candidates, str) and raw_candidates.strip():
        try:
            decoded = json.loads(raw_candidates)
        except json.JSONDecodeError:
            decoded = []
        candidates = [row for row in _list(decoded) if isinstance(row, dict)]
    elif isinstance(raw_candidates, list):
        candidates = [row for row in raw_candidates if isinstance(row, dict)]
    return {
        "pool_key": pool_key,
        "pool_title": _as_text(raw_row.get("pool_title")) or pool.get("title"),
        "pool_path": _as_text(raw_row.get("pool_path")) or legacy_pool.get("pool_path"),
        "pool_ident": _as_text(raw_row.get("pool_ident"))
        or legacy_pool.get("pool_ident"),
        "relationship_key": None,
        "relationship_status": None,
        "source_kind": _as_text(raw_row.get("match_basis")) or None,
        "evidence_level": evidence_level or None,
        "match_status": _library_match_status(evidence_level, ""),
        "match_score": raw_row.get("match_score"),
        "note": raw_row.get("match_note"),
        "candidates": deepcopy(candidates),
        "available_count": selection.get("available_count"),
        "legacy_question_count": legacy_pool.get("question_count"),
        "basis": f"canonical_extractor_row:{basis}",
    }


def _source_diagnostics_for_occurrence(
    *,
    relationship: dict[str, Any],
    question: dict[str, Any] | None,
    section: dict[str, Any] | None,
    quiz: dict[str, Any] | None,
    occurrence_evidence: list[str],
    diagnostic_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ids: list[str] = []
    ids.extend(str(value) for value in _list(relationship.get("diagnostic_ids")))
    for entity in (question, section, quiz):
        if entity:
            ids.extend(str(value) for value in _list(entity.get("diagnostic_ids")))
    result: list[dict[str, Any]] = []
    evidence_set = set(occurrence_evidence)
    for diagnostic_id in _stable_unique(ids):
        diagnostic = diagnostic_by_id.get(diagnostic_id)
        if not diagnostic:
            continue
        diagnostic_evidence = set(
            str(value) for value in _list(diagnostic.get("evidence_keys"))
        )
        if (
            diagnostic_evidence
            and evidence_set
            and not diagnostic_evidence.intersection(evidence_set)
        ):
            continue
        result.append(deepcopy(diagnostic))
    return result


def _ancestor_quizzes(
    start_key: str,
    *,
    quiz_keys: set[str],
    parents: dict[str, list[str]],
) -> set[str]:
    found: set[str] = set()
    queue = [start_key]
    visited: set[str] = set()
    while queue:
        key = queue.pop()
        if key in visited:
            continue
        visited.add(key)
        if key in quiz_keys:
            found.add(key)
            continue
        queue.extend(parents.get(key, []))
    return found


def _ancestor_draws(
    start_key: str,
    *,
    structure_by_key: dict[str, dict[str, Any]],
    parents: dict[str, list[str]],
) -> list[str]:
    result: list[str] = []
    queue = [start_key]
    visited: set[str] = set()
    while queue:
        key = queue.pop(0)
        if key in visited:
            continue
        visited.add(key)
        structure = structure_by_key.get(key)
        if structure and structure.get("kind") == "draw":
            result.append(key)
        queue.extend(parents.get(key, []))
    return result


def _quiz_context_candidates(
    structure_key: str,
    *,
    structure_by_key: dict[str, dict[str, Any]],
    quiz_keys: set[str],
    quiz_key_by_file: dict[str, str],
    parents: dict[str, list[str]],
    evidence_by_key: dict[str, dict[str, Any]],
) -> set[str]:
    candidates = _ancestor_quizzes(structure_key, quiz_keys=quiz_keys, parents=parents)
    structure = structure_by_key.get(structure_key)
    if not structure:
        return candidates
    quiz_file, _path = _section_source_alias(structure)
    if quiz_file and quiz_file in quiz_key_by_file:
        candidates.add(quiz_key_by_file[quiz_file])
    for evidence_key in _list(structure.get("source_evidence_keys")):
        source_ref = _as_text(
            evidence_by_key.get(str(evidence_key), {}).get("source_ref")
        )
        if source_ref in quiz_key_by_file:
            candidates.add(quiz_key_by_file[source_ref])
    return candidates


def _selection_context(
    *,
    section_key: str | None,
    pool_memberships: list[dict[str, Any]],
    structure_by_key: dict[str, dict[str, Any]],
    parents: dict[str, list[str]],
    draws_from_by_draw: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if not section_key:
        return {
            "mode": "unknown",
            "is_random_draw": False,
            "draw_structure_key": None,
            "draw_count": None,
            "section_available_count": None,
            "candidate_pool_size": None,
            "pool_keys": [],
            "pool_context_basis": "unavailable",
        }
    draw_keys = _ancestor_draws(
        section_key, structure_by_key=structure_by_key, parents=parents
    )
    draw_key = draw_keys[0] if len(draw_keys) == 1 else None
    context_structure = structure_by_key.get(draw_key or section_key, {})
    selection = _dict(context_structure.get("selection"))
    draws_from = draws_from_by_draw.get(draw_key or "", [])
    resolved_draw_pools = _stable_unique(
        str(row.get("to_entity_key"))
        for row in draws_from
        if row.get("status") == "resolved" and row.get("to_entity_key")
    )
    membership_pools = _stable_unique(
        str(row.get("pool_key")) for row in pool_memberships if row.get("pool_key")
    )
    if resolved_draw_pools:
        pool_keys = resolved_draw_pools
        basis = "draws_from_relationship"
    elif draw_key and membership_pools:
        pool_keys = membership_pools
        basis = "occurrence_pool_membership"
    else:
        pool_keys = []
        basis = "none"
    is_random = bool(draw_key) or selection.get("mode") == "random"
    return {
        "mode": selection.get("mode") or "unknown",
        "is_random_draw": is_random,
        "draw_structure_key": draw_key,
        "draw_count": selection.get("requested_count") if is_random else None,
        "section_available_count": selection.get("available_count"),
        "candidate_pool_size": selection.get("available_count") if is_random else None,
        "pool_keys": pool_keys,
        "pool_context_basis": basis,
    }


def build_quiz_review_projection(
    model: dict[str, Any], *, include_content: bool = False
) -> dict[str, Any]:
    """Return linked unique-question and occurrence views for a quiz model.

    The source model is treated as immutable.  Structural uncertainty is left
    explicit through projection diagnostics instead of being repaired by
    assumption.

    By default this is a reference projection: authored questions, assets,
    source evidence, and source diagnostics remain canonical in the source
    model and are named here only by stable keys.  ``include_content=True`` is
    retained for in-process consumers, such as the legacy reviewer-workbook
    writer, that still need an embedded compatibility view.  Serialized
    Unbind projections should use the compact default.
    """

    _require_model_shape(model)
    quizzes = [row for row in model["quizzes"] if isinstance(row, dict)]
    structures = [row for row in model["structures"] if isinstance(row, dict)]
    # Question-library records are coverage entities: they exist so a match has
    # a target and so library coverage can be counted honestly, but they carry
    # no extracted answer payload and must not be offered to a reviewer as
    # questions to review.
    all_questions = [row for row in model["questions"] if isinstance(row, dict)]
    library_questions = [
        row
        for row in all_questions
        if _dict(row.get("extensions")).get("library_only")
    ]
    questions = [
        row
        for row in all_questions
        if not _dict(row.get("extensions")).get("library_only")
    ]
    relationships = [row for row in model["relationships"] if isinstance(row, dict)]
    evidence = [row for row in model["evidence"] if isinstance(row, dict)]
    source_diagnostics = [row for row in model["diagnostics"] if isinstance(row, dict)]

    quiz_by_key = {
        _as_text(row.get("entity_key")): row
        for row in quizzes
        if _as_text(row.get("entity_key"))
    }
    quiz_order = {key: index for index, key in enumerate(quiz_by_key, start=1)}
    quiz_file_by_key: dict[str, str] = {}
    quiz_key_by_file: dict[str, str] = {}
    for key, quiz in quiz_by_key.items():
        aliases = _entity_aliases(quiz, "brightspace.quiz_file")
        if aliases:
            quiz_file_by_key[key] = aliases[0]
            quiz_key_by_file[aliases[0]] = key

    structure_by_key = {
        _as_text(row.get("entity_key")): row
        for row in structures
        if _as_text(row.get("entity_key"))
    }
    question_by_key = {
        _as_text(row.get("entity_key")): row
        for row in questions
        if _as_text(row.get("entity_key"))
    }
    asset_by_key = {
        _as_text(row.get("entity_key")): row
        for row in model["assets"]
        if isinstance(row, dict) and _as_text(row.get("entity_key"))
    }
    evidence_by_key = {
        _as_text(row.get("evidence_key")): row
        for row in evidence
        if _as_text(row.get("evidence_key"))
    }
    diagnostic_by_id = {
        _as_text(row.get("diagnostic_id")): row
        for row in source_diagnostics
        if _as_text(row.get("diagnostic_id"))
    }

    parents: dict[str, list[str]] = defaultdict(list)
    for relationship in relationships:
        if (
            relationship.get("kind") == "contains"
            and relationship.get("status") == "resolved"
            and relationship.get("to_entity_key") in structure_by_key
        ):
            child = str(relationship["to_entity_key"])
            parent = _as_text(relationship.get("from_entity_key"))
            if parent:
                parents[child].append(parent)

    structure_quiz_candidates: dict[str, set[str]] = {}
    for key, structure in structure_by_key.items():
        if structure.get("kind") not in _SECTION_KINDS:
            continue
        structure_quiz_candidates[key] = _quiz_context_candidates(
            key,
            structure_by_key=structure_by_key,
            quiz_keys=set(quiz_by_key),
            quiz_key_by_file=quiz_key_by_file,
            parents=parents,
            evidence_by_key=evidence_by_key,
        )

    section_order_by_quiz: dict[tuple[str, str], int] = {}
    section_counts: dict[str, int] = defaultdict(int)
    for structure in structures:
        key = _as_text(structure.get("entity_key"))
        candidates = structure_quiz_candidates.get(key, set())
        if len(candidates) != 1:
            continue
        quiz_key = next(iter(candidates))
        section_counts[quiz_key] += 1
        section_order_by_quiz[(quiz_key, key)] = section_counts[quiz_key]

    canonical_records = _canonical_occurrence_records(questions)
    by_pool_ident, by_pool_title, by_pool_path = _pool_index(structures)
    member_of_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    uses_asset_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    draws_from_by_draw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relationship in relationships:
        from_key = _as_text(relationship.get("from_entity_key"))
        if relationship.get("kind") == "member_of":
            member_of_by_question[from_key].append(relationship)
        elif relationship.get("kind") == "uses_asset":
            uses_asset_by_question[from_key].append(relationship)
        elif relationship.get("kind") == "draws_from":
            draws_from_by_draw[from_key].append(relationship)

    projection_diagnostics: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    relationship_order = {
        _as_text(row.get("relationship_key")): index
        for index, row in enumerate(relationships, start=1)
    }

    for relationship in relationships:
        if relationship.get("kind") not in _PLACEMENT_KINDS:
            continue
        from_key = _as_text(relationship.get("from_entity_key"))
        target_key = relationship.get("to_entity_key")
        from_is_quiz = from_key in quiz_by_key
        from_is_section = (
            from_key in structure_by_key
            and structure_by_key[from_key].get("kind") in _SECTION_KINDS
        )
        target_is_question = (
            isinstance(target_key, str) and target_key in question_by_key
        )
        if not (from_is_quiz or from_is_section):
            continue
        if target_key is not None and not target_is_question:
            continue

        relationship_key = _as_text(relationship.get("relationship_key"))
        occurrence_key = _occurrence_key(relationship_key)
        source_file, locator_item_number, occurrence_evidence = (
            _relationship_occurrence_locator(relationship, evidence_by_key)
        )
        section_item_number = _as_int(relationship.get("ordinal"))
        relationship_target = str(target_key) if target_is_question else None
        raw_record, raw_record_basis = _select_canonical_record(
            canonical_records,
            target_question_key=relationship_target,
            source_file=source_file,
            item_number=locator_item_number,
            occurrence_evidence=occurrence_evidence,
            section_item_number=section_item_number,
        )
        raw_row = raw_record["payload"] if raw_record else {}
        observed_question_key = (
            raw_record["question_key"] if raw_record else relationship_target
        )
        referenced_question_key = (
            relationship_target if relationship.get("status") == "resolved" else None
        )

        local_projection_diagnostics: list[dict[str, Any]] = []
        if relationship.get("status") != "resolved" or not referenced_question_key:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "occurrence_question_unresolved",
                    "The placement is preserved, but its question reference is not resolved.",
                    occurrence_key=occurrence_key,
                    entity_keys=[from_key],
                    details={
                        "relationship_key": relationship_key,
                        "relationship_status": relationship.get("status"),
                        "observed_question_entity_key": observed_question_key,
                    },
                )
            )
        if not raw_record:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "canonical_occurrence_row_unavailable",
                    "No unique canonical extractor row could be joined to this placement.",
                    severity="info",
                    occurrence_key=occurrence_key,
                    entity_keys=[key for key in (from_key, relationship_target) if key],
                    details={"selection_basis": raw_record_basis},
                )
            )

        raw_quiz_file = _as_text(raw_row.get("quiz_file"))
        quiz_candidates: set[str] = set()
        if from_is_quiz:
            quiz_candidates.add(from_key)
        elif from_is_section:
            quiz_candidates.update(structure_quiz_candidates.get(from_key, set()))
        for quiz_file in (source_file, raw_quiz_file):
            if quiz_file and quiz_file in quiz_key_by_file:
                quiz_candidates.add(quiz_key_by_file[quiz_file])
        quiz_key = next(iter(quiz_candidates)) if len(quiz_candidates) == 1 else None
        if len(quiz_candidates) != 1:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    (
                        "occurrence_quiz_unresolved"
                        if not quiz_candidates
                        else "occurrence_quiz_ambiguous"
                    ),
                    (
                        "Quiz context could not be resolved for this placement."
                        if not quiz_candidates
                        else "Conflicting quiz context was preserved for this placement."
                    ),
                    occurrence_key=occurrence_key,
                    entity_keys=[from_key, *sorted(quiz_candidates)],
                    details={"candidate_quiz_keys": sorted(quiz_candidates)},
                )
            )

        section_key = from_key if from_is_section else None
        section = structure_by_key.get(section_key or "")
        _section_file, section_path = (
            _section_source_alias(section) if section else (None, None)
        )
        raw_section_path = _as_text(raw_row.get("section_path"))
        if raw_section_path and section_path and raw_section_path != section_path:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "occurrence_section_conflict",
                    "The placement relationship and canonical row name different section paths.",
                    occurrence_key=occurrence_key,
                    entity_keys=[section_key] if section_key else [],
                    details={
                        "relationship_section_path": section_path,
                        "canonical_section_path": raw_section_path,
                    },
                )
            )
        section_path = section_path or raw_section_path or None
        section_order = (
            section_order_by_quiz.get((quiz_key, section_key))
            if quiz_key and section_key
            else None
        )
        if section_key and quiz_key and section_order is None:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "section_order_unresolved",
                    "The section is known, but its source sequence within the quiz is unresolved.",
                    occurrence_key=occurrence_key,
                    entity_keys=[quiz_key, section_key],
                )
            )

        quiz_item_number = _as_int(raw_row.get("quiz_item_number"))
        if quiz_item_number is None:
            quiz_item_number = locator_item_number
        order_basis = (
            "canonical_extractor_row"
            if _as_int(raw_row.get("quiz_item_number")) is not None
            else (
                "occurrence_evidence_locator"
                if locator_item_number is not None
                else None
            )
        )
        if quiz_item_number is None:
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "occurrence_order_unresolved",
                    "Global question order within the quiz is not represented for this placement.",
                    occurrence_key=occurrence_key,
                    entity_keys=[key for key in (quiz_key, section_key) if key],
                )
            )

        pool_memberships: list[dict[str, Any]] = []
        raw_pool_present = any(
            _as_text(raw_row.get(field))
            for field in ("pool_ident", "pool_title", "pool_path")
        )
        if raw_pool_present:
            pool_keys, pool_basis = _raw_pool_keys(
                raw_row, by_pool_ident, by_pool_title, by_pool_path
            )
            if len(pool_keys) == 1:
                pool_key = pool_keys[0]
                descriptor = _raw_pool_descriptor(
                    raw_row,
                    pool_key,
                    structure_by_key.get(pool_key, {}),
                    basis=pool_basis or "unresolved_key",
                )
                matching_relationships = [
                    row
                    for row in member_of_by_question.get(
                        observed_question_key or "", []
                    )
                    if row.get("to_entity_key") == pool_key
                    and set(occurrence_evidence).intersection(
                        str(value) for value in _list(row.get("source_evidence_keys"))
                    )
                ]
                if matching_relationships:
                    descriptor["relationship_key"] = matching_relationships[-1].get(
                        "relationship_key"
                    )
                    descriptor["relationship_status"] = matching_relationships[-1].get(
                        "status"
                    )
                pool_memberships.append(descriptor)
            elif len(pool_keys) > 1:
                local_projection_diagnostics.append(
                    _projection_diagnostic(
                        "occurrence_pool_ambiguous",
                        "The canonical pool identity matches more than one normalized pool.",
                        occurrence_key=occurrence_key,
                        entity_keys=pool_keys,
                        details={"candidate_pool_keys": pool_keys},
                    )
                )
            else:
                pool_memberships.append(
                    _raw_pool_descriptor(raw_row, None, {}, basis="unresolved_pool_key")
                )
                local_projection_diagnostics.append(
                    _projection_diagnostic(
                        "occurrence_pool_unresolved",
                        "Pool source facts were preserved, but no normalized pool key could be joined.",
                        occurrence_key=occurrence_key,
                        entity_keys=(
                            [observed_question_key] if observed_question_key else []
                        ),
                        details={
                            "pool_ident": raw_row.get("pool_ident"),
                            "pool_title": raw_row.get("pool_title"),
                            "pool_path": raw_row.get("pool_path"),
                        },
                    )
                )
        elif observed_question_key:
            candidates: list[tuple[int, dict[str, Any]]] = []
            occurrence_evidence_set = set(occurrence_evidence)
            for membership in member_of_by_question.get(observed_question_key, []):
                membership_evidence = {
                    str(value)
                    for value in _list(membership.get("source_evidence_keys"))
                    if str(value).startswith("ev.occurrence.")
                }
                overlap = len(occurrence_evidence_set.intersection(membership_evidence))
                if not overlap:
                    continue
                score = 3 if membership_evidence == occurrence_evidence_set else 1
                if membership.get("source_kind") == relationship.get("source_kind"):
                    score += 1
                candidates.append((score, membership))
            if candidates:
                best_score = max(score for score, _row in candidates)
                best = [row for score, row in candidates if score == best_score]
                pool_memberships = [
                    _relationship_pool_descriptor(row, structure_by_key) for row in best
                ]
                distinct_pool_keys = {row.get("pool_key") for row in pool_memberships}
                if len(distinct_pool_keys) > 1:
                    local_projection_diagnostics.append(
                        _projection_diagnostic(
                            "occurrence_pool_ambiguous",
                            "More than one occurrence-scoped pool relationship remains applicable.",
                            occurrence_key=occurrence_key,
                            entity_keys=[str(key) for key in distinct_pool_keys if key],
                        )
                    )

        selection_context = _selection_context(
            section_key=section_key,
            pool_memberships=pool_memberships,
            structure_by_key=structure_by_key,
            parents=parents,
            draws_from_by_draw=draws_from_by_draw,
        )
        if (
            selection_context["is_random_draw"]
            and len(selection_context["pool_keys"]) > 1
        ):
            local_projection_diagnostics.append(
                _projection_diagnostic(
                    "draw_pool_context_ambiguous",
                    "The random-draw occurrence reaches more than one candidate pool.",
                    occurrence_key=occurrence_key,
                    entity_keys=selection_context["pool_keys"],
                )
            )

        source_question = question_by_key.get(
            referenced_question_key or observed_question_key or ""
        )
        source_quiz = quiz_by_key.get(quiz_key or "")
        source_diagnostic_rows = _source_diagnostics_for_occurrence(
            relationship=relationship,
            question=source_question,
            section=section,
            quiz=source_quiz,
            occurrence_evidence=occurrence_evidence,
            diagnostic_by_id=diagnostic_by_id,
        )
        projection_diagnostics.extend(local_projection_diagnostics)
        source_diagnostic_ids = _stable_unique(
            str(row.get("diagnostic_id")) for row in source_diagnostic_rows
        )
        projection_diagnostic_ids = _stable_unique(
            str(row.get("diagnostic_id")) for row in local_projection_diagnostics
        )
        occurrence = {
            "occurrence_key": occurrence_key,
            "relationship_key": relationship_key,
            "relationship_status": relationship.get("status"),
            "referenced_question_key": referenced_question_key,
            "observed_question_entity_key": observed_question_key,
            "quiz_key": quiz_key,
            "quiz_title": source_quiz.get("title") if source_quiz else None,
            "quiz_file": (
                quiz_file_by_key.get(quiz_key or "")
                or source_file
                or raw_quiz_file
                or None
            ),
            "quiz_order": quiz_order.get(quiz_key or ""),
            "quiz_occurrence_order": quiz_item_number,
            "quiz_occurrence_order_basis": order_basis,
            "section_key": section_key,
            "section_title": section.get("title") if section else None,
            "section_path": section_path,
            "section_order": section_order,
            "section_order_basis": "model_sequence" if section_order else None,
            "section_level": section.get("ordinal") if section else None,
            "section_occurrence_order": section_item_number,
            "placement_kind": (
                "itemref" if relationship.get("kind") == "itemref" else "inline"
            ),
            "source_kind": relationship.get("source_kind"),
            "points": raw_row.get("question_weight"),
            "library_match_status": _library_match_status(
                _as_text(raw_row.get("evidence_level")),
                _as_text(relationship.get("status")),
            ),
            "pool_memberships": pool_memberships,
            "selection_context": selection_context,
            "source_evidence_keys": occurrence_evidence,
            "source_diagnostic_ids": source_diagnostic_ids,
            "projection_diagnostic_ids": projection_diagnostic_ids,
            "diagnostic_ids": _stable_unique(
                [
                    *source_diagnostic_ids,
                    *projection_diagnostic_ids,
                ]
            ),
            "extensions": {
                "canonical_row_join_basis": raw_record_basis,
                "model_relationship_order": relationship_order.get(relationship_key),
            },
        }
        if include_content:
            occurrence["source_diagnostics"] = source_diagnostic_rows
            occurrence["projection_diagnostics"] = local_projection_diagnostics
        occurrences.append(occurrence)

    def occurrence_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
        fallback = 10**9
        return (
            row.get("quiz_order") or fallback,
            row.get("quiz_occurrence_order") or fallback,
            row.get("section_order") or fallback,
            row.get("section_occurrence_order") or fallback,
            _dict(row.get("extensions")).get("model_relationship_order") or fallback,
        )

    occurrences.sort(key=occurrence_sort_key)
    quiz_projection_counts: dict[str, int] = defaultdict(int)
    order_index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for projection_order, occurrence in enumerate(occurrences, start=1):
        occurrence["projection_order"] = projection_order
        quiz_key = occurrence.get("quiz_key")
        if isinstance(quiz_key, str):
            quiz_projection_counts[quiz_key] += 1
            occurrence["quiz_projection_order"] = quiz_projection_counts[quiz_key]
            quiz_occurrence_order = occurrence.get("quiz_occurrence_order")
            if isinstance(quiz_occurrence_order, int):
                order_index[(quiz_key, quiz_occurrence_order)].append(occurrence)
        else:
            occurrence["quiz_projection_order"] = None

    for (quiz_key, item_order), duplicates in order_index.items():
        if len(duplicates) < 2:
            continue
        duplicate_keys = [str(row["occurrence_key"]) for row in duplicates]
        diagnostic = _projection_diagnostic(
            "duplicate_quiz_occurrence_order",
            "More than one occurrence claims the same global question order in a quiz.",
            entity_keys=[quiz_key],
            details={
                "quiz_occurrence_order": item_order,
                "occurrence_keys": duplicate_keys,
            },
        )
        projection_diagnostics.append(diagnostic)
        for occurrence in duplicates:
            occurrence["projection_diagnostic_ids"].append(diagnostic["diagnostic_id"])
            occurrence["diagnostic_ids"].append(diagnostic["diagnostic_id"])
            if include_content:
                occurrence["projection_diagnostics"].append(deepcopy(diagnostic))

    resolved_occurrences_by_question: dict[str, list[str]] = defaultdict(list)
    observed_occurrences_by_question: dict[str, list[str]] = defaultdict(list)
    for occurrence in occurrences:
        key = occurrence.get("referenced_question_key")
        if isinstance(key, str):
            resolved_occurrences_by_question[key].append(occurrence["occurrence_key"])
        observed_key = occurrence.get("observed_question_entity_key")
        if isinstance(observed_key, str):
            observed_occurrences_by_question[observed_key].append(
                occurrence["occurrence_key"]
            )

    representative_by_key, members_by_representative = _variant_classes(
        questions, relationships
    )

    question_entities: list[dict[str, Any]] = []
    for question in questions:
        question_key = _as_text(question.get("entity_key"))
        resolved_keys = _stable_unique(
            resolved_occurrences_by_question.get(question_key, [])
        )
        observed_keys = _stable_unique(
            observed_occurrences_by_question.get(question_key, [])
        )
        all_occurrence_keys = _stable_unique([*resolved_keys, *observed_keys])
        asset_keys = _stable_unique(
            str(row.get("to_entity_key"))
            for row in uses_asset_by_question.get(question_key, [])
            if row.get("to_entity_key") in asset_by_key
        )
        question_diagnostic_ids = _stable_unique(
            str(value) for value in _list(question.get("diagnostic_ids"))
        )
        question_evidence_keys = _stable_unique(
            str(value) for value in _list(question.get("source_evidence_keys"))
        )
        entity = {
            "question_key": question_key,
            "question_ref": {
                "artifact": "source_model",
                "collection": "questions",
                "entity_key": question_key,
            },
            "occurrence_count": len(all_occurrence_keys),
            "resolved_occurrence_count": len(resolved_keys),
            "observed_occurrence_count": len(observed_keys),
            "occurrence_keys": all_occurrence_keys,
            "resolved_occurrence_keys": resolved_keys,
            "observed_occurrence_keys": observed_keys,
            "pool_memberships": [
                _relationship_pool_descriptor(row, structure_by_key)
                for row in member_of_by_question.get(question_key, [])
            ],
            "asset_keys": asset_keys,
            "source_evidence_keys": question_evidence_keys,
            "source_diagnostic_ids": question_diagnostic_ids,
            "authored_variant_digest": _authored_variant_digest(question),
            "variant_class_key": representative_by_key.get(question_key, question_key),
            "variant_class_member_keys": members_by_representative.get(
                representative_by_key.get(question_key, question_key), [question_key]
            ),
            "is_variant_class_representative": representative_by_key.get(
                question_key, question_key
            )
            == question_key,
        }
        if include_content:
            entity.update(
                {
                    "question": deepcopy(question),
                    "assets": [deepcopy(asset_by_key[key]) for key in asset_keys],
                    "source_evidence": [
                        deepcopy(evidence_by_key[evidence_key])
                        for evidence_key in question_evidence_keys
                        if evidence_key in evidence_by_key
                    ],
                    "source_diagnostics": [
                        deepcopy(diagnostic_by_id[diagnostic_id])
                        for diagnostic_id in question_diagnostic_ids
                        if diagnostic_id in diagnostic_by_id
                    ],
                }
            )
        question_entities.append(entity)

    quiz_summaries: list[dict[str, Any]] = []
    for quiz_key, quiz in quiz_by_key.items():
        quiz_occurrences = [
            row for row in occurrences if row.get("quiz_key") == quiz_key
        ]
        resolved_questions = _stable_unique(
            str(row.get("referenced_question_key"))
            for row in quiz_occurrences
            if row.get("referenced_question_key")
        )
        observed_questions = _stable_unique(
            str(row.get("observed_question_entity_key"))
            for row in quiz_occurrences
            if row.get("observed_question_entity_key")
        )
        quiz_summaries.append(
            {
                "quiz_key": quiz_key,
                "quiz_title": quiz.get("title"),
                "quiz_file": quiz_file_by_key.get(quiz_key),
                "quiz_order": quiz_order[quiz_key],
                "unique_question_count": len(resolved_questions),
                "observed_unique_question_count": len(observed_questions),
                "question_occurrence_count": len(quiz_occurrences),
                "resolved_occurrence_count": sum(
                    row.get("referenced_question_key") is not None
                    for row in quiz_occurrences
                ),
                "unresolved_occurrence_count": sum(
                    row.get("referenced_question_key") is None
                    for row in quiz_occurrences
                ),
                "random_draw_occurrence_count": sum(
                    bool(_dict(row.get("selection_context")).get("is_random_draw"))
                    for row in quiz_occurrences
                ),
            }
        )

    library_match_question_keys: dict[str, set[str]] = {
        "direct": set(),
        "inferred_exact": set(),
        "inferred_similarity": set(),
    }
    for occurrence in occurrences:
        match_status = _as_text(occurrence.get("library_match_status"))
        if match_status not in library_match_question_keys:
            continue
        question_key = _as_text(
            occurrence.get("referenced_question_key")
            or occurrence.get("observed_question_entity_key")
        )
        if question_key:
            library_match_question_keys[match_status].add(question_key)
    all_library_matched_question_keys = set().union(
        *library_match_question_keys.values()
    )

    return {
        "schema": PROJECTION_SCHEMA,
        "source_model_id": model.get("model_id"),
        "source_run_id": model.get("run_id"),
        "storage": {
            "mode": "embedded_compatibility" if include_content else "references",
            "question_content": (
                "embedded_copy" if include_content else "source_model_reference"
            ),
            "assets": "embedded_copy" if include_content else "source_model_reference",
            "source_evidence": (
                "embedded_copy" if include_content else "source_model_reference"
            ),
            "source_diagnostics": (
                "embedded_copy" if include_content else "source_model_reference"
            ),
            "projection_diagnostics": "projection_catalog",
        },
        "summary": {
            "quiz_count": len(quizzes),
            "unique_question_count": len(question_entities),
            "authored_variant_class_count": len(members_by_representative),
            "reused_authored_variant_class_count": sum(
                len(members) > 1 for members in members_by_representative.values()
            ),
            "library_question_count": len(library_questions),
            "library_payload_parsed_count": sum(
                1
                for row in library_questions
                if _dict(row.get("extensions")).get("payload_state") == "parsed"
            ),
            # Duplication inside the library is a curation signal, kept separate
            # from the quiz-review classes so a dormant record can never become
            # the page a reviewer is sent to.
            "library_duplicate_group_count": len(
                {
                    _as_text(row.get("to_entity_key"))
                    for row in relationships
                    if row.get("kind") == "same_as"
                    and row.get("source_kind") == LIBRARY_VARIANT_EQUIVALENCE
                }
            ),
            "library_duplicate_record_count": len(
                _stable_unique(
                    [
                        _as_text(row.get("from_entity_key"))
                        for row in relationships
                        if row.get("kind") == "same_as"
                        and row.get("source_kind") == LIBRARY_VARIANT_EQUIVALENCE
                    ]
                    + [
                        _as_text(row.get("to_entity_key"))
                        for row in relationships
                        if row.get("kind") == "same_as"
                        and row.get("source_kind") == LIBRARY_VARIANT_EQUIVALENCE
                    ]
                )
            ),
            # A direct itemref already names the library-backed source question
            # and therefore needs no second, synthetic match relationship.
            # Count from canonical occurrence evidence so direct references and
            # inferred matches are both reported without conflating them.
            "direct_library_reference_question_count": len(
                library_match_question_keys["direct"]
            ),
            "inferred_exact_library_match_question_count": len(
                library_match_question_keys["inferred_exact"]
            ),
            "inferred_similarity_library_match_question_count": len(
                library_match_question_keys["inferred_similarity"]
            ),
            "inferred_library_match_question_count": len(
                library_match_question_keys["inferred_exact"]
                | library_match_question_keys["inferred_similarity"]
            ),
            "library_matched_question_count": len(
                all_library_matched_question_keys
            ),
            "question_occurrence_count": len(occurrences),
            "resolved_occurrence_count": sum(
                row.get("referenced_question_key") is not None for row in occurrences
            ),
            "unresolved_occurrence_count": sum(
                row.get("referenced_question_key") is None for row in occurrences
            ),
            "random_draw_occurrence_count": sum(
                bool(_dict(row.get("selection_context")).get("is_random_draw"))
                for row in occurrences
            ),
            "source_diagnostic_count": len(source_diagnostics),
            "projection_diagnostic_count": len(projection_diagnostics),
            "quizzes": quiz_summaries,
        },
        "question_entities": question_entities,
        "question_occurrences": occurrences,
        "diagnostics": projection_diagnostics,
        "source_diagnostic_ids": _stable_unique(
            str(row.get("diagnostic_id")) for row in source_diagnostics
        ),
        **(
            {"source_diagnostics": deepcopy(source_diagnostics)}
            if include_content
            else {}
        ),
    }


__all__ = ["PROJECTION_SCHEMA", "build_quiz_review_projection"]
