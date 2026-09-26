from __future__ import annotations

import html
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def metadata_field(label: str, entry: str) -> str:
    return f"""
        <qti_metadatafield>
          <fieldlabel>{label}</fieldlabel>
          <fieldentry>{entry}</fieldentry>
        </qti_metadatafield>
    """.strip()


def render_choice(labels: list[str]) -> str:
    blocks = []
    for index, label in enumerate(labels, start=1):
        blocks.append(
            f"""
            <flow_label class="Block">
              <response_label ident="A{index}">
                <flow_mat>
                  <material>
                    <mattext texttype="text/html">{html.escape(f"<p>{label}</p>")}</mattext>
                  </material>
                </flow_mat>
              </response_label>
            </flow_label>
            """.strip()
        )
    return "\n".join(blocks)


def mc_item(
    *,
    ident: str,
    label: str,
    title: str,
    displayid: str,
    globalid: str,
    question_html: str,
    choices: list[str],
    correct_indexes: list[int] | None = None,
) -> str:
    if correct_indexes is None:
        correct_indexes = [0]
    respconditions = []
    for index, _choice in enumerate(choices, start=1):
        score = "100.000000000" if (index - 1) in correct_indexes else "0.000000000"
        respconditions.append(
            f"""
            <respcondition>
              <conditionvar>
                <varequal respident="{label}_LID">A{index}</varequal>
              </conditionvar>
              <setvar action="Set">{score}</setvar>
            </respcondition>
            """.strip()
        )
    return f"""
    <item ident="{ident}" label="{label}" title="{title}">
      <itemmetadata>
        <qtimetadata>
          {metadata_field("qmd_questiontype", "Multiple Choice")}
          {metadata_field("qmd_weighting", "1.000000000")}
          {metadata_field("qmd_displayid", displayid)}
          {metadata_field("qmd_globalid", globalid)}
        </qtimetadata>
      </itemmetadata>
      <presentation>
        <flow>
          <material>
            <mattext texttype="text/html">{html.escape(question_html)}</mattext>
          </material>
          <response_lid ident="{label}_LID" rcardinality="Single">
            <render_choice shuffle="no">
              {render_choice(choices)}
            </render_choice>
          </response_lid>
        </flow>
      </presentation>
      <resprocessing>
        {''.join(respconditions)}
      </resprocessing>
    </item>
    """.strip()


def short_answer_item(
    *,
    ident: str,
    label: str,
    title: str,
    displayid: str,
    globalid: str,
    question_html: str,
    accepted_answers: list[str],
) -> str:
    respconditions = []
    for answer in accepted_answers:
        respconditions.append(
            f"""
            <respcondition>
              <conditionvar>
                <varequal respident="{label}_ANS" case="no">{answer}</varequal>
              </conditionvar>
              <setvar action="Set">100.000000000</setvar>
            </respcondition>
            """.strip()
        )

    return f"""
    <item ident="{ident}" label="{label}" title="{title}">
      <itemmetadata>
        <qtimetadata>
          {metadata_field("qmd_questiontype", "Short Answer")}
          {metadata_field("qmd_weighting", "1.000000000")}
          {metadata_field("qmd_displayid", displayid)}
          {metadata_field("qmd_globalid", globalid)}
        </qtimetadata>
      </itemmetadata>
      <presentation>
        <flow>
          <material>
            <mattext texttype="text/html">{html.escape(question_html)}</mattext>
          </material>
          <response_str ident="{label}_STR" rcardinality="Single">
            <render_fib rows="1" columns="40" prompt="Box" fibtype="String">
              <response_label ident="{label}_ANS" />
            </render_fib>
          </response_str>
        </flow>
      </presentation>
      <resprocessing>
        {''.join(respconditions)}
      </resprocessing>
    </item>
    """.strip()


def multi_select_item(
    *,
    ident: str,
    label: str,
    title: str,
    displayid: str,
    globalid: str,
    question_html: str,
    choices: list[str],
    correct_indexes: list[int],
) -> str:
    response_blocks = []
    for index, choice in enumerate(choices, start=1):
        response_blocks.append(
            f"""
            <flow_label class="Block">
              <response_label ident="{label}_A{index}">
                <flow_mat>
                  <material>
                    <mattext texttype="text/html">{html.escape(f"<p>{choice}</p>")}</mattext>
                  </material>
                </flow_mat>
              </response_label>
            </flow_label>
            """.strip()
        )
    response_labels = "\n".join(response_blocks)
    positive_ids = [f"{label}_A{index + 1}" for index in correct_indexes]
    negative_ids = [f"{label}_A{index + 1}" for index in range(len(choices)) if index not in correct_indexes]
    positive_block = "".join(f'<varequal respident="{label}_LID">{value}</varequal>' for value in positive_ids)
    negative_block = "".join(f'<varequal respident="{label}_LID">{value}</varequal>' for value in negative_ids)

    return f"""
    <item ident="{ident}" label="{label}" title="{title}">
      <itemmetadata>
        <qtimetadata>
          {metadata_field("qmd_questiontype", "Multi-Select")}
          {metadata_field("qmd_weighting", "1.000000000")}
          {metadata_field("qmd_displayid", displayid)}
          {metadata_field("qmd_globalid", globalid)}
        </qtimetadata>
      </itemmetadata>
      <presentation>
        <flow>
          <material>
            <mattext texttype="text/html">{html.escape(question_html)}</mattext>
          </material>
          <response_lid ident="{label}_LID" rcardinality="Multiple">
            <render_choice shuffle="no">
              {response_labels}
            </render_choice>
          </response_lid>
        </flow>
      </presentation>
      <resprocessing>
        <respcondition title="Scoring for the correct answers" continue="yes">
          <conditionvar>
            {positive_block}
            <not>{negative_block}</not>
          </conditionvar>
          <setvar action="Add">1</setvar>
        </respcondition>
      </resprocessing>
    </item>
    """.strip()


