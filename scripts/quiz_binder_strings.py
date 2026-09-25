#!/usr/bin/env python3
"""Presentation labels and evidence-aware claim rendering for Quiz Binder.

This module owns words, not capability truth. Callers must pass the current
capability registry into ``capability_claim`` on every run.
"""

from __future__ import annotations

from typing import Any


LABELS = {
    "station_title": "Quiz Binder",
    "source_snapshot": "Source snapshot",
    "no_safe_match": "No reliable library link found",
    "probable_match": "Possible library links to review",
    "settings_review": "Settings to resolve",
    "asset_review": "Assets to resolve",
    "promotion_preview": "Promotion preview",
    "local_only": "Local Stage 1 route",
    "plain_mode": "Plain mode",
}

FAMILY_LABELS = {
    "question_kinds": "Question kind",
    "settings": "Setting",
    "structures": "Structure",
    "assets": "Asset",
    "authoring_routes": "Authoring route",
}

VALUE_LABELS = {
    "direct_library_link": "Direct library link",
    "exact_content_match": "Linked by identical content",
    "probable_content_match": "Possible link based on similar content",
    "no_safe_library_match": "No reliable library link found",
    "not_recorded": "Match not recorded by this extraction",
    "roundtrip_verified": "Verified for rebuild (L4)",
    "import_verified": "Import verified (L3)",
    "generated_validated": "Validated locally (L2)",
    "locally_projected": "Projected and validated locally (L2)",
    "extraction_only": "Review and preservation only",
    "unsupported": "Unsupported for build",
    "unknown": "Unknown support",
}

MATCH_LABELS = {
    "direct": "Direct library link",
    "source_evidence": "Direct library link",
    "direct_library_link": "Direct library link",
    "inferred_exact": "Linked by identical content",
    "exact_content_match": "Linked by identical content",
    "Exact content match": "Linked by identical content",
    "Linked by identical content": "Linked by identical content",
    "inferred_similarity": "Possible link based on similar content",
    "ambiguous": "Possible link based on similar content",
    "probable_content_match": "Possible link based on similar content",
    "Probable content match": "Possible link based on similar content",
    "Possible link based on similar content": "Possible link based on similar content",
    "unmatched": "No reliable library link found",
    "unresolved": "No reliable library link found",
    "no_safe_library_match": "No reliable library link found",
    "No safe library match": "No reliable library link found",
    "No reliable library link found": "No reliable library link found",
    "unknown": "Match not recorded by this extraction",
    "not_recorded": "Match not recorded by this extraction",
    "": "Match not recorded by this extraction",
}


class ClaimError(ValueError):
    """Raised when a capability claim lacks the evidence needed to render."""


def label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " ").strip().title())


def value_label(value: Any) -> str:
    text = str(value if value is not None else "unknown")
    return VALUE_LABELS.get(text, text.replace("_", " ").strip().title())


def match_label(value: Any) -> str:
    """Render the adopted four-level library-match vocabulary.

    Projection and legacy extractor codes intentionally remain available in
    detail fields, but they must not become the reviewer-facing label.
    """

    text = str(value if value is not None else "").strip()
    return MATCH_LABELS.get(text, "Match not recorded by this extraction")


def capability_claim(
    registry: dict[str, Any],
    family: str,
    capability: str,
    *,
    route: str = "registered_capability",
) -> dict[str, Any]:
    """Build a structured claim from live registry data.

    L4 claims require an explicit receipt reference. The strict-model Stage 1
    route is always reported as local-only even when the registered legacy
    capability has L4 evidence.
    """
    family_rows = registry.get(family)
    if not isinstance(family_rows, dict) or capability not in family_rows:
        raise ClaimError(f"Unregistered capability: {family}.{capability}")
    row = family_rows[capability]
    if not isinstance(row, dict):
        raise ClaimError(f"Malformed capability row: {family}.{capability}")
    status = str(row.get("status") or "").strip()
    evidence_level = str(row.get("evidence_level") or "").strip()
    receipt_refs = [str(item) for item in row.get("receipt_refs", []) if str(item).strip()]
    if not status or not evidence_level:
        raise ClaimError(f"Missing status/evidence rung: {family}.{capability}")
    if evidence_level == "L4" and not receipt_refs:
        raise ClaimError(f"L4 claim has no receipt reference: {family}.{capability}")

    return {
        "family": family,
        "family_label": FAMILY_LABELS.get(family, family),
        "capability": capability,
        "status": status,
        "evidence_level": evidence_level,
        "receipt_refs": receipt_refs,
        "route": route,
        "route_status": "local_only" if route == "strict_model_stage1" else "registered",
        "live_verification_boundary": registry.get("policy", {}).get(
            "live_verification_boundary"
        ),
    }


def render_claim(claim: dict[str, Any]) -> str:
    required = {"family", "capability", "status", "evidence_level", "receipt_refs", "route"}
    missing = sorted(required - claim.keys())
    if missing:
        raise ClaimError(f"Claim missing fields: {', '.join(missing)}")
    receipts = claim["receipt_refs"]
    if claim["evidence_level"] == "L4" and not receipts:
        raise ClaimError("L4 claim cannot render without a receipt reference")
    if claim["route"] == "strict_model_stage1":
        return (
            f"{claim['capability']}: registered capability {claim['status']} "
            f"({claim['evidence_level']}); strict-model route remains local-only."
        )
    return f"{claim['capability']}: {claim['status']} ({claim['evidence_level']})."
