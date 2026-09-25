#!/usr/bin/env python3
"""Verify a provisional ``coursecraft.quiz_unbind_run/0`` evidence folder.

Verification is recursive: the top-level Unbind record is schema-checked and
all ten direct references are re-hashed; the canonical extraction receipt is
then loaded and every emitted artifact it declares (workbooks, JSON, Markdown,
model, and copied assets) is independently resolved, size-checked, and
re-hashed.  Every static Reading Room shard declared by its direct manifest is
verified the same way.  Cross-record checks bind the model, readiness report,
station, and capability registry facts to that same run.

These local receipts are tamper-evident, not externally signed.  A party able to
regenerate the entire directory and every hash remains outside this local trust
model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from quiz_unbind_receipt import (
    SCHEMA_ID,
    ensure_unique_paths,
    safe_relative_path,
    sha256_file,
    validate_unbind_record,
)


RECORD_NAME = "quiz_unbind_run.json"
DIRECT_ARTIFACT_KEYS = (
    "model",
    "extraction_receipt",
    "readiness_report",
    "readiness_markdown",
    "station_data",
    "station_html",
    "review_projection",
    "reading_room_data",
    "reading_room_html",
    "summary_markdown",
)


class UnbindVerificationError(Exception):
    """A content-minimized verified-close refusal."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnbindVerificationError(f"cannot read {label}: {exc.strerror or 'I/O error'}") from exc
    scrubbed = raw.replace("\n", "").replace("\r", "").replace("\t", "")
    if any(ord(character) < 32 or ord(character) == 127 for character in scrubbed):
        raise UnbindVerificationError(f"{label} contains terminal control characters")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnbindVerificationError(f"{label} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise UnbindVerificationError(f"{label} must be a JSON object")
    return value


def _load_record(run_dir: Path) -> dict[str, Any]:
    record_path = run_dir / RECORD_NAME
    if not record_path.is_file() or record_path.is_symlink():
        raise UnbindVerificationError(f"{RECORD_NAME} is missing or symlinked")
    return _read_json(record_path, RECORD_NAME)


def _check_internal_consistency(record: dict[str, Any], *, allow_pending: bool) -> None:
    if record.get("schema") != SCHEMA_ID:
        raise UnbindVerificationError(f"unexpected Unbind schema: {record.get('schema')!r}")
    if record.get("contract_status") != "draft_unratified":
        raise UnbindVerificationError("version-zero contract status must be draft_unratified")
    if record.get("evidence_level") != "extraction_only":
        raise UnbindVerificationError("evidence_level must remain extraction_only")

    guarantee = record.get("non_build_guarantee", {})
    if guarantee.get("build_performed") is not False:
        raise UnbindVerificationError("non-build ceiling violated: build_performed is not false")
    if guarantee.get("instance_state") != "extraction_only":
        raise UnbindVerificationError("non-build ceiling violated: instance_state changed")
    brightspace = record.get("brightspace", {})
    if brightspace.get("roundtrip") != "not_performed":
        raise UnbindVerificationError("Brightspace roundtrip must remain not_performed")
    if brightspace.get("network_operations") != 0:
        raise UnbindVerificationError("network_operations must remain zero")
    if brightspace.get("brightspace_operations") != 0:
        raise UnbindVerificationError("brightspace_operations must remain zero")
    privacy = record.get("privacy", {})
    if privacy.get("receipt_records_authored_text") is not False:
        raise UnbindVerificationError("receipt must not record authored question text")
    if privacy.get("sharing_requires_review") is not True:
        raise UnbindVerificationError("local course evidence must retain its share-review gate")

    readiness = record.get("readiness", {})
    blockers = readiness.get("blocker_codes", [])
    warnings = readiness.get("warning_codes", [])
    if readiness.get("error_count") != len(blockers):
        raise UnbindVerificationError("readiness error_count does not match blocker codes")
    if readiness.get("warning_count") != len(warnings):
        raise UnbindVerificationError("readiness warning_count does not match warning codes")
    if readiness.get("ready") is True and blockers:
        raise UnbindVerificationError("ready run cannot carry readiness blockers")
    if readiness.get("ready") is False and not blockers:
        raise UnbindVerificationError("not-ready run must retain its blocker codes")

    verification = record.get("verification", {})
    status = verification.get("status")
    if status not in ({"pending", "verified"} if allow_pending else {"verified"}):
        raise UnbindVerificationError("Unbind record does not carry a verified close")
    if status == "pending" and verification.get("verified_at") is not None:
        raise UnbindVerificationError("pending verification cannot carry verified_at")
    if status == "verified" and not verification.get("verified_at"):
        raise UnbindVerificationError("verified record must carry verified_at")

    events = record.get("events", [])
    expected_sequences = list(range(1, len(events) + 1))
    if [row.get("sequence") for row in events] != expected_sequences:
        raise UnbindVerificationError("progress event sequence is not contiguous")
    completed_verifications = [
        row
        for row in events
        if row.get("stage") == "verified_close" and row.get("status") == "completed"
    ]
    if status == "verified" and len(completed_verifications) != 1:
        raise UnbindVerificationError("verified record must carry one completed verification event")
    if status == "pending" and completed_verifications:
        raise UnbindVerificationError("pending record cannot carry a completed verification event")


def _verify_ref(run_dir: Path, key: str, ref: dict[str, Any]) -> Path:
    if not isinstance(ref, dict):
        raise UnbindVerificationError(f"missing direct artifact reference: {key}")
    try:
        path = safe_relative_path(run_dir, str(ref.get("path", "")), label=key)
    except ValueError as exc:
        raise UnbindVerificationError(str(exc)) from exc
    if not path.is_file() or path.is_symlink():
        raise UnbindVerificationError(f"direct artifact is missing or symlinked: {ref.get('path')}")
    actual_size = path.stat().st_size
    if ref.get("bytes") != actual_size:
        raise UnbindVerificationError(
            f"byte-size mismatch for {key} ({ref.get('path')}): recorded "
            f"{ref.get('bytes')} vs actual {actual_size}"
        )
    actual_sha = sha256_file(path)
    if ref.get("sha256") != actual_sha:
        raise UnbindVerificationError(
            f"hash mismatch for {key} ({ref.get('path')}): recorded "
            f"{str(ref.get('sha256'))[:16]}… vs actual {actual_sha[:16]}…"
        )
    return path


def _verify_direct_artifacts(record: dict[str, Any], run_dir: Path) -> dict[str, Path]:
    refs = record.get("artifacts", {})
    if not isinstance(refs, dict):
        raise UnbindVerificationError("artifacts must be an object")
    try:
        ensure_unique_paths(
            [ref for ref in refs.values() if isinstance(ref, dict)],
            label="direct artifact",
        )
    except ValueError as exc:
        raise UnbindVerificationError(str(exc)) from exc
    return {key: _verify_ref(run_dir, key, refs.get(key, {})) for key in DIRECT_ARTIFACT_KEYS}


def _verify_extraction_artifacts(
    record: dict[str, Any], extraction_receipt_path: Path, model_path: Path
) -> tuple[int, dict[str, Any]]:
    receipt = _read_json(extraction_receipt_path, "extraction receipt")
    if receipt.get("schema") != "coursecraft.quiz_run/1":
        raise UnbindVerificationError("referenced extraction receipt is not coursecraft.quiz_run/1")
    rows = receipt.get("artifacts", [])
    if not isinstance(rows, list) or not rows:
        raise UnbindVerificationError("extraction receipt declares no artifacts")
    if not all(isinstance(row, dict) for row in rows):
        raise UnbindVerificationError("extraction receipt artifact rows must be objects")
    try:
        ensure_unique_paths(rows, label="extraction artifact")
    except ValueError as exc:
        raise UnbindVerificationError(str(exc)) from exc

    extraction_dir = extraction_receipt_path.parent
    symlinks = sorted(
        path.relative_to(extraction_dir).as_posix()
        for path in extraction_dir.rglob("*")
        if path.is_symlink()
    )
    if symlinks:
        raise UnbindVerificationError(f"extraction output contains a symlink: {symlinks[0]}")

    emitted_rows = [row for row in rows if row.get("status") == "emitted"]
    review_or_model = [row for row in rows if row.get("role") in {"review", "model"}]
    if len(emitted_rows) != len(rows) or len(review_or_model) != len(rows):
        raise UnbindVerificationError(
            "canonical extraction artifact inventory must contain emitted review/model rows only"
        )

    verified_paths: set[str] = set()
    model_rows: list[dict[str, Any]] = []
    for row in emitted_rows:
        relative = str(row.get("path", ""))
        try:
            path = safe_relative_path(extraction_dir, relative, label="extraction artifact")
        except ValueError as exc:
            raise UnbindVerificationError(str(exc)) from exc
        if not path.is_file() or path.is_symlink():
            raise UnbindVerificationError(
                f"declared extraction artifact is missing or symlinked: {relative}"
            )
        actual_size = path.stat().st_size
        if row.get("bytes") != actual_size:
            raise UnbindVerificationError(
                f"byte-size mismatch for extraction artifact {relative}: recorded "
                f"{row.get('bytes')} vs actual {actual_size}"
            )
        actual_sha = sha256_file(path)
        if row.get("sha256") != actual_sha:
            raise UnbindVerificationError(
                f"hash mismatch for extraction artifact {relative}: recorded "
                f"{str(row.get('sha256'))[:16]}… vs actual {actual_sha[:16]}…"
            )
        verified_paths.add(relative)
        if row.get("role") == "model":
            model_rows.append(row)

    actual_paths = {
        path.relative_to(extraction_dir).as_posix()
        for path in extraction_dir.rglob("*")
        if path.is_file() and path.resolve() != extraction_receipt_path.resolve()
    }
    if actual_paths != verified_paths:
        missing = sorted(verified_paths - actual_paths)
        extra = sorted(actual_paths - verified_paths)
        detail = f"missing={missing[:1]} extra={extra[:1]}"
        raise UnbindVerificationError(f"extraction artifact inventory is not exact: {detail}")
    if len(model_rows) != 1:
        raise UnbindVerificationError(
            f"extraction receipt must declare exactly one model artifact, found {len(model_rows)}"
        )
    model_row = model_rows[0]
    if model_row.get("sha256") != record["artifacts"]["model"].get("sha256"):
        raise UnbindVerificationError("chain mismatch: extraction receipt model hash differs")
    declared_model = safe_relative_path(
        extraction_dir, str(model_row.get("path", "")), label="model artifact"
    )
    if declared_model.resolve() != model_path.resolve():
        raise UnbindVerificationError("chain mismatch: extraction receipt names another model file")

    inventory = record.get("extraction_inventory", {})
    review_count = sum(row.get("role") == "review" for row in emitted_rows)
    model_count = sum(row.get("role") == "model" for row in emitted_rows)
    copied_asset_count = sum(
        row.get("role") == "review" and "_assets/" in str(row.get("path", ""))
        for row in emitted_rows
    )
    expected = {
        "declared_artifact_count": len(emitted_rows),
        "review_artifact_count": review_count,
        "model_artifact_count": model_count,
        "copied_asset_count": copied_asset_count,
    }
    if inventory != expected:
        raise UnbindVerificationError("extraction inventory counts do not match its receipt")
    if record.get("source_export", {}).get("copied_asset_count") != copied_asset_count:
        raise UnbindVerificationError("source asset count does not match extraction inventory")
    return len(emitted_rows), receipt


def _verify_reading_room_shards(manifest_path: Path) -> int:
    manifest = _read_json(manifest_path, "Reading Room manifest")
    rows = manifest.get("shards")
    if not isinstance(rows, list):
        raise UnbindVerificationError("Reading Room manifest shards must be an array")
    if not all(isinstance(row, dict) for row in rows):
        raise UnbindVerificationError("Reading Room shard rows must be objects")
    if not all(isinstance(row.get("path"), str) and row.get("path") for row in rows):
        raise UnbindVerificationError(
            "Reading Room shard paths must be nonempty strings"
        )
    try:
        ensure_unique_paths(rows, label="Reading Room shard")
    except ValueError as exc:
        raise UnbindVerificationError(str(exc)) from exc

    reading_room_dir = manifest_path.parent
    if reading_room_dir.is_symlink():
        raise UnbindVerificationError("Reading Room directory must not be a symlink")
    declared_paths: set[str] = set()
    for row in rows:
        relative = row["path"]
        try:
            path = safe_relative_path(
                reading_room_dir,
                relative,
                label="Reading Room shard",
            )
        except ValueError as exc:
            raise UnbindVerificationError(str(exc)) from exc
        declared_paths.add(relative)
        cursor = reading_room_dir
        for part in relative.replace("\\", "/").split("/"):
            cursor = cursor / part
            if cursor.is_symlink():
                raise UnbindVerificationError(
                    f"Reading Room shard path is a symlink: {relative}"
                )
        if not path.is_file() or path.is_symlink():
            raise UnbindVerificationError(
                f"Reading Room shard is missing or not a regular file: {relative}"
            )
        actual_size = path.stat().st_size
        recorded_size = row.get("bytes")
        if (
            not isinstance(recorded_size, int)
            or isinstance(recorded_size, bool)
            or recorded_size < 0
            or recorded_size != actual_size
        ):
            raise UnbindVerificationError(
                f"byte-size mismatch for Reading Room shard {relative}: recorded "
                f"{recorded_size} vs actual {actual_size}"
            )
        actual_sha = sha256_file(path)
        recorded_sha = row.get("sha256")
        if (
            not isinstance(recorded_sha, str)
            or len(recorded_sha) != 64
            or any(character not in "0123456789abcdef" for character in recorded_sha)
            or recorded_sha != actual_sha
        ):
            raise UnbindVerificationError(
                f"hash mismatch for Reading Room shard {relative}: recorded "
                f"{str(recorded_sha)[:16]}… vs actual {actual_sha[:16]}…"
            )

    tree_entries = list(reading_room_dir.rglob("*"))
    symlinks = sorted(
        path.relative_to(reading_room_dir).as_posix()
        for path in tree_entries
        if path.is_symlink()
    )
    if symlinks:
        raise UnbindVerificationError(
            f"Reading Room contains a symlink: {symlinks[0]}"
        )
    non_regular = sorted(
        path.relative_to(reading_room_dir).as_posix()
        for path in tree_entries
        if not path.is_dir() and not path.is_file()
    )
    if non_regular:
        raise UnbindVerificationError(
            f"Reading Room contains a non-regular artifact: {non_regular[0]}"
        )
    actual_files = {
        path.relative_to(reading_room_dir).as_posix()
        for path in tree_entries
        if path.is_file()
    }
    expected_files = declared_paths | {"station.json", "index.html"}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise UnbindVerificationError(
            "Reading Room shard inventory is not exact: "
            f"missing={missing[:1]} extra={extra[:1]}"
        )
    return len(rows)


def _cross_check_chain(
    record: dict[str, Any], paths: dict[str, Path], extraction_receipt: dict[str, Any]
) -> list[str]:
    model = _read_json(paths["model"], "quiz model")
    readiness = _read_json(paths["readiness_report"], "readiness report")
    station = _read_json(paths["station_data"], "station data")
    projection = _read_json(paths["review_projection"], "review projection")
    reading_room = _read_json(paths["reading_room_data"], "Reading Room data")
    if model.get("schema") != "coursecraft.quiz/1":
        raise UnbindVerificationError("referenced model is not coursecraft.quiz/1")
    model_sha = record["artifacts"]["model"]["sha256"]
    if readiness.get("source_model_sha256") != model_sha:
        raise UnbindVerificationError("chain mismatch: readiness model hash differs")
    registry_sha = record.get("producer", {}).get("capability_registry", {}).get("sha256")
    if readiness.get("capability_registry_sha256") != registry_sha:
        raise UnbindVerificationError("chain mismatch: readiness registry hash differs")
    if readiness.get("selected_quiz_key") != record.get("selection", {}).get(
        "selected_quiz_entity_key"
    ):
        raise UnbindVerificationError("chain mismatch: selected quiz key differs")
    issues = [row for row in readiness.get("issues", []) if isinstance(row, dict)]
    blockers = [str(row.get("code")) for row in issues if row.get("severity") == "error"]
    warnings = [str(row.get("code")) for row in issues if row.get("severity") == "warning"]
    if blockers != record.get("readiness", {}).get("blocker_codes"):
        raise UnbindVerificationError("chain mismatch: readiness blocker codes differ")
    if warnings != record.get("readiness", {}).get("warning_codes"):
        raise UnbindVerificationError("chain mismatch: readiness warning codes differ")
    if bool(readiness.get("ready")) != record.get("readiness", {}).get("ready"):
        raise UnbindVerificationError("chain mismatch: readiness state differs")
    if station.get("format") != "quiz-binder-local-station-v0":
        raise UnbindVerificationError("station data has an unexpected format")
    if station.get("model_id") != model.get("model_id"):
        raise UnbindVerificationError("chain mismatch: station model identity differs")
    if station.get("source_fingerprint") != model.get("source", {}).get("fingerprint", {}).get(
        "digest"
    ):
        raise UnbindVerificationError("chain mismatch: station source fingerprint differs")
    if station.get("registry_schema") != "coursecraft.quiz_build_capabilities/1":
        raise UnbindVerificationError("station registry identity is unexpected")
    if projection.get("schema") != "coursecraft.quiz_review_projection/0":
        raise UnbindVerificationError("review projection has an unexpected schema")
    if projection.get("source_model_id") != model.get("model_id"):
        raise UnbindVerificationError("chain mismatch: projection model identity differs")
    if projection.get("source_run_id") != model.get("run_id"):
        raise UnbindVerificationError("chain mismatch: projection run identity differs")
    if reading_room.get("format") != "coursecraft.quiz_review_station/0":
        raise UnbindVerificationError("Reading Room data has an unexpected format")
    room_model = reading_room.get("model", {})
    if room_model.get("model_id") != model.get("model_id"):
        raise UnbindVerificationError("chain mismatch: Reading Room model identity differs")
    if room_model.get("run_id") != model.get("run_id"):
        raise UnbindVerificationError("chain mismatch: Reading Room run identity differs")
    if room_model.get("source_fingerprint", {}).get("digest") != model.get(
        "source", {}
    ).get("fingerprint", {}).get("digest"):
        raise UnbindVerificationError(
            "chain mismatch: Reading Room source fingerprint differs"
        )
    projection_summary = projection.get("summary", {})
    room_summary = reading_room.get("summary", {})
    if room_summary.get("unique_question_count") != projection_summary.get(
        "unique_question_count"
    ) or room_summary.get("occurrence_count") != projection_summary.get(
        "question_occurrence_count"
    ):
        raise UnbindVerificationError(
            "chain mismatch: Reading Room and projection counts differ"
        )

    model_summary = record.get("model_summary", {})
    actual_summary = {
        "quiz_count": len(model.get("quizzes", [])),
        "question_entity_count": int(
            projection_summary.get("unique_question_count", 0)
        ),
        "question_occurrence_count": int(
            projection_summary.get("question_occurrence_count", 0)
        ),
        "library_only_question_entity_count": int(
            projection_summary.get("library_question_count", 0)
        ),
        "diagnostic_count": len(model.get("diagnostics", [])),
        "projection_diagnostic_count": len(projection.get("diagnostics", [])),
    }
    if model_summary != actual_summary:
        raise UnbindVerificationError("model summary counts do not match the referenced model")

    # Fidelity is semantic, not tamper-evidence: a run can be fully verified and
    # still unsafe for answer-key approval, so it is recomputed rather than
    # trusted from the record.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from quiz_normalization import variant_collision_report

    report = variant_collision_report(model)
    recorded_fidelity = record.get("fidelity", {})
    expected_fidelity = {
        "state": report["state"],
        "approval_safe": report["approval_safe"],
        "variant_collision_count": report["variant_collision_count"],
        "collided_occurrence_count": report["collided_occurrence_count"],
        "authored_variant_class_count": int(
            projection_summary.get("authored_variant_class_count", 0)
        ),
        "reused_authored_variant_class_count": int(
            projection_summary.get("reused_authored_variant_class_count", 0)
        ),
    }
    if recorded_fidelity != expected_fidelity:
        raise UnbindVerificationError(
            "recorded fidelity does not match the referenced model"
        )
    source = extraction_receipt.get("source", {})
    source_inputs = extraction_receipt.get("inputs", [])
    if len(source_inputs) != 1 or not isinstance(source_inputs[0], dict):
        raise UnbindVerificationError(
            "chain mismatch: extraction receipt must retain one source input"
        )
    source_input = source_inputs[0]
    source_record = record.get("source_export", {})
    source_fields = {
        "reference": source_input.get("path"),
        "sha256": source_input.get("sha256"),
        "bytes": source_input.get("bytes"),
        "source_key": source.get("source_key"),
        "source_lineage_key": source.get("source_lineage_key"),
    }
    if any(source_record.get(key) != value for key, value in source_fields.items()):
        raise UnbindVerificationError("chain mismatch: source identity differs from extraction receipt")
    scope = source.get("extensions", {}).get("quiz_scope")
    if not isinstance(scope, dict) or source_record.get("scope") != scope:
        raise UnbindVerificationError(
            "chain mismatch: export scope differs from canonical extraction receipt"
        )
    try:
        summary_text = paths["summary_markdown"].read_text(encoding="utf-8")
    except OSError as exc:
        raise UnbindVerificationError("cannot read Unbind summary") from exc
    if "local extraction evidence only" not in summary_text:
        raise UnbindVerificationError(
            "Unbind summary does not qualify its verified-close claim"
        )
    return [
        "extraction receipt model hash matches",
        "readiness model and registry hashes match",
        "content-minimized station model and source identities match",
        "review projection and Reading Room identities and counts match",
        "source identity, lineage, and export scope match",
        "recorded fidelity matches the referenced model",
        "summary qualifies the local extraction-only close",
    ]


def verify_unbind_run(run_dir: Path, *, allow_pending: bool = False) -> dict[str, Any]:
    """Verify the full evidence chain and return a content-minimized report."""
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise UnbindVerificationError(f"Unbind run directory not found: {run_dir.name}")
    record = _load_record(run_dir)
    findings = validate_unbind_record(record)
    if findings:
        raise UnbindVerificationError(
            "Unbind record failed schema validation: " + "; ".join(findings[:5])
        )
    _check_internal_consistency(record, allow_pending=allow_pending)
    paths = _verify_direct_artifacts(record, run_dir)
    extraction_count, extraction_receipt = _verify_extraction_artifacts(
        record, paths["extraction_receipt"], paths["model"]
    )
    reading_room_shard_count = _verify_reading_room_shards(
        paths["reading_room_data"]
    )
    chain_checks = _cross_check_chain(record, paths, extraction_receipt)
    chain_checks.append("Reading Room shard manifest recursively re-hashed")
    direct_count = len(paths)
    total_count = direct_count + extraction_count + reading_room_shard_count
    verification = record.get("verification", {})
    if verification.get("status") == "verified":
        expected_counts = {
            "direct_artifact_count": direct_count,
            "extraction_artifact_count": extraction_count,
            "total_reference_count": total_count,
        }
        if any(verification.get(key) != value for key, value in expected_counts.items()):
            raise UnbindVerificationError("verified reference counts do not match the evidence chain")
    return {
        "schema": SCHEMA_ID,
        "contract_status": "draft_unratified",
        "status": "verified",
        "run_id": record["run_id"],
        "direct_artifact_count": direct_count,
        "extraction_artifact_count": extraction_count,
        "reading_room_shard_count": reading_room_shard_count,
        "total_reference_count": total_count,
        "chain_checks": chain_checks,
        "evidence_level": "extraction_only",
        "close_scope": "local_extraction_evidence_only",
        "brightspace_roundtrip": "not_performed",
    }


def render_verification(report: dict[str, Any]) -> str:
    lines = [
        f"VERIFIED CLOSE — {report['schema']} (DRAFT / UNRATIFIED)",
        f"run: {report['run_id']}",
        "artifacts re-hashed:",
        f"  - direct references: {report['direct_artifact_count']}",
        f"  - extraction-receipt artifacts: {report['extraction_artifact_count']}",
        f"  - Reading Room shards: {report['reading_room_shard_count']}",
        f"  - total reference checks: {report['total_reference_count']}",
        "chain cross-checks:",
    ]
    lines.extend(f"  - {line}: OK" for line in report["chain_checks"])
    lines.extend(
        [
            f"evidence_level: {report['evidence_level']}",
            f"brightspace_roundtrip: {report['brightspace_roundtrip']}",
            "close: VERIFIED (local extraction evidence only; no build, seal, or import)",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help=f"Run directory containing {RECORD_NAME}.")
    parser.add_argument("--json", action="store_true", help="Emit the minimized result as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = verify_unbind_run(args.run_dir.expanduser().resolve())
    except UnbindVerificationError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - safe one-line CLI boundary
        print("ERROR: Unbind verification failed unexpectedly", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_verification(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
