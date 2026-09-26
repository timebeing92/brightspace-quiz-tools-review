from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from openpyxl import Workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORING_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quiz_authoring"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def write_source_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bank_1"
    ws.append(
        [
            "bank_id",
            "bank_title",
            "week",
            "question_code",
            "question_title",
            "target_question_type",
            "scoring_policy",
            "scoring_policy_value",
            "question_text",
            "points",
            "randomize_answers",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "option_e",
            "correct_option_key",
            "correct_option_text",
            "evaluator_answer_key",
            "associate_learning_objective",
            "notes",
        ]
    )
    ws.append(
        [
            "BANK_A",
            "Foundations Bank",
            "Week 1",
            "Q001",
            "Core concept",
            "MULTICHOICE",
            "",
            "",
            "Which option is correct?",
            1,
            "TRUE",
            "Correct",
            "Distractor",
            "",
            "",
            "",
            "A",
            "Correct",
            "",
            "LO1",
            "",
        ]
    )
    ws.append(
        [
            "BANK_A",
            "Foundations Bank",
            "Week 1",
            "Q002",
            "True false concept",
            "TRUEFALSE",
            "",
            "",
            "This statement is false.",
            1,
            "FALSE",
            "",
            "",
            "",
            "",
            "",
            "B",
            "False",
            "",
            "LO1",
            "",
        ]
    )
    ws.append(
        [
            "BANK_A",
            "Foundations Bank",
            "Week 1",
            "Q003",
            "Multi select concept",
            "MULTISELECT",
            "ALL_OR_NOTHING",
            "",
            "Select the correct options.",
            1,
            "TRUE",
            "Alpha",
            "Beta",
            "Gamma",
            "",
            "",
            "A;C",
            "Alpha; Gamma",
            "",
            "LO2",
            "",
        ]
    )
    ws.append(
        [
            "BANK_A",
            "Foundations Bank",
            "Week 1",
            "Q004",
            "Application prompt",
            "WRITTEN_RESPONSE",
            "",
            "",
            "Explain the practical implication.",
            2,
            "FALSE",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Look for a concrete implication and a relevant example.",
            "LO3",
            "",
        ]
    )

    structure = wb.create_sheet("Quiz_Structure")
    structure.append(
        [
            "section_order",
            "section_title",
            "source_bank_id",
            "available_questions",
            "recommended_draw_count",
            "points_per_question",
            "notes",
        ]
    )
    structure.append([1, "Random draw foundations", "BANK_A", 4, 2, 1, "Draw two from the bank."])
    wb.save(path)


def test_graded_placed_quiz_builds_validates_and_conforms(tmp_path: Path) -> None:
    """--grade-item --module wires a gradebook item + module placement; the package
    must still validate clean (quicklink URL not flagged) and stay conformant."""
    workbook = tmp_path / "source.xlsx"
    package_dir = tmp_path / "pkg"
    write_source_workbook(workbook)
    build = run_script(
        "scripts/build_quiz_package_from_workbook.py", str(workbook),
        "--output-dir", str(package_dir), "--grade-item", "--module", "Module One",
    )
    assert build.returncode == 0, build.stderr
    assert (package_dir / "grades_d2l.xml").exists()

    quiz_file = next(package_dir.glob("quiz_d2l_*.xml"))
    quiz_root = ET.parse(quiz_file).getroot()
    grade_items = [e for e in quiz_root.iter() if local_name(e.tag) == "grade_item"]
    assert grade_items and grade_items[0].attrib.get("resource_code"), "quiz must carry an assess_procextension grade_item"

    manifest_text = (package_dir / "imsmanifest.xml").read_text(encoding="utf-8")
    assert '"d2lgrades"' in manifest_text  # grades resource
    assert "organizations" in manifest_text  # module placement
    assert "type=quiz" in manifest_text  # quiz quicklink

    validation = run_script("scripts/validate_quiz_package.py", str(package_dir), "--workbook", str(workbook))
    assert validation.returncode == 0, validation.stdout + validation.stderr
    assert "- Errors: 0" in validation.stdout

    conformance = run_script("scripts/check_package_conformance.py", str(package_dir))
    assert "CONFORMANT" in conformance.stdout, conformance.stdout


