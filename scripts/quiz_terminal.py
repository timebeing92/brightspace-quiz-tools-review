#!/usr/bin/env python3
"""Guided local Quiz workflow: Unbind, review, Compose, and Rebind.

This is a bundle-owned orchestration and presentation layer.  The pinned
Workbench producers remain the authority for extraction, review marginalia,
promotion, authoring readiness, package generation, and validation.

The ordinary real-export path is deliberately staged:

1. Unbind creates verified local evidence and a protected reviewer baseline.
2. A reviewer edits the separate working workbook.
3. Compose materializes only explicit marginalia and accepted decisions, then
   runs strict readiness checks for one, several, or all selected quizzes.
4. Rebind is enabled one quiz/package at a time only when that exact Compose
   result is authoring-ready.

No command contacts Brightspace or imports a package.  A successful Rebind is
local validation evidence only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import textwrap
from typing import Any

import build_quiz_package_from_workbook as builder
from quiz_authoring_readiness import analyze_authoring_readiness, render_readiness_markdown
from quiz_build_support import default_settings_receipt
from quiz_contracts import validate_contract
from quiz_promote_revisions import PromotionError, promote_revisions
from quiz_review_workbook_reingest import ReingestError, materialize_workbook_decisions
from quiz_assessment_review import LAYOUT, prepare_review_packet, validate_packet
from quiz_unbind import UnbindFailed, UnbindRefused, run_unbind
from verify_quiz_unbind_run import UnbindVerificationError, verify_unbind_run
import validate_quiz_package as package_validator


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
REGISTRY = (
    REPO_ROOT
    / "workspace"
    / "reference"
    / "schemas"
    / "quiz"
    / "quiz_build_capabilities.json"
)
STATE_NAME = "quiz-terminal-workspace.json"
STATE_FORMAT = "brightspace-quiz-bundle.terminal-workspace/1"
WIDTH = 78


class WorkflowError(RuntimeError):
    """A safe, operator-facing workflow refusal."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "quiz"


