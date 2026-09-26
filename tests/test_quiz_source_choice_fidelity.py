"""Synthetic regression evidence for source-ID choice normalization."""
from copy import deepcopy
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from extract_quiz_pool_review import (
    build_payload, build_reviewer_entity_rows, build_reviewer_unresolved_rows,
    extract_source_response_facts, reviewer_answer_key,
    reviewer_response_options,
)
from quiz_contracts import validate_contract
from quiz_normalization import build_normalized_model, source_choice_projection
from quiz_review_projection import build_quiz_review_projection

FIXTURE = ROOT / "tests/fixtures/quiz_source_choice_fidelity"


def normalized(source=FIXTURE, payload=None):
    payload = build_payload(source) if payload is None else payload
    return build_normalized_model(payload, source, source, "directory",
                                  source_lineage_key="cc:lineage:synthetic-choice-fidelity",
                                  run_id="cc:run:synthetic-choice-fidelity")[0]


def source_item(code):
    return next(e for e in ET.parse(FIXTURE / "quiz_d2l_choices.xml").iter("item")
                if e.get("title") == code)


def test_duplicate_labels_keep_distinct_source_ids_and_exact_key():
    payload = build_payload(FIXTURE)
    model = normalized(payload=payload)
    question = next(q for q in model["questions"] if q["title"] == "DUPLICATE")
    options = question["type_payload"]["options"]
    assert [o["correct"] for o in options] == [True, False, False]
    assert [o["extensions"]["coursecraft.source_response_ident"] for o in options] == [
        "DUPLICATE_SOURCE_1", "DUPLICATE_SOURCE_2", "DUPLICATE_SOURCE_3"]
    row = next(r for r in payload["quiz_question_rows"] if r["quiz_item_title"] == "DUPLICATE")
    assert row["choices_text"] == "Same label || Other || Same label"
    assert reviewer_response_options(row) == "A. Same label\nB. Other\nC. Same label"
    assert reviewer_answer_key(row) == "A. Same label"
    assert validate_contract(model, mode="transform") == []


def test_image_only_math_and_literal_delimiter_material_is_not_flattened():
    payload = build_payload(FIXTURE)
    questions = {q["title"]: q for q in normalized(payload=payload)["questions"]}
    images = questions["IMAGES"]["type_payload"]["options"]
    assert len(images) == 2
    assert [o["correct"] for o in images] == [False, True]
    assert images[0]["content"] == {"format": "html", "content": '<p><img src="shape-a.svg" alt=""/></p>', "extensions": {}}
    math = questions["MATH"]["type_payload"]["options"]
    assert len(math) == 3
    assert math[0]["content"]["content"] == '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    assert math[1]["content"]["content"] == "<p>A || B</p>"
    row = next(r for r in payload["quiz_question_rows"] if r["quiz_item_title"] == "IMAGES")
    assert reviewer_response_options(row) == "A. [Image: shape-a.svg]\nB. [Image: shape-b.svg]"
    assert reviewer_answer_key(row) == "B. [Image: shape-b.svg]"
    unmatched = next(r for r in build_reviewer_unresolved_rows(payload) if r["question_number"] == 2)
    assert unmatched["answer_key"] == "B. [Image: shape-b.svg]"
    row = next(r for r in payload["quiz_question_rows"] if r["quiz_item_title"] == "MATH")
    assert reviewer_response_options(row).splitlines() == ["x", "A || B", "Other"]


def test_genuine_multi_select_ordering_and_true_false_keep_their_native_types():
    questions = {q["title"]: q for q in normalized()["questions"]}
    assert questions["MULTI"]["kind"] == "multi_select"
    assert [o["correct"] for o in questions["MULTI"]["type_payload"]["options"]] == [True, True, False]
    assert questions["ORDER"]["kind"] == "ordering"
    assert [(o["option_key"], o["position"]) for o in questions["ORDER"]["type_payload"]["correct_order"]] == [("A", 1), ("B", 2), ("C", 3)]
    assert questions["TF"]["kind"] == "true_false"
    assert [o["correct"] for o in questions["TF"]["type_payload"]["options"]] == [False, True]


