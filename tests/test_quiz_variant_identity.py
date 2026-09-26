"""Authored-variant identity invariants for the normalized quiz model.

The governing rule for this tranche is recorded in
``workspace/review/quiz_binder_fable/31_UNBIND_FIDELITY_CORRECTION_ACTIVATION_2026-08-03.md``:

    Every set of rows sharing an entity key must resolve to exactly one
    authored-variant fingerprint.

Multiple occurrences may legitimately share one entity key.  Divergence in
placement-scoped facts -- points, quiz, section, ordinal, pool, draw -- never
violates the rule.  Divergence in authored semantics always does.

The fingerprint here is deliberately test-owned.  It states the property
independently of the implementation so the invariant cannot drift with the code
it constrains.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

from extract_quiz_pool_review import build_payload
from quiz_normalization import (
    AUTHORED_VARIANT_BASIS,
    apply_authored_variant_identity,
    build_normalized_model,
    enrich_payload,
)


COLLISION_FIXTURE = "library_alias_variant_collision"

# Payload fields that are scoped to one placement rather than to the authored
# question.  Letting any of these reach the fingerprint would split every reuse
# group in the corpus while still passing a small fixture.
PLACEMENT_SCOPED_FIELDS = (
    "question_weight",
    "correct_response_ids",
    "quiz_file",
    "quiz_title",
    "quiz_item_number",
    "quiz_item_ident",
    "quiz_item_label",
    "quiz_section_item_number",
    "section_ident",
    "section_path",
    "section_title",
    "section_source_locator",
    "pool_ident",
    "pool_path",
    "pool_title",
    "evidence_level",
    "match_basis",
    "match_score",
    "match_note",
    "match_candidates_json",
    "draw_count",
    "is_random_draw_section",
)


def normalized_fixture(name: str) -> dict[str, Any]:
    fixture = FIXTURE_ROOT / name
    payload = build_payload(fixture)
    enrich_payload(payload, fixture)
    model, _source_meta = build_normalized_model(
        payload,
        fixture,
        fixture,
        "directory",
        source_lineage_key=f"cc:lineage:fixture:{name}",
        run_id=f"cc:run:fixture:{name}",
    )
    return model


def _text(value: Any) -> str:
    """Whitespace- and entity-normalize without casefolding.

    Casefolding under-splits: a corpus check found question texts that differ
    only in capitalization and are genuinely distinct authored variants.
    """
    return " ".join(html.unescape("" if value is None else str(value)).split())


def _image_binding(payload: dict[str, Any]) -> tuple[str, ...]:
    for field in ("question_image_paths", "question_image_refs"):
        raw = payload.get(field)
        if isinstance(raw, str) and raw.strip():
            return tuple(
                _text(part)
                for part in re.split(r"\s*\|\|\s*|\s*;\s*|\n", raw)
                if part.strip()
            )
        if isinstance(raw, list) and raw:
            return tuple(_text(part) for part in raw)
    primary = payload.get("question_primary_image_path")
    return (_text(primary),) if primary else ()


def authored_variant_fingerprint(payload: dict[str, Any]) -> str:
    """Digest the authored semantics of one canonical extractor row.

    Scoring is excluded on purpose: in Brightspace the weight belongs to the
    quiz item, and every reuse group in the ANAT control varies in points.
    """
    basis = [
        _text(payload.get("question_type")),
        _text(payload.get("question_text")),
        tuple(
            _text(choice)
            for choice in re.split(r"\s*\|\|\s*", str(payload.get("choices_text") or ""))
            if choice.strip()
        ),
        _text(payload.get("correct_answer")),
        _text(payload.get("fill_in_blank_answers_text")),
        _text(payload.get("matching_prompts_text")),
        _text(payload.get("matching_options_text")),
        _image_binding(payload),
    ]
    serialized = json.dumps(basis, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def placed_questions(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Questions that came from a quiz placement, excluding library-only records."""
    return [
        question
        for question in model.get("questions", [])
        if not (question.get("extensions") or {}).get("library_only")
    ]


