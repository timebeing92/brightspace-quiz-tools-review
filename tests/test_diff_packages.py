from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/diff_packages.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def question_item(ident: str, label: str, title: str, qtype: str = "Multiple Choice", text: str = "Stem?") -> str:
    return f"""
    <item ident="{ident}" label="{label}" title="{title}">
      <itemmetadata><qtimetadata>
        <qti_metadatafield><fieldlabel>qmd_questiontype</fieldlabel><fieldentry>{qtype}</fieldentry></qti_metadatafield>
        <qti_metadatafield><fieldlabel>qmd_displayid</fieldlabel><fieldentry>{label}</fieldentry></qti_metadatafield>
      </qtimetadata></itemmetadata>
      <presentation><flow><material><mattext texttype="text/html">{text}</mattext></material></flow></presentation>
    </item>"""


def build_package(
    root: Path,
    *,
    prefix: str,
    quiz_title: str = "Module 1 Quiz",
    questions: list[tuple[str, str]] | None = None,
    draw_count: int = 2,
    quiz_question_titles: list[str] | None = None,
    time_limit: str = "0",
    asset_ref: str = "",
    asset_exists: bool = True,
) -> Path:
    """Build a minimal quiz package. `questions` is a list of (title, text)."""
    if questions is None:
        questions = [("Q One", "Stem one?"), ("Q Two", "Stem two?"), ("Q Three", "Stem three?")]
    if quiz_question_titles is None:
        quiz_question_titles = [title for title, _ in questions]

    root.mkdir(parents=True, exist_ok=True)
    labels = {title: f"QUES_{prefix}_{index}" for index, (title, _) in enumerate(questions, start=1)}
    texts = dict(questions)

    db_items = "".join(
        question_item(f"OBJ_{prefix}_{index}", labels[title], title, text=text)
        for index, (title, text) in enumerate(questions, start=1)
    )
    (root / "questiondb.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <objectbank ident="QLIB_{prefix}">
    <section ident="SECT_{prefix}" title="Bank Section">{db_items}
    </section>
  </objectbank>
</questestinterop>
""",
        encoding="utf-8",
    )

    asset_html = (
        f'&lt;p&gt;Intro &lt;img src="{asset_ref}" alt="figure" /&gt;&lt;/p&gt;' if asset_ref else "Intro"
    )
    quiz_items = "".join(
        question_item(f"OBJ_{prefix}_S1", labels.get(title, f"QUES_{prefix}_X"), title, text=texts.get(title, "Stem?"))
        for title in quiz_question_titles
    )
    (root / f"quiz_d2l_{prefix}.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_{prefix}" title="{quiz_title}">
    <assessmentcontrol hintswitch="no" feedbackswitch="no" />
    <presentation_material><flow_mat><material>
      <mattext texttype="text/html">{asset_html}</mattext>
    </material></flow_mat></presentation_material>
    <assess_procextension>
      <is_active>yes</is_active>
      <time_limit>{time_limit}</time_limit>
    </assess_procextension>
    <section ident="CONTAINER_SECTION">
      <section ident="RAND_{prefix}" title="Pool A">
        <sectionproc_extension><qtimetadata>
          <qti_metadatafield><fieldlabel>qmd_numberofitems</fieldlabel><fieldentry>{draw_count}</fieldentry></qti_metadatafield>
        </qtimetadata></sectionproc_extension>{quiz_items}
      </section>
    </section>
  </assessment>
</questestinterop>
""",
        encoding="utf-8",
    )

    (root / "imsmanifest.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="MANIFEST_{prefix}">
  <resources>
    <resource identifier="res_qdb" type="webcontent" d2l_material_type="d2lquestionlibrary" href="questiondb.xml" />
    <resource identifier="res_quiz" type="webcontent" d2l_material_type="d2lquiz" href="quiz_d2l_{prefix}.xml" />
  </resources>
</manifest>
""",
        encoding="utf-8",
    )

    if asset_ref and asset_exists:
        asset_path = root / asset_ref
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"fake-image")
    return root


def run_diff(tmp_path: Path, **b_kwargs) -> tuple[subprocess.CompletedProcess[str], dict]:
    build_package(tmp_path / "generated", prefix="100")
    build_package(tmp_path / "reexport", prefix="200", **b_kwargs)
    output_dir = tmp_path / "review"
    result = run_script(
        str(tmp_path / "generated"),
        str(tmp_path / "reexport"),
        "--output-dir",
        str(output_dir),
        "--fail-on-break",
    )
    report_path = output_dir / "generated__vs__reexport__roundtrip_diff.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    return result, report


def add_grade_join(
    package: Path,
    *,
    prefix: str,
    identifier: str,
    resource_code: str,
    name: str,
) -> None:
    quiz_path = package / f"quiz_d2l_{prefix}.xml"
    quiz_root = ET.parse(quiz_path).getroot()
    assess_proc = next(elem for elem in quiz_root.iter() if elem.tag == "assess_procextension")
    grade_item = ET.Element("grade_item", {"resource_code": resource_code})
    grade_item.text = identifier
    assess_proc.insert(0, grade_item)
    ET.ElementTree(quiz_root).write(quiz_path, encoding="utf-8", xml_declaration=True)

    (package / "grades_d2l.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<grades><items><item identifier="{identifier}" resource_code="{resource_code}">
  <name>{name}</name>
</item></items></grades>
""",
        encoding="utf-8",
    )


