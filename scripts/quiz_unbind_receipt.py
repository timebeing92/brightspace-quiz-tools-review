#!/usr/bin/env python3
"""Build and validate the provisional Workbench Quiz Unbind run envelope.

``coursecraft.quiz_unbind_run/0`` is deliberately a version-zero draft.  It is
implementation evidence for a Workbench-owned composition receipt; it is not a
promoted contract and it does not replace any canonical quiz contract.

The envelope is content-minimizing.  It records source identity, counts, codes,
relative paths, hashes, and tool facts.  Authored question and answer content
remains in the local review artifacts referenced by the envelope.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable


WORKBENCH_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = "coursecraft.quiz_unbind_run/0"
SCHEMA_PATH = (
    WORKBENCH_ROOT
    / "workspace"
    / "reference"
    / "schemas"
    / "quiz"
    / "quiz_unbind_run_schema.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_relative(path: Path, run_dir: Path) -> str:
    """Return a run-relative POSIX path, refusing an escaping target."""
    resolved_run = run_dir.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_run).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact is outside the Unbind run: {path.name}") from exc


def safe_relative_path(base_dir: Path, relative: str, *, label: str = "artifact") -> Path:
    """Resolve a recorded relative POSIX path inside ``base_dir``.

    Absolute paths, parent traversal, control characters, an escaping resolved
    target, and symlinked targets are refused.  Callers may use a narrower base
    (for example, ``extraction/``) to bind a nested receipt to its own scope.
    """
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path must be a nonempty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in relative):
        raise ValueError(f"{label} path contains control characters")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError(f"unsafe {label} path: {relative}")
    candidate = base_dir.joinpath(*pure.parts)
    try:
        candidate.resolve().relative_to(base_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} path escapes its declared scope: {relative}") from exc
    if candidate.is_symlink():
        raise ValueError(f"{label} path is a symlink: {relative}")
    return candidate


def artifact_ref(path: Path, run_dir: Path, identity: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"cannot record missing or symlinked artifact: {path.name}")
    return {
        "path": run_relative(path, run_dir),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "identity": identity,
    }


def _tool_ref(path: Path, name: str, role: str) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"cannot record missing producer tool: {path.name}")
    return {
        "name": name,
        "path": path.resolve().relative_to(WORKBENCH_ROOT.resolve()).as_posix(),
        "sha256": sha256_file(path),
        "role": role,
    }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _fidelity_block(
    model: dict[str, Any], projection: dict[str, Any]
) -> dict[str, Any]:
    """Report semantic fidelity beside, and separate from, hash verification.

    The 2026-08-03 ANAT run reported a verified close while thirty entities
    held more than one authored question, and no field in the envelope showed
    it.  Verification answers whether the bytes are intact; this answers
    whether the meaning is.
    """
    from quiz_normalization import variant_collision_report

    report = variant_collision_report(model)
    summary = projection.get("summary", {})
    return {
        "state": report["state"],
        "approval_safe": report["approval_safe"],
        "variant_collision_count": report["variant_collision_count"],
        "collided_occurrence_count": report["collided_occurrence_count"],
        "authored_variant_class_count": int(
            summary.get("authored_variant_class_count", 0)
        ),
        "reused_authored_variant_class_count": int(
            summary.get("reused_authored_variant_class_count", 0)
        ),
    }


def build_unbind_record(
    *,
    run_dir: Path,
    model_path: Path,
    extraction_receipt_path: Path,
    readiness_path: Path,
    readiness_markdown_path: Path,
    station_data_path: Path,
    station_html_path: Path,
    review_projection_path: Path,
    reading_room_data_path: Path,
    reading_room_html_path: Path,
    summary_markdown_path: Path,
    registry_path: Path,
    asset_mode: str,
    requested_quiz_entity_key: str | None,
    generated_at: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an unverified draft envelope from canonical producer outputs."""
    if asset_mode not in {"self_contained", "reference"}:
        raise ValueError(f"unsupported Unbind asset mode: {asset_mode}")

    model = _read_json(model_path, "quiz model")
    receipt = _read_json(extraction_receipt_path, "extraction receipt")
    readiness = _read_json(readiness_path, "readiness report")
    projection = _read_json(review_projection_path, "review projection")
    reading_room = _read_json(reading_room_data_path, "Reading Room data")
    if model.get("schema") != "coursecraft.quiz/1":
        raise ValueError("Unbind requires a coursecraft.quiz/1 model")
    if receipt.get("schema") != "coursecraft.quiz_run/1":
        raise ValueError("Unbind requires a coursecraft.quiz_run/1 extraction receipt")
    if projection.get("schema") != "coursecraft.quiz_review_projection/0":
        raise ValueError(
            "Unbind requires a coursecraft.quiz_review_projection/0 review projection"
        )
    if reading_room.get("format") != "coursecraft.quiz_review_station/0":
        raise ValueError(
            "Unbind requires a coursecraft.quiz_review_station/0 Reading Room"
        )

    inputs = receipt.get("inputs", [])
    if len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise ValueError("extraction receipt must declare exactly one source input")
    source_input = inputs[0]
    source = receipt.get("source", {})
    source_extensions = source.get("extensions", {})
    quiz_scope = source_extensions.get("quiz_scope", {})
    if not isinstance(quiz_scope, dict) or not quiz_scope.get("coverage_kind"):
        raise ValueError("canonical extraction receipt did not record quiz source scope")
    rows = [row for row in receipt.get("artifacts", []) if isinstance(row, dict)]
    emitted_rows = [row for row in rows if row.get("status") == "emitted"]
    review_rows = [row for row in emitted_rows if row.get("role") == "review"]
    model_rows = [row for row in emitted_rows if row.get("role") == "model"]
    copied_asset_rows = [
        row for row in review_rows if "_assets/" in str(row.get("path", ""))
    ]
    issues = [row for row in readiness.get("issues", []) if isinstance(row, dict)]
    blocker_codes = [
        str(row.get("code")) for row in issues if row.get("severity") == "error"
    ]
    warning_codes = [
        str(row.get("code")) for row in issues if row.get("severity") == "warning"
    ]
    readiness_summary = readiness.get("summary", {})

    tool_specs = (
        (Path(__file__).resolve(), "quiz_unbind_receipt", "producer"),
        (WORKBENCH_ROOT / "scripts" / "quiz_unbind.py", "quiz_unbind", "orchestrator"),
        (
            WORKBENCH_ROOT / "scripts" / "verify_quiz_unbind_run.py",
            "verify_quiz_unbind_run",
            "verifier",
        ),
        (
            WORKBENCH_ROOT / "scripts" / "extract_quiz_pool_review.py",
            "extract_quiz_pool_review",
            "producer",
        ),
        (
            WORKBENCH_ROOT / "scripts" / "check_quiz_authoring_readiness.py",
            "check_quiz_authoring_readiness",
            "producer",
        ),
        (
            WORKBENCH_ROOT / "scripts" / "quiz_binder_station.py",
            "quiz_binder_station",
            "renderer",
        ),
        (
            WORKBENCH_ROOT / "scripts" / "quiz_review_projection.py",
            "quiz_review_projection",
            "producer",
        ),
        (
            WORKBENCH_ROOT / "scripts" / "quiz_review_station.py",
            "quiz_review_station",
            "renderer",
        ),
        (SCHEMA_PATH, "quiz_unbind_run_schema", "contract"),
    )
    registry_sha = sha256_file(registry_path)
    run_token = sha256_file(extraction_receipt_path)[:24]
    record: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "contract_status": "draft_unratified",
        "run_id": f"cc:run:quiz-unbind:{run_token}",
        "generated_at": generated_at,
        "producer": {
            "name": "coursecraft-workbench quiz unbind",
            "version": "0",
            "commit": receipt.get("producer", {}).get("commit"),
            "tree_state": str(
                receipt.get("producer", {})
                .get("extensions", {})
                .get("tree_state", "unknown")
            ),
            "invocation": "module_api",
            "canonical_extractor": "extract_quiz_pool_review",
            "canonical_readiness": "check_quiz_authoring_readiness",
            "station_renderer": "quiz_binder_station",
            "review_projection": "quiz_review_projection",
            "reading_room_renderer": "quiz_review_station",
            "capability_registry": {
                "path": registry_path.resolve()
                .relative_to(WORKBENCH_ROOT.resolve())
                .as_posix(),
                "sha256": registry_sha,
                "pinned": True,
            },
            "tools": [_tool_ref(path, name, role) for path, name, role in tool_specs],
        },
        "source_export": {
            "reference": str(source_input.get("path", "")),
            "sha256": str(source_input.get("sha256", "")),
            "bytes": int(source_input.get("bytes", 0)),
            "file_count": int(
                source_extensions.get(
                    "file_count", source_input.get("extensions", {}).get("file_count", 0)
                )
            ),
            "intake_kind": str(source_extensions.get("intake_kind", "")),
            "source_key": str(source.get("source_key", "")),
            "source_lineage_key": str(source.get("source_lineage_key", "")),
            "lineage_basis": str(source_extensions.get("lineage_basis", "unknown")),
            "asset_mode": asset_mode,
            "copied_asset_count": len(copied_asset_rows),
            "scope": {
                "coverage_kind": str(quiz_scope.get("coverage_kind", "")),
                "questiondb_present": bool(quiz_scope.get("questiondb_present")),
                "inline_occurrence_count": int(
                    quiz_scope.get("inline_occurrence_count", 0)
                ),
                "itemref_occurrence_count": int(
                    quiz_scope.get("itemref_occurrence_count", 0)
                ),
                "resolved_itemref_count": int(
                    quiz_scope.get("resolved_itemref_count", 0)
                ),
                "unresolved_itemref_count": int(
                    quiz_scope.get("unresolved_itemref_count", 0)
                ),
            },
        },
        "selection": {
            "requested_quiz_entity_key": requested_quiz_entity_key or None,
            "selected_quiz_entity_key": readiness.get("selected_quiz_key"),
        },
        "model_summary": {
            "quiz_count": len(model.get("quizzes", [])),
            # Reviewable question entities. Question-library coverage records
            # are counted separately so the headline stays comparable with the
            # projection, the station, the Reading Room, and the workbook.
            "question_entity_count": int(
                projection.get("summary", {}).get("unique_question_count", 0)
            ),
            "question_occurrence_count": int(
                projection.get("summary", {}).get("question_occurrence_count", 0)
            ),
            "library_only_question_entity_count": int(
                projection.get("summary", {}).get("library_question_count", 0)
            ),
            "diagnostic_count": len(model.get("diagnostics", [])),
            "projection_diagnostic_count": len(projection.get("diagnostics", [])),
        },
        "fidelity": _fidelity_block(model, projection),
        "artifacts": {
            "model": artifact_ref(model_path, run_dir, "coursecraft.quiz/1"),
            "extraction_receipt": artifact_ref(
                extraction_receipt_path, run_dir, "coursecraft.quiz_run/1"
            ),
            "readiness_report": artifact_ref(
                readiness_path,
                run_dir,
                str(readiness.get("report_type", "coursecraft.quiz_authoring_readiness/1")),
            ),
            "readiness_markdown": artifact_ref(
                readiness_markdown_path, run_dir, "text/markdown"
            ),
            "station_data": artifact_ref(
                station_data_path, run_dir, "quiz-binder-local-station-v0"
            ),
            "station_html": artifact_ref(station_html_path, run_dir, "text/html"),
            "review_projection": artifact_ref(
                review_projection_path,
                run_dir,
                "coursecraft.quiz_review_projection/0",
            ),
            "reading_room_data": artifact_ref(
                reading_room_data_path,
                run_dir,
                "coursecraft.quiz_review_station/0",
            ),
            "reading_room_html": artifact_ref(
                reading_room_html_path, run_dir, "text/html"
            ),
            "summary_markdown": artifact_ref(
                summary_markdown_path, run_dir, "text/markdown"
            ),
        },
        "extraction_inventory": {
            "declared_artifact_count": len(emitted_rows),
            "review_artifact_count": len(review_rows),
            "model_artifact_count": len(model_rows),
            "copied_asset_count": len(copied_asset_rows),
        },
        "readiness": {
            "report_type": str(
                readiness.get("report_type", "coursecraft.quiz_authoring_readiness/1")
            ),
            "ready": bool(readiness.get("ready", False)),
            "error_count": int(readiness_summary.get("error_count", len(blocker_codes))),
            "warning_count": int(
                readiness_summary.get("warning_count", len(warning_codes))
            ),
            "blocker_codes": blocker_codes,
            "warning_codes": warning_codes,
        },
        "verification": {
            "mode": "recursive_extraction_receipt",
            "status": "pending",
            "verified_at": None,
            "direct_artifact_count": 0,
            "extraction_artifact_count": 0,
            "total_reference_count": 0,
        },
        "evidence_level": "extraction_only",
        "non_build_guarantee": {
            "statement": (
                "This run completed local evidence extraction, readiness analysis, "
                "and review rendering only. No quiz package was built, sealed, or imported."
            ),
            "build_performed": False,
            "instance_state": "extraction_only",
        },
        "privacy": {
            "class": "local_course_evidence",
            "receipt_records_authored_text": False,
            "run_contains_course_content": True,
            "sharing_requires_review": True,
        },
        "brightspace": {
            "roundtrip": "not_performed",
            "network_operations": 0,
            "brightspace_operations": 0,
        },
        "events": list(events),
        "extensions": {},
    }
    return record


