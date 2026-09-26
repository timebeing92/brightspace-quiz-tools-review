from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import quiz_review_station as station_module  # noqa: E402
from quiz_review_station import (  # noqa: E402
    FORMAT,
    build_quiz_review_station,
    render_quiz_review_station_html,
    write_quiz_review_station,
)


SCRIPT = SCRIPTS / "quiz_review_station.py"
GOLDEN_MODEL = (
    REPO_ROOT / "tests" / "fixtures" / "quiz_binder" / "golden_course.model.json"
)
KINDS_MODEL = (
    REPO_ROOT
    / "workspace"
    / "reference"
    / "schemas"
    / "quiz"
    / "examples"
    / "observed_question_kinds.example.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


def test_station_links_unique_questions_to_every_occurrence_and_keeps_authored_evidence():
    model = _load(GOLDEN_MODEL)
    repeated = deepcopy(model["relationships"][0])
    repeated["relationship_key"] = "cc:relationship:fixture:binder-quiz-q1-repeat"
    repeated["ordinal"] = 5
    model["relationships"].append(repeated)

    data = build_quiz_review_station(model)
    rendered = render_quiz_review_station_html(data)

    assert data["format"] == FORMAT
    assert data["share_safety"] == {
        "classification": "local_course_evidence",
        "share_safe": False,
        "reason": "Contains authored prompts, response keys, feedback, and course provenance.",
        "label": "Local course evidence — not share-safe by default",
    }
    assert data["summary"]["unique_question_count"] == 4
    assert data["summary"]["occurrence_count"] == 5
    assert (
        sum(
            row["question_entity_key"] == "cc:question:fixture:binder-direct"
            for row in data["question_occurrences"]
        )
        == 2
    )
    assert (
        next(
            row
            for row in data["question_entities"]
            if row["entity_key"] == "cc:question:fixture:binder-direct"
        )["extraction_fidelity"]["occurrence_count"]
        == 2
    )
    assert "SENTINEL_STEM_DIRECT" in rendered
    assert "SENTINEL_OPTION_ALPHA" in rendered
    assert "SENTINEL_MANUAL_ANSWER" in rendered
    assert "SENTINEL_ACCEPTED_RESPONSE" in rendered
    assert "Local course evidence — not share-safe by default" in rendered
    assert "<h1>Reading Room</h1>" in rendered
    assert data["queue_summary"]["no_safe_match_count"] == 1
    assert data["queue_summary"]["probable_match_count"] == 1
    assert {row["match_status"] for row in data["question_occurrences"]} == {
        "Direct library link",
        "Linked by identical content",
        "Possible link based on similar content",
        "No reliable library link found",
    }
    assert "A person must choose or decline; the Binder will not guess." in rendered
    assert "Review queues" in rendered
    assert "Build support — separate claim" in rendered
    assert "Extraction fidelity" in rendered


def test_station_renders_kind_specific_response_views():
    data = build_quiz_review_station(_load(KINDS_MODEL))
    rendered = render_quiz_review_station_html(data)

    assert data["summary"]["unique_question_count"] == 9
    assert "Options and answer key" in rendered
    assert "Accepted responses" in rendered
    assert "Blanks and accepted responses" in rendered
    assert "Manual answer key / evaluator guidance" in rendered
    assert "Matching answer key" in rendered
    assert "Correct order" in rendered
    assert "Source response facts" in rendered


def test_station_surfaces_authored_variant_reuse_without_hiding_source_items():
    model = _load(GOLDEN_MODEL)
    representative = "cc:question:fixture:binder-exact"
    member = "cc:question:fixture:binder-probable"
    question_by_key = {row["entity_key"]: row for row in model["questions"]}
    for question_key, weight, section_item_number in (
        (representative, "1", 2),
        (member, "2.5", 3),
    ):
        question_by_key[question_key]["type_payload"]["raw_response_models"].append(
            {
                "source_kind": "canonical-extractor-row:synthetic-reuse-points",
                "payload": {
                    "question_weight": weight,
                    "quiz_section_item_number": section_item_number,
                },
                "source_evidence_keys": [],
                "extensions": {},
            }
        )
    model["relationships"].append(
        {
            "relationship_key": "cc:relationship:fixture:authored-variant-reuse",
            "kind": "same_as",
            "source_kind": "authored_variant_equivalence",
            "from_entity_key": member,
            "to_entity_key": representative,
            "status": "resolved",
            "ordinal": None,
            "attributes": {
                "basis": "authored variant",
                "authored_variant_digest": "synthetic-variant-digest",
                "representative_entity_key": representative,
                "class_size": 2,
            },
            "candidates": [],
            "source_evidence_keys": [],
            "diagnostic_ids": [],
            "extensions": {},
        }
    )

    data = build_quiz_review_station(model)
    rendered = render_quiz_review_station_html(data)
    by_key = {row["entity_key"]: row for row in data["question_entities"]}

    assert data["summary"]["authored_variant_class_count"] == 3
    assert data["summary"]["reused_authored_variant_class_count"] == 1
    assert by_key[representative]["authored_variant"] == {
        "digest": None,
        "class_key": representative,
        "member_keys": [representative, member],
        "members": [
            {
                "entity_key": representative,
                "display_identifier": "BINDER-Q002",
                "point_values": ["1"],
                "placement_count": 1,
            },
            {
                "entity_key": member,
                "display_identifier": "BINDER-Q003",
                "point_values": ["2.5"],
                "placement_count": 1,
            },
        ],
        "member_count": 2,
        "reused": True,
        "is_representative": True,
    }
    assert by_key[member]["authored_variant"]["class_key"] == representative
    assert by_key[member]["authored_variant"]["is_representative"] is False
    assert "Source question records" in rendered
    assert "Reused variant groups" in rendered
    assert "Same authored content across 2 source question records" in rendered
    assert "Placement points differ across these source records" in rendered
    assert "Placement points:</strong> 1" in rendered
    assert "Placement points:</strong> 2.5" in rendered


def test_static_reading_room_remains_useful_without_javascript_and_meets_visual_cues():
    rendered = render_quiz_review_station_html(
        build_quiz_review_station(_load(GOLDEN_MODEL))
    )
    static_markup = rendered.split("<script>", 1)[0]

    assert '<a class="skip" href="#main">' in static_markup
    assert '<main id="main" tabindex="-1">' in static_markup
    assert 'class="queue-lane"' in static_markup
    assert 'class="occurrence-card"' in static_markup
    assert 'class="question-card"' in static_markup
    assert "Authored prompt" in static_markup
    assert "Responses and answer evidence" in static_markup
    assert "Machine codes" in static_markup
    assert "Plain mode" not in rendered
    assert ":focus-visible" in rendered
    assert "prefers-reduced-motion" in rendered
    assert "@media(max-width:650px)" in rendered
    assert "[hidden]" in rendered

    variables = dict(re.findall(r"--([a-z-]+):(#(?:[0-9a-fA-F]{6}))", rendered))
    contrast_pairs = [
        (variables["ink"], variables["paper"]),
        (variables["muted"], variables["paper"]),
        (variables["muted"], "#ffffff"),
        (variables["accent"], "#ffffff"),
        (variables["danger"], "#ffffff"),
        (variables["good"], "#ffffff"),
        (variables["warn"], "#fff8e7"),
    ]
    assert all(
        _contrast_ratio(foreground, background) >= 4.5
        for foreground, background in contrast_pairs
    )


def test_write_station_allows_run_local_links_and_withholds_absolute_source_paths(
    tmp_path: Path,
):
    model = _load(GOLDEN_MODEL)
    model["source"]["references"] = [
        "/Users/example/private/export.zip",
        r"C:\\Users\\example\\private\\export.zip",
    ]
    model_path = tmp_path / "private-input" / "model.json"
    model_path.parent.mkdir()
    model_path.write_text(json.dumps(model), encoding="utf-8")
    output_dir = tmp_path / "run" / "station"

    data = write_quiz_review_station(
        model_path,
        output_dir,
        artifact_links={
            "model": "../extraction/model.json",
            "reviewer_workbook": "../extraction/reviewer.xlsx",
            "readiness": "../readiness/readiness.json",
        },
    )

    station_json = (output_dir / "station.json").read_text(encoding="utf-8")
    station_html = (output_dir / "index.html").read_text(encoding="utf-8")
    first_quiz_html = (output_dir / "quizzes" / "quiz-0001.html").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(station_json)
    assert data["artifacts"][0]["href"].startswith("../")
    assert "/Users/example" not in station_json
    assert r"C:\\Users\\example" not in station_json
    assert str(tmp_path) not in station_json
    assert str(tmp_path) not in station_html
    assert "[absolute local path withheld]/export.zip" in station_json
    assert "fetch(" not in station_html
    assert "XMLParser" not in station_html
    assert "<script src=" not in station_html
    assert '<link rel="stylesheet"' not in station_html
    assert "file://" in station_html
    assert 'aria-live="polite"' in first_quiz_html
    assert "prefers-reduced-motion" in station_html
    assert "Plain mode" not in station_html
    assert "Questions in context" not in station_html
    assert "<h1>Reading Room</h1>" in station_html
    assert manifest["storage"] == {
        "authored_question_content": "question_html_once",
        "file_protocol_fallback": True,
        "mode": "reference_shards",
        "navigation": "quiz_first",
        "occurrences": "quiz_pages_with_placement_evidence_and_question_reference_only",
        "requires_host": False,
        "requires_javascript": False,
        "shared_records": "catalog_html_once",
        "workbook_export": "verified_local_zip_when_available",
    }
    assert "SENTINEL_STEM_DIRECT" not in station_json
    assert "SENTINEL_STEM_DIRECT" not in station_html
    assert (output_dir / "questions" / "questions-0001.html").is_file()
    assert (output_dir / "quizzes" / "quiz-0001.html").is_file()
    assert (output_dir / "catalogs" / "assets-0001.html").is_file()


def test_reading_room_home_is_quiz_first_and_keeps_technical_files_secondary(
    tmp_path: Path,
):
    model = _load(GOLDEN_MODEL)
    model["questions"][0]["title"] = "Untitled question"
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    output_dir = tmp_path / "room"

    write_quiz_review_station(model_path, output_dir)

    index_html = (output_dir / "index.html").read_text(encoding="utf-8")
    quiz_html = (output_dir / "quizzes" / "quiz-0001.html").read_text(
        encoding="utf-8"
    )
    assert "Download the review workbooks" in index_html
    assert "Choose a quiz" in index_html
    assert "Open quiz" in index_html
    assert "Evidence and technical files" in index_html
    assert "Reading Room shelves" not in index_html
    assert "record(s)" not in index_html
    assert '<a class="skip" href="#main">Skip to main content</a>' in index_html
    assert '<main id="main" tabindex="-1">' in index_html
    assert '<a class="skip" href="#main">Skip to main content</a>' in quiz_html
    assert '<main id="main" tabindex="-1">' in quiz_html
    assert "@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}" in quiz_html
    assert "Find a question" in quiz_html
    assert 'id="quiz-filters"' in quiz_html
    assert 'class="section-group"' in quiz_html
    assert 'aria-live="polite"' in quiz_html
    assert "SENTINEL_STEM_DIRECT" not in quiz_html
    assert len(list((output_dir / "questions").glob("*.html"))) == 4
    first_question_html = (
        output_dir / "questions" / "questions-0001.html"
    ).read_text(encoding="utf-8")
    assert "<h1>Untitled question</h1>" not in first_question_html


def test_quiz_page_numbers_duplicate_section_titles_and_placeholder_questions():
    rows = [
        {
            "occurrence_key": f"occurrence-{index}",
            "question_entity_key": "question-1",
            "quiz_title": "Midterm",
            "quiz_ordinal": 1,
            "section_title": "Quiz Section",
            "section_ordinal": index,
            "question_ordinal": index,
            "source_kind": "inline",
            "source_status": "resolved",
            "source_status_label": "Resolved",
            "match_status": "No reliable library link found",
            "match_status_code": "unresolved",
            "diagnostic_ids": [],
        }
        for index in (1, 2)
    ]
    rendered = station_module._render_quiz_page(
        {
            "title": "Midterm",
            "rows": rows,
            "occurrence_count": 2,
            "unique_question_count": 1,
            "section_count": 2,
            "pool_count": 0,
            "review_occurrence_count": 2,
        },
        entity_by_key={
            "question-1": {
                "title": "Untitled question",
                "kind": "multiple_choice",
                "display_identifier": "question-1",
            }
        },
        question_hrefs={"question-1": "../questions/questions-0001.html"},
        previous_href=None,
        next_href=None,
    )

    assert "Section 1" in rendered
    assert "Section 2" in rendered
    assert rendered.count('<option value="section-') == 2
    assert "<h3>Question 1</h3>" in rendered
    assert "<h3>Question 2</h3>" in rendered
    assert "<h3>Untitled question</h3>" not in rendered
    assert "Needs source review" in rendered
    placement_context = station_module._placement_context_html(
        rows,
        {
            "occurrence-1": "../quizzes/quiz-0001.html#occurrence-1",
            "occurrence-2": "../quizzes/quiz-0001.html#occurrence-2",
        },
    )
    assert "Midterm · Section 1 · question 1" in placement_context
    assert "Midterm · Section 2 · question 2" in placement_context


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"match_status_code": "direct"}, set()),
        ({"match_status_code": "inferred_exact"}, set()),
        ({"match_status_code": "unresolved"}, {"no_safe"}),
        ({"match_status_code": "unmatched"}, {"no_safe"}),
        ({"match_status_code": "no_safe_library_match"}, {"no_safe"}),
        ({"source_status": "incomplete"}, {"no_safe"}),
        ({"match_status_code": "inferred_similarity"}, {"probable"}),
        ({"match_status_code": "ambiguous"}, {"probable"}),
        ({"match_status_code": "probable_content_match"}, {"probable"}),
        (
            {
                "match_status_code": "inferred_exact",
                "match_explanation": "More than one library candidate remains plausible.",
            },
            {"probable"},
        ),
        ({"match_status_code": "direct", "diagnostic_ids": ["diag-1"]}, set()),
    ],
)
def test_source_review_filter_uses_the_review_queue_classifier(row, expected):
    assert station_module._occurrence_review_lanes(row) == expected
    assert station_module._occurrence_needs_source_review(row) is bool(expected)


