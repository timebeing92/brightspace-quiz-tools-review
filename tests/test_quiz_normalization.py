from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

from extract_quiz_pool_review import build_payload
from quiz_contracts import validate_contract
import quiz_normalization
from quiz_normalization import (
    build_normalized_model,
    copy_resolved_assets,
    enrich_payload,
    resolve_export,
)


def run_extractor(source: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/extract_quiz_pool_review.py",
            str(source),
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_all_edge_fixtures_normalize_without_contract_errors() -> None:
    fixtures = sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_dir())
    assert len(fixtures) == 14
    for fixture in fixtures:
        payload = build_payload(fixture)
        enrich_payload(payload, fixture)
        model, _source_meta = build_normalized_model(
            payload,
            fixture,
            fixture,
            "directory",
            source_lineage_key=f"cc:lineage:fixture:{fixture.name}",
            run_id=f"cc:run:fixture:{fixture.name}",
        )
        assert validate_contract(model, mode="transform") == [], fixture.name
        # Question-library records are additional coverage entities with no
        # placements; only placed questions are bounded by the quiz row count.
        placed_questions = [
            question
            for question in model["questions"]
            if not (question.get("extensions") or {}).get("library_only")
        ]
        assert len(placed_questions) <= payload["summary"]["quiz_question_count"]
        # Placed questions and question-library records are extracted by the
        # same parser but must stay countable apart, so each population is
        # asserted against its own producer row count.
        placed_raw_models = [
            raw_model
            for question in placed_questions
            for raw_model in question["type_payload"]["raw_response_models"]
        ]
        library_raw_models = [
            raw_model
            for question in model["questions"]
            if (question.get("extensions") or {}).get("library_only")
            for raw_model in question["type_payload"]["raw_response_models"]
        ]
        assert (
            sum(
                raw_model["source_kind"].startswith("canonical-extractor-row:")
                for raw_model in placed_raw_models
            )
            == payload["summary"]["quiz_question_count"]
        )
        assert (
            sum(
                raw_model["source_kind"] == "d2l_qti_response_facts/0"
                for raw_model in placed_raw_models
            )
            == payload["summary"]["quiz_question_count"]
        )
        # A library record never carries the quiz-question marker, or occurrence
        # joins would count a dormant record as a placement.
        assert not any(
            raw_model["source_kind"].startswith("canonical-extractor-row:")
            for raw_model in library_raw_models
        )
        library_entity_count = sum(
            1
            for question in model["questions"]
            if (question.get("extensions") or {}).get("library_only")
        )
        assert (
            sum(
                raw_model["source_kind"].startswith("question-library-row:")
                for raw_model in library_raw_models
            )
            == library_entity_count
        )
        assert model["extensions"]["legacy_summary"] == payload["summary"]


def test_identity_collisions_preserve_sections_assets_and_occurrences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = FIXTURE_ROOT / "identity_collisions"
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    original_sha256_file = quiz_normalization.sha256_file
    sha256_calls: dict[Path, int] = {}

    def counted_sha256_file(path: Path) -> str:
        resolved = path.resolve()
        sha256_calls[resolved] = sha256_calls.get(resolved, 0) + 1
        return original_sha256_file(path)

    monkeypatch.setattr(quiz_normalization, "sha256_file", counted_sha256_file)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key="cc:lineage:fixture:identity-collisions",
        run_id="cc:run:fixture:identity-collisions",
    )

    assert validate_contract(model, mode="transform") == []
    assert sha256_calls[(fixture / "shared.svg").resolve()] == 1
    sections = [
        row for row in model["structures"] if row["source_kind"] == "section"
    ]
    assert len(sections) == 3
    assert len({row["entity_key"] for row in sections}) == 3
    assert {
        row["extensions"]["section_source_locator"] for row in sections
    } == {"section[1]", "section[2]", "section[3]"}
    assert all(
        any(
            alias["namespace"] == "brightspace.quiz_section_path"
            and alias["value"].endswith("::[SECTION]")
            for alias in row["identity"]["source_aliases"]
        )
        for row in sections
    )
    structure_conflicts = [
        row
        for row in model["diagnostics"]
        if row["code"] == "structure_alias_collision"
    ]
    assert len(structure_conflicts) == 1
    assert structure_conflicts[0]["status"] == "resolved"
    assert set(structure_conflicts[0]["entity_keys"]) == {
        row["entity_key"] for row in sections
    }

    question_keys = {row["entity_key"] for row in model["questions"]}
    placements = [
        row
        for row in model["relationships"]
        if row["kind"] == "contains" and row["to_entity_key"] in question_keys
    ]
    assert len(placements) == 3
    assert len({row["relationship_key"] for row in placements}) == 3
    assert {row["from_entity_key"] for row in placements} == {
        row["entity_key"] for row in sections
    }
    shared_question = next(
        row
        for row in model["questions"]
        if any(
            alias["namespace"] == "d2l.qmd_displayid"
            and alias["value"] == "COLLIDE_SHARED"
            for alias in row["identity"]["source_aliases"]
        )
    )
    assert (
        sum(
            raw["source_kind"].startswith("canonical-extractor-row:")
            for raw in shared_question["type_payload"]["raw_response_models"]
        )
        == 2
    )

    assert len(model["assets"]) == 1
    asset = model["assets"][0]
    assert asset["package_path"] == "shared.svg"
    assert set(asset["extensions"]["source_paths"]) == {
        "shared.svg",
        "/content/enforced/FIXTURE/shared.svg",
    }
    assert asset["fingerprint"] is not None
    uses_asset = [
        row for row in model["relationships"] if row["kind"] == "uses_asset"
    ]
    assert len(uses_asset) == 3
    assert len({row["relationship_key"] for row in uses_asset}) == 3
    assert len({tuple(row["source_evidence_keys"]) for row in uses_asset}) == 3
    assert all(
        row["source_evidence_keys"][0].startswith("ev.occurrence.")
        for row in uses_asset
    )


