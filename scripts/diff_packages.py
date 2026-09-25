#!/usr/bin/env python3
"""Round-trip diff for Brightspace quiz packages.

Compares two packages — typically a generated package (side A) and a post-import
re-export of it (side B) — and reports object matches, ID mappings, broken or
changed joins, draw-count changes, settings changes, and missing assets.

Brightspace reassigns idents/labels/resource codes on import, so those raw IDs
are never treated as identity. Questions match first by author-controlled
qmd_displayid (the permanent question code), then qmd_globalid, then conservative
title/type/text signals. Raw ID changes are mappings, not failures. Matches that
cannot be resolved unambiguously are reported as ambiguous, never guessed.

Evidence base: docs/reference/Brightspace_Quiz_XML_Reference_Bundle_2026-04-02/
(example_generated_* vs example_exported_quiz_d2l_163030.xml shows titles
surviving import verbatim while every ident/label is rewritten).

Usage:
    python3 scripts/diff_packages.py /path/to/generated_pkg /path/to/reexport
    python3 scripts/diff_packages.py a.zip b.zip --label-a generated --label-b reimport
    python3 scripts/diff_packages.py a.zip whole-export.zip --quiz-file-b quiz_d2l_123.xml
    python3 scripts/diff_packages.py A B --fail-on-break
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from common_xml import clean, local_name
from quiz_build_support import package_member_path

URL_SCHEMES = ("http://", "https://", "data:", "mailto:", "javascript:", "#", "//")
MAX_ZIP_MEMBERS = 100_000
MAX_ZIP_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024


def norm_title(value: str) -> str:
    return re.sub(r"\s+", " ", clean(value))


def metadata_value(elem: ET.Element, label: str) -> str:
    for field in elem.iter():
        if local_name(field.tag) != "qti_metadatafield":
            continue
        field_label = ""
        field_entry = ""
        for child in field:
            if local_name(child.tag) == "fieldlabel":
                field_label = clean(child.text)
            elif local_name(child.tag) == "fieldentry":
                field_entry = clean(child.text)
        if field_label == label:
            return field_entry
    return ""


def text_signature(item: ET.Element) -> str:
    """Conservative content signature: first presentation mattext, tags stripped,
    entities unescaped, whitespace collapsed."""
    for elem in item.iter():
        if local_name(elem.tag) != "presentation":
            continue
        for mattext in elem.iter():
            if local_name(mattext.tag) != "mattext":
                continue
            raw = clean(mattext.text)
            if not raw:
                continue
            stripped = re.sub(r"<[^>]+>", " ", html.unescape(raw))
            normalized = re.sub(r"\s+", " ", stripped).strip()
            if normalized:
                return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return ""


def element_asset_refs(elem: ET.Element) -> list[str]:
    refs: set[str] = set()
    for mattext in elem.iter():
        if local_name(mattext.tag) != "mattext":
            continue
        raw = clean(mattext.text)
        if not raw:
            continue
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html.unescape(raw)):
            value = ref.strip()
            if value and not value.lower().startswith(URL_SCHEMES):
                refs.add(value.split("?")[0].split("#")[0])
    return sorted(refs)


# --- package loading ---------------------------------------------------------


def load_package_root(path: Path, holder: list) -> Path:
    """Return a directory root for a package path; extract ZIPs to a temp dir
    kept alive in `holder`."""
    if path.is_dir():
        return path
    if path.is_file() and zipfile.is_zipfile(path):
        tmp = tempfile.TemporaryDirectory(prefix="diff_pkg_")
        holder.append(tmp)
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise SystemExit(f"error: ZIP contains too many members: {path}")
            if sum(info.file_size for info in members) > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise SystemExit(f"error: ZIP expands beyond the safety limit: {path}")
            for info in members:
                member = PurePosixPath(info.filename.replace("\\", "/"))
                mode = info.external_attr >> 16
                if member.is_absolute() or ".." in member.parts or (mode & 0o170000) == 0o120000:
                    raise SystemExit(f"error: unsafe ZIP member {info.filename!r} in {path}")
            archive.extractall(tmp.name)
        return Path(tmp.name)
    raise SystemExit(f"error: not a package directory or zip: {path}")


def parse_xml(path: Path, diagnostics: list[str]) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        diagnostics.append(f"{path.name} is not well-formed XML: {exc}")
    return None


def fingerprint_package(root: Path) -> dict:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_hasher.update(block)
        file_digest = file_hasher.hexdigest()
        digest.update(json.dumps([relative, size, file_digest], separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        file_count += 1
        total_bytes += size
    return {
        "algorithm": "sha256",
        "digest": digest.hexdigest(),
        "scope": "file_set",
        "file_count": file_count,
        "bytes": total_bytes,
    }


# --- model extraction --------------------------------------------------------


def walk_sections(elem: ET.Element, path: tuple[str, ...], out: list) -> None:
    for child in elem:
        name = local_name(child.tag)
        if name == "section":
            title = norm_title(child.attrib.get("title", ""))
            child_path = path + (title,) if title else path
            if title:
                out.append((child_path, child))
            walk_sections(child, child_path, out)


def extract_questions(questiondb_root: ET.Element) -> list[dict]:
    sections: list[tuple[tuple[str, ...], ET.Element]] = []
    walk_sections(questiondb_root, (), sections)
    located: dict[int, tuple[str, ...]] = {}
    for section_path, section in sections:
        for child in section:
            if local_name(child.tag) == "item":
                located[id(child)] = section_path

    questions = []
    for item in questiondb_root.iter():
        if local_name(item.tag) != "item":
            continue
        questions.append(
            {
                "title": norm_title(item.attrib.get("title", "")),
                "label": item.attrib.get("label", ""),
                "ident": item.attrib.get("ident", ""),
                "qtype": metadata_value(item, "qmd_questiontype"),
                "displayid": metadata_value(item, "qmd_displayid"),
                "globalid": metadata_value(item, "qmd_globalid"),
                "section_path": list(located.get(id(item), ())),
                "text_sig": text_signature(item),
                "asset_refs": element_asset_refs(item),
            }
        )
    return questions


def direct_metadata_value(elem: ET.Element, label: str) -> str:
    for child in elem:
        if local_name(child.tag) == "qtimetadata":
            for field in child:
                if local_name(field.tag) != "qti_metadatafield":
                    continue
                field_label = field_entry = ""
                for field_child in field:
                    if local_name(field_child.tag) == "fieldlabel":
                        field_label = clean(field_child.text)
                    elif local_name(field_child.tag) == "fieldentry":
                        field_entry = clean(field_child.text)
                if field_label == label:
                    return field_entry
        elif local_name(child.tag) == "sectionproc_extension":
            found = direct_metadata_value(child, label)
            if found:
                return found
    return ""


def extract_quiz(quiz_root: ET.Element, file_name: str) -> dict | None:
    assessment = None
    for elem in quiz_root.iter():
        if local_name(elem.tag) == "assessment":
            assessment = elem
            break
    if assessment is None:
        return None

    settings: dict[str, str] = {}
    grade_join = {"identifier": "", "resource_code": ""}
    for child in assessment:
        name = local_name(child.tag)
        if name == "assessmentcontrol":
            for key, value in child.attrib.items():
                settings[f"assessmentcontrol/{local_name(key)}"] = value
        elif name == "assess_procextension":
            for setting in child:
                setting_name = local_name(setting.tag)
                if setting_name == "grade_item":
                    grade_join = {
                        "identifier": clean(setting.text),
                        "resource_code": setting.attrib.get("resource_code", ""),
                    }
                elif not list(setting) and clean(setting.text):
                    settings[local_name(setting.tag)] = clean(setting.text)

    sections = []
    found: list[tuple[tuple[str, ...], ET.Element]] = []
    walk_sections(assessment, (), found)
    for section_path, section in found:
        items = []
        for child in section:
            name = local_name(child.tag)
            if name == "item":
                items.append(
                    {
                        "title": norm_title(child.attrib.get("title", "")),
                        "label": child.attrib.get("label", ""),
                        "text_sig": text_signature(child),
                        "displayid": metadata_value(child, "qmd_displayid"),
                        "globalid": metadata_value(child, "qmd_globalid"),
                    }
                )
            elif name == "itemref":
                items.append(
                    {
                        "title": "",
                        "label": child.attrib.get("linkrefid", ""),
                        "text_sig": "",
                        "displayid": "",
                        "globalid": "",
                    }
                )
        draw_raw = direct_metadata_value(section, "qmd_numberofitems")
        try:
            draw = int(float(draw_raw)) if draw_raw else None
        except ValueError:
            draw = None
        sections.append(
            {
                "title": section_path[-1],
                "path": list(section_path),
                "draw_count": draw,
                "items": items,
            }
        )

    return {
        "file": file_name,
        "title": norm_title(assessment.attrib.get("title", "")),
        "ident": assessment.attrib.get("ident", ""),
        "settings": settings,
        "grade_join": grade_join,
        "asset_refs": element_asset_refs(assessment),
        "sections": sections,
    }


def extract_grade_items(root: Path, diagnostics: list[str]) -> list[dict[str, str]]:
    grades_path = root / "grades_d2l.xml"
    if not grades_path.exists():
        return []
    grades_root = parse_xml(grades_path, diagnostics)
    if grades_root is None:
        return []
    items: list[dict[str, str]] = []
    for elem in grades_root.iter():
        if local_name(elem.tag) != "item":
            continue
        name = next(
            (clean(child.text) for child in elem if local_name(child.tag) == "name"),
            "",
        )
        items.append(
            {
                "identifier": elem.attrib.get("identifier", ""),
                "resource_code": elem.attrib.get("resource_code", ""),
                "name": name,
            }
        )
    return items


def resolve_grade_join(quiz: dict, grade_items: list[dict[str, str]]) -> None:
    """Resolve a quiz grade reference without treating tenant IDs as identity."""
    raw = quiz.get("grade_join", {})
    identifier = str(raw.get("identifier") or "")
    resource_code = str(raw.get("resource_code") or "")
    if not identifier and not resource_code:
        quiz["grade_join"] = {
            "state": "absent",
            "identifier": "",
            "resource_code": "",
            "resolved_name": "",
            "title_matches": None,
        }
        return

    candidates = [
        item
        for item in grade_items
        if (identifier and item["identifier"] == identifier)
        or (resource_code and item["resource_code"] == resource_code)
    ]
    unique = {
        (item["identifier"], item["resource_code"], item["name"]): item
        for item in candidates
    }
    if len(unique) != 1:
        quiz["grade_join"] = {
            "state": "unresolved" if not unique else "ambiguous",
            "identifier": identifier,
            "resource_code": resource_code,
            "resolved_name": "",
            "title_matches": False,
        }
        return

    item = next(iter(unique.values()))
    title_matches = norm_title(item["name"]) == norm_title(quiz["title"])
    quiz["grade_join"] = {
        "state": "resolved_title_matched" if title_matches else "resolved_title_mismatch",
        "identifier": identifier,
        "resource_code": resource_code,
        "resolved_name": item["name"],
        "title_matches": title_matches,
    }


def extract_asset_refs(root: Path) -> dict[str, list[str]]:
    """Relative asset paths referenced from mattext HTML in package XML, with
    the missing ones called out."""
    referenced: set[str] = set()
    for xml_path in sorted(root.glob("*.xml")):
        try:
            tree_root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        for mattext in tree_root.iter():
            if local_name(mattext.tag) != "mattext":
                continue
            raw = clean(mattext.text)
            if not raw:
                continue
            for ref in re.findall(r'(?:src|href)="([^"]+)"', html.unescape(raw)):
                ref = ref.strip()
                if ref and not ref.lower().startswith(URL_SCHEMES):
                    referenced.add(ref.split("?")[0].split("#")[0])
    missing = []
    for ref in sorted(referenced):
        try:
            member = package_member_path(ref)
        except (UnicodeDecodeError, ValueError):
            missing.append(ref)
            continue
        if not root.joinpath(*member.parts).exists():
            missing.append(ref)
    return {"referenced": sorted(referenced), "missing": missing}


def extract_manifest_summary(root: Path, diagnostics: list[str]) -> dict:
    manifest_path = root / "imsmanifest.xml"
    if not manifest_path.exists():
        diagnostics.append("imsmanifest.xml missing")
        return {"resource_types": {}, "missing_hrefs": []}
    manifest_root = parse_xml(manifest_path, diagnostics)
    if manifest_root is None:
        return {"resource_types": {}, "missing_hrefs": []}
    types: dict[str, int] = {}
    missing_hrefs = []
    for elem in manifest_root.iter():
        if local_name(elem.tag) != "resource":
            continue
        rtype = elem.attrib.get("type", "") or "(untyped)"
        types[rtype] = types.get(rtype, 0) + 1
        href = elem.attrib.get("href", "")
        if not href:
            continue
        # Quicklink/contentlink and external hrefs are URLs the LMS resolves
        # (e.g. /d2l/common/dialogs/quickLink/...), not files inside the package —
        # do not flag them as missing on-disk.
        if href.startswith("/") or href.startswith(URL_SCHEMES):
            continue
        try:
            member = package_member_path(href)
        except (UnicodeDecodeError, ValueError):
            missing_hrefs.append(href)
            continue
        if not root.joinpath(*member.parts).exists():
            missing_hrefs.append(href)
    return {"resource_types": types, "missing_hrefs": sorted(missing_hrefs)}


def load_side(root: Path, label: str) -> dict:
    diagnostics: list[str] = []
    questions: list[dict] = []
    questiondb_path = root / "questiondb.xml"
    if questiondb_path.exists():
        questiondb_root = parse_xml(questiondb_path, diagnostics)
        if questiondb_root is not None:
            questions = extract_questions(questiondb_root)
    else:
        diagnostics.append("questiondb.xml missing")

    grade_items = extract_grade_items(root, diagnostics)
    quizzes = []
    for quiz_path in sorted(root.glob("quiz_d2l_*.xml")):
        quiz_root = parse_xml(quiz_path, diagnostics)
        if quiz_root is None:
            continue
        quiz = extract_quiz(quiz_root, quiz_path.name)
        if quiz is None:
            diagnostics.append(f"{quiz_path.name} has no assessment element")
        else:
            resolve_grade_join(quiz, grade_items)
            quizzes.append(quiz)
    if not quizzes:
        diagnostics.append("no quiz_d2l_*.xml payload found")

    return {
        "label": label,
        "root": str(root),
        "fingerprint": fingerprint_package(root),
        "questions": questions,
        "quizzes": quizzes,
        "manifest": extract_manifest_summary(root, diagnostics),
        "assets": extract_asset_refs(root),
        "diagnostics": diagnostics,
    }


def scope_side_to_quiz_file(side: dict, file_name: str) -> dict:
    """Limit one loaded side to the named quiz and its reachable questions."""
    matches = [quiz for quiz in side["quizzes"] if quiz["file"] == file_name]
    if len(matches) != 1:
        raise SystemExit(
            f"error: expected exactly one quiz file {file_name!r} in {side['label']}, found {len(matches)}"
        )
    quiz = matches[0]
    referenced_labels = {
        item["label"]
        for section in quiz["sections"]
        for item in section["items"]
        if item.get("label")
    }
    scoped = dict(side)
    scoped["root"] = f"{side['root']}#quiz-file={file_name}"
    scoped["quizzes"] = [quiz]
    scoped["questions"] = [
        question for question in side["questions"] if question.get("label") in referenced_labels
    ]
    referenced_assets = sorted(
        {
            ref
            for owner in [quiz, *scoped["questions"]]
            for ref in owner.get("asset_refs", [])
        }
    )
    missing_assets = set(side.get("assets", {}).get("missing", []))
    scoped["assets"] = {
        "referenced": referenced_assets,
        "missing": [ref for ref in referenced_assets if ref in missing_assets],
    }
    relevant_manifest_hrefs = {file_name, "questiondb.xml", *referenced_assets}
    scoped["manifest"] = {
        **side["manifest"],
        "missing_hrefs": [
            href
            for href in side["manifest"].get("missing_hrefs", [])
            if href in relevant_manifest_hrefs
        ],
    }
    return scoped


# --- matching ----------------------------------------------------------------


def match_by_title(a_entries: list[dict], b_entries: list[dict]) -> dict:
    """Match two lists of dicts (each with title/qtype/text_sig) by title, with
    a (qtype, text_sig) tiebreak for duplicate titles. Never guesses: leftovers
    under a duplicated title go to `ambiguous`."""
    a_by_title: dict[str, list[dict]] = {}
    b_by_title: dict[str, list[dict]] = {}
    for entry in a_entries:
        a_by_title.setdefault(entry["title"], []).append(entry)
    for entry in b_entries:
        b_by_title.setdefault(entry["title"], []).append(entry)

    matched: list[tuple[dict, dict]] = []
    ambiguous: list[dict] = []
    missing_in_b: list[dict] = []
    added_in_b: list[dict] = []

    for title in sorted(set(a_by_title) | set(b_by_title)):
        a_group = list(a_by_title.get(title, []))
        b_group = list(b_by_title.get(title, []))
        if len(a_group) == 1 and len(b_group) == 1:
            a_group[0]["_match_basis"] = "title"
            b_group[0]["_match_basis"] = "title"
            matched.append((a_group[0], b_group[0]))
            continue
        if not b_group:
            missing_in_b.extend(a_group)
            continue
        if not a_group:
            added_in_b.extend(b_group)
            continue
        # Duplicate titles: pair only where (qtype, text_sig) is unique on both sides.
        def sig(entry: dict) -> tuple[str, str]:
            return (entry.get("qtype", ""), entry.get("text_sig", ""))

        a_by_sig: dict[tuple[str, str], list[dict]] = {}
        b_by_sig: dict[tuple[str, str], list[dict]] = {}
        for entry in a_group:
            a_by_sig.setdefault(sig(entry), []).append(entry)
        for entry in b_group:
            b_by_sig.setdefault(sig(entry), []).append(entry)
        for key in sorted(set(a_by_sig) | set(b_by_sig)):
            a_sig_group = a_by_sig.get(key, [])
            b_sig_group = b_by_sig.get(key, [])
            if len(a_sig_group) == 1 and len(b_sig_group) == 1:
                a_sig_group[0]["_match_basis"] = "title+type+text_signature"
                b_sig_group[0]["_match_basis"] = "title+type+text_signature"
                matched.append((a_sig_group[0], b_sig_group[0]))
            else:
                ambiguous.append(
                    {
                        "title": title,
                        "qtype": key[0],
                        "count_a": len(a_sig_group),
                        "count_b": len(b_sig_group),
                    }
                )
    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "missing_in_b": missing_in_b,
        "added_in_b": added_in_b,
    }


def match_with_stable_identity(
    a_entries: list[dict],
    b_entries: list[dict],
    *,
    identity_fields: tuple[str, ...] = ("displayid", "globalid"),
) -> dict:
    """Match unique permanent codes first, then global IDs, then conservative content.

    A duplicated identity value is never guessed. It falls through to the same
    title/type/text ambiguity rules used by older packages without stable IDs.
    """
    remaining_a = list(a_entries)
    remaining_b = list(b_entries)
    matched: list[tuple[dict, dict]] = []
    for field in identity_fields:
        a_values: dict[str, list[dict]] = {}
        b_values: dict[str, list[dict]] = {}
        for entry in remaining_a:
            if entry.get(field):
                a_values.setdefault(entry[field], []).append(entry)
        for entry in remaining_b:
            if entry.get(field):
                b_values.setdefault(entry[field], []).append(entry)
        paired_a: set[int] = set()
        paired_b: set[int] = set()
        for value in sorted(set(a_values) & set(b_values)):
            if len(a_values[value]) != 1 or len(b_values[value]) != 1:
                continue
            a_entry = a_values[value][0]
            b_entry = b_values[value][0]
            a_entry["_match_basis"] = field
            b_entry["_match_basis"] = field
            matched.append((a_entry, b_entry))
            paired_a.add(id(a_entry))
            paired_b.add(id(b_entry))
        remaining_a = [entry for entry in remaining_a if id(entry) not in paired_a]
        remaining_b = [entry for entry in remaining_b if id(entry) not in paired_b]

    fallback = match_by_title(remaining_a, remaining_b)
    fallback["matched"] = [*matched, *fallback["matched"]]
    return fallback


# --- comparison --------------------------------------------------------------


def resolve_quiz_item_titles(quiz: dict, questions: list[dict]) -> None:
    """Fill empty itemref titles from that side's questiondb labels."""
    by_label = {q["label"]: q for q in questions if q["label"]}
    for section in quiz["sections"]:
        for item in section["items"]:
            if not item["title"] and item["label"] in by_label:
                question = by_label[item["label"]]
                item["title"] = question["title"]
                item["text_sig"] = question["text_sig"]
                item["displayid"] = question["displayid"]
                item["globalid"] = question["globalid"]