def test_library_only_omits_quiz(tmp_path: Path) -> None:
    """--library-only emits the question library alone — no quiz file or quiz resource."""
    workbook = tmp_path / "source.xlsx"
    package_dir = tmp_path / "lib"
    write_source_workbook(workbook)
    build = run_script(
        "scripts/build_quiz_package_from_workbook.py", str(workbook),
        "--output-dir", str(package_dir), "--library-only",
    )
    assert build.returncode == 0, build.stderr
    assert (package_dir / "questiondb.xml").exists()
    assert not list(package_dir.glob("quiz_d2l_*.xml")), "library-only must not emit a quiz file"

    manifest_text = (package_dir / "imsmanifest.xml").read_text(encoding="utf-8")
    assert "d2lquestionlibrary" in manifest_text
    assert '"d2lquiz"' not in manifest_text

    conformance = run_script("scripts/check_package_conformance.py", str(package_dir))
    assert "CONFORMANT" in conformance.stdout, conformance.stdout


def _build_and_parse_questiondb(tmp_path: Path) -> ET.Element:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)
    build_result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
    )
    assert build_result.returncode == 0, build_result.stderr
    return ET.parse(package_dir / "questiondb.xml").getroot()


def _question_profiles(questiondb_root: ET.Element) -> dict:
    profiles: dict = {}
    for item in questiondb_root.iter():
        if local_name(item.tag) != "item":
            continue
        qtype = computerscored = None
        for field in item.iter():
            if local_name(field.tag) != "qti_metadatafield":
                continue
            label = entry = ""
            for child in field:
                if local_name(child.tag) == "fieldlabel":
                    label = (child.text or "").strip()
                elif local_name(child.tag) == "fieldentry":
                    entry = (child.text or "").strip()
            if label == "qmd_questiontype":
                qtype = entry
            elif label == "qmd_computerscored":
                computerscored = entry
        if not qtype:
            continue
        tags = {local_name(elem.tag) for elem in item.iter()}
        option_texts = [
            (mt.text or "")
            for rl in item.iter() if local_name(rl.tag) == "response_label"
            for mt in rl.iter() if local_name(mt.tag) == "mattext" and (mt.text or "").strip()
        ]
        option_texttypes = [
            mt.attrib.get("texttype", "")
            for rl in item.iter() if local_name(rl.tag) == "response_label"
            for mt in rl.iter() if local_name(mt.tag) == "mattext" and (mt.text or "").strip()
        ]
        profiles[qtype] = {
            "computerscored": computerscored,
            "has_decvar": "decvar" in tags,
            "has_displayfeedback": "displayfeedback" in tags,
            "has_itemfeedback": "itemfeedback" in tags,
            "option_texts": option_texts,
            "option_texttypes": option_texttypes,
        }
    return profiles


def test_quiz_question_structure_matches_canonical_d2l(tmp_path: Path) -> None:
    """Lock the QTI structure to what D2L itself emits (per the reference bundle).

    Guards the fidelity fixes: computerscored=yes for every type (incl. Long
    Answer); MC/TF carry displayfeedback + itemfeedback and no outcomes/decvar;
    multi-select keeps decvar; True/False option text is bare, not <p>-wrapped.
    """
    profiles = _question_profiles(_build_and_parse_questiondb(tmp_path))

    for qtype, profile in profiles.items():
        assert profile["computerscored"] == "yes", f"{qtype}: {profile['computerscored']}"

    mc, tf, ms, la = (profiles["Multiple Choice"], profiles["True/False"],
                      profiles["Multi-Select"], profiles["Long Answer"])

    for choice in (mc, tf):
        assert choice["has_displayfeedback"] and choice["has_itemfeedback"]
        assert not choice["has_decvar"]

    assert ms["has_decvar"] and not ms["has_displayfeedback"]
    assert not la["has_decvar"] and not la["has_displayfeedback"]

    assert tf["option_texts"] and all("<p>" not in t for t in tf["option_texts"]), tf["option_texts"]
    assert tf["option_texttypes"] == ["text/plain", "text/plain"]
    assert mc["option_texts"] and all(t.startswith("<p>") for t in mc["option_texts"]), mc["option_texts"]
    assert all(value == "text/html" for value in mc["option_texttypes"])


