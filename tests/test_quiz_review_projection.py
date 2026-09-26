from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

from extract_quiz_pool_review import build_payload
from quiz_normalization import build_normalized_model, enrich_payload
from quiz_review_projection import (
    PROJECTION_SCHEMA,
    build_quiz_review_projection,
)


def normalized_fixture(name: str) -> dict[str, object]:
    fixture = FIXTURE_ROOT / name
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key=f"cc:lineage:fixture:{name}",
        run_id=f"cc:run:fixture:{name}",
    )
    return model


def test_mixed_storage_projection_preserves_order_source_and_draw_context() -> None:
    model = normalized_fixture("mixed_inline_itemref_and_root_bank")
    original = deepcopy(model)

    projection = build_quiz_review_projection(model)

    assert model == original
    assert projection["schema"] == PROJECTION_SCHEMA
    assert projection["summary"] == {
        "quiz_count": 1,
        "unique_question_count": 3,
        "authored_variant_class_count": 3,
        "reused_authored_variant_class_count": 0,
        # Both library records in this fixture are themselves placed, so they
        # are the same entity rather than a separate match target. The direct
        # references still count as library-backed questions.
        "library_question_count": 0,
        "library_payload_parsed_count": 0,
        "library_duplicate_group_count": 0,
        "library_duplicate_record_count": 0,
        "direct_library_reference_question_count": 2,
        "inferred_exact_library_match_question_count": 0,
        "inferred_similarity_library_match_question_count": 0,
        "inferred_library_match_question_count": 0,
        "library_matched_question_count": 2,
        "question_occurrence_count": 3,
        "resolved_occurrence_count": 3,
        "unresolved_occurrence_count": 0,
        "random_draw_occurrence_count": 1,
        "source_diagnostic_count": 1,
        "projection_diagnostic_count": 0,
        "quizzes": [
            {
                "quiz_key": model["quizzes"][0]["entity_key"],
                "quiz_title": "Mixed storage fixture",
                "quiz_file": "quiz_d2l_mixed.xml",
                "quiz_order": 1,
                "unique_question_count": 3,
                "observed_unique_question_count": 3,
                "question_occurrence_count": 3,
                "resolved_occurrence_count": 3,
                "unresolved_occurrence_count": 0,
                "random_draw_occurrence_count": 1,
            }
        ],
    }

    occurrences = projection["question_occurrences"]
    assert [row["quiz_occurrence_order"] for row in occurrences] == [1, 2, 3]
    assert [row["section_order"] for row in occurrences] == [1, 2, 3]
    assert [row["section_occurrence_order"] for row in occurrences] == [1, 1, 1]
    assert [row["placement_kind"] for row in occurrences] == [
        "itemref",
        "itemref",
        "inline",
    ]
    assert [row["source_kind"] for row in occurrences] == [
        "itemref_linkrefid",
        "itemref_linkrefid",
        "unmatched",
    ]
    assert all(
        re.fullmatch(r"cc:occurrence:[a-f0-9]{24}", row["occurrence_key"])
        for row in occurrences
    )
    assert len({row["occurrence_key"] for row in occurrences}) == 3
    assert all(row["referenced_question_key"] for row in occurrences)

    draw = occurrences[1]
    assert draw["selection_context"] == {
        "mode": "random",
        "is_random_draw": True,
        "draw_structure_key": draw["section_key"],
        "draw_count": 1,
        "section_available_count": 1,
        "candidate_pool_size": 1,
        "pool_keys": [draw["pool_memberships"][0]["pool_key"]],
        "pool_context_basis": "occurrence_pool_membership",
    }
    assert draw["pool_memberships"][0]["pool_title"] == "Section Bank"
    assert draw["pool_memberships"][0]["match_status"] == "direct"
    assert occurrences[2]["library_match_status"] == "unresolved"
    assert "source_diagnostics" not in occurrences[2]
    source_diagnostic_by_id = {
        row["diagnostic_id"]: row for row in model["diagnostics"]
    }
    assert {
        source_diagnostic_by_id[diagnostic_id]["code"]
        for diagnostic_id in occurrences[2]["source_diagnostic_ids"]
    } == {"no_safe_library_match"}


def test_variant_classes_never_name_a_hidden_library_representative() -> None:
    projection = build_quiz_review_projection(
        normalized_fixture("long_answer_with_answer_key")
    )
    projected_keys = {
        row["question_key"] for row in projection["question_entities"]
    }
    assert projected_keys
    for row in projection["question_entities"]:
        assert row["variant_class_key"] in projected_keys
        assert set(row["variant_class_member_keys"]) <= projected_keys
    assert sum(
        bool(row["is_variant_class_representative"])
        for row in projection["question_entities"]
    ) == projection["summary"]["authored_variant_class_count"]