def compare_questions(side_a: dict, side_b: dict, breaks: list[str]) -> dict:
    result = match_with_stable_identity(side_a["questions"], side_b["questions"])
    matched = []
    for a_question, b_question in result["matched"]:
        flags = []
        if a_question["qtype"] and b_question["qtype"] and a_question["qtype"] != b_question["qtype"]:
            flags.append(f"question type changed: {a_question['qtype']} -> {b_question['qtype']}")
        if (
            a_question["text_sig"]
            and b_question["text_sig"]
            and a_question["text_sig"] != b_question["text_sig"]
        ):
            flags.append("question text changed (normalized signatures differ)")
        if a_question["section_path"] != b_question["section_path"]:
            flags.append(
                "library section moved: "
                f"{'/'.join(a_question['section_path'])} -> {'/'.join(b_question['section_path'])}"
            )
        matched.append(
            {
                "title": a_question["title"],
                "label_a": a_question["label"],
                "label_b": b_question["label"],
                "ident_a": a_question["ident"],
                "ident_b": b_question["ident"],
                "displayid_a": a_question["displayid"],
                "displayid_b": b_question["displayid"],
                "globalid_a": a_question["globalid"],
                "globalid_b": b_question["globalid"],
                "match_basis": a_question.get("_match_basis", "unknown"),
                "qtype": a_question["qtype"] or b_question["qtype"],
                "flags": flags,
            }
        )
    for question in result["missing_in_b"]:
        breaks.append(f"question missing in {side_b['label']}: {question['title']!r}")
    for entry in result["ambiguous"]:
        breaks.append(
            f"ambiguous question match for title {entry['title']!r} "
            f"({entry['count_a']} in {side_a['label']}, {entry['count_b']} in {side_b['label']}) — not guessed"
        )
    return {
        "matched": matched,
        "missing_in_b": [q["title"] for q in result["missing_in_b"]],
        "added_in_b": [q["title"] for q in result["added_in_b"]],
        "ambiguous": result["ambiguous"],
    }


