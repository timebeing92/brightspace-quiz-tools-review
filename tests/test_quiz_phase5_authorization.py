from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from quiz_phase5_authorization import (  # noqa: E402
    CandidateAuthorizationError,
    canonical_digest,
    create_candidate_authorization,
    verify_candidate_authorization,
)
from quiz_authoring_readiness import analyze_authoring_readiness  # noqa: E402


FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_authoring"
SOURCE_MODEL = FIXTURE_ROOT / "buildable_quiz.model.json"
SETTINGS = FIXTURE_ROOT / "buildable_quiz.settings.json"
REGISTRY = (
    REPO_ROOT
    / "workspace"
    / "reference"
    / "schemas"
    / "quiz"
    / "quiz_build_capabilities.json"
)
ACTIVATION_BASE = "0d1aaa2b68738cf5a5fc57268b4420ca10088bb4"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def prepare_candidate(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    model = json.loads(SOURCE_MODEL.read_text(encoding="utf-8"))
    token = "f" * 64
    source_sha = "a" * 64
    overlay_sha = "b" * 64
    model["model_id"] = f"cc:model:quiz-binder:{token[:24]}"
    model["run_id"] = f"cc:run:quiz-binder:{token[:24]}"
    model.setdefault("extensions", {})["coursecraft.binder_stage1"] = {
        "route": "strict_model_stage1",
        "route_status": "local_only",
        "activation_base_commit": ACTIVATION_BASE,
        "source_model_sha256": source_sha,
        "decision_overlay_sha256": overlay_sha,
        "materialized_view_fingerprint": "c" * 64,
        "promotion_token": token,
    }
    for question in model["questions"]:
        question["build_support"] = {
            "level": "extraction_only",
            "receipt_refs": [],
            "notes": ["Exact Phase 5 candidate; no live evidence yet."],
            "extensions": {},
        }
    model_path = tmp_path / "candidate.model.json"
    model_path.write_text(
        json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    promotion_receipt = {
        "format": "quiz-binder-local-promotion-receipt-v0",
        "route": "strict_model_stage1",
        "route_status": "local_only",
        "source_model_sha256": source_sha,
        "decision_overlay_sha256": overlay_sha,
        "promotion_token": token,
        "promoted_model_id": model["model_id"],
        "promoted_model_fingerprint": canonical_digest(model),
        "content_minimized": True,
    }
    receipt_path = tmp_path / "promotion.receipt.json"
    receipt_path.write_text(
        json.dumps(promotion_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return model_path, receipt_path, [row["entity_key"] for row in model["questions"]]


def write_authorization(
    tmp_path: Path, model_path: Path, receipt_path: Path, question_keys: list[str]
) -> Path:
    authorization = create_candidate_authorization(
        model_path=model_path,
        promotion_receipt_path=receipt_path,
        registry_path=REGISTRY,
        quiz_entity_key="cc:quiz:fixture:phase4",
        question_entity_keys=question_keys,
        approved_by="operator-confirmed-2026-07-20",
        approved_at="2026-07-20T12:00:00Z",
        valid_until="2099-12-31T23:59:59Z",
        activation_base_commit=ACTIVATION_BASE,
    )
    path = tmp_path / "candidate.authorization.json"
    path.write_text(
        json.dumps(authorization, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_candidate_authorization_is_exact_and_not_evidence(tmp_path: Path) -> None:
    model, receipt, question_keys = prepare_candidate(tmp_path)
    authorization_path = write_authorization(tmp_path, model, receipt, question_keys)

    verified = verify_candidate_authorization(
        model_path=model,
        promotion_receipt_path=receipt,
        registry_path=REGISTRY,
        authorization_path=authorization_path,
        quiz_entity_key="cc:quiz:fixture:phase4",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert verified["evidence_status"] == "phase5_candidate_not_evidence"
    assert verified["scope"]["question_entity_keys"] == sorted(question_keys)

    tampered = json.loads(authorization_path.read_text(encoding="utf-8"))
    tampered["scope"]["question_entity_keys"] = question_keys[:-1]
    authorization_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CandidateAuthorizationError, match="fingerprint"):
        verify_candidate_authorization(
            model_path=model,
            promotion_receipt_path=receipt,
            registry_path=REGISTRY,
            authorization_path=authorization_path,
            quiz_entity_key="cc:quiz:fixture:phase4",
            now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        )


def test_builder_requires_exact_authorization_and_chains_receipts(tmp_path: Path) -> None:
    model, receipt, question_keys = prepare_candidate(tmp_path)
    authorization = write_authorization(tmp_path, model, receipt, question_keys)

    refused = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(SETTINGS),
        "--asset-root",
        str(FIXTURE_ROOT),
        "--output-dir",
        str(tmp_path / "refused"),
    )
    assert refused.returncode == 2
    assert "exact Phase 5 candidate authorization" in refused.stderr

    package = tmp_path / "candidate-package"
    built = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(SETTINGS),
        "--asset-root",
        str(FIXTURE_ROOT),
        "--promotion-receipt",
        str(receipt),
        "--phase5-candidate-authorization",
        str(authorization),
        "--output-dir",
        str(package),
        "--zip-output",
        str(tmp_path / "candidate.zip"),
    )
    assert built.returncode == 0, built.stdout + built.stderr

    run_receipt = json.loads(
        (tmp_path / "candidate-package__receipts" / "quiz_build.run.json").read_text(
            encoding="utf-8"
        )
    )
    extensions = run_receipt["extensions"]
    assert extensions["coursecraft.evidence_status"] == (
        "phase5_candidate_not_evidence"
    )
    assert extensions["coursecraft.authoring_route"] == "strict_model_projection"
    assert extensions["coursecraft.promotion_token"] == "f" * 64
    assert extensions["coursecraft.phase5_candidate_authorization_sha256"]
    roles = {row["role"] for row in run_receipt["inputs"]}
    assert {"input", "receipt", "other"} <= roles
    assert any(
        row["extensions"].get("coursecraft.artifact_kind")
        == "phase5_candidate_authorization"
        for row in run_receipt["inputs"]
    )

    readiness = analyze_authoring_readiness(
        model,
        settings_path=SETTINGS,
        asset_root=FIXTURE_ROOT,
        promotion_receipt_path=receipt,
        trial_authorization_path=authorization,
    )
    assert readiness["ready"] is True
    assert readiness["summary"]["warning_count"] == 4
    assert {row["code"] for row in readiness["issues"]} == {
        "phase5_candidate_not_evidence"
    }


def test_expired_authorization_is_rejected(tmp_path: Path) -> None:
    model, receipt, question_keys = prepare_candidate(tmp_path)
    authorization = write_authorization(tmp_path, model, receipt, question_keys)

    with pytest.raises(CandidateAuthorizationError, match="active window"):
        verify_candidate_authorization(
            model_path=model,
            promotion_receipt_path=receipt,
            registry_path=REGISTRY,
            authorization_path=authorization,
            quiz_entity_key="cc:quiz:fixture:phase4",
            now=datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
