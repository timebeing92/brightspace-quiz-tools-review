from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_authoring"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from quiz_authoring_readiness import (  # noqa: E402
    analyze_authoring_readiness,
    render_readiness_markdown,
)


def test_buildable_fixture_is_ready_without_exposing_question_content() -> None:
    report = analyze_authoring_readiness(
        FIXTURE_ROOT / "buildable_quiz.model.json",
        settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
    )

    assert report["ready"] is True
    assert report["summary"] == {
        "quiz_count": 1,
        "reachable_draw_count": 1,
        "selected_pool_count": 1,
        "selected_question_count": 4,
        "selected_asset_count": 1,
        "error_count": 0,
        "warning_count": 0,
    }
    assert report["question_capabilities"] == {"roundtrip_verified": 4}
    serialized = json.dumps(report)
    assert "Which option is correct" not in serialized
    assert "The connected option" not in serialized


def test_readiness_references_are_portable_and_location_independent(
    tmp_path: Path,
) -> None:
    model_name = "Mødél Quiz Ω with spaces.model.json"
    model_bytes = (FIXTURE_ROOT / "buildable_quiz.model.json").read_bytes()
    model_paths = []
    for directory_name in ("first temporary location", "second temporary location"):
        directory = tmp_path / directory_name
        directory.mkdir()
        model_path = directory / model_name
        model_path.write_bytes(model_bytes)
        model_paths.append(model_path)

    reports = [
        analyze_authoring_readiness(
            model_path,
            settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
            asset_root=FIXTURE_ROOT,
        )
        for model_path in model_paths
    ]

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["ready"] is True
    assert report["source_model"] == model_name
    assert not Path(report["source_model"]).is_absolute()
    assert report["source_model_sha256"] == hashlib.sha256(model_bytes).hexdigest()
    assert report["capability_registry"] == (
        "workspace/reference/schemas/quiz/quiz_build_capabilities.json"
    )
    assert not Path(report["capability_registry"]).is_absolute()
    registry_path = REPO_ROOT / report["capability_registry"]
    assert report["capability_registry_sha256"] == hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()

    serialized = json.dumps(report, ensure_ascii=False)
    markdown = render_readiness_markdown(report)
    for machine_prefix in (str(tmp_path), str(REPO_ROOT), str(Path.home())):
        assert machine_prefix not in serialized
        assert machine_prefix not in markdown
    windows_drive_prefix = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]")
    assert windows_drive_prefix.search(serialized) is None
    assert windows_drive_prefix.search(markdown) is None
    assert f"- Model: `{model_name}`" in markdown


def test_readiness_markdown_accepts_historical_absolute_references() -> None:
    report = analyze_authoring_readiness(
        FIXTURE_ROOT / "buildable_quiz.model.json",
        settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
    )
    historical_model = r"C:\Users\Legacy User\Quiz Ω.model.json"
    historical_registry = (
        "/Users/legacy/checkout/workspace/reference/schemas/quiz/"
        "quiz_build_capabilities.json"
    )
    report["source_model"] = historical_model
    report["capability_registry"] = historical_registry

    markdown = render_readiness_markdown(report)

    assert f"- Model: `{historical_model}`" in markdown
    assert report["capability_registry"] == historical_registry


def test_readiness_reports_multiple_builder_blockers_without_mutating_model(tmp_path: Path) -> None:
    model = json.loads((FIXTURE_ROOT / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    model["questions"][0]["build_support"]["level"] = "extraction_only"
    model["questions"][0]["prompt"]["content"] = ""
    model["relationships"][1]["status"] = "proposed"
    path = tmp_path / "blocked.model.json"
    path.write_text(json.dumps(model), encoding="utf-8")

    report = analyze_authoring_readiness(
        path,
        settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
        asset_root=FIXTURE_ROOT,
    )
    codes = {row["code"] for row in report["issues"]}

    assert report["ready"] is False
    assert "unresolved_structure_relationship" in codes
    assert "draw_pool_join_invalid" in codes
    assert path.read_text(encoding="utf-8") == json.dumps(model)


def test_readiness_separates_extraction_only_from_roundtrip_support(tmp_path: Path) -> None:
    model = json.loads((FIXTURE_ROOT / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    question = model["questions"][0]
    question["kind"] = "short_answer"
    question["source_kind"] = "Short Answer"
    question["build_support"]["level"] = "extraction_only"
    path = tmp_path / "extraction_only.model.json"
    path.write_text(json.dumps(model), encoding="utf-8")

    report = analyze_authoring_readiness(
        path,
        settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
        asset_root=FIXTURE_ROOT,
    )
    codes = {row["code"] for row in report["issues"]}

    assert "question_kind_not_buildable" in codes
    assert "question_not_approved_for_build" in codes
    assert report["question_capabilities"]["extraction_only"] == 1


def test_readiness_reports_builder_projection_constraints_on_reachable_question(tmp_path: Path) -> None:
    model = json.loads((FIXTURE_ROOT / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    question = model["questions"][0]
    question["prompt"]["content"] = ""
    question["scoring"]["maximum_points"] = 0
    question["type_payload"]["options"] = question["type_payload"]["options"][:1]
    path = tmp_path / "projection_blocked.model.json"
    path.write_text(json.dumps(model), encoding="utf-8")

    report = analyze_authoring_readiness(
        path,
        settings_path=FIXTURE_ROOT / "buildable_quiz.settings.json",
        asset_root=FIXTURE_ROOT,
    )
    codes = {row["code"] for row in report["issues"]}

    assert {"missing_question_prompt", "invalid_question_points", "unsupported_option_count"} <= codes
    assert report["ready"] is False


def test_readiness_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_quiz_authoring_readiness.py",
            str(FIXTURE_ROOT / "buildable_quiz.model.json"),
            "--settings",
            str(FIXTURE_ROOT / "buildable_quiz.settings.json"),
            "--output-dir",
            str(tmp_path),
            "--fail-if-not-ready",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ready: yes" in result.stdout
    json_path = tmp_path / "buildable_quiz.model__quiz_authoring_readiness.json"
    markdown_path = tmp_path / "buildable_quiz.model__quiz_authoring_readiness.md"
    assert json.loads(json_path.read_text(encoding="utf-8"))["ready"] is True
    assert "Ready for local generation: yes" in markdown_path.read_text(encoding="utf-8")