def test_truefalse_plaintext_override_is_explicit_and_scoped(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)
    build_result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
        "--true-false-answer-texttype",
        "text/plain",
    )
    assert build_result.returncode == 0, build_result.stderr

    root = ET.parse(package_dir / "questiondb.xml").getroot()
    answer_labels = [
        mattext
        for response_label in root.iter()
        if local_name(response_label.tag) == "response_label"
        for mattext in response_label.iter()
        if local_name(mattext.tag) == "mattext" and (mattext.text or "") in {"True", "False"}
    ]
    assert [(node.attrib.get("texttype"), node.text) for node in answer_labels] == [
        ("text/plain", "True"),
        ("text/plain", "False"),
    ]

    receipt = json.loads(
        (tmp_path / "package__receipts" / "quiz_build.run.json").read_text(encoding="utf-8")
    )
    assert receipt["extensions"]["coursecraft.true_false_answer_texttype"] == "text/plain"


def test_validator_rejects_tenant_proven_truefalse_html_label_regression(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)
    build_result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
        "--true-false-answer-texttype",
        "text/html",
    )
    assert build_result.returncode == 0, build_result.stderr

    validation = run_script("scripts/validate_quiz_package.py", str(package_dir))
    assert validation.returncode == 2
    assert "answer labels must use mattext texttype='text/plain'" in validation.stdout
    assert "broken localization labels" in validation.stdout


def test_quiz_feedback_links_resolve(tmp_path: Path) -> None:
    """Every MC/TF displayfeedback linkrefid must resolve to an itemfeedback ident."""
    root = _build_and_parse_questiondb(tmp_path)
    link_ids = {e.attrib.get("linkrefid") for e in root.iter() if local_name(e.tag) == "displayfeedback"}
    feedback_ids = {e.attrib.get("ident") for e in root.iter() if local_name(e.tag) == "itemfeedback"}
    assert link_ids, "expected displayfeedback links on MC/TF questions"
    assert link_ids <= feedback_ids, f"dangling feedback links: {link_ids - feedback_ids}"