def compare_section(a_section: dict, b_section: dict, quiz_title: str, side_b_label: str, breaks: list[str]) -> dict:
    item_match = match_with_stable_identity(a_section["items"], b_section["items"])
    missing = [item["title"] or item["label"] for item in item_match["missing_in_b"]]
    added = [item["title"] or item["label"] for item in item_match["added_in_b"]]
    flags = []
    for title in missing:
        breaks.append(
            f"quiz {quiz_title!r} section {a_section['title']!r}: question {title!r} missing in {side_b_label}"
        )
    if a_section["draw_count"] != b_section["draw_count"]:
        flags.append(f"draw count changed: {a_section['draw_count']} -> {b_section['draw_count']}")
        breaks.append(
            f"quiz {quiz_title!r} section {a_section['title']!r}: "
            f"draw count changed {a_section['draw_count']} -> {b_section['draw_count']}"
        )
    b_draw = b_section["draw_count"]
    if b_draw is not None and b_draw > len(b_section["items"]):
        breaks.append(
            f"quiz {quiz_title!r} section {b_section['title']!r} in {side_b_label}: "
            f"draws {b_draw} from {len(b_section['items'])} candidates"
        )
    return {
        "title": a_section["title"],
        "draw_a": a_section["draw_count"],
        "draw_b": b_section["draw_count"],
        "item_count_a": len(a_section["items"]),
        "item_count_b": len(b_section["items"]),
        "missing_in_b": missing,
        "added_in_b": added,
        "flags": flags,
    }


