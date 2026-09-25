#!/usr/bin/env python3
"""Shared identity and validation helpers for the CourseCraft quiz contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz"
SCHEMA_REGISTRY = {
    "coursecraft.quiz/1": SCHEMA_ROOT / "quiz_model_schema.json",
    "coursecraft.quiz_run/1": SCHEMA_ROOT / "quiz_run_schema.json",
    "coursecraft.quiz_settings/1": SCHEMA_ROOT / "quiz_settings_schema.json",
}
ENTITY_COLLECTIONS = ("quizzes", "structures", "questions", "assets", "resources")
SETTING_LAYER_RANK = {
    "source_observation": -1,
    "hard_default": 0,
    "profile": 1,
    "quiz_decision": 2,
    "question_override": 3,
}


@dataclass(frozen=True)
class ContractIssue:
    severity: str
    code: str
    path: str
    message: str

    def render(self) -> str:
        location = self.path or "$"
        return f"{self.severity}: {self.code} at {location}: {self.message}"


def _json_path(parts: Iterable[object]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def load_schema(schema_id: str) -> dict[str, Any]:
    path = SCHEMA_REGISTRY[schema_id]
    return json.loads(path.read_text(encoding="utf-8"))


def check_schema_documents() -> None:
    for schema_id in SCHEMA_REGISTRY:
        Draft202012Validator.check_schema(load_schema(schema_id))


def _schema_issues(record: dict[str, Any], schema_id: str) -> list[ContractIssue]:
    validator = Draft202012Validator(load_schema(schema_id), format_checker=FormatChecker())
    issues: list[ContractIssue] = []
    for error in sorted(validator.iter_errors(record), key=lambda item: list(item.absolute_path)):
        issues.append(
            ContractIssue(
                severity="error",
                code="schema_validation",
                path=_json_path(error.absolute_path),
                message=error.message,
            )
        )
    return issues


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _append_duplicates(
    issues: list[ContractIssue],
    values: Iterable[str],
    *,
    path: str,
    code: str,
) -> None:
    for duplicate in sorted(_duplicates(values)):
        issues.append(ContractIssue("error", code, path, f"duplicate identifier {duplicate!r}"))


def _walk_reference_fields(value: Any, path: tuple[object, ...] = ()) -> Iterable[tuple[str, list[str], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            if key.endswith("_evidence_keys") and isinstance(child, list):
                yield "evidence", [item for item in child if isinstance(item, str)], _json_path(child_path)
            elif key == "diagnostic_ids" and isinstance(child, list):
                yield "diagnostic", [item for item in child if isinstance(item, str)], _json_path(child_path)
            yield from _walk_reference_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_reference_fields(child, (*path, index))


def _validate_model_semantics(record: dict[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    entities: dict[str, dict[str, Any]] = {}
    entity_occurrences: list[str] = []
    for collection_name in ENTITY_COLLECTIONS:
        for entity in record[collection_name]:
            entity_key = entity["entity_key"]
            entity_occurrences.append(entity_key)
            entities.setdefault(entity_key, entity)
    _append_duplicates(
        issues,
        entity_occurrences,
        path="$.quizzes|structures|questions|assets|resources",
        code="duplicate_entity_key",
    )

    evidence = {row["evidence_key"]: row for row in record["evidence"]}
    diagnostics = {row["diagnostic_id"]: row for row in record["diagnostics"]}
    _append_duplicates(
        issues,
        (row["evidence_key"] for row in record["evidence"]),
        path="$.evidence",
        code="duplicate_evidence_key",
    )
    _append_duplicates(
        issues,
        (row["diagnostic_id"] for row in record["diagnostics"]),
        path="$.diagnostics",
        code="duplicate_diagnostic_id",
    )
    _append_duplicates(
        issues,
        (row["relationship_key"] for row in record["relationships"]),
        path="$.relationships",
        code="duplicate_relationship_key",
    )
    _append_duplicates(
        issues,
        (row["lineage_id"] for row in record["lineage"]),
        path="$.lineage",
        code="duplicate_lineage_id",
    )
    _append_duplicates(
        issues,
        (row["annotation_id"] for row in record["annotations"]),
        path="$.annotations",
        code="duplicate_annotation_id",
    )

    source_key = record["source"]["source_key"]
    for index, row in enumerate(record["evidence"]):
        if row["source_key"] != source_key:
            issues.append(
                ContractIssue(
                    "error",
                    "foreign_evidence_source",
                    f"$.evidence[{index}].source_key",
                    "evidence source_key must match the model source_key",
                )
            )

    for reference_kind, references, path in _walk_reference_fields(record):
        available = evidence if reference_kind == "evidence" else diagnostics
        for reference in references:
            if reference not in available:
                issues.append(
                    ContractIssue(
                        "error",
                        f"missing_{reference_kind}_reference",
                        path,
                        f"unknown {reference_kind} key {reference!r}",
                    )
                )

    for index, relationship in enumerate(record["relationships"]):
        source = relationship["from_entity_key"]
        target = relationship["to_entity_key"]
        if source not in entities:
            issues.append(
                ContractIssue(
                    "error",
                    "missing_relationship_source",
                    f"$.relationships[{index}].from_entity_key",
                    f"unknown entity key {source!r}",
                )
            )
        if target is not None and target not in entities:
            issues.append(
                ContractIssue(
                    "error",
                    "missing_relationship_target",
                    f"$.relationships[{index}].to_entity_key",
                    f"unknown entity key {target!r}",
                )
            )
        for candidate_index, candidate in enumerate(relationship["candidates"]):
            if candidate["entity_key"] not in entities:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_relationship_candidate",
                        f"$.relationships[{index}].candidates[{candidate_index}].entity_key",
                        f"unknown entity key {candidate['entity_key']!r}",
                    )
                )

    for index, question in enumerate(record["questions"]):
        if question["kind"] == "unknown" and not question["source_evidence_keys"]:
            issues.append(
                ContractIssue(
                    "error",
                    "unknown_question_without_evidence",
                    f"$.questions[{index}].source_evidence_keys",
                    "unknown question kinds require preserved source evidence",
                )
            )

    for collection_name in ("lineage", "settings_observations", "annotations"):
        target_field = {
            "lineage": "subject_entity_key",
            "settings_observations": "target_entity_key",
            "annotations": "target_entity_key",
        }[collection_name]
        for index, row in enumerate(record[collection_name]):
            target = row[target_field]
            if target not in entities:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_entity_reference",
                        f"$.{collection_name}[{index}].{target_field}",
                        f"unknown entity key {target!r}",
                    )
                )

    for index, diagnostic in enumerate(record["diagnostics"]):
        for entity_key in diagnostic["entity_keys"]:
            if entity_key not in entities:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_diagnostic_entity",
                        f"$.diagnostics[{index}].entity_keys",
                        f"unknown entity key {entity_key!r}",
                    )
                )
    return issues


def _validate_run_semantics(record: dict[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    artifacts = [*record["inputs"], *record["artifacts"]]
    artifact_paths = {row["path"] for row in artifacts}
    diagnostic_ids = {row["diagnostic_id"] for row in record["diagnostics"]}
    _append_duplicates(
        issues,
        (row["path"] for row in record["inputs"]),
        path="$.inputs",
        code="duplicate_input_path",
    )
    _append_duplicates(
        issues,
        (row["path"] for row in record["artifacts"]),
        path="$.artifacts",
        code="duplicate_artifact_path",
    )
    _append_duplicates(
        issues,
        (row["name"] for row in record["capabilities"]),
        path="$.capabilities",
        code="duplicate_capability",
    )
    _append_duplicates(
        issues,
        (row["schema"] for row in record["contract_versions"]),
        path="$.contract_versions",
        code="duplicate_contract_version",
    )
    for collection_name in ("inputs", "artifacts"):
        for index, artifact in enumerate(record[collection_name]):
            if artifact["status"] == "emitted" and (
                artifact["sha256"] is None or artifact["bytes"] is None
            ):
                issues.append(
                    ContractIssue(
                        "error",
                        "emitted_artifact_without_checksum",
                        f"$.{collection_name}[{index}]",
                        "emitted artifacts require both sha256 and byte count",
                    )
                )
    for index, contract in enumerate(record["contract_versions"]):
        schema_id = contract["schema"]
        if schema_id not in SCHEMA_REGISTRY:
            continue
        actual_digest = hashlib.sha256(SCHEMA_REGISTRY[schema_id].read_bytes()).hexdigest()
        if contract["schema_sha256"] != actual_digest:
            issues.append(
                ContractIssue(
                    "error",
                    "schema_receipt_hash_mismatch",
                    f"$.contract_versions[{index}].schema_sha256",
                    f"recorded hash does not match the registered {schema_id} schema",
                )
            )
    for index, capability in enumerate(record["capabilities"]):
        for path in capability["artifact_paths"]:
            if path not in artifact_paths:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_capability_artifact",
                        f"$.capabilities[{index}].artifact_paths",
                        f"artifact path {path!r} is not recorded",
                    )
                )
        for diagnostic_id in capability["diagnostic_ids"]:
            if diagnostic_id not in diagnostic_ids:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_capability_diagnostic",
                        f"$.capabilities[{index}].diagnostic_ids",
                        f"diagnostic {diagnostic_id!r} is not recorded",
                    )
                )
    return issues


def _validate_settings_semantics(record: dict[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    inputs = {row["input_id"]: row for row in record["inputs"]}
    _append_duplicates(
        issues,
        (row["input_id"] for row in record["inputs"]),
        path="$.inputs",
        code="duplicate_settings_input",
    )
    resolution_keys = [f"{row['target_entity_key']}::{row['setting']}" for row in record["resolutions"]]
    _append_duplicates(
        issues,
        resolution_keys,
        path="$.resolutions",
        code="duplicate_settings_resolution",
    )
    for index, resolution in enumerate(record["resolutions"]):
        path = f"$.resolutions[{index}]"
        considered = resolution["considered_input_ids"]
        for input_id in considered:
            if input_id not in inputs:
                issues.append(
                    ContractIssue(
                        "error",
                        "missing_settings_input",
                        f"{path}.considered_input_ids",
                        f"input {input_id!r} is not recorded",
                    )
                )
        winner_id = resolution["winner_input_id"]
        if resolution["state"] == "known" and winner_id is None:
            issues.append(
                ContractIssue(
                    "error",
                    "known_setting_without_winner",
                    f"{path}.winner_input_id",
                    "a known effective setting must name its winning input",
                )
            )
            continue
        if winner_id is None:
            continue
        if winner_id not in inputs:
            issues.append(
                ContractIssue(
                    "error",
                    "missing_settings_winner",
                    f"{path}.winner_input_id",
                    f"input {winner_id!r} is not recorded",
                )
            )
            continue
        if winner_id not in considered:
            issues.append(
                ContractIssue(
                    "error",
                    "unconsidered_settings_winner",
                    f"{path}.winner_input_id",
                    "winning input must be listed among considered_input_ids",
                )
            )
        winner = inputs[winner_id]
        if winner["setting"] != resolution["setting"] or winner["target_entity_key"] != resolution["target_entity_key"]:
            issues.append(
                ContractIssue(
                    "error",
                    "settings_winner_scope_mismatch",
                    f"{path}.winner_input_id",
                    "winning input must match the resolution setting and target",
                )
            )
        if resolution["state"] == "known" and winner["state"] == "known" and resolution["effective_value"] != winner["value"] and not resolution["coerced"]:
            issues.append(
                ContractIssue(
                    "error",
                    "settings_value_mismatch",
                    f"{path}.effective_value",
                    "effective value differs from the winner without a coercion receipt",
                )
            )
        applicable = [
            inputs[input_id]
            for input_id in considered
            if input_id in inputs
            and inputs[input_id]["setting"] == resolution["setting"]
            and inputs[input_id]["target_entity_key"] == resolution["target_entity_key"]
            and inputs[input_id]["state"] == "known"
        ]
        if applicable:
            maximum_rank = max(SETTING_LAYER_RANK[row["layer"]] for row in applicable)
            if SETTING_LAYER_RANK[winner["layer"]] != maximum_rank:
                issues.append(
                    ContractIssue(
                        "error",
                        "settings_precedence_violation",
                        f"{path}.winner_input_id",
                        "winner is not the highest-precedence applicable input",
                    )
                )
        if resolution["defaulted"] != (winner["layer"] == "hard_default"):
            issues.append(
                ContractIssue(
                    "error",
                    "settings_default_receipt_mismatch",
                    f"{path}.defaulted",
                    "defaulted must be true exactly when the hard-default layer wins",
                )
            )
        if resolution["coerced"] and not resolution["coercion_note"]:
            issues.append(
                ContractIssue(
                    "error",
                    "missing_coercion_note",
                    f"{path}.coercion_note",
                    "coerced settings require a coercion note",
                )
            )
    return issues


def validate_contract(record: dict[str, Any], *, mode: str = "inspect") -> list[ContractIssue]:
    """Validate a quiz-family record.

    Inspection mode warns and preserves an unknown contract version. Transform
    mode treats an unknown version as unsafe because it cannot prove semantics.
    """

    if mode not in {"inspect", "transform"}:
        raise ValueError("mode must be 'inspect' or 'transform'")
    schema_id = record.get("schema")
    if schema_id not in SCHEMA_REGISTRY:
        severity = "warning" if mode == "inspect" else "error"
        return [
            ContractIssue(
                severity,
                "unknown_contract_version",
                "$.schema",
                f"contract {schema_id!r} is not registered; preserve it without transformation",
            )
        ]

    issues = _schema_issues(record, str(schema_id))
    if issues:
        return issues
    if schema_id == "coursecraft.quiz/1":
        issues.extend(_validate_model_semantics(record))
    elif schema_id == "coursecraft.quiz_run/1":
        issues.extend(_validate_run_semantics(record))
    elif schema_id == "coursecraft.quiz_settings/1":
        issues.extend(_validate_settings_semantics(record))
    return issues


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._~-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "unnamed"


def make_source_key(sha256_digest: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", sha256_digest):
        raise ValueError("source digest must be a lowercase SHA-256 hex string")
    return f"cc:source:{sha256_digest}"


def make_entity_key(
    entity_type: str,
    source_lineage_key: str,
    *,
    permanent_code: str | None = None,
    alias_namespace: str | None = None,
    alias_value: str | None = None,
) -> str:
    """Create a refresh-stable entity key without using mutable content.

    Permanent question codes take priority. Otherwise a stable source alias is
    hashed with the source-lineage key. The export fingerprint is deliberately
    absent, so a refreshed export does not mint new entity identities.
    """

    lineage_token = _slug(source_lineage_key.removeprefix("cc:"))
    entity_token = _slug(entity_type)
    if permanent_code:
        code_token = _slug(permanent_code)
        suffix = hashlib.sha256(permanent_code.encode("utf-8")).hexdigest()[:8]
        return f"cc:{entity_token}:{lineage_token}:{code_token}-{suffix}"
    if not alias_namespace or not alias_value:
        raise ValueError("provide permanent_code or both alias_namespace and alias_value")
    basis = json.dumps(
        [source_lineage_key, entity_type, alias_namespace, alias_value],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
    return f"cc:{entity_token}:{lineage_token}:source-{digest}"


def make_content_fingerprint(value: Any, *, basis: str) -> dict[str, Any]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "digest": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "basis": basis,
        "extensions": {},
    }