def test_materially_conflicting_asset_aliases_remain_distinct() -> None:
    fixture = FIXTURE_ROOT / "identity_collisions"
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    conflicting_row = dict(payload["question_image_rows"][0])
    conflicting_row.update(
        {
            "image_file_path": "variant.svg",
            "image_file_absolute_path": str((fixture / "variant.svg").resolve()),
            "image_resolution_basis": "synthetic_conflicting_resolution",
        }
    )
    payload["question_image_rows"].append(conflicting_row)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key="cc:lineage:fixture:asset-conflict",
        run_id="cc:run:fixture:asset-conflict",
    )

    assert validate_contract(model, mode="transform") == []
    assert len(model["assets"]) == 2
    assert len({row["entity_key"] for row in model["assets"]}) == 2
    assert len(
        {
            row["fingerprint"]["digest"]
            for row in model["assets"]
            if row["fingerprint"] is not None
        }
    ) == 2
    conflicts = [
        row
        for row in model["diagnostics"]
        if row["code"] == "asset_identity_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0]["details"]["alias_namespace"] == "brightspace.asset_ref"
    assert conflicts[0]["details"]["alias_value"] == "shared.svg"
    assert set(conflicts[0]["entity_keys"]) == {
        row["entity_key"] for row in model["assets"]
    }
    assert all(
        conflicts[0]["diagnostic_id"] in row["diagnostic_ids"]
        for row in model["assets"]
    )


def test_mixed_storage_preserves_itemrefs_root_bank_and_occurrences() -> None:
    fixture = FIXTURE_ROOT / "mixed_inline_itemref_and_root_bank"
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key="cc:lineage:fixture:mixed",
        run_id="cc:run:fixture:mixed",
    )

    assert len(model["questions"]) == 3
    assert len([row for row in model["relationships"] if row["kind"] == "itemref"]) == 2
    assert (
        len(
            [
                row
                for row in model["relationships"]
                if row["kind"] == "contains"
                and row["to_entity_key"]
                in {question["entity_key"] for question in model["questions"]}
            ]
        )
        == 1
    )
    assert {row["kind"] for row in model["structures"]} >= {"pool", "section", "draw"}
    assert any(
        question["extensions"]["storage"] == "questiondb"
        for question in model["questions"]
    )
    assert any(
        question["extensions"]["storage"] == "quiz-local"
        for question in model["questions"]
    )
    assert model["source"]["extensions"]["quiz_scope"] == {
        "coverage_kind": "question_library_with_itemrefs",
        "questiondb_present": True,
        "inline_occurrence_count": 1,
        "itemref_occurrence_count": 2,
        "resolved_itemref_count": 2,
        "unresolved_itemref_count": 0,
    }