def compare_quiz_pair(a_quiz: dict, b_quiz: dict, side_b_label: str, breaks: list[str]) -> dict:
    settings_diffs = []
    for key in sorted(a_quiz["settings"]):
        value_a = a_quiz["settings"][key]
        value_b = b_quiz["settings"].get(key)
        if value_b is None:
            settings_diffs.append({"key": key, "a": value_a, "b": "(absent)"})
        elif value_a != value_b:
            settings_diffs.append({"key": key, "a": value_a, "b": value_b})

    grade_a = a_quiz.get("grade_join", {"state": "absent"})
    grade_b = b_quiz.get("grade_join", {"state": "absent"})
    grade_states = (grade_a.get("state"), grade_b.get("state"))
    grade_join_equivalent = grade_states in {
        ("absent", "absent"),
        ("resolved_title_matched", "resolved_title_matched"),
    }
    if not grade_join_equivalent:
        settings_diffs.append(
            {
                "key": "grade_join",
                "a": grade_a.get("state", "unknown"),
                "b": grade_b.get("state", "unknown"),
            }
        )

    a_sections = [{**s, "qtype": "", "text_sig": ""} for s in a_quiz["sections"]]
    b_sections = [{**s, "qtype": "", "text_sig": ""} for s in b_quiz["sections"]]
    section_match = match_by_title(a_sections, b_sections)
    sections = [
        compare_section(a_section, b_section, a_quiz["title"], side_b_label, breaks)
        for a_section, b_section in section_match["matched"]
    ]
    for section in section_match["missing_in_b"]:
        breaks.append(f"quiz {a_quiz['title']!r}: section {section['title']!r} missing in {side_b_label}")
    for entry in section_match["ambiguous"]:
        breaks.append(
            f"quiz {a_quiz['title']!r}: ambiguous section match for title {entry['title']!r} — not guessed"
        )

    return {
        "title_a": a_quiz["title"],
        "title_b": b_quiz["title"],
        "file_a": a_quiz["file"],
        "file_b": b_quiz["file"],
        "title_changed": a_quiz["title"] != b_quiz["title"],
        "grade_join_a": grade_a,
        "grade_join_b": grade_b,
        "grade_join_equivalent": grade_join_equivalent,
        "settings_diffs": settings_diffs,
        "sections": sections,
        "sections_missing_in_b": [s["title"] for s in section_match["missing_in_b"]],
        "sections_added_in_b": [s["title"] for s in section_match["added_in_b"]],
    }