def test_short_answer_projection_shows_three_entities_and_five_occurrences() -> None:
    model = normalized_fixture("short_answer_projection")
    projection = build_quiz_review_projection(model)

    assert projection["summary"]["unique_question_count"] == 3
    assert projection["summary"]["question_occurrence_count"] == 5
    assert [
        row["quiz_occurrence_order"] for row in projection["question_occurrences"]
    ] == [1, 2, 3, 4, 5]
    assert [row["placement_kind"] for row in projection["question_occurrences"]] == [
        "itemref",
        "inline",
        "itemref",
        "inline",
        "inline",
    ]

    titles_by_key = {row["entity_key"]: row["title"] for row in model["questions"]}
    by_title = {
        titles_by_key[row["question_key"]]: row
        for row in projection["question_entities"]
    }
    assert by_title["[AGREE]"]["occurrence_count"] == 2
    assert by_title["[CONFLICT]"]["occurrence_count"] == 2
    assert by_title["[COMPACT]"]["occurrence_count"] == 1
    assert by_title["[AGREE]"]["resolved_occurrence_count"] == 2
    assert by_title["[AGREE]"]["observed_occurrence_count"] == 2
    assert "question" not in by_title["[AGREE]"]
    assert by_title["[AGREE]"]["question_ref"] == {
        "artifact": "source_model",
        "collection": "questions",
        "entity_key": by_title["[AGREE]"]["question_key"],
    }
    assert by_title["[AGREE]"]["question_key"].startswith("cc:question:")
    assert all(
        key.startswith("cc:occurrence:")
        for key in by_title["[AGREE]"]["occurrence_keys"]
    )

    second = projection["question_occurrences"][1]
    assert second["referenced_question_key"] == (
        projection["question_occurrences"][0]["referenced_question_key"]
    )
    assert second["source_kind"] == "qmd_displayid"
    assert second["pool_memberships"][0]["evidence_level"] == "source_evidence"


def test_occurrence_scoped_source_facts_do_not_collapse_itemref_and_inline() -> None:
    projection = build_quiz_review_projection(
        normalized_fixture("occurrence_scoped_source_facts")
    )

    assert projection["summary"]["unique_question_count"] == 1
    assert projection["summary"]["question_occurrence_count"] == 2
    first, second = projection["question_occurrences"]
    assert first["referenced_question_key"] == second["referenced_question_key"]
    assert first["occurrence_key"] != second["occurrence_key"]
    assert (first["placement_kind"], first["source_kind"]) == (
        "itemref",
        "itemref_linkrefid",
    )
    assert (second["placement_kind"], second["source_kind"]) == (
        "inline",
        "qmd_displayid",
    )
    assert first["pool_memberships"][0]["relationship_key"] != (
        second["pool_memberships"][0]["relationship_key"]
    )
    assert projection["question_entities"][0]["occurrence_count"] == 2


def test_unresolved_itemrefs_remain_occurrences_without_claiming_a_join(
    tmp_path: Path,
) -> None:
    export = tmp_path / "quiz_only"
    export.mkdir()
    source_quiz = (
        FIXTURE_ROOT / "mixed_inline_itemref_and_root_bank" / "quiz_d2l_mixed.xml"
    )
    (export / source_quiz.name).write_bytes(source_quiz.read_bytes())
    payload = build_payload(export)
    enrich_payload(payload, export)
    model, _source_meta = build_normalized_model(
        payload,
        export,
        export,
        "directory",
        source_lineage_key="cc:lineage:fixture:quiz-only-projection",
        run_id="cc:run:fixture:quiz-only-projection",
    )

    projection = build_quiz_review_projection(model)

    assert projection["summary"]["unique_question_count"] == 3
    assert projection["summary"]["question_occurrence_count"] == 3
    assert projection["summary"]["resolved_occurrence_count"] == 1
    assert projection["summary"]["unresolved_occurrence_count"] == 2
    unresolved = [
        row
        for row in projection["question_occurrences"]
        if row["referenced_question_key"] is None
    ]
    assert len(unresolved) == 2
    assert all(row["placement_kind"] == "itemref" for row in unresolved)
    assert all(row["observed_question_entity_key"] for row in unresolved)
    assert all(row["relationship_status"] == "incomplete" for row in unresolved)
    projection_diagnostic_by_id = {
        row["diagnostic_id"]: row for row in projection["diagnostics"]
    }
    assert all(
        "occurrence_question_unresolved"
        in {
            projection_diagnostic_by_id[diagnostic_id]["code"]
            for diagnostic_id in row["projection_diagnostic_ids"]
        }
        for row in unresolved
    )
    assert sorted(
        row["quiz_occurrence_order"] for row in projection["question_occurrences"]
    ) == [1, 2, 3]


