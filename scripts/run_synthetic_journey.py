#!/usr/bin/env python3
"""Run the two-door Quiz Binder journey using synthetic fixtures only.

Refactored for the r2 terminal tranche: the orchestration is an importable
engine driven by the canonical step plan. The observable default contract
of the previous runner is unchanged:

- exactly one JSON line on stdout on full success, identical key set;
- ``journey.receipt.json`` and ``JOURNEY_SUMMARY.md`` written only after
  the entire journey succeeds, with the identical structure;
- a non-empty output directory is refused (exit 2) and preserved;
- errors print one ``ERROR: ...`` line to stderr and exit 2.

What this refactoring adds, all opt-in or strictly-better:

- the orchestration is an importable engine (``run_journey``) driven by
  the canonical step plan in ``quiz_binder_journey_plan.py``, so the
  executed order, the receipt's ``commands[]``, and any narrator all read
  the same source;
- ``--progress-events`` emits the draft quiz-progress NDJSON stream to
  stdout (the final one-line JSON summary remains the last stdout line and
  carries no ``event`` key, so grammar-following consumers pass it through);
- ``--events-file PATH`` creates the same stream as a sidecar file, opened
  only after the output directory is accepted;
- Ctrl-C now closes cleanly: no traceback, a single stderr line naming the
  interrupted step, exit 130, and a truthful ``run_end`` event with
  ``outcome: interrupted``. Completed steps' artifacts stay on disk and the
  interrupted step's residue is untrusted. If interruption lands after the
  receipt is written, consumers defer to the completed-run renderer.

Behavior change relative to the pre-r2 runner, accepted with this file:
Ctrl-C previously died with an uncaught traceback; it now closes cleanly
with the same ordinary on-disk consequence (partial tree kept, receipt
absent). If the interrupt lands after receipt creation, the renderer decides
whether the completed record is trustworthy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, IO

from openpyxl import load_workbook

from quiz_binder_journey_plan import (
    PlanError,
    RECEIPT_FORMAT,
    RECEIPT_NAME,
    STEP_PLAN,
    SUMMARY_NAME,
    find_repo_root,
    step_by_key,
)
from quiz_binder_progress import EventEmissionError, EventSink, ProgressEmitter


REPO_ROOT = find_repo_root(Path(__file__).resolve().parent)
REGISTRY = REPO_ROOT / "workspace/reference/schemas/quiz/quiz_build_capabilities.json"
UNBIND_FIXTURE = REPO_ROOT / "tests/fixtures/quiz_xml/mixed_inline_itemref_and_root_bank"
AUTHORING_ROOT = REPO_ROOT / "tests/fixtures/quiz_authoring"
AUTHORING_MODEL = AUTHORING_ROOT / "buildable_quiz.model.json"
AUTHORING_SETTINGS = AUTHORING_ROOT / "buildable_quiz.settings.json"
PIN_PATH = REPO_ROOT / "upstream/workbench_pin.json"
FORMAT = RECEIPT_FORMAT


class JourneyError(RuntimeError):
    pass


class JourneyRefused(JourneyError):
    """The run was refused before any step started (directory not empty)."""


class JourneyInterrupted(RuntimeError):
    """Ctrl-C arrived; carries the step that was open at the time and
    whether the receipt had already been written (an interrupt can land
    after the receipt but before the summary/close)."""

    def __init__(
        self,
        step_key: str | None,
        completed: list[str],
        *,
        receipt_written: bool = False,
    ) -> None:
        super().__init__(step_key or "between steps")
        self.step_key = step_key
        self.completed = completed
        self.receipt_written = receipt_written


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def require_output_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise JourneyRefused(f"output directory must be empty: {path}")


def prepare_output(path: Path) -> None:
    require_output_empty(path)
    path.mkdir(parents=True, exist_ok=True)


def add_accepted_code(workbook_path: Path) -> None:
    workbook = load_workbook(workbook_path)
    if "_Assessment Review" in workbook.sheetnames:
        layout = json.loads((workbook_path.parent / "assessment-review.json").read_text())
        tab = next(t for t in layout["sheets"] if any(
            workbook[t["title"]].cell(r["row"], next(c.column for c in workbook[t["title"]][2]
                if c.value == "question_type")).value == "Multiple Choice" for r in t["rows"]))
        sheet = workbook[tab["title"]]
        header_row = 2
        question_rows = [r["row"] for r in tab["rows"]]
    else:
        sheet = workbook["Quiz Questions"]
        header_row = 1
        question_rows = range(2, sheet.max_row + 1)
    headers = {
        cell.value: index
        for index, cell in enumerate(sheet[header_row], start=1)
        if isinstance(cell.value, str)
    }
    required = {
        "question_type",
        "proposed_permanent_code",
        "revision_reason",
        "proposed_by",
        "proposed_at",
        "approval_status",
        "approved_by",
        "approved_at",
    }
    missing = sorted(required - set(headers))
    if missing:
        raise JourneyError("review workbook lacks marginalia columns: " + ", ".join(missing))

    target_row = None
    for row in question_rows:
        if sheet.cell(row, headers["question_type"]).value == "Multiple Choice":
            target_row = row
            break
    if target_row is None:
        raise JourneyError("synthetic unbind fixture has no Multiple Choice row")

    values = {
        "proposed_permanent_code": "QB-SYN-Q001",
        "revision_reason": "Assign a stable synthetic review code.",
        "proposed_by": "Synthetic Reviewer",
        "proposed_at": "2026-07-20T14:00:00Z",
        "approval_status": "accepted",
        "approved_by": "Synthetic Approver",
        "approved_at": "2026-07-20T15:00:00Z",
    }
    for name, value in values.items():
        sheet.cell(target_row, headers[name]).value = value
    workbook.save(workbook_path)


def only(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise JourneyError(
            f"expected one {pattern!r} artifact in {directory}, found {len(matches)}"
        )
    return matches[0]


def write_summary(path: Path, receipt: dict[str, Any]) -> None:
    unbind = receipt["doors"]["unbind"]
    compose = receipt["doors"]["compose"]
    seal = receipt["seal"]
    text = f"""# Synthetic Quiz Binder journey

