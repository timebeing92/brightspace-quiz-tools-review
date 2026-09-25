#!/usr/bin/env python3
"""Render a plain, read-only log from a completed synthetic Quiz Binder run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import textwrap
from typing import Any


FORMAT = "brightspace-quiz-bundle.synthetic-journey/1"
RECEIPT_NAME = "journey.receipt.json"
KNOWN_BLOCKER_CODES = {
    "missing_permanent_question_code",
    "question_kind_not_buildable",
    "question_not_approved_for_build",
}


class LogRenderError(RuntimeError):
    """Raised when a run cannot support an honest completed-run log."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LogRenderError(f"{label} must be an object")
    return value


def rows(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LogRenderError(f"{label} must be an array")
    return value


def text_value(value: Any, label: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value:
        raise LogRenderError(f"{label} must be a non-empty string")
    if len(value) > maximum:
        raise LogRenderError(f"{label} is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise LogRenderError(f"{label} contains terminal control characters")
    return value


def integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LogRenderError(f"{label} must be an integer")
    if value < 0:
        raise LogRenderError(f"{label} must not be negative")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LogRenderError(f"cannot read {label}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LogRenderError(f"{label} is not valid JSON: {exc.msg}") from exc
    return mapping(payload, label)


def safe_artifact_path(run_dir: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise LogRenderError(f"unsafe receipt artifact path: {relative}")
    candidate = (run_dir / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        raise LogRenderError(f"receipt artifact escapes the run directory: {relative}") from exc
    return candidate


def verified_artifacts(
    receipt: dict[str, Any], run_dir: Path
) -> dict[str, Path]:
    verified: dict[str, Path] = {}
    for index, raw in enumerate(rows(receipt.get("artifacts"), "receipt.artifacts")):
        artifact = mapping(raw, f"receipt.artifacts[{index}]")
        relative = text_value(
            artifact.get("path"), f"receipt.artifacts[{index}].path"
        )
        if relative in verified:
            raise LogRenderError(f"duplicate receipt artifact path: {relative}")
        expected = text_value(
            artifact.get("sha256"), f"receipt.artifacts[{index}].sha256", maximum=64
        ).lower()
        if len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            raise LogRenderError(f"invalid SHA-256 for receipt artifact: {relative}")
        path = safe_artifact_path(run_dir, relative)
        if not path.is_file():
            raise LogRenderError(f"completed-run artifact is missing: {relative}")
        if sha256_file(path) != expected:
            raise LogRenderError(f"SHA-256 mismatch for completed-run artifact: {relative}")
        verified[relative] = path
    if not verified:
        raise LogRenderError("receipt.artifacts is empty")
    return verified


def find_artifact(
    artifacts: dict[str, Path], suffix: str, label: str
) -> tuple[str, Path]:
    exact = suffix.lstrip("/")
    matches = [
        (relative, path)
        for relative, path in artifacts.items()
        if relative == exact or relative.endswith(suffix)
    ]
    if len(matches) != 1:
        raise LogRenderError(
            f"expected one {label} artifact ending in {suffix!r}, found {len(matches)}"
        )
    return matches[0]


def companion_markdown(json_relative: str, json_path: Path, label: str) -> str:
    markdown_path = json_path.with_suffix(".md")
    if not markdown_path.is_file():
        raise LogRenderError(f"completed-run {label} is missing: {markdown_path.name}")
    return str(PurePosixPath(json_relative).with_suffix(".md"))


def command_rows(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows(receipt.get("commands"), "receipt.commands")):
        command = mapping(raw, f"receipt.commands[{index}]")
        step = text_value(command.get("step"), f"receipt.commands[{index}].step", maximum=128)
        if step in seen:
            raise LogRenderError(f"duplicate completed-run step: {step}")
        seen.add(step)
        entrypoint = text_value(
            command.get("entrypoint"),
            f"receipt.commands[{index}].entrypoint",
        )
        exit_code = integer(command.get("exit_code"), f"receipt.commands[{index}].exit_code")
        if exit_code != 0:
            raise LogRenderError(
                f"completed receipt contains a refused step: {step} exited {exit_code}"
            )
        commands.append(
            {"step": step, "entrypoint": entrypoint, "exit_code": exit_code}
        )
    if not commands:
        raise LogRenderError("receipt.commands is empty")
    return commands


def readiness_report(
    artifacts: dict[str, Path], lane: str
) -> tuple[str, str, dict[str, Any]]:
    relative, path = find_artifact(
        artifacts,
        f"/{lane}.model__quiz_authoring_readiness.json",
        f"{lane} readiness JSON",
    )
    markdown = companion_markdown(relative, path, f"{lane} readiness report")
    return relative, markdown, load_json(path, f"{lane} readiness JSON")


def settings_receipt(
    run_dir: Path, artifacts: dict[str, Path]
) -> tuple[str, str]:
    run_relative, run_path = find_artifact(
        artifacts,
        "/compose/receipts/quiz_build.run.json",
        "quiz build run receipt",
    )
    run_receipt = load_json(run_path, "quiz build run receipt")
    candidates = []
    run_artifacts = rows(
        run_receipt.get("artifacts"), "quiz build run receipt.artifacts"
    )
    for index, raw in enumerate(run_artifacts):
        artifact = mapping(raw, f"quiz build run receipt.artifacts[{index}]")
        nested_path = text_value(
            artifact.get("path"),
            f"quiz build run receipt.artifacts[{index}].path",
        )
        if Path(nested_path).name == "quiz_build.settings.json":
            candidates.append(artifact)
    if len(candidates) != 1:
        raise LogRenderError(
            "quiz build run receipt must record exactly one quiz_build.settings.json artifact"
        )
    nested = candidates[0]
    expected = text_value(
        nested.get("sha256"), "quiz build settings receipt SHA-256", maximum=64
    ).lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise LogRenderError("quiz build settings receipt has an invalid SHA-256")
    path = run_path.parent / "quiz_build.settings.json"
    if not path.is_file():
        raise LogRenderError("quiz build settings receipt is missing")
    if sha256_file(path) != expected:
        raise LogRenderError("quiz build settings receipt SHA-256 mismatch")
    relative = path.resolve().relative_to(run_dir).as_posix()
    return run_relative, relative


def issue_pairs(report: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
    grouped: dict[str, set[tuple[str, str]]] = {}
    for index, raw in enumerate(rows(report.get("issues"), "unbind readiness.issues")):
        issue = mapping(raw, f"unbind readiness.issues[{index}]")
        if issue.get("severity") != "error":
            continue
        code = text_value(issue.get("code"), f"unbind readiness.issues[{index}].code", maximum=128)
        if code not in KNOWN_BLOCKER_CODES:
            continue
        message = text_value(
            issue.get("message"),
            f"unbind readiness.issues[{index}].message",
            maximum=512,
        )
        remediation = text_value(
            issue.get("remediation"),
            f"unbind readiness.issues[{index}].remediation",
            maximum=1024,
        )
        grouped.setdefault(code, set()).add((message, remediation))
    return grouped


def append_paragraph(lines: list[str], value: str, *, width: int = 78) -> None:
    lines.extend(
        textwrap.wrap(
            value,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def append_issue_field(lines: list[str], label: str, value: str) -> None:
    lines.extend(
        textwrap.wrap(
            value,
            width=78,
            initial_indent=f"   {label}: ",
            subsequent_indent="     ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def append_path(lines: list[str], label: str, path: str, *, note: str = "") -> None:
    lines.append(f"{label}:")
    lines.append(f"  {path}{note}")


def render_completed_run(run_dir: Path) -> str:
    root = run_dir.expanduser().resolve()
    if not root.is_dir():
        raise LogRenderError(f"run directory does not exist: {run_dir}")
    receipt_path = root / RECEIPT_NAME
    if not receipt_path.is_file():
        raise LogRenderError(
            "incomplete run: journey.receipt.json is missing; "
            "partial-run narration is not implemented"
        )
    receipt = load_json(receipt_path, RECEIPT_NAME)
    if receipt.get("format") != FORMAT:
        raise LogRenderError(f"unsupported completed-run format: {receipt.get('format')!r}")
    if receipt.get("fixture_policy") != "synthetic_only":
        raise LogRenderError("completed-run renderer currently accepts synthetic-only receipts")
    network_operations = integer(receipt.get("network_operations"), "receipt.network_operations")
    brightspace_operations = integer(
        receipt.get("brightspace_operations"), "receipt.brightspace_operations"
    )
    if network_operations != 0 or brightspace_operations != 0:
        raise LogRenderError(
            "synthetic completed-run receipt unexpectedly records external operations"
        )

    commands = command_rows(receipt)
    artifacts = verified_artifacts(receipt, root)
    doors = mapping(receipt.get("doors"), "receipt.doors")
    unbind = mapping(doors.get("unbind"), "receipt.doors.unbind")
    compose = mapping(doors.get("compose"), "receipt.doors.compose")
    seal = mapping(receipt.get("seal"), "receipt.seal")

    unbind_readiness = text_value(
        unbind.get("readiness"), "receipt.doors.unbind.readiness", maximum=64
    )
    compose_readiness = text_value(
        compose.get("readiness"), "receipt.doors.compose.readiness", maximum=64
    )
    if unbind_readiness != "not_ready" or compose_readiness != "ready":
        raise LogRenderError("completed synthetic door states do not match the format contract")

    unbind_count = integer(unbind.get("question_count"), "receipt.doors.unbind.question_count")
    accepted_count = integer(
        unbind.get("accepted_change_count"),
        "receipt.doors.unbind.accepted_change_count",
    )
    applied_count = integer(
        unbind.get("applied_change_count"),
        "receipt.doors.unbind.applied_change_count",
    )
    compose_count = integer(
        compose.get("question_count"), "receipt.doors.compose.question_count"
    )
    package_zip = text_value(
        compose.get("package_zip"), "receipt.doors.compose.package_zip"
    )
    if package_zip not in artifacts:
        raise LogRenderError("compose package ZIP is not a verified journey artifact")

    blocker_codes = []
    raw_blocker_codes = rows(
        unbind.get("blocker_codes"), "receipt.doors.unbind.blocker_codes"
    )
    for index, value in enumerate(raw_blocker_codes):
        code = text_value(value, f"receipt.doors.unbind.blocker_codes[{index}]", maximum=128)
        if code in blocker_codes:
            raise LogRenderError(f"duplicate blocker code: {code}")
        blocker_codes.append(code)

    unbind_json, unbind_markdown, unbind_report = readiness_report(
        artifacts, "promoted"
    )
    compose_json, compose_markdown, compose_report = readiness_report(
        artifacts, "buildable_quiz"
    )
    if unbind_report.get("ready") is not False or compose_report.get("ready") is not True:
        raise LogRenderError("readiness JSON values do not match the completed door states")
    compose_summary = mapping(compose_report.get("summary"), "compose readiness.summary")
    selected_questions = integer(
        compose_summary.get("selected_question_count"),
        "compose readiness.summary.selected_question_count",
    )
    if selected_questions != compose_count:
        raise LogRenderError("compose question count disagrees with its readiness report")
    draw_count = integer(
        compose_summary.get("reachable_draw_count"),
        "compose readiness.summary.reachable_draw_count",
    )
    asset_count = integer(
        compose_summary.get("selected_asset_count"),
        "compose readiness.summary.selected_asset_count",
    )

    validation_errors = integer(
        seal.get("validation_error_count"), "receipt.seal.validation_error_count"
    )
    equivalence_breaks = integer(
        seal.get("local_equivalence_break_count"),
        "receipt.seal.local_equivalence_break_count",
    )
    brightspace_roundtrip = text_value(
        seal.get("brightspace_roundtrip"),
        "receipt.seal.brightspace_roundtrip",
        maximum=64,
    )
    if brightspace_roundtrip != "not_performed":
        raise LogRenderError(
            "this renderer cannot elevate proof above the synthetic local-run contract"
        )
    if seal.get("capability_evidence_changed") is not False:
        raise LogRenderError("synthetic run unexpectedly changes capability evidence")

    validation_relative, _ = find_artifact(
        artifacts, "/compose/package-validation.md", "package validation note"
    )
    evidence_relative, _ = find_artifact(
        artifacts,
        "__quiz_pool_review.model.json",
        "unbound evidence model",
    )
    diff_json_relative, diff_json_path = find_artifact(
        artifacts, "__roundtrip_diff.json", "local-equivalence record"
    )
    diff_markdown = companion_markdown(
        diff_json_relative, diff_json_path, "local-equivalence report"
    )
    run_receipt_relative, settings_receipt_relative = settings_receipt(
        root, artifacts
    )

    lines = [
        "QUIZ BINDER - completed run log",
        "================================",
        "mode: read-only; completed synthetic receipts only",
        f"format: {FORMAT}",
        "",
        "RUN RECORD",
    ]
    total = len(commands)
    for index, command in enumerate(commands, start=1):
        boundary = (
            " [boundary]"
            if command["step"] == "check_unbound_readiness"
            else ""
        )
        lines.append(
            f"entry {index} of {total}: {command['step']} - completed "
            f"(exit {command['exit_code']}){boundary}"
        )

    lines.extend(
        [
            "",
            "UNBIND",
            f"questions extracted: {unbind_count}",
            f"accepted changes: {accepted_count}",
            f"promoted changes: {applied_count}",
        ]
    )
    append_path(lines, "evidence", evidence_relative, note=" (SHA-256 verified)")
    lines.extend(
        [
            "",
            "STOP: not ready to rebind. This stop is expected behavior.",
            f"readiness: {unbind_readiness}",
            "Extracted questions are evidence, not build-approved records.",
        ]
    )
    append_path(lines, "report", unbind_markdown)
    append_path(lines, "record", unbind_json, note=" (SHA-256 verified)")
    lines.append(f"blockers ({len(blocker_codes)}):")
    grouped_issues = issue_pairs(unbind_report)
    for index, code in enumerate(blocker_codes, start=1):
        lines.append(f"{index}. {code}")
        pairs = grouped_issues.get(code, set())
        if code in KNOWN_BLOCKER_CODES and len(pairs) == 1:
            message, remediation = next(iter(pairs))
            append_issue_field(lines, "Message", message)
            append_issue_field(lines, "Action", remediation)
        else:
            append_issue_field(
                lines,
                "Message",
                "No registered explanation is available in this renderer.",
            )
            append_issue_field(
                lines,
                "Action",
                f"Open {unbind_markdown} for this code's recorded message and remediation.",
            )

    decision_noun = "decision" if applied_count == 1 else "decisions"
    append_paragraph(
        lines,
        "Nothing was lost by stopping here. Every artifact recorded for this "
        f"run remains on disk, SHA-256 verified; the promoted copy holds "
        f"{applied_count} accepted {decision_noun}.",
    )
    append_paragraph(
        lines,
        "Route evidence is not instance evidence: a verified build route does "
        "not make an extracted question build-approved.",
    )

    local_valid = validation_errors == 0 and equivalence_breaks == 0
    proof_rung = "Validated locally" if local_valid else "Drafted"
    lines.extend(
        [
            "",
            "COMPOSE",
            f"readiness: {compose_readiness}",
            f"questions selected: {compose_count}",
            f"reachable draws: {draw_count}",
            f"selected assets: {asset_count}",
        ]
    )
    append_path(lines, "report", compose_markdown)
    append_path(lines, "record", compose_json, note=" (SHA-256 verified)")
    lines.extend(
        [
            f"package: {package_zip}",
            f"run receipt: {run_receipt_relative}",
            f"settings receipt: {settings_receipt_relative}",
            "",
            "SEAL",
            f"strict validation errors: {validation_errors}",
            f"validation note: {validation_relative}",
            f"local folder/ZIP equivalence breaks: {equivalence_breaks}",
        ]
    )
    append_path(lines, "local-equivalence report", diff_markdown)
    append_paragraph(
        lines,
        "local-equivalence meaning: folder-vs-ZIP comparison, local only - "
        "no Brightspace contact",
    )
    lines.extend(
        [
            "",
            f"PROOF LADDER: {package_zip}",
            "[x] Drafted - package and run receipt exist",
            (
                "[x] Validated locally - package validation and folder/ZIP "
                "equivalence passed"
                if local_valid
                else "[ ] Validated locally - local checks did not both pass"
            ),
            "[ ] Import verified - not recorded for this run",
            f"[ ] Round-trip verified - Brightspace round trip: "
            f"{brightspace_roundtrip}",
            f"run proof: {proof_rung} - earned by this run's receipts alone",
        ]
    )
    append_paragraph(
        lines,
        "Registered capability evidence describes the tool, not this package; "
        "it does not raise this run's proof rung.",
    )
    lines.extend(
        [
            "",
            "RUN SUMMARY",
            f"artifact integrity: {len(artifacts)}/{len(artifacts)} SHA-256 "
            "values verified",
            f"network operations: {network_operations}",
            f"Brightspace operations: {brightspace_operations}",
            f"Brightspace round trip: {brightspace_roundtrip}",
            f"receipt: {RECEIPT_NAME}",
        ]
    )
    append_paragraph(
        lines,
        "The receipt records counts, states, paths, and SHA-256 values. It "
        "never records question or answer text.",
    )
    lines.append(
        f"RUN RECORD CLOSED - {total} entries, {len(artifacts)} artifacts, "
        f"{network_operations} network operations"
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Completed synthetic journey directory containing journey.receipt.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        output = render_completed_run(args.run_dir)
    except (OSError, ValueError, LogRenderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
