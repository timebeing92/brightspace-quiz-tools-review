#!/usr/bin/env python3
"""Run the Workbench Quiz Binder Unbind path on a local Brightspace export.

Unbind accepts a caller-supplied ZIP or unpacked export, invokes the canonical
quiz extractor and readiness analyzer, preserves the content-minimized status
station, renders a separate authored-content Reading Room, writes the
provisional ``coursecraft.quiz_unbind_run/0`` envelope, and immediately verifies
the complete evidence chain.  It performs no network or live Brightspace work
and exposes no build, seal, or import path.

By default resolved review assets are copied so the run is self-contained.
``--asset-mode reference`` is the explicit opt-out for a lighter local run.
Version 0 of the Unbind envelope is draft and unratified.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Callable, Iterable

import quiz_unbind_receipt
from local_evidence_output import (
    RepoUnsafeOutputError,
    guard_repo_safe_output,
)
from verify_quiz_unbind_run import UnbindVerificationError, verify_unbind_run


WORKBENCH_ROOT = Path(__file__).resolve().parents[1]
ENTITY_KEY_PATTERN = re.compile(r"^cc:[A-Za-z0-9._~:/-]+$")
EventSink = Callable[[dict[str, Any]], None]
UNBIND_RECORD_NAME = "quiz_unbind_run.json"
UNBIND_SCHEMA_ID = quiz_unbind_receipt.SCHEMA_ID


class UnbindRefused(Exception):
    """An input or safe-boundary refusal (CLI exit 2)."""


class UnbindFailed(Exception):
    """A canonical producer or verification failure (CLI exit 2)."""


class _Events:
    def __init__(self, sinks: Iterable[EventSink] = ()) -> None:
        self.rows: list[dict[str, Any]] = []
        self.sinks = tuple(sinks)

    def emit(
        self, stage: str, status: str, code: str, *, notify: bool = True
    ) -> dict[str, Any]:
        row = {
            "sequence": len(self.rows) + 1,
            "stage": stage,
            "status": status,
            "code": code,
        }
        self.rows.append(row)
        if notify:
            self.notify(row)
        return row

    def notify(self, row: dict[str, Any]) -> None:
        for sink in self.sinks:
            sink(dict(row))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_control_chars(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _guard_entity_key(value: str, label: str) -> str:
    if not value:
        return ""
    if _has_control_chars(value) or ENTITY_KEY_PATTERN.fullmatch(value) is None:
        raise UnbindRefused(f"{label} must be a valid cc: entity key")
    return value


def _guard_export_path(raw_export: str) -> Path:
    if _has_control_chars(raw_export):
        raise UnbindRefused("export path contains control characters")
    unresolved = Path(raw_export).expanduser()
    if unresolved.is_symlink():
        raise UnbindRefused("export path must not be a symlink")
    export = unresolved.resolve()
    if not export.exists():
        raise UnbindRefused("export path does not exist")
    if export.is_dir():
        for path in export.rglob("*"):
            if path.is_symlink():
                raise UnbindRefused("export folder contains a symlink")
    return export


def _guard_output_path(raw_output: str) -> Path:
    if _has_control_chars(raw_output):
        raise UnbindRefused("output directory path contains control characters")
    unresolved = Path(raw_output).expanduser()
    if unresolved.is_symlink():
        raise UnbindRefused("output directory must not be a symlink")
    output = unresolved.resolve()
    if output.exists():
        if not output.is_dir():
            raise UnbindRefused("output path is a file, not a directory")
        if any(output.iterdir()):
            raise UnbindRefused("output directory must be empty")
    return output


def _guard_repo_safe_output(output: Path, *, allow_tracked: bool) -> None:
    """Refuse to write course evidence into a tracked lane of this repository.

    A completed run holds authored prompts, answer keys, and feedback, and the
    model can exceed the size limits of any hosted remote.  Runs therefore
    belong in an ignored local-evidence lane unless the caller says otherwise.
    """
    try:
        guard_repo_safe_output(
            output,
            repo_root=WORKBENCH_ROOT,
            allow_tracked=allow_tracked,
        )
    except RepoUnsafeOutputError as exc:
        raise UnbindRefused(str(exc)) from exc


def _resolve_registry(raw_registry: str | None) -> Path:
    import quiz_build_support

    canonical = Path(quiz_build_support.CAPABILITY_REGISTRY_PATH).resolve()
    if raw_registry:
        if _has_control_chars(raw_registry):
            raise UnbindRefused("registry path contains control characters")
        requested = Path(raw_registry).expanduser().resolve()
        if requested != canonical:
            raise UnbindRefused(
                "requested registry does not match the pinned Workbench capability registry"
            )
    if not canonical.is_file() or canonical.is_symlink():
        raise UnbindFailed("pinned capability registry is unavailable")
    return canonical


def _run_extractor(
    export: Path,
    extraction_dir: Path,
    *,
    asset_mode: str,
    source_lineage_key: str,
) -> tuple[int, str, str]:
    import extract_quiz_pool_review as extractor

    argv = [
        "extract_quiz_pool_review.py",
        str(export),
        "--output-dir",
        str(extraction_dir),
        "--asset-mode",
        "copy" if asset_mode == "self_contained" else "reference",
        # Unbind has already guarded the operator-selected final run directory.
        # Its extractor writes into an owned sibling staging tree, so do not
        # apply the repository decision a second time to that transient path.
        "--allow-tracked-output",
    ]
    if source_lineage_key:
        argv.extend(["--source-lineage-key", source_lineage_key])
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = sys.argv
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = extractor.main()
    except SystemExit as exc:
        result = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.argv = previous
    return int(result), stdout.getvalue(), stderr.getvalue()


def _run_readiness(
    model_path: Path, readiness_dir: Path, *, quiz_entity_key: str
) -> tuple[int, str, str]:
    import check_quiz_authoring_readiness as readiness_cli

    argv = [str(model_path), "--output-dir", str(readiness_dir)]
    if quiz_entity_key:
        argv.extend(["--quiz-entity-key", quiz_entity_key])
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = readiness_cli.main(argv)
    return int(result), stdout.getvalue(), stderr.getvalue()


def _render_station(model_path: Path, registry_path: Path, station_dir: Path) -> None:
    import quiz_binder_station

    quiz_binder_station.write_station(model_path, registry_path, station_dir)


def _write_review_projection(model_path: Path, projection_path: Path) -> dict[str, Any]:
    from quiz_review_projection import build_quiz_review_projection

    model = json.loads(model_path.read_text(encoding="utf-8"))
    projection = build_quiz_review_projection(model)
    projection_path.write_text(
        json.dumps(projection, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return projection


def _render_reading_room(
    model_path: Path,
    reading_room_dir: Path,
    *,
    artifact_links: dict[str, str],
) -> None:
    import quiz_review_station

    quiz_review_station.write_quiz_review_station(
        model_path,
        reading_room_dir,
        artifact_links=artifact_links,
    )


def _only(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise UnbindFailed(f"expected one {label}; found {len(matches)}")
    return matches[0]


def _classify_extraction_error(stderr: str) -> str:
    lowered = stderr.lower()
    cases = (
        ("unsafe path or symlink", "unsafe_archive_member"),
        ("too many members", "archive_member_limit_exceeded"),
        ("safety limit", "archive_size_limit_exceeded"),
        ("multiple quiz export roots", "ambiguous_export_roots"),
        ("could not find questiondb.xml", "quiz_xml_not_found"),
        ("must be a brightspace export zip", "unsupported_export_input"),
    )
    return next((code for marker, code in cases if marker in lowered), "canonical_extraction_failed")


def _write_record(path: Path, record: dict[str, Any]) -> None:
    findings = quiz_unbind_receipt.validate_unbind_record(record)
    if findings:
        raise UnbindFailed(
            "Unbind record failed self-validation: " + "; ".join(findings[:3])
        )
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def render_summary(record: dict[str, Any], verification: dict[str, Any]) -> str:
    source = record["source_export"]
    readiness = record["readiness"]
    artifacts = record["artifacts"]
    scope = source["scope"]
    return "\n".join(
        [
            "# Quiz Binder Unbind Run",
            "",
            f"- Envelope: `{record['schema']}` (`{record['contract_status']}`)",
            f"- Run: `{record['run_id']}`",
            f"- Evidence level: `{record['evidence_level']}`",
            "- Verified close: `local extraction evidence only`",
            f"- Receipt verification status: `{verification['status']}`",
            f"- Fidelity: `{record['fidelity']['state']}` | "
            f"approval-safe: {'yes' if record['fidelity']['approval_safe'] else 'NO'} | "
            f"variant collisions: {record['fidelity']['variant_collision_count']}",
            "",
            "## Source identity",
            f"- Reference: `{source['reference']}`",
            f"- SHA-256: `{source['sha256'][:16]}…`",
            f"- Intake: `{source['intake_kind']}` | files: {source['file_count']} | bytes: {source['bytes']}",
            f"- Source key: `{source['source_key']}`",
            f"- Lineage key: `{source['source_lineage_key']}` ({source['lineage_basis']})",
            f"- Asset mode: `{source['asset_mode']}` | copied assets: {source['copied_asset_count']}",
            f"- Export scope: `{scope['coverage_kind']}`",
            f"- Question library present: {'yes' if scope['questiondb_present'] else 'no'}",
            f"- Occurrence forms: inline {scope['inline_occurrence_count']} | itemref {scope['itemref_occurrence_count']} (resolved {scope['resolved_itemref_count']}, unresolved {scope['unresolved_itemref_count']})",
            "",
            "## Model counts",
            f"- Quizzes: {record['model_summary']['quiz_count']}",
            f"- Unique question entities: {record['model_summary']['question_entity_count']}",
            f"- Question occurrences: {record['model_summary']['question_occurrence_count']}",
            f"- Library-only question entities: {record['model_summary']['library_only_question_entity_count']}",
            f"- Model diagnostics: {record['model_summary']['diagnostic_count']}",
            f"- Projection diagnostics: {record['model_summary']['projection_diagnostic_count']}",
            "",
            "## Readiness (extraction-only stop is valid)",
            f"- Ready for build: {'yes' if readiness['ready'] else 'no'}",
            f"- Errors: {readiness['error_count']} | warnings: {readiness['warning_count']}",
            f"- Blocker codes: {', '.join(readiness['blocker_codes']) or '(none)'}",
            f"- Warning codes: {', '.join(readiness['warning_codes']) or '(none)'}",
            "",
            "## Review artifacts",
            f"- Model: `{artifacts['model']['path']}`",
            f"- Detailed/reviewer outputs: `{Path(artifacts['extraction_receipt']['path']).parent.as_posix()}/`",
            f"- Readiness: `{artifacts['readiness_report']['path']}`",
            f"- Content-minimized status station: `{artifacts['station_html']['path']}`",
            f"- Authored-content Reading Room: `{artifacts['reading_room_html']['path']}`",
            f"- Entity/occurrence projection: `{artifacts['review_projection']['path']}`",
            "",
            "## Verification scope",
            f"- Direct references re-hashed: {verification['direct_artifact_count']}",
            f"- Extraction artifacts recursively re-hashed: {verification['extraction_artifact_count']}",
            f"- Reading Room shards recursively re-hashed: {verification['reading_room_shard_count']}",
            f"- Total reference checks: {verification['total_reference_count']}",
            "",
            "## Boundary",
            "No quiz package was built, sealed, imported, or sent to Brightspace.",
            "This folder contains local course evidence and requires review before sharing.",
            "The version-zero envelope is draft and unratified.",
            "",
        ]
    )


def _run_unbind_in_place(
    *,
    export_path: str | Path,
    output_dir: str | Path,
    source_lineage_key: str = "",
    quiz_entity_key: str = "",
    asset_mode: str = "self_contained",
    registry_path: str | Path | None = None,
    event_sinks: Iterable[EventSink] = (),
) -> dict[str, Any]:
    """Build and verify an Unbind run inside an already isolated directory.

    The canonical extractor currently exposes a CLI-shaped ``main`` function;
    this orchestrator scopes its ``sys.argv`` mutation and captures producer
    output so authored content and absolute paths do not leak through this API.
    Calls should therefore not execute concurrently in one Python process.
    """
    if asset_mode not in {"self_contained", "reference"}:
        raise UnbindRefused("asset_mode must be self_contained or reference")
    source_lineage_key = _guard_entity_key(source_lineage_key, "source_lineage_key")
    quiz_entity_key = _guard_entity_key(quiz_entity_key, "quiz_entity_key")
    events = _Events(event_sinks)

    events.emit("source_check", "started", "source_check_started")
    export = _guard_export_path(str(export_path))
    output = _guard_output_path(str(output_dir))
    registry = _resolve_registry(str(registry_path) if registry_path else None)
    output.mkdir(parents=True, exist_ok=True)
    extraction_dir = output / "extraction"
    review_dir = output / "review"
    readiness_dir = output / "readiness"
    station_dir = output / "station"
    reading_room_dir = output / "reading_room"
    extraction_dir.mkdir()
    review_dir.mkdir()
    readiness_dir.mkdir()
    events.emit("source_check", "completed", "source_check_completed")

    events.emit("extract_normalize", "started", "canonical_extraction_started")
    result, _stdout, stderr = _run_extractor(
        export,
        extraction_dir,
        asset_mode=asset_mode,
        source_lineage_key=source_lineage_key,
    )
    if result != 0:
        raise UnbindRefused(
            f"canonical extraction refused the export ({_classify_extraction_error(stderr)})"
        )
    model_path = _only(extraction_dir, "*.model.json", "quiz model")
    extraction_receipt_path = _only(
        extraction_dir, "*.run.json", "extraction receipt"
    )
    events.emit("extract_normalize", "completed", "canonical_extraction_completed")

    events.emit("project_review", "started", "review_projection_started")
    projection_path = review_dir / "quiz_review_projection.json"
    _write_review_projection(model_path, projection_path)
    _render_station(model_path, registry, station_dir)
    station_data_path = station_dir / "station.json"
    station_html_path = station_dir / "index.html"
    if not station_data_path.is_file() or not station_html_path.is_file():
        raise UnbindFailed(
            "content-minimized status station renderer did not emit its required artifacts"
        )
    reviewer_workbook_path = _only(
        extraction_dir,
        "*__quiz_pool_review_reviewer.xlsx",
        "reviewer workbook",
    )
    detailed_workbook_path = _only(
        extraction_dir,
        "*__quiz_pool_review.xlsx",
        "detailed review workbook",
    )
    from quiz_authoring_readiness import safe_label

    readiness_stem = f"{safe_label(model_path.stem)}__quiz_authoring_readiness"
    _render_reading_room(
        model_path,
        reading_room_dir,
        artifact_links={
            "reviewer_workbook": f"../extraction/{reviewer_workbook_path.name}",
            "detailed_workbook": f"../extraction/{detailed_workbook_path.name}",
            "model": f"../extraction/{model_path.name}",
            "extraction_receipt": f"../extraction/{extraction_receipt_path.name}",
            "review_projection": "../review/quiz_review_projection.json",
            "readiness_report": f"../readiness/{readiness_stem}.json",
            "status_station": "../station/index.html",
            "unbind_receipt": "../quiz_unbind_run.json",
            "unbind_summary": "../UNBIND_SUMMARY.md",
        },
    )
    reading_room_data_path = reading_room_dir / "station.json"
    reading_room_html_path = reading_room_dir / "index.html"
    if (
        not projection_path.is_file()
        or not reading_room_data_path.is_file()
        or not reading_room_html_path.is_file()
    ):
        raise UnbindFailed(
            "review projection or Reading Room renderer did not emit its required artifacts"
        )
    events.emit("project_review", "completed", "review_projection_completed")

    events.emit("readiness", "started", "readiness_analysis_started")
    result, _stdout, _stderr = _run_readiness(
        model_path, readiness_dir, quiz_entity_key=quiz_entity_key
    )
    if result != 0:
        raise UnbindFailed("canonical readiness analysis failed")
    readiness_path = _only(
        readiness_dir, "*__quiz_authoring_readiness.json", "readiness report"
    )
    readiness_markdown_path = _only(
        readiness_dir, "*__quiz_authoring_readiness.md", "readiness Markdown"
    )
    events.emit("readiness", "completed", "readiness_analysis_completed")

    events.emit("verified_close", "started", "verified_close_started")
    record_path = output / UNBIND_RECORD_NAME
    summary_path = output / "UNBIND_SUMMARY.md"
    summary_path.write_text(
        "# Quiz Binder Unbind Run\n\n"
        "Verified-close preparation is in progress. The run remains local "
        "extraction evidence only; no build, seal, import, or Brightspace "
        "operation was performed.\n",
        encoding="utf-8",
    )
    record = quiz_unbind_receipt.build_unbind_record(
        run_dir=output,
        model_path=model_path,
        extraction_receipt_path=extraction_receipt_path,
        readiness_path=readiness_path,
        readiness_markdown_path=readiness_markdown_path,
        station_data_path=station_data_path,
        station_html_path=station_html_path,
        review_projection_path=projection_path,
        reading_room_data_path=reading_room_data_path,
        reading_room_html_path=reading_room_html_path,
        summary_markdown_path=summary_path,
        registry_path=registry,
        asset_mode=asset_mode,
        requested_quiz_entity_key=quiz_entity_key or None,
        generated_at=_utc_now(),
        events=events.rows,
    )
    _write_record(record_path, record)

    try:
        preliminary = verify_unbind_run(output, allow_pending=True)
    except UnbindVerificationError as exc:
        raise UnbindFailed(f"Unbind evidence verification failed: {exc}") from exc
    completed_event = events.emit(
        "verified_close",
        "completed",
        "verified_close_completed",
        notify=False,
    )
    record["events"] = list(events.rows)
    record["verification"] = {
        "mode": "recursive_extraction_receipt",
        "status": "verified",
        "verified_at": _utc_now(),
        "direct_artifact_count": preliminary["direct_artifact_count"],
        "extraction_artifact_count": preliminary["extraction_artifact_count"],
        "total_reference_count": preliminary["total_reference_count"],
    }
    summary_path.write_text(
        render_summary(record, {**preliminary, "status": "verified"}),
        encoding="utf-8",
    )
    record["artifacts"]["summary_markdown"] = quiz_unbind_receipt.artifact_ref(
        summary_path,
        output,
        "text/markdown",
    )
    _write_record(record_path, record)
    try:
        verification = verify_unbind_run(output)
    except UnbindVerificationError as exc:
        raise UnbindFailed(f"final Unbind verified close failed: {exc}") from exc
    return {
        "record": record,
        "verification": verification,
        "events": list(events.rows),
        "record_path": record_path,
        "summary_path": summary_path,
    }


def _promote_staging(staging: Path, output: Path) -> None:
    """Atomically rename one verified sibling staging directory into place."""
    if staging.parent != output.parent:
        raise UnbindFailed("Unbind staging directory is not an output sibling")
    removed_empty_output = False
    try:
        if output.exists():
            if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
                raise UnbindRefused("output directory became occupied during Unbind")
            output.rmdir()
            removed_empty_output = True
        staging.rename(output)
    except KeyboardInterrupt:
        if not staging.exists() and output.is_dir() and not output.is_symlink():
            return
        if removed_empty_output and not output.exists():
            output.mkdir()
        raise
    except OSError as exc:
        became_occupied = output.exists()
        if removed_empty_output and not output.exists():
            output.mkdir()
        if became_occupied:
            raise UnbindRefused(
                "output directory became occupied during Unbind"
            ) from exc
        raise


def run_unbind(
    *,
    export_path: str | Path,
    output_dir: str | Path,
    source_lineage_key: str = "",
    quiz_entity_key: str = "",
    asset_mode: str = "self_contained",
    registry_path: str | Path | None = None,
    event_sinks: Iterable[EventSink] = (),
    allow_tracked_output: bool = False,
) -> dict[str, Any]:
    """Build and verify in owned staging, then atomically publish the run."""
    sinks = tuple(event_sinks)
    output = _guard_output_path(str(output_dir))
    _guard_repo_safe_output(output, allow_tracked=allow_tracked_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{output.name}.unbind-staging-"
    staging = Path(tempfile.mkdtemp(prefix=prefix, dir=output.parent))
    promoted = False
    try:
        result = _run_unbind_in_place(
            export_path=export_path,
            output_dir=staging,
            source_lineage_key=source_lineage_key,
            quiz_entity_key=quiz_entity_key,
            asset_mode=asset_mode,
            registry_path=registry_path,
            event_sinks=sinks,
        )
        result["record_path"] = output / UNBIND_RECORD_NAME
        result["summary_path"] = output / "UNBIND_SUMMARY.md"
        try:
            _promote_staging(staging, output)
        except KeyboardInterrupt:
            if not staging.exists() and output.is_dir() and not output.is_symlink():
                promoted = True
            else:
                raise
        else:
            promoted = True
        completed_event = result["events"][-1]
        for sink in sinks:
            sink(dict(completed_event))
        return result
    finally:
        if not promoted and staging.exists():
            shutil.rmtree(staging)


def _print_result(result: dict[str, Any]) -> None:
    record = result["record"]
    verification = result["verification"]
    source = record["source_export"]
    readiness = record["readiness"]
    print(
        "unbind: verified close -- local extraction evidence only  "
        f"contract: {record['contract_status']}"
    )
    print(
        f"source: {source['reference']}  intake={source['intake_kind']}  "
        f"sha256={source['sha256'][:16]}…"
    )
    fidelity = record["fidelity"]
    print(
        f"fidelity={fidelity['state']}  "
        f"approval_safe={'yes' if fidelity['approval_safe'] else 'NO'}  "
        f"variant_collisions={fidelity['variant_collision_count']}"
    )
    print(
        f"quizzes={record['model_summary']['quiz_count']}  "
        f"questions={record['model_summary']['question_entity_count']}  "
        f"occurrences={record['model_summary']['question_occurrence_count']}  "
        f"asset_mode={source['asset_mode']}"
    )
    print(
        f"scope={source['scope']['coverage_kind']}  "
        f"inline={source['scope']['inline_occurrence_count']}  "
        f"itemref={source['scope']['itemref_occurrence_count']}"
    )
    print(
        f"ready={'yes' if readiness['ready'] else 'no'}  "
        f"errors={readiness['error_count']}  warnings={readiness['warning_count']}"
    )
    print(f"blocker_codes: {', '.join(readiness['blocker_codes']) or '(none)'}")
    print(
        f"verified references: {verification['total_reference_count']}  "
        f"reading_room_shards={verification['reading_room_shard_count']}  "
        "brightspace_roundtrip=not_performed"
    )
    print("receipt: quiz_unbind_run.json  summary: UNBIND_SUMMARY.md")
    print(f"status station: {record['artifacts']['station_html']['path']}")
    print(f"Reading Room: {record['artifacts']['reading_room_html']['path']}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="Local Brightspace export ZIP or folder.")
    parser.add_argument(
        "--export",
        dest="legacy_export",
        help="Compatibility alias for the source positional argument.",
    )
    parser.add_argument("--output-dir", required=True, help="A new or empty run directory.")
    parser.add_argument(
        "--source-lineage-key",
        default="",
        help="Optional stable cc:lineage key for refresh-stable identities.",
    )
    parser.add_argument(
        "--quiz-entity-key",
        default="",
        help="Optional quiz key when the extracted model contains multiple quizzes.",
    )
    parser.add_argument(
        "--asset-mode",
        choices=("self-contained", "reference"),
        default="self-contained",
        help="Copy resolved assets by default; reference is the explicit no-copy mode.",
    )
    parser.add_argument(
        "--registry",
        help="Optional assertion of the pinned Workbench capability registry path.",
    )
    parser.add_argument(
        "--allow-tracked-output",
        action="store_true",
        help="Permit a run directory inside the workbench that git does not ignore.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Stream content-minimized progress events as JSON Lines.",
    )
    parser.add_argument(
        "--json-result",
        action="store_true",
        help="Print the final content-minimized verification result as JSON.",
    )
    args = parser.parse_args(argv)
    if args.source and args.legacy_export:
        parser.error("provide either source or --export, not both")
    if not args.source and not args.legacy_export:
        parser.error("a source export is required")
    args.source = args.source or args.legacy_export
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    sink: EventSink | None = None
    if args.json_events:
        sink = lambda event: print(json.dumps(event, sort_keys=True), flush=True)
    try:
        result = run_unbind(
            export_path=args.source,
            output_dir=args.output_dir,
            source_lineage_key=args.source_lineage_key,
            quiz_entity_key=args.quiz_entity_key,
            asset_mode=args.asset_mode.replace("-", "_"),
            registry_path=args.registry,
            event_sinks=(sink,) if sink is not None else (),
            allow_tracked_output=args.allow_tracked_output,
        )
    except KeyboardInterrupt:
        print(
            "CANCELLED: Unbind cancelled; no output was promoted",
            file=sys.stderr,
        )
        return 130
    except UnbindRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except UnbindFailed as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("ERROR: Unbind failed at a local processing boundary", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - safe one-line CLI boundary
        print("ERROR: Unbind failed unexpectedly", file=sys.stderr)
        return 2
    if args.json_result:
        print(json.dumps(result["verification"], indent=2, sort_keys=True))
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