def compare_quizzes(side_a: dict, side_b: dict, breaks: list[str], notes: list[str]) -> list[dict]:
    for side in (side_a, side_b):
        for quiz in side["quizzes"]:
            resolve_quiz_item_titles(quiz, side["questions"])

    a_quizzes = list(side_a["quizzes"])
    b_quizzes = list(side_b["quizzes"])
    pairs: list[tuple[dict, dict]] = []
    if len(a_quizzes) == 1 and len(b_quizzes) == 1:
        if a_quizzes[0]["title"] != b_quizzes[0]["title"]:
            notes.append(
                "single quiz on each side matched positionally despite title change: "
                f"{a_quizzes[0]['title']!r} -> {b_quizzes[0]['title']!r}"
            )
        pairs.append((a_quizzes[0], b_quizzes[0]))
    else:
        quiz_match = match_by_title(
            [{**q, "qtype": "", "text_sig": ""} for q in a_quizzes],
            [{**q, "qtype": "", "text_sig": ""} for q in b_quizzes],
        )
        pairs.extend(quiz_match["matched"])
        for quiz in quiz_match["missing_in_b"]:
            breaks.append(f"quiz missing in {side_b['label']}: {quiz['title']!r}")
        for quiz in quiz_match["added_in_b"]:
            notes.append(f"quiz only in {side_b['label']}: {quiz['title']!r}")
        for entry in quiz_match["ambiguous"]:
            breaks.append(f"ambiguous quiz match for title {entry['title']!r} — not guessed")

    return [compare_quiz_pair(a_quiz, b_quiz, side_b["label"], breaks) for a_quiz, b_quiz in pairs]


