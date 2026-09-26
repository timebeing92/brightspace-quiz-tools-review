#!/usr/bin/env python3
"""Content-minimizing readiness analysis for quiz model authoring projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from quiz_build_support import (
    CAPABILITY_REGISTRY_PATH,
    REPO_ROOT,
    SAFE_BUILD_LEVELS,
    load_authoring_projection,
    load_capability_registry,
    load_settings_receipt,
    question_projection_issues,
    question_asset_reference_issues,
    projected_bank_id,
    resolve_projection_relationships,
    resolve_model_asset,
    sha256_file,
    target_identifier_issues,
)
from quiz_contracts import validate_contract
from quiz_phase5_authorization import verify_candidate_authorization


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    entity_key: str | None = None,
    remediation: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "entity_key": entity_key,
        "message": message,
        "remediation": remediation,
    }


def _empty_report(model_path: Path) -> dict[str, Any]:
    return {
        "report_type": "coursecraft.quiz_authoring_readiness/1",
        "source_model": model_path.name,
        "source_model_sha256": sha256_file(model_path) if model_path.is_file() else None,
        "capability_registry": CAPABILITY_REGISTRY_PATH.relative_to(REPO_ROOT).as_posix(),
        "capability_registry_sha256": sha256_file(CAPABILITY_REGISTRY_PATH),
        "selected_quiz_key": None,
        "ready": False,
        "summary": {
            "quiz_count": 0,
            "reachable_draw_count": 0,
            "selected_pool_count": 0,
            "selected_question_count": 0,
            "selected_asset_count": 0,
            "error_count": 0,
            "warning_count": 0,
        },
        "question_capabilities": {},
        "issues": [],
        "extensions": {"coursecraft.content_minimizing": True},
    }


def analyze_authoring_readiness(
    model_path: Path,
    *,
    quiz_entity_key: str = "",
    settings_path: Path | None = None,
    asset_root: Path | list[Path] | None = None,
    promotion_receipt_path: Path | None = None,
    trial_authorization_path: Path | None = None,
) -> dict[str, Any]:
    """Report all readily detectable build blockers without changing the model."""
    model_path = model_path.expanduser().resolve()
    report = _empty_report(model_path)
    issues = report["issues"]
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(
            _issue(
                "error",
                "unreadable_model",
                f"Could not read quiz model JSON: {exc}",
                remediation="Provide a readable coursecraft.quiz/1 JSON model.",
            )
        )
        return _finish(report)

    contract_issues = validate_contract(model, mode="transform")
    for item in contract_issues:
        issues.append(
            _issue(
                item.severity,
                item.code,
                item.render(),
                remediation="Resolve the contract/schema issue before authoring projection.",
            )
        )
    report["summary"]["quiz_count"] = len(model.get("quizzes", []))

    # A collapsed question variant means one entity holds several authored
    # questions and only one answer key survived. Nothing downstream may treat
    # such a model as approvable, so this blocks before any build projection.
    from quiz_normalization import variant_collision_report

    collision_report = variant_collision_report(model)
    for row in collision_report["entities"][:20]:
        issues.append(
            _issue(
                "error",
                "question_variant_collision_unresolved",
                (
                    f"Entity holds {row['authored_variant_count']} different authored "
                    f"questions across {row['occurrence_count']} placements; only one "
                    "answer key would be presented."
                ),
                entity_key=row["entity_key"],
                remediation=(
                    "Re-extract with the corrected question identity so each authored "
                    "variant keeps its own answer key before approving anything."
                ),
            )
        )

    if any(item["severity"] == "error" for item in issues):
        return _finish(report)

    quizzes = model["quizzes"]
    if quiz_entity_key:
        selected = next((row for row in quizzes if row["entity_key"] == quiz_entity_key), None)
        if selected is None:
            issues.append(
                _issue(
                    "error",
                    "quiz_not_found",
                    f"Requested quiz entity key is absent: {quiz_entity_key}",
                    remediation="Choose one of the model's quiz entity keys.",
                )
            )
            return _finish(report)
    elif len(quizzes) == 1:
        selected = quizzes[0]
    else:
        issues.append(
            _issue(
                "error",
                "quiz_selection_required",
                f"Model contains {len(quizzes)} quizzes; no unique authoring target can be selected.",
                remediation="Pass --quiz-entity-key after reviewing the intended quiz.",
            )
        )
        return _finish(report)

    quiz_key = selected["entity_key"]
    report["selected_quiz_key"] = quiz_key
    authorized_question_keys: set[str] = set()
    if trial_authorization_path is not None:
        if promotion_receipt_path is None:
            issues.append(
                _issue(
                    "error",
                    "phase5_candidate_authorization_invalid",
                    "A Phase 5 candidate authorization requires a promotion receipt.",
                    entity_key=quiz_key,
                    remediation="Supply the exact bound promotion receipt or remove trial authorization.",
                )
            )
        else:
            try:
                authorization = verify_candidate_authorization(
                    model_path=model_path,
                    promotion_receipt_path=promotion_receipt_path,
                    registry_path=CAPABILITY_REGISTRY_PATH,
                    authorization_path=trial_authorization_path,
                    quiz_entity_key=quiz_key,
                )
                authorized_question_keys = set(
                    authorization["scope"]["question_entity_keys"]
                )
                report["extensions"].update(
                    {
                        "coursecraft.evidence_status": "phase5_candidate_not_evidence",
                        "coursecraft.phase5_candidate_authorization_fingerprint": authorization[
                            "authorization_fingerprint"
                        ],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    _issue(
                        "error",
                        "phase5_candidate_authorization_invalid",
                        str(exc),
                        entity_key=quiz_key,
                        remediation="Regenerate authorization from the exact model, promotion receipt, registry, and question scope.",
                    )
                )
    run_id = f"cc:run:quiz-readiness:{hashlib.sha256((str(model_path) + quiz_key).encode()).hexdigest()[:24]}"
    try:
        _settings_receipt, _quiz_settings, question_randomize = load_settings_receipt(
            settings_path,
            quiz_key,
            run_id,
        )
    except Exception as exc:  # noqa: BLE001
        question_randomize = {}
        issues.append(
            _issue(
                "error",
                "settings_not_projectable",
                str(exc),
                entity_key=quiz_key,
                remediation="Resolve settings receipt validation, scope, type, or unsupported-setting errors.",
            )
        )

    registry = load_capability_registry()
    kind_caps = registry["question_kinds"]
    structures = {row["entity_key"]: row for row in model["structures"]}
    questions = {row["entity_key"]: row for row in model["questions"]}
    assets = {row["entity_key"]: row for row in model["assets"]}
    resolved, route_derivations = resolve_projection_relationships(model, quiz_key)
    if route_derivations:
        report["extensions"]["coursecraft.source_observed_route_derivations"] = route_derivations

    reachable = {quiz_key}
    changed = True
    while changed:
        changed = False
        for relation in resolved:
            if relation["kind"] == "contains" and relation["from_entity_key"] in reachable:
                target = relation["to_entity_key"]
                if target and target not in reachable:
                    reachable.add(target)
                    changed = True
    unresolved_reachable = [
        row
        for row in model["relationships"]
        if row["from_entity_key"] in reachable
        and row["status"] != "resolved"
        and row["kind"] in {"contains", "draws_from"}
    ]
    for relation in unresolved_reachable:
        issues.append(
            _issue(
                "error",
                "unresolved_structure_relationship",
                f"Relationship {relation['relationship_key']} is {relation['status']}.",
                entity_key=relation["from_entity_key"],
                remediation="Review and resolve or reject the structural relationship explicitly.",
            )
        )

    draws = sorted(
        (structures[key] for key in reachable if key in structures and structures[key]["kind"] == "draw"),
        key=lambda row: (row["ordinal"] is None, row["ordinal"] or 0, row["entity_key"]),
    )
    report["summary"]["reachable_draw_count"] = len(draws)
    if not draws:
        issues.append(
            _issue(
                "error",
                "no_reachable_draw",
                "Selected quiz has no resolved, reachable random draw structure.",
                entity_key=quiz_key,
                remediation="Approve a contains path from the quiz to at least one draw structure.",
            )
        )

    selected_pools: set[str] = set()
    selected_questions: list[dict[str, Any]] = []
    projected_banks: list[tuple[str, str]] = []
    projected_draws: list[tuple[int, str, str]] = []
    question_draws: dict[str, list[str]] = {}
    for draw_index, draw in enumerate(draws, start=1):
        draw_key = draw["entity_key"]
        if draw["selection"]["mode"] != "random":
            issues.append(
                _issue(
                    "error",
                    "unsupported_draw_mode",
                    f"Draw uses selection mode {draw['selection']['mode']!r}.",
                    entity_key=draw_key,
                    remediation="Use an explicitly approved random draw; fixed/all projection is not yet claimed.",
                )
            )
        pool_edges = [
            row
            for row in resolved
            if row["kind"] == "draws_from" and row["from_entity_key"] == draw_key
        ]
        if len(pool_edges) != 1 or pool_edges[0]["to_entity_key"] not in structures:
            issues.append(
                _issue(
                    "error",
                    "draw_pool_join_invalid",
                    f"Draw has {len(pool_edges)} resolved draws_from relationships.",
                    entity_key=draw_key,
                    remediation="Resolve exactly one draws_from relationship to a bank or pool.",
                )
            )
            continue
        pool = structures[pool_edges[0]["to_entity_key"]]
        if pool["kind"] not in {"bank", "pool"}:
            issues.append(
                _issue(
                    "error",
                    "draw_target_not_pool",
                    f"Draw targets structure kind {pool['kind']!r}.",
                    entity_key=draw_key,
                    remediation="Point draws_from to a reviewed bank or pool structure.",
                )
            )
            continue
        pool_key = pool["entity_key"]
        selected_pools.add(pool_key)
        bank_id = projected_bank_id(pool, draw_index)
        projected_banks.append((bank_id, pool_key))
        projected_draws.append((draw["ordinal"] if draw["ordinal"] is not None else draw_index, bank_id, draw_key))
        members = [
            questions[row["from_entity_key"]]
            for row in resolved
            if row["kind"] == "member_of"
            and row["to_entity_key"] == pool_key
            and row["from_entity_key"] in questions
        ]
        unresolved_members = [
            row
            for row in model["relationships"]
            if row["kind"] == "member_of"
            and row["to_entity_key"] == pool_key
            and row["status"] != "resolved"
        ]
        for relation in unresolved_members:
            issues.append(
                _issue(
                    "error",
                    "unresolved_pool_membership",
                    f"Pool membership {relation['relationship_key']} is {relation['status']}.",
                    entity_key=relation["from_entity_key"],
                    remediation="Resolve or reject the candidate membership before building.",
                )
            )
        if not members:
            issues.append(
                _issue(
                    "error",
                    "empty_resolved_pool",
                    "Pool has no resolved question members.",
                    entity_key=pool_key,
                    remediation="Approve at least one question membership.",
                )
            )
            continue
        requested = draw["selection"]["requested_count"]
        if requested is None or requested < 1 or requested > len(members):
            issues.append(
                _issue(
                    "error",
                    "invalid_draw_count",
                    f"Draw requested_count {requested!r} is not within 1..{len(members)}.",
                    entity_key=draw_key,
                    remediation="Set a positive draw count no larger than the resolved pool.",
                )
            )
        point_value = draw["selection"]["extensions"].get("points_per_question")
        if point_value is None:
            point_values = {str(row["scoring"]["maximum_points"]) for row in members}
            if len(point_values) != 1:
                issues.append(
                    _issue(
                        "error",
                        "mixed_draw_points_without_override",
                        "Draw members have mixed point values without points_per_question.",
                        entity_key=draw_key,
                        remediation="Approve selection.extensions.points_per_question explicitly.",
                    )
                )
        for question in members:
            selected_questions.append(question)
            question_draws.setdefault(question["entity_key"], []).append(draw_key)

    report["summary"]["selected_pool_count"] = len(selected_pools)
    unique_questions = {row["entity_key"]: row for row in selected_questions}
    report["summary"]["selected_question_count"] = len(unique_questions)
    capability_counts: dict[str, int] = {}
    permanent_codes: dict[str, list[str]] = {}
    for key, question in sorted(unique_questions.items()):
        capability = kind_caps.get(question["kind"], {"status": "unsupported"})
        capability_status = capability.get("status", "unsupported")
        capability_counts[capability_status] = capability_counts.get(capability_status, 0) + 1
        if len(question_draws.get(key, [])) > 1:
            issues.append(
                _issue(
                    "error",
                    "question_selected_by_multiple_draws",
                    f"Question is selected by {len(question_draws[key])} draws.",
                    entity_key=key,
                    remediation="Give each question one approved placement for the current projection.",
                )
            )
        if capability_status != "roundtrip_verified":
            issues.append(
                _issue(
                    "error",
                    "question_kind_not_buildable",
                    f"Question kind {question['kind']!r} is {capability_status}.",
                    entity_key=key,
                    remediation="Keep the question for review or add separately proven builder support; do not coerce its kind.",
                )
            )
        if question["build_support"]["level"] not in SAFE_BUILD_LEVELS:
            if (
                question["build_support"]["level"] == "extraction_only"
                and key in authorized_question_keys
            ):
                issues.append(
                    _issue(
                        "warning",
                        "phase5_candidate_not_evidence",
                        "Question is authorized only for the exact local Phase 5 candidate build.",
                        entity_key=key,
                        remediation="Do not call this instance importable or change build_support until live receipts exist.",
                    )
                )
            else:
                issues.append(
                    _issue(
                        "error",
                        "question_not_approved_for_build",
                        f"Question build_support is {question['build_support']['level']!r}.",
                        entity_key=key,
                        remediation="Record import/round-trip evidence or supply an exact Phase 5 candidate authorization; never promote evidence by preference.",
                    )
                )
        permanent_code = question["identity"].get("permanent_code")
        if not permanent_code:
            issues.append(
                _issue(
                    "error",
                    "missing_permanent_question_code",
                    "Question has no permanent identity code.",
                    entity_key=key,
                    remediation="Assign and review a durable question code before generation.",
                )
            )
        else:
            permanent_codes.setdefault(permanent_code, []).append(key)
        for code, message in question_projection_issues(
            question,
            randomize_answers=question_randomize.get(key, False),
        ):
            issues.append(
                _issue(
                    "error",
                    code,
                    message,
                    entity_key=key,
                    remediation="Revise the approved authoring fields without changing preserved source evidence.",
                )
            )
    report["question_capabilities"] = dict(sorted(capability_counts.items()))
    for code, message, owner in target_identifier_issues(
        [(question["identity"]["permanent_code"], key) for key, question in sorted(unique_questions.items())
         if question["identity"].get("permanent_code")],
        projected_banks,
        projected_draws,
    ):
        issues.append(_issue("error", code, message, entity_key=owner,
                             remediation="Review target identifier collisions; preserve canonical identities and supply distinct approved codes before generation."))
    for code, keys in sorted(permanent_codes.items()):
        if len(keys) > 1:
            issues.append(
                _issue(
                    "error",
                    "duplicate_permanent_question_code",
                    f"Permanent code {code!r} is assigned to {len(keys)} selected questions.",
                    remediation="Assign a unique durable code to each semantic question.",
                )
            )

    root = ([item.resolve() for item in asset_root] if isinstance(asset_root, (list, tuple))
            else (asset_root or model_path.parent).resolve())
    asset_keys: set[str] = set()
    resolved_assets: dict[str, dict[str, Any]] = {}
    package_paths: dict[str, tuple[str, str]] = {}
    for relation in model["relationships"]:
        if relation["kind"] != "uses_asset" or relation["from_entity_key"] not in unique_questions:
            continue
        if relation["status"] != "resolved":
            issues.append(
                _issue(
                    "error",
                    "unresolved_asset_relationship",
                    f"Asset relationship is {relation['status']}.",
                    entity_key=relation["from_entity_key"],
                    remediation="Resolve the asset target and source/package paths before building.",
                )
            )
            continue
        asset = assets.get(relation["to_entity_key"])
        if asset is None:
            issues.append(
                _issue(
                    "error",
                    "missing_asset_entity",
                    "Resolved uses_asset relationship has no asset entity.",
                    entity_key=relation["from_entity_key"],
                    remediation="Repair the relationship target in the reviewed model.",
                )
            )
            continue
        try:
            resolved_asset = resolve_model_asset(asset, asset_root=root)
        except Exception as exc:  # noqa: BLE001
            issues.append(
                _issue(
                    "error",
                    "asset_not_projectable",
                    str(exc),
                    entity_key=asset["entity_key"],
                    remediation="Provide a safe in-root source, safe package path, and matching checksum.",
                )
            )
            continue
        asset_keys.add(asset["entity_key"])
        resolved_assets[asset["entity_key"]] = resolved_asset
        path = resolved_asset["archive_path"].as_posix()
        previous = package_paths.get(path)
        current = (asset["entity_key"], resolved_asset["sha256"])
        if previous and previous[1] != current[1]:
            issues.append(
                _issue(
                    "error",
                    "asset_package_path_collision",
                    f"Different assets target archive member path {path!r}.",
                    entity_key=asset["entity_key"],
                    remediation="Assign distinct package paths or reconcile byte-identical reuse.",
                )
            )
        package_paths[path] = current
    report["summary"]["selected_asset_count"] = len(asset_keys)
    for code, message, owner in question_asset_reference_issues(
        list(unique_questions.values()), list(resolved_assets.values()), model["relationships"]
    ):
        issues.append(_issue("error", code, message, entity_key=owner,
                             remediation="Bind each local HTML reference to exactly one resolved in-root asset at the unchanged package path, or review the unsupported delivery profile."))

    if model["source"]["source_lineage_key"].startswith("cc:lineage:unresolved:"):
        issues.append(
            _issue(
                "warning",
                "unresolved_source_lineage",
                "Source lineage is unresolved, so refresh-stable reconciliation is not dependable.",
                remediation="Assign a defensible course/package lineage before relying on future refresh matching.",
            )
        )

    if not any(item["severity"] == "error" for item in issues):
        try:
            load_authoring_projection(
                model_path,
                quiz_entity_key=quiz_entity_key,
                settings_path=settings_path,
                asset_root=asset_root,
                promotion_receipt_path=promotion_receipt_path,
                trial_authorization_path=trial_authorization_path,
            )
        except Exception as exc:  # noqa: BLE001
            issues.append(
                _issue(
                    "error",
                    "strict_projection_disagreement",
                    str(exc),
                    entity_key=quiz_key,
                    remediation="Treat this as a readiness/builder consistency defect before generation.",
                )
            )
    return _finish(report)


def _finish(report: dict[str, Any]) -> dict[str, Any]:
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    report["issues"] = sorted(
        report["issues"],
        key=lambda row: (severity_rank.get(row["severity"], 9), row["code"], row.get("entity_key") or ""),
    )
    report["summary"]["error_count"] = sum(row["severity"] == "error" for row in report["issues"])
    report["summary"]["warning_count"] = sum(row["severity"] == "warning" for row in report["issues"])
    report["ready"] = report["summary"]["error_count"] == 0 and report["selected_quiz_key"] is not None
    return report


def render_readiness_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Quiz Authoring Readiness",
        "",
        f"- Model: `{report['source_model']}`",
        f"- Selected quiz: `{report['selected_quiz_key'] or '(not selected)'}`",
        f"- Ready for local generation: {'yes' if report['ready'] else 'no'}",
        f"- Errors: {summary['error_count']}",
        f"- Warnings: {summary['warning_count']}",
        f"- Reachable draws: {summary['reachable_draw_count']}",
        f"- Selected pools: {summary['selected_pool_count']}",
        f"- Selected questions: {summary['selected_question_count']}",
        f"- Selected assets: {summary['selected_asset_count']}",
        "",
        "This report is content-minimizing: it records identities, relationship",
        "states, capability levels, and remediation without question or answer text.",
        "",
        "## Issues",
        "",
    ]
    if not report["issues"]:
        lines.append("- None.")
    for item in report["issues"]:
        target = f" (`{item['entity_key']}`)" if item.get("entity_key") else ""
        lines.append(f"- **{item['severity'].upper()} `{item['code']}`**{target}: {item['message']}")
        lines.append(f"  - Remediation: {item['remediation']}")
    lines.extend(["", "## Question Capability Counts", ""])
    if report["question_capabilities"]:
        for status, count in report["question_capabilities"].items():
            lines.append(f"- `{status}`: {count}")
    else:
        lines.append("- No questions reached through a valid draw/pool graph.")
    lines.append("")
    return "\n".join(lines)


def safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "quiz_model"