def test_build_quiz_package_from_workbook_and_validate_zip(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    zip_path = tmp_path / "package.zip"
    validation_note = tmp_path / "validation.md"
    orgunitconfig = tmp_path / "orgunitconfig.xml"
    orgunitconfig.write_text('<?xml version="1.0" encoding="UTF-8"?><orgunitconfig />', encoding="utf-8")
    write_source_workbook(workbook)

    build_result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
        "--quiz-title",
        "Starter Quiz Package Smoke",
        "--orgunitconfig",
        str(orgunitconfig),
        "--zip-output",
        str(zip_path),
    )

    assert build_result.returncode == 0, build_result.stderr
    assert (package_dir / "imsmanifest.xml").exists()
    assert (package_dir / "questiondb.xml").exists()
    assert (package_dir / "quiz_d2l_starter_quiz_package_smoke.xml").exists()
    assert (package_dir / "orgunitconfig" / "orgunitconfig.xml").exists()
    assert zip_path.exists()

    questiondb_root = ET.parse(package_dir / "questiondb.xml").getroot()
    question_types = [
        field_entry.text
        for field_entry in questiondb_root.iter()
        if local_name(field_entry.tag) == "fieldentry" and field_entry.text in {"Multiple Choice", "True/False", "Multi-Select", "Long Answer"}
    ]
    assert sorted(question_types) == ["Long Answer", "Multi-Select", "Multiple Choice", "True/False"]

    quiz_root = ET.parse(package_dir / "quiz_d2l_starter_quiz_package_smoke.xml").getroot()
    itemrefs = [elem for elem in quiz_root.iter() if local_name(elem.tag) == "itemref"]
    assert len(itemrefs) == 4
    assert {itemref.attrib["linkrefid"] for itemref in itemrefs} == {"QUES_Q001", "QUES_Q002", "QUES_Q003", "QUES_Q004"}

    validate_result = run_script(
        "scripts/validate_quiz_package.py",
        str(package_dir),
        "--workbook",
        str(workbook),
        "--zip",
        str(zip_path),
        "--output",
        str(validation_note),
    )
    assert validate_result.returncode == 0, validate_result.stdout + validate_result.stderr
    assert "- Errors: 0" in validation_note.read_text(encoding="utf-8")

    review_dir = tmp_path / "review"
    review_result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(package_dir),
        "--output-dir",
        str(review_dir),
    )
    assert review_result.returncode == 0, review_result.stderr
    payload = json.loads((review_dir / "package__quiz_pool_review.json").read_text(encoding="utf-8"))
    assert payload["summary"]["quiz_question_count"] == 4
    assert payload["summary"]["source_evidence_count"] == 4


def test_validate_quiz_package_rejects_draw_count_overflow(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)

    build_result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
        "--quiz-title",
        "Overflow Check",
    )
    assert build_result.returncode == 0, build_result.stderr

    quiz_path = package_dir / "quiz_d2l_overflow_check.xml"
    tree = ET.parse(quiz_path)
    for field in tree.getroot().iter():
        if local_name(field.tag) != "qti_metadatafield":
            continue
        children = list(field)
        if len(children) >= 2 and clean_text(children[0]) == "qmd_numberofitems":
            children[1].text = "99"
            break
    tree.write(quiz_path, encoding="utf-8", xml_declaration=True)

    validate_result = run_script("scripts/validate_quiz_package.py", str(package_dir))
    assert validate_result.returncode == 2
    assert "draws 99 from 4 candidate questions" in validate_result.stdout


def clean_text(elem: ET.Element) -> str:
    return "" if elem.text is None else elem.text.strip()