def test_zip_and_folder_intake_keep_the_legacy_payload_equal(tmp_path: Path) -> None:
    fixture = FIXTURE_ROOT / "mixed_inline_itemref_and_root_bank"
    archive = tmp_path / f"{fixture.name}.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in sorted(fixture.rglob("*")):
            if path.is_file():
                handle.write(
                    path, f"{fixture.name}/{path.relative_to(fixture).as_posix()}"
                )

    folder_output = tmp_path / "folder"
    zip_output = tmp_path / "zip"
    folder_result = run_extractor(fixture, folder_output)
    zip_result = run_extractor(archive, zip_output)
    assert folder_result.returncode == 0, folder_result.stderr
    assert zip_result.returncode == 0, zip_result.stderr

    name = f"{fixture.name}__quiz_pool_review"
    folder_payload = json.loads(
        (folder_output / f"{name}.json").read_text(encoding="utf-8")
    )
    zip_payload = json.loads((zip_output / f"{name}.json").read_text(encoding="utf-8"))
    assert zip_payload == folder_payload
    model = json.loads((zip_output / f"{name}.model.json").read_text(encoding="utf-8"))
    receipt = json.loads((zip_output / f"{name}.run.json").read_text(encoding="utf-8"))
    assert model["source"]["extensions"]["intake_kind"] == "zip"
    assert receipt["inputs"][0]["media_type"] == "application/zip"
    assert validate_contract(model, mode="transform") == []
    assert validate_contract(receipt, mode="transform") == []


def test_quiz_only_export_without_questiondb_is_supported(tmp_path: Path) -> None:
    export = tmp_path / "quiz_only"
    export.mkdir()
    source_quiz = (
        FIXTURE_ROOT / "mixed_inline_itemref_and_root_bank" / "quiz_d2l_mixed.xml"
    )
    (export / source_quiz.name).write_bytes(source_quiz.read_bytes())

    payload = build_payload(export)
    assert payload["summary"]["quiz_count"] == 1
    assert payload["summary"]["pool_count"] == 0
    assert payload["summary"]["quiz_question_count"] == 3
    assert payload["summary"]["unmatched_count"] == 3
    enrich_payload(payload, export)
    model, _source_meta = build_normalized_model(
        payload,
        export,
        export,
        "directory",
        source_lineage_key="cc:lineage:fixture:quiz-only",
        run_id="cc:run:fixture:quiz-only",
    )
    assert validate_contract(model, mode="transform") == []
    unresolved = [row for row in model["relationships"] if row["kind"] == "itemref"]
    assert len(unresolved) == 2
    assert all(
        row["status"] == "incomplete" and row["to_entity_key"] is None
        for row in unresolved
    )
    assert model["source"]["extensions"]["quiz_scope"] == {
        "coverage_kind": "quiz_only_with_unresolved_itemrefs",
        "questiondb_present": False,
        "inline_occurrence_count": 1,
        "itemref_occurrence_count": 2,
        "resolved_itemref_count": 0,
        "unresolved_itemref_count": 2,
    }
    output_dir = tmp_path / "quiz_only_review"
    result = run_extractor(export, output_dir)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (output_dir / "quiz_only__quiz_pool_review.run.json").read_text(
            encoding="utf-8"
        )
    )
    library_capability = next(
        row
        for row in receipt["capabilities"]
        if row["name"] == "question_library_resolution"
    )
    assert library_capability["status"] == "skipped"
    assert "questiondb.xml was not present" in library_capability["notes"][0]
    assert library_capability["extensions"]["quiz_scope"] == model["source"][
        "extensions"
    ]["quiz_scope"]


def test_zip_traversal_and_symlink_style_entries_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../outside.xml", "<unsafe />")
        handle.writestr("quiz_d2l_safe.xml", "<questestinterop />")
    with pytest.raises(ValueError, match="unsafe path or symlink"):
        with resolve_export(archive):
            pass


