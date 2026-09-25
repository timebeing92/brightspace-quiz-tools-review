#!/usr/bin/env python3
"""Library for building Brightspace course-component import packages.

Generalized 2026-06-12 from the DSW 821 whole-package builder
(`build_dsw821_summer_a_starter.py`), whose generated package imported into
Brightspace successfully — its emitted field set is the proven-sufficient
baseline here. Course-specific conventions were stripped; everything is
driven by a normalized JSON contract instead of hardcoded content.

Element shapes were cross-checked against real export evidence (CHEM 1020
2021, DSW 821 2026). One deliberate extension beyond the DSW baseline:
discussion topics may carry a grade join (properties/score_out_of +
grade_item_id), which the DSW build never used but real exports prove
(CHEM 1020 graded topics). Generated resource codes survive Brightspace
import/re-export (confirmed: the DSW base code reappears verbatim in the
2026 re-export), so packages built here can be round-trip diffed by code.

Emits only the payload files the contract asks for, registers exactly those
in the manifest, and writes a key->resource_code mapping for review. Favor
small element bundles over whole-course builds (repo ground rule 9).
"""
from __future__ import annotations

import copy
import html
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import xml.etree.ElementTree as ET

from course_artifact_contracts import canonical_sha256, sha256_file, validate_contract

D2L_NS = "http://desire2learn.com/xsd/d2lcp_v2p0"
IMSCP_NS = "http://www.imsglobal.org/xsd/imscp_v1p1"
IMSMD_NS = "http://www.imsglobal.org/xsd/imsmd_rootv1p2p1"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Register canonical prefixes so the serialized manifest matches what D2L itself
# emits (d2l_2p0 / imsmd / default imscp). Without these, ElementTree invents
# ns0/ns1 prefixes and D2L's Course Package Converter fails to recognize the
# package as D2L ("Plugin not found for conversion"). These three calls were
# present in the field-proven DSW 821 builder and dropped during generalization;
# restoring them is what makes the package importable. (D2L does not require the
# scorm_1p2 namespace, and the original did not register it.)
ET.register_namespace("", IMSCP_NS)
ET.register_namespace("d2l_2p0", D2L_NS)
ET.register_namespace("imsmd", IMSMD_NS)

QUICKLINK_TYPES = {
    "dropbox_link": ("dropbox", "D2L.LE.Dropbox.Dropbox"),
    "discussion_link": ("discuss", "D2L.LE.Discussions.DiscussionTopic"),
    "checklist_link": ("checklist", "D2L.LE.Checklist.ChecklistEntity"),
    "quiz_link": ("quiz", "D2L.LE.Quizzing.Quiz"),
}


class ResourceCodeFactory:
    """Mints sequential resource codes on a base GUID, the DSW-proven pattern."""

    def __init__(self, base_code: str, start: int = 900000) -> None:
        self.base_code = base_code
        self.counter = start

    def next(self) -> str:
        self.counter += 1
        return f"{self.base_code}-{self.counter}"


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper() or "ITEM"


def format_points(value: float | int | str) -> str:
    return f"{float(value):.9f}"