def library_questions(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        question
        for question in model.get("questions", [])
        if (question.get("extensions") or {}).get("library_only")
    ]


def canonical_rows(question: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row["payload"]
        for row in (question.get("type_payload") or {}).get("raw_response_models") or []
        if str(row.get("source_kind", "")).startswith("canonical-extractor-row:")
        and isinstance(row.get("payload"), dict)
    ]


def variant_collisions(model: dict[str, Any]) -> dict[str, set[str]]:
    """Return entity keys whose rows disagree on authored semantics."""
    collisions: dict[str, set[str]] = {}
    for question in model.get("questions", []):
        digests = {
            authored_variant_fingerprint(row) for row in canonical_rows(question)
        }
        if len(digests) > 1:
            collisions[str(question.get("entity_key"))] = digests
    return collisions


CLEAN_FIXTURES = [
    "authored_variant_reuse_points",
    "quiz_only_inline_tf",
    "mixed_inline_itemref_and_root_bank",
    "occurrence_scoped_source_facts",
    "short_answer_projection",
    "duplicate_pool_and_similarity",
    "answer_key_types",
    "matching_response_groups",
    "ordering_response_group",
    "fill_in_the_blanks_multiblank",
    "long_answer_with_answer_key",
    "identity_collisions",
    "math_rendering",
]


@pytest.mark.parametrize("fixture_name", CLEAN_FIXTURES)
def test_entity_keys_resolve_to_one_authored_variant(fixture_name: str) -> None:
    model = normalized_fixture(fixture_name)
    collisions = variant_collisions(model)
    assert not collisions, (
        f"{fixture_name}: entity keys carry more than one authored variant: "
        f"{sorted(collisions)}"
    )


def test_library_alias_variants_do_not_collapse() -> None:
    model = normalized_fixture(COLLISION_FIXTURE)
    collisions = variant_collisions(model)
    assert not collisions, (
        "library-matched quiz items with different answers and images collapsed "
        f"into one entity: {sorted(collisions)}"
    )
    assert len(placed_questions(model)) == 3
    keyed = sorted(
        option["content"]["content"]
        for question in placed_questions(model)
        for option in question["type_payload"]["options"]
        if option.get("correct")
    )
    assert keyed == ["<p>Alpha</p>", "<p>Beta</p>", "<p>Gamma</p>"], (
        "each variant must keep its own answer key, not inherit the first one"
    )


def test_every_observed_identifier_survives_in_source_aliases() -> None:
    """The contract requires all observed D2L identifiers to remain as aliases."""
    model = normalized_fixture(COLLISION_FIXTURE)
    for question in placed_questions(model):
        namespaces = {
            alias["namespace"] for alias in question["identity"]["source_aliases"]
        }
        assert {"d2l.qmd_globalid", "d2l.quiz_item.ident"} <= namespaces
        assert {
            "d2l.library.qmd_globalid",
            "d2l.library.question.ident",
        } <= namespaces, "the matched library identifiers were dropped from identity"

    own = {
        alias["value"]
        for question in placed_questions(model)
        for alias in question["identity"]["source_aliases"]
        if alias["namespace"] == "d2l.qmd_globalid"
    }
    assert len(own) == 3, "each source item must keep its own global identifier"


def test_library_identity_never_names_the_question() -> None:
    """A library match is evidence about an item, never the item's name."""
    model = normalized_fixture(COLLISION_FIXTURE)
    library_values = {
        alias["value"]
        for question in placed_questions(model)
        for alias in question["identity"]["source_aliases"]
        if alias["namespace"].startswith("d2l.library.")
    }
    assert library_values, "fixture must actually carry a library match"
    library_keys = {q["entity_key"] for q in library_questions(model)}
    for question in placed_questions(model):
        rows = canonical_rows(question)
        assert rows and rows[0].get("pool_question_globalid")
        assert question["entity_key"] not in library_keys


