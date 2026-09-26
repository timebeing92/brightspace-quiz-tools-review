from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from quiz_binder_strings import ClaimError, capability_claim, render_claim  # noqa: E402
from quiz_contracts import validate_contract  # noqa: E402
from quiz_review_queues import project_review_queues  # noqa: E402


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quiz_binder" / "golden_course.model.json"
REGISTRY = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "quiz_build_capabilities.json"
SENTINELS = {
    "SENTINEL_STEM_DIRECT",
    "SENTINEL_OPTION_ALPHA",
    "SENTINEL_MANUAL_ANSWER",
    "SENTINEL_ACCEPTED_RESPONSE",
    "SENTINEL_PROPOSED_REVISION",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_fixture_validates_and_projects_every_required_queue_without_content() -> None:
    model = load_json(FIXTURE)
    registry = load_json(REGISTRY)

    assert validate_contract(model, mode="transform") == []
    result = project_review_queues(model, registry)

    assert result["summary"] == {
        "no_safe_match_count": 1,
        "probable_match_count": 1,
        "settings_review_count": 1,
        "asset_review_count": 1,
        "blocker_count": 5,
    }
    assert result["queues"]["no_safe_match"][0]["permanent_code"] == "BINDER-Q004"
    assert result["queues"]["probable_match"][0]["permanent_code"] == "BINDER-Q003"
    serialized = json.dumps(result)
    for sentinel in SENTINELS:
        assert sentinel not in serialized


def test_registry_changes_immediately_change_queue_claims_and_blockers() -> None:
    model = load_json(FIXTURE)
    registry = load_json(REGISTRY)
    baseline = project_review_queues(model, registry)
    assert any(row.get("reason") == "question_kind_extraction_only" for row in baseline["queues"]["blockers"])

    changed = deepcopy(registry)
    changed["question_kinds"]["short_answer"] = {
        "status": "roundtrip_verified",
        "evidence_level": "L4",
        "receipt_refs": ["synthetic-receipt"],
    }
    refreshed = project_review_queues(model, changed)

    assert not any(
        row.get("reason") == "question_kind_extraction_only"
        for row in refreshed["queues"]["blockers"]
    )
    short_claim = next(
        row for row in refreshed["capability_claims"] if row["capability"] == "short_answer"
    )
    assert short_claim["evidence_level"] == "L4"
    assert "strict-model route remains local-only" in render_claim(short_claim)


def test_no_safe_queue_uses_real_extractor_diagnostic_without_binder_extension() -> None:
    model = load_json(FIXTURE)
    registry = load_json(REGISTRY)
    target = "cc:question:fixture:binder-no-safe"
    question = next(row for row in model["questions"] if row["entity_key"] == target)
    question["extensions"].pop("coursecraft.binder.match_state")
    model["relationships"] = [
        row
        for row in model["relationships"]
        if not (
            row.get("kind") == "member_of"
            and row.get("from_entity_key") == target
            and row.get("status") == "incomplete"
        )
    ]

    result = project_review_queues(model, registry)

    assert result["summary"]["no_safe_match_count"] == 1
    assert result["queues"]["no_safe_match"] == [
        {
            "entity_key": target,
            "permanent_code": "BINDER-Q004",
            "kind": "short_answer",
        }
    ]


def test_structured_l4_claims_fail_closed_without_receipts() -> None:
    registry = load_json(REGISTRY)
    with pytest.raises(ClaimError, match="no receipt"):
        capability_claim(registry, "structures", "question_library_banks")
    with pytest.raises(ClaimError, match="without a receipt"):
        render_claim(
            {
                "family": "question_kinds",
                "capability": "multiple_choice",
                "status": "roundtrip_verified",
                "evidence_level": "L4",
                "receipt_refs": [],
                "route": "registered_capability",
            }
        )


def test_strict_model_projection_claim_is_scoped_to_phase5_l4_evidence() -> None:
    registry = load_json(REGISTRY)

    claim = capability_claim(
        registry,
        "authoring_routes",
        "strict_model_projection",
    )

    assert claim["family_label"] == "Authoring route"
    assert claim["status"] == "roundtrip_verified"
    assert claim["evidence_level"] == "L4"
    assert claim["live_verification_boundary"] == "Phase 5"
    assert claim["receipt_refs"]
    assert "L4" in render_claim(claim)