def path_from_user(value: str) -> Path:
    """Accept a literal path or a path dragged into a terminal."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    text = text.replace("\\ ", " ")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise WorkflowError("path contains a control character")
    return Path(text).expanduser().resolve()


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise WorkflowError(f"workflow artifact is outside its workspace: {path.name}") from exc


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise WorkflowError("workspace contains an invalid empty artifact path")
    candidate = root.joinpath(*Path(relative).parts)
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise WorkflowError(f"workspace artifact escapes its root: {relative}") from exc
    if candidate.is_symlink():
        raise WorkflowError(f"workspace artifact must not be a symlink: {relative}")
    return candidate


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"could not read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{label} must be a JSON object")
    return payload


def load_state(root_value: str | Path) -> tuple[Path, dict[str, Any]]:
    root = path_from_user(str(root_value))
    state = load_json(root / STATE_NAME, "Quiz terminal workspace")
    if state.get("format") != STATE_FORMAT:
        raise WorkflowError("folder is not a supported Quiz terminal workspace")
    return root, state


def save_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(root / STATE_NAME, state)


def require_new_workspace(root: Path) -> None:
    if root.is_symlink():
        raise WorkflowError("workspace must not be a symlink")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise WorkflowError("workspace must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)


def _only(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise WorkflowError(f"expected one {label}; found {len(matches)}")
    return matches[0]


def _artifact(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise WorkflowError(f"missing workflow artifact: {path.name}")
    return {
        "path": rel(root, path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _print_rule(character: str = "─") -> None:
    print("  " + character * min(WIDTH - 2, max(36, terminal_width() - 4)))


def terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(WIDTH, 24)).columns


def _wrap(text: str, indent: str = "  ") -> None:
    for line in textwrap.wrap(
        text,
        width=max(50, min(WIDTH, terminal_width()) - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
    ):
        print(indent + line)


def heading(title: str, subtitle: str = "") -> None:
    print()
    print(f"  {title}")
    if subtitle:
        _wrap(subtitle, "  ")
    _print_rule()


def fact(label: str, value: object) -> None:
    print(f"  {label:<22} {value}")


def prompt_text(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"  ? {label}{suffix}: ").strip()
    return answer or default


def confirm(label: str, *, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"  ? {label} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def choose(label: str, options: list[tuple[str, str]], *, default: str) -> str:
    print(f"  ? {label}")
    keys = [key for key, _ in options]
    for number, (key, description) in enumerate(options, start=1):
        marker = " (default)" if key == default else ""
        print(f"      {number}. {description}{marker}")
    while True:
        answer = prompt_text("choice", str(keys.index(default) + 1))
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        if answer in keys:
            return answer
        print(f"    Enter 1-{len(options)}.")


def _model_quizzes(model_path: Path) -> list[dict[str, Any]]:
    model = load_json(model_path, "quiz model")
    quizzes = model.get("quizzes", [])
    if not isinstance(quizzes, list) or not quizzes:
        raise WorkflowError("quiz model contains no quizzes")
    return [row for row in quizzes if isinstance(row, dict)]


def select_quizzes_interactive(
    model_path: Path, current: list[str] | None = None
) -> list[str]:
    quizzes = _model_quizzes(model_path)
    if len(quizzes) == 1:
        return [str(quizzes[0]["entity_key"])]
    options = []
    for row in quizzes:
        key = str(row.get("entity_key", ""))
        title = str(row.get("title") or "Untitled quiz")
        options.append((key, f"{title}  ·  {key}"))
    mode = choose(
        "How many quizzes should Compose check?",
        [
            ("one", "One quiz"),
            ("multiple", "Several quizzes"),
            ("all", "All quizzes"),
        ],
        default="one" if not current or len(current) == 1 else "multiple",
    )
    if mode == "all":
        return [key for key, _ in options]
    if mode == "one":
        default = (
            current[0]
            if current and current[0] in {key for key, _ in options}
            else options[0][0]
        )
        return [choose("Which quiz?", options, default=default)]

    print("  ? Choose several quizzes (comma-separated numbers):")
    for number, (_key, description) in enumerate(options, start=1):
        print(f"      {number}. {description}")
    while True:
        answer = prompt_text("choices")
        tokens = [token.strip() for token in answer.split(",") if token.strip()]
        if tokens and all(
            token.isdigit() and 1 <= int(token) <= len(options) for token in tokens
        ):
            indexes = list(dict.fromkeys(int(token) - 1 for token in tokens))
            if len(indexes) >= 2:
                return [options[index][0] for index in indexes]
        print(f"    Enter two or more numbers from 1-{len(options)}, separated by commas.")


def _event_line(event: dict[str, Any]) -> None:
    if event.get("status") == "started":
        labels = {
            "source_check": "Check source and safe output boundary",
            "extract_normalize": "Extract and normalize quiz evidence",
            "project_review": "Build workbook and Reading Room",
            "readiness": "Check authoring readiness",
            "verified_close": "Verify hashes and close the Unbind record",
        }
        print(f"  … {labels.get(str(event.get('stage')), event.get('stage'))}", flush=True)


def unbind_workspace(
    source_value: str | Path,
    workspace_value: str | Path,
    *,
    asset_mode: str = "self_contained",
    source_lineage_key: str = "",
    quiz_entity_key: str = "",
    allow_tracked_output: bool = False,
) -> dict[str, Any]:
    source = path_from_user(str(source_value))
    root = path_from_user(str(workspace_value))
    require_new_workspace(root)
    unbind_dir = root / "unbind"
    heading("Unbind", "Extracting local review evidence. No package will be built or sent to Brightspace.")
    try:
        result = run_unbind(
            export_path=source,
            output_dir=unbind_dir,
            source_lineage_key=source_lineage_key,
            quiz_entity_key=quiz_entity_key,
            asset_mode=asset_mode,
            registry_path=REGISTRY,
            event_sinks=(_event_line,),
            allow_tracked_output=allow_tracked_output,
        )
    except Exception:
        # An empty shell created by this workflow is harmless; do not delete a
        # directory if another process or the operator placed anything in it.
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
        raise

    record = result["record"]
    model_path = inside(unbind_dir, record["artifacts"]["model"]["path"])
    reviewer_source = _only(
        unbind_dir / "extraction",
        "*__quiz_pool_review_reviewer.xlsx",
        "reviewer workbook",
    )
    compose_dir = root / "compose"
    compose_dir.mkdir()
    baseline = compose_dir / "reviewer_baseline_DO_NOT_EDIT.xlsx"
    working = compose_dir / "reviewer_working.xlsx"
    if asset_mode == "self_contained":
        prepare_review_packet(reviewer_source, model_path, compose_dir)
    else:
        # The explicitly requested reference-only mode retains the native view.
        shutil.copy2(reviewer_source, baseline)
        shutil.copy2(reviewer_source, working)

    state: dict[str, Any] = {
        "format": STATE_FORMAT,
        "bundle_version": VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stage": "unbound_for_review",
        "network_operations": 0,
        "brightspace_operations": 0,
        "unbind": {
            "run_dir": rel(root, unbind_dir),
            "receipt": _artifact(root, unbind_dir / "quiz_unbind_run.json"),
            "model": _artifact(root, model_path),
            "reading_room": rel(root, inside(unbind_dir, record["artifacts"]["reading_room_html"]["path"])),
            "status_station": rel(root, inside(unbind_dir, record["artifacts"]["station_html"]["path"])),
            "readiness": record["readiness"],
            "fidelity": record["fidelity"],
        },
        "review": {
            "baseline": _artifact(root, baseline),
            "working": _artifact(root, working),
            "source_workbook": _artifact(root, reviewer_source),
        },
        "compose": None,
        "rebind": {},
    }
    if (compose_dir / LAYOUT).is_file():
        state["review"]["assessment_layout"] = _artifact(root, compose_dir / LAYOUT)
    save_state(root, state)

    print("  ✓ Unbind evidence verified")
    fact("Quizzes", record["model_summary"]["quiz_count"])
    fact("Unique questions", record["model_summary"]["question_entity_count"])
    fact("Question occurrences", record["model_summary"]["question_occurrence_count"])
    fact(
        "Question library",
        "parsed automatically"
        if record["source_export"]["scope"]["questiondb_present"]
        else "not present in export",
    )
    fact("Fidelity", record["fidelity"]["state"])
    fact("Working workbook", working)
    fact("Reading Room", root / state["unbind"]["reading_room"])
    print()
    if state["review"].get("assessment_layout"):
        _wrap("Next: open reviewer_working.xlsx and follow START HERE. Keep the complete compose folder together, including Images and the unchanged baseline; Compose collects eligible review edits.")
    else:
        _wrap("Next: edit the revision fields in reviewer_working.xlsx. This reference-only workspace uses the native Quiz Questions layout and source file links. Keep reviewer_baseline_DO_NOT_EDIT.xlsx unchanged.")
    _wrap("When questiondb.xml is present, Quiz Workshop parses it automatically alongside every quiz XML. Brightspace quizzes often store placements or references while reusable question bodies live in the library; reading both prevents library-backed and library-only questions from being omitted.")
    return state


def verify_workspace(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    unbind_dir = inside(root, state["unbind"]["run_dir"])
    try:
        verification = verify_unbind_run(unbind_dir)
    except UnbindVerificationError as exc:
        raise WorkflowError(f"Unbind verification failed: {exc}") from exc
    baseline = inside(root, state["review"]["baseline"]["path"])
    if not baseline.is_file() or sha256_file(baseline) != state["review"]["baseline"]["sha256"]:
        raise WorkflowError(
            "reviewer baseline changed; restore it before Compose so source evidence remains distinguishable"
        )
    layout = state["review"].get("assessment_layout")
    if layout:
        path = inside(root, layout["path"])
        if not path.is_file() or sha256_file(path) != layout["sha256"]:
            raise WorkflowError("assessment review layout changed; restore the original companion")
        validate_packet(path.parent)
    return verification


def verify_compose_artifacts(root: Path, compose: dict[str, Any]) -> None:
    """Require the workbook and generated evidence from the exact Compose run."""
    records = [compose.get(key) for key in (
        "edited_workbook", "decision_overlay", "promoted_model",
        "promotion_receipt", "summary_markdown",
    )]
    for result in compose.get("results", []):
        records.extend(result.get(key) for key in (
            "settings_receipt", "readiness_json", "readiness_markdown",
        ))
    if compose.get("phase5_candidate_authorization"):
        records.append(compose["phase5_candidate_authorization"])
    for record in records:
        if not isinstance(record, dict) or not record.get("sha256"):
            raise WorkflowError("Compose evidence is incomplete; run Compose again")
        path = inside(root, record.get("path"))
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise WorkflowError(
                f"Compose input or artifact changed: {record['path']}; run Compose again"
            )


def show_status(workspace_value: str | Path) -> dict[str, Any]:
    root, state = load_state(workspace_value)
    verification = verify_workspace(root, state)
    heading("Quiz workflow status", str(root))
    fact("Stage", state["stage"])
    fact("Unbind receipt", verification["status"])
    fact("Fidelity", state["unbind"]["fidelity"]["state"])
    fact("Working workbook", root / state["review"]["working"]["path"])
    compose = state.get("compose")
    if compose:
        verify_compose_artifacts(root, compose)
        fact("Review metadata", compose.get("metadata_policy", "required"))
        fact("Selected quizzes", len(compose["results"]))
        fact("Ready to Rebind", compose["ready_count"])
        fact("Need review", compose["not_ready_count"])
        for result in compose["results"]:
            status = (
                "READY"
                if result["ready"]
                else f"NOT READY ({result['summary']['error_count']} errors)"
            )
            fact(f"  {result['title'][:18]}", status)
        fact("Compose summary", root / compose["summary_markdown"]["path"])
    else:
        fact("Compose", "not run")
    rebind = state.get("rebind") or {}
    validated = sum(row.get("status") == "validated_locally" for row in rebind.values())
    fact("Rebind", f"{validated} validated package(s)" if rebind else "not run")
    print()
    if validated:
        _wrap("Proof ceiling: validated locally; Brightspace import and round trip were not performed.")
    else:
        _wrap("Proof ceiling: local extraction and Compose evidence; no validated package is recorded.")
    return state


def _settings_receipt(
    overlay: dict[str, Any],
    *,
    quiz_key: str,
    model: dict[str, Any],
) -> dict[str, Any]:
    seed = json.dumps(
        {
            "quiz": quiz_key,
            "view": overlay.get("materialized_view_fingerprint"),
        },
        sort_keys=True,
    )
    run_id = f"cc:run:quiz-compose:{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
    receipt = default_settings_receipt(quiz_key, run_id)
    question_keys = {
        str(row.get("entity_key"))
        for row in model.get("questions", [])
        if isinstance(row, dict) and row.get("entity_key")
    }
    accepted = [
        row
        for row in overlay.get("settings_inputs", [])
        if isinstance(row, dict)
        and (
            row.get("target_entity_key") == quiz_key
            or (
                row.get("target_entity_key") in question_keys
                and row.get("setting") == "randomize_answers"
            )
        )
    ]
    resolutions = {
        (row["target_entity_key"], row["setting"]): row
        for row in receipt["resolutions"]
    }
    for row in accepted:
        receipt["inputs"].append(row)
        key = (row["target_entity_key"], row["setting"])
        previous = resolutions.get(key)
        considered = ([previous["winner_input_id"]] if previous else []) + [row["input_id"]]
        resolution = {
            "setting": row["setting"],
            "target_entity_key": row["target_entity_key"],
            "state": row["state"],
            "effective_value": row["value"],
            "winner_input_id": row["input_id"],
            "considered_input_ids": considered,
            "defaulted": False,
            "coerced": False,
            "coercion_note": None,
            "source_evidence_keys": list(row.get("source_evidence_keys", [])),
            "extensions": {
                "coursecraft.binder.accepted_setting_decision": row["input_id"]
            },
        }
        resolutions[key] = resolution
    receipt["resolutions"] = [resolutions[key] for key in sorted(resolutions)]
    receipt["extensions"].update(
        {
            "coursecraft.binder.route_status": "local_only",
            "coursecraft.binder.accepted_setting_input_count": len(accepted),
            "coursecraft.binder.unaccepted_settings_use_safe_defaults": True,
        }
    )
    issues = validate_contract(receipt, mode="transform")
    if issues:
        raise WorkflowError(f"settings receipt is invalid: {issues[0].render()}")
    return receipt


def compose_workspace(
    workspace_value: str | Path,
    *,
    quiz_entity_keys: list[str],
    edited_workbook: str | Path | None = None,
    phase5_candidate_authorization: str | Path | None = None,
    metadata_policy: str = "optional",
) -> dict[str, Any]:
    root, state = load_state(workspace_value)
    verify_workspace(root, state)
    quiz_entity_keys = list(dict.fromkeys(quiz_entity_keys))
    if not quiz_entity_keys:
        raise WorkflowError("Compose requires at least one reviewed quiz selection")
    model_path = inside(root, state["unbind"]["model"]["path"])
    baseline = inside(root, state["review"]["baseline"]["path"])
    edited = (
        path_from_user(str(edited_workbook))
        if edited_workbook is not None
        else inside(root, state["review"]["working"]["path"])
    )
    if not edited.is_file() or edited.is_symlink():
        raise WorkflowError("edited reviewer workbook is missing or is a symlink")
    quiz_rows = _model_quizzes(model_path)
    quizzes_by_key = {str(row.get("entity_key")): row for row in quiz_rows}
    missing = [key for key in quiz_entity_keys if key not in quizzes_by_key]
    if missing:
        raise WorkflowError(
            f"selected quiz entity key is not present in the Unbind model: {missing[0]}"
        )
    if phase5_candidate_authorization and len(quiz_entity_keys) != 1:
        raise WorkflowError(
            "a Phase 5 candidate authorization is exact-bound to one quiz; select one quiz"
        )

    heading(
        "Compose",
        "Reading explicit reviewer marginalia and checking each selected quiz against the pinned capability registry.",
    )
    generated = root / "compose" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    # Replacement paths are relative to the protected review packet.
    overlay_path = baseline.parent / "decision-overlay.json"
    asset_roots = list(dict.fromkeys([model_path.parent, baseline.parent]))
    promoted_path = generated / "promoted.model.json"
    promotion_path = generated / "promotion.receipt.json"
    readiness_dir = generated / "readiness"
    readiness_dir.mkdir(exist_ok=True)

    overlay = materialize_workbook_decisions(
        model_path, baseline, edited, metadata_policy=metadata_policy,
    )
    write_json(overlay_path, overlay)
    promoted, promotion = promote_revisions(model_path, overlay_path, REGISTRY)
    write_json(promoted_path, promoted)
    write_json(promotion_path, promotion)
    if promotion["excluded"]:
        # Accepted edits must never disappear into an otherwise buildable ZIP.
        state["compose"] = None
        state["rebind"] = {}
        state["stage"] = "compose_review_required"
        save_state(root, state)
        reasons = "; ".join(sorted({row["reason"] for row in promotion["excluded"]}))
        raise WorkflowError(
            f"{len(promotion['excluded'])} accepted revision(s) could not be applied: "
            f"{reasons}. Inspect {promotion_path} and resolve before Rebind."
        )
    authorization_path = (
        path_from_user(str(phase5_candidate_authorization))
        if phase5_candidate_authorization
        else None
    )
    results: list[dict[str, Any]] = []
    for index, quiz_key in enumerate(quiz_entity_keys, start=1):
        title = str(quizzes_by_key[quiz_key].get("title") or "Untitled quiz")
        token = f"{index:02d}__{safe_label(title)[:48]}"
        settings_path = generated / f"{token}__settings.receipt.json"
        settings = _settings_receipt(overlay, quiz_key=quiz_key, model=promoted)
        write_json(settings_path, settings)
        readiness = analyze_authoring_readiness(
            promoted_path,
            quiz_entity_key=quiz_key,
            settings_path=settings_path,
            asset_root=asset_roots,
            promotion_receipt_path=promotion_path,
            trial_authorization_path=authorization_path,
        )
        readiness_json = readiness_dir / f"{token}.json"
        readiness_md = readiness_dir / f"{token}.md"
        write_json(readiness_json, readiness)
        readiness_md.write_text(render_readiness_markdown(readiness), encoding="utf-8")
        results.append(
            {
                "quiz_entity_key": quiz_key,
                "title": title,
                "settings_receipt": _artifact(root, settings_path),
                "readiness_json": _artifact(root, readiness_json),
                "readiness_markdown": _artifact(root, readiness_md),
                "ready": bool(readiness["ready"]),
                "summary": readiness["summary"],
                "blocker_codes": [
                    row["code"]
                    for row in readiness["issues"]
                    if row["severity"] == "error"
                ],
                "warning_codes": [
                    row["code"]
                    for row in readiness["issues"]
                    if row["severity"] == "warning"
                ],
            }
        )

    ready_count = sum(row["ready"] for row in results)
    summary_path = generated / "COMPOSE_SUMMARY.md"
    table_rows = []
    for row in results:
        title = row["title"].replace("|", "\\|")
        status = "READY" if row["ready"] else "NOT READY"
        table_rows.append(
            f"| {title} | {status} | {row['summary']['error_count']} | "
            f"{row['summary']['warning_count']} | `{row['readiness_markdown']['path']}` |"
        )
    summary_path.write_text(
        "\n".join(
            [
                "# Quiz Compose Summary",
                "",
                f"- Selected quizzes: `{len(results)}`",
                f"- Review metadata policy: `{metadata_policy}`",
                f"- Ready to Rebind: `{ready_count}`",
                f"- Need review: `{len(results) - ready_count}`",
                "",
                "| Quiz | State | Errors | Warnings | Readiness report |",
                "| --- | --- | ---: | ---: | --- |",
                *table_rows,
                "",
                "Each Rebind target remains one quiz/package. No Brightspace operation was performed.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    compose = {
        "composed_at": utc_now(),
        "metadata_policy": metadata_policy,
        "quiz_entity_keys": quiz_entity_keys,
        "edited_workbook": _artifact(root, edited),
        "decision_overlay": _artifact(root, overlay_path),
        "promoted_model": _artifact(root, promoted_path),
        "promotion_receipt": _artifact(root, promotion_path),
        "asset_roots": [rel(root, path) for path in asset_roots],
        "phase5_candidate_authorization": (
            _artifact(root, authorization_path) if authorization_path else None
        ),
        "results": results,
        "summary_markdown": _artifact(root, summary_path),
        "ready_count": ready_count,
        "not_ready_count": len(results) - ready_count,
        "accepted_change_count": overlay["content_minimized_summary"]["accepted_change_count"],
        "accepted_setting_input_count": overlay["content_minimized_summary"]["accepted_setting_input_count"],
    }
    state["compose"] = compose
    state["rebind"] = {}
    state["stage"] = "ready_to_rebind" if ready_count else "compose_review_required"
    save_state(root, state)

    fact("Selected quizzes", len(results))
    fact("Review metadata", metadata_policy)
    fact("Accepted revisions", compose["accepted_change_count"])
    fact("Accepted settings", compose["accepted_setting_input_count"])
    fact("Ready to Rebind", ready_count)
    fact("Need review", len(results) - ready_count)
    for result in results:
        print()
        fact(result["title"], "READY" if result["ready"] else "NOT READY")
        fact("  Selected questions", result["summary"]["selected_question_count"])
        fact(
            "  Errors / warnings",
            f"{result['summary']['error_count']} / {result['summary']['warning_count']}",
        )
        fact("  Report", root / result["readiness_markdown"]["path"])
        if result["blocker_codes"]:
            _wrap("Gates: " + ", ".join(sorted(set(result["blocker_codes"]))))
    fact("Compose summary", summary_path)
    if ready_count:
        print("  ✓ Ready quizzes may enter local Rebind one package at a time.")
    if ready_count < len(results):
        print("  ■ Expected stop for quizzes that need review; no package was built for them.")
    _wrap("Settings without an accepted reviewer decision use the builder's recorded safe defaults; inspect the settings receipt before Rebind.")
    return state


def rebind_workspace(
    workspace_value: str | Path,
    *,
    quiz_entity_key: str = "",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    root, state = load_state(workspace_value)
    verify_workspace(root, state)
    compose = state.get("compose")
    if not isinstance(compose, dict) or not compose.get("results"):
        raise WorkflowError(
            "Rebind is unavailable until Compose has checked at least one quiz"
        )
    ready_results = [row for row in compose["results"] if row.get("ready")]
    if quiz_entity_key:
        matches = [
            row for row in ready_results if row["quiz_entity_key"] == quiz_entity_key
        ]
        if not matches:
            raise WorkflowError(
                "selected quiz is not ready in the exact Compose result"
            )
        selected = matches[0]
    elif len(ready_results) == 1:
        selected = ready_results[0]
    elif not ready_results:
        raise WorkflowError("Rebind is unavailable because no selected quiz is ready")
    else:
        raise WorkflowError(
            "more than one selected quiz is ready; pass --quiz-entity-key for one package"
        )
    verify_compose_artifacts(root, compose)
    promoted = inside(root, compose["promoted_model"]["path"])
    promotion = inside(root, compose["promotion_receipt"]["path"])
    settings = inside(root, selected["settings_receipt"]["path"])
    # Read earlier workspace records as well as the multiple-root form.
    asset_roots = [inside(root, path) for path in
                   compose.get("asset_roots", [compose.get("asset_root")])]
    asset_args = [value for path in asset_roots for value in ("--asset-root", str(path))]
    authorization = compose.get("phase5_candidate_authorization")
    authorization_path = inside(root, authorization["path"]) if authorization else None
    rebind_root = (
        path_from_user(str(output_dir))
        if output_dir is not None
        else root / "rebind" / safe_label(selected["title"])
    )
    if rebind_root.exists() and (not rebind_root.is_dir() or any(rebind_root.iterdir())):
        raise WorkflowError("Rebind output must be a new or empty directory")
    rebind_root.mkdir(parents=True, exist_ok=True)
    package_dir = rebind_root / "package"
    zip_path = rebind_root / "quiz-import.zip"
    receipts = rebind_root / "receipts"
    validation_path = rebind_root / "VALIDATION.md"

    heading("Rebind", "Generating and strictly validating a local Brightspace package. No import will run.")
    args = [
        str(promoted),
        "--output-dir",
        str(package_dir),
        "--zip-output",
        str(zip_path),
        "--receipt-dir",
        str(receipts),
        "--quiz-entity-key",
        selected["quiz_entity_key"],
        "--settings",
        str(settings),
        *asset_args,
        "--promotion-receipt",
        str(promotion),
    ]
    if authorization_path:
        args.extend(["--phase5-candidate-authorization", str(authorization_path)])
    builder.build_package(builder.parse_args(args))
    run_receipt = receipts / "quiz_build.run.json"
    validate_args = [
        str(package_dir),
        "--model",
        str(promoted),
        "--settings",
        str(settings),
        "--quiz-entity-key",
        selected["quiz_entity_key"],
        *asset_args,
        "--promotion-receipt",
        str(promotion),
        "--run-receipt",
        str(run_receipt),
        "--zip",
        str(zip_path),
        "--strict-closure",
    ]
    if authorization_path:
        validate_args.extend(["--phase5-candidate-authorization", str(authorization_path)])
    report = package_validator.validate_package(package_validator.parse_args(validate_args))
    validation_path.write_text(package_validator.render_report(report), encoding="utf-8")
    if report.errors:
        state.setdefault("rebind", {})[selected["quiz_entity_key"]] = {
            "status": "validation_failed_do_not_import",
            "attempted_at": utc_now(),
            "validation": _artifact(root, validation_path)
            if validation_path.is_relative_to(root)
            else {"path": str(validation_path), "sha256": sha256_file(validation_path)},
            "error_count": len(report.errors),
        }
        state["stage"] = "rebind_validation_failed"
        save_state(root, state)
        raise WorkflowError(
            f"generated package failed strict validation with {len(report.errors)} error(s); do not import it"
        )

    summary_path = rebind_root / "REBIND_SUMMARY.md"
    summary_path.write_text(
        "\n".join(
            [
                "# Quiz Rebind Summary",
                "",
                f"- Selected quiz: `{selected['quiz_entity_key']}`",
                "- Status: `validated locally`",
                f"- Package ZIP SHA-256: `{sha256_file(zip_path)}`",
                f"- Validation errors: `{len(report.errors)}`",
                f"- Validation warnings: `{len(report.warnings)}`",
                "- Brightspace import: `not performed`",
                "- Brightspace round trip: `not performed`",
                "",
                "Local validation does not establish tenant import or round-trip fidelity.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    state.setdefault("rebind", {})[selected["quiz_entity_key"]] = {
        "status": "validated_locally",
        "validated_at": utc_now(),
        "output_root": str(rebind_root),
        "package_zip": {
            "path": str(zip_path),
            "sha256": sha256_file(zip_path),
            "bytes": zip_path.stat().st_size,
        },
        "run_receipt": {"path": str(run_receipt), "sha256": sha256_file(run_receipt)},
        "validation": {"path": str(validation_path), "sha256": sha256_file(validation_path)},
        "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "brightspace_import": "not_performed",
        "brightspace_roundtrip": "not_performed",
    }
    state["stage"] = "validated_locally"
    save_state(root, state)
    print("  ✓ Package built and validated locally")
    fact("Package ZIP", zip_path)
    fact(
        "SHA-256",
        state["rebind"][selected["quiz_entity_key"]]["package_zip"]["sha256"],
    )
    fact("Validation", validation_path)
    print()
    _wrap("Stop here for local work. Brightspace import and re-export verification were not performed by this terminal.")
    return state


def _wizard_unbind() -> int:
    source_text = prompt_text("Brightspace export ZIP or folder")
    if not source_text:
        raise WorkflowError("an export path is required")
    source = path_from_user(source_text)
    suggested = str(Path.cwd() / "output" / f"{safe_label(source.stem)}__quiz_workflow")
    workspace = path_from_user(prompt_text("New workflow folder", suggested))
    mode = choose(
        "How should included images and files be handled?",
        [
            ("self_contained", "Copy available assets into local evidence"),
            ("reference", "Keep source references only (smaller, less shareable)"),
        ],
        default="self_contained",
    )
    heading("Ready to Unbind")
    fact("Reads", source)
    fact("Writes", workspace)
    fact("Quiz scope", "all quizzes in the export")
    fact("Question library", "parsed automatically when present")
    fact("Assets", "copied when present" if mode == "self_contained" else "references only")
    fact("Network", "none")
    fact("Brightspace", "no operations")
    _wrap(
        "Quiz XML may contain placements or references while reusable question "
        "bodies live in questiondb.xml. Unbind therefore keeps every quiz and "
        "parses the available library together; quiz selection happens later in "
        "Compose without discarding that relationship evidence."
    )
    if not confirm("Begin Unbind?", default=True):
        print("  Canceled; nothing was written.")
        return 2
    unbind_workspace(source, workspace, asset_mode=mode)
    return 0


def _wizard_compose() -> int:
    root = path_from_user(prompt_text("Quiz workflow folder"))
    _, state = load_state(root)
    verify_workspace(root, state)
    model_path = inside(root, state["unbind"]["model"]["path"])
    current = (state.get("compose") or {}).get("quiz_entity_keys", [])
    keys = select_quizzes_interactive(
        model_path,
        current if isinstance(current, list) else [],
    )
    quizzes_by_key = {
        str(row.get("entity_key")): str(row.get("title") or "Untitled quiz")
        for row in _model_quizzes(model_path)
    }
    working = root / state["review"]["working"]["path"]
    heading("Review checkpoint")
    fact("Working workbook", working)
    fact("Selected quizzes", len(keys))
    for key in keys:
        _wrap(f"• {quizzes_by_key[key]}", "    ")
    _wrap("Compose reads explicit reviewer proposals. Keep extracted source cells unchanged. Blank approval status leaves a proposal open; only an explicit accepted decision can be applied.")
    metadata_policy = choose(
        "How should descriptive review metadata be handled?",
        [
            ("optional", "Allow blank names, reasons and dates"),
            ("required", "Require proposer, reason, dates and decision attribution"),
        ],
        default="optional",
    )
    if not confirm("Has review and approval been saved in the working workbook?", default=False):
        print("  Compose paused. The workspace is ready when review is complete.")
        return 0
    result = compose_workspace(root, quiz_entity_keys=keys, metadata_policy=metadata_policy)
    ready = [row for row in result["compose"]["results"] if row["ready"]]
    if ready and confirm("Run local Rebind for a ready quiz now?", default=False):
        if len(ready) == 1:
            rebind_key = ready[0]["quiz_entity_key"]
        else:
            rebind_key = choose(
                "Which ready quiz should become one local package?",
                [
                    (row["quiz_entity_key"], row["title"])
                    for row in ready
                ],
                default=ready[0]["quiz_entity_key"],
            )
        rebind_workspace(root, quiz_entity_key=rebind_key)
    return 0


def _wizard_rebind() -> int:
    root = path_from_user(prompt_text("Quiz workflow folder"))
    _, state = load_state(root)
    verify_workspace(root, state)
    compose = state.get("compose") or {}
    ready = [row for row in compose.get("results", []) if row.get("ready")]
    if not ready:
        raise WorkflowError("Rebind is unavailable because no selected quiz is ready; run Compose first")
    verify_compose_artifacts(root, compose)
    key = choose(
        "Which ready quiz should become one local package?",
        [(row["quiz_entity_key"], row["title"]) for row in ready],
        default=ready[0]["quiz_entity_key"],
    )
    selected = next(row for row in ready if row["quiz_entity_key"] == key)
    output = path_from_user(prompt_text(
        "New package folder", str(root / "rebind" / safe_label(selected["title"])),
    ))
    heading("Ready to Rebind")
    fact("Quiz", selected["title"])
    fact("Writes", output)
    _wrap("The package will be built and validated locally. Brightspace import remains a separate step.")
    if not confirm("Build this local package?", default=False):
        print("  Canceled; nothing was written.")
        return 0
    rebind_workspace(root, quiz_entity_key=key, output_dir=output)
    return 0


def interactive_wizard() -> int:
    print()
    print("  QUIZ WORKSHOP")
    print(f"  Unbind · Compose · Rebind                         v{VERSION}")
    _print_rule("═")
    _wrap("A local workflow for Brightspace quiz evidence and bounded package authoring. It never signs in to Brightspace or imports a package.")
    action = choose(
        "What would you like to do?",
        [
            ("unbind", "Unbind a Brightspace export for review"),
            ("compose", "Continue a reviewed workflow with Compose"),
            ("status", "Verify and summarize an existing workflow"),
            ("proof", "Open the advanced synthetic full-screen proof"),
            ("rebind", "Rebind an already reviewed, ready quiz"),
            ("quit", "Quit"),
        ],
        default="unbind",
    )
    if action == "unbind":
        return _wizard_unbind()
    if action == "compose":
        return _wizard_compose()
    if action == "status":
        show_status(prompt_text("Quiz workflow folder"))
        return 0
    if action == "rebind":
        return _wizard_rebind()
    if action == "quit":
        print("  Quiz Workshop closed.")
        return 0
    from quiz_binder_tui import main as proof_main

    return proof_main([])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"quiz-terminal v{VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    unbind = subparsers.add_parser("unbind", help="extract a real export into a review workspace")
    unbind.add_argument("source")
    unbind.add_argument("--workspace", required=True)
    unbind.add_argument("--asset-mode", choices=("self-contained", "reference"), default="self-contained")
    unbind.add_argument("--source-lineage-key", default="")
    unbind.add_argument(
        "--quiz-entity-key",
        default="",
        help=(
            "optional initial-readiness focus; Unbind still extracts all quizzes "
            "and the complete available question library"
        ),
    )
    unbind.add_argument("--allow-tracked-output", action="store_true")

    status = subparsers.add_parser("status", help="verify and summarize a workflow")
    status.add_argument("workspace")

    compose = subparsers.add_parser("compose", help="materialize review decisions and check readiness")
    compose.add_argument("workspace")
    compose_scope = compose.add_mutually_exclusive_group(required=True)
    compose_scope.add_argument(
        "--quiz-entity-key",
        action="append",
        default=[],
        help="quiz key to check; repeat for multiple quizzes",
    )
    compose_scope.add_argument(
        "--all-quizzes",
        action="store_true",
        help="check every quiz while retaining the shared full-library evidence",
    )
    compose.add_argument("--edited-workbook")
    compose.add_argument("--phase5-candidate-authorization")
    compose.add_argument(
        "--metadata-policy", choices=("optional", "required"), default="optional",
        help="allow blank review attribution (default), or require complete metadata",
    )

    rebind = subparsers.add_parser("rebind", help="build only an authoring-ready Compose result")
    rebind.add_argument("workspace")
    rebind.add_argument(
        "--quiz-entity-key",
        default="",
        help="required when more than one selected quiz is ready",
    )
    rebind.add_argument("--output-dir")

    subparsers.add_parser("wizard", help="open the guided interactive workflow")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.command in {None, "wizard"}:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                raise WorkflowError("interactive wizard requires a terminal; use a subcommand for automation")
            return interactive_wizard()
        if args.command == "unbind":
            unbind_workspace(
                args.source,
                args.workspace,
                asset_mode=args.asset_mode.replace("-", "_"),
                source_lineage_key=args.source_lineage_key,
                quiz_entity_key=args.quiz_entity_key,
                allow_tracked_output=args.allow_tracked_output,
            )
        elif args.command == "status":
            show_status(args.workspace)
        elif args.command == "compose":
            if args.all_quizzes:
                root, state = load_state(args.workspace)
                model_path = inside(root, state["unbind"]["model"]["path"])
                quiz_entity_keys = [
                    str(row["entity_key"]) for row in _model_quizzes(model_path)
                ]
            else:
                quiz_entity_keys = args.quiz_entity_key
            compose_workspace(
                args.workspace,
                quiz_entity_keys=quiz_entity_keys,
                edited_workbook=args.edited_workbook,
                phase5_candidate_authorization=args.phase5_candidate_authorization,
                metadata_policy=args.metadata_policy,
            )
        elif args.command == "rebind":
            rebind_workspace(
                args.workspace,
                quiz_entity_key=args.quiz_entity_key,
                output_dir=args.output_dir,
            )
        return 0
    except KeyboardInterrupt:
        print("\n  Canceled. No completion claim was recorded.", file=sys.stderr)
        return 130
    except EOFError:
        print("\n  Input closed. Canceled without accepting a default confirmation.", file=sys.stderr)
        return 2
    except (WorkflowError, UnbindRefused, UnbindFailed, ReingestError, PromotionError, OSError, ValueError) as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
