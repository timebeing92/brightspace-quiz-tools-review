from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from quiz_binder_station import build_station_data, write_station  # noqa: E402


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quiz_binder" / "golden_course.model.json"
REGISTRY = REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "quiz_build_capabilities.json"
SCRIPT = REPO_ROOT / "scripts" / "quiz_binder_station.py"
SENTINELS = {
    "SENTINEL_STEM_DIRECT",
    "SENTINEL_OPTION_ALPHA",
    "SENTINEL_MANUAL_ANSWER",
    "SENTINEL_ACCEPTED_RESPONSE",
    "SENTINEL_PROPOSED_REVISION",
}


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.external_assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "a" and values.get("href"):
            self.hrefs.append(str(values["href"]))
        if tag in {"script", "link", "img"} and values.get("src"):
            self.external_assets.append(str(values["src"]))
        if tag == "link" and values.get("href"):
            self.external_assets.append(str(values["href"]))


def test_station_is_self_contained_accessible_and_content_minimized(tmp_path: Path) -> None:
    data = write_station(FIXTURE, REGISTRY, tmp_path)
    station_json = (tmp_path / "station.json").read_text(encoding="utf-8")
    station_html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert data["route_status"] == "local_only"
    for sentinel in SENTINELS:
        assert sentinel not in station_json
        assert sentinel not in station_html
    assert "does not parse XML" in station_html
    assert 'id="plain-toggle"' in station_html
    assert 'aria-pressed="false"' in station_html
    assert ":focus-visible" in station_html
    assert "prefers-reduced-motion" in station_html
    assert "overflow-x:auto" in station_html
    assert "strict-model route remains local-only" in station_html
    assert "Brightspace-ready" not in station_html

    parser = StructureParser()
    parser.feed(station_html)
    assert len(parser.ids) == len(set(parser.ids))
    internal_targets = {value[1:] for value in parser.hrefs if value.startswith("#")}
    assert internal_targets <= set(parser.ids)
    assert parser.external_assets == []


def test_station_source_has_no_xml_parser_dependency() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "xml.etree" not in source
    assert "common_xml" not in source
    assert "extract_quiz_pool_review" not in source


def test_station_renders_content_minimized_exclusion_reason_and_next_action(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "promotion.receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "summary": {
                    "accepted_change_count": 1,
                    "applied_change_count": 0,
                    "excluded_change_count": 1,
                },
                "excluded": [
                    {
                        "annotation_id": "ann.synthetic",
                        "target_entity_key": "cc:question:fixture:binder-direct",
                        "field_path": "/prompt/content",
                        "reason": "question_instance_not_build_approved",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    data = write_station(
        FIXTURE,
        REGISTRY,
        tmp_path / "station",
        promotion_receipt_path=receipt_path,
    )
    station_html = (tmp_path / "station" / "index.html").read_text(encoding="utf-8")

    assert data["promotion_exclusions"][0]["reason"] == (
        "question_instance_not_build_approved"
    )
    assert "Excluded changes and next actions" in station_html
    assert "question_instance_not_build_approved" in station_html
    assert "separately authorized build-trial route" in station_html
    for sentinel in SENTINELS:
        assert sentinel not in station_html


def test_station_uses_disambiguating_key_suffix_and_plain_value_labels() -> None:
    model = json.loads(FIXTURE.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    question = model["questions"][0]
    question["identity"]["permanent_code"] = None
    question["entity_key"] = (
        "cc:question:lineage-unresolved-example:source-4c153323db154ea767e1a5ca"
    )

    data = build_station_data(model, registry)
    row = data["questions"][0]

    assert row["display_identifier"] == "source-4c153323db154ea767e1a5ca"
    assert row["match_state_label"] == "Direct library link"
    assert row["build_support_label"] == "Verified for rebuild (L4)"