def test_prompt_images_and_math_survive_normalization_without_answer_material():
    payload = build_payload(FIXTURE)
    row = next(r for r in payload["quiz_question_rows"] if r["quiz_item_title"] == "DUPLICATE")
    item = source_item("DUPLICATE")
    mattext = item.find("./presentation/material/mattext")
    assert mattext is not None
    markup = '<p>Choose <strong>one</strong>: H<sub>2</sub>O.</p><img src="shape-a.svg" alt="diagram"/><math><mi>x</mi></math>'
    mattext.set("texttype", "text/html")
    mattext.text = markup
    row["source_response_facts"] = extract_source_response_facts(item)
    question = next(q for q in normalized(payload=payload)["questions"] if q["title"] == "DUPLICATE")
    assert question["prompt"] == {"format": "html", "content": markup, "extensions": {}}
    assert "Same label" not in question["prompt"]["content"]


def test_unprojected_native_prompt_media_stays_visible_as_a_build_blocker():
    from quiz_build_support import question_projection_issues

    payload = build_payload(FIXTURE)
    row = next(r for r in payload["quiz_question_rows"] if r["quiz_item_title"] == "DUPLICATE")
    item = source_item("DUPLICATE")
    ET.SubElement(item.find("./presentation/material"), "matimage", {"uri": "shape-a.svg"})
    row["source_response_facts"] = extract_source_response_facts(item)
    question = next(q for q in normalized(payload=payload)["questions"] if q["title"] == "DUPLICATE")
    codes = {code for code, _ in question_projection_issues(question, randomize_answers=False)}
    assert "source_prompt_projection_unresolved" in codes


def test_copied_asset_without_content_reference_is_not_build_ready():
    from quiz_build_support import question_asset_reference_issues

    question = next(q for q in normalized()["questions"] if q["title"] == "DUPLICATE")
    asset = {"entity_key": "cc:asset:synthetic:missing-image", "archive_path": Path("diagram.svg")}
    relationship = {"kind": "uses_asset", "status": "resolved", "from_entity_key": question["entity_key"], "to_entity_key": asset["entity_key"]}
    issues = question_asset_reference_issues([question], [asset], [relationship])
    assert any(code == "question_asset_content_not_projected" for code, _, _ in issues)


@pytest.mark.parametrize("mutation", ["partial-credit", "unknown-id", "duplicate-id", "wrong-cardinality", "wrong-predicate", "unknown-scoring-attribute", "unknown-scoremodel", "setter-child", "duplicate-type-metadata", "missing-condition", "summary-disagreement"])
def test_ambiguous_source_keeps_material_and_facts_but_withholds_keys(mutation):
    item = source_item("DUPLICATE")
    if mutation == "partial-credit":
        item.find(".//setvar").text = "50"
    elif mutation == "unknown-id":
        item.find(".//varequal").text = "ABSENT"
    elif mutation == "duplicate-id":
        labels = list(item.iter("response_label")); labels[1].set("ident", labels[0].get("ident"))
    elif mutation == "wrong-cardinality":
        item.find(".//response_lid").set("rcardinality", "Multiple")
    elif mutation == "wrong-predicate":
        item.find(".//varequal").tag = "vargte"
    elif mutation == "unknown-scoring-attribute":
        item.find(".//varequal").set("index", "2")
    elif mutation == "unknown-scoremodel":
        item.find("resprocessing").set("scoremodel", "UNRECOGNIZED_PROFILE")
    elif mutation == "setter-child":
        ET.SubElement(item.find(".//setvar"), "unexpected").text = "semantic extension"
    elif mutation == "duplicate-type-metadata":
        metadata = item.find(".//qtimetadata")
        metadata.append(deepcopy(metadata[0]))
    elif mutation == "missing-condition":
        # Omitted zero clauses are supplied by the QTI implicit SCORE default.
        # Removing the only positive clause leaves no authoritative full key.
        processing = item.find("resprocessing"); processing.remove(processing[0])
    facts = extract_source_response_facts(item)
    if mutation == "summary-disagreement":
        facts["response_declarations"][0]["response_labels"][0]["source_ident"] = "ABSENT"
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert not projection["recognized"]
    assert projection["reason"]
    assert len(projection["options"]) == 3
    assert all(o["correct"] is None for o in projection["options"])
    assert facts == original


