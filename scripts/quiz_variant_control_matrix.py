#!/usr/bin/env python3
"""Measure question-identity fidelity across real course exports.

The control matrix answers one question per course: does every entity resolve
to exactly one authored question?  It builds each model in memory and reports
counts only.  No prompt, option, answer, feedback, or asset content is read
back out, and nothing is written to disk, so the output is safe to commit while
the exports it reads never leave the local evidence lane.

Usage:

    python3 scripts/quiz_variant_control_matrix.py \\
        --label "ANAT 1005" workspace/exports/unpacked/<export> \\
        --label "BIOL 1020" workspace/exports/unpacked/<export> \\
        --markdown workspace/review/.../matrix.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from extract_quiz_pool_review import build_payload
from quiz_normalization import (
    AUTHORED_VARIANT_EQUIVALENCE,
    QUESTION_LIBRARY_MATCH,
    authored_variant_row_digest,
    build_normalized_model,
    enrich_payload,
    variant_collision_report,
)
from quiz_review_projection import build_quiz_review_projection


def _is_library(question: dict[str, Any]) -> bool:
    return bool((question.get("extensions") or {}).get("library_only"))


ALIAS_FIELDS = (
    ("qmd_globalid", "d2l.qmd_globalid"),
    ("qmd_displayid", "d2l.qmd_displayid"),
    ("quiz_item_label", "d2l.quiz_item.label"),
    ("quiz_item_ident", "d2l.quiz_item.ident"),
    ("pool_question_globalid", "d2l.library.qmd_globalid"),
    ("pool_question_displayid", "d2l.library.qmd_displayid"),
    ("pool_question_label", "d2l.library.question.label"),
    ("pool_question_ident", "d2l.library.question.ident"),
)


def _canonical_payloads(question: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record["payload"]
        for record in (question.get("type_payload") or {}).get(
            "raw_response_models"
        )
        or []
        if str(record.get("source_kind", "")).startswith(
            "canonical-extractor-row:"
        )
        and isinstance(record.get("payload"), dict)
    ]


def _alias_gap_count(questions: list[dict[str, Any]]) -> int:
    gaps = 0
    for question in questions:
        aliases = {
            (str(alias.get("namespace")), str(alias.get("value")))
            for alias in (question.get("identity") or {}).get("source_aliases") or []
            if isinstance(alias, dict)
        }
        observed = {
            (namespace, str(payload.get(field)))
            for payload in _canonical_payloads(question)
            for field, namespace in ALIAS_FIELDS
            if payload.get(field)
        }
        gaps += len(observed - aliases)
    return gaps


def _variant_class_injectivity_failures(
    projection: dict[str, Any], questions_by_key: dict[str, dict[str, Any]]
) -> int:
    digests_by_class: dict[str, set[str]] = {}
    for row in projection.get("question_entities", []):
        if not isinstance(row, dict):
            continue
        question_key = str(row.get("question_key") or "")
        class_key = str(row.get("variant_class_key") or question_key)
        question = questions_by_key.get(question_key)
        if not question:
            continue
        digests_by_class.setdefault(class_key, set()).update(
            authored_variant_row_digest(payload)
            for payload in _canonical_payloads(question)
        )
    return sum(len(digests) > 1 for digests in digests_by_class.values())


def acceptance_failures(row: dict[str, Any]) -> list[str]:
    """Return every semantic gate that prevents an approval-safe control."""
    checks = (
        ("variant_collisions", "entity-level authored-variant collision"),
        (
            "variant_class_injectivity_failures",
            "non-injective authored-variant class",
        ),
        ("dropped_source_identifiers", "observed D2L identifier missing from aliases"),
        (
            "library_equivalence_edges",
            "authored equivalence touches library-only coverage",
        ),
        ("hidden_variant_representatives", "projected class representative is hidden"),
        ("resolved_similarity_matches", "similarity-only library match is resolved"),
    )
    failures = [label for field, label in checks if int(row.get(field, 0)) != 0]
    if not row.get("collision_approval_safe", False):
        failures.append("collision report does not permit approval")
    return failures


def measure(label: str, export: Path) -> dict[str, Any]:
    payload = build_payload(export)
    enrich_payload(payload, export)
    model, _source_meta = build_normalized_model(
        payload,
        export,
        export,
        "directory",
        source_lineage_key=f"cc:lineage:control:{hashlib.sha256(label.encode()).hexdigest()[:12]}",
        run_id=f"cc:run:control:{hashlib.sha256(label.encode()).hexdigest()[:12]}",
    )
    projection = build_quiz_review_projection(model)
    collisions = variant_collision_report(model)
    summary = projection["summary"]

    placed = [row for row in model["questions"] if not _is_library(row)]
    library = [row for row in model["questions"] if _is_library(row)]
    occurrence_rows = [
        payload
        for question in placed
        for payload in _canonical_payloads(question)
    ]
    questions_by_key = {str(row["entity_key"]): row for row in placed}
    library_keys = {str(row["entity_key"]) for row in library}
    equivalence_rows = [
        row
        for row in model["relationships"]
        if row.get("kind") == "same_as"
        and row.get("source_kind") == AUTHORED_VARIANT_EQUIVALENCE
    ]
    library_equivalence_edges = sum(
        row.get("from_entity_key") in library_keys
        or row.get("to_entity_key") in library_keys
        for row in equivalence_rows
    )
    projected_question_keys = {
        str(row.get("question_key"))
        for row in projection.get("question_entities", [])
        if isinstance(row, dict) and row.get("question_key")
    }
    hidden_variant_representatives = sum(
        str(row.get("variant_class_key")) not in projected_question_keys
        for row in projection.get("question_entities", [])
        if isinstance(row, dict) and row.get("variant_class_key")
    )
    resolved_similarity_matches = sum(
        row.get("status") == "resolved"
        and row.get("kind") == "same_as"
        and row.get("source_kind") == QUESTION_LIBRARY_MATCH
        and (row.get("attributes") or {}).get("evidence_level")
        == "inferred_similarity"
        for row in model["relationships"]
    )
    key_digest = hashlib.sha256(
        "\n".join(sorted(str(row["entity_key"]) for row in model["questions"])).encode()
    ).hexdigest()[:16]
    row = {
        "label": label,
        "quizzes": len(model["quizzes"]),
        "occurrences": len(occurrence_rows),
        "placed_entities": len(placed),
        "library_entities": len(library),
        "authored_variant_classes": int(summary.get("authored_variant_class_count", 0)),
        "reused_variant_classes": int(
            summary.get("reused_authored_variant_class_count", 0)
        ),
        "library_matched_questions": int(
            summary.get("library_matched_question_count", 0)
        ),
        "direct_library_references": int(
            summary.get("direct_library_reference_question_count", 0)
        ),
        "inferred_exact_library_matches": int(
            summary.get("inferred_exact_library_match_question_count", 0)
        ),
        "inferred_similarity_library_matches": int(
            summary.get("inferred_similarity_library_match_question_count", 0)
        ),
        "variant_collisions": collisions["variant_collision_count"],
        "collision_approval_safe": collisions["approval_safe"],
        "variant_class_injectivity_failures": _variant_class_injectivity_failures(
            projection, questions_by_key
        ),
        "dropped_source_identifiers": _alias_gap_count(placed),
        "library_equivalence_edges": library_equivalence_edges,
        "hidden_variant_representatives": hidden_variant_representatives,
        "resolved_similarity_matches": resolved_similarity_matches,
        "entity_key_digest": key_digest,
    }
    row["acceptance_failures"] = acceptance_failures(row)
    row["approval_safe"] = not row["acceptance_failures"]
    return row


def render_markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Control | Quizzes | Placements | Placed entities | Library entities "
        "| Variant classes | Reused | Direct refs | Exact inferred | Similarity inferred "
        "| Total matched | Entity collisions | Class failures | Alias gaps | Library edges "
        "| Hidden reps | Resolved similarity | Approval safe | Entity-key digest |"
    )
    divider = (
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: "
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"
    )
    lines = [header, divider]
    for row in rows:
        lines.append(
            "| {label} | {quizzes} | {occurrences} | {placed_entities} "
            "| {library_entities} | {authored_variant_classes} "
            "| {reused_variant_classes} | {direct_library_references} "
            "| {inferred_exact_library_matches} | {inferred_similarity_library_matches} "
            "| {library_matched_questions} | **{variant_collisions}** "
            "| **{variant_class_injectivity_failures}** | {dropped_source_identifiers} "
            "| {library_equivalence_edges} | {hidden_variant_representatives} "
            "| {resolved_similarity_matches} | **{approval_safe}** "
            "| `{entity_key_digest}` |".format(**row)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        action="append",
        dest="pairs",
        nargs=2,
        metavar=("LABEL", "EXPORT"),
        required=True,
        help="Course label and its unpacked export directory (repeatable).",
    )
    parser.add_argument("--json", help="Optional path for the machine-readable matrix.")
    parser.add_argument("--markdown", help="Optional path for the Markdown table.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    rows: list[dict[str, Any]] = []
    for label, export in args.pairs:
        row = measure(label, Path(export).expanduser().resolve())
        rows.append(row)
        print(
            f"{row['label']:<18} placements={row['occurrences']:>5} "
            f"entities={row['placed_entities']:>5} "
            f"variants={row['authored_variant_classes']:>5} "
            f"collisions={row['variant_collisions']}",
            flush=True,
        )

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(rows) + "\n", encoding="utf-8")
    return 0 if all(row["approval_safe"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