def test_feedback_settings_and_sensitive_values_are_preserved_safely(
    tmp_path: Path,
) -> None:
    export = tmp_path / "feedback_settings"
    export.mkdir()
    (export / "images").mkdir()
    (export / "images" / "alpha.png").write_bytes(b"synthetic-png-fixture")
    (export / "quiz_d2l_feedback.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="QUIZ_FEEDBACK" title="Feedback and settings">
    <assess_procextension>
      <d2l_2p0:attempts_allowed>3</d2l_2p0:attempts_allowed>
      <d2l_2p0:time_limit>45</d2l_2p0:time_limit>
      <d2l_2p0:password>do-not-emit</d2l_2p0:password>
    </assess_procextension>
    <section ident="S1" title="Questions">
      <item ident="I1" label="Q1" title="Feedback question">
        <itemmetadata><qtimetadata>
          <qti_metadatafield><fieldlabel>qmd_questiontype</fieldlabel><fieldentry>Multiple Choice</fieldentry></qti_metadatafield>
          <qti_metadatafield><fieldlabel>qmd_weighting</fieldlabel><fieldentry>1</fieldentry></qti_metadatafield>
        </qtimetadata></itemmetadata>
        <presentation><material><mattext texttype="text/html">Choose alpha. &lt;img src=&quot;images/alpha.png&quot; alt=&quot;alpha&quot; /&gt;</mattext></material>
          <response_lid ident="R1"><render_choice>
            <response_label ident="A"><material><mattext>Alpha</mattext></material></response_label>
            <response_label ident="B"><material><mattext>Beta</mattext></material></response_label>
          </render_choice></response_lid>
        </presentation>
        <resprocessing><respcondition><conditionvar><varequal respident="R1">A</varequal></conditionvar>
          <setvar varname="D2L_Correct">1</setvar><displayfeedback linkrefid="CORRECT" />
        </respcondition></resprocessing>
        <itemfeedback ident="CORRECT"><material><mattext texttype="text/html">Correct: alpha is keyed.</mattext></material></itemfeedback>
      </item>
    </section>
  </assessment>
</questestinterop>
""",
        encoding="utf-8",
    )

    payload = build_payload(export)
    enrich_payload(payload, export)
    assert payload["quiz_summary_rows"][0]["attempts_allowed"] == 3
    assert payload["quiz_summary_rows"][0]["time_limit_minutes"] == 45
    assert (
        payload["quiz_question_rows"][0]["correct_feedback"]
        == "Correct: alpha is keyed."
    )
    assert payload["summary"]["resolved_image_reference_count"] == 1
    review_dir = tmp_path / "review"
    assert copy_resolved_assets(payload, export, review_dir, "assets") == 1
    assert (
        review_dir / "assets" / "images" / "alpha.png"
    ).read_bytes() == b"synthetic-png-fixture"
    model, _source_meta = build_normalized_model(
        payload,
        export,
        export,
        "directory",
        source_lineage_key="cc:lineage:fixture:feedback",
        run_id="cc:run:fixture:feedback",
    )
    assert validate_contract(model, mode="transform") == []
    assert model["questions"][0]["feedback"][0]["channel"] == "correct"
    password = next(
        row for row in model["settings_observations"] if row["name"] == "password"
    )
    assert password["raw_value"] == "[redacted]"
    assert password["normalized_value"] == "[redacted]"
    assert password["extensions"]["sensitive"] is True
    assert "do-not-emit" not in json.dumps(model)
    assert (
        model["assets"][0]["extensions"]["review_copy_path"]
        == "assets/images/alpha.png"
    )


def test_copy_resolved_assets_includes_library_only_images(tmp_path: Path) -> None:
    export = tmp_path / "library_assets"
    image_path = export / "library" / "unused.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"synthetic-library-only-image")
    output = tmp_path / "review"
    payload = {
        "question_image_rows": [],
        "quiz_question_rows": [],
        "library_question_rows": [
            {
                "question_image_paths": "library/unused.png",
                "question_primary_image_path": "library/unused.png",
                "question_primary_image_absolute_path": str(image_path),
            }
        ],
        "unresolved_rows": [
            {
                "question_image_paths": "library/unused.png",
                "question_primary_image_path": "library/unused.png",
                "question_primary_image_absolute_path": str(image_path),
            }
        ],
    }

    assert copy_resolved_assets(payload, export, output, "assets") == 1
    copied = output / "assets" / "library" / "unused.png"
    assert copied.read_bytes() == b"synthetic-library-only-image"
    library_row = payload["library_question_rows"][0]
    assert library_row["question_primary_image_absolute_path"] == str(copied)
    assert library_row["question_primary_image_review_copy_path"] == (
        "assets/library/unused.png"
    )
    assert library_row["question_image_review_copy_paths"] == (
        "assets/library/unused.png"
    )
    unresolved_row = payload["unresolved_rows"][0]
    assert unresolved_row["question_primary_image_review_copy_path"] == (
        "assets/library/unused.png"
    )
    assert unresolved_row["question_image_review_copy_paths"] == (
        "assets/library/unused.png"
    )


def test_unresolved_library_matches_expose_candidate_evidence() -> None:
    fixture = FIXTURE_ROOT / "duplicate_pool_and_similarity"
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key="cc:lineage:fixture:duplicates",
        run_id="cc:run:fixture:duplicates",
    )
    proposals = [
        row
        for row in model["relationships"]
        if row["kind"] == "member_of" and row["status"] == "proposed"
    ]
    assert proposals
    assert any(row["attributes"]["candidates"] for row in proposals)
    candidate = next(
        row["attributes"]["candidates"][0]
        for row in proposals
        if row["attributes"]["candidates"]
    )
    assert {"basis", "score", "pool_title", "question_ident"} <= candidate.keys()