def test_collision_fixture_carries_three_distinct_authored_variants() -> None:
    """The fixture must stay a real collision case regardless of the fix."""
    model = normalized_fixture(COLLISION_FIXTURE)
    rows = [row for question in model["questions"] for row in canonical_rows(question)]
    assert len(rows) == 3
    assert {_text(row.get("correct_answer")) for row in rows} == {
        "Alpha",
        "Beta",
        "Gamma",
    }
    assert len({_image_binding(row) for row in rows}) == 3
    assert len({authored_variant_fingerprint(row) for row in rows}) == 3
    # Placement-scoped divergence is present too, and must never be the reason
    # two rows are called different questions.
    assert len({_text(row.get("question_weight")) for row in rows}) == 2


def test_placement_scoped_fields_do_not_change_the_authored_fingerprint() -> None:
    """Perturbing placement facts must leave the authored fingerprint alone.

    ``correct_response_ids`` is the dangerous one: it embeds the quiz item's own
    identifiers, so admitting it would silently split every reuse group.
    """
    model = normalized_fixture(COLLISION_FIXTURE)
    row = canonical_rows(model["questions"][0])[0]
    baseline = authored_variant_fingerprint(row)
    for field in PLACEMENT_SCOPED_FIELDS:
        perturbed = dict(row)
        perturbed[field] = "perturbed-placement-value"
        assert authored_variant_fingerprint(perturbed) == baseline, (
            f"{field} leaked into the authored-variant fingerprint"
        )


def test_authored_fields_do_change_the_fingerprint() -> None:
    model = normalized_fixture(COLLISION_FIXTURE)
    row = canonical_rows(model["questions"][0])[0]
    baseline = authored_variant_fingerprint(row)
    for field in (
        "question_type",
        "question_text",
        "choices_text",
        "correct_answer",
        "question_image_paths",
    ):
        perturbed = dict(row)
        perturbed[field] = "changed-authored-value"
        assert authored_variant_fingerprint(perturbed) != baseline, (
            f"{field} is authored semantics but did not change the fingerprint"
        )


def _same_as_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in model["relationships"]
        if row.get("kind") == "same_as"
        and row.get("source_kind") == "authored_variant_equivalence"
    ]


def test_every_reviewable_question_carries_an_authored_variant_fingerprint() -> None:
    model = normalized_fixture(COLLISION_FIXTURE)
    for question in placed_questions(model):
        bases = {
            row["basis"] for row in question["identity"]["content_fingerprints"]
        }
        assert "authored variant" in bases


def test_library_records_never_join_quiz_review_equivalence() -> None:
    """Library payloads are parsed, but the review path stays placed-only.

    Content sameness and "which record is the page a reviewer is sent to" are
    separate decisions. A library record may now be fingerprinted and linked to
    its duplicates for curation, but it must never enter a quiz-review class,
    where it could become the canonical face of a placed question.
    """
    model = normalized_fixture("long_answer_with_answer_key")
    library_keys = {question["entity_key"] for question in library_questions(model)}
    assert library_keys, "fixture must retain its question-library coverage record"

    # Payloads are parsed now, so a library record does carry the fingerprint.
    for question in library_questions(model):
        bases = {
            row["basis"] for row in question["identity"]["content_fingerprints"]
        }
        assert AUTHORED_VARIANT_BASIS in bases
        assert question["extensions"]["payload_state"] == "parsed"

    # The guarantee that still matters: no quiz-review equivalence edge touches
    # a library record, in either direction.
    touching_library = [
        row
        for row in _same_as_rows(model)
        if row.get("from_entity_key") in library_keys
        or row.get("to_entity_key") in library_keys
    ]
    assert touching_library == []


