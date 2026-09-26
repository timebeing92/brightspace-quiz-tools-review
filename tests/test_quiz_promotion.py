from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from quiz_contracts import validate_contract  # noqa: E402
from quiz_promote_revisions import PromotionError, promote_revisions  # noqa: E402
from quiz_review_workbook_reingest import FORMAT as OVERLAY_FORMAT  # noqa: E402


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quiz_binder" / "golden_course.model.json"
REGISTRY = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "quiz_build_capabilities.json"


def annotation(identifier: str, target: str, field: str, value: object) -> dict:
    return {
        "annotation_id": identifier,
        "target_entity_key": target,
        "field_path": field,
        "kind": "approved_change",
        "value": value,
        "actor": "Synthetic Approver",
        "timestamp": "2026-07-20T16:00:00Z",
        "source_evidence_keys": [],
        "status": "accepted",
        "extensions": {"coursecraft.binder.proposal_id": f"proposal.{identifier}"},
    }


def prepare(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "source.model.json"
    overlay = tmp_path / "decisions.json"
    shutil.copyfile(FIXTURE, model)
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    annotations = [
        annotation(
            "ann.approved.prompt",
            "cc:question:fixture:binder-direct",
            "/prompt/content",
            "PROMOTED_PROMPT_SENTINEL",
        ),
        annotation(
            "ann.excluded.short-answer",
            "cc:question:fixture:binder-no-safe",
            "/prompt/content",
            "EXCLUDED_PROMPT_SENTINEL",
        ),
        annotation(
            "ann.excluded.relationship",
            "cc:question:fixture:binder-exact",
            "/relationships/member_of",
            "Synthetic Pool B",
        ),
    ]
    overlay.write_text(
        json.dumps(
            {
                "format": OVERLAY_FORMAT,
                "source_model": {
                    "model_id": "cc:model:quiz-binder:golden-course",
                    "sha256": model_sha,
                    "source_fingerprint": "a" * 64,
                },
                "workbooks": {"baseline_sha256": "b" * 64, "edited_sha256": "c" * 64},
                "materialized_view_fingerprint": "d" * 64,
                "row_diffs": [],
                "annotations": annotations,
                "settings_inputs": [],
                "content_minimized_summary": {
                    "changed_row_count": 3,
                    "annotation_count": 3,
                    "accepted_change_count": 3,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return model, overlay


def test_promotion_is_idempotent_preserves_source_and_carries_permanent_codes(
    tmp_path: Path,
) -> None:
    model, overlay = prepare(tmp_path)
    before = model.read_bytes()
    original = json.loads(before)

    promoted_a, receipt_a = promote_revisions(model, overlay, REGISTRY)
    promoted_b, receipt_b = promote_revisions(model, overlay, REGISTRY)

    assert promoted_a == promoted_b
    assert receipt_a == receipt_b
    assert model.read_bytes() == before
    assert receipt_a["summary"] == {
        "accepted_change_count": 3,
        "applied_change_count": 1,
        "excluded_change_count": 2,
    }
    assert promoted_a["questions"][0]["prompt"]["content"] == "PROMOTED_PROMPT_SENTINEL"
    assert promoted_a["questions"][3]["prompt"]["content"] == "SENTINEL_STEM_NO_SAFE"
    assert [row["identity"]["permanent_code"] for row in promoted_a["questions"]] == [
        row["identity"]["permanent_code"] for row in original["questions"]
    ]
    assert validate_contract(promoted_a, mode="transform") == []
    assert receipt_a["route_status"] == "local_only"
    serialized_receipt = json.dumps(receipt_a)
    assert "PROMOTED_PROMPT_SENTINEL" not in serialized_receipt
    assert "EXCLUDED_PROMPT_SENTINEL" not in serialized_receipt
    assert {row["reason"] for row in receipt_a["excluded"]} == {
        "question_kind_extraction_only",
        "Library-location revisions require a stable target entity key and remain review-only.",
    }


def test_promotion_refuses_overlay_bound_to_different_model_bytes(tmp_path: Path) -> None:
    model, overlay = prepare(tmp_path)
    payload = json.loads(overlay.read_text(encoding="utf-8"))
    payload["source_model"]["sha256"] = "0" * 64
    overlay.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionError, match="not bound"):
        promote_revisions(model, overlay, REGISTRY)


def test_promotion_assigns_code_without_promoting_build_support(tmp_path: Path) -> None:
    model, overlay = prepare(tmp_path)
    payload = json.loads(overlay.read_text(encoding="utf-8"))
    target = "cc:question:fixture:binder-no-safe"
    payload["annotations"].append(
        annotation(
            "ann.approved.code",
            target,
            "/identity/permanent_code",
            "P5-TWIN-M-Q004",
        )
    )
    overlay.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    promoted, receipt = promote_revisions(model, overlay, REGISTRY)

    question = next(row for row in promoted["questions"] if row["entity_key"] == target)
    assert question["identity"]["permanent_code"] == "P5-TWIN-M-Q004"
    assert question["build_support"]["level"] == "extraction_only"
    assert question["identity"]["extensions"]["coursecraft.binder.code_assignment"][
        "annotation_id"
    ] == "ann.approved.code"
    assert any(
        row["annotation_id"] == "ann.approved.code" for row in receipt["applied"]
    )


def test_promotion_applies_approved_multi_select_scoring_without_promoting_evidence(
    tmp_path: Path,
) -> None:
    model, overlay = prepare(tmp_path)
    model_value = json.loads(model.read_text(encoding="utf-8"))
    question = model_value["questions"][0]
    question["kind"] = "multi_select"
    question["source_kind"] = "Multi-Select"
    question["scoring"]["mode"] = "unknown"
    question["build_support"] = {
        "level": "extraction_only",
        "receipt_refs": [],
        "notes": [],
        "extensions": {},
    }
    question["type_payload"]["options"][1]["correct"] = True
    model.write_text(json.dumps(model_value, sort_keys=True), encoding="utf-8")
    payload = json.loads(overlay.read_text(encoding="utf-8"))
    payload["source_model"]["sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
    payload["annotations"].append(
        annotation(
            "ann.approved.scoring",
            question["entity_key"],
            "/scoring/mode",
            "all_or_nothing",
        )
    )
    overlay.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    promoted, receipt = promote_revisions(model, overlay, REGISTRY)

    promoted_question = promoted["questions"][0]
    assert promoted_question["scoring"]["mode"] == "all_or_nothing"
    assert promoted_question["build_support"]["level"] == "extraction_only"
    assert promoted_question["scoring"]["extensions"][
        "coursecraft.binder.scoring_assignment"
    ]["annotation_id"] == "ann.approved.scoring"
    assert any(
        row["annotation_id"] == "ann.approved.scoring" for row in receipt["applied"]
    )


def test_promotion_rejects_duplicate_or_unsafe_permanent_codes(tmp_path: Path) -> None:
    model, overlay = prepare(tmp_path)
    payload = json.loads(overlay.read_text(encoding="utf-8"))
    payload["annotations"].extend(
        [
            annotation(
                "ann.approved.code-a",
                "cc:question:fixture:binder-direct",
                "/identity/permanent_code",
                "P5-DUP-Q001",
            ),
            annotation(
                "ann.approved.code-b",
                "cc:question:fixture:binder-exact",
                "/identity/permanent_code",
                "P5-DUP-Q001",
            ),
        ]
    )
    overlay.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(PromotionError, match="Duplicate permanent question code"):
        promote_revisions(model, overlay, REGISTRY)

    payload["annotations"] = [
        annotation(
            "ann.approved.unsafe-code",
            "cc:question:fixture:binder-direct",
            "/identity/permanent_code",
            "bad code",
        )
    ]
    overlay.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(PromotionError, match="proposed_permanent_code"):
        promote_revisions(model, overlay, REGISTRY)


@pytest.mark.parametrize("revision", ["prompt", "options", "answer_key"])
def test_reviewed_extraction_can_be_edited_but_still_requires_build_authorization(
    tmp_path: Path, revision: str,
) -> None:
    from quiz_authoring_readiness import analyze_authoring_readiness
    from quiz_build_support import load_authoring_projection

    fixture = REPO_ROOT / "tests/fixtures/quiz_authoring"
    original = json.loads((fixture / "buildable_quiz.model.json").read_text())
    question = original["questions"][0]
    question["build_support"] = {
        "level": "extraction_only", "receipt_refs": [], "notes": [], "extensions": {},
    }
    key = question["entity_key"]
    revisions = {
        "prompt": ("/prompt/content", question["prompt"]["content"] + "<p>SME wording edit.</p>"),
        "options": ("/type_payload/options", json.dumps(["Revised first option", "Revised second option"])),
        "answer_key": ("/type_payload/answer_key", json.dumps({"correct_option_keys": ["B"]})),
    }
    field, value = revisions[revision]
    model = tmp_path / "source.model.json"
    model.write_text(json.dumps(original))
    before = model.read_bytes()
    overlay = tmp_path / "decisions.json"
    overlay.write_text(json.dumps({
        "format": OVERLAY_FORMAT,
        "source_model": {"model_id": original["model_id"], "sha256": hashlib.sha256(before).hexdigest()},
        "annotations": [annotation("ann.sme.edit", key, field, value)],
    }))

    promoted, receipt = promote_revisions(model, overlay, REGISTRY)
    edited = promoted["questions"][0]
    assert model.read_bytes() == before
    assert receipt["summary"]["applied_change_count"] == 1
    assert receipt["excluded"] == []
    assert edited["build_support"] == question["build_support"]
    if revision == "prompt":
        assert edited["prompt"]["content"] == value
        assert edited["type_payload"] == question["type_payload"]
    elif revision == "options":
        assert [o["content"]["content"] for o in edited["type_payload"]["options"]] == json.loads(value)
        assert [o["correct"] for o in edited["type_payload"]["options"]] == [True, False]
    else:
        assert [o["correct"] for o in edited["type_payload"]["options"]] == [False, True]

    promoted_path = tmp_path / "promoted.model.json"
    promoted_path.write_text(json.dumps(promoted))
    settings = fixture / "buildable_quiz.settings.json"
    report = analyze_authoring_readiness(promoted_path, settings_path=settings, asset_root=fixture)
    assert not report["ready"]
    assert any(i["code"] == "question_not_approved_for_build" and i["entity_key"] == key for i in report["issues"])
    with pytest.raises(ValueError, match="exact Phase 5 candidate authorization"):
        load_authoring_projection(promoted_path, settings_path=settings, asset_root=fixture)