def long_answer_item(
    *,
    ident: str,
    label: str,
    title: str,
    displayid: str,
    globalid: str,
    question_html: str,
) -> str:
    return f"""
    <item ident="{ident}" label="{label}" title="{title}">
      <itemmetadata>
        <qtimetadata>
          {metadata_field("qmd_questiontype", "Long Answer")}
          {metadata_field("qmd_weighting", "1.000000000")}
          {metadata_field("qmd_displayid", displayid)}
          {metadata_field("qmd_globalid", globalid)}
        </qtimetadata>
      </itemmetadata>
      <presentation>
        <flow>
          <material>
            <mattext texttype="text/html">{html.escape(question_html)}</mattext>
          </material>
          <response_str ident="{label}_STR" rcardinality="Multiple">
            <render_fib rows="5" columns="60" prompt="Box" fibtype="String">
              <response_label ident="{label}_LA" />
            </render_fib>
          </response_str>
        </flow>
      </presentation>
    </item>
    """.strip()


def build_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "sample_export"
    export_dir.mkdir()

    questiondb = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <objectbank ident="QLIB_1">
    <section ident="SECT_DUP" title="Duplicate Pool">
      {mc_item(
          ident="OBJ_DUP_1",
          label="LIB_DUP_1",
          title="1",
          displayid="dup-1",
          globalid="dup-global-1",
          question_html="<p>What is 2 + 2?</p>",
          choices=["4", "5"],
      )}
      {mc_item(
          ident="OBJ_DUP_2",
          label="LIB_DUP_2",
          title="2",
          displayid="dup-2",
          globalid="dup-global-2",
          question_html="<p>What is 2 + 2?</p>",
          choices=["4", "5"],
      )}
    </section>
    <section ident="SECT_SIM" title="Similarity Pool">
      {mc_item(
          ident="OBJ_SIM_1",
          label="LIB_SIM_1",
          title="1",
          displayid="sim-1",
          globalid="sim-global-1",
          question_html="<p>Find the area of a triangle bounded by the x-axis, the y-axis, and the line f open parentheses x close parentheses equals 12 minus 1 third x.</p>",
          choices=["12", "18"],
      )}
    </section>
  </objectbank>
</questestinterop>
"""

    quiz = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_1" title="Practice Quiz">
    <section ident="CONTAINER_SECTION">
      <section ident="RAND_1" title="Random Section 1">
        <qtimetadata>
          {metadata_field("qmd_numberofitems", "1")}
        </qtimetadata>
        {mc_item(
            ident="QUIZ_DUP",
            label="QUIZ_DUP",
            title="1",
            displayid="quiz-dup",
            globalid="quiz-dup-global",
            question_html="<p>What is 2 + 2?</p>",
            choices=["4", "5"],
        )}
        {mc_item(
            ident="QUIZ_SIM",
            label="QUIZ_SIM",
            title="2",
            displayid="quiz-sim",
            globalid="quiz-sim-global",
            question_html="<p>Find the area of a triangle bounded by the x-axis, the y-axis, and the line f(x) = 12 - (1/3) x.</p>",
            choices=["12", "18"],
        )}
        {mc_item(
            ident="QUIZ_UNMATCHED",
            label="QUIZ_UNMATCHED",
            title="3",
            displayid="quiz-unmatched",
            globalid="quiz-unmatched-global",
            question_html="<p>This question is not in the library.</p>",
            choices=["Yes", "No"],
        )}
      </section>
    </section>
  </assessment>
</questestinterop>
"""

    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <resources>
    <resource identifier="RES_QDB" href="questiondb.xml" />
    <resource identifier="RES_QUIZ" href="quiz_d2l_1.xml" />
  </resources>
</manifest>
"""

    (export_dir / "questiondb.xml").write_text(questiondb, encoding="utf-8")
    (export_dir / "quiz_d2l_1.xml").write_text(quiz, encoding="utf-8")
    (export_dir / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    return export_dir


def build_math_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "math_export"
    export_dir.mkdir()

    questiondb = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <objectbank ident="QLIB_MATH">
    <section ident="SECT_MATH" title="Math Pool">
      {mc_item(
          ident="OBJ_MATH_1",
          label="LIB_MATH_1",
          title="1",
          displayid="math-1",
          globalid="math-global-1",
          question_html='<p>Evaluate the expression <img alt="10 plus 2 cross times open parentheses 3 minus 5 close parentheses plus 5." data-mathml="«math xmlns=¨http://www.w3.org/1998/Math/MathML¨»«mn»10«/mn»«mo»+«/mo»«mn»2«/mn»«mo»§#215;«/mo»«mfenced»«mrow»«mn»3«/mn»«mo»-«/mo»«mn»5«/mn»«/mrow»«/mfenced»«mo»+«/mo»«mn»5«/mn»«mo».«/mo»«/math»" /></p>',
          choices=["11", "17"],
      )}
      {mc_item(
          ident="OBJ_MATH_2",
          label="LIB_MATH_2",
          title="2",
          displayid="math-2",
          globalid="math-global-2",
          question_html='<p>Evaluate the expression <img alt="fraction numerator 18 x over denominator 9 end fraction minus 12" /></p>',
          choices=["-10", "-4"],
      )}
    </section>
  </objectbank>
</questestinterop>
"""

    quiz = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_math" title="Math Quiz">
    <section ident="CONTAINER_SECTION">
      <section ident="RAND_MATH" title="Math Section">
        <qtimetadata>
          {metadata_field("qmd_numberofitems", "2")}
        </qtimetadata>
        {mc_item(
            ident="QUIZ_MATH_1",
            label="QUIZ_MATH_1",
            title="1",
            displayid="quiz-math-1",
            globalid="quiz-math-global-1",
            question_html='<p>Evaluate the expression <img alt="10 plus 2 cross times open parentheses 3 minus 5 close parentheses plus 5." data-mathml="«math xmlns=¨http://www.w3.org/1998/Math/MathML¨»«mn»10«/mn»«mo»+«/mo»«mn»2«/mn»«mo»§#215;«/mo»«mfenced»«mrow»«mn»3«/mn»«mo»-«/mo»«mn»5«/mn»«/mrow»«/mfenced»«mo»+«/mo»«mn»5«/mn»«mo».«/mo»«/math»" /></p>',
            choices=["11", "17"],
        )}
        {mc_item(
            ident="QUIZ_MATH_2",
            label="QUIZ_MATH_2",
            title="2",
            displayid="quiz-math-2",
            globalid="quiz-math-global-2",
            question_html='<p>Evaluate the expression <img alt="fraction numerator 18 x over denominator 9 end fraction minus 12" /></p>',
            choices=["-10", "-4"],
        )}
      </section>
    </section>
  </assessment>