def test_contract_authoring_model_builds_with_settings_assets_and_receipts(tmp_path: Path) -> None:
    model = AUTHORING_FIXTURE / "buildable_quiz.model.json"
    settings = AUTHORING_FIXTURE / "buildable_quiz.settings.json"
    package_dir = tmp_path / "contract_pkg"
    zip_path = tmp_path / "contract_pkg.zip"

    build = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(settings),
        "--output-dir",
        str(package_dir),
        "--zip-output",
        str(zip_path),
    )
    assert build.returncode == 0, build.stdout + build.stderr
    receipt_dir = tmp_path / "contract_pkg__receipts"
    run_receipt = receipt_dir / "quiz_build.run.json"
    settings_receipt = receipt_dir / "quiz_build.settings.json"
    assert run_receipt.is_file() and settings_receipt.is_file()
    assert (package_dir / "quiz-assets" / "concept-map.svg").is_file()

    questiondb = ET.parse(package_dir / "questiondb.xml").getroot()
    displayids = {
        (entry.text or "").strip()
        for field in questiondb.iter()
        if local_name(field.tag) == "qti_metadatafield"
        for label, entry in [(list(field)[0], list(field)[1])]
        if clean_text(label) == "qmd_displayid"
    }
    assert displayids == {"P4-Q001", "P4-Q002", "P4-Q003", "P4-Q004"}

    quiz_root = ET.parse(next(package_dir.glob("quiz_d2l_*.xml"))).getroot()
    settings_values = {
        local_name(elem.tag): clean_text(elem)
        for elem in quiz_root.iter()
        if local_name(elem.tag)
        in {"is_active", "attempts_allowed", "time_limit", "show_clock", "enforce_time_limit", "is_forward_only"}
    }
    assert settings_values == {
        "is_active": "no",
        "attempts_allowed": "2",
        "time_limit": "45",
        "show_clock": "yes",
        "enforce_time_limit": "yes",
        "is_forward_only": "yes",
    }

    validation = run_script(
        "scripts/validate_quiz_package.py",
        str(package_dir),
        "--model",
        str(model),
        "--settings",
        str(settings),
        "--run-receipt",
        str(run_receipt),
        "--zip",
        str(zip_path),
        "--strict-closure",
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    assert "Authoring model question identity coverage checked: 4" in validation.stdout
    assert "Settings receipt values checked: 6" in validation.stdout
    assert "Authoring asset checksums checked: 1" in validation.stdout

    run_record = json.loads(run_receipt.read_text(encoding="utf-8"))
    assert run_record["schema"] == "coursecraft.quiz_run/1"
    assert run_record["extensions"]["coursecraft.live_brightspace_operations"] == "not_performed"
    with zipfile.ZipFile(zip_path) as archive:
        assert all("__receipts" not in name for name in archive.namelist())


def test_encoded_asset_uri_builds_a_decoded_archive_member(tmp_path: Path) -> None:
    source = json.loads((AUTHORING_FIXTURE / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    source["assets"][0]["package_path"] = "quiz-assets/concept%20map.svg"
    source["questions"][0]["prompt"]["content"] = source["questions"][0]["prompt"]["content"].replace(
        "quiz-assets/concept-map.svg", "quiz-assets/concept%20map.svg"
    )
    for relation in source["relationships"]:
        if relation["kind"] == "uses_asset":
            relation["attributes"]["raw_ref"] = "quiz-assets/concept%20map.svg"
    model = tmp_path / "encoded-asset.model.json"
    model.write_text(json.dumps(source), encoding="utf-8")
    package_dir = tmp_path / "package"
    zip_path = tmp_path / "package.zip"

    build = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(AUTHORING_FIXTURE / "buildable_quiz.settings.json"),
        "--asset-root",
        str(AUTHORING_FIXTURE),
        "--output-dir",
        str(package_dir),
        "--zip-output",
        str(zip_path),
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert (package_dir / "quiz-assets" / "concept map.svg").is_file()
    assert not (package_dir / "quiz-assets" / "concept%20map.svg").exists()
    assert "quiz-assets/concept%20map.svg" in (package_dir / "questiondb.xml").read_text(encoding="utf-8")
    assert "quiz-assets/concept%20map.svg" in (package_dir / "imsmanifest.xml").read_text(encoding="utf-8")
    with zipfile.ZipFile(zip_path) as archive:
        assert "quiz-assets/concept map.svg" in archive.namelist()
        assert "quiz-assets/concept%20map.svg" not in archive.namelist()

    validation = run_script(
        "scripts/validate_quiz_package.py",
        str(package_dir),
        "--model",
        str(model),
        "--settings",
        str(AUTHORING_FIXTURE / "buildable_quiz.settings.json"),
        "--asset-root",
        str(AUTHORING_FIXTURE),
        "--zip",
        str(zip_path),
        "--strict-closure",
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr


def test_builder_rejects_encoded_asset_path_traversal(tmp_path: Path) -> None:
    source = json.loads((AUTHORING_FIXTURE / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    source["assets"][0]["package_path"] = "quiz-assets/%2e%2e/out.svg"
    model = tmp_path / "unsafe-asset.model.json"
    model.write_text(json.dumps(source), encoding="utf-8")

    result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(AUTHORING_FIXTURE / "buildable_quiz.settings.json"),
        "--asset-root",
        str(AUTHORING_FIXTURE),
        "--output-dir",
        str(tmp_path / "package"),
    )
    assert result.returncode == 2
    assert "Unsafe asset package_path" in result.stderr


def test_contract_builder_rejects_extraction_only_question(tmp_path: Path) -> None:
    source = json.loads((AUTHORING_FIXTURE / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    source["questions"][0]["build_support"]["level"] = "extraction_only"
    model = tmp_path / "unsafe.model.json"
    model.write_text(json.dumps(source), encoding="utf-8")

    result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(AUTHORING_FIXTURE / "buildable_quiz.settings.json"),
        "--asset-root",
        str(AUTHORING_FIXTURE),
        "--output-dir",
        str(tmp_path / "pkg"),
    )
    assert result.returncode == 2
    assert "exact Phase 5 candidate authorization is required" in result.stderr


def test_contract_builder_rejects_unresolved_draw_relationship(tmp_path: Path) -> None:
    source = json.loads((AUTHORING_FIXTURE / "buildable_quiz.model.json").read_text(encoding="utf-8"))
    source["relationships"][1]["status"] = "proposed"
    model = tmp_path / "unresolved.model.json"
    model.write_text(json.dumps(source), encoding="utf-8")

    result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(model),
        "--settings",
        str(AUTHORING_FIXTURE / "buildable_quiz.settings.json"),
        "--asset-root",
        str(AUTHORING_FIXTURE),
        "--output-dir",
        str(tmp_path / "pkg"),
    )
    assert result.returncode == 2
    assert "needs exactly one resolved draws_from pool" in result.stderr


def test_builder_rejects_nonempty_output_by_default(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "stale.txt").write_text("old build", encoding="utf-8")
    write_source_workbook(workbook)

    result = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
    )

    assert result.returncode == 2
    assert "Output directory is not empty" in result.stderr
    assert "--allow-nonempty-output" in result.stderr


def test_strict_closure_catches_stale_file_after_explicit_nonempty_build(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "stale.txt").write_text("old build", encoding="utf-8")
    write_source_workbook(workbook)

    build = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
        "--allow-nonempty-output",
    )
    assert build.returncode == 0, build.stdout + build.stderr

    ordinary = run_script("scripts/validate_quiz_package.py", str(package_dir))
    assert ordinary.returncode == 0, ordinary.stdout + ordinary.stderr
    assert "Package file is not declared by the manifest and may be stale or nonportable: stale.txt" in ordinary.stdout

    strict = run_script("scripts/validate_quiz_package.py", str(package_dir), "--strict-closure")
    assert strict.returncode == 2
    assert "Package file is not declared by the manifest and may be stale or nonportable: stale.txt" in strict.stdout


def test_validator_rejects_undeclared_structural_payload_even_without_strict_closure(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)
    build = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
    )
    assert build.returncode == 0, build.stdout + build.stderr
    source_quiz = next(package_dir.glob("quiz_d2l_*.xml"))
    (package_dir / "quiz_d2l_stale.xml").write_bytes(source_quiz.read_bytes())

    result = run_script("scripts/validate_quiz_package.py", str(package_dir))

    assert result.returncode == 2
    assert "Package structural file is not declared by the manifest: quiz_d2l_stale.xml" in result.stdout


def test_validator_rejects_manifest_path_escape_even_when_target_exists(tmp_path: Path) -> None:
    workbook = tmp_path / "source_questions.xlsx"
    package_dir = tmp_path / "package"
    write_source_workbook(workbook)
    build = run_script(
        "scripts/build_quiz_package_from_workbook.py",
        str(workbook),
        "--output-dir",
        str(package_dir),
    )
    assert build.returncode == 0, build.stdout + build.stderr
    (tmp_path / "outside.xml").write_text("<outside />", encoding="utf-8")
    manifest = package_dir / "imsmanifest.xml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('href="questiondb.xml"', 'href="../outside.xml"'),
        encoding="utf-8",
    )

    result = run_script("scripts/validate_quiz_package.py", str(package_dir))

    assert result.returncode == 2
    assert "Unsafe manifest href escapes the package: ../outside.xml" in result.stdout
