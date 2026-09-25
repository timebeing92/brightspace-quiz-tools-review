#!/usr/bin/env python3
"""Promote and verify the byte-pinned Quiz Binder surface from Workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPO_ROOT / "upstream" / "workbench_pin.json"
PIN_SCHEMA = "coursecraft.workbench_vendor_pin/1"
SOURCE_REMOTE = "https://github.com/timebeing92/coursecraft-workbench.git"

SOURCE_FILES = (
    "docs/project/quiz-consolidation/ASSESSMENT_REVIEW_PACKETS.md",
    "docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md",
    "docs/project/quiz-consolidation/QUIZ_PROJECTION_FIDELITY_GATES_2026-09-16.md",
    "scripts/build_quiz_package_from_workbook.py",
    "scripts/check_package_conformance.py",
    "scripts/check_quiz_authoring_readiness.py",
    "scripts/common_xml.py",
    "scripts/course_artifact_contracts.py",
    "scripts/creatorplus_package.py",
    "scripts/d2l_package_lib.py",
    "scripts/diff_packages.py",
    "scripts/extract_quiz_pool_review.py",
    "scripts/local_evidence_output.py",
    "scripts/quiz_authoring_readiness.py",
    "scripts/quiz_assessment_review.py",
    "scripts/quiz_binder_station.py",
    "scripts/quiz_binder_strings.py",
    "scripts/quiz_build_support.py",
    "scripts/quiz_contracts.py",
    "scripts/quiz_draft_intake.py",
    "scripts/quiz_normalization.py",
    "scripts/quiz_phase5_authorization.py",
    "scripts/quiz_promote_revisions.py",
    "scripts/quiz_question_types.py",
    "scripts/quiz_review_projection.py",
    "scripts/quiz_review_text.py",
    "scripts/quiz_review_queues.py",
    "scripts/quiz_review_station.py",
    "scripts/quiz_review_workbook_reingest.py",
    "scripts/quiz_share_gate.py",
    "scripts/quiz_unbind.py",
    "scripts/quiz_unbind_receipt.py",
    "scripts/quiz_variant_control_matrix.py",
    "scripts/validate_quiz_contract.py",
    "scripts/validate_quiz_package.py",
    "scripts/verify_quiz_unbind_run.py",
    "tests/test_diff_packages.py",
    "tests/test_extract_quiz_pool_review.py",
    "tests/test_local_evidence_output.py",
    "tests/test_quiz_authoring_readiness.py",
    "tests/test_quiz_assessment_review.py",
    "tests/test_quiz_binder_station.py",
    "tests/test_quiz_contracts.py",
    "tests/test_quiz_draft_intake.py",
    "tests/test_quiz_normalization.py",
    "tests/test_quiz_package_builder_validator.py",
    "tests/test_quiz_phase5_authorization.py",
    "tests/test_quiz_projection_fidelity.py",
    "tests/test_quiz_promotion.py",
    "tests/test_quiz_review_projection.py",
    "tests/test_quiz_review_queues.py",
    "tests/test_quiz_review_station.py",
    "tests/test_quiz_review_workbook_reingest.py",
    "tests/test_quiz_share_gate.py",
    "tests/test_quiz_short_answer_consumers.py",
    "tests/test_quiz_short_answer_projection.py",
    "tests/test_quiz_source_choice_fidelity.py",
    "tests/test_quiz_source_response_facts.py",
    "tests/test_quiz_unbind.py",
    "tests/test_quiz_variant_control_matrix.py",
    "tests/test_quiz_variant_identity.py",
    "tests/test_quiz_xml_edge_fixtures.py",
    "workspace/reference/schemas/course/component_package_receipt_schema.json",
)

SOURCE_TREES = (
    "tests/fixtures/quiz_authoring",
    "tests/fixtures/quiz_binder",
    "tests/fixtures/quiz_source_choice_fidelity",
    "tests/fixtures/quiz_xml",
    "workspace/review/quiz_capability_lab_r1/fixtures/specimens",
    "workspace/reference/schemas/quiz",
    "workspace/reference/examples/quiz_draft",
)


class PromotionError(RuntimeError):
    pass


def git(workbench: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(workbench), *args],
        capture_output=True,
        check=False,
        text=not binary,
    )
    if result.returncode:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise PromotionError(stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_target(relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise PromotionError(f"unsafe target path: {relative}")
    target = (REPO_ROOT / Path(*pure.parts)).resolve()
    try:
        target.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise PromotionError(f"target escapes repository: {relative}") from exc
    return target


def selected_sources(workbench: Path, commit: str) -> list[str]:
    selected = set(SOURCE_FILES)
    for tree in SOURCE_TREES:
        output = git(workbench, "ls-tree", "-r", "--name-only", commit, "--", tree)
        assert isinstance(output, str)
        selected.update(line for line in output.splitlines() if line)
    missing = []
    for source in sorted(selected):
        result = subprocess.run(
            ["git", "-C", str(workbench), "cat-file", "-e", f"{commit}:{source}"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            missing.append(source)
    if missing:
        raise PromotionError("selected source paths missing at ref: " + ", ".join(missing))
    return sorted(selected)


def source_bytes(workbench: Path, commit: str, source: str) -> bytes:
    data = git(workbench, "show", f"{commit}:{source}", binary=True)
    assert isinstance(data, bytes)
    return data


def read_pin() -> dict:
    if not PIN_PATH.exists():
        raise PromotionError(f"pin not found: {PIN_PATH.relative_to(REPO_ROOT)}")
    record = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    if record.get("schema") != PIN_SCHEMA:
        raise PromotionError(f"unsupported pin schema: {record.get('schema')!r}")
    return record


def check_targets(pin: dict) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for entry in pin.get("files", []):
        target_name = entry.get("target")
        if not isinstance(target_name, str) or target_name in seen:
            errors.append(f"invalid or duplicate target: {target_name!r}")
            continue
        seen.add(target_name)
        target = safe_target(target_name)
        if not target.is_file():
            errors.append(f"missing target: {target_name}")
            continue
        actual = sha256(target.read_bytes())
        if actual != entry.get("sha256"):
            errors.append(f"target drift: {target_name}")
    return errors


def compare_source(workbench: Path, ref: str, pin: dict) -> list[str]:
    commit_value = git(workbench, "rev-parse", f"{ref}^{{commit}}")
    assert isinstance(commit_value, str)
    commit = commit_value.strip()
    errors: list[str] = []
    selected = selected_sources(workbench, commit)
    pinned_sources = [entry["source"] for entry in pin.get("files", [])]
    if selected != pinned_sources:
        errors.append("selected source inventory differs from the pin")
    pinned_by_source = {entry["source"]: entry for entry in pin.get("files", [])}
    for source in selected:
        expected = pinned_by_source.get(source, {}).get("sha256")
        actual = sha256(source_bytes(workbench, commit, source))
        if expected != actual:
            errors.append(f"upstream drift at {commit[:12]}: {source}")
    return errors


def update_pin(workbench: Path, ref: str) -> dict:
    commit_value = git(workbench, "rev-parse", f"{ref}^{{commit}}")
    assert isinstance(commit_value, str)
    commit = commit_value.strip()
    timestamp_value = git(workbench, "show", "-s", "--format=%cI", commit)
    assert isinstance(timestamp_value, str)
    branch_value = git(workbench, "branch", "--format=%(refname:short)", "--contains", commit)
    assert isinstance(branch_value, str)
    branches = branch_value.splitlines()

    files = []
    for source in selected_sources(workbench, commit):
        data = source_bytes(workbench, commit, source)
        target_name = source
        target = safe_target(target_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(
            {
                "source": source,
                "target": target_name,
                "sha256": sha256(data),
            }
        )

    pin = {
        "schema": PIN_SCHEMA,
        "source_repository": SOURCE_REMOTE,
        "source_branch": "main" if "main" in branches else (branches[0] if branches else None),
        "source_commit": commit,
        "source_committed_at": timestamp_value.strip(),
        "policy": "byte-identical mechanical promotion; behavior changes upstream first",
        "files": files,
    }
    PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIN_PATH.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    return pin


def emit_errors(errors: Iterable[str]) -> int:
    rows = list(errors)
    if not rows:
        return 0
    for row in rows:
        print(f"ERROR: {row}", file=sys.stderr)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbench",
        type=Path,
        help="path to a coursecraft_workbench checkout (required for promotion or source comparison)",
    )
    parser.add_argument("--ref", help="explicit reviewed Workbench ref for --update-pin")
    parser.add_argument(
        "--compare-ref",
        help="compare the selected source surface at this Workbench ref with the current pin",
    )
    parser.add_argument("--update-pin", action="store_true", help="materialize sources and replace the pin")
    parser.add_argument("--check", action="store_true", help="verify vendored targets (the default)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.update_pin:
        if not args.workbench or not args.ref:
            raise PromotionError("--update-pin requires --workbench and --ref")
        pin = update_pin(args.workbench.resolve(), args.ref)
        print(f"promoted {len(pin['files'])} files from {pin['source_commit']}")
    else:
        pin = read_pin()

    errors = check_targets(pin)
    if args.compare_ref:
        if not args.workbench:
            raise PromotionError("--compare-ref requires --workbench")
        errors.extend(compare_source(args.workbench.resolve(), args.compare_ref, pin))
    if errors:
        return emit_errors(errors)
    print(f"vendor pin OK: {len(pin['files'])} files at {pin['source_commit'][:12]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError, PromotionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