def test_conflicting_same_identity_occurrences_cannot_adopt_first_key(tmp_path):
    tree = ET.parse(FIXTURE / "quiz_d2l_choices.xml")
    section = tree.find(".//section")
    repeated = deepcopy(section[0])
    repeated.set("ident", "REPEATED_ITEM")
    repeated.set("label", "REPEATED_LABEL")
    setters = list(repeated.iter("setvar"))
    setters[0].text, setters[2].text = "0", "100"
    section.append(repeated)
    tree.write(tmp_path / "quiz_d2l_choices.xml", encoding="utf-8")
    payload = build_payload(tmp_path)
    # Duplicate keyed labels produce identical legacy key strings, so a
    # display-string comparison cannot detect this source conflict.
    first, last = payload["quiz_question_rows"][0], payload["quiz_question_rows"][-1]
    assert first["correct_answer"] == last["correct_answer"]
    model = normalized(tmp_path, payload)
    question = next(q for q in model["questions"] if q["title"] == "DUPLICATE")
    assert question["type_payload"]["options"] == []
    assert question["scoring"]["state"] == "unresolved"
    assert any(d["code"] == "source_choice_occurrence_conflict" and question["entity_key"] in d["entity_keys"] for d in model["diagnostics"])
    raw = [r for r in question["type_payload"]["raw_response_models"] if r["source_kind"] == "d2l_qti_response_facts/0"]
    assert [r["payload"] for r in raw] == [first["source_response_facts"], last["source_response_facts"]]
    entity = next(r for r in build_reviewer_entity_rows(build_quiz_review_projection(model, include_content=True)) if r["title"] == "DUPLICATE")
    assert entity["answer_key_state"].startswith("DO NOT APPROVE")
    assert entity["answer_key"].startswith("[Unresolved")


def test_missing_facts_never_recovers_authoritative_key_from_display_text():
    payload = build_payload(FIXTURE)
    for row in payload["quiz_question_rows"]:
        row.pop("source_response_facts")
    model = normalized(payload=payload)
    assert all(q["type_payload"]["options"] == [] for q in model["questions"])
    assert all(q["scoring"]["state"] == "unresolved" for q in model["questions"])
    assert sum(d["code"] == "source_choice_projection_unresolved" for d in model["diagnostics"]) == 6


def test_rich_projection_does_not_remint_source_identity():
    payload = build_payload(FIXTURE)
    model = normalized(payload=payload)
    legacy = deepcopy(payload)
    for row in legacy["quiz_question_rows"]:
        row.pop("source_response_facts")
        row.pop("source_choice_display")
        row["choices_text"] = "Changed display, same native identity"
    legacy_model = normalized(payload=legacy)
    assert [q["entity_key"] for q in model["questions"]] == [q["entity_key"] for q in legacy_model["questions"]]
    assert [r["relationship_key"] for r in model["relationships"]] == [r["relationship_key"] for r in legacy_model["relationships"]]


@pytest.mark.parametrize("default,recognized", [("0", True), ("100", False), (None, True)])
def test_sparse_single_choice_honors_documented_score_defaults(default, recognized):
    item = source_item("DUPLICATE")
    processing = item.find("resprocessing")
    for condition in list(processing)[1:]:
        processing.remove(condition)
    if default is not None:
        outcome = ET.Element("outcomes")
        ET.SubElement(outcome, "decvar", varname="SCORE", vartype="Integer", defaultval=default, minvalue="0", maxvalue="100")
        processing.insert(0, outcome)
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert projection["recognized"] is recognized
    assert [o["correct"] for o in projection["options"]] == ([True, False, False] if recognized else [None] * 3)
    assert facts == original


@pytest.mark.parametrize("declaration", ["absent", "empty-outcomes", "empty-decvar", "named-score", "named-que-score"])
def test_defaulted_single_choice_profiles_preserve_facts(declaration):
    item = source_item("DUPLICATE")
    processing = item.find("resprocessing")
    for condition in list(processing)[1:]:
        processing.remove(condition)
    setter = processing.find(".//setvar")
    setter.attrib.clear()
    if declaration != "absent":
        outcomes = ET.Element("outcomes")
        if declaration != "empty-outcomes":
            variable = ET.SubElement(outcomes, "decvar")
            if declaration in {"named-score", "named-que-score"}:
                name = "SCORE" if declaration == "named-score" else "que_score"
                variable.set("varname", name)
                setter.set("varname", name)
        processing.insert(0, outcomes)
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert projection["recognized"], projection["reason"]
    assert [o["correct"] for o in projection["options"]] == [True, False, False]
    assert facts == original