def test_library_duplicates_are_linked_on_their_own_channel() -> None:
    """Duplication inside a library is a curation signal, not a review class."""
    model = normalized_fixture("library_alias_variant_collision")
    library_keys = {question["entity_key"] for question in library_questions(model)}
    library_edges = [
        row
        for row in model["relationships"]
        if row.get("kind") == "same_as"
        and row.get("source_kind") == "library_variant_equivalence"
    ]
    # Every library-channel edge stays entirely inside the library population.
    for row in library_edges:
        assert row["from_entity_key"] in library_keys
        assert row["to_entity_key"] in library_keys

    review_edges = _same_as_rows(model)
    assert not any(
        row["from_entity_key"] in library_keys or row["to_entity_key"] in library_keys
        for row in review_edges
    )


def test_library_payloads_are_parsed_by_the_quiz_item_parser() -> None:
    """A library record's answer key comes from the same parser, not a second one."""
    model = normalized_fixture(COLLISION_FIXTURE)
    library = library_questions(model)
    assert len(library) == 1
    payload = library[0]["type_payload"]
    keyed = [
        option["content"]["content"]
        for option in payload["options"]
        if option.get("correct")
    ]
    assert keyed == ["<p>Alpha</p>"], "the library record must carry its own answer key"
    assert [option["content"]["content"] for option in payload["options"]] == [
        "<p>Alpha</p>",
        "<p>Beta</p>",
        "<p>Gamma</p>",
    ]
    markers = {
        row["source_kind"]
        for row in payload["raw_response_models"]
        if str(row["source_kind"]).startswith(("question-library-row:", "canonical-"))
    }
    assert markers == {"question-library-row:Multiple Choice"}


def test_long_answer_guidance_is_typed_and_identity_bearing() -> None:
    """Different evaluator guidance must not become a resolved reuse claim."""
    model = normalized_fixture("long_answer_with_answer_key")
    first = placed_questions(model)[0]
    manual = first["type_payload"].get("manual_answer_key")
    assert manual and "Biofilms are a community" in manual["content"]

    second = deepcopy(first)
    second["entity_key"] = f"{first['entity_key']}-different-guidance"
    second["identity"]["source_aliases"] = []
    second["type_payload"]["manual_answer_key"] = {
        "format": "plain_text",
        "content": "Synthetic, materially different evaluator guidance.",
        "extensions": {},
    }
    model["questions"] = [first, second]
    model["relationships"] = [
        row
        for row in model["relationships"]
        if row.get("source_kind") != "authored_variant_equivalence"
    ]
    for question in model["questions"]:
        question["identity"]["content_fingerprints"] = [
            row
            for row in question["identity"]["content_fingerprints"]
            if row.get("basis") != AUTHORED_VARIANT_BASIS
        ]

    apply_authored_variant_identity(model)

    assert _same_as_rows(model) == []
    digests = {
        row["digest"]
        for question in model["questions"]
        for row in question["identity"]["content_fingerprints"]
        if row.get("basis") == AUTHORED_VARIANT_BASIS
    }
    assert len(digests) == 2


def test_similarity_only_library_match_remains_proposed() -> None:
    model = normalized_fixture("duplicate_pool_and_similarity")
    matches = [
        row
        for row in model["relationships"]
        if row.get("kind") == "same_as"
        and row.get("source_kind") == "question_library_match"
    ]
    exact = [
        row
        for row in matches
        if row.get("attributes", {}).get("evidence_level") == "inferred_exact"
    ]
    similarity = [
        row
        for row in matches
        if row.get("attributes", {}).get("evidence_level")
        == "inferred_similarity"
    ]
    assert exact and {row["status"] for row in exact} == {"resolved"}
    assert similarity and {row["status"] for row in similarity} == {"proposed"}
    assert all("match_score" in row["attributes"] for row in similarity)
    assert all(isinstance(row["attributes"].get("candidates"), list) for row in similarity)


def test_distinct_variants_are_not_declared_equivalent() -> None:
    model = normalized_fixture(COLLISION_FIXTURE)
    assert _same_as_rows(model) == [], (
        "three different answers and images must not be called the same variant"
    )
    digests = {
        row["digest"]
        for question in placed_questions(model)
        for row in question["identity"]["content_fingerprints"]
        if row["basis"] == "authored variant"
    }
    assert len(digests) == 3