def build_diff(side_a: dict, side_b: dict) -> dict:
    breaks: list[str] = []
    notes: list[str] = []

    for side in (side_a, side_b):
        for label in side["assets"]["missing"]:
            breaks.append(f"asset referenced but missing in {side['label']}: {label}")
        for diagnostic in side["diagnostics"]:
            breaks.append(f"{side['label']}: {diagnostic}")
        for href in side["manifest"]["missing_hrefs"]:
            breaks.append(f"manifest href missing in {side['label']}: {href}")

    questions = compare_questions(side_a, side_b, breaks)
    quizzes = compare_quizzes(side_a, side_b, breaks, notes)
    if questions["added_in_b"]:
        notes.append(
            f"questions only in {side_b['label']} (added after import?): "
            + ", ".join(repr(t) for t in questions["added_in_b"])
        )

    manifest = {
        "resource_types_a": side_a["manifest"]["resource_types"],
        "resource_types_b": side_b["manifest"]["resource_types"],
    }
    return {
        "report_type": "coursecraft.quiz_package_diff/1",
        "matching_policy": {
            "question_identity_priority": ["qmd_displayid", "qmd_globalid", "title+type+text_signature"],
            "ambiguous_matches": "never_guess",
            "raw_platform_ids_are_identity": False,
        },
        "side_a": {
            "label": side_a["label"],
            "root": side_a["root"],
            "question_count": len(side_a["questions"]),
            "fingerprint": side_a["fingerprint"],
        },
        "side_b": {
            "label": side_b["label"],
            "root": side_b["root"],
            "question_count": len(side_b["questions"]),
            "fingerprint": side_b["fingerprint"],
        },
        "questions": questions,
        "quizzes": quizzes,
        "manifest": manifest,
        "breaks": breaks,
        "notes": notes,
    }