def test_source_review_filter_and_named_queues_have_identical_occurrence_scope():
    data = build_quiz_review_station(_load(GOLDEN_MODEL))
    queue_occurrences = {
        row["occurrence_key"]
        for lane in ("no_safe_match", "probable_match")
        for row in data["review_queues"][lane]
    }
    filtered_occurrences = {
        row["occurrence_key"]
        for row in data["question_occurrences"]
        if station_module._occurrence_needs_source_review(row)
    }
    assert filtered_occurrences == queue_occurrences


def test_reading_room_exports_both_review_workbooks_in_one_verified_zip(
    tmp_path: Path,
):
    extraction_dir = tmp_path / "run" / "extraction"
    extraction_dir.mkdir(parents=True)
    detailed_path = extraction_dir / "course__quiz_pool_review.xlsx"
    reviewer_path = extraction_dir / "course__quiz_pool_review_reviewer.xlsx"
    detailed_path.write_bytes(b"detailed-workbook")
    reviewer_path.write_bytes(b"reviewer-workbook")
    model_path = extraction_dir / "course.model.json"
    model_path.write_text(json.dumps(_load(GOLDEN_MODEL)), encoding="utf-8")
    output_dir = tmp_path / "run" / "reading_room"

    write_quiz_review_station(
        model_path,
        output_dir,
        artifact_links={
            "detailed_workbook": f"../extraction/{detailed_path.name}",
            "reviewer_workbook": f"../extraction/{reviewer_path.name}",
        },
    )

    export_path = output_dir / "exports" / "quiz-review-workbooks.zip"
    manifest = _load(output_dir / "station.json")
    index_html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert export_path.is_file()
    with zipfile.ZipFile(export_path) as archive:
        assert archive.namelist() == [detailed_path.name, reviewer_path.name]
        assert archive.read(detailed_path.name) == b"detailed-workbook"
        assert archive.read(reviewer_path.name) == b"reviewer-workbook"
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
    export = manifest["exports"]["workbooks"]
    assert export["path"] == "exports/quiz-review-workbooks.zip"
    assert export["file_count"] == 2
    descriptor = next(
        row for row in manifest["shards"] if row["kind"] == "export:workbooks"
    )
    payload = export_path.read_bytes()
    assert descriptor["bytes"] == len(payload)
    assert descriptor["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "Download all workbooks (.zip)" in index_html
    assert "Detailed Workbook" in index_html
    assert "Reviewer Workbook" in index_html
    assert "Source question records" in index_html
    assert "Reused variant groups" in index_html


@pytest.mark.parametrize(
    "artifact_link",
    [
        "../../outside.json",
        "/absolute/model.json",
        "https://example.invalid/model.json",
        r"..\\extraction\\model.json",
    ],
)
def test_station_rejects_unsafe_artifact_links(tmp_path: Path, artifact_link: str):
    with pytest.raises(ValueError, match="artifact link"):
        build_quiz_review_station(
            _load(GOLDEN_MODEL),
            artifact_links={"model": artifact_link},
            output_dir=tmp_path / "run" / "station",
        )


def test_cli_is_path_safe_and_refuses_an_occupied_output_directory(tmp_path: Path):
    output_dir = tmp_path / "run" / "station"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(GOLDEN_MODEL),
            "--output-dir",
            str(output_dir),
            "--artifact-link",
            "model=../extraction/model.json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary == {
        "format": FORMAT,
        "occurrence_count": 4,
        "output": "index.html",
        "share_safe": False,
        "unique_question_count": 4,
    }
    assert str(GOLDEN_MODEL) not in completed.stdout
    assert str(output_dir) not in completed.stdout

    refused = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(GOLDEN_MODEL),
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "not empty" in refused.stderr