This proof uses synthetic fixtures only and performs no network or Brightspace
operation.

## Unbind and marginalia

- Questions extracted: {unbind['question_count']}
- Accepted marginalia changes: {unbind['accepted_change_count']}
- Applied promotion changes: {unbind['applied_change_count']}
- Readiness: **{unbind['readiness']}**
- Readiness blocker codes: {', '.join(unbind['blocker_codes'])}

The extracted route stops honestly at review because extracted instances remain
evidence, not automatically build-approved authoring records.

## Compose and rebind

- Authoring readiness: **{compose['readiness']}**
- Questions assembled: {compose['question_count']}
- Import package: `{compose['package_zip']}`

## Seal

- Structural/package validation errors: {seal['validation_error_count']}
- Folder-to-ZIP equivalence breaks: {seal['local_equivalence_break_count']}
- Brightspace round trip: **{seal['brightspace_roundtrip']}**

This is local release-candidate proof. It does not create or strengthen a live
tenant capability claim.
"""
    path.write_text(text, encoding="utf-8")


ArgvTransform = Callable[[str, list[str]], list[str]]


class JourneyEngine:
    """Executes the canonical step plan against the real producer scripts.

    ``argv_transform`` is a test seam: it may rewrite one step's argument
    list so failure paths can be exercised deterministically against real
    subprocess outcomes. Production callers leave it None.
    """

    def __init__(
        self,
        output: Path,
        *,
        emitter: ProgressEmitter | None = None,
        argv_transform: ArgvTransform | None = None,
    ) -> None:
        self.output = output
        self.emitter = emitter
        self.argv_transform = argv_transform
        self.command_records: list[dict[str, Any]] = []
        self.completed_steps: list[str] = []
        self.failed_step: str | None = None
        self.receipt_written = False
        self._open_step: str | None = None

    # -- emission helpers (no-ops without an emitter) ---------------------

    def _emit_step_start(self, key: str) -> None:
        if self.emitter:
            self.emitter.step_start(key)

    def _emit_step_end(
        self,
        key: str,
        *,
        status: str,
        exit_code: int,
        artifacts: tuple[Path, ...] = (),
        facts: dict[str, Any] | None = None,
    ) -> None:
        if self.emitter:
            self.emitter.step_end(
                key,
                status=status,
                exit_code=exit_code,
                artifacts=artifacts,
                facts=facts,
            )

    def _emit_note(
        self,
        note: str,
        artifacts: tuple[Path, ...],
        facts: dict[str, Any],
    ) -> None:
        if self.emitter:
            self.emitter.journey_note(note, artifacts=artifacts, facts=facts)

    # -- step execution ---------------------------------------------------

    def run_step(
        self,
        key: str,
        *arguments: str,
        artifacts: Callable[[], tuple[Path, ...]] | None = None,
        facts: Callable[[], dict[str, Any]] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        spec = step_by_key(key)
        entrypoint = spec["entrypoint"]
        argv = [sys.executable, str(REPO_ROOT / entrypoint), *arguments]
        if self.argv_transform is not None:
            argv = self.argv_transform(key, argv)
        self._open_step = key
        self._emit_step_start(key)
        try:
            result = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except KeyboardInterrupt:
            raise JourneyInterrupted(key, list(self.completed_steps)) from None
        self.command_records.append(
            {
                "step": key,
                "entrypoint": entrypoint,
                "exit_code": result.returncode,
            }
        )
        if result.returncode:
            self.failed_step = key
            self._emit_step_end(
                key, status="error", exit_code=result.returncode
            )
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            raise JourneyError(f"{key} failed: {detail}")
        self._emit_step_end(
            key,
            status="ok",
            exit_code=0,
            artifacts=artifacts() if artifacts else (),
            facts=facts() if facts else None,
        )
        self.completed_steps.append(key)
        self._open_step = None
        return result

    # -- the journey ------------------------------------------------------

    def run(self) -> tuple[dict[str, Any], Path, Path]:
        """Execute the full journey; returns (receipt, receipt_path, summary_path)."""
        output = self.output
        try:
            unbind = output / "unbind"
            self.run_step(
                "unbind",
                str(UNBIND_FIXTURE),
                "--output-dir",
                str(unbind),
                "--source-lineage-key",
                "cc:lineage:synthetic:quiz-binder-rc1",
                artifacts=lambda: (only(unbind, "*__quiz_pool_review.model.json"),),
                facts=lambda: {
                    "question_count": len(
                        json.loads(
                            only(unbind, "*__quiz_pool_review.model.json").read_text(
                                encoding="utf-8"
                            )
                        ).get("questions", [])
                    )
                },
            )
            source_model = only(unbind, "*__quiz_pool_review.model.json")
            extracted_reviewer = only(unbind, "*__quiz_pool_review_reviewer.xlsx")
            from quiz_assessment_review import BASELINE, WORKING, prepare_review_packet
            review_packet = unbind / "assessment-review"
            prepare_review_packet(extracted_reviewer, source_model, review_packet)
            baseline = review_packet / BASELINE
            edited = review_packet / WORKING
            add_accepted_code(edited)
            self._emit_note(
                "synthetic_marginalia_simulation",
                (baseline, edited),
                {"simulated": True},
            )

            overlay = unbind / "decision-overlay.json"
            self.run_step(
                "materialize_marginalia",
                "--model",
                str(source_model),
                "--baseline-workbook",
                str(baseline),
                "--edited-workbook",
                str(edited),
                "--output",
                str(overlay),
                artifacts=lambda: (overlay,),
                facts=lambda: {
                    "accepted_change_count": json.loads(
                        overlay.read_text(encoding="utf-8")
                    )["content_minimized_summary"]["accepted_change_count"]
                },
            )
            promoted_model = unbind / "promoted.model.json"
            promotion_receipt = unbind / "promotion.receipt.json"
            self.run_step(
                "promote_accepted_marginalia",
                "--model",
                str(source_model),
                "--overlay",
                str(overlay),
                "--registry",
                str(REGISTRY),
                "--output-model",
                str(promoted_model),
                "--output-receipt",
                str(promotion_receipt),
                artifacts=lambda: (promoted_model, promotion_receipt),
                facts=lambda: {
                    "applied_change_count": json.loads(
                        promotion_receipt.read_text(encoding="utf-8")
                    )["summary"]["applied_change_count"]
                },
            )
            station = unbind / "station"
            self.run_step(
                "render_review_station",
                "--model",
                str(source_model),
                "--registry",
                str(REGISTRY),
                "--overlay",
                str(overlay),
                "--promotion-receipt",
                str(promotion_receipt),
                "--output-dir",
                str(station),
            )
            unbind_readiness_dir = unbind / "readiness"
            self.run_step(
                "check_unbound_readiness",
                str(promoted_model),
                "--output-dir",
                str(unbind_readiness_dir),
                artifacts=lambda: (only(unbind_readiness_dir, "*.json"),),
                facts=lambda: _boundary_facts(
                    only(unbind_readiness_dir, "*.json"), output
                ),
            )
            unbind_readiness_path = only(unbind_readiness_dir, "*.json")
            unbind_readiness = json.loads(
                unbind_readiness_path.read_text(encoding="utf-8")
            )
            if unbind_readiness.get("ready") is not False:
                raise JourneyError(
                    "synthetic unbind lane unexpectedly bypassed build approval"
                )

            compose = output / "compose"
            compose_readiness_dir = compose / "readiness"
            self.run_step(
                "check_compose_readiness",
                str(AUTHORING_MODEL),
                "--settings",
                str(AUTHORING_SETTINGS),
                "--asset-root",
                str(AUTHORING_ROOT),
                "--output-dir",
                str(compose_readiness_dir),
                "--fail-if-not-ready",
                artifacts=lambda: (only(compose_readiness_dir, "*.json"),),
                facts=lambda: _compose_readiness_facts(
                    only(compose_readiness_dir, "*.json"), output
                ),
            )
            compose_readiness_path = only(compose_readiness_dir, "*.json")
            compose_readiness = json.loads(
                compose_readiness_path.read_text(encoding="utf-8")
            )

            package_dir = compose / "package"
            package_zip = compose / "quiz-binder-synthetic.zip"
            receipts_dir = compose / "receipts"
            self.run_step(
                "rebind",
                str(AUTHORING_MODEL),
                "--settings",
                str(AUTHORING_SETTINGS),
                "--asset-root",
                str(AUTHORING_ROOT),
                "--output-dir",
                str(package_dir),
                "--zip-output",
                str(package_zip),
                "--receipt-dir",
                str(receipts_dir),
                artifacts=lambda: (package_zip, receipts_dir / "quiz_build.run.json"),
                facts=lambda: {
                    "package_zip": relative(package_zip, output),
                    "settings_receipt": relative(
                        receipts_dir / "quiz_build.settings.json", output
                    ),
                },
            )
            run_receipt = receipts_dir / "quiz_build.run.json"
            validation_note = compose / "package-validation.md"
            self.run_step(
                "seal_validate",
                str(package_dir),
                "--model",
                str(AUTHORING_MODEL),
                "--settings",
                str(AUTHORING_SETTINGS),
                "--asset-root",
                str(AUTHORING_ROOT),
                "--run-receipt",
                str(run_receipt),
                "--strict-closure",
                "--zip",
                str(package_zip),
                "--output",
                str(validation_note),
                artifacts=lambda: (validation_note,),
                facts=lambda: {"validation_note": relative(validation_note, output)},
            )
            diff_dir = compose / "local-equivalence"
            self.run_step(
                "seal_local_equivalence",
                str(package_dir),
                str(package_zip),
                "--label-a",
                "generated-folder",
                "--label-b",
                "generated-zip",
                "--output-dir",
                str(diff_dir),
                "--fail-on-break",
                artifacts=lambda: (only(diff_dir, "*.json"),),
                facts=lambda: {
                    "local_equivalence_break_count": len(
                        json.loads(
                            only(diff_dir, "*.json").read_text(encoding="utf-8")
                        ).get("breaks", [])
                    ),
                    "report": relative(
                        only(diff_dir, "*.json"), output
                    )[: -len(".json")]
                    + ".md",
                },
            )
            diff_json_path = only(diff_dir, "*.json")
            diff_payload = json.loads(diff_json_path.read_text(encoding="utf-8"))

            source_payload = json.loads(source_model.read_text(encoding="utf-8"))
            overlay_payload = json.loads(overlay.read_text(encoding="utf-8"))
            promotion_payload = json.loads(
                promotion_receipt.read_text(encoding="utf-8")
            )
            authoring_payload = json.loads(AUTHORING_MODEL.read_text(encoding="utf-8"))
            pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
            blocker_codes = sorted(
                {
                    issue["code"]
                    for issue in unbind_readiness.get("issues", [])
                    if issue.get("severity") == "error"
                }
            )
            receipt = {
                "format": FORMAT,
                "fixture_policy": "synthetic_only",
                "network_operations": 0,
                "brightspace_operations": 0,
                "workbench_source_commit": pin["source_commit"],
                "doors": {
                    "unbind": {
                        "status": "reviewed_with_build_blockers",
                        "question_count": len(source_payload.get("questions", [])),
                        "accepted_change_count": overlay_payload[
                            "content_minimized_summary"
                        ]["accepted_change_count"],
                        "applied_change_count": promotion_payload["summary"][
                            "applied_change_count"
                        ],
                        "readiness": "not_ready",
                        "blocker_codes": blocker_codes,
                    },
                    "compose": {
                        "status": "rebuilt_and_locally_sealed",
                        "readiness": "ready",
                        "question_count": len(authoring_payload.get("questions", [])),
                        "package_zip": relative(package_zip, output),
                    },
                },
                "seal": {
                    "validation_error_count": 0,
                    "local_equivalence_break_count": len(
                        diff_payload.get("breaks", [])
                    ),
                    "brightspace_roundtrip": "not_performed",
                    "capability_evidence_changed": False,
                },
                "commands": self.command_records,
            }
            artifact_paths = [
                source_model,
                baseline,
                edited,
                overlay,
                promoted_model,
                promotion_receipt,
                unbind_readiness_path,
                compose_readiness_path,
                package_zip,
                run_receipt,
                validation_note,
                diff_json_path,
            ]
            receipt["artifacts"] = [
                {"path": relative(path, output), "sha256": sha256_file(path)}
                for path in artifact_paths
            ]
            receipt_path = output / RECEIPT_NAME
            receipt_path.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.receipt_written = True
            summary_path = output / SUMMARY_NAME
            write_summary(summary_path, receipt)
            if self.emitter:
                self.emitter.run_end(
                    status="ok",
                    outcome="completed",
                    facts={
                        "outputs": {
                            "receipt": RECEIPT_NAME,
                            "summary": SUMMARY_NAME,
                        },
                        "delivery": {
                            "receipt_written": True,
                            "unbind_readiness": "not_ready",
                            "compose_readiness": "ready",
                            "validation_error_count": 0,
                            "local_equivalence_break_count": len(
                                diff_payload.get("breaks", [])
                            ),
                            "brightspace_roundtrip": "not_performed",
                        },
                    },
                )
            return receipt, receipt_path, summary_path
        except KeyboardInterrupt:
            raise JourneyInterrupted(
                self._open_step,
                list(self.completed_steps),
                receipt_written=self.receipt_written,
            ) from None


def _boundary_facts(readiness_path: Path, run_root: Path) -> dict[str, Any]:
    payload = json.loads(readiness_path.read_text(encoding="utf-8"))
    codes = sorted(
        {
            issue["code"]
            for issue in payload.get("issues", [])
            if issue.get("severity") == "error"
        }
    )
    record = relative(readiness_path, run_root)
    ready = payload.get("ready")
    return {
        "boundary": True,
        "readiness": "not_ready" if ready is False else ("ready" if ready is True else "unknown"),
        "blocker_codes": codes,
        "record": record,
        "report": record[: -len(".json")] + ".md",
    }


def _compose_readiness_facts(readiness_path: Path, run_root: Path) -> dict[str, Any]:
    payload = json.loads(readiness_path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    record = relative(readiness_path, run_root)
    ready = payload.get("ready")
    return {
        "readiness": "ready" if ready is True else ("not_ready" if ready is False else "unknown"),
        "selected_question_count": summary.get("selected_question_count"),
        "reachable_draw_count": summary.get("reachable_draw_count"),
        "selected_asset_count": summary.get("selected_asset_count"),
        "record": record,
        "report": record[: -len(".json")] + ".md",
    }


def run_journey(
    output: Path,
    *,
    event_sinks: tuple[EventSink, ...] = (),
    event_streams: tuple[IO[str], ...] = (),
    events_file: Path | None = None,
    argv_transform: ArgvTransform | None = None,
) -> tuple[dict[str, Any], ProgressEmitter | None]:
    """Engine entry point. Raises JourneyRefused / JourneyError /
    JourneyInterrupted; emits truthful events throughout when any sink,
    stream, or sidecar is configured."""
    output = output.expanduser().resolve()
    wants_events = bool(event_sinks or event_streams or events_file)
    emitter: ProgressEmitter | None = None
    sidecar: IO[str] | None = None
    try:
        try:
            require_output_empty(output)
        except JourneyRefused:
            if wants_events:
                # The sidecar may not be written: the refused directory is
                # preserved untouched. Streams and sinks still hear the truth.
                refusal = ProgressEmitter(output, sinks=event_sinks, streams=event_streams)
                refusal.run_start()
                refusal.run_end(
                    status="error",
                    outcome="refused",
                    facts={
                        "reason": "output_directory_not_empty",
                        "delivery": {"receipt_written": False},
                    },
                )
            raise
        if wants_events and events_file is not None and events_file.exists():
            # Check an external sidecar only after the run folder has received
            # its established occupied-folder classification, but before
            # creating a new run directory or clobbering any event stream.
            raise JourneyError(f"events file already exists: {events_file}")
        output.mkdir(parents=True, exist_ok=True)
        streams = tuple(event_streams)
        if wants_events and events_file is not None:
            # Exclusive create: refuse to clobber or interleave with any
            # existing file rather than silently mixing two runs' streams.
            sidecar = events_file.open("x", encoding="utf-8")
            streams = streams + (sidecar,)
        emitter = (
            ProgressEmitter(output, sinks=event_sinks, streams=streams)
            if wants_events
            else None
        )
        engine = JourneyEngine(output, emitter=emitter, argv_transform=argv_transform)
        if emitter:
            emitter.run_start()
        try:
            receipt, _, _ = engine.run()
        except JourneyInterrupted as exc:
            if emitter:
                emitter.run_end(
                    status="error",
                    outcome="interrupted",
                    facts={
                        "interrupted_step": exc.step_key,
                        "completed_steps": exc.completed,
                        "delivery": {"receipt_written": exc.receipt_written},
                    },
                )
            raise
        except JourneyError:
            if emitter:
                # engine.failed_step is set only by a real nonzero step
                # exit; an engine-level JourneyError (guard, glob mismatch)
                # carries None rather than blaming the last successful step.
                emitter.run_end(
                    status="error",
                    outcome="failed",
                    facts={
                        "failed_step": engine.failed_step,
                        "completed_steps": engine.completed_steps,
                        "delivery": {"receipt_written": engine.receipt_written},
                    },
                )
            raise
        except (OSError, ValueError, PlanError) as exc:
            if emitter:
                emitter.run_end(
                    status="error",
                    outcome="failed",
                    facts={
                        "failed_step": engine.failed_step,
                        "completed_steps": engine.completed_steps,
                        "delivery": {"receipt_written": engine.receipt_written},
                    },
                )
            raise JourneyError(f"journey internal check failed: {exc}") from exc
        return receipt, emitter
    finally:
        if sidecar is not None:
            sidecar.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--progress-events",
        action="store_true",
        help="Emit the draft quiz-progress NDJSON stream to stdout. The "
        "final one-line JSON summary is still printed last and carries no "
        "'event' key.",
    )
    parser.add_argument(
        "--events-file",
        type=Path,
        default=None,
        help="Create the same NDJSON stream in this sidecar file (opened "
        "only after the output directory is accepted).",
    )
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    streams: tuple[IO[str], ...] = (sys.stdout,) if args.progress_events else ()

    try:
        receipt, _ = run_journey(
            output,
            event_streams=streams,
            events_file=args.events_file,
        )
    except JourneyInterrupted as exc:
        print(f"ERROR: interrupted during step: {exc.step_key or 'between steps'}", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("ERROR: interrupted before the journey started", file=sys.stderr)
        return 130
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        EventEmissionError,
        PlanError,
        JourneyError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "format": FORMAT,
                "output_dir": str(output),
                "receipt": str(output / RECEIPT_NAME),
                "summary": str(output / SUMMARY_NAME),
                "unbind_readiness": "not_ready",
                "compose_readiness": "ready",
                "seal_breaks": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
