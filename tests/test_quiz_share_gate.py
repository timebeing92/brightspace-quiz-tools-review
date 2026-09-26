from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/quiz_share_gate.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def standard_policy_args() -> tuple[str, ...]:
    return (
        "--person",
        "Jane Example",
        "--lineage-policy",
        "deny",
        "--lineage-id",
        "cc:lineage:private-course",
    )


def test_share_gate_passes_clean_supported_artifacts(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "README.md").write_text("Synthetic review packet.\n", encoding="utf-8")
    (packet / ".gitignore").write_text("local-output/\n", encoding="utf-8")
    (packet / "diagram.png").write_bytes(b"synthetic-image-placeholder")

    result = run_gate(str(packet), *standard_policy_args())

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["summary"]["finding_count"] == 0
    assert payload["summary"]["unscanned_artifact_count"] == 0
    assert payload["summary"]["image_filename_scan_count"] == 1
    assert payload["summary"]["image_embedded_text_scan_count"] == 1


def test_share_gate_catches_seeded_text_and_json_leaks_without_echoing_values(tmp_path: Path) -> None:
    packet = tmp_path / "Jane Example packet"
    packet.mkdir()
    (packet / "README.md").write_text(
        "Owner: Jane Example\nSource: /Users/jane/Documents/course/export.zip\n"
        "Lineage: cc:lineage:private-course\n",
        encoding="utf-8",
    )
    (packet / "review.json").write_text(
        json.dumps({"image_file_absolute_path": "/Users/jane/Documents/course/image.png"}),
        encoding="utf-8",
    )
    (packet / "portrait.jpg").write_bytes(
        b"\xff\xd8embedded metadata: Jane Example /Users/jane/Documents/portrait.jpg\xff\xd9"
    )

    result = run_gate(str(packet), *standard_policy_args())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    rules = {row["rule"] for row in payload["findings"]}
    assert {
        "absolute_path_field",
        "configured_person_name",
        "disallowed_lineage_identifier",
        "local_absolute_path",
    } <= rules
    assert "Jane Example" not in result.stdout
    assert "/Users/jane" not in result.stdout
    assert "cc:lineage:private-course" not in result.stdout
    assert any(
        row["location"] == "embedded image text (utf-8)"
        for row in payload["findings"]
    )


def test_share_gate_scans_xlsx_cells_hyperlinks_and_properties(tmp_path: Path) -> None:
    workbook_path = tmp_path / "review.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quiz Questions"
    sheet.append(["image_file_absolute_path", "reviewer"])
    sheet.append(["/Users/jane/Documents/course/image.png", "Jane Example"])
    sheet["A2"].hyperlink = "file:///Users/jane/Documents/course/image.png"
    workbook.properties.creator = "Jane Example"
    workbook.save(workbook_path)

    result = run_gate(str(workbook_path), *standard_policy_args())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    locations = {row["location"] for row in payload["findings"]}
    assert "Quiz Questions!A1" in locations
    assert "Quiz Questions!A2" in locations
    assert "Quiz Questions!A2 (hyperlink)" in locations
    assert "workbook.properties.creator" in locations


def test_share_gate_requires_explicit_name_and_lineage_posture(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text("Synthetic.\n", encoding="utf-8")

    no_people = run_gate(str(target), "--lineage-policy", "allow")
    assert no_people.returncode == 2
    assert json.loads(no_people.stdout)["status"] == "configuration_error"

    no_lineage_terms = run_gate(
        str(target),
        "--person",
        "Jane Example",
        "--lineage-policy",
        "deny",
    )
    assert no_lineage_terms.returncode == 2
    assert json.loads(no_lineage_terms.stdout)["status"] == "configuration_error"


def test_share_gate_fails_closed_on_unscanned_types_without_override(tmp_path: Path) -> None:
    target = tmp_path / "packet.pdf"
    target.write_bytes(b"not-a-real-pdf")

    blocked = run_gate(str(target), *standard_policy_args())
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["summary"]["unscanned_artifact_count"] == 1

    allowed = run_gate(str(target), *standard_policy_args(), "--allow-unscanned")
    assert allowed.returncode == 0
    assert json.loads(allowed.stdout)["policy"]["unscanned_artifacts"] == "allow"