# --- rendering ---------------------------------------------------------------


def render_markdown(diff: dict) -> str:
    side_a, side_b = diff["side_a"], diff["side_b"]
    lines = [
        "# Round-Trip Package Diff",
        "",
        f"- Side A ({side_a['label']}): `{side_a['root']}` — {side_a['question_count']} questions",
        f"- Side B ({side_b['label']}): `{side_b['root']}` — {side_b['question_count']} questions",
        f"- Side A fingerprint: `{side_a['fingerprint']['digest']}` ({side_a['fingerprint']['file_count']} files)",
        f"- Side B fingerprint: `{side_b['fingerprint']['digest']}` ({side_b['fingerprint']['file_count']} files)",
        f"- Breaks: {len(diff['breaks'])}",
        "",
        "ID changes between sides are expected after a Brightspace import and are",
        "reported as mappings, not breaks. Questions match first by permanent",
        "qmd_displayid, then qmd_globalid, then title with a type/text-signature",
        "tiebreak. Ambiguous matches are surfaced, never guessed.",
        "",
        "## Breaks",
        "",
    ]
    lines.extend(f"- {item}" for item in diff["breaks"]) if diff["breaks"] else lines.append("- None.")
    lines.extend(["", "## Question matching", ""])
    questions = diff["questions"]
    lines.append(f"- Matched: {len(questions['matched'])}")
    lines.append(f"- Missing in B: {len(questions['missing_in_b'])}")
    lines.append(f"- Added in B: {len(questions['added_in_b'])}")
    lines.append(f"- Ambiguous: {len(questions['ambiguous'])}")
    match_bases: dict[str, int] = {}
    for question in questions["matched"]:
        basis = question.get("match_basis", "unknown")
        match_bases[basis] = match_bases.get(basis, 0) + 1
    if match_bases:
        lines.append("- Match bases: " + ", ".join(f"{key}={value}" for key, value in sorted(match_bases.items())))
    flagged = [q for q in questions["matched"] if q["flags"]]
    if flagged:
        lines.extend(["", "### Matched with changes", ""])
        for question in flagged:
            lines.append(f"- {question['title']!r}: " + "; ".join(question["flags"]))
    id_changes = [q for q in questions["matched"] if q["label_a"] != q["label_b"]]
    if id_changes:
        lines.extend(["", "### ID mappings (expected)", "", "| Title | Label A | Label B |", "| --- | --- | --- |"])
        for question in id_changes:
            lines.append(f"| {question['title']} | `{question['label_a']}` | `{question['label_b']}` |")
    for quiz in diff["quizzes"]:
        lines.extend(["", f"## Quiz: {quiz['title_a']!r} ({quiz['file_a']} vs {quiz['file_b']})", ""])
        if quiz["title_changed"]:
            lines.append(f"- Title changed: {quiz['title_a']!r} -> {quiz['title_b']!r}")
        grade_a = quiz.get("grade_join_a", {})
        grade_b = quiz.get("grade_join_b", {})
        lines.append(
            "- Grade joins: "
            f"{grade_a.get('state', 'unknown')} ({grade_a.get('resolved_name') or 'none'}) -> "
            f"{grade_b.get('state', 'unknown')} ({grade_b.get('resolved_name') or 'none'})"
        )
        if quiz["settings_diffs"]:
            lines.append("- Settings differences (keys present on side A only):")
            for entry in quiz["settings_diffs"]:
                lines.append(f"  - `{entry['key']}`: {entry['a']!r} -> {entry['b']!r}")
        else:
            lines.append("- Settings: no differences on side-A keys.")
        for section in quiz["sections"]:
            descriptor = (
                f"- Section {section['title']!r}: items {section['item_count_a']} -> {section['item_count_b']}, "
                f"draw {section['draw_a']} -> {section['draw_b']}"
            )
            if section["flags"] or section["missing_in_b"] or section["added_in_b"]:
                details = section["flags"] + [
                    f"missing in B: {title!r}" for title in section["missing_in_b"]
                ] + [f"added in B: {title!r}" for title in section["added_in_b"]]
                descriptor += " — " + "; ".join(details)
            lines.append(descriptor)
        for title in quiz["sections_missing_in_b"]:
            lines.append(f"- Section missing in B: {title!r}")
        for title in quiz["sections_added_in_b"]:
            lines.append(f"- Section added in B: {title!r}")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in diff["notes"]) if diff["notes"] else lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def safe_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "pkg"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("side_a", type=Path, help="Source/generated package (dir or zip)")
    parser.add_argument("side_b", type=Path, help="Comparison/re-export package (dir or zip)")
    parser.add_argument("--label-a", default="", help="Label for side A (default: folder name)")
    parser.add_argument("--label-b", default="", help="Label for side B (default: folder name)")
    parser.add_argument("--quiz-file-a", default="", help="Scope side A to one exact quiz_d2l XML filename.")
    parser.add_argument("--quiz-file-b", default="", help="Scope side B to one exact quiz_d2l XML filename.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write report files (default: <repo>/workspace/review)",
    )
    parser.add_argument(
        "--fail-on-break",
        action="store_true",
        help="Exit 1 when any break is found (for scripted checks)",
    )
    args = parser.parse_args(argv)

    holder: list = []
    root_a = load_package_root(args.side_a.expanduser().resolve(), holder)
    root_b = load_package_root(args.side_b.expanduser().resolve(), holder)
    label_a = args.label_a or safe_label(args.side_a.stem if args.side_a.is_file() else args.side_a.name)
    label_b = args.label_b or safe_label(args.side_b.stem if args.side_b.is_file() else args.side_b.name)

    side_a = load_side(root_a, label_a)
    side_b = load_side(root_b, label_b)
    if args.quiz_file_a:
        side_a = scope_side_to_quiz_file(side_a, args.quiz_file_a)
    if args.quiz_file_b:
        side_b = scope_side_to_quiz_file(side_b, args.quiz_file_b)
    diff = build_diff(side_a, side_b)

    output_dir = args.output_dir or (Path(__file__).resolve().parents[1] / "workspace" / "review")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_label(label_a)}__vs__{safe_label(label_b)}__roundtrip_diff"
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"
    md_path.write_text(render_markdown(diff), encoding="utf-8")
    json_path.write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")

    print(f"breaks: {len(diff['breaks'])}")
    print(f"questions matched: {len(diff['questions']['matched'])}")
    print(f"report: {md_path}")
    print(f"json: {json_path}")

    if args.fail_on_break and diff["breaks"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