def test_equivalence_uses_a_stable_representative_and_n_minus_one_records() -> None:
    """A class of N entities costs N-1 records, not an all-pairs graph."""
    model = normalized_fixture(COLLISION_FIXTURE)
    # Force a three-member class by giving every question identical semantics.
    model["questions"] = placed_questions(model)
    first = model["questions"][0]
    for question in model["questions"][1:]:
        question["prompt"] = deepcopy(first["prompt"])
        question["type_payload"]["options"] = deepcopy(
            first["type_payload"]["options"]
        )
    for question in model["questions"]:
        question["identity"]["content_fingerprints"] = [
            row
            for row in question["identity"]["content_fingerprints"]
            if row["basis"] != "authored variant"
        ]
    model["relationships"] = [
        row for row in model["relationships"] if row.get("kind") != "uses_asset"
    ]
    apply_authored_variant_identity(model)

    rows = _same_as_rows(model)
    keys = sorted(str(q["entity_key"]) for q in model["questions"])
    assert len(rows) == len(keys) - 1
    representative = keys[0]
    assert {row["to_entity_key"] for row in rows} == {representative}
    assert {row["from_entity_key"] for row in rows} == set(keys[1:])
    for row in rows:
        assert row["status"] == "resolved"
        assert row["attributes"]["representative_entity_key"] == representative
        assert row["attributes"]["class_size"] == len(keys)
        assert row["attributes"]["basis"] == "authored variant"


def test_equivalence_is_deterministic_across_repeated_normalization() -> None:
    first = normalized_fixture(COLLISION_FIXTURE)
    second = normalized_fixture(COLLISION_FIXTURE)
    assert [q["entity_key"] for q in first["questions"]] == [
        q["entity_key"] for q in second["questions"]
    ]
    assert first["relationships"] == second["relationships"]
    assert [q["identity"]["content_fingerprints"] for q in first["questions"]] == [
        q["identity"]["content_fingerprints"] for q in second["questions"]
    ]


def test_library_matching_is_not_authored_variant_equivalence() -> None:
    """The two kinds of sameness must stay distinguishable in the record."""
    model = normalized_fixture(COLLISION_FIXTURE)
    same_as = [row for row in model["relationships"] if row.get("kind") == "same_as"]
    kinds = {row["source_kind"] for row in same_as}
    assert "question_library_match" in kinds, "the library match must be recorded"
    assert not (
        {"authored_variant_equivalence"} & kinds
    ), "three different answers are not one authored variant"

    matches = [row for row in same_as if row["source_kind"] == "question_library_match"]
    library_keys = {q["entity_key"] for q in library_questions(model)}
    placed_keys = {q["entity_key"] for q in placed_questions(model)}
    assert len(matches) == 3
    for row in matches:
        assert row["from_entity_key"] in placed_keys
        assert row["to_entity_key"] in library_keys
        assert row["attributes"]["evidence_level"]


def test_primary_image_path_binds_when_no_image_list_is_recorded() -> None:
    """The fallback path must still be identity-bearing, not silently ignored."""
    base = {"question_type": "Multiple Choice", "question_text": "Same prompt"}
    without_image = authored_variant_fingerprint(dict(base))
    with_image = authored_variant_fingerprint(
        {**base, "question_primary_image_path": "variant-a.svg"}
    )
    other_image = authored_variant_fingerprint(
        {**base, "question_primary_image_path": "variant-b.svg"}
    )
    assert len({without_image, with_image, other_image}) == 3


