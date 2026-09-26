#!/usr/bin/env python3
"""Validate or assemble a deterministic Brightspace Quiz Bundle archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


REPO_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPO_ROOT / "upstream" / "workbench_pin.json"
VERSION_PATH = REPO_ROOT / "VERSION"
# 1980-01-02T00:00:00Z: deterministic and safely inside the ZIP timestamp
# range even when the extracted filesystem reports local time west of UTC.
RELEASE_MTIME = 315619200
STATIC_FILES = (
    "README.md",
    "CHANGELOG.md",
    "INSTALL.md",
    "LICENSE",
    "LICENSE_POSTURE.md",
    "COMMERCIAL.md",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "requirements-lock.txt",
    "VERSION",
    "docs/REPOSITORY_BOUNDARY.md",
    "docs/SYNTHETIC_JOURNEY.md",
    "docs/SANDBOX_VERIFICATION_2026-09-26.md",
    "docs/TERMINAL_WORKFLOW.md",
    "docs/UNBIND_TERMINAL.md",
    "docs/RUN_SCRIPTS.md",
    "docs/project/quiz-consolidation/ASSESSMENT_REVIEW_PACKETS.md",
    "docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md",
    "docs/project/quiz-consolidation/QUIZ_PROJECTION_FIDELITY_GATES_2026-09-16.md",
    "docs/reference/quiz_progress_draft.schema.json",
    "sbom/runtime.cdx.json",
    "upstream/workbench_pin.json",
)
BUNDLE_RUNTIME_FILES = (
    "scripts/bootstrap_env.py",
    "scripts/make_release_asset.py",
    "scripts/quiz_binder_journey_plan.py",
    "scripts/quiz_binder_progress.py",
    "scripts/quiz_binder_tui.py",
    "scripts/quiz_binder_tui_frames.py",
    "scripts/quiz_binder_tui_session.py",
    "scripts/quiz_binder_tui_shelf.py",
    "scripts/quiz_binder_tui_terminal.py",
    "scripts/quiz_binder_unbind_terminal.py",
    "scripts/quiz_binder_tui_view.py",
    "scripts/quiz_binder_wizard.py",
    "scripts/quiz_terminal.py",
    "scripts/render_quiz_binder_log.py",
    "scripts/run_synthetic_journey.py",
    "scripts/vendor_from_workbench.py",
)
# The review distribution includes its sanitized tests and pinned specimens.
# Selection remains explicit through the reviewed Workbench pin.
PUBLIC_FIXTURE_PREFIXES = (
    "tests/",
    "workspace/review/quiz_capability_lab_r1/fixtures/specimens/",
)
PUBLIC_TEST_FILES = (
    "tests/conftest.py",
    "tests/README.md",
    "tests/test_quiz_terminal_roundtrip.py",
    "tests/test_public_distribution.py",
)
# These checked-in examples are synthetic fresh-intake templates, not course
# banks. The optional Node template generator remains in Workbench.
PUBLIC_DRAFT_TEMPLATE_PREFIX = "workspace/reference/examples/quiz_draft/"


class ReleaseError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_repo_file(relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ReleaseError(f"unsafe release path: {relative}")
    path = (REPO_ROOT / Path(*pure.parts)).resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ReleaseError(f"release path escapes repository: {relative}") from exc
    if not path.is_file():
        raise ReleaseError(f"release file missing: {relative}")
    return path


def load_pin() -> dict:
    try:
        return json.loads(PIN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read vendor pin: {exc}") from exc


def release_files(pin: dict) -> list[str]:
    promoted = []
    for entry in pin.get("files", []):
        target = entry.get("target", "")
        if (
            target.startswith("scripts/")
            or target.startswith("workspace/reference/schemas/quiz/")
            or target
            == "workspace/reference/schemas/course/component_package_receipt_schema.json"
            or target.startswith(PUBLIC_FIXTURE_PREFIXES)
            or target.startswith(PUBLIC_DRAFT_TEMPLATE_PREFIX)
        ):
            promoted.append(target)
    files = sorted(
        set((*STATIC_FILES, *BUNDLE_RUNTIME_FILES, *PUBLIC_TEST_FILES, *promoted))
    )
    for relative in files:
        safe_repo_file(relative)
    return files


def release_pin(pin: dict, files: list[str]) -> dict:
    """Return the exact upstream subset present in the public archive."""
    included = set(files)
    result = dict(pin)
    result["files"] = [
        dict(entry)
        for entry in pin.get("files", [])
        if entry.get("target") in included
    ]
    result["distribution_scope"] = "public_review_with_tests"
    return result


def release_bytes(relative: str, pin: dict, files: list[str]) -> bytes:
    if relative == "upstream/workbench_pin.json":
        return (
            json.dumps(release_pin(pin, files), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    return safe_repo_file(relative).read_bytes()


def verify_pin(pin: dict) -> None:
    errors = []
    for entry in pin.get("files", []):
        target = safe_repo_file(entry["target"])
        if sha256(target.read_bytes()) != entry.get("sha256"):
            errors.append(entry["target"])
    if errors:
        raise ReleaseError("vendored file drift: " + ", ".join(errors))


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise ReleaseError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def require_release_ref(ref: str) -> str:
    if git("status", "--porcelain"):
        raise ReleaseError("release build requires a clean worktree")
    commit = git("rev-parse", f"{ref}^{{commit}}")
    head = git("rev-parse", "HEAD")
    if commit != head:
        raise ReleaseError("release ref must resolve to the checked-out HEAD")
    return commit


def release_manifest(version: str, commit: str, pin: dict, files: list[str]) -> bytes:
    record = {
        "schema": "coursecraft.quiz_bundle_release/1",
        "name": "brightspace-quiz-bundle",
        "version": version,
        "release_commit": commit,
        "workbench_commit": pin["source_commit"],
        "files": [
            {
                "path": relative,
                "sha256": sha256(release_bytes(relative, pin, files)),
            }
            for relative in files
        ],
    }
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = RELEASE_MTIME
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def build_archive(
    output: Path,
    root_name: str,
    manifest: bytes,
    files: list[str],
    pin: dict | None = None,
) -> None:
    pin = pin or load_pin()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative in files:
                    add_bytes(
                        archive,
                        f"{root_name}/{relative}",
                        release_bytes(relative, pin, files),
                    )
                add_bytes(archive, f"{root_name}/RELEASE_MANIFEST.json", manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="validate release inputs without writing an archive")
    parser.add_argument("--ref", help="explicit clean git ref to package")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pin = load_pin()
    verify_pin(pin)
    files = release_files(pin)
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    if not version:
        raise ReleaseError("VERSION is empty")
    if args.check_only:
        print(
            f"release inputs OK: {len(files)} files, version {version}, "
            f"Workbench {pin['source_commit'][:12]}"
        )
        return 0
    if not args.ref:
        raise ReleaseError("archive creation requires an explicit --ref")
    commit = require_release_ref(args.ref)
    root_name = f"brightspace-quiz-bundle-{version}"
    manifest = release_manifest(version, commit, pin, files)
    output = args.output_dir / f"{root_name}.tar.gz"
    build_archive(output, root_name, manifest, files, pin)
    print(f"wrote {output} ({sha256(output.read_bytes())})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ReleaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