def test_reference_station_owns_authored_and_shared_content_once_and_keeps_occurrences_placement_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(station_module, "QUESTION_SHARD_SIZE", 2)
    monkeypatch.setattr(station_module, "OCCURRENCE_SHARD_SIZE", 2)
    monkeypatch.setattr(station_module, "CATALOG_SHARD_SIZE", 1)
    model = _load(GOLDEN_MODEL)
    model["questions"][0]["prompt"][
        "content"
    ] += "<math><mi>MATHML_SENTINEL</mi></math>"
    model["questions"][0]["feedback"].append(
        {
            "channel": "general",
            "source_kind": "fixture",
            "content": {"format": "html", "content": "FEEDBACK_SENTINEL"},
            "source_evidence_keys": [],
        }
    )
    model["questions"][0]["type_payload"]["raw_response_models"].append(
        {
            "source_kind": "fixture:source-payload",
            "payload": {"answer_source": "SOURCE_PAYLOAD_SENTINEL"},
            "source_evidence_keys": [],
        }
    )
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    output_dir = tmp_path / "room"

    data = write_quiz_review_station(model_path, output_dir)
    manifest = _load(output_dir / "station.json")
    files = [path for path in output_dir.rglob("*") if path.is_file()]
    texts = {path: path.read_text(encoding="utf-8") for path in files}

    assert all(
        key not in row
        for row in data["question_occurrences"]
        for key in ("prompt_preview", "title", "kind", "display_identifier")
    )
    assert all(
        key not in row
        for row in data["question_entities"]
        for key in ("assets", "diagnostics", "provenance")
    )
    assert data["catalogs"]["assets"][0]["source_path"] == (
        "assets/synthetic-diagram.png"
    )

    authored_tokens = [
        "SENTINEL_STEM_DIRECT",
        "SENTINEL_OPTION_ALPHA",
        "SENTINEL_MANUAL_ANSWER",
        "SENTINEL_ACCEPTED_RESPONSE",
        "MATHML_SENTINEL",
        "FEEDBACK_SENTINEL",
        "SOURCE_PAYLOAD_SENTINEL",
    ]
    for token in authored_tokens:
        owners = [
            path.relative_to(output_dir).as_posix()
            for path, text in texts.items()
            if token in text
        ]
        assert len(owners) == 1
        assert owners[0].startswith("questions/questions-")
        assert sum(text.count(token) for text in texts.values()) == 1

    asset_path = "assets/synthetic-diagram.png"
    assert [
        path.relative_to(output_dir).as_posix()
        for path, text in texts.items()
        if asset_path in text
    ] == ["catalogs/assets-0001.html"]
    occurrence_text = "".join(
        text for path, text in texts.items() if path.parent.name == "quizzes"
    )
    assert "SENTINEL_" not in occurrence_text
    assert "MATHML_SENTINEL" not in occurrence_text
    assert "../questions/questions-0001.html#question-" in occurrence_text

    assert len(list((output_dir / "questions").glob("*.html"))) == 2
    assert len(list((output_dir / "quizzes").glob("*.html"))) == 1
    assert {row["entity_key"] for row in manifest["question_entities"]} == {
        row["entity_key"] for row in data["question_entities"]
    }
    assert {row["occurrence_key"] for row in manifest["question_occurrences"]} == {
        row["occurrence_key"] for row in data["question_occurrences"]
    }
    assert all(
        "title" not in row and "prompt" not in row
        for row in manifest["question_entities"]
    )
    assert all(
        "fetch(" not in text and "<script src=" not in text
        for path, text in texts.items()
        if path.suffix == ".html"
    )
    for descriptor in manifest["shards"]:
        payload = (output_dir / descriptor["path"]).read_bytes()
        assert len(payload) == descriptor["bytes"]
        assert hashlib.sha256(payload).hexdigest() == descriptor["sha256"]