def test_projection_diagnoses_quiz_context_that_cannot_be_resolved() -> None:
    model = normalized_fixture("occurrence_scoped_source_facts")
    for structure in model["structures"]:
        for alias in structure["identity"]["source_aliases"]:
            if alias["namespace"] == "brightspace.quiz_section_path":
                _quiz_file, section_path = alias["value"].split("::", 1)
                alias["value"] = f"unjoined.xml::{section_path}"
    for evidence in model["evidence"]:
        if evidence["evidence_key"].startswith(("ev.quiz.", "ev.occurrence.")):
            evidence["source_ref"] = "unjoined.xml"
    for question in model["questions"]:
        for raw_model in question["type_payload"]["raw_response_models"]:
            if raw_model["source_kind"].startswith("canonical-extractor-row:"):
                raw_model["payload"]["quiz_file"] = "unjoined.xml"
    section_keys = {
        row["entity_key"] for row in model["structures"] if row.get("kind") == "section"
    }
    for relationship in model["relationships"]:
        if (
            relationship.get("kind") == "contains"
            and relationship.get("to_entity_key") in section_keys
        ):
            relationship["from_entity_key"] = "cc:unjoined:quiz-parent"

    projection = build_quiz_review_projection(model)

    assert all(
        occurrence["quiz_key"] is None
        for occurrence in projection["question_occurrences"]
    )
    assert {diagnostic["code"] for diagnostic in projection["diagnostics"]} == {
        "occurrence_quiz_unresolved"
    }
    assert projection["summary"]["projection_diagnostic_count"] == 2


def test_projection_is_deterministic_and_rejects_other_contracts() -> None:
    model = normalized_fixture("occurrence_scoped_source_facts")
    first = build_quiz_review_projection(model)
    second = build_quiz_review_projection(model)

    assert first == second
    with pytest.raises(ValueError, match=r"requires coursecraft\.quiz/1"):
        build_quiz_review_projection({"schema": "coursecraft.quiz/2"})


def test_reference_projection_is_semantically_equivalent_without_content_amplification() -> (
    None
):
    model = normalized_fixture("short_answer_projection")
    for index, question in enumerate(model["questions"], start=1):
        question["type_payload"]["raw_response_models"].append(
            {
                "source_kind": "fixture:large-authored-source-payload",
                "payload": {
                    "variant": index,
                    "authored_html_and_mathml": (
                        f"<p>VARIANT_{index}</p><math><mi>x</mi></math>" * 500
                    ),
                },
                "source_evidence_keys": question["source_evidence_keys"],
            }
        )

    compact = build_quiz_review_projection(model)
    embedded = build_quiz_review_projection(model, include_content=True)

    assert compact["summary"] == embedded["summary"]
    assert compact["storage"]["mode"] == "references"
    assert embedded["storage"]["mode"] == "embedded_compatibility"
    assert [row["occurrence_key"] for row in compact["question_occurrences"]] == [
        row["occurrence_key"] for row in embedded["question_occurrences"]
    ]
    assert [
        row["referenced_question_key"] for row in compact["question_occurrences"]
    ] == [row["referenced_question_key"] for row in embedded["question_occurrences"]]
    assert all("question" not in row for row in compact["question_entities"])
    assert all("assets" not in row for row in compact["question_entities"])
    assert all("source_evidence" not in row for row in compact["question_entities"])
    assert all("source_diagnostics" not in row for row in compact["question_entities"])
    assert all(
        "source_diagnostics" not in row and "projection_diagnostics" not in row
        for row in compact["question_occurrences"]
    )

    compact_bytes = len(
        json.dumps(compact, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    embedded_bytes = len(
        json.dumps(embedded, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    assert compact_bytes < embedded_bytes * 0.25


def test_projection_retains_distinct_materially_divergent_question_entities() -> None:
    model = normalized_fixture("occurrence_scoped_source_facts")
    original = model["questions"][0]
    variant = deepcopy(original)
    variant["entity_key"] = original["entity_key"] + ":material-variant"
    variant["title"] = "Materially divergent authored variant"
    variant["prompt"]["content"] = "<p>Different authored prompt</p>"
    variant["type_payload"]["accepted_responses"] = [
        {"value": "different answer", "case_sensitive": False, "weight": 1}
    ]
    model["questions"].append(variant)

    projection = build_quiz_review_projection(model)

    keys = [row["question_key"] for row in projection["question_entities"]]
    assert original["entity_key"] in keys
    assert variant["entity_key"] in keys
    assert len(keys) == len(model["questions"])
