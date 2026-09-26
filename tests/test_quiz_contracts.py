from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.quiz_contracts import (
    SCHEMA_REGISTRY,
    check_schema_documents,
    make_content_fingerprint,
    make_entity_key,
    make_source_key,
    validate_contract,
)
from scripts.extract_quiz_pool_review import build_payload


EXAMPLE_ROOT = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "examples"
BUILD_CAPABILITY_PATH = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "quiz_build_capabilities.json"
QUIZ_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
QUESTION_KIND_MAP = {
    "Multiple Choice": "multiple_choice",
    "True/False": "true_false",
    "Multi-Select": "multi_select",
    "Short Answer": "short_answer",
    "Multi-Short Answer": "multi_short_answer",
    "Fill in the Blanks": "fill_in_blanks",
    "Long Answer": "long_answer",
    "Matching": "matching",
    "Ordering": "ordering",
}


def load_example(name: str) -> dict:
    return json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))


def error_codes(record: dict, *, mode: str = "transform") -> set[str]:
    return {issue.code for issue in validate_contract(record, mode=mode) if issue.severity == "error"}


def test_quiz_schema_family_and_all_examples_validate() -> None:
    check_schema_documents()
    assert set(SCHEMA_REGISTRY) == {
        "coursecraft.quiz/1",
        "coursecraft.quiz_run/1",
        "coursecraft.quiz_settings/1",
    }
    for path in sorted(EXAMPLE_ROOT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert validate_contract(record, mode="transform") == [], path.name


def test_observed_question_kind_example_covers_all_phase_1_types() -> None:
    record = load_example("observed_question_kinds.example.json")

    assert {question["kind"] for question in record["questions"]} == {
        "multiple_choice",
        "true_false",
        "multi_select",
        "short_answer",
        "multi_short_answer",
        "fill_in_blanks",
        "long_answer",
        "matching",
        "ordering",
    }
    assert {question["build_support"]["level"] for question in record["questions"]} == {
        "roundtrip_verified",
        "extraction_only",
    }


def test_build_capability_registry_separates_roundtrip_and_extraction_support() -> None:
    registry = json.loads(BUILD_CAPABILITY_PATH.read_text(encoding="utf-8"))
    statuses = {kind: row["status"] for kind, row in registry["question_kinds"].items()}

    assert {kind for kind, status in statuses.items() if status == "roundtrip_verified"} == {
        "multiple_choice",
        "true_false",
        "multi_select",
        "long_answer",
    }
    assert {kind for kind, status in statuses.items() if status == "extraction_only"} == {
        "short_answer",
        "multi_short_answer",
        "fill_in_blanks",
        "matching",
        "ordering",
    }
    assert statuses["unknown"] == "unsupported"
    phase5_ref = (
        "workspace/review/quiz_binder_fable/"
        "27_PHASE5_REMEDIATION_CLOSEOUT_AND_REGISTRY_PROPOSAL.md"
    )
    settings = registry["settings"]
    for name in {
        "is_active",
        "attempts_allowed",
        "time_limit",
        "show_clock",
        "enforce_time_limit",
        "is_forward_only",
        "randomize_answers",
    }:
        assert settings[name]["status"] == "roundtrip_verified"
        assert settings[name]["evidence_level"] == "L4"
        assert phase5_ref in settings[name]["receipt_refs"]
    assets = registry["assets"]["resolved_question_assets"]
    assert assets["status"] == "roundtrip_verified"
    assert assets["evidence_level"] == "L4"
    assert assets["constraints"]["tested_path_forms"] == [
        "nested",
        "shared_reference",
        "percent_encoded_uri_reference",
    ]
    assert assets["constraints"]["archive_member_rule"] == (
        "Percent-encoded URI references map to safely decoded physical archive members."
    )
    assert phase5_ref in assets["receipt_refs"]
    route = registry["authoring_routes"]["strict_model_projection"]
    assert route["status"] == "roundtrip_verified"
    assert route["evidence_level"] == "L4"
    assert route["tested_scope"] == {
        "question_kinds": [
            "multiple_choice",
            "true_false",
            "multi_select",
            "long_answer",
        ],
        "structures": [
            "question_library_banks",
            "itemref_joins",
            "random_draw_sections",
            "grade_item_join",
            "module_placement",
        ],
        "authorization": "exact promoted Phase 5 candidate only",
    }
    assert phase5_ref in route["receipt_refs"]
    assert registry["policy"]["minimum_question_build_support"] == [
        "import_verified",
        "roundtrip_verified",
    ]
    assert registry["policy"]["live_verification_boundary"] == "Phase 5"


def test_build_capability_registry_receipt_references_resolve() -> None:
    registry = json.loads(BUILD_CAPABILITY_PATH.read_text(encoding="utf-8"))

    for family in (
        "question_kinds",
        "structures",
        "settings",
        "assets",
        "authoring_routes",
    ):
        for capability, row in registry.get(family, {}).items():
            for reference in row.get("receipt_refs", []):
                relative_path = str(reference).split("#", 1)[0]
                assert (REPO_ROOT / relative_path).is_file(), (
                    f"{family}.{capability} names a missing receipt: {reference}"
                )


def test_every_phase_1_fixture_question_row_fits_the_contract_without_losing_raw_projection() -> None:
    fixture_dirs = sorted(path for path in QUIZ_FIXTURE_ROOT.iterdir() if path.is_dir())
    assert len(fixture_dirs) == 14
    observed_kinds: set[str] = set()
    validated_rows = 0
    for fixture_dir in fixture_dirs:
        payload = build_payload(fixture_dir)
        for index, row in enumerate(payload["quiz_question_rows"]):
            source_kind = str(row.get("question_type", "") or "")
            kind = QUESTION_KIND_MAP.get(source_kind, "unknown")
            observed_kinds.add(kind)
            row_digest = hashlib.sha256(
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            source_key = f"cc:source:{row_digest}"
            entity_key = f"cc:question:fixture:{fixture_dir.name}:{index}"
            evidence_key = f"ev.{fixture_dir.name}.{index}"
            contract = {
                "schema": "coursecraft.quiz/1",
                "model_id": f"cc:model:fixture:{fixture_dir.name}:{index}",
                "run_id": f"cc:run:fixture:{fixture_dir.name}:{index}",
                "source": {
                    "source_key": source_key,
                    "source_lineage_key": f"cc:lineage:fixture:{fixture_dir.name}",
                    "source_kind": "fixture",
                    "fingerprint": {"algorithm": "sha256", "digest": row_digest, "scope": "file_set"},
                    "references": [str(fixture_dir.relative_to(REPO_ROOT))],
                    "extensions": {},
                },
                "quizzes": [],
                "structures": [],
                "questions": [
                    {
                        "entity_key": entity_key,
                        "identity": {
                            "strategy": "assigned",
                            "permanent_code": None,
                            "source_aliases": [],
                            "content_fingerprints": [],
                            "extensions": {},
                        },
                        "kind": kind,
                        "source_kind": source_kind or "[missing]",
                        "title": row.get("quiz_item_title") or None,
                        "prompt": {
                            "format": "plain_text",
                            "content": str(row.get("question_text", "") or ""),
                            "extensions": {},
                        },
                        "type_payload": {
                            "options": [],
                            "accepted_responses": [],
                            "blanks": [],
                            "match_pairs": [],
                            "correct_order": [],
                            "raw_response_models": [
                                {
                                    "source_kind": f"extractor-row:{source_kind or 'missing'}",
                                    "payload": row,
                                    "source_evidence_keys": [evidence_key],
                                    "extensions": {},
                                }
                            ],
                            "extensions": {},
                        },
                        "scoring": {
                            "state": "known" if row.get("question_weight") else "unknown",
                            "mode": "unknown",
                            "maximum_points": row.get("question_weight") or None,
                            "rules": [],
                            "extensions": {},
                        },
                        "feedback": [],
                        "build_support": {
                            "level": "extraction_only",
                            "receipt_refs": [],
                            "notes": ["Fixture projection test; build support is asserted separately."],
                            "extensions": {},
                        },
                        "source_evidence_keys": [evidence_key],
                        "diagnostic_ids": [],
                        "extensions": {},
                    }
                ],
                "assets": [],
                "resources": [],
                "relationships": [],
                "evidence": [
                    {
                        "evidence_key": evidence_key,
                        "source_key": source_key,
                        "kind": "observation",
                        "source_ref": str(row.get("quiz_file", fixture_dir.name)),
                        "locator": str(row.get("quiz_item_ident", "")) or None,
                        "content": None,
                        "content_ref": str(fixture_dir.relative_to(REPO_ROOT)),
                        "sha256": None,
                        "extraction_method": "deterministic",
                        "extensions": {},
                    }
                ],
                "lineage": [],
                "settings_observations": [],
                "annotations": [],
                "diagnostics": [],
                "extensions": {},
            }
            assert validate_contract(contract, mode="transform") == [], (
                fixture_dir.name,
                index,
                source_kind,
            )
            assert contract["questions"][0]["type_payload"]["raw_response_models"][0]["payload"] == row
            validated_rows += 1

    assert validated_rows > 0
    assert "unknown" not in observed_kinds


def test_unknown_question_extensions_missing_optional_prompt_and_incomplete_edge_survive() -> None:
    record = load_example("unknown_incomplete.example.json")

    assert validate_contract(record, mode="transform") == []
    question = record["questions"][0]
    assert question["kind"] == "unknown"
    assert question["source_kind"] == "Vendor Hotspot 2.0"
    assert question["prompt"] is None
    assert question["type_payload"]["raw_response_models"][0]["payload"]["regions"]
    assert question["extensions"]["vendor.future_question_field"]
    assert record["extensions"]["vendor.future_top_level_extension"]["preserved"] is True
    incomplete = next(row for row in record["relationships"] if row["status"] == "incomplete")
    assert incomplete["to_entity_key"] is None
    assert incomplete["attributes"]["linkrefid"] == "MISSING_Q"


def test_unknown_question_without_evidence_is_rejected() -> None:
    record = load_example("unknown_incomplete.example.json")
    record["questions"][0]["source_evidence_keys"] = []

    assert "schema_validation" in error_codes(record)


def test_resolved_relationship_requires_a_present_target() -> None:
    record = load_example("unknown_incomplete.example.json")
    record["relationships"][1]["status"] = "resolved"

    assert "schema_validation" in error_codes(record)


def test_semantic_validation_rejects_dangling_entity_and_evidence_references() -> None:
    record = load_example("mixed_storage.example.json")
    record["relationships"][0]["to_entity_key"] = "cc:structure:missing"
    record["questions"][0]["source_evidence_keys"] = ["ev.missing"]

    codes = error_codes(record)
    assert "missing_relationship_target" in codes
    assert "missing_evidence_reference" in codes


def test_settings_receipt_enforces_precedence_and_winner_value() -> None:
    record = load_example("settings_resolution.example.json")
    attempts = record["resolutions"][0]
    attempts["winner_input_id"] = "in.attempts.profile"
    attempts["effective_value"] = 2

    assert "settings_precedence_violation" in error_codes(record)


def test_settings_receipt_requires_coercion_note_when_value_changes() -> None:
    record = load_example("settings_resolution.example.json")
    time_limit = record["resolutions"][1]
    time_limit["effective_value"] = "PT60M"
    time_limit["coerced"] = True

    assert "missing_coercion_note" in error_codes(record)


def test_run_receipt_schema_hashes_are_checked() -> None:
    record = load_example("run_receipt.example.json")
    record["contract_versions"][0]["schema_sha256"] = "0" * 64

    assert "schema_receipt_hash_mismatch" in error_codes(record)


def test_run_receipt_requires_checksums_for_emitted_artifacts() -> None:
    record = load_example("run_receipt.example.json")
    record["artifacts"][0]["sha256"] = None

    assert "emitted_artifact_without_checksum" in error_codes(record)


def test_unknown_contract_version_warns_for_inspection_and_fails_for_transform() -> None:
    record = {"schema": "coursecraft.quiz/99", "future": {"preserve": True}}

    inspect_issues = validate_contract(record, mode="inspect")
    transform_issues = validate_contract(record, mode="transform")
    assert [(issue.severity, issue.code) for issue in inspect_issues] == [
        ("warning", "unknown_contract_version")
    ]
    assert [(issue.severity, issue.code) for issue in transform_issues] == [
        ("error", "unknown_contract_version")
    ]


def test_permanent_and_source_alias_keys_survive_export_refreshes() -> None:
    lineage = "cc:lineage:course:bumg-660"
    permanent_before = make_entity_key("question", lineage, permanent_code="EX100_Q002")
    permanent_after = make_entity_key("question", lineage, permanent_code="EX100_Q002")
    alias_before = make_entity_key(
        "question",
        lineage,
        alias_namespace="d2l.qmd_globalid",
        alias_value="global-question-17",
    )
    alias_after = make_entity_key(
        "question",
        lineage,
        alias_namespace="d2l.qmd_globalid",
        alias_value="global-question-17",
    )

    assert permanent_before == permanent_after
    assert alias_before == alias_after
    assert make_source_key("1" * 64) != make_source_key("2" * 64)
    assert "111111" not in permanent_before
    assert "111111" not in alias_before


def test_content_fingerprint_is_a_separate_matching_hint() -> None:
    first = make_content_fingerprint({"prompt": "A", "choices": ["B"]}, basis="prompt+choices")
    second = make_content_fingerprint({"prompt": "A revised", "choices": ["B"]}, basis="prompt+choices")

    assert first["digest"] != second["digest"]
    assert first["basis"] == second["basis"] == "prompt+choices"


def test_model_copy_remains_valid_when_optional_content_is_absent() -> None:
    record = deepcopy(load_example("mixed_storage.example.json"))
    record["quizzes"][0].pop("description", None)
    record["quizzes"][0].pop("instructions", None)
    record["questions"][0]["title"] = None
    record["questions"][0]["prompt"] = None

    assert validate_contract(record, mode="transform") == []