def load_schema() -> dict[str, Any]:
    return _read_json(SCHEMA_PATH, "Unbind run schema")


def validate_unbind_record(
    record: dict[str, Any], schema: dict[str, Any] | None = None
) -> list[str]:
    """Return schema-validation findings; an empty list means valid."""
    schema = schema or load_schema()
    try:
        import jsonschema  # type: ignore

        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        return [
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in sorted(validator.iter_errors(record), key=lambda item: list(item.path))
        ]
    except ModuleNotFoundError:
        return _fallback_validate(record, schema, schema, "<root>")


def _resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    node: Any = root_schema
    for part in reference.lstrip("#/").split("/"):
        node = node[part]
    return node


def _fallback_validate(
    value: Any, node: dict[str, Any], root_schema: dict[str, Any], path: str
) -> list[str]:
    """Small dependency-free validator for the schema features used here."""
    findings: list[str] = []
    if "$ref" in node:
        node = _resolve_ref(root_schema, node["$ref"])
    expected = node.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, candidate) for candidate in types):
            return [f"{path}: expected type {expected}, got {type(value).__name__}"]
    if "const" in node and value != node["const"]:
        findings.append(f"{path}: expected const {node['const']!r}, got {value!r}")
    if "enum" in node and value not in node["enum"]:
        findings.append(f"{path}: {value!r} is not in {node['enum']!r}")
    if "pattern" in node and isinstance(value, str):
        if re.search(str(node["pattern"]), value) is None:
            findings.append(f"{path}: value does not match {node['pattern']!r}")
    if "minimum" in node and isinstance(value, (int, float)):
        if value < node["minimum"]:
            findings.append(f"{path}: {value} is below minimum {node['minimum']}")
    if "minLength" in node and isinstance(value, str) and len(value) < node["minLength"]:
        findings.append(f"{path}: string is shorter than {node['minLength']}")

    if isinstance(value, dict) and (node.get("type") == "object" or "properties" in node):
        properties = node.get("properties", {})
        for required in node.get("required", []):
            if required not in value:
                findings.append(f"{path}: missing required property {required!r}")
        if node.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    findings.append(f"{path}: unexpected property {key!r}")
        for key, child in properties.items():
            if key in value:
                findings.extend(
                    _fallback_validate(value[key], child, root_schema, f"{path}/{key}")
                )
    if isinstance(value, list) and node.get("type") == "array":
        if len(value) < int(node.get("minItems", 0)):
            findings.append(f"{path}: array has fewer than {node['minItems']} items")
        child = node.get("items")
        if child:
            for index, item in enumerate(value):
                findings.extend(
                    _fallback_validate(item, child, root_schema, f"{path}[{index}]")
                )
    return findings


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def ensure_unique_paths(rows: Iterable[dict[str, Any]], *, label: str) -> None:
    """Refuse duplicate declared paths in a receipt inventory."""
    seen: set[str] = set()
    for row in rows:
        path = str(row.get("path", ""))
        if path in seen:
            raise ValueError(f"duplicate {label} path: {path}")
        seen.add(path)
