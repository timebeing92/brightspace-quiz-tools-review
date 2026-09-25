#!/usr/bin/env python3
"""Create and verify exact, local-only Phase 5 candidate authorizations.

An authorization permits package generation for a named synthetic trial. It is
not Brightspace evidence and never changes question-instance ``build_support``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


FORMAT = "quiz-binder-phase5-candidate-authorization-v0"
PURPOSE = "phase5_roundtrip_candidate"


class CandidateAuthorizationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateAuthorizationError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise CandidateAuthorizationError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def verify_promotion_chain(
    model: dict[str, Any], promotion_receipt: dict[str, Any]
) -> dict[str, str]:
    extension = model.get("extensions", {}).get("coursecraft.binder_stage1", {})
    promotion_token = str(promotion_receipt.get("promotion_token") or "")
    if not promotion_token:
        raise CandidateAuthorizationError("Promotion receipt has no promotion_token.")
    if promotion_receipt.get("promoted_model_id") != model.get("model_id"):
        raise CandidateAuthorizationError("Promotion receipt does not name this promoted model.")
    if promotion_receipt.get("promoted_model_fingerprint") != canonical_digest(model):
        raise CandidateAuthorizationError("Promotion receipt fingerprint does not match the model.")
    if extension.get("promotion_token") != promotion_token:
        raise CandidateAuthorizationError("Model and promotion receipt tokens do not match.")
    for field in ("source_model_sha256", "decision_overlay_sha256"):
        if extension.get(field) != promotion_receipt.get(field):
            raise CandidateAuthorizationError(
                f"Model and promotion receipt disagree on {field}."
            )
    if extension.get("route") != "strict_model_stage1":
        raise CandidateAuthorizationError("Model is not a strict-model Stage 1 promotion output.")
    return {
        "promotion_token": promotion_token,
        "source_model_sha256": str(promotion_receipt["source_model_sha256"]),
        "decision_overlay_sha256": str(promotion_receipt["decision_overlay_sha256"]),
    }


def create_candidate_authorization(
    *,
    model_path: Path,
    promotion_receipt_path: Path,
    registry_path: Path,
    quiz_entity_key: str,
    question_entity_keys: list[str],
    approved_by: str,
    approved_at: str,
    valid_until: str,
    activation_base_commit: str,
) -> dict[str, Any]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    promotion_receipt = json.loads(
        promotion_receipt_path.read_text(encoding="utf-8")
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    chain = verify_promotion_chain(model, promotion_receipt)
    approved_time = _parse_timestamp(approved_at, "approved_at")
    expiry_time = _parse_timestamp(valid_until, "valid_until")
    if expiry_time <= approved_time:
        raise CandidateAuthorizationError("valid_until must be after approved_at.")
    if not approved_by.strip():
        raise CandidateAuthorizationError("approved_by is required.")
    if not activation_base_commit.strip():
        raise CandidateAuthorizationError("activation_base_commit is required.")
    quiz_keys = {row["entity_key"] for row in model.get("quizzes", [])}
    if quiz_entity_key not in quiz_keys:
        raise CandidateAuthorizationError("Authorized quiz entity key is not in the model.")
    model_question_keys = {row["entity_key"] for row in model.get("questions", [])}
    question_keys = sorted(set(question_entity_keys))
    if not question_keys or not set(question_keys) <= model_question_keys:
        raise CandidateAuthorizationError(
            "Every authorized question entity key must exist in the model."
        )
    if registry.get("schema") != "coursecraft.quiz_build_capabilities/1":
        raise CandidateAuthorizationError("Unsupported capability registry.")

    payload = {
        "format": FORMAT,
        "purpose": PURPOSE,
        "status": "approved",
        "evidence_status": "phase5_candidate_not_evidence",
        "activation_base_commit": activation_base_commit,
        "approved_by": approved_by.strip(),
        "approved_at": approved_time.isoformat().replace("+00:00", "Z"),
        "valid_until": expiry_time.isoformat().replace("+00:00", "Z"),
        "bindings": {
            "model_id": model["model_id"],
            "promoted_model_sha256": sha256_file(model_path),
            "promoted_model_fingerprint": canonical_digest(model),
            "promotion_receipt_sha256": sha256_file(promotion_receipt_path),
            "promotion_token": chain["promotion_token"],
            "source_model_sha256": chain["source_model_sha256"],
            "decision_overlay_sha256": chain["decision_overlay_sha256"],
            "registry_sha256": sha256_file(registry_path),
        },
        "scope": {
            "quiz_entity_key": quiz_entity_key,
            "question_entity_keys": question_keys,
        },
        "content_minimized": True,
    }
    payload["authorization_fingerprint"] = canonical_digest(payload)
    return payload


def verify_candidate_authorization(
    *,
    model_path: Path,
    promotion_receipt_path: Path,
    registry_path: Path,
    authorization_path: Path,
    quiz_entity_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    promotion_receipt = json.loads(
        promotion_receipt_path.read_text(encoding="utf-8")
    )
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    chain = verify_promotion_chain(model, promotion_receipt)
    if authorization.get("format") != FORMAT:
        raise CandidateAuthorizationError("Unsupported candidate authorization format.")
    if authorization.get("purpose") != PURPOSE or authorization.get("status") != "approved":
        raise CandidateAuthorizationError("Candidate authorization is not approved for Phase 5.")
    stored_fingerprint = authorization.get("authorization_fingerprint")
    unsigned = {key: value for key, value in authorization.items() if key != "authorization_fingerprint"}
    if stored_fingerprint != canonical_digest(unsigned):
        raise CandidateAuthorizationError("Candidate authorization fingerprint is invalid.")
    if not str(authorization.get("approved_by") or "").strip():
        raise CandidateAuthorizationError("Candidate authorization has no approver.")
    approved_at = _parse_timestamp(str(authorization.get("approved_at") or ""), "approved_at")
    valid_until = _parse_timestamp(str(authorization.get("valid_until") or ""), "valid_until")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current < approved_at or current > valid_until:
        raise CandidateAuthorizationError("Candidate authorization is outside its active window.")

    expected_bindings = {
        "model_id": model.get("model_id"),
        "promoted_model_sha256": sha256_file(model_path),
        "promoted_model_fingerprint": canonical_digest(model),
        "promotion_receipt_sha256": sha256_file(promotion_receipt_path),
        "promotion_token": chain["promotion_token"],
        "source_model_sha256": chain["source_model_sha256"],
        "decision_overlay_sha256": chain["decision_overlay_sha256"],
        "registry_sha256": sha256_file(registry_path),
    }
    if authorization.get("bindings") != expected_bindings:
        raise CandidateAuthorizationError("Candidate authorization bindings do not match inputs.")
    scope = authorization.get("scope", {})
    if scope.get("quiz_entity_key") != quiz_entity_key:
        raise CandidateAuthorizationError("Candidate authorization does not name this quiz.")
    question_keys = scope.get("question_entity_keys")
    if not isinstance(question_keys, list) or not question_keys:
        raise CandidateAuthorizationError("Candidate authorization has no question scope.")
    if len(question_keys) != len(set(question_keys)):
        raise CandidateAuthorizationError("Candidate authorization repeats question keys.")
    return authorization


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a local-only authorization for an exact Phase 5 candidate build."
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--promotion-receipt", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--quiz-entity-key", required=True)
    parser.add_argument("--question-entity-key", action="append", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--valid-until", required=True)
    parser.add_argument("--activation-base-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = create_candidate_authorization(
            model_path=Path(args.model),
            promotion_receipt_path=Path(args.promotion_receipt),
            registry_path=Path(args.registry),
            quiz_entity_key=args.quiz_entity_key,
            question_entity_keys=args.question_entity_key,
            approved_by=args.approved_by,
            approved_at=args.approved_at,
            valid_until=args.valid_until,
            activation_base_commit=args.activation_base_commit,
        )
        Path(args.output).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, CandidateAuthorizationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"format": FORMAT, "output": args.output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