def _collapse(model: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the pre-correction collapse so the guards can be exercised."""
    collapsed = deepcopy(model)
    placed = placed_questions(collapsed)
    survivor = placed[0]
    for question in placed[1:]:
        survivor["type_payload"]["raw_response_models"].extend(
            question["type_payload"]["raw_response_models"]
        )
    collapsed["questions"] = [survivor, *library_questions(collapsed)]
    return collapsed


def test_variant_collision_report_names_the_affected_entity() -> None:
    from quiz_normalization import variant_collision_report

    clean = variant_collision_report(normalized_fixture(COLLISION_FIXTURE))
    assert clean["variant_collision_count"] == 0
    assert clean["approval_safe"] is True
    assert clean["state"] == "clean"

    collapsed = variant_collision_report(_collapse(normalized_fixture(COLLISION_FIXTURE)))
    assert collapsed["variant_collision_count"] == 1
    assert collapsed["approval_safe"] is False
    assert collapsed["state"] == "collapsed_question_variants"
    assert collapsed["entities"][0]["authored_variant_count"] == 3
    assert collapsed["entities"][0]["occurrence_count"] == 3


def test_readiness_blocks_approval_when_variants_collapsed(tmp_path: Path) -> None:
    from quiz_authoring_readiness import analyze_authoring_readiness

    model_path = tmp_path / "collapsed.model.json"
    model_path.write_text(
        json.dumps(_collapse(normalized_fixture(COLLISION_FIXTURE))), encoding="utf-8"
    )
    report = analyze_authoring_readiness(model_path)
    codes = {issue["code"] for issue in report["issues"]}
    assert "question_variant_collision_unresolved" in codes
    assert report["ready"] is False


def test_promotion_refuses_a_collapsed_entity() -> None:
    """A decision cannot be attributed when one record holds several questions."""
    import inspect

    import quiz_promote_revisions

    source = inspect.getsource(quiz_promote_revisions.promote_revisions)
    assert "question_variant_collision_unresolved" in source
    assert "collided_entity_keys" in source


def test_reader_refuses_a_single_authoritative_key_for_a_collapsed_entity() -> None:
    from quiz_review_station import build_quiz_review_station

    collapsed = _collapse(normalized_fixture(COLLISION_FIXTURE))
    data = build_quiz_review_station(collapsed)
    entity = data["question_entities"][0]
    assert entity["fidelity"]["variant_conflict"] is True
    assert entity["fidelity"]["authored_variant_count"] == 3

    clean = build_quiz_review_station(normalized_fixture(COLLISION_FIXTURE))
    assert all(
        row["fidelity"]["variant_conflict"] is False
        for row in clean["question_entities"]
    )


def test_reading_room_shows_placement_points_and_a_varies_note() -> None:
    from quiz_review_station import _placement_points_note, _points_display

    varied = {
        "maximum_points": "1",
        "extensions": {"observed_maximum_points": ["1", "2.5"]},
    }
    single = {"maximum_points": "1", "extensions": {"observed_maximum_points": ["1"]}}
    equivalent = {
        "maximum_points": "1.0",
        "extensions": {"observed_maximum_points": ["1", "1.000000000"]},
    }
    assert "varies by placement" in _points_display(varied)
    assert _points_display(single) == "1"
    assert _points_display(equivalent) == "1"
    assert "Points differ by placement" in _placement_points_note(varied)
    assert _placement_points_note(single) == ""
    assert _placement_points_note(equivalent) == ""


def test_real_export_reuse_panel_shows_each_members_placement_points(
    tmp_path: Path,
) -> None:
    """Exercise export parsing through authored equivalence and static rendering."""
    from quiz_review_station import build_quiz_review_station, write_quiz_review_station

    model = normalized_fixture("authored_variant_reuse_points")
    relationships = _same_as_rows(model)
    # Four members, so representative selection and the N-1 edge count are
    # distinguishable from the degenerate pair case.
    assert len(relationships) == 3

    data = build_quiz_review_station(model)
    reused = [
        row
        for row in data["question_entities"]
        if row["authored_variant"]["reused"]
    ]
    assert len(reused) == 4
    # The fourth member sits in a random-draw section that declares the award
    # and its item carries no weight of its own, so it contributes no
    # placement points rather than inventing one.
    assert {
        tuple(str(value) for value in member["point_values"])
        for member in reused[0]["authored_variant"]["members"]
    } == {("1",), ("2.5",), ("4",), ("",)}

    model_path = tmp_path / "reuse.model.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    reading_room = tmp_path / "reading_room"
    write_quiz_review_station(model_path, reading_room)
    pages = sorted((reading_room / "questions").glob("*.html"))
    assert len(pages) == 4
    for page in pages:
        rendered = page.read_text(encoding="utf-8")
        assert "Same authored content across 4 source question records" in rendered
        assert "Placement points differ across these source records" in rendered
        assert "Placement points:</strong> 1" in rendered
        assert "Placement points:</strong> 2.5" in rendered
        assert "Placement points:</strong> 4" in rendered
        # The draw-section member's item declares no weight of its own.
        assert "Placement points:</strong> not recorded" in rendered
        assert rendered.count("../questions/questions-") >= 4


def test_library_records_are_coverage_entities_not_reviewable_questions() -> None:
    from quiz_review_projection import build_quiz_review_projection

    model = normalized_fixture(COLLISION_FIXTURE)
    assert len(library_questions(model)) == 1
    projection = build_quiz_review_projection(model)
    keys = {row["question_key"] for row in projection["question_entities"]}
    assert keys == {q["entity_key"] for q in placed_questions(model)}
    assert projection["summary"]["library_question_count"] == 1
    assert projection["summary"]["library_matched_question_count"] == 3


def test_asset_reference_spelling_is_not_authored_variance() -> None:
    """Brightspace spells one reference several ways inside a single export.

    College Algebra carries backslash-and-space and slash-and-percent forms of
    the same five images on two placements of one question. Treating the
    spelling as content reported them as different authored questions.
    """
    from quiz_normalization import authored_variant_row_digest

    base = {"question_type": "Multiple Choice", "question_text": "Same prompt"}
    windows_style = authored_variant_row_digest(
        {**base, "question_image_refs": "csfiles\\home_dir\\Math Images\\a.png"}
    )
    url_style = authored_variant_row_digest(
        {**base, "question_image_refs": "csfiles/home_dir/Math%20Images/a.png"}
    )
    other_image = authored_variant_row_digest(
        {**base, "question_image_refs": "csfiles/home_dir/Math%20Images/b.png"}
    )
    assert windows_style == url_style
    assert windows_style != other_image


def test_reuse_class_larger_than_two_uses_n_minus_one_edges() -> None:
    """Representative selection is only meaningful above a pair.

    BIOL 1055 produces classes of three and four; every earlier fixture and
    corpus topped out at two, where "lowest key wins" and "N-1 edges" are
    indistinguishable from "one edge".
    """
    model = normalized_fixture("authored_variant_reuse_points")
    placed = placed_questions(model)
    rows = _same_as_rows(model)
    keys = sorted(str(q["entity_key"]) for q in placed)
    assert len(keys) == 4
    assert len(rows) == len(keys) - 1 == 3
    representative = keys[0]
    assert {row["to_entity_key"] for row in rows} == {representative}
    assert {row["from_entity_key"] for row in rows} == set(keys[1:])
    assert all(row["attributes"]["class_size"] == 4 for row in rows)


def test_draw_section_weight_is_preserved_on_the_structure() -> None:
    """A draw can declare the points it awards; the model must keep that.

    Every item observed in the corpus also carries its own weight and the two
    never disagree, so this is rebuild fidelity rather than a points
    correction: without it the structure that declares the award cannot be
    reconstructed from the model.
    """
    model = normalized_fixture("authored_variant_reuse_points")
    draws = [row for row in model["structures"] if row.get("kind") == "draw"]
    assert len(draws) == 1
    assert draws[0]["extensions"]["section_weight"] == "7.5"

    plain = [
        row
        for row in model["structures"]
        if row.get("kind") != "draw" and row.get("source_kind") == "section"
    ]
    assert all(
        row["extensions"].get("section_weight") in (None, "") for row in plain
    ), "an unweighted section must not invent a weight"