def load_contract(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_package_path(value: str, *, label: str) -> str:
    """Return a normalized package-relative POSIX path or fail closed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if "\\" in value:
        raise ValueError(f"{label} must use forward slashes: {value}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
        raise ValueError(f"{label} must stay inside the package: {value}")
    return relative.as_posix()


def resolve_source_path(source_root: Path, value: str, *, label: str) -> Path:
    relative = safe_package_path(value, label=label)
    root = source_root.resolve()
    target = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its source root: {value}") from exc
    return target


def deterministic_zip(package_dir: Path, zip_path: Path) -> None:
    """Write byte-stable ZIP metadata for an already deterministic file set."""
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in sorted(path for path in package_dir.rglob("*") if path.is_file()):
            relative = file_path.relative_to(package_dir).as_posix()
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, file_path.read_bytes())


def replace_output(temp_root: Path, output_dir: Path, *, force: bool) -> None:
    if output_dir.exists() and not force:
        raise ValueError(f"Output directory already exists: {output_dir}. Use force.")
    backup: Path | None = None
    try:
        if output_dir.exists():
            backup = output_dir.with_name(f".{output_dir.name}.backup-{uuid.uuid4().hex}")
            output_dir.rename(backup)
        temp_root.rename(output_dir)
    except Exception:
        if backup is not None and backup.exists() and not output_dir.exists():
            backup.rename(output_dir)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup)


# --- payload serializers (shapes per DSW-proven baseline + export evidence) ----


def build_grades_xml(grade_items: list[dict], codes: dict[str, str]) -> ET.Element:
    root = ET.Element("grades", {"xmlns:d2l_2p0": D2L_NS})
    config = ET.SubElement(root, "configuration")
    ET.SubElement(
        config,
        "calculation_options",
        {
            "auto_update_final_grade": "1",
            "grading_system": "0",
            "include_empty_grades_in_final": "0",
            "release_adjusted_grade": "1",
            "auto_release_final_grade": "1",
        },
    )
    ET.SubElement(
        config,
        "org_unit_display_options",
        {
            "decimals_displayed": "2",
            "show_points": "1",
            "show_colour": "0",
            "show_symbol": "1",
            "show_weighted": "1",
            "decimals_displayed_my_grades": "2",
            "max_characters": "50",
            "show_final_grade_calc": "0",
        },
    )
    ET.SubElement(root, "categories")
    items = ET.SubElement(root, "items")
    for index, item in enumerate(grade_items, start=1):
        item_el = ET.SubElement(
            items,
            "item",
            {
                "id": str(400 + index),
                "identifier": str(700000 + index),
                "dates_in_calendar": "false",
                "resource_code": codes[item["key"]],
            },
        )
        ET.SubElement(item_el, "name").text = item["name"]
        ET.SubElement(item_el, "short_name").text = item.get("short_name", "")
        ET.SubElement(item_el, "sort_order").text = str(index)
        ET.SubElement(item_el, "show_average").text = "false"
        ET.SubElement(item_el, "show_distribution").text = "false"
        ET.SubElement(item_el, "description", {"text_type": "text/html", "is_displayed": "false"})
        ET.SubElement(item_el, "type_id").text = "1"
        ET.SubElement(item_el, "is_active").text = "true"
        scoring = ET.SubElement(item_el, "scoring")
        ET.SubElement(scoring, "can_exceed_weight").text = "false"
        ET.SubElement(scoring, "out_of").text = str(item["points"])
        ET.SubElement(scoring, "is_bonus").text = "false"
        ET.SubElement(scoring, "max_grade").text = str(item["points"])
        ET.SubElement(scoring, "exclude_from_final_grade_calc").text = (
            "true" if item.get("exclude_from_final") else "false"
        )
        ET.SubElement(scoring, "is_milestone_grade").text = "false"
    return root


def build_simple_rubrics_xml(rubrics: list[dict], codes: dict[str, str]) -> ET.Element:
    """Minimal rubric payload (DSW-proven shape). For richer rubric work use
    rubric_package_lib; this exists so a dropbox+grade+rubric bundle is
    self-contained."""
    root = ET.Element("rubrics", {"schemaversion": "v2011"})
    level_id = 800000
    for rubric_index, rubric in enumerate(rubrics, start=1):
        rubric_el = ET.SubElement(
            root,
            "rubric",
            {
                "id": str(rubric_index),
                "resource_code": codes[rubric["key"]],
                "name": rubric["name"],
                "type": "1",
                "scoring_method": "3",
                "display_levels_in_des_order": "True",
                "state": "0",
                "visibility": "0",
                "uses_overall_score": "True",
                "has_manual_alignment": "False",
                "score_visible_to_assessed_users": "True",
                "enabled_feedback_copy": "False",
                "usage_restrictions": "Competency,ePortfolio",
            },
        )
        description = ET.SubElement(rubric_el, "description", {"text_type": "text"})
        ET.SubElement(description, "text")
        criteria_groups = ET.SubElement(rubric_el, "criteria_groups")
        criteria_group = ET.SubElement(criteria_groups, "criteria_group", {"name": "Criteria", "sort_order": "1"})
        level_set = ET.SubElement(criteria_group, "level_set")
        levels_el = ET.SubElement(level_set, "levels")
        level_ids = []
        for sort_order, level in enumerate(rubric["levels"], start=1):
            level_id += 1
            level_ids.append(str(level_id))
            ET.SubElement(
                levels_el,
                "level",
                {"name": level["name"], "sort_order": str(sort_order), "level_id": str(level_id)},
            )
        criteria_el = ET.SubElement(criteria_group, "criteria")
        for sort_order, criterion in enumerate(rubric["criteria"], start=1):
            criterion_el = ET.SubElement(criteria_el, "criterion", {"name": criterion["name"], "sort_order": str(sort_order)})
            cells = ET.SubElement(criterion_el, "cells")
            for level_index, level in enumerate(rubric["levels"]):
                cell = ET.SubElement(
                    cells,
                    "cell",
                    {"level_id": level_ids[level_index], "cell_value": format_points(level["value"])},
                )
                desc = ET.SubElement(cell, "description", {"text_type": "text/html"})
                text_el = ET.SubElement(desc, "text")
                cell_text = criterion.get("descriptions", [""] * len(rubric["levels"]))[level_index]
                text_el.text = f"<p>{html.escape(cell_text)}</p>" if cell_text else ""
                feedback = ET.SubElement(cell, "feedback", {"text_type": "text"})
                ET.SubElement(feedback, "text")
        overall = ET.SubElement(rubric_el, "overall_level_set")
        overall_levels = ET.SubElement(overall, "overall_levels")
        for sort_order, (level, level_id_str) in enumerate(zip(reversed(rubric["levels"]), reversed(level_ids)), start=1):
            overall_level = ET.SubElement(
                overall_levels,
                "overall_level",
                {"name": level["name"], "sort_order": str(sort_order), "range_start_value": format_points(level["value"])},
            )
            desc = ET.SubElement(overall_level, "description", {"text_type": "text"})
            ET.SubElement(desc, "text")
            feedback = ET.SubElement(overall_level, "feedback", {"text_type": "text"})
            ET.SubElement(feedback, "text")
    return root


def build_dropbox_xml(folders: list[dict], codes: dict[str, str], rubric_ids: dict[str, str]) -> ET.Element:
    root = ET.Element("dropbox")
    for index, folder in enumerate(folders, start=1):
        attrib = {
            "name": folder["name"],
            "id": str(500 + index),
            "submission_type": str(folder.get("submission_type", "4")),
            "completion_type": "0",
            "allowable_file_type": "0",
            "folder_type": "2",
            "sort_order": str(index),
            "folder_is_retricted": "false",
            "files_per_submission": "0",
            "submissions": "1",
            "ai_human_origin": "0",
            "resource_code": codes[folder["key"]],
            "is_hidden": "true" if folder.get("is_hidden") else "false",
            "is_anonymous": "false",
        }
        if folder.get("grade_item"):
            attrib["out_of"] = format_points(folder.get("points", 0))
            attrib["grade_item"] = codes[folder["grade_item"]]
        folder_el = ET.SubElement(root, "folder", attrib)
        if folder.get("rubric"):
            associations = ET.SubElement(folder_el, f"{{{D2L_NS}}}associations")
            rubric = ET.SubElement(associations, f"{{{D2L_NS}}}rubric", {"isDefault": "false"})
            rubric.text = rubric_ids[folder["rubric"]]
        ET.SubElement(folder_el, "blti", {"xmlns": "asset_processors"})
        instructions = ET.SubElement(folder_el, "instructions", {"text_type": "text/html"})
        ET.SubElement(instructions, "text").text = folder.get("instructions_html", "")
        if folder.get("date_due"):
            ET.SubElement(folder_el, "date_due").text = folder["date_due"]
    return root


def build_discussion_xml(discussion: dict, codes: dict[str, str]) -> ET.Element:
    root = ET.Element("discussion", {"xmlns:d2l_2p0": D2L_NS})
    forum = ET.SubElement(root, "forum", {"id": "610", "resource_code": codes[discussion["key"]]})
    props = ET.SubElement(forum, "properties")
    for name, value in [
        ("allow_anon", "False"),
        ("display_order", "1"),
        ("is_hidden", "False"),
        ("requires_approval", "False"),
        ("must_post_to_participate", "False"),
        ("show_description_in_topics", "False"),
        ("has_group_restrictions", "False"),
        ("uses_group_restrictions", "False"),
    ]:
        ET.SubElement(props, name).text = value
    content = ET.SubElement(forum, "content")
    ET.SubElement(content, "title").text = discussion["forum_title"]
    topics_el = ET.SubElement(forum, "topics")
    for index, topic in enumerate(discussion["topics"], start=1):
        topic_el = ET.SubElement(topics_el, "topic", {"id": str(700 + index), "resource_code": codes[topic["key"]]})
        props = ET.SubElement(topic_el, "properties")
        ET.SubElement(props, "ai_human_origin").text = "0"
        ET.SubElement(props, "allow_anon").text = "False"
        ET.SubElement(props, "display_order").text = str(index)
        ET.SubElement(props, "is_hidden").text = "False"
        ET.SubElement(props, "requires_approval").text = "False"
        ET.SubElement(props, "must_post_to_participate").text = "False"
        if topic.get("score_out_of") is not None:
            # Evidence-backed extension (CHEM 1020 graded topics); not in the
            # DSW-proven baseline — verify on first real import of a graded topic.
            ET.SubElement(props, "score_out_of").text = str(topic["score_out_of"])
        ET.SubElement(props, "is_auto_score").text = "False"
        if topic.get("grade_item"):
            ET.SubElement(props, "grade_item_id").text = codes[topic["grade_item"]]
        ET.SubElement(props, "include_nonscored_values").text = "False"
        ET.SubElement(props, "rating_type_id").text = "0"
        ET.SubElement(props, "has_group_restrictions").text = "False"
        ET.SubElement(props, "uses_group_restrictions").text = "False"
        content = ET.SubElement(topic_el, "content")
        ET.SubElement(content, "title").text = topic["title"]
        ET.SubElement(content, "description").text = topic.get("description_html", "")
    return root


def build_checklists_xml(checklists: list[dict], codes: dict[str, str], code_factory: ResourceCodeFactory) -> ET.Element:
    root = ET.Element("checklists")
    for index, checklist in enumerate(checklists, start=1):
        checklist_el = ET.SubElement(
            root,
            "checklist",
            {"id": str(800 + index), "resource_code": codes[checklist["key"]], "display_in_new_window": "False"},
        )
        ET.SubElement(checklist_el, "name").text = checklist["name"]
        ET.SubElement(checklist_el, "description", {"text_type": "text/html"})
        for cat_order, category in enumerate(checklist.get("categories", []), start=1):
            category_el = ET.SubElement(checklist_el, "category", {"sort_order": str(cat_order)})
            name_el = ET.SubElement(category_el, "name", {"text_type": "text/plain"})
            name_el.text = category["name"]
            ET.SubElement(category_el, "description", {"text_type": "text/html"})
            for sort_order, item in enumerate(category.get("items", []), start=1):
                item_el = ET.SubElement(
                    category_el,
                    "item",
                    {
                        "sort_order": str(sort_order),
                        "in_schedule": "true" if item.get("date_end") else "false",
                        "resource_code": code_factory.next(),
                    },
                )
                item_name = ET.SubElement(item_el, "name", {"text_type": "text/plain"})
                item_name.text = item["name"]
                ET.SubElement(item_el, "description", {"text_type": "text/html"})
                if item.get("date_end"):
                    ET.SubElement(item_el, "date_end").text = item["date_end"]
    return root


# --- manifest -------------------------------------------------------------------


def make_resource(parent: ET.Element, identifier: str, material_type: str, href: str, link_target: str = "", title: str = "") -> None:
    ET.SubElement(
        parent,
        "resource",
        {
            "identifier": identifier,
            "type": "webcontent",
            f"{{{D2L_NS}}}material_type": material_type,
            f"{{{D2L_NS}}}link_target": link_target,
            "href": href,
            "title": title,
        },
    )


def build_manifest_xml(contract: dict, codes: dict[str, str], payload_files: dict[str, str], code_factory: ResourceCodeFactory) -> ET.Element:
    package_meta = contract.get("package", {})
    title_text = package_meta.get("title", "Generated Component Package")
    manifest = ET.Element(
        "manifest",
        {"identifier": package_meta.get("manifest_identifier", "D2L_COMPONENT_PACKAGE"), "xmlns": IMSCP_NS},
    )
    metadata = ET.SubElement(manifest, "metadata")
    lom = ET.SubElement(metadata, f"{{{IMSMD_NS}}}lom")
    general = ET.SubElement(lom, f"{{{IMSMD_NS}}}general")
    title = ET.SubElement(general, f"{{{IMSMD_NS}}}title")
    lang = ET.SubElement(title, f"{{{IMSMD_NS}}}langstring", {"xml:lang": "en-us"})
    lang.text = title_text
    keyword = ET.SubElement(general, f"{{{IMSMD_NS}}}keyword")
    keyword_lang = ET.SubElement(keyword, f"{{{IMSMD_NS}}}langstring", {"xml:lang": "en-us"})
    keyword_lang.text = package_meta.get("keyword", title_text)
    ET.SubElement(general, f"{{{IMSMD_NS}}}language").text = "en-us"

    organizations = ET.SubElement(manifest, "organizations", {"default": "d2l_orgs"})
    organization = ET.SubElement(organizations, "organization", {"identifier": "d2l_org"})
    resources = ET.SubElement(manifest, "resources")

    for material_type, href in payload_files.items():
        make_resource(resources, f"res_{material_type}", material_type, href)

    item_counter = [2000]

    def item_attrs(extra: dict | None = None) -> dict:
        item_counter[0] += 1
        attrs = {
            f"{{{D2L_NS}}}id": str(item_counter[0]),
            f"{{{D2L_NS}}}resource_code": code_factory.next(),
            "description": "",
            "completion_type": "2",
            f"{{{D2L_NS}}}ai_human_origin": "0",
        }
        if extra:
            attrs.update(extra)
        return attrs

    for module in contract.get("modules", []):
        module_slug = slugify(module["title"])
        module_item = ET.SubElement(
            organization,
            "item",
            {"identifier": f"ITEM_{module_slug}", "identifierref": f"RES_CONTENT_{module_slug}", **item_attrs()},
        )
        ET.SubElement(module_item, "title").text = module["title"]
        make_resource(resources, f"RES_CONTENT_{module_slug}", "contentmodule", "")

        for entry in module.get("items", []):
            entry_type = entry["type"]
            entry_title = entry["title"]
            entry_slug = f"{module_slug}_{slugify(entry_title)}"
            extra: dict = {}
            if entry.get("date_due"):
                extra["date_due"] = entry["date_due"]
            if entry_type == "html_topic":
                resource_id = f"RES_CONTENT_{entry_slug}"
                child = ET.SubElement(
                    module_item,
                    "item",
                    {"identifier": f"ITEM_{entry_slug}", "identifierref": resource_id, **item_attrs(extra)},
                )
                ET.SubElement(child, "title").text = entry_title
                make_resource(resources, resource_id, "content", entry["href"])
            elif entry_type in QUICKLINK_TYPES:
                link_type, resource_type_key = QUICKLINK_TYPES[entry_type]
                target_code = codes[entry["ref"]] if entry.get("ref") else entry["resource_code"]
                href = (
                    "/d2l/common/dialogs/quickLink/quickLink.d2l"
                    f"?ou={{orgUnitId}}&type={link_type}&rCode={target_code}"
                )
                extra["resource_type_key"] = resource_type_key
                resource_id = f"RES_CONTENT_{entry_slug}"
                child = ET.SubElement(
                    module_item,
                    "item",
                    {"identifier": f"ITEM_{entry_slug}", "identifierref": resource_id, **item_attrs(extra)},
                )
                ET.SubElement(child, "title").text = entry_title
                make_resource(resources, resource_id, "contentlink", href, link_target="_self")
            else:
                raise ValueError(f"Unsupported module item type: {entry_type}")
    return manifest


def build_orgunit_xml(contract: dict) -> ET.Element:
    package_meta = contract.get("package", {})
    org = ET.Element(
        "orgunit",
        {"identifier": package_meta.get("orgunit_identifier", "PLACEHOLDER_ORGUNIT")},
    )
    ET.SubElement(org, "name").text = package_meta.get("title", "Generated Component Package")
    ET.SubElement(org, "code").text = package_meta.get("orgunit_code", "PLACEHOLDER")
    return org


# --- assembly --------------------------------------------------------------------


def assign_codes(contract: dict, code_factory: ResourceCodeFactory) -> dict[str, str]:
    codes: dict[str, str] = {}

    def claim(key: str) -> None:
        if key in codes:
            raise ValueError(f"Duplicate contract key: {key}")
        codes[key] = code_factory.next()

    for item in contract.get("grade_items", []):
        claim(item["key"])
    for rubric in contract.get("rubrics", []):
        claim(rubric["key"])
    for folder in contract.get("dropbox_folders", []):
        claim(folder["key"])
    for discussion in contract.get("discussions", []):
        claim(discussion["key"])
        for topic in discussion.get("topics", []):
            claim(topic["key"])
    for checklist in contract.get("checklists", []):
        claim(checklist["key"])
    return codes


def write_xml(root: ET.Element, path: Path) -> None:
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_component_package(
    contract: dict,
    output_dir: Path,
    source_dir: Path | None = None,
    force: bool = False,
    producer_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Build the package described by the contract into output_dir/package.

    source_dir is the base for html_topic entries that use `html_file`
    (copied into the package); entries with `html_content` are written
    directly. Each html_topic must give `filename` (package-relative href).
    Optional top-level `support_assets` rows copy checksummed files from the
    same source root to explicit package-relative targets.
    """
    output_dir = output_dir.resolve()
    source_root = (source_dir or Path(".")).resolve()
    if output_dir.exists() and not force:
        raise ValueError(f"Output directory already exists: {output_dir}. Use force.")

    contract_sha256 = canonical_sha256(contract)
    contract = copy.deepcopy(contract)
    package_meta = contract.get("package", {})
    explicit_base_code = bool(package_meta.get("base_resource_code"))
    base_code = package_meta.get("base_resource_code") or str(uuid.uuid4()).upper()

    reserved_targets = {
        "imsmanifest.xml",
        "orgunitconfig/orgunitconfig.xml",
    }
    if contract.get("grade_items"):
        reserved_targets.add("grades_d2l.xml")
    if contract.get("rubrics"):
        reserved_targets.add("rubrics_d2l.xml")
    if contract.get("dropbox_folders"):
        reserved_targets.add("dropbox_d2l.xml")
    if contract.get("checklists"):
        reserved_targets.add("checklist_d2l.xml")
    reserved_targets.update(
        f"discussion_d2l_{index}.xml"
        for index, _discussion in enumerate(contract.get("discussions", []), start=1)
    )

    html_sources: dict[str, Path | None] = {}
    for module in contract.get("modules", []):
        for entry in module.get("items", []):
            if entry["type"] != "html_topic":
                continue
            href = safe_package_path(entry["filename"], label=f"html_topic {entry['title']!r} filename")
            if href in reserved_targets:
                raise ValueError(f"Duplicate package target: {href}")
            reserved_targets.add(href)
            entry["href"] = href
            has_content = entry.get("html_content") is not None
            has_file = bool(entry.get("html_file"))
            if has_content == has_file:
                raise ValueError(f"html_topic {entry['title']!r} needs exactly one of html_content or html_file")
            source: Path | None = None
            if has_file:
                source = resolve_source_path(
                    source_root,
                    entry["html_file"],
                    label=f"html_topic {entry['title']!r} html_file",
                )
                if not source.is_file():
                    raise FileNotFoundError(f"HTML topic source does not exist: {entry['html_file']}")
            html_sources[href] = source

    support_assets: list[dict[str, Any]] = []
    for index, asset in enumerate(contract.get("support_assets", [])):
        try:
            source_value = asset["source"]
            target_value = asset["target"]
            role = asset["role"]
            expected_sha256 = asset["sha256"]
        except KeyError as exc:
            raise ValueError(f"support_assets[{index}] is missing {exc.args[0]}") from exc
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"support_assets[{index}].role must be non-empty text")
        if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError(f"support_assets[{index}].sha256 must be a lowercase SHA-256 digest")
        source = resolve_source_path(source_root, source_value, label=f"support_assets[{index}].source")
        target = safe_package_path(target_value, label=f"support_assets[{index}].target")
        if not source.is_file():
            raise FileNotFoundError(f"Support asset source does not exist: {source_value}")
        if target in reserved_targets:
            raise ValueError(f"Duplicate package target: {target}")
        actual_sha256 = sha256_file(source)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"Support asset checksum mismatch for {source_value}: expected {expected_sha256}, got {actual_sha256}"
            )
        reserved_targets.add(target)
        support_assets.append(
            {
                "source": source_value,
                "target": target,
                "role": role,
                "sha256": actual_sha256,
                "bytes": source.stat().st_size,
                "extensions": {},
                "_source_path": source,
            }
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.build-", dir=output_dir.parent))
    package_dir = temp_root / "package"
    package_dir.mkdir(parents=True)
    try:
        code_factory = ResourceCodeFactory(base_code)
        codes = assign_codes(contract, code_factory)

        payload_files: dict[str, str] = {"orgunitconfig": r"orgunitconfig\orgunitconfig.xml"}
        write_xml(build_orgunit_xml(contract), package_dir / "orgunitconfig" / "orgunitconfig.xml")

        if contract.get("grade_items"):
            write_xml(build_grades_xml(contract["grade_items"], codes), package_dir / "grades_d2l.xml")
            payload_files["d2lgrades"] = "grades_d2l.xml"
        rubric_ids: dict[str, str] = {}
        if contract.get("rubrics"):
            write_xml(build_simple_rubrics_xml(contract["rubrics"], codes), package_dir / "rubrics_d2l.xml")
            payload_files["d2lrubrics"] = "rubrics_d2l.xml"
            rubric_ids = {rubric["key"]: str(index) for index, rubric in enumerate(contract["rubrics"], start=1)}
        if contract.get("dropbox_folders"):
            write_xml(
                build_dropbox_xml(contract["dropbox_folders"], codes, rubric_ids),
                package_dir / "dropbox_d2l.xml",
            )
            payload_files["d2ldropbox"] = "dropbox_d2l.xml"
        for index, discussion in enumerate(contract.get("discussions", []), start=1):
            write_xml(build_discussion_xml(discussion, codes), package_dir / f"discussion_d2l_{index}.xml")
            payload_files["d2ldiscussion"] = f"discussion_d2l_{index}.xml"
        if contract.get("checklists"):
            write_xml(
                build_checklists_xml(contract["checklists"], codes, code_factory),
                package_dir / "checklist_d2l.xml",
            )
            payload_files["d2lchecklist"] = "checklist_d2l.xml"

        for module in contract.get("modules", []):
            for entry in module.get("items", []):
                if entry["type"] != "html_topic":
                    continue
                href = entry["href"]
                target = package_dir / Path(*PurePosixPath(href).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry.get("html_content") is not None:
                    target.write_text(entry["html_content"], encoding="utf-8")
                else:
                    shutil.copyfile(html_sources[href], target)

        support_receipts: list[dict[str, Any]] = []
        for asset in support_assets:
            target = package_dir / Path(*PurePosixPath(asset["target"]).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset["_source_path"], target)
            support_receipts.append({key: value for key, value in asset.items() if not key.startswith("_")})

        write_xml(build_manifest_xml(contract, codes, payload_files, code_factory), package_dir / "imsmanifest.xml")

        mapping_lines = ["# Component Package Mapping", "", f"- Base resource code: `{base_code}`", ""]
        mapping_lines.extend(f"- `{key}` -> `{code}`" for key, code in sorted(codes.items()))
        mapping_lines.append("")
        (temp_root / "component_mapping.md").write_text("\n".join(mapping_lines), encoding="utf-8")

        zip_path = temp_root / "package.zip"
        deterministic_zip(package_dir, zip_path)

        role_by_path = {
            "imsmanifest.xml": "manifest",
            "orgunitconfig/orgunitconfig.xml": "orgunitconfig",
            "grades_d2l.xml": "grades",
            "rubrics_d2l.xml": "rubrics",
            "dropbox_d2l.xml": "dropbox",
            "checklist_d2l.xml": "checklist",
            **{f"discussion_d2l_{index}.xml": "discussion" for index, _ in enumerate(contract.get("discussions", []), start=1)},
            **{path: "html_topic" for path in html_sources},
            **{asset["target"]: asset["role"] for asset in support_receipts},
        }
        package_files = []
        for file_path in sorted(path for path in package_dir.rglob("*") if path.is_file()):
            relative = file_path.relative_to(package_dir).as_posix()
            package_files.append(
                {
                    "path": relative,
                    "role": role_by_path.get(relative, "package_support"),
                    "bytes": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                    "extensions": {},
                }
            )
        receipt = {
            "schema": "coursecraft.component_package_receipt/1",
            "package_title": package_meta.get("title", "Generated Component Package"),
            "base_resource_code": base_code,
            "deterministic": explicit_base_code,
            "contract_sha256": contract_sha256,
            "producer_files": [],
            "codes": dict(sorted(codes.items())),
            "support_assets": support_receipts,
            "package_files": package_files,
            "zip": {
                "path": "package.zip",
                "role": "package_zip",
                "bytes": zip_path.stat().st_size,
                "sha256": sha256_file(zip_path),
                "extensions": {},
            },
            "extensions": {
                "live_brightspace_operations": "not_performed",
                "contract": copy.deepcopy(contract.get("extensions", {})),
            },
        }
        seen_producers: set[Path] = set()
        for producer in [Path(__file__).resolve(), *(producer_paths or [])]:
            producer = producer.resolve()
            if producer in seen_producers:
                continue
            seen_producers.add(producer)
            try:
                display_path = producer.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                display_path = producer.name
            receipt["producer_files"].append(
                {
                    "path": display_path,
                    "role": "producer",
                    "bytes": producer.stat().st_size,
                    "sha256": sha256_file(producer),
                    "extensions": {},
                }
            )
        receipt_issues = [issue for issue in validate_contract(receipt) if issue.severity == "error"]
        if receipt_issues:
            raise ValueError("Component package receipt failed validation: " + "; ".join(issue.message for issue in receipt_issues))
        (temp_root / "component_package_receipt.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        replace_output(temp_root, output_dir, force=force)
    except Exception:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        raise

    return {
        "output_dir": output_dir,
        "package_dir": output_dir / "package",
        "zip_path": output_dir / "package.zip",
        "mapping_path": output_dir / "component_mapping.md",
        "receipt_path": output_dir / "component_package_receipt.json",
        "codes": codes,
    }
