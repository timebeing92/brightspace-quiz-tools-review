#!/usr/bin/env python3
"""Prepare and re-import portable, assessment-grouped quiz review workbooks.

This is a presentation adapter over the existing native review contract. Exact
source content stays in the copied model and native workbook. No Google service
or network access is used. Run with --help for independent commands.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from copy import copy
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter

from quiz_review_projection import build_quiz_review_projection
from quiz_review_text import readable_html
from quiz_review_workbook_reingest import (
    EDITABLE_HEADERS, SETTINGS_EDITABLE_HEADERS, ReingestError,
    materialize_workbook_decisions, sha256_file,
)

SCHEMA = "coursecraft.assessment_review_packet/1"
MARKER = "_Assessment Review"
WORKING = "reviewer_working.xlsx"
BASELINE = "reviewer_baseline_DO_NOT_EDIT.xlsx"
LAYOUT = "assessment-review.json"
EXTRA_HEADERS = ["response_options_markup", "answer_key_markup", "question_text_markup",
                 "source_points", "source_feedback", "content_preservation", "source_content_ref"]
VISIBLE = {"question_number", "Source pool / folder", "question_type", "question_text",
           "revised_question_text", "image_link", "response_options", "revised_response_options",
           "answer_key", "revised_answer_key", "revision_reason", "proposed_by", "approval_status",
           "reviewer_note", "source_points", "source_feedback", "content_preservation"}
TEAL, PALE, YELLOW = "175C72", "E9F4F7", "FFF2CC"
RICH_MARKUP = re.compile(r"<(?:[\w.-]+:)?(?:math|img|matimage|sup|sub)\b", re.I)


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _inside(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ReingestError("Unsafe packet path: " + relative)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ReingestError("Packet path escapes its folder: " + relative)
    return path


def _ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def _nodes(node: dict, name: str):
    if node.get("name") == name:
        yield node
    for child in node.get("children", []):
        yield from _nodes(child, name)


def _presentation(question: dict, native: dict, model_index: int) -> dict:
    """Keep original rich fragments addressable; render only known structure."""
    result = {h: native.get(h) for h in ("question_text", "response_options", "answer_key")}
    result.update({h: None for h in EXTRA_HEADERS})
    result["source_content_ref"] = f"model.json#/questions/{model_index}"
    result["content_preservation"] = "Source retained; review only"
    payload = question.get("type_payload", {})
    facts = [r["payload"] for r in payload.get("raw_response_models", [])
             if r.get("source_kind") == "d2l_qti_response_facts/0"]
    prompt_fragments: list[str] = []
    ambiguous_source = bool(facts) and (len(facts) != 1 or len(facts[0].get("presentation", {}).get("occurrences", [])) != 1)
    if ambiguous_source:
        result["content_preservation"] = "Source retained; multiple source presentations need display review"
        result["question_text_markup"] = "See " + result["source_content_ref"]
    if len(facts) == 1 and len(facts[0].get("presentation", {}).get("occurrences", [])) == 1:
        tree = facts[0]["presentation"]["occurrences"][0]["node"]

        def prompt_material(node):
            for child in node.get("children", []):
                if child.get("name") == "material":
                    yield from (n.get("raw_text") or "" for n in _nodes(child, "mattext"))
                elif child.get("name") == "flow":
                    yield from prompt_material(child)

        prompt_fragments = list(prompt_material(tree))
    elif question.get("prompt", {}).get("format") in {"html", "xhtml", "mathml"}:
        prompt_fragments = [question["prompt"]["content"]]

    options = payload.get("options", [])
    originals = [{"option_key": o.get("option_key"), "source_response_ident":
                  o.get("extensions", {}).get("coursecraft.source_response_ident"),
                  "content": o.get("content"), "correct": o.get("correct")}
                 for o in options]
    raw_fields = {"question_text_markup": prompt_fragments,
                  "response_options_markup": originals,
                  "answer_key_markup": [o for o in originals if o["correct"] is True]}
    for name, value in raw_fields.items():
        if value:
            encoded = json.dumps(value, ensure_ascii=False)
            result[name] = encoded if len(encoded) < 30000 else "See " + result["source_content_ref"]
    try:
        if prompt_fragments:
            result["question_text"] = readable_html(" ".join(prompt_fragments)) or native.get("question_text")
        if options and all(o.get("content", {}).get("format") in {"html", "xhtml", "plain_text", "mathml"}
                           for o in options):
            labels = []
            for option in options:
                content = option.get("content", {})
                raw = content.get("content", "")
                label = raw if content.get("format") == "plain_text" else readable_html(raw)
                if not label and re.search(r"<(?:img|matimage)\b", raw, re.I):
                    label = "[Image option — open the question images]"
                labels.append(label or "[No visible text; inspect source]")
            # Always expose option keys; duplicate and image-only choices stay distinct.
            result["response_options"] = "\n".join(f"{o['option_key']}. {label}" for o, label in zip(options, labels))
            correct = [f"{o['option_key']}. {label}" for o, label in zip(options, labels) if o.get("correct") is True]
            if correct:
                result["answer_key"] = "\n".join(correct)
        feedback = []
        if len(facts) == 1:
            for slot in facts[0].get("feedback_slots", []):
                fragments = [n.get("raw_text") or "" for n in _nodes(slot.get("node", {}), "mattext")]
                text = readable_html(" ".join(fragments))
                if text:
                    feedback.append(str(slot.get("source_ident") or "Feedback") + ": " + text)
        if not feedback:
            for item in question.get("feedback", []):
                content = item.get("content", {})
                if isinstance(content, dict) and content.get("content"):
                    feedback.append(readable_html(content["content"]))
        result["source_feedback"] = "\n".join(feedback) or None
    except (ValueError, ET.ParseError) as exc:
        result["content_preservation"] = "Source retained; display needs review: " + str(exc)
    result["rich_revision_hold"] = any(RICH_MARKUP.search(s) for s in prompt_fragments)
    result["rich_revision_hold"] |= any(o.get("content", {}).get("format") != "plain_text" and
        RICH_MARKUP.search(o.get("content", {}).get("content", "")) for o in options)
    result["rich_revision_hold"] |= native.get("image_count") not in (None, "", 0, "0")
    if ambiguous_source:
        result["rich_revision_hold"] = True
    return result


def _copy_cell(src, dst) -> None:
    dst.value = src.value
    if isinstance(src.value, str):
        dst.data_type = "s"  # source text beginning '=' stays literal
    dst._style = copy(src._style)
    if src.comment:
        dst.comment = copy(src.comment)
    if src.hyperlink:
        dst._hyperlink = copy(src.hyperlink)
        dst.hyperlink.ref = dst.coordinate


def _portable_links(book, source_dir: Path, output_dir: Path) -> list[dict]:
    assets: dict[str, dict] = {}
    for sheet in book:
        for row in sheet:
            for cell in row:
                if not cell.hyperlink or not cell.hyperlink.target:
                    continue
                old = cell.hyperlink.target
                parsed = urlsplit(old)
                if parsed.scheme in {"http", "https", "mailto"} or old.startswith("#"):
                    continue
                source = (source_dir / unquote(old)).resolve()
                if parsed.scheme or not source.is_relative_to(source_dir.resolve()) or not source.is_file():
                    raise ValueError("Image link is missing or outside the extraction folder; use self_contained extraction: " + old)
                digest = sha256_file(source)
                name = re.sub(r"[^A-Za-z0-9._-]", "_", source.name)[-100:]
                relative = "Images/" + digest + "-" + name
                target = output_dir / relative
                if relative not in assets:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    assets[relative] = _ref(output_dir, target)
                cell.hyperlink = relative
    return list(assets.values())


def _sheet_title(title: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", " ", title).strip().strip("'") or "Assessment"
    candidate = base[:31]
    number = 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = base[:31-len(suffix)] + suffix
        number += 1
    used.add(candidate.casefold())
    return candidate


def prepare_review_packet(source_workbook: Path, model_path: Path, output_dir: Path) -> dict:
    """Create a new packet. Existing files and reviewer work are never replaced."""
    source_workbook, model_path, output_dir = map(Path, (source_workbook, model_path, output_dir))
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Review output must be a new or empty folder; preserve existing reviewer edits")
    model = json.loads(model_path.read_text(encoding="utf-8"))
    projection = build_quiz_review_projection(model, include_content=True)
    # Confirm the baseline really belongs to the supplied model before adapting it.
    if projection["question_occurrences"]:
        materialize_workbook_decisions(model_path, source_workbook, source_workbook, metadata_policy="optional")
    book = load_workbook(source_workbook)
    if MARKER in book.sheetnames:
        raise ValueError("This workbook is already grouped")
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = _portable_links(book, source_workbook.parent, output_dir)
    shutil.copyfile(model_path, output_dir / "model.json")
    # Native baseline shares the packet's image folder and remains a separate source.
    native = output_dir / "native_source_DO_NOT_EDIT.xlsx"
    book.save(native)
    if not projection["question_occurrences"]:
        # A question-library-only export has no assessment placement to revise.
        # Preserve successful extraction and portable links without inventing one.
        guide = book.create_sheet("START HERE", 0)
        guide.append(["Question inventory", f"{len(model['questions'])} source questions; no quiz occurrences in this export."])
        guide.append(["Review", "Use All Questions and Question Images. Keep this folder together for local image links."])
        guide.append(["Author", "Use the blank drafting template for new questions/assessments. This inventory has no assessment edits to import."])
        guide.column_dimensions["A"].width = 24
        guide.column_dimensions["B"].width = 105
        for cells in guide:
            guide.row_dimensions[cells[0].row].height = 45
            for cell in cells:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for sheet in book:
            sheet.protection.sheet = True
            for cells in sheet:
                for cell in cells:
                    cell.protection = Protection(locked=True)
        book.active = 0
        baseline = output_dir / BASELINE
        book.save(baseline)
        shutil.copyfile(baseline, output_dir / WORKING)
        spec = {"schema": SCHEMA, "profile": "inventory_only", "model": _ref(output_dir, output_dir / "model.json"),
                "native_workbook": _ref(output_dir, native), "baseline": _ref(output_dir, baseline),
                "assets": assets, "sheets": [], "question_count": 0, "inventory_question_count": len(model["questions"])}
        _json(output_dir / LAYOUT, spec)
        (output_dir / "README.md").write_text("# Question inventory\n\nOpen reviewer_working.xlsx. "
            "This export has no quiz occurrences, so there are no assessment edits to import. "
            "Keep Images/ beside the workbook. Use the separate blank drafting template for new assessments.\n", encoding="utf-8")
        return spec
    src = book["Quiz Questions"]
    original_headers = [c.value for c in src[1]]
    required = {"occurrence_key", "question_entity_key", "quiz", "question_number"}
    if not required.issubset(original_headers):
        raise ValueError("Assessment review needs the current occurrence-aware native workbook")
    columns = {h: i + 1 for i, h in enumerate(original_headers)}
    headers = original_headers[:2] + ["Source pool / folder"] + original_headers[2:] + EXTRA_HEADERS
    dest_columns = {h: i + 1 for i, h in enumerate(headers)}
    occurrences = {o["occurrence_key"]: o for o in projection["question_occurrences"]}
    questions = {q["entity_key"]: (i, q) for i, q in enumerate(model["questions"])}
    assessments: OrderedDict[str, list] = OrderedDict()
    seen = set()
    for row in range(2, src.max_row + 1):
        values = {h: src.cell(row, c).value for h, c in columns.items()}
        key = values["occurrence_key"]
        if key in seen or key not in occurrences:
            raise ValueError("Duplicate or unknown source occurrence: " + str(key))
        seen.add(key)
        occurrence = occurrences[key]
        assessments.setdefault(occurrence["quiz_key"], []).append((row, values, occurrence))
    if seen != set(occurrences):
        raise ValueError("Native workbook does not cover every source occurrence")
    guide = book.create_sheet("START HERE", 0)
    source_label = ", ".join(model["source"].get("references", [])) or model["source"]["source_key"]
    guide_rows = [
        ("Assessment review", "Edit the yellow fields on each assessment tab."),
        ("Source", source_label),
        ("Questions", f"{len(seen)} occurrences in {len(assessments)} assessments. Source sets/pools appear within each tab."),
        ("Images", "Open image_link or Question Images. Keep Images/ beside this workbook when sharing the complete folder."),
        ("Draft and accept", "Leave approval_status blank or open while drafting. Enter accepted only for an accepted change. Reviewer names, reasons and dates are optional."),
        ("Source content", "Original text and keys are protected. Readable text is paired with original markup columns and model.json."),
        ("Import edits", "Keep the complete packet, including hidden sheets and companion files. Compose collects assessment-tab edits automatically."),
        ("Quiz settings", "Quiz Settings retains its own editable proposed-value fields. Question edits belong on assessment tabs."),
        ("New questions", "Use the separate blank drafting template and quiz_draft_intake.py. Do not insert or delete source question rows here."),
        ("Math and diagrams", "Accepted revisions to questions containing equations or diagrams require a supported rich-content workflow. Drafts and notes can be collected for review."),
    ]
    for row in guide_rows:
        guide.append(row)
    guide.column_dimensions["A"].width = 24
    guide.column_dimensions["B"].width = 108
    for row in guide:
        guide.row_dimensions[row[0].row].height = 48
        for cell in row:
            cell.font = Font(name="Calibri", size=11, bold=cell.column == 1, color=TEAL if cell.column == 1 else "222222")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    guide.freeze_panes = "B2"
    spec = {"schema": SCHEMA, "model": _ref(output_dir, output_dir / "model.json"),
            "native_workbook": _ref(output_dir, native), "assets": assets, "sheets": [], "question_count": len(seen)}
    used = {name.casefold() for name in book.sheetnames} | {MARKER.casefold()}
    for index, (quiz_key, entries) in enumerate(sorted(assessments.items(), key=lambda p: -len(p[1])), 1):
        title = entries[0][2]["quiz_title"] or "Untitled assessment"
        dst = book.create_sheet(_sheet_title(title, used), index)
        dst.sheet_properties.tabColor = TEAL
        dst.sheet_properties.outlinePr.summaryBelow = False
        dst.sheet_view.showGridLines = False
        dst.freeze_panes = "D3"
        dst.merge_cells("B1:C1")
        dst.cell(1, 2, title)
        prompt_col = dest_columns["question_text"]
        dst.merge_cells(start_row=1, start_column=prompt_col, end_row=1, end_column=prompt_col + 2)
        dst.cell(1, prompt_col, "Source: " + source_label)
        dst.row_dimensions[1].height = 64
        dst.row_dimensions[2].height = 48
        for col, header in enumerate(headers, 1):
            dst.cell(2, col, header)
            letter = get_column_letter(col)
            width = 24
            if header in {"question_text", "revised_question_text", "response_options", "revised_response_options", "source_feedback", "reviewer_note"}:
                width = 56
            elif header == "Source pool / folder":
                width = 52
            elif header == "question_number":
                width = 10
            dst.column_dimensions[letter].width = width
            dst.column_dimensions[letter].hidden = header not in VISIBLE
        groups: OrderedDict[tuple, list] = OrderedDict()
        for entry in entries:
            o = entry[2]
            pools = tuple(p.get("pool_path") or p.get("pool_title") or "" for p in o.get("pool_memberships", []))
            group = (o.get("section_key"), pools)
            groups.setdefault(group, []).append(entry)
        tab = {"title": dst.title, "quiz_key": quiz_key, "rows": [], "group_count": len(groups)}
        row = 3
        for (_section_key, pools), members in groups.items():
            occurrence = members[0][2]
            local_label = "Quiz pool > " if occurrence.get("selection_context", {}).get("mode") == "random" else "Quiz section > "
            label = " | ".join(p for p in pools if p) or (
                local_label + str(occurrence.get("section_path") or occurrence.get("section_title"))
                if occurrence.get("section_key") else "Quiz root")
            dst.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
            dst.cell(row, 2, label)
            selection = occurrence.get("selection_context", {})
            count_text = f"{len(members)} questions"
            if selection.get("mode") == "random":
                count_text = f"{len(members)} candidates; {selection.get('draw_count')} drawn per attempt"
            dst.cell(row, prompt_col, count_text)
            dst.row_dimensions[row].height = max(44, math.ceil(len(label)/62)*15)
            for cell in dst[row]:
                cell.fill = PatternFill("solid", fgColor=PALE)
                cell.font = Font(name="Calibri", size=11, bold=True, color=TEAL)
                cell.alignment = Alignment(wrap_text=True, vertical="center")
            row += 1
            start = row
            for source_row, values, occurrence in members:
                qindex, question = questions[values["question_entity_key"]]
                display = _presentation(question, values, qindex)
                display["source_points"] = occurrence.get("points")
                diagnostics = occurrence.get("diagnostic_ids", [])
                if diagnostics:
                    display["content_preservation"] += f"; {len(diagnostics)} source/projection diagnostics"
                for header in original_headers:
                    _copy_cell(src.cell(source_row, columns[header]), dst.cell(row, dest_columns[header]))
                for header in ("question_text", "response_options", "answer_key", *EXTRA_HEADERS):
                    cell = dst.cell(row, dest_columns[header], display.get(header))
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                dst.cell(row, dest_columns["Source pool / folder"], label)
                lines = 1
                for col, header in enumerate(headers, 1):
                    cell = dst.cell(row, col)
                    cell.font = Font(name="Calibri", size=11, color="0563C1" if cell.hyperlink else "222222")
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                    cell.protection = Protection(locked=header not in EDITABLE_HEADERS)
                    if header in EDITABLE_HEADERS:
                        cell.fill = PatternFill("solid", fgColor=YELLOW)
                    if header in VISIBLE:
                        width = dst.column_dimensions[get_column_letter(col)].width
                        lines = max(lines, sum(max(1, math.ceil(len(s)/width)) for s in str(cell.value or "").split("\n")))
                dst.row_dimensions[row].height = min(409, max(54, lines*15 + 12))
                tab["rows"].append({"row": row, "native_row": source_row, "occurrence_key": values["occurrence_key"],
                                    "rich_revision_hold": bool(display["rich_revision_hold"])})
                row += 1
            dst.row_dimensions.group(start, row-1, outline_level=1, hidden=False)
        for r in (1, 2):
            for cell in dst[r]:
                cell.fill = PatternFill("solid", fgColor=TEAL)
                cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                cell.alignment = Alignment(wrap_text=True, vertical="center")
        # Copy native dropdowns by header and by question row, never group headings.
        for validation in src.data_validations.dataValidation:
            new = copy(validation)
            new.sqref = ""
            for item in tab["rows"]:
                for header, col in columns.items():
                    if f"{get_column_letter(col)}{item['native_row']}" in validation:
                        new.add(dst.cell(item["row"], dest_columns[header]))
            if str(new.sqref):
                dst.add_data_validation(new)
        dst.protection.sheet = True
        dst.protection.selectLockedCells = False
        dst.protection.selectUnlockedCells = False
        dst.protection.formatRows = False
        dst.protection.formatColumns = False
        spec["sheets"].append(tab)
    # The inventory is a readable source view too. Stable source aliases bind it
    # to model entities; ambiguous aliases are left intact instead of guessed.
    if "All Questions" in book:
        inventory = book["All Questions"]
        ih = {c.value: c.column for c in inventory[1]}
        aliases = {}
        for key, (qi, question) in questions.items():
            for alias in question.get("identity", {}).get("source_aliases", []):
                value = alias.get("value")
                aliases.setdefault(value, set()).add(key)
        for name in EXTRA_HEADERS[:3]:
            ih[name] = inventory.max_column + 1
            inventory.cell(1, ih[name], name)
            inventory.column_dimensions[get_column_letter(ih[name])].hidden = True
        for row in range(2, inventory.max_row + 1):
            candidates = set()
            for h in ("source_global_id", "source_question_ident"):
                if h in ih:
                    candidates.update(aliases.get(inventory.cell(row, ih[h]).value, set()))
            if len(candidates) != 1:
                continue
            qi, question = questions[next(iter(candidates))]
            old = {h: inventory.cell(row, col).value for h, col in ih.items()}
            display = _presentation(question, old, qi)
            for h in ("question_text", "response_options", "answer_key", *EXTRA_HEADERS[:3]):
                cell = inventory.cell(row, ih[h], display.get(h))
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        if "question_type" in ih:
            dim = inventory.column_dimensions[get_column_letter(ih["question_type"])]
            dim.hidden, dim.width = False, 24
    # Native evidence tabs are snapshots. Quiz Settings remains an active native surface.
    for sheet in book:
        if sheet.title in {"START HERE", "Quiz Settings", "Question Images", "All Questions"} or sheet.title in {s["title"] for s in spec["sheets"]}:
            sheet.sheet_state = "visible"
        else:
            sheet.sheet_state = "hidden"
        if sheet.title not in {s["title"] for s in spec["sheets"]} | {"Quiz Settings"}:
            for cells in sheet:
                for cell in cells:
                    cell.protection = Protection(locked=True)
            sheet.protection.sheet = True
    marker = book.create_sheet(MARKER)
    marker.append(["schema", SCHEMA])
    marker.sheet_state = "veryHidden"
    marker.protection.sheet = True
    book.active = 0
    baseline = output_dir / BASELINE
    book.save(baseline)
    shutil.copyfile(baseline, output_dir / WORKING)
    spec["baseline"] = _ref(output_dir, baseline)
    _json(output_dir / LAYOUT, spec)
    (output_dir / "README.md").write_text(
        "# Assessment review\n\nOpen `reviewer_working.xlsx` and start with START HERE. "
        "Edit the yellow fields on assessment tabs. Keep this entire folder together; "
        "Images/ holds the linked diagrams. Hidden source sheets and companion files are required for import.\n\n"
        "Use Quiz Workshop Compose, or run from the tool repository:\n\n"
        "```sh\npython scripts/quiz_assessment_review.py import --packet /path/to/this-folder "
        "--output /path/to/decisions.json\n```\n\n"
        "A downloaded or renamed edited copy can be supplied with `--edited-workbook /path/to/edited.xlsx`. "
        "Keep the baseline and companion files unchanged. Import collects decisions; use the original full "
        "extraction workspace for readiness and package building.\n", encoding="utf-8")
    return spec


def validate_packet(packet_dir: Path) -> dict:
    packet_dir = Path(packet_dir)
    spec = json.loads((packet_dir / LAYOUT).read_text(encoding="utf-8"))
    if spec.get("schema") != SCHEMA:
        raise ReingestError("Unknown assessment review packet version")
    # A copied packet must have ordinary local companions, not symlinked folders.
    if packet_dir.is_symlink() or any(p.is_symlink() for p in packet_dir.rglob("*")):
        raise ReingestError("Review packets cannot contain symlinks")
    for ref in [spec["model"], spec["native_workbook"], spec["baseline"], *spec["assets"]]:
        path = _inside(packet_dir, ref["path"])
        if not path.is_file() or sha256_file(path) != ref["sha256"]:
            raise ReingestError("Review companion missing or changed: " + ref["path"])
    return spec


def materialize_assessment_decisions(model_path: Path, baseline_path: Path, edited_path: Path, *, metadata_policy="optional") -> dict:
    """Validate the grouped source view, then collect edits through native reingest."""
    packet = baseline_path.parent
    spec = validate_packet(packet)
    if spec.get("profile") == "inventory_only":
        raise ReingestError("This library-only inventory has no assessment edits to import; use the blank drafting template for new assessments")
    if sha256_file(baseline_path) != spec["baseline"]["sha256"] or sha256_file(model_path) != spec["model"]["sha256"]:
        raise ReingestError("Assessment review model or baseline does not match the packet")
    baseline, edited = load_workbook(baseline_path), load_workbook(edited_path)
    if set(baseline.sheetnames) != set(edited.sheetnames):
        raise ReingestError("Assessment review sheets were added or removed")
    native_path = _inside(packet, spec["native_workbook"]["path"])
    normalized = load_workbook(native_path)
    native_columns = {c.value: c.column for c in normalized["Quiz Questions"][1]}
    native_sheet = normalized["Quiz Questions"]
    expected_keys = {native_sheet.cell(r, native_columns["occurrence_key"]).value
                     for r in range(2, native_sheet.max_row+1)}
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model_questions = {q["entity_key"]: (i, q) for i, q in enumerate(model["questions"])}
    occurrence_quizzes = {o["occurrence_key"]: o["quiz_key"] for o in
                          build_quiz_review_projection(model)["question_occurrences"]}
    seen = set()
    asset_paths = {ref["path"] for ref in spec["assets"]}
    allowed = {}
    for tab in spec["sheets"]:
        sheet = baseline[tab["title"]]
        headers = {c.value: c.column for c in sheet[2]}
        for item in tab["rows"]:
            key = item["occurrence_key"]
            row, native_row = item["row"], item["native_row"]
            if (key in seen or key not in expected_keys or
                sheet.cell(row, headers["occurrence_key"]).value != key or
                native_sheet.cell(native_row, native_columns["occurrence_key"]).value != key or
                occurrence_quizzes.get(key) != tab["quiz_key"]):
                raise ReingestError("Assessment layout does not match source occurrence identities")
            values = {h: native_sheet.cell(native_row, c).value for h, c in native_columns.items()}
            qi, question = model_questions[values["question_entity_key"]]
            held = bool(_presentation(question, values, qi)["rich_revision_hold"])
            if item["rich_revision_hold"] is not held:
                raise ReingestError("Assessment layout rich-content policy differs from source")
            seen.add(key)
        allowed[tab["title"]] = {(r["row"], headers[h]) for r in tab["rows"] for h in EDITABLE_HEADERS}
    if seen != expected_keys or spec["question_count"] != len(expected_keys):
        raise ReingestError("Assessment layout does not cover all source occurrences")
    if "Quiz Settings" in baseline:
        sheet = baseline["Quiz Settings"]
        allowed["Quiz Settings"] = {(r, c.column) for r in range(2, sheet.max_row+1)
                                    for c in sheet[1] if c.value in SETTINGS_EDITABLE_HEADERS}
    for before in baseline:
        after = edited[before.title]
        permitted = allowed.get(before.title, set())
        for cells in after:
            for cell in cells:
                if cell.data_type == "f":
                    raise ReingestError(f"Formula cells are not accepted ({after.title}!{cell.coordinate})")
        for r in range(1, max(before.max_row, after.max_row)+1):
            for c in range(1, max(before.max_column, after.max_column)+1):
                a, b = before.cell(r, c), after.cell(r, c)
                if (r, c) in permitted:
                    continue
                # Blank and empty-string serialize identically across spreadsheet editors.
                av = None if a.value in (None, "") else a.value
                bv = None if b.value in (None, "") else b.value
                if av != bv:
                    raise ReingestError(f"Source or layout cell changed: {before.title}!{a.coordinate}")
                if (a.hyperlink.target if a.hyperlink else None) != (b.hyperlink.target if b.hyperlink else None):
                    raise ReingestError(f"Source image link changed: {before.title}!{a.coordinate}")
                if a.hyperlink and a.hyperlink.target and a.hyperlink.target.startswith("Images/"):
                    if a.hyperlink.target not in asset_paths:
                        raise ReingestError("Image link is missing from the packet asset manifest")
    for tab in spec["sheets"]:
        sheet = edited[tab["title"]]
        headers = {c.value: c.column for c in sheet[2]}
        for item in tab["rows"]:
            row = item["row"]
            status = str(sheet.cell(row, headers["approval_status"]).value or "").lower().strip()
            if item["rich_revision_hold"] and status == "accepted" and any(
                sheet.cell(row, headers[h]).value not in (None, "") for h in
                ("revised_question_text", "revised_response_options", "revised_answer_key")):
                raise ReingestError("Accepted equation/image revisions need a supported rich-content path; retain as an open draft")
            for header in EDITABLE_HEADERS:
                normalized["Quiz Questions"].cell(item["native_row"], native_columns[header]).value = sheet.cell(row, headers[header]).value
    if "Quiz Settings" in normalized:
        for cells in edited["Quiz Settings"]:
            for cell in cells:
                normalized["Quiz Settings"].cell(cell.row, cell.column).value = cell.value
    with tempfile.TemporaryDirectory(prefix="quiz-assessment-import-") as scratch:
        normalized_path = Path(scratch) / "native-review.xlsx"
        normalized.save(normalized_path)
        overlay = materialize_workbook_decisions(model_path, native_path, normalized_path, metadata_policy=metadata_policy)
    overlay["workbooks"] = {"baseline_sha256": sha256_file(baseline_path), "edited_sha256": sha256_file(edited_path)}
    overlay["assessment_review"] = {"schema": SCHEMA, "layout_sha256": sha256_file(packet / LAYOUT), "question_count": spec["question_count"]}
    return overlay


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Create a new portable review folder from native extraction")
    prepare.add_argument("--source-workbook", type=Path, required=True)
    prepare.add_argument("--model", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    collect = commands.add_parser("import", help="Collect assessment edits into a decision overlay")
    collect.add_argument("--packet", type=Path, required=True)
    collect.add_argument("--edited-workbook", type=Path)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--metadata-policy", choices=("optional", "required"), default="optional")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_review_packet(args.source_workbook, args.model, args.output_dir)
            print(f"Created {len(result['sheets'])} assessment tabs, {result['question_count']} quiz occurrences and {len(result['assets'])} image files in {args.output_dir}")
        else:
            if args.output.exists():
                raise ValueError("Choose a new output filename; existing decisions will not be overwritten")
            result = materialize_assessment_decisions(args.packet / "model.json", args.packet / BASELINE,
                args.edited_workbook or args.packet / WORKING, metadata_policy=args.metadata_policy)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            _json(args.output, result)
            print(json.dumps(result["content_minimized_summary"]))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"assessment review: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