def test_sharded_station_materially_reduces_artifact_amplification(tmp_path: Path):
    model = _load(GOLDEN_MODEL)
    large_payload = "<p>AMPLIFICATION_SENTINEL</p><math><mi>x</mi></math>" * 2000
    for index, question in enumerate(model["questions"], start=1):
        question["type_payload"]["raw_response_models"].append(
            {
                "source_kind": "fixture:large-payload",
                "payload": {"variant": index, "authored": large_payload},
                "source_evidence_keys": [],
            }
        )
    model_path = tmp_path / "large.model.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    output_dir = tmp_path / "room"

    data = write_quiz_review_station(model_path, output_dir)
    legacy_bytes = len(
        (
            json.dumps(data, ensure_ascii=False, sort_keys=True)
            + render_quiz_review_station_html(data)
        ).encode("utf-8")
    )
    static_bytes = sum(
        path.stat().st_size for path in output_dir.rglob("*") if path.is_file()
    )
    manifest_bytes = (output_dir / "station.json").stat().st_size
    index_bytes = (output_dir / "index.html").stat().st_size

    # One-question pages intentionally trade modest shared-shell overhead for
    # direct, lazy human navigation while still materially reducing duplication.
    assert static_bytes < legacy_bytes * 0.75
    assert manifest_bytes < legacy_bytes * 0.05
    assert index_bytes < legacy_bytes * 0.05
    assert (
        sum(
            path.read_text(encoding="utf-8").count("AMPLIFICATION_SENTINEL")
            for path in output_dir.rglob("*")
            if path.is_file()
        )
        == 4 * 2000
    )