</questestinterop>
"""

    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <resources>
    <resource identifier="RES_QDB" href="questiondb.xml" />
    <resource identifier="RES_QUIZ" href="quiz_d2l_math.xml" />
  </resources>
</manifest>
"""

    (export_dir / "questiondb.xml").write_text(questiondb, encoding="utf-8")
    (export_dir / "quiz_d2l_math.xml").write_text(quiz, encoding="utf-8")
    (export_dir / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    return export_dir


def build_answer_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "answer_export"
    export_dir.mkdir()

    questiondb = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <objectbank ident="QLIB_ANS">
    <section ident="SECT_ANS" title="Answer Pool">
      {short_answer_item(
          ident="OBJ_SA_1",
          label="LIB_SA_1",
          title="1",
          displayid="ans-sa-1",
          globalid="ans-sa-global-1",
          question_html="<p>Compute 2 + 6.</p>",
          accepted_answers=["8"],
      )}
      {multi_select_item(
          ident="OBJ_MS_1",
          label="LIB_MS_1",
          title="2",
          displayid="ans-ms-1",
          globalid="ans-ms-global-1",
          question_html="<p>Select the prime numbers.</p>",
          choices=["2", "4", "5"],
          correct_indexes=[0, 2],
      )}
      {long_answer_item(
          ident="OBJ_LA_1",
          label="LIB_LA_1",
          title="3",
          displayid="ans-la-1",
          globalid="ans-la-global-1",
          question_html="<p>Explain your strategy.</p>",
      )}
    </section>
  </objectbank>
</questestinterop>
"""

    quiz = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_answers" title="Answer Quiz">
    <section ident="CONTAINER_SECTION">
      <section ident="RAND_ANS" title="Answer Section">
        <qtimetadata>
          {metadata_field("qmd_numberofitems", "3")}
        </qtimetadata>
        {short_answer_item(
            ident="QUIZ_SA_1",
            label="QUIZ_SA_1",
            title="1",
            displayid="quiz-sa-1",
            globalid="quiz-sa-global-1",
            question_html="<p>Compute 2 + 6.</p>",
            accepted_answers=["8"],
        )}
        {multi_select_item(
            ident="QUIZ_MS_1",
            label="QUIZ_MS_1",
            title="2",
            displayid="quiz-ms-1",
            globalid="quiz-ms-global-1",
            question_html="<p>Select the prime numbers.</p>",
            choices=["2", "4", "5"],
            correct_indexes=[0, 2],
        )}
        {long_answer_item(
            ident="QUIZ_LA_1",
            label="QUIZ_LA_1",
            title="3",
            displayid="quiz-la-1",
            globalid="quiz-la-global-1",
            question_html="<p>Explain your strategy.</p>",
        )}
      </section>
    </section>
  </assessment>
</questestinterop>
"""

    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <resources>
    <resource identifier="RES_QDB" href="questiondb.xml" />
    <resource identifier="RES_QUIZ" href="quiz_d2l_answers.xml" />
  </resources>
</manifest>
"""

    (export_dir / "questiondb.xml").write_text(questiondb, encoding="utf-8")
    (export_dir / "quiz_d2l_answers.xml").write_text(quiz, encoding="utf-8")
    (export_dir / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    return export_dir


def build_image_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "image_export"
    export_dir.mkdir()
    (export_dir / "assets").mkdir()
    (export_dir / "assets" / "sample diagram.png").write_text("fake-image", encoding="utf-8")
    (export_dir / "assets" / "library-only.png").write_text(
        "fake-library-only-image", encoding="utf-8"
    )

    question_html = (
        '<p>Review the anatomy image.</p>'
        '<p><img src="assets/sample%20diagram.png" alt="resolved asset" /></p>'
        '<p><img src="missing/not-here.jpg" alt="missing asset" /></p>'
    )

    questiondb = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <objectbank ident="QLIB_IMG">
    <section ident="SECT_IMG" title="Image Pool">
      {mc_item(
          ident="OBJ_IMG_1",
          label="LIB_IMG_1",
          title="1",
          displayid="img-1",
          globalid="img-global-1",
          question_html=question_html,
          choices=["A", "B"],
      )}
      {mc_item(
          ident="OBJ_IMG_2",
          label="LIB_IMG_2",
          title="2",
          displayid="img-2",
          globalid="img-global-2",
          question_html='<p>Library-only image question.</p><p><img src="library-only/figure.png" alt="library-only missing asset" /></p>',
          choices=["A", "B"],
      )}
      {mc_item(
          ident="OBJ_IMG_3",
          label="LIB_IMG_3",
          title="3",
          displayid="img-3",
          globalid="img-global-3",
          question_html='<p>Library-only resolved image question.</p><p><img src="assets/library-only.png" alt="library-only included asset" /></p>',
          choices=["A", "B"],
      )}
    </section>
  </objectbank>
</questestinterop>
"""

    quiz = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_img" title="Image Quiz">
    <section ident="CONTAINER_SECTION">
      <section ident="RAND_IMG" title="Image Section">
        <qtimetadata>
          {metadata_field("qmd_numberofitems", "1")}
        </qtimetadata>
        {mc_item(
            ident="QUIZ_IMG_1",
            label="QUIZ_IMG_1",
            title="1",
            displayid="img-quiz-1",
            globalid="img-quiz-global-1",
            question_html=question_html,
            choices=["A", "B"],
        )}
      </section>
    </section>
  </assessment>
</questestinterop>
"""

    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <resources>
    <resource identifier="RES_QDB" href="questiondb.xml" />
    <resource identifier="RES_QUIZ" href="quiz_d2l_image.xml" />
  </resources>
</manifest>
"""

    (export_dir / "questiondb.xml").write_text(questiondb, encoding="utf-8")
    (export_dir / "quiz_d2l_image.xml").write_text(quiz, encoding="utf-8")
    (export_dir / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    return export_dir


def build_itemref_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "itemref_export"
    export_dir.mkdir()

    questiondb = f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop>
  <objectbank ident="QLIB_REF">
    {mc_item(
        ident="OBJ_REF_1",
        label="LIB_REF_1",
        title="Referenced Question",
        displayid="ref-1",
        globalid="ref-global-1",
        question_html="<p>What is the referenced question?</p>",
        choices=["Yes", "No"],
    )}
  </objectbank>
</questestinterop>
"""

    quiz = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0">
  <assessment ident="res_quiz_ref" title="Itemref Quiz">
    <section ident="CONTAINER_SECTION">
      <itemref linkrefid="LIB_REF_1" d2l_2p0:page="1">
        <d2l_2p0:file href="questiondb.xml" />
        <d2l_2p0:points>3.000000000</d2l_2p0:points>
      </itemref>
    </section>
  </assessment>
</questestinterop>
"""

    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <resources>
    <resource identifier="RES_QDB" href="questiondb.xml" />
    <resource identifier="RES_QUIZ" href="quiz_d2l_itemref.xml" />
  </resources>
</manifest>
"""

    (export_dir / "questiondb.xml").write_text(questiondb, encoding="utf-8")
    (export_dir / "quiz_d2l_itemref.xml").write_text(quiz, encoding="utf-8")
    (export_dir / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    return export_dir


def test_extract_quiz_pool_review_writes_workbook_and_trace_outputs(tmp_path: Path) -> None:
    export_dir = build_export(tmp_path)
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(export_dir),
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads((output_dir / "sample_export__quiz_pool_review.json").read_text(encoding="utf-8"))
    assert payload["summary"]["quiz_count"] == 1
    assert payload["summary"]["bank_count"] == 2
    assert payload["summary"]["pool_count"] == 2
    assert payload["summary"]["quiz_question_count"] == 3
    assert payload["summary"]["inferred_exact_count"] == 1
    assert payload["summary"]["inferred_similarity_count"] == 1
    assert payload["summary"]["unmatched_count"] == 1
    assert len(payload["unresolved_rows"]) == 1

    by_label = {row["quiz_item_label"]: row for row in payload["quiz_question_rows"]}
    assert by_label["QUIZ_DUP"]["pool_title"] == "Duplicate Pool"
    assert "one pool" in by_label["QUIZ_DUP"]["match_note"]
    assert by_label["QUIZ_DUP"]["correct_answer"] == "4"
    assert by_label["QUIZ_DUP"]["correct_answer_basis"] == "single_selection"
    assert by_label["QUIZ_DUP"]["mathml_used"] == "no"
    assert by_label["QUIZ_SIM"]["pool_title"] == "Similarity Pool"
    assert by_label["QUIZ_SIM"]["evidence_level"] == "inferred_similarity"
    assert by_label["QUIZ_SIM"]["correct_answer"] == "12"
    assert by_label["QUIZ_UNMATCHED"]["evidence_level"] == "unmatched"

    workbook = load_workbook(output_dir / "sample_export__quiz_pool_review.xlsx", read_only=True)
    assert workbook.sheetnames == [
        "Overview",
        "Quiz Summary",
        "Quiz Sections",
        "Quiz Questions",
        "Question Images",
        "Question Banks",
        "Question Pools",
        "Unresolved Pool Match",
    ]
    unresolved_sheet = workbook["Unresolved Pool Match"]
    assert unresolved_sheet["A1"].value == "Reviewer Note"
    assert "exception queue for question-library / pool matching" in str(unresolved_sheet["A2"].value)
    overview_rows = list(workbook["Overview"].iter_rows(min_row=1, max_row=40, values_only=True))
    assert any(row[0] == "Workbook README" for row in overview_rows if row and row[0])
    assert any(row[0] == "How this was created" for row in overview_rows if row and row[0])
    assert any(row[0] == "Question Banks" for row in overview_rows if row and row[0])
    assert any(row[0] == "Question Images" for row in overview_rows if row and row[0])

    reviewer_workbook = load_workbook(output_dir / "sample_export__quiz_pool_review_reviewer.xlsx", read_only=True)
    styled_reviewer = load_workbook(output_dir / "sample_export__quiz_pool_review_reviewer.xlsx")
    for title in ("Quiz Questions", "All Questions"):
        sheet = styled_reviewer[title]
        column = next(cell.column_letter for cell in sheet[1] if cell.value == "question_type")
        assert sheet.column_dimensions[column].hidden is False
        assert sheet.column_dimensions[column].width == 24
    styled_reviewer.close()
    assert reviewer_workbook.sheetnames == [
        "Overview",
        "Quiz Summary",
        "All Questions",
        "Quiz Occurrences",
        "Quiz Questions",
        "Quiz Settings",
        "Question Images",
        "Question Library",
        "No Safe Library Match",
        "Question Entities",
    ]
    reviewer_overview_rows = list(reviewer_workbook["Overview"].iter_rows(min_row=1, max_row=50, values_only=True))
    assert any(row[0] == "What this workbook is" for row in reviewer_overview_rows if row and row[0])
    assert any(row[0] == "How to propose revisions" for row in reviewer_overview_rows if row and row[0])
    assert any(row[0] == "Source label" and row[1] == "sample_export" for row in reviewer_overview_rows if row and row[0])
    assert not any("/Users/" in str(value or "") for row in reviewer_overview_rows for value in row)
    workbook_description = next(
        str(row[1])
        for row in reviewer_overview_rows
        if row and row[0] == "What this workbook is"
    )
    assert workbook_description == (
        "This workbook presents the exported quizzes and complete Question "
        "Library for review, including question content, answer data, quiz "
        "placement, library relationships, settings, and image references."
    )
    how_to_use = next(
        str(row[1])
        for row in reviewer_overview_rows
        if row and row[0] == "How to use it"
    )
    assert "Quiz Occurrences shows each individual ordered placement" in how_to_use
    assert "Question Entities" not in how_to_use
    assert not any(
        row[0] == "Question Entities identifier glossary"
        for row in reviewer_overview_rows
        if row and row[0]
    )
    revision_help = next(
        str(row[1])
        for row in reviewer_overview_rows
        if row and row[0] == "How to propose revisions"
    )
    assert "Yellow proposal cells are editable" in revision_help
    assert "matching revised_* column" in revision_help
    assert "Leave approval_status" in revision_help
    assert "reviewer_note for context only" in revision_help
    reviewer_question_headers = [cell.value for cell in reviewer_workbook["Quiz Questions"][1]]
    assert reviewer_question_headers == [
        "quiz",
        "question_number",
        "quiz_section (blank = quiz root)",
        "occurrence_key",
        "question_entity_key",
        "question_occurrence_count",
        "placement",
        "relationship_status",
        "selection_mode",
        "draw_count",
        "candidate_pool_size",
        "pool_context_basis",
        "question_type",
        "proposed_permanent_code",
        "proposed_scoring_mode",
        "question_text",
        "revised_question_text",
        "image_link",
        "response_options",
        "revised_response_options",
        "answer_key",
        "revised_answer_key",
        "question_library_location",
        "revised_question_library_location",
        "library_link_status",
        "image_count",
        "note",
        "revision_reason",
        "proposed_by",
        "proposed_at",
        "approval_status",
        "approved_by",
        "approved_at",
        "reviewer_note",
    ]
    assert "question_library_location" in reviewer_question_headers
    assert "library_link_status" in reviewer_question_headers
    reviewer_needs_review_headers = [cell.value for cell in reviewer_workbook["No Safe Library Match"][1]]
    assert reviewer_needs_review_headers == [
        "quiz",
        "question_number",
        "quiz_section (blank = quiz root)",
        "occurrence_key",
        "question_entity_key",
        "question_type",
        "proposed_permanent_code",
        "proposed_scoring_mode",
        "question_text",
        "revised_question_text",
        "response_options",
        "revised_response_options",
        "answer_key",
        "revised_answer_key",
        "primary_image",
        "why_it_needs_review",
        "revision_reason",
        "proposed_by",
        "proposed_at",
        "approval_status",
        "approved_by",
        "approved_at",
        "reviewer_note",
    ]
    entity_sheet = reviewer_workbook["Question Entities"]
    occurrence_sheet = reviewer_workbook["Quiz Occurrences"]
    assert entity_sheet.max_row - 1 == 3
    assert occurrence_sheet.max_row - 1 == 3
    assert entity_sheet["A1"].value == "display_identifier"
    assert occurrence_sheet["A1"].value == "quiz_order"
    all_questions_sheet = reviewer_workbook["All Questions"]
    assert all_questions_sheet.max_row - 1 == 4
    all_question_headers = [cell.value for cell in all_questions_sheet[1]]
    all_question_rows = [
        dict(zip(all_question_headers, values))
        for values in all_questions_sheet.iter_rows(min_row=2, values_only=True)
    ]
    classification_counts = Counter(
        str(row["classification"]) for row in all_question_rows
    )
    assert classification_counts == {
        "Used in quiz — linked by identical content": 1,
        "Possible quiz use — similar content; review recommended": 1,
        "Library only — not used in exported quizzes": 1,
        "Quiz only — no library link found": 1,
    }
    assert sum(
        1 for row in all_question_rows if row["source_type"] == "question library"
    ) == 3
    assert next(
        row
        for row in all_question_rows
        if row["classification"] == "Quiz only — no library link found"
    )["question_text"] == "This question is not in the library."

    overview_values = {
        row[0]: row[1]
        for row in reviewer_workbook["Overview"].iter_rows(values_only=True)
        if row and row[0]
    }
    assert overview_values[
        "Additional quiz–library links inferred from identical content"
    ] == 1
    assert overview_values[
        "Possible quiz–library links suggested by similar content"
    ] == 1
    assert "not errors" in overview_values["About these library-link counts"]
    assert (
        "`Linked by identical content`"
        in overview_values["How quiz–library links are identified"]
    )
    reviewer_styled_workbook = load_workbook(output_dir / "sample_export__quiz_pool_review_reviewer.xlsx")
    assert reviewer_styled_workbook["Overview"].column_dimensions["A"].width == 48
    styled_entity_sheet = reviewer_styled_workbook["Question Entities"]
    assert reviewer_styled_workbook.sheetnames[-1] == "Question Entities"
    assert styled_entity_sheet.sheet_state == "hidden"
    styled_entity_headers = [cell.value for cell in styled_entity_sheet[1]]
    for header in ("display_identifier", "question_entity_key", "source_aliases"):
        header_cell = styled_entity_sheet.cell(
            row=1, column=styled_entity_headers.index(header) + 1
        )
        assert header_cell.comment is not None
        assert header_cell.comment.author == "CourseCraft"
    reviewer_questions_sheet = reviewer_styled_workbook["Quiz Questions"]
    assert reviewer_questions_sheet["A2"].font.sz == 14
    assert reviewer_questions_sheet["A2"].font.name == "Open Sans"
    assert reviewer_questions_sheet["A2"].fill.fgColor.rgb == "00F5F8FB"
    assert reviewer_questions_sheet["A3"].fill.fgColor.rgb in {"00000000", "000000", None}
    styled_headers = [cell.value for cell in reviewer_questions_sheet[1]]
    source_column = styled_headers.index("question_text") + 1
    revision_column = styled_headers.index("revised_question_text") + 1
    approval_column = styled_headers.index("approval_status") + 1
    assert reviewer_questions_sheet.protection.sheet is True
    assert reviewer_questions_sheet.cell(row=2, column=source_column).protection.locked is True
    assert reviewer_questions_sheet.cell(row=2, column=revision_column).protection.locked is False
    assert reviewer_questions_sheet.cell(row=2, column=approval_column).protection.locked is False
    assert reviewer_questions_sheet.cell(row=2, column=revision_column).fill.fgColor.rgb == "00FFF7D6"
    assert len(reviewer_questions_sheet.data_validations.dataValidation) == 1
    reviewer_settings_sheet = reviewer_styled_workbook["Quiz Settings"]
    assert [cell.value for cell in reviewer_settings_sheet[1]] == [
        "quiz",
        "target_entity_key",
        "setting_observation_key",
        "setting",
        "observed_state",
        "observed_value",
        "proposed_value",
        "revision_reason",
        "proposed_by",
        "proposed_at",
        "approval_status",
        "approved_by",
        "approved_at",
        "reviewer_note",
    ]
    setting_keys = [
        row[2]
        for row in reviewer_settings_sheet.iter_rows(min_row=2, values_only=True)
        if row[2]
    ]
    assert len(setting_keys) == len(set(setting_keys))
    assert all(str(value).startswith("setting.observation.") for value in setting_keys)
    assert reviewer_settings_sheet.column_dimensions["C"].hidden is True
    assert reviewer_settings_sheet.protection.sheet is True
    assert reviewer_styled_workbook["Question Entities"].protection.sheet is True
    assert reviewer_styled_workbook["Quiz Occurrences"].protection.sheet is True
    assert reviewer_styled_workbook["All Questions"].protection.sheet is True
    assert reviewer_styled_workbook["All Questions"].freeze_panes == "F2"
    assert reviewer_styled_workbook["All Questions"].row_dimensions[2].height == 60
    assert reviewer_questions_sheet.freeze_panes == "D2"
    assert reviewer_styled_workbook["Question Entities"].freeze_panes == "D2"
    assert reviewer_styled_workbook["Quiz Occurrences"].freeze_panes == "G2"
    assert reviewer_questions_sheet.page_setup.fitToWidth == 3
    assert reviewer_questions_sheet.print_title_cols == "$A:$C"


def test_extract_quiz_pool_review_renders_formula_images_readably(tmp_path: Path) -> None:
    export_dir = build_math_export(tmp_path)
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(export_dir),
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads((output_dir / "math_export__quiz_pool_review.json").read_text(encoding="utf-8"))
    by_label = {row["quiz_item_label"]: row for row in payload["quiz_question_rows"]}

    assert by_label["QUIZ_MATH_1"]["question_text"] == "Evaluate the expression 10 + 2 × (3 - 5) + 5."
    assert by_label["QUIZ_MATH_2"]["question_text"] == "Evaluate the expression 18x/9 - 12"
    assert by_label["QUIZ_MATH_1"]["mathml_used"] == "yes"
    assert by_label["QUIZ_MATH_2"]["mathml_used"] == "no"
    assert by_label["QUIZ_MATH_1"]["formula_present"] == "yes"
    assert by_label["QUIZ_MATH_2"]["formula_present"] == "yes"


def test_extract_quiz_pool_review_extracts_correct_answers_for_keyed_types(tmp_path: Path) -> None:
    export_dir = build_answer_export(tmp_path)
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(export_dir),
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads((output_dir / "answer_export__quiz_pool_review.json").read_text(encoding="utf-8"))
    by_label = {row["quiz_item_label"]: row for row in payload["quiz_question_rows"]}

    assert by_label["QUIZ_SA_1"]["correct_answer"] == "8"
    assert by_label["QUIZ_SA_1"]["correct_answer_basis"] == "text_entry"
    assert by_label["QUIZ_MS_1"]["correct_answer"] == "2 || 5"
    assert by_label["QUIZ_MS_1"]["correct_answer_basis"] == "multiple_selection"
    assert by_label["QUIZ_LA_1"]["correct_answer"] == "[manual grading; no fixed keyed answer in XML]"
    assert by_label["QUIZ_LA_1"]["correct_answer_basis"] == "manual_review"


def test_extract_quiz_pool_review_surfaces_question_image_refs_and_links(tmp_path: Path) -> None:
    export_dir = build_image_export(tmp_path)
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(export_dir),
        "--output-dir",
        str(output_dir),
        "--asset-mode",
        "copy",
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads((output_dir / "image_export__quiz_pool_review.json").read_text(encoding="utf-8"))
    assert payload["summary"]["question_rows_with_images"] == 1
    assert payload["summary"]["image_reference_count"] == 2
    assert payload["summary"]["resolved_image_reference_count"] == 1
    assert payload["summary"]["missing_image_reference_count"] == 1

    row = payload["quiz_question_rows"][0]
    assert row["question_image_count"] == 2
    assert row["question_image_refs"] == "assets/sample%20diagram.png || missing/not-here.jpg"
    assert row["question_image_paths"] == "assets/sample diagram.png"
    assert row["question_primary_image_path"] == "assets/sample diagram.png"
    assert row["question_missing_image_refs"] == "missing/not-here.jpg"

    image_rows = payload["question_image_rows"]
    assert len(image_rows) == 2
    assert image_rows[0]["image_status"] == "resolved"
    assert image_rows[0]["image_file_path"] == "assets/sample diagram.png"
    assert image_rows[1]["image_status"] == "missing"
    assert image_rows[1]["image_file_path"] == ""

    workbook = load_workbook(output_dir / "image_export__quiz_pool_review.xlsx")
    sheet = workbook["Question Images"]
    headers = [cell.value for cell in sheet[1]]
    path_col = headers.index("image_file_path") + 1
    first_path_cell = sheet.cell(row=2, column=path_col)
    assert first_path_cell.value == "assets/sample diagram.png"
    assert first_path_cell.hyperlink is not None
    copied_primary = (
        output_dir
        / "image_export__quiz_pool_review_assets"
        / "assets"
        / "sample diagram.png"
    )
    assert copied_primary.is_file()
    assert first_path_cell.hyperlink.target == copied_primary.resolve().as_uri()

    question_sheet = workbook["Quiz Questions"]
    question_headers = [cell.value for cell in question_sheet[1]]
    question_path_col = question_headers.index("question_primary_image_path") + 1
    question_path_cell = question_sheet.cell(row=2, column=question_path_col)
    assert question_path_cell.value == "assets/sample diagram.png"
    assert question_path_cell.hyperlink is not None
    assert question_path_cell.hyperlink.target == copied_primary.resolve().as_uri()

    reviewer_workbook = load_workbook(output_dir / "image_export__quiz_pool_review_reviewer.xlsx")
    reviewer_question_sheet = reviewer_workbook["Quiz Questions"]
    reviewer_headers = [cell.value for cell in reviewer_question_sheet[1]]
    reviewer_path_col = reviewer_headers.index("image_link") + 1
    reviewer_path_cell = reviewer_question_sheet.cell(row=2, column=reviewer_path_col)
    assert reviewer_path_cell.value == "assets/sample diagram.png"
    assert reviewer_path_cell.hyperlink is not None
    assert reviewer_path_cell.hyperlink.target == (
        "image_export__quiz_pool_review_assets/assets/sample diagram.png"
    )

    reviewer_image_sheet = reviewer_workbook["Question Images"]
    reviewer_image_headers = [cell.value for cell in reviewer_image_sheet[1]]
    reviewer_image_path_col = reviewer_image_headers.index("image_file") + 1
    reviewer_image_path_cell = reviewer_image_sheet.cell(
        row=2, column=reviewer_image_path_col
    )
    assert reviewer_image_path_cell.value == "assets/sample diagram.png"
    assert reviewer_image_path_cell.hyperlink is not None
    assert reviewer_image_path_cell.hyperlink.target == (
        "image_export__quiz_pool_review_assets/assets/sample diagram.png"
    )

    all_questions_sheet = reviewer_workbook["All Questions"]
    all_questions_headers = [cell.value for cell in all_questions_sheet[1]]
    assert [
        header
        for header in all_questions_headers
        if header
        in {
            "has_image",
            "image_reference_count",
            "image_references",
            "image_status",
            "image_included",
            "primary_image",
            "resolved_image_paths",
            "included_image_paths",
            "missing_image_references",
        }
    ] == [
        "has_image",
        "image_reference_count",
        "image_references",
        "image_status",
        "image_included",
        "primary_image",
        "resolved_image_paths",
        "included_image_paths",
        "missing_image_references",
    ]
    assert all_questions_headers[-12:] == [
        "has_image",
        "image_reference_count",
        "image_references",
        "image_status",
        "image_included",
        "primary_image",
        "resolved_image_paths",
        "included_image_paths",
        "missing_image_references",
        "payload_state",
        "source_question_ident",
        "source_global_id",
    ]
    all_question_rows = [
        dict(zip(all_questions_headers, values))
        for values in all_questions_sheet.iter_rows(min_row=2, values_only=True)
    ]
    used_image_row = next(
        row for row in all_question_rows if row["display_identifier"] == "img-1"
    )
    assert used_image_row["has_image"] == "yes"
    assert used_image_row["image_reference_count"] == 2
    assert used_image_row["image_status"] == "partially resolved"
    assert used_image_row["image_included"] == "partially"
    assert used_image_row["primary_image"] == (
        "image_export__quiz_pool_review_assets/assets/sample diagram.png"
    )
    assert used_image_row["resolved_image_paths"] == "assets/sample diagram.png"
    assert used_image_row["included_image_paths"] == (
        "image_export__quiz_pool_review_assets/assets/sample diagram.png"
    )
    assert used_image_row["missing_image_references"] == "missing/not-here.jpg"
    primary_image_column = all_questions_headers.index("primary_image") + 1
    used_image_row_number = next(
        row_number
        for row_number in range(2, all_questions_sheet.max_row + 1)
        if all_questions_sheet.cell(row=row_number, column=all_questions_headers.index("display_identifier") + 1).value
        == "img-1"
    )
    used_primary_image_cell = all_questions_sheet.cell(
        row=used_image_row_number, column=primary_image_column
    )
    assert used_primary_image_cell.hyperlink is not None
    assert used_primary_image_cell.hyperlink.target == (
        "image_export__quiz_pool_review_assets/assets/sample diagram.png"
    )
    library_only_image_row = next(
        row for row in all_question_rows if row["display_identifier"] == "img-2"
    )
    assert library_only_image_row["classification"] == (
        "Library only — not used in exported quizzes"
    )
    assert library_only_image_row["image_reference_count"] == 1
    assert library_only_image_row["image_references"] == "library-only/figure.png"
    assert library_only_image_row["image_status"] == "missing"
    assert library_only_image_row["image_included"] == "no"
    assert library_only_image_row["primary_image"] is None
    assert library_only_image_row["resolved_image_paths"] is None
    assert library_only_image_row["included_image_paths"] is None
    assert library_only_image_row["missing_image_references"] == (
        "library-only/figure.png"
    )
    resolved_library_only_row = next(
        row for row in all_question_rows if row["display_identifier"] == "img-3"
    )
    assert resolved_library_only_row["classification"] == (
        "Library only — not used in exported quizzes"
    )
    assert resolved_library_only_row["image_status"] == "resolved"
    assert resolved_library_only_row["image_included"] == "yes"
    assert resolved_library_only_row["primary_image"] == (
        "image_export__quiz_pool_review_assets/assets/library-only.png"
    )
    assert (
        output_dir
        / "image_export__quiz_pool_review_assets"
        / "assets"
        / "library-only.png"
    ).is_file()

    overview_rows = list(
        reviewer_workbook["Overview"].iter_rows(values_only=True)
    )
    assert any(
        row[0] == "All source questions with image refs" and row[1] == 3
        for row in overview_rows
    )
    assert any(
        row[0] == "Images in All Questions"
        and "Click a blue/underlined primary_image value" in str(row[1])
        for row in overview_rows
    )
    assert sum(row[0] == "Images in All Questions" for row in overview_rows) == 1
    revision_help_row = next(
        row_number
        for row_number in range(1, reviewer_workbook["Overview"].max_row + 1)
        if reviewer_workbook["Overview"].cell(row=row_number, column=1).value
        == "How to propose revisions"
    )
    assert reviewer_workbook["Overview"].row_dimensions[revision_help_row].height >= 220


def test_extract_quiz_pool_review_resolves_questiondb_itemrefs(tmp_path: Path) -> None:
    export_dir = build_itemref_export(tmp_path)
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(export_dir),
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads((output_dir / "itemref_export__quiz_pool_review.json").read_text(encoding="utf-8"))
    assert payload["summary"]["bank_count"] == 1
    assert payload["summary"]["quiz_question_count"] == 1
    assert payload["summary"]["source_evidence_count"] == 1

    row = payload["quiz_question_rows"][0]
    assert row["quiz_title"] == "Itemref Quiz"
    assert row["quiz_item_label"] == "LIB_REF_1"
    assert row["pool_title"] == "Question Library Root Items"
    assert row["question_text"] == "What is the referenced question?"
    assert row["choices_text"] == "Yes || No"
    assert row["correct_answer"] == "Yes"
    assert row["question_weight"] == "3.000000000"
    assert row["evidence_level"] == "source_evidence"
    assert row["match_basis"] == "itemref_linkrefid"

    bank_row = payload["bank_rows"][0]
    assert bank_row["bank_title"] == "Question Library Root Items"
    assert bank_row["bank_pool_count"] == 1
    assert bank_row["mapped_quiz_question_count"] == 1
    assert bank_row["source_evidence_count"] == 1

    reviewer_workbook = load_workbook(output_dir / "itemref_export__quiz_pool_review_reviewer.xlsx", read_only=True)
    reviewer_question_sheet = reviewer_workbook["Quiz Questions"]
    reviewer_headers = [cell.value for cell in reviewer_question_sheet[1]]
    reviewer_row = dict(zip(reviewer_headers, next(reviewer_question_sheet.iter_rows(min_row=2, max_row=2, values_only=True))))
    assert reviewer_row["response_options"] == "Yes\nNo"
    assert reviewer_row["answer_key"] == "Yes"


def test_reviewer_occurrence_view_keeps_ambiguous_exact_candidates_in_place(
    tmp_path: Path,
) -> None:
    fixture = (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "quiz_xml"
        / "duplicate_pool_and_similarity"
    )
    output_dir = tmp_path / "review"

    result = run_script(
        "scripts/extract_quiz_pool_review.py",
        str(fixture),
        "--output-dir",
        str(output_dir),
        "--source-lineage-key",
        "cc:lineage:fixture:ambiguous-review-workbook",
    )
    assert result.returncode == 0, result.stderr

    workbook = load_workbook(
        output_dir
        / "duplicate_pool_and_similarity__quiz_pool_review_reviewer.xlsx",
        read_only=True,
    )
    sheet = workbook["Quiz Occurrences"]
    headers = [cell.value for cell in sheet[1]]
    rows = [
        dict(zip(headers, values))
        for values in sheet.iter_rows(min_row=2, values_only=True)
    ]
    exact = next(row for row in rows if row["question_number"] == 1)
    probable = next(row for row in rows if row["question_number"] == 2)

    assert exact["library_match_status"] == "Linked by identical content"
    assert exact["library_match_code"] == "inferred_exact"
    assert "dup-1 (score 1.0)" in exact["library_match_candidates"]
    assert "dup-2 (score 1.0)" in exact["library_match_candidates"]
    assert (
        exact["library_match_explanation"]
        == "More than one library candidate remains plausible. A person must "
        "choose or decline; the Binder will not guess."
    )
    assert probable["library_match_status"] == (
        "Possible link based on similar content"
    )
    assert probable["library_match_code"] == "inferred_similarity"
    assert "A person must confirm or decline it." in probable[
        "library_match_explanation"
    ]