@pytest.mark.parametrize("profile", ["undeclared-named-score", "conflicting-outcome", "duplicate-outcome", "noninteger-type", "positive-minimum"])
def test_implicit_defaults_never_override_conflicting_profiles(profile):
    item = source_item("DUPLICATE")
    processing = item.find("resprocessing")
    for condition in list(processing)[1:]:
        processing.remove(condition)
    if profile == "undeclared-named-score":
        processing.find(".//setvar").set("varname", "que_score")
    else:
        outcomes = ET.Element("outcomes")
        variable = ET.SubElement(outcomes, "decvar")
        if profile == "conflicting-outcome":
            variable.set("varname", "que_score")
        elif profile == "duplicate-outcome":
            ET.SubElement(outcomes, "decvar")
        elif profile == "noninteger-type":
            variable.set("vartype", "String")
        elif profile == "positive-minimum":
            variable.set("minvalue", "1")
        processing.insert(0, outcomes)
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert not projection["recognized"]
    assert all(o["correct"] is None for o in projection["options"])
    assert facts == original


@pytest.mark.parametrize("action", ["Set", "Add"])
def test_multi_select_honors_implicit_score_without_fallback(action):
    item = source_item("MULTI")
    processing = item.find("resprocessing")
    processing.remove(processing.find("outcomes"))
    processing.remove(processing.findall("respcondition")[-1])
    setter = processing.find(".//setvar")
    setter.set("action", action)
    setter.text = "1" if action == "Add" else "100"
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert projection["recognized"], projection["reason"]
    assert [o["correct"] for o in projection["options"]] == [True, True, False]
    assert facts == original


@pytest.mark.parametrize("mutation", ["default100", "continue", "fallback-first", "bounds", "mixed-variables"])
def test_multi_select_cannot_ignore_outcomes_or_continuation(mutation):
    item = source_item("MULTI")
    processing = item.find("resprocessing")
    conditions = processing.findall("respcondition")
    if mutation == "default100":
        processing.remove(conditions[-1])
        processing.find(".//decvar").set("defaultval", "100")
    elif mutation == "continue":
        conditions[0].set("continue", "Yes")
    elif mutation == "fallback-first":
        processing.remove(conditions[-1]); processing.insert(1, conditions[-1])
    elif mutation == "bounds":
        processing.find(".//decvar").set("maxvalue", "50")
    elif mutation == "mixed-variables":
        conditions[-1].find("setvar").set("varname", "que_score")
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert not projection["recognized"]
    assert all(o["correct"] is None for o in projection["options"])
    assert facts == original


@pytest.mark.parametrize("mutation", ["default5", "continue-no", "missing-outcomes", "early-terminal"])
def test_ordering_requires_complete_native_counter_profile(mutation):
    item = source_item("ORDER")
    processing = item.find("resprocessing")
    if mutation == "default5":
        processing.find(".//decvar[@varname='D2L_Incorrect']").set("defaultval", "5")
    elif mutation == "continue-no":
        processing.find("respcondition").set("continue", "No")
    elif mutation == "missing-outcomes":
        processing.remove(processing.find("outcomes"))
    elif mutation == "early-terminal":
        terminal = processing.findall("respcondition")[-2]
        processing.remove(terminal); processing.insert(1, terminal)
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert not projection["recognized"]
    assert all(o["position"] is None for o in projection["options"])
    assert facts == original


def test_native_ordering_corpus_and_explicit_continuation_are_supported():
    path = ROOT / "tests/fixtures/quiz_xml/ordering_response_group/quiz_d2l_5166.xml"
    item = next(ET.parse(path).iter("item"))
    projection = source_choice_projection(extract_source_response_facts(item))
    assert projection["recognized"], projection["reason"]
    assert sorted(o["position"] for o in projection["options"]) == [1, 2, 3, 4]
    for condition in item.findall("resprocessing/respcondition"):
        condition.set("continue", "Yes")
    projection = source_choice_projection(extract_source_response_facts(item))
    assert projection["recognized"], projection["reason"]


def test_ordering_declared_counters_allow_omitted_zero_defaults_and_set_action():
    item = source_item("ORDER")
    for variable in item.findall("resprocessing/outcomes/decvar"):
        variable.attrib.pop("defaultval")
    for setter in item.findall("resprocessing/respcondition/setvar"):
        if setter.get("action") == "Set":
            setter.attrib.pop("action")
    facts = extract_source_response_facts(item)
    original = deepcopy(facts)
    projection = source_choice_projection(facts)
    assert projection["recognized"], projection["reason"]
    assert [o["position"] for o in projection["options"]] == [1, 2, 3]
    assert facts == original