def test_quicklink_href_not_flagged_as_missing(tmp_path: Path) -> None:
    """Quicklink/external manifest hrefs are LMS URLs, not files in the package.

    Regression: a real re-export carries contentlink quicklink resources whose
    href is `/d2l/common/dialogs/quickLink/...`; the missing-file check wrongly
    flagged these as breaks. They must be skipped.
    """
    generated = build_package(tmp_path / "generated", prefix="100")
    reexport = build_package(tmp_path / "reexport", prefix="200")
    manifest = reexport / "imsmanifest.xml"
    quicklink = (
        '<resource identifier="res_ql" type="webcontent" d2l_material_type="contentlink" '
        'href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou={orgUnitId}&amp;type=dropbox&amp;rCode=ABC-123" />'
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("</resources>", f"  {quicklink}\n  </resources>"),
        encoding="utf-8",
    )
    output_dir = tmp_path / "review"
    result = run_script(str(generated), str(reexport), "--output-dir", str(output_dir))
    report = json.loads((output_dir / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8"))

    assert not any("quickLink" in b for b in report["breaks"]), report["breaks"]
    assert result.returncode == 0, result.stdout + result.stderr


def test_clean_roundtrip_with_reassigned_ids_passes(tmp_path: Path) -> None:
    result, report = run_diff(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["report_type"] == "coursecraft.quiz_package_diff/1"
    assert report["matching_policy"]["ambiguous_matches"] == "never_guess"
    assert report["matching_policy"]["raw_platform_ids_are_identity"] is False
    for side_name in ("side_a", "side_b"):
        fingerprint = report[side_name]["fingerprint"]
        assert fingerprint["algorithm"] == "sha256"
        assert fingerprint["scope"] == "file_set"
        assert len(fingerprint["digest"]) == 64
        assert fingerprint["file_count"] == 3
        assert fingerprint["bytes"] > 0
    assert report["side_a"]["fingerprint"]["digest"] != report["side_b"]["fingerprint"]["digest"]
    assert report["breaks"] == []
    assert len(report["questions"]["matched"]) == 3
    # IDs were reassigned (different prefixes) and reported as mappings, not breaks.
    mappings = {q["label_a"]: q["label_b"] for q in report["questions"]["matched"]}
    assert mappings == {"QUES_100_1": "QUES_200_1", "QUES_100_2": "QUES_200_2", "QUES_100_3": "QUES_200_3"}

    markdown = (tmp_path / "review" / "generated__vs__reexport__roundtrip_diff.md").read_text(encoding="utf-8")
    assert "Side A fingerprint" in markdown
    assert report["side_a"]["fingerprint"]["digest"] in markdown
    assert "ID mappings (expected)" in markdown
    assert "Breaks" in markdown


def test_dropped_question_is_a_break(tmp_path: Path) -> None:
    result, report = run_diff(
        tmp_path,
        questions=[("Q One", "Stem one?"), ("Q Two", "Stem two?")],
        quiz_question_titles=["Q One", "Q Two"],
    )

    assert result.returncode == 1
    assert any("question missing in reexport: 'Q Three'" in b for b in report["breaks"])
    section = report["quizzes"][0]["sections"][0]
    assert "Q Three" in section["missing_in_b"]


def test_changed_draw_count_is_a_break(tmp_path: Path) -> None:
    result, report = run_diff(tmp_path, draw_count=3)

    assert result.returncode == 1
    assert any("draw count changed 2 -> 3" in b for b in report["breaks"])


def test_missing_asset_is_a_break(tmp_path: Path) -> None:
    build_package(tmp_path / "generated", prefix="100", asset_ref="images/fig1.png", asset_exists=True)
    build_package(tmp_path / "reexport", prefix="200", asset_ref="images/fig1.png", asset_exists=False)
    result = run_script(
        str(tmp_path / "generated"),
        str(tmp_path / "reexport"),
        "--output-dir",
        str(tmp_path / "review"),
        "--fail-on-break",
    )

    assert result.returncode == 1
    report = json.loads(
        (tmp_path / "review" / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8")
    )
    assert any("asset referenced but missing in reexport: images/fig1.png" in b for b in report["breaks"])


def test_duplicate_titles_with_same_text_are_ambiguous_not_guessed(tmp_path: Path) -> None:
    duplicated = [("Q Same", "Identical stem?"), ("Q Same", "Identical stem?")]
    build_package(tmp_path / "generated", prefix="100", questions=duplicated, quiz_question_titles=["Q Same"], draw_count=1)
    build_package(tmp_path / "reexport", prefix="200", questions=duplicated, quiz_question_titles=["Q Same"], draw_count=1)
    result = run_script(
        str(tmp_path / "generated"),
        str(tmp_path / "reexport"),
        "--output-dir",
        str(tmp_path / "review"),
        "--fail-on-break",
    )

    assert result.returncode == 1
    report = json.loads(
        (tmp_path / "review" / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8")
    )
    assert report["questions"]["ambiguous"], "duplicate identical questions must surface as ambiguous"
    assert any("not guessed" in b for b in report["breaks"])


def test_duplicate_titles_with_distinct_text_match_cleanly(tmp_path: Path) -> None:
    distinct = [("Q Same", "First stem?"), ("Q Same", "Second stem?")]
    build_package(tmp_path / "generated", prefix="100", questions=distinct, quiz_question_titles=["Q Same"], draw_count=1)
    build_package(tmp_path / "reexport", prefix="200", questions=distinct, quiz_question_titles=["Q Same"], draw_count=1)
    result = run_script(
        str(tmp_path / "generated"),
        str(tmp_path / "reexport"),
        "--output-dir",
        str(tmp_path / "review"),
        "--fail-on-break",
    )

    report = json.loads(
        (tmp_path / "review" / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8")
    )
    assert len(report["questions"]["matched"]) == 2
    assert not report["questions"]["ambiguous"]
    assert result.returncode == 0, result.stdout + result.stderr


def test_settings_change_reported_not_break(tmp_path: Path) -> None:
    result, report = run_diff(tmp_path, time_limit="120")

    assert result.returncode == 0, result.stdout + result.stderr
    diffs = report["quizzes"][0]["settings_diffs"]
    assert {"key": "time_limit", "a": "0", "b": "120"} in diffs


def test_grade_join_compares_semantics_not_reassigned_platform_ids(tmp_path: Path) -> None:
    generated = build_package(
        tmp_path / "generated", prefix="100", quiz_title="P5 TWIN (workbook lane)"
    )
    reexport = build_package(
        tmp_path / "reexport", prefix="200", quiz_title="P5 TWIN (model lane)"
    )
    add_grade_join(
        generated,
        prefix="100",
        identifier="700001",
        resource_code="grade-w",
        name="P5 TWIN (workbook lane)",
    )
    add_grade_join(
        reexport,
        prefix="200",
        identifier="712050",
        resource_code="grade-m",
        name="P5 TWIN (model lane)",
    )

    output_dir = tmp_path / "review"
    result = run_script(str(generated), str(reexport), "--output-dir", str(output_dir))
    report = json.loads(
        (output_dir / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8")
    )

    assert result.returncode == 0, result.stdout + result.stderr
    quiz = report["quizzes"][0]
    assert quiz["grade_join_equivalent"] is True
    assert quiz["grade_join_a"]["state"] == "resolved_title_matched"
    assert quiz["grade_join_b"]["state"] == "resolved_title_matched"
    assert quiz["settings_diffs"] == []


def test_unresolved_or_mismatched_grade_join_is_reported(tmp_path: Path) -> None:
    generated = build_package(tmp_path / "generated", prefix="100")
    reexport = build_package(tmp_path / "reexport", prefix="200")
    add_grade_join(
        generated,
        prefix="100",
        identifier="700001",
        resource_code="grade-a",
        name="Module 1 Quiz",
    )
    add_grade_join(
        reexport,
        prefix="200",
        identifier="700002",
        resource_code="grade-b",
        name="Wrong grade item",
    )

    output_dir = tmp_path / "review"
    result = run_script(str(generated), str(reexport), "--output-dir", str(output_dir))
    report = json.loads(
        (output_dir / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8")
    )

    assert result.returncode == 0, result.stdout + result.stderr
    quiz = report["quizzes"][0]
    assert quiz["grade_join_equivalent"] is False
    assert quiz["grade_join_b"]["state"] == "resolved_title_mismatch"
    assert {"key": "grade_join", "a": "resolved_title_matched", "b": "resolved_title_mismatch"} in quiz[
        "settings_diffs"
    ]


def test_quiz_title_change_matched_positionally(tmp_path: Path) -> None:
    result, report = run_diff(tmp_path, quiz_title="Module 1 Quiz (Renamed)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["quizzes"][0]["title_changed"] is True
    assert any("matched positionally" in note for note in report["notes"])


def test_accepts_zip_input(tmp_path: Path) -> None:
    import zipfile

    package_dir = build_package(tmp_path / "generated", prefix="100")
    zip_path = tmp_path / "generated.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in sorted(package_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(package_dir).as_posix())
    build_package(tmp_path / "reexport", prefix="200")

    result = run_script(
        str(zip_path),
        str(tmp_path / "reexport"),
        "--output-dir",
        str(tmp_path / "review"),
        "--fail-on-break",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "questions matched: 3" in result.stdout


def test_can_scope_a_whole_export_to_one_exact_quiz_file(tmp_path: Path) -> None:
    generated = build_package(tmp_path / "generated", prefix="100")
    whole_export = build_package(tmp_path / "whole-export", prefix="200")
    extra = whole_export / "quiz_d2l_extra.xml"
    extra.write_text(
        (whole_export / "quiz_d2l_200.xml").read_text(encoding="utf-8").replace(
            "Module 1 Quiz", "Unrelated extra quiz"
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "review"
    result = run_script(
        str(generated),
        str(whole_export),
        "--quiz-file-b",
        "quiz_d2l_200.xml",
        "--output-dir",
        str(output_dir),
        "--fail-on-break",
    )
    report = json.loads(
        (output_dir / "generated__vs__whole-export__roundtrip_diff.json").read_text(encoding="utf-8")
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["side_b"]["question_count"] == 3
    assert len(report["quizzes"]) == 1
    assert report["quizzes"][0]["file_b"] == "quiz_d2l_200.xml"


def test_quiz_scope_ignores_missing_asset_owned_by_unrelated_question(tmp_path: Path) -> None:
    generated = build_package(tmp_path / "generated", prefix="100")
    whole_export = build_package(tmp_path / "whole-export", prefix="200")
    questiondb = whole_export / "questiondb.xml"
    unrelated = question_item(
        "OBJ_UNRELATED",
        "QUES_UNRELATED",
        "Unrelated probe question",
        text='&lt;img src="probe-assets/broken.png" alt="probe" /&gt;',
    )
    questiondb.write_text(
        questiondb.read_text(encoding="utf-8").replace(
            "    </section>", f"{unrelated}\n    </section>"
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "review"
    result = run_script(
        str(generated),
        str(whole_export),
        "--quiz-file-b",
        "quiz_d2l_200.xml",
        "--output-dir",
        str(output_dir),
        "--fail-on-break",
    )
    report = json.loads(
        (output_dir / "generated__vs__whole-export__roundtrip_diff.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("probe-assets/broken.png" in row for row in report["breaks"])


def test_quiz_scope_still_fails_for_missing_asset_owned_by_selected_quiz(tmp_path: Path) -> None:
    generated = build_package(
        tmp_path / "generated",
        prefix="100",
        asset_ref="images/selected.png",
        asset_exists=True,
    )
    whole_export = build_package(
        tmp_path / "whole-export",
        prefix="200",
        asset_ref="images/selected.png",
        asset_exists=False,
    )

    output_dir = tmp_path / "review"
    result = run_script(
        str(generated),
        str(whole_export),
        "--quiz-file-b",
        "quiz_d2l_200.xml",
        "--output-dir",
        str(output_dir),
        "--fail-on-break",
    )
    report = json.loads(
        (output_dir / "generated__vs__whole-export__roundtrip_diff.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.returncode == 1
    assert any(
        "asset referenced but missing in whole-export: images/selected.png" in row
        for row in report["breaks"]
    )


def test_permanent_displayid_matches_before_changed_title_and_text(tmp_path: Path) -> None:
    generated = build_package(tmp_path / "generated", prefix="100")
    reexport = build_package(
        tmp_path / "reexport",
        prefix="200",
        questions=[
            ("Renamed One", "Revised stem one?"),
            ("Renamed Two", "Revised stem two?"),
            ("Renamed Three", "Revised stem three?"),
        ],
    )
    for index in range(1, 4):
        for xml_path in generated.glob("*.xml"):
            xml_path.write_text(
                xml_path.read_text(encoding="utf-8").replace(f"QUES_100_{index}</fieldentry>", f"PERSIST_Q{index}</fieldentry>"),
                encoding="utf-8",
            )
        for xml_path in reexport.glob("*.xml"):
            xml_path.write_text(
                xml_path.read_text(encoding="utf-8").replace(f"QUES_200_{index}</fieldentry>", f"PERSIST_Q{index}</fieldentry>"),
                encoding="utf-8",
            )

    output_dir = tmp_path / "review"
    result = run_script(str(generated), str(reexport), "--output-dir", str(output_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output_dir / "generated__vs__reexport__roundtrip_diff.json").read_text(encoding="utf-8"))
    assert len(report["questions"]["matched"]) == 3
    assert {row["match_basis"] for row in report["questions"]["matched"]} == {"displayid"}
    assert not report["questions"]["missing_in_b"]
