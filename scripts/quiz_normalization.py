#!/usr/bin/env python3
"""Normalize canonical quiz extraction rows into the CourseCraft quiz contracts.

The legacy quiz review payload remains a supported projection.  This module
adds safe ZIP intake, exact source fingerprints, evidence-aware normalization,
and run receipts without making downstream tools parse Brightspace XML again.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import html
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterator
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

from common_xml import local_name
from quiz_contracts import (
    SCHEMA_REGISTRY,
    make_content_fingerprint,
    make_entity_key,
    make_source_key,
    validate_contract,
)


QUESTION_KIND_MAP = {
    "Multiple Choice": "multiple_choice",
    "True/False": "true_false",
    "Multi-Select": "multi_select",
    "Multi Select": "multi_select",
    "Multiple Response": "multi_select",
    "Short Answer": "short_answer",
    "Multi-Short Answer": "multi_short_answer",
    "Fill in the Blanks": "fill_in_blanks",
    "Long Answer": "long_answer",
    "Matching": "matching",
    "Ordering": "ordering",
}
EXPORT_NAME_PATTERN = re.compile(
    r"D2LExport_(?P<org_unit_id>\d+)_(?P<course_code>.+?)_(?P<timestamp>\d{8,14})(?:_|$|\.)",
    re.IGNORECASE,
)
SENSITIVE_SETTING_NAMES = {"password"}
MAX_ZIP_MEMBERS = 100_000
MAX_ZIP_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
D2L_QTI_NAMESPACE = "http://desire2learn.com/xsd/d2lcp_v2p0"
SHORT_ANSWER_AWARD = "100.000000000"


@dataclass(frozen=True)
class ResolvedExport:
    source_dir: Path
    source_arg: Path
    source_kind: str
    export_label: str


@dataclass(frozen=True)
class ShortAnswerOccurrence:
    values: tuple[str, ...]
    signature: tuple[Any, ...]


def _is_safe_zip_member(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    mode = info.external_attr >> 16
    is_symlink = (mode & 0o170000) == 0o120000
    return not path.is_absolute() and ".." not in path.parts and not is_symlink


def _find_export_root(root: Path) -> Path:
    """Locate one quiz export, allowing quiz-only packages without questiondb."""
    direct_quizzes = sorted(root.glob("quiz_d2l_*.xml"))
    if (root / "questiondb.xml").exists() or direct_quizzes:
        return root
    candidates = {
        path.parent
        for pattern in ("questiondb.xml", "quiz_d2l_*.xml")
        for path in root.rglob(pattern)
    }
    if len(candidates) == 1:
        return candidates.pop()
    if not candidates:
        raise FileNotFoundError(
            f"Could not find questiondb.xml or quiz_d2l_*.xml beneath {root}"
        )
    rendered = ", ".join(sorted(str(path.relative_to(root)) for path in candidates))
    raise FileExistsError(
        f"Found multiple quiz export roots beneath {root}: {rendered}"
    )


@contextmanager
def resolve_export(source: Path) -> Iterator[ResolvedExport]:
    """Resolve a Brightspace ZIP/folder and clean up any temporary extraction."""
    source = source.expanduser().resolve()
    if source.is_dir():
        source_dir = _find_export_root(source)
        yield ResolvedExport(source_dir, source, "directory", source.name)
        return
    if not source.is_file() or not zipfile.is_zipfile(source):
        raise ValueError(
            "Input must be a Brightspace export ZIP or unpacked export folder."
        )
    with tempfile.TemporaryDirectory(prefix="coursecraft_quiz_") as temp_name:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise ValueError(
                    f"ZIP contains too many members ({len(members)} > {MAX_ZIP_MEMBERS})."
                )
            uncompressed_bytes = sum(info.file_size for info in members)
            if uncompressed_bytes > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"ZIP expands beyond the {MAX_ZIP_UNCOMPRESSED_BYTES}-byte safety limit."
                )
            unsafe = [
                info.filename for info in members if not _is_safe_zip_member(info)
            ]
            if unsafe:
                raise ValueError(f"ZIP contains unsafe path or symlink: {unsafe[0]}")
            archive.extractall(temp_name)
        source_dir = _find_export_root(Path(temp_name))
        yield ResolvedExport(source_dir, source, "zip", source.stem)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file_set(
    source_dir: Path, *, sha256_cache: dict[Path, str] | None = None
) -> tuple[str, int, int]:
    """Hash relative paths, bytes, and file hashes for an exact export snapshot."""
    digest = hashlib.sha256()
    total_bytes = 0
    count = 0
    for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(source_dir).as_posix()
        size = path.stat().st_size
        cache_key = path.resolve()
        if sha256_cache is not None and cache_key in sha256_cache:
            file_digest = sha256_cache[cache_key]
        else:
            file_digest = sha256_file(path)
            if sha256_cache is not None:
                sha256_cache[cache_key] = file_digest
        digest.update(
            json.dumps([relative, size, file_digest], separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
        total_bytes += size
        count += 1
    return digest.hexdigest(), total_bytes, count


def derive_source_lineage_key(source_arg: Path, source_dir: Path) -> tuple[str, str]:
    """Use source-backed course identity; otherwise mark lineage as unresolved."""
    orgunit_path = source_dir / "orgunitconfig" / "orgunitconfig.xml"
    if orgunit_path.exists():
        try:
            root = ET.parse(orgunit_path).getroot()
            identifier = next(
                (
                    value
                    for key, value in root.attrib.items()
                    if local_name(key) == "identifier" and value
                ),
                "",
            )
            if identifier:
                token = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:20]
                return f"cc:lineage:d2l-orgunit:{token}", "orgunitconfig identifier"
        except ET.ParseError:
            pass
    for candidate in (source_arg.stem, source_arg.name, source_dir.name):
        match = EXPORT_NAME_PATTERN.search(candidate or "")
        if match:
            basis = f"{match.group('org_unit_id')}|{match.group('course_code')}"
            token = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
            return f"cc:lineage:d2l-export:{token}", "D2L export name"
    token = hashlib.sha256(source_arg.name.encode("utf-8")).hexdigest()[:20]
    return f"cc:lineage:unresolved:{token}", "unresolved source label"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr", "td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(raw: str) -> str:
    decoded = raw
    while True:
        next_value = html.unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    parser = _TextExtractor()
    parser.feed(decoded)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _element_text(element: ET.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if local_name(node.tag) == "mattext" and node.text:
            text = _plain_text(node.text)
            if text:
                parts.append(text)
    return " | ".join(dict.fromkeys(parts))


def extract_feedback(item: ET.Element) -> tuple[dict[str, str], list[str]]:
    """Classify QTI feedback while preserving ambiguous blocks as general."""
    feedback_map: dict[str, str] = {}
    for feedback in item.iter():
        if local_name(feedback.tag) != "itemfeedback":
            continue
        ident = feedback.attrib.get("ident", "")
        text = _element_text(feedback)
        if ident and text:
            feedback_map[ident] = text
    if not feedback_map:
        return {}, []

    general: set[str] = set()
    correct: set[str] = set()
    incorrect: set[str] = set()
    choice_parts: list[str] = []
    referenced: set[str] = set()
    notes: list[str] = []
    for condition in (
        node for node in item.iter() if local_name(node.tag) == "respcondition"
    ):
        setvars = [
            node for node in condition.iter() if local_name(node.tag) == "setvar"
        ]
        varnames = {node.attrib.get("varname", "") for node in setvars}
        scores: list[float] = []
        for node in setvars:
            try:
                scores.append(float((node.text or "").strip()))
            except ValueError:
                pass
        chosen = [
            (node.text or "").strip()
            for node in condition.iter()
            if local_name(node.tag) == "varequal" and (node.text or "").strip()
        ]
        refs = [
            node.attrib.get("linkrefid", "")
            for node in condition.iter()
            if local_name(node.tag) == "displayfeedback"
            and node.attrib.get("linkrefid", "") in feedback_map
        ]
        if not refs:
            continue
        referenced.update(refs)
        if "D2L_Correct" in varnames or any(score > 0 for score in scores):
            correct.update(refs)
        elif "D2L_Incorrect" in varnames or (
            scores and all(score <= 0 for score in scores)
        ):
            incorrect.update(refs)
        elif chosen:
            for ref in refs:
                choice_parts.append(f"{','.join(chosen)}: {feedback_map[ref]}")
        else:
            general.update(refs)
    unused = set(feedback_map) - referenced
    if unused:
        general.update(unused)
        if referenced:
            notes.append("Unlinked feedback blocks were preserved as general feedback.")
    if (correct & incorrect) or (correct & general) or (incorrect & general):
        notes.append("One or more feedback blocks were routed to multiple channels.")
    result = {
        "general_feedback": " | ".join(feedback_map[key] for key in sorted(general)),
        "correct_feedback": " | ".join(feedback_map[key] for key in sorted(correct)),
        "incorrect_feedback": " | ".join(
            feedback_map[key] for key in sorted(incorrect)
        ),
        "answer_specific_feedback": " | ".join(choice_parts),
    }
    return result, notes


def _item_aliases(item: ET.Element) -> list[str]:
    aliases = [item.attrib.get("ident", ""), item.attrib.get("label", "")]
    labels: dict[str, str] = {}
    for field in (
        node for node in item.iter() if local_name(node.tag) == "qti_metadatafield"
    ):
        label = next(
            (node.text or "" for node in field if local_name(node.tag) == "fieldlabel"),
            "",
        ).strip()
        value = next(
            (node.text or "" for node in field if local_name(node.tag) == "fieldentry"),
            "",
        ).strip()
        if label:
            labels[label] = value
    aliases.extend([labels.get("qmd_displayid", ""), labels.get("qmd_globalid", "")])
    return [alias for alias in aliases if alias]


def collect_question_facts(source_dir: Path) -> dict[str, dict[str, Any]]:
    """Index feedback/raw item facts by every stable alias seen in source XML."""
    facts: dict[str, dict[str, Any]] = {}
    paths = [source_dir / "questiondb.xml", *sorted(source_dir.glob("quiz_d2l_*.xml"))]
    for path in paths:
        if not path.exists():
            continue
        root = ET.parse(path).getroot()
        for item in (node for node in root.iter() if local_name(node.tag) == "item"):
            feedback, notes = extract_feedback(item)
            fact = {
                **feedback,
                "feedback_notes": notes,
                "source_file": path.name,
                "aliases": _item_aliases(item),
            }
            for alias in fact["aliases"]:
                facts.setdefault(alias, fact)
    return facts


def _normalize_scalar(value: str) -> Any:
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered in {"yes", "true"}:
        return True
    if lowered in {"no", "false"}:
        return False
    if re.fullmatch(r"-?\d+", cleaned):
        return int(cleaned)
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        return float(cleaned)
    return cleaned


def collect_quiz_settings(source_dir: Path) -> dict[str, list[dict[str, Any]]]:
    observations: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(source_dir.glob("quiz_d2l_*.xml")):
        root = ET.parse(path).getroot()
        assessment = next(
            (node for node in root.iter() if local_name(node.tag) == "assessment"), None
        )
        if assessment is None:
            continue
        rows: list[dict[str, Any]] = []
        for child in list(assessment):
            child_name = local_name(child.tag)
            if child_name == "assessmentcontrol":
                for name, raw in sorted(child.attrib.items()):
                    rows.append(
                        {
                            "name": local_name(name),
                            "raw": raw,
                            "normalized": _normalize_scalar(raw),
                            "source_kind": "assessmentcontrol.attribute",
                        }
                    )
            elif child_name in {"assess_procextension", "assessfeedback"}:
                for setting in list(child):
                    name = local_name(setting.tag)
                    raw: Any = (setting.text or "").strip()
                    if setting.attrib:
                        raw = {
                            "value": raw,
                            "attributes": {
                                local_name(key): value
                                for key, value in setting.attrib.items()
                            },
                        }
                    sensitive = name.lower() in SENSITIVE_SETTING_NAMES
                    rows.append(
                        {
                            "name": name,
                            "raw": "[redacted]" if sensitive else raw,
                            "normalized": (
                                "[redacted]"
                                if sensitive
                                else (
                                    _normalize_scalar(raw)
                                    if isinstance(raw, str)
                                    else raw
                                )
                            ),
                            "source_kind": child_name,
                            "sensitive": sensitive,
                        }
                    )
        observations[path.name] = rows
    return observations


def enrich_payload(payload: dict[str, Any], source_dir: Path) -> None:
    """Add portable extractor facts to canonical rows (additive fields only)."""
    facts = collect_question_facts(source_dir)
    settings = collect_quiz_settings(source_dir)
    for row in payload["quiz_question_rows"]:
        aliases = [
            row.get("pool_question_globalid"),
            row.get("pool_question_displayid"),
            row.get("pool_question_label"),
            row.get("pool_question_ident"),
            row.get("qmd_globalid"),
            row.get("qmd_displayid"),
            row.get("quiz_item_label"),
            row.get("quiz_item_ident"),
        ]
        fact = next(
            (facts[str(alias)] for alias in aliases if alias and str(alias) in facts),
            {},
        )
        for field in (
            "general_feedback",
            "correct_feedback",
            "incorrect_feedback",
            "answer_specific_feedback",
        ):
            row[field] = fact.get(field, "")
        row["feedback_notes"] = " | ".join(fact.get("feedback_notes", []))
    # Library records resolve feedback through the same alias lookup. Their
    # aliases are their own identifiers, since a library record is not placed
    # and therefore has no quiz-item alias to fall back on.
    for row in payload.get("library_question_rows", []):
        aliases = [
            row.get("qmd_globalid"),
            row.get("qmd_displayid"),
            row.get("question_label"),
            row.get("question_ident"),
        ]
        fact = next(
            (facts[str(alias)] for alias in aliases if alias and str(alias) in facts),
            {},
        )
        for field in (
            "general_feedback",
            "correct_feedback",
            "incorrect_feedback",
            "answer_specific_feedback",
        ):
            row[field] = fact.get(field, "")
        row["feedback_notes"] = " | ".join(fact.get("feedback_notes", []))
    for row in payload["quiz_summary_rows"]:
        observed = settings.get(str(row["quiz_file"]), [])
        by_name = {item["name"]: item for item in observed}
        row["attempts_allowed"] = by_name.get("attempts_allowed", {}).get(
            "normalized", ""
        )
        row["time_limit_minutes"] = by_name.get("time_limit", {}).get("normalized", "")
        row["shuffle_questions"] = by_name.get("shuffle_questions", {}).get(
            "normalized", ""
        )
        row["shuffle_answers"] = by_name.get("shuffle_answers", {}).get(
            "normalized", ""
        )


def copy_resolved_assets(
    payload: dict[str, Any],
    source_dir: Path,
    output_dir: Path,
    asset_dir_name: str,
) -> int:
    """Create stable reviewer copies without replacing source package paths."""
    asset_dir = output_dir / asset_dir_name
    copied_by_package_path: dict[str, Path] = {}

    def copy_package_path(package_path: str) -> Path | None:
        package_path = str(package_path or "").strip()
        if not package_path:
            return None
        existing = copied_by_package_path.get(package_path)
        if existing is not None:
            return existing
        safe_parts = [
            part
            for part in PurePosixPath(package_path.replace("\\", "/")).parts
            if part not in {"", ".", ".."}
        ]
        if not safe_parts:
            return None
        source_path = source_dir.joinpath(*safe_parts)
        if not source_path.is_file():
            return None
        destination = asset_dir.joinpath(*safe_parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or sha256_file(destination) != sha256_file(
            source_path
        ):
            shutil.copy2(source_path, destination)
        copied_by_package_path[package_path] = destination
        return destination

    for image in payload["question_image_rows"]:
        package_path = str(image.get("image_file_path", "") or "")
        if image.get("image_status") != "resolved":
            continue
        destination = copy_package_path(package_path)
        if destination is None:
            continue
        image["image_file_absolute_path"] = str(destination)
        image["review_copy_path"] = destination.relative_to(output_dir).as_posix()

    for row in payload.get("library_question_rows", []):
        for package_path in _split(row.get("question_image_paths")):
            copy_package_path(package_path)

    for row in [
        *payload.get("quiz_question_rows", []),
        *payload.get("library_question_rows", []),
        *payload.get("unresolved_rows", []),
    ]:
        copied_paths = [
            copied_by_package_path[path]
            for path in _split(row.get("question_image_paths"))
            if path in copied_by_package_path
        ]
        if copied_paths:
            row["question_image_review_copy_paths"] = " || ".join(
                path.relative_to(output_dir).as_posix() for path in copied_paths
            )
        primary = str(row.get("question_primary_image_path", "") or "")
        if primary in copied_by_package_path:
            row["question_primary_image_absolute_path"] = str(
                copied_by_package_path[primary]
            )
            row["question_primary_image_review_copy_path"] = (
                copied_by_package_path[primary].relative_to(output_dir).as_posix()
            )
    return len(copied_by_package_path)


def _token(*parts: Any, length: int = 24) -> str:
    raw = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _source_alias(namespace: str, value: str, evidence: list[str]) -> dict[str, Any]:
    scope = (
        "file"
        if any(
            token in namespace
            for token in ("quiz_file", "quiz_item", "quiz_section")
        )
        else "course"
    )
    return {
        "namespace": namespace,
        "value": value,
        "scope": scope,
        "source_evidence_keys": evidence,
        "extensions": {},
    }


def _identity(
    namespace: str, value: str, evidence: list[str], content: Any = None
) -> dict[str, Any]:
    return {
        "strategy": "source_alias",
        "permanent_code": None,
        "source_aliases": [_source_alias(namespace, value, evidence)],
        "content_fingerprints": (
            [make_content_fingerprint(content, basis="normalized projection")]
            if content is not None
            else []
        ),
        "extensions": {},
    }


def _entity_key(kind: str, lineage: str, namespace: str, value: str) -> str:
    return make_entity_key(kind, lineage, alias_namespace=namespace, alias_value=value)


def _formatted(content: str) -> dict[str, Any]:
    return {"format": "plain_text", "content": content, "extensions": {}}


def _split(value: Any, separator: str = " || ") -> list[str]:
    return [part.strip() for part in str(value or "").split(separator) if part.strip()]


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if "absolute_path" not in key and key != "source_response_facts"
    }


def _source_response_raw_model(
    row: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any] | None:
    facts = row.get("source_response_facts")
    if not isinstance(facts, dict):
        return None
    if facts.get("schema") != "coursecraft.quiz_source_response_facts/0":
        return None
    return {
        "source_kind": "d2l_qti_response_facts/0",
        "payload": facts,
        "source_evidence_keys": list(evidence),
        "extensions": {},
    }


def _fact_node(
    value: object,
    name: str,
    namespace_uri: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "qualified_name",
        "namespace_uri",
        "name",
        "attributes",
        "raw_text",
        "raw_tail",
        "children",
    }:
        return None
    expected_qualified = (
        f"{{{namespace_uri}}}{name}" if namespace_uri is not None else name
    )
    if (
        value.get("qualified_name") != expected_qualified
        or value.get("namespace_uri") != namespace_uri
        or value.get("name") != name
        or not isinstance(value.get("attributes"), list)
        or not isinstance(value.get("children"), list)
    ):
        return None
    return value


def _fact_attributes(
    node: dict[str, Any],
) -> dict[tuple[str | None, str], str] | None:
    attributes: dict[tuple[str | None, str], str] = {}
    for attribute in node["attributes"]:
        if not isinstance(attribute, dict) or set(attribute) != {
            "qualified_name",
            "namespace_uri",
            "name",
            "raw_value",
        }:
            return None
        namespace_uri = attribute.get("namespace_uri")
        name = attribute.get("name")
        raw_value = attribute.get("raw_value")
        if (
            namespace_uri is not None
            and not isinstance(namespace_uri, str)
        ) or not isinstance(name, str) or not isinstance(raw_value, str):
            return None
        expected_qualified = (
            f"{{{namespace_uri}}}{name}" if namespace_uri is not None else name
        )
        if attribute.get("qualified_name") != expected_qualified:
            return None
        key = (namespace_uri, name)
        if key in attributes:
            return None
        attributes[key] = raw_value
    return attributes


def _fact_children(
    node: dict[str, Any],
    names: tuple[tuple[str, str | None], ...],
) -> list[dict[str, Any]] | None:
    if len(node["children"]) != len(names):
        return None
    children: list[dict[str, Any]] = []
    for value, (name, namespace_uri) in zip(node["children"], names):
        child = _fact_node(value, name, namespace_uri)
        if child is None:
            return None
        children.append(child)
    return children


def _fact_structural_text_is_empty(node: dict[str, Any]) -> bool:
    return (
        not str(node.get("raw_text") or "").strip()
        and not str(node.get("raw_tail") or "").strip()
    )


def _is_exact_ordinal(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _single_fact_tree(
    facts: dict[str, Any],
    key: str,
    element_name: str,
) -> dict[str, Any] | None:
    value = facts.get(key)
    if (
        not isinstance(value, dict)
        or set(value) != {"state", "occurrences"}
        or value.get("state") != "present"
    ):
        return None
    occurrences = value.get("occurrences")
    if not isinstance(occurrences, list) or len(occurrences) != 1:
        return None
    occurrence = occurrences[0]
    if (
        not isinstance(occurrence, dict)
        or set(occurrence) != {"ordinal", "node"}
        or not _is_exact_ordinal(occurrence.get("ordinal"), 1)
    ):
        return None
    return _fact_node(occurrence.get("node"), element_name)


CHOICE_SOURCE_KINDS = {"multiple_choice", "true_false", "multi_select", "ordering"}


def _choice_fact_element(node: object) -> ET.Element:
    """Reconstruct an evidence tree without interpreting or rewriting its text."""
    if not isinstance(node, dict):
        raise ValueError("Missing source XML tree.")
    checked = _fact_node(node, node.get("name"), node.get("namespace_uri"))
    attributes = _fact_attributes(node) if checked is not None else None
    if attributes is None or any(
        value is not None and not isinstance(value, str)
        for value in (node.get("raw_text"), node.get("raw_tail"))
    ):
        raise ValueError("Malformed source XML tree.")
    element = ET.Element(node["qualified_name"], {
        (f"{{{ns}}}{name}" if ns else name): value
        for (ns, name), value in attributes.items()
    })
    element.text, element.tail = node["raw_text"], node["raw_tail"]
    element.extend(_choice_fact_element(child) for child in node["children"])
    return element


def _source_prompt_content(row: dict[str, Any], display_field: str) -> dict[str, Any] | None:
    """Preserve bounded prompt material, excluding response/answer material."""
    fallback = _formatted(str(row[display_field])) if row.get(display_field) else None
    # Other kinds retain their existing extraction-only projections. In
    # particular, inline blanks need a separate response/material contract.
    kind = QUESTION_KIND_MAP.get(str(row.get("question_type", "")), "unknown")
    if kind not in {"multiple_choice", "true_false", "multi_select", "long_answer"}:
        return fallback
    facts = row.get("source_response_facts")
    if not isinstance(facts, dict):
        return fallback
    try:
        presentation = _choice_fact_element(_single_fact_tree(facts, "presentation", "presentation"))
        parts: list[tuple[str, str]] = []

        def collect(element: ET.Element) -> None:
            if element.tag.startswith("response_"):
                return
            if element.tag in {"presentation", "flow", "flow_mat", "material"}:
                if (element.text or "").strip():
                    raise ValueError("Unrecognized text outside prompt material.")
                for child in element:
                    collect(child)
            elif element.tag == "mattext":
                texttype = element.get("texttype", "text/plain")
                if list(element) or texttype not in {"text/html", "text/plain"}:
                    raise ValueError("Prompt material requires an explicit rich-content projection.")
                parts.append(("html" if texttype == "text/html" else "plain_text", element.text or ""))
            else:
                raise ValueError("Prompt element requires an explicit projection: " + element.tag)

        collect(presentation)
        if not parts:
            raise ValueError("No supported source prompt material.")
        if len(parts) == 1:
            fmt, content = parts[0]
        elif any(fmt == "html" for fmt, _ in parts):
            fmt = "html"
            content = "\n".join(value if kind == "html" else html.escape(value) for kind, value in parts)
        else:
            fmt, content = "plain_text", "\n".join(value for _, value in parts)
        return {"format": fmt, "content": content, "extensions": {}}
    except (ValueError, TypeError) as exc:
        fallback = fallback or _formatted("")
        fallback["extensions"]["coursecraft.source_prompt_projection"] = {
            "state": "unresolved", "reason": str(exc),
        }
        return fallback


def source_choice_projection(facts: object) -> dict[str, Any]:
    """Recognize bounded native choice shapes, never infer keys from display text.

    Rich option material and source identifiers survive even when grading cannot
    be interpreted. Such a projection has no authoritative correctness flags.
    The unchanged complete XML fact trees remain the recovery authority.
    """
    result: dict[str, Any] = {"options": [], "recognized": False,
                              "scoring_mode": "unknown", "reason": ""}
    try:
        if not isinstance(facts, dict) or facts.get("schema") != "coursecraft.quiz_source_response_facts/0":
            raise ValueError("Source response facts are missing or unsupported.")
        kind = QUESTION_KIND_MAP.get(str(facts.get("source_question_type")), "unknown")
        if kind not in CHOICE_SOURCE_KINDS:
            raise ValueError("Source type is not a supported choice projection.")
        presentation = _choice_fact_element(_single_fact_tree(facts, "presentation", "presentation"))
        declarations = [e for e in presentation.iter() if e.tag in {"response_lid", "response_grp", "response_str"}]
        # Collect every option first. Do not silently drop textless, duplicated,
        # or unsupported material when the scoring shape needs human review.
        labels = [e for e in presentation.iter() if e.tag == "response_label"]
        content_supported = True
        for index, label in enumerate(labels):
            materials = [e for e in label.iter() if e.tag == "mattext"]
            parts = [{"format": "html" if e.get("texttype") == "text/html" else "plain_text",
                      "content": e.text or "", "extensions": {}} for e in materials]
            supported = len(parts) == 1 and all(
                e.tag in {"response_label", "flow_mat", "material", "mattext"}
                for e in label.iter()
            ) and all(e.get("texttype", "text/plain") in {"text/html", "text/plain"} and not list(e) for e in materials)
            content_supported &= supported
            content = parts[0] if len(parts) == 1 else {
                "format": "html", "content": "".join(
                    p["content"] if p["format"] == "html" else html.escape(p["content"])
                    for p in parts), "extensions": {"coursecraft.source_material_parts": parts}}
            result["options"].append({
                "option_key": chr(ord("A") + index) if index < 26 else f"OPT_{index + 1}",
                "source_ident": label.get("ident"), "content": content,
                "correct": None, "position": None,
            })
        ids = [o["source_ident"] for o in result["options"]]
        if not ids or any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Choice response IDs must be nonempty and unique.")
        if not content_supported:
            raise ValueError("Choice material needs an explicit rich-content projection.")
        if _item_metadata_value(facts, "qmd_questiontype") != facts["source_question_type"]:
            raise ValueError("Source question-type metadata is missing, repeated, or inconsistent.")
        if len(declarations) != 1:
            raise ValueError("Expected exactly one native choice declaration.")
        declaration = declarations[0]
        expected_cardinality = "Ordered" if kind == "ordering" else "Multiple" if kind == "multi_select" else "Single"
        if declaration.get("rcardinality") != expected_cardinality:
            raise ValueError("Native response cardinality conflicts with the declared question type.")
        expected_element = "response_grp" if kind == "ordering" else "response_lid"
        if declaration.tag != expected_element:
            raise ValueError("Unrecognized native response declaration.")
        renderers = [e for e in declaration if e.tag == "render_choice"]
        if len(renderers) != 1 or [e for e in renderers[0].iter() if e.tag == "response_label"] != labels:
            raise ValueError("Choice labels do not belong to one render_choice.")
        declaration_id = declaration.get("respident") if kind == "ordering" else declaration.get("ident")
        if not declaration_id:
            raise ValueError("Native response declaration has no identifier.")
        # Require the redundant fact summaries to agree with their immutable
        # XML trees. Consumers must not choose whichever representation fits.
        summaries = facts.get("response_declarations", [])
        if len(summaries) != 1 or ET.tostring(_choice_fact_element(summaries[0].get("tree"))) != ET.tostring(declaration):
            raise ValueError("Response declaration summaries disagree with source trees.")
        if [r.get("source_ident") for r in summaries[0].get("response_labels", [])] != ids:
            raise ValueError("Response label summaries disagree with source trees.")
        if facts.get("diagnostics"):
            raise ValueError("Source response facts contain unresolved shape diagnostics.")
        processing = _choice_fact_element(_single_fact_tree(facts, "response_processing", "resprocessing"))
        if processing.attrib or any(e.tag not in {"outcomes", "respcondition"} for e in processing):
            raise ValueError("Unrecognized response-processing operation.")
        conditions = [e for e in processing if e.tag == "respcondition"]
        recorded_conditions = facts.get("response_conditions", [])
        if len(conditions) != len(recorded_conditions) or any(
            ET.tostring(e) != ET.tostring(_choice_fact_element(row.get("tree")))
            for e, row in zip(conditions, recorded_conditions)
        ):
            raise ValueError("Scoring summaries disagree with source trees.")
        outcomes = [e for e in processing if e.tag == "outcomes"]
        if len(outcomes) > 1 or any(e.attrib for e in outcomes):
            raise ValueError("Unsupported source outcome declarations.")
        variables: dict[str, dict[str, Decimal | None]] = {}
        for outcome in outcomes:
            for variable in outcome:
                if (variable.tag != "decvar" or list(variable)
                    or set(variable.attrib) - {"varname", "vartype", "defaultval", "minvalue", "maxvalue"}
                    or variable.get("vartype", "Integer") not in {"Integer", "Decimal"}):
                    raise ValueError("Unsupported source outcome variable.")
                name = variable.get("varname", "SCORE")
                if not name or name in variables:
                    raise ValueError("Source outcome variable names must be unique.")
                values = {key: Decimal(variable.get(key)) if variable.get(key) is not None else (Decimal(0) if key == "defaultval" else None)
                          for key in ("defaultval", "minvalue", "maxvalue")}
                if any(value is not None and not value.is_finite() for value in values.values()):
                    raise ValueError("Nonfinite source outcome declaration.")
                if (values["minvalue"] is not None and values["maxvalue"] is not None
                    and values["minvalue"] > values["maxvalue"]):
                    raise ValueError("Source outcome bounds conflict.")
                variables[name] = values

        def outcome_default(name: str) -> Decimal | None:
            # QTI 1.2 XML Binding §§3.5.24/3.6.19/3.6.21 define implicit
            # integer SCORE=0, a missing decvar defaultval=0, and setvar's
            # omitted varname/action as SCORE/Set. Apply those defaults only
            # inside this validated grammar, never to undeclared named counters
            # or a conflicting explicit outcome program.
            if name in variables:
                return variables[name]["defaultval"]
            return Decimal(0) if name == "SCORE" and not variables else None

        def outcome_allows(name: str, *scores: Decimal) -> bool:
            limits = variables.get(name, {})
            return all((limits.get("minvalue") is None or value >= limits["minvalue"])
                       and (limits.get("maxvalue") is None or value <= limits["maxvalue"])
                       for value in scores)

        def condition_parts(condition: ET.Element) -> tuple[ET.Element, ET.Element, Decimal]:
            predicates = [e for e in condition if e.tag == "conditionvar"]
            setters = [e for e in condition if e.tag == "setvar"]
            if len(predicates) != 1 or len(setters) != 1 or any(e.tag not in {"conditionvar", "setvar", "displayfeedback"} for e in condition):
                raise ValueError("Unrecognized scoring condition structure.")
            setter = setters[0]
            if set(condition.attrib) - {"title", "continue"} or set(setter.attrib) - {"action", "varname"} or list(setter) or predicates[0].attrib:
                raise ValueError("Unsupported scoring condition attributes.")
            if condition.get("continue", "No").lower() not in {"yes", "no"}:
                raise ValueError("Unsupported scoring continuation value.")
            value = Decimal(setter.text or "")
            if not value.is_finite():
                raise ValueError("Nonfinite source score.")
            return predicates[0], setter, value
        correct: set[str] = set()
        positions: dict[str, int] = {}
        if kind in {"multiple_choice", "true_false"}:
            scores: dict[str, Decimal] = {}
            score_variables: set[str] = set()
            for condition in conditions:
                predicate, setter, value = condition_parts(condition)
                if setter.get("action", "Set") != "Set" or setter.get("varname", "SCORE") not in {"SCORE", "que_score"} or value not in {Decimal(0), Decimal(100)}:
                    raise ValueError("Single-choice grading is not a simple zero/full-credit key.")
                if len(predicate) != 1 or predicate[0].tag != "varequal" or list(predicate[0]):
                    raise ValueError("Single-choice grading has an ambiguous predicate.")
                test = predicate[0]
                oid = test.text or ""
                if set(test.attrib) != {"respident"} or test.get("respident") != declaration_id or oid not in ids or oid in scores:
                    raise ValueError("Single-choice scoring IDs do not join uniquely to options.")
                scores[oid] = value
                score_variables.add(setter.get("varname", "SCORE"))
            if len(score_variables) != 1 or set(variables) - score_variables:
                raise ValueError("Single-choice grading uses conflicting outcome variables.")
            score_variable = next(iter(score_variables))
            if not outcome_allows(score_variable, Decimal(0), Decimal(100)):
                raise ValueError("Source outcome bounds change the single-choice key.")
            default = outcome_default(score_variable)
            if default is not None and default != 0:
                raise ValueError("Single-choice default score is not zero.")
            if default == 0:
                for oid in ids:
                    scores.setdefault(oid, Decimal(0))
            if set(scores) != set(ids):
                raise ValueError("Single-choice scoring lacks option clauses or a supported zero default.")
            correct = {oid for oid, score in scores.items() if score == 100}
            if len(correct) != 1:
                raise ValueError("Single-choice source must key exactly one option.")
            result["scoring_mode"] = "exact"
        elif kind == "multi_select":
            # Recognize the established all-or-nothing conjunction shape only.
            # No OR, numeric partial credit, or counter programs are guessed.
            positive: list[ET.Element] = []
            score_variables: set[str] = set()
            positive_condition = None
            positive_action = None
            positive_value = None
            fallback_condition = None
            native_complement = None
            for condition in conditions:
                predicate, setter, value = condition_parts(condition)
                if setter.get("varname", "SCORE") not in {"SCORE", "que_score"}:
                    raise ValueError("Unsupported multi-select score variable.")
                score_variables.add(setter.get("varname", "SCORE"))
                if value == 0 and setter.get("action", "Set") == "Add":
                    if native_complement is not None:
                        raise ValueError("Multi-select contains repeated native complements.")
                    native_complement = condition
                    continue
                if value == 0 and setter.get("action", "Set") == "Set" and len(predicate) == 0:
                    if fallback_condition is not None:
                        raise ValueError("Multi-select contains repeated fallback conditions.")
                    fallback_condition = condition
                    continue
                if (setter.get("action", "Set"), value) not in {("Set", Decimal(100)), ("Add", Decimal(1))}:
                    raise ValueError("Unsupported multi-select grading program.")
                positive.append(predicate)
                positive_condition, positive_action, positive_value = condition, setter.get("action", "Set"), value
            if len(positive) != 1:
                raise ValueError("Multi-select must have one exact full-credit conjunction.")
            if len(score_variables) != 1 or set(variables) - score_variables:
                raise ValueError("Multi-select grading uses conflicting outcome variables.")
            score_variable = next(iter(score_variables))
            default = outcome_default(score_variable)
            if not outcome_allows(score_variable, Decimal(0), positive_value):
                raise ValueError("Source outcome bounds change the multi-select key.")
            if fallback_condition is not None and (conditions[-1] is not fallback_condition
                or positive_condition.get("continue", "No").lower() != "no"):
                raise ValueError("Multi-select continuation changes the keyed result.")
            if (fallback_condition is None or positive_action == "Add") and default != 0:
                raise ValueError("Multi-select grading requires a supported zero default.")
            if native_complement is not None:
                # Native D2L exports repeat the complete positive conjunction
                # under conditionvar/and/not, then Add 0. Match the complete
                # tree; arbitrary zero-valued programs remain unsupported.
                complement = native_complement.find("conditionvar")
                def signature(node):
                    return (node.tag, sorted(node.attrib.items()), (node.text or "").strip(),
                            tuple(signature(child) for child in node))
                if (fallback_condition is not None or len(conditions) != 2
                    or conditions[-1] is not native_complement or positive_action != "Add"
                    or positive_condition.get("continue", "").lower() != "yes"
                    or native_complement.get("continue", "").lower() != "no"
                    or len(complement) != 1 or complement[0].tag != "and" or complement[0].attrib
                    or len(complement[0]) != 1 or complement[0][0].tag != "not" or complement[0][0].attrib
                    or tuple(signature(e) for e in complement[0][0]) != tuple(signature(e) for e in positive[0])):
                    raise ValueError("Multi-select native complement does not match its full-credit condition.")
            terms: dict[str, bool] = {}
            def conjunction(node: ET.Element, negated: bool = False) -> None:
                if node.tag in {"conditionvar", "and"} and not node.attrib and not negated and len(node):
                    for child in node:
                        conjunction(child)
                elif node.tag == "not" and not node.attrib and not negated and len(node):
                    # D2L also emits multiple negative varequal siblings in one
                    # not block; this is the exporter form already reviewed.
                    if any(c.tag != "varequal" for c in node):
                        raise ValueError("Unsupported multi-select negation.")
                    for child in node:
                        conjunction(child, True)
                elif node.tag == "varequal" and not list(node):
                    oid = node.text or ""
                    if set(node.attrib) != {"respident"} or node.get("respident") != declaration_id or oid not in ids or oid in terms:
                        raise ValueError("Multi-select scoring IDs do not join uniquely to options.")
                    terms[oid] = not negated
                else:
                    raise ValueError("Unsupported multi-select predicate.")
            conjunction(positive[0])
            if set(terms) != set(ids) or not any(terms.values()):
                raise ValueError("Multi-select conjunction must specify every option.")
            correct = {oid for oid, selected in terms.items() if selected}
            result["scoring_mode"] = "all_or_nothing"
        else:
            # D2L Ordering counter form: one positive and one complement for
            # each source option, then the two canonical score assignments.
            # Native D2L exports omit continue on this program (see the
            # ordering_response_group corpus). That is an explicit exporter
            # profile, not a claim that generic QTI default-No evaluates it.
            expected_variables = {"D2L_Correct", "D2L_Incorrect", "que_score"}
            if set(variables) != expected_variables or any(
                variables[name]["defaultval"] != 0
                or not outcome_allows(name, Decimal(0), Decimal(len(ids) if name != "que_score" else 1))
                for name in expected_variables
            ):
                raise ValueError("Ordering requires declared zero-initialized counters and compatible bounds.")
            continuations = [e.get("continue", "").lower() or None for e in conditions]
            if any(value is not None for value in continuations) and (
                any(value != "yes" for value in continuations[:-1])
                or continuations[-1] not in {"yes", "no"}
            ):
                raise ValueError("Ordering continuation is outside the supported native counter profile.")
            negative: dict[str, int] = {}
            terminal: list[tuple[str, str, Decimal]] = []
            for condition in conditions:
                predicate, setter, value = condition_parts(condition)
                if len(predicate) != 1:
                    raise ValueError("Unsupported Ordering predicate.")
                test = predicate[0]
                variable = setter.get("varname")
                if variable == "que_score" and setter.get("action", "Set") == "Set" and test.tag in {"vargte", "varequal"} and test.attrib == {"respident": "D2L_Incorrect"} and test.text == "0" and not list(test):
                    terminal.append((test.tag, test.text, value))
                    continue
                target = positions if variable == "D2L_Correct" else negative if variable == "D2L_Incorrect" else None
                if terminal:
                    raise ValueError("Ordering score assignments must follow every counter condition.")
                if target is None or setter.get("action") != "Add" or value != 1:
                    raise ValueError("Unsupported Ordering score operation.")
                if target is negative:
                    if test.tag != "not" or test.attrib or len(test) != 1:
                        raise ValueError("Ordering incorrect condition is not the exact complement.")
                    test = test[0]
                oid = test.get("respident")
                if test.tag != "varequal" or set(test.attrib) != {"respident"} or list(test) or oid not in ids or oid in target or not re.fullmatch(r"[1-9][0-9]*", test.text or ""):
                    raise ValueError("Ordering source IDs or positions are ambiguous.")
                target[oid] = int(test.text)
            if positions != negative or set(positions) != set(ids) or sorted(positions.values()) != list(range(1, len(ids) + 1)) or terminal != [("vargte", "0", Decimal(0)), ("varequal", "0", Decimal(1))]:
                raise ValueError("Ordering grading is not a complete supported counter program.")
        for option in result["options"]:
            option["correct"] = option["source_ident"] in correct if kind != "ordering" else None
            option["position"] = positions.get(option["source_ident"])
        result["recognized"] = True
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
        result["reason"] = str(exc)
    return result


def _item_metadata_value(
    facts: dict[str, Any],
    field_name: str,
) -> str | None:
    item_metadata = facts.get("item_metadata")
    if not isinstance(item_metadata, dict) or set(item_metadata) != {
        "qmd_questiontype",
        "qmd_computerscored",
        "qmd_weighting",
    }:
        return None
    field = item_metadata.get(field_name)
    if (
        not isinstance(field, dict)
        or set(field) != {"state", "raw_value", "occurrences"}
        or field.get("state") != "present_value"
        or not isinstance(field.get("raw_value"), str)
    ):
        return None
    occurrences = field.get("occurrences")
    if not isinstance(occurrences, list) or len(occurrences) != 1:
        return None
    occurrence = occurrences[0]
    if (
        not isinstance(occurrence, dict)
        or set(occurrence) != {"ordinal", "raw_value", "node"}
        or not _is_exact_ordinal(occurrence.get("ordinal"), 1)
        or occurrence.get("raw_value") != field["raw_value"]
    ):
        return None
    metadata_field = _fact_node(occurrence.get("node"), "qti_metadatafield")
    if (
        metadata_field is None
        or _fact_attributes(metadata_field) != {}
        or not _fact_structural_text_is_empty(metadata_field)
    ):
        return None
    children = _fact_children(
        metadata_field,
        (("fieldlabel", None), ("fieldentry", None)),
    )
    if children is None:
        return None
    label, entry = children
    if (
        _fact_attributes(label) != {}
        or _fact_attributes(entry) != {}
        or label["children"]
        or entry["children"]
        or label.get("raw_text") != field_name
        or entry.get("raw_text") != field["raw_value"]
        or str(label.get("raw_tail") or "").strip()
        or str(entry.get("raw_tail") or "").strip()
    ):
        return None
    return field["raw_value"]


def _recognize_short_answer_occurrence(
    facts: object,
) -> ShortAnswerOccurrence | None:
    if not isinstance(facts, dict):
        return None
    if (
        facts.get("schema") != "coursecraft.quiz_source_response_facts/0"
        or facts.get("source_question_type") != "Short Answer"
        or facts.get("diagnostics") != []
        or facts.get("feedback_slots") != []
    ):
        return None

    question_type = _item_metadata_value(
        facts,
        "qmd_questiontype",
    )
    computer_scored = _item_metadata_value(
        facts,
        "qmd_computerscored",
    )
    raw_weight = _item_metadata_value(
        facts,
        "qmd_weighting",
    )
    if (
        question_type != "Short Answer"
        or computer_scored != "yes"
        or raw_weight is None
        or raw_weight != raw_weight.strip()
    ):
        return None
    try:
        parsed_weight = Decimal(raw_weight)
    except InvalidOperation:
        return None
    if not parsed_weight.is_finite() or parsed_weight <= 0:
        return None

    presentation = _single_fact_tree(facts, "presentation", "presentation")
    if (
        presentation is None
        or _fact_attributes(presentation) != {}
        or not _fact_structural_text_is_empty(presentation)
    ):
        return None
    presentation_children = _fact_children(
        presentation,
        (("flow", None),),
    )
    if presentation_children is None:
        return None
    flow = presentation_children[0]
    if (
        _fact_attributes(flow) != {}
        or not _fact_structural_text_is_empty(flow)
    ):
        return None
    flow_children = _fact_children(
        flow,
        (
            ("material", None),
            ("response_extension", None),
            ("response_str", None),
        ),
    )
    if flow_children is None:
        return None
    material, response_extension, response_str = flow_children

    if (
        _fact_attributes(material) != {}
        or not _fact_structural_text_is_empty(material)
    ):
        return None
    material_children = _fact_children(material, (("mattext", None),))
    if material_children is None:
        return None
    mattext = material_children[0]
    if (
        _fact_attributes(mattext) != {(None, "texttype"): "text/html"}
        or mattext["children"]
        or not isinstance(mattext.get("raw_text"), str)
        or str(mattext.get("raw_tail") or "").strip()
    ):
        return None
    prompt_html = mattext["raw_text"]

    if (
        _fact_attributes(response_extension) != {}
        or not _fact_structural_text_is_empty(response_extension)
    ):
        return None
    extension_children = _fact_children(
        response_extension,
        (("grading_type", D2L_QTI_NAMESPACE),),
    )
    if extension_children is None:
        return None
    grading_type = extension_children[0]
    if (
        _fact_attributes(grading_type) != {}
        or grading_type["children"]
        or grading_type.get("raw_text") != "0"
        or str(grading_type.get("raw_tail") or "").strip()
    ):
        return None

    response_attributes = _fact_attributes(response_str)
    if response_attributes is None or set(response_attributes) != {
        (None, "ident"),
        (None, "rcardinality"),
    }:
        return None
    response_ident = response_attributes[(None, "ident")]
    if (
        not response_ident
        or response_attributes[(None, "rcardinality")] != "Single"
        or not _fact_structural_text_is_empty(response_str)
    ):
        return None
    response_children = _fact_children(
        response_str,
        (("render_fib", None),),
    )
    if response_children is None:
        return None
    renderer = response_children[0]
    renderer_attributes = _fact_attributes(renderer)
    if renderer_attributes not in (
        {
            (None, "rows"): "0",
            (None, "columns"): "20",
            (None, "prompt"): "Box",
            (None, "fibtype"): "String",
        },
        {
            (None, "rows"): "1",
            (None, "columns"): "60",
            (None, "prompt"): "Box",
            (None, "fibtype"): "String",
        },
    ) or not _fact_structural_text_is_empty(renderer):
        return None
    renderer_children = _fact_children(
        renderer,
        (("response_label", None),),
    )
    if renderer_children is None:
        return None
    response_label = renderer_children[0]
    label_attributes = _fact_attributes(response_label)
    if (
        label_attributes is None
        or set(label_attributes) != {(None, "ident")}
        or not label_attributes[(None, "ident")]
        or response_label["children"]
        or not _fact_structural_text_is_empty(response_label)
    ):
        return None
    label_ident = label_attributes[(None, "ident")]

    sequence = facts.get("presentation_sequence")
    declarations = facts.get("response_declarations")
    grading_fact = facts.get("grading_type")
    response_labels = (
        declarations[0].get("response_labels")
        if isinstance(declarations, list)
        and len(declarations) == 1
        and isinstance(declarations[0], dict)
        else None
    )
    if (
        not isinstance(sequence, list)
        or [entry.get("kind") for entry in sequence if isinstance(entry, dict)]
        != ["material", "response_extension", "response"]
        or len(sequence) != 3
        or not isinstance(declarations, list)
        or len(declarations) != 1
        or not isinstance(declarations[0], dict)
        or declarations[0].get("tree") != response_str
        or declarations[0].get("source_ident") != response_ident
        or declarations[0].get("source_respident") is not None
        or declarations[0].get("rcardinality") != "Single"
        or declarations[0].get("element_kind") != "response_str"
        or not isinstance(declarations[0].get("renderer"), dict)
        or declarations[0]["renderer"].get("tree") != renderer
        or not isinstance(response_labels, list)
        or len(response_labels) != 1
        or not isinstance(response_labels[0], dict)
        or set(response_labels[0]) != {
            "ordinal",
            "source_ident",
            "attributes",
        }
        or not _is_exact_ordinal(response_labels[0].get("ordinal"), 1)
        or response_labels[0].get("source_ident") != label_ident
        or response_labels[0].get("attributes")
        != response_label["attributes"]
        or not isinstance(grading_fact, dict)
        or grading_fact.get("state") != "present_value"
        or grading_fact.get("raw_value") != "0"
        or not isinstance(grading_fact.get("occurrences"), list)
        or len(grading_fact["occurrences"]) != 1
        or not _is_exact_ordinal(
            grading_fact["occurrences"][0].get("ordinal"),
            1,
        )
        or grading_fact["occurrences"][0].get("raw_value") != "0"
        or grading_fact["occurrences"][0].get("node") != grading_type
    ):
        return None

    response_processing = _single_fact_tree(
        facts,
        "response_processing",
        "resprocessing",
    )
    if (
        response_processing is None
        or _fact_attributes(response_processing) != {}
        or not _fact_structural_text_is_empty(response_processing)
    ):
        return None
    processing_children = response_processing["children"]
    if (
        not isinstance(processing_children, list)
        or not 3 <= len(processing_children) <= 7
    ):
        return None
    outcomes = _fact_node(processing_children[0], "outcomes")
    if (
        outcomes is None
        or _fact_attributes(outcomes) != {}
        or not _fact_structural_text_is_empty(outcomes)
    ):
        return None
    outcome_children = _fact_children(outcomes, (("decvar", None),))
    if outcome_children is None:
        return None
    decvar = outcome_children[0]
    if (
        _fact_attributes(decvar)
        != {
            (None, "vartype"): "Integer",
            (None, "minvalue"): "0",
            (None, "maxvalue"): "100",
            (None, "varname"): "Blank_1",
        }
        or decvar["children"]
        or not _fact_structural_text_is_empty(decvar)
    ):
        return None

    condition_nodes: list[dict[str, Any]] = []
    for raw_condition in processing_children[1:]:
        condition = _fact_node(raw_condition, "respcondition")
        if (
            condition is None
            or _fact_attributes(condition) != {}
            or not _fact_structural_text_is_empty(condition)
        ):
            return None
        condition_nodes.append(condition)
    if not 2 <= len(condition_nodes) <= 6:
        return None

    values: list[str] = []
    for condition in condition_nodes[:-1]:
        condition_children = _fact_children(
            condition,
            (("conditionvar", None), ("setvar", None)),
        )
        if condition_children is None:
            return None
        conditionvar, setvar = condition_children
        if (
            _fact_attributes(conditionvar) != {}
            or not _fact_structural_text_is_empty(conditionvar)
        ):
            return None
        predicate_children = _fact_children(
            conditionvar,
            (("varequal", None),),
        )
        if predicate_children is None:
            return None
        varequal = predicate_children[0]
        if (
            _fact_attributes(varequal)
            != {
                (None, "respident"): label_ident,
                (None, "case"): "no",
            }
            or varequal["children"]
            or not isinstance(varequal.get("raw_text"), str)
            or str(varequal.get("raw_tail") or "").strip()
        ):
            return None
        value = varequal["raw_text"]
        if (
            not value
            or value != value.strip()
            or " || " in value
        ):
            return None
        if (
            _fact_attributes(setvar) != {(None, "action"): "Set"}
            or setvar["children"]
            or setvar.get("raw_text") != SHORT_ANSWER_AWARD
            or str(setvar.get("raw_tail") or "").strip()
        ):
            return None
        values.append(value)
    if not 1 <= len(values) <= 5 or len(set(values)) != len(values):
        return None

    aggregator = condition_nodes[-1]
    aggregator_children = _fact_children(
        aggregator,
        (("conditionvar", None), ("setvar", None)),
    )
    if aggregator_children is None:
        return None
    aggregator_conditionvar, aggregator_setvar = aggregator_children
    if (
        _fact_attributes(aggregator_conditionvar) != {}
        or not _fact_structural_text_is_empty(aggregator_conditionvar)
    ):
        return None
    other_children = _fact_children(
        aggregator_conditionvar,
        (("other", None),),
    )
    if other_children is None:
        return None
    other = other_children[0]
    if (
        _fact_attributes(other) != {}
        or other["children"]
        or not _fact_structural_text_is_empty(other)
        or _fact_attributes(aggregator_setvar)
        != {
            (None, "varname"): "que_score",
            (None, "action"): "Set",
        }
        or aggregator_setvar["children"]
        or aggregator_setvar.get("raw_text") != "D2L_Correct"
        or str(aggregator_setvar.get("raw_tail") or "").strip()
    ):
        return None

    outcome_facts = facts.get("outcomes")
    condition_facts = facts.get("response_conditions")
    if (
        not isinstance(outcome_facts, list)
        or len(outcome_facts) != 1
        or not isinstance(outcome_facts[0], dict)
        or not _is_exact_ordinal(outcome_facts[0].get("ordinal"), 1)
        or outcome_facts[0].get("node") != decvar
        or not isinstance(condition_facts, list)
        or len(condition_facts) != len(condition_nodes)
        or any(
            not isinstance(condition_fact, dict)
            or not _is_exact_ordinal(
                condition_fact.get("ordinal"),
                ordinal,
            )
            or condition_fact.get("tree") != condition_node
            for ordinal, (condition_fact, condition_node) in enumerate(
                zip(condition_facts, condition_nodes),
                start=1,
            )
        )
    ):
        return None

    renderer_signature = tuple(
        renderer_attributes[(None, name)]
        for name in ("rows", "columns", "prompt", "fibtype")
    )
    return ShortAnswerOccurrence(
        values=tuple(values),
        signature=(
            prompt_html,
            tuple(values),
            tuple("no" for _value in values),
            tuple(SHORT_ANSWER_AWARD for _value in values),
            renderer_signature,
            "0",
            ("Blank_1", "Integer", "0", "100"),
            ("que_score", "Set", "D2L_Correct"),
            raw_weight,
            computer_scored,
            (),
        ),
    )


def _legacy_short_answer_row_matches(
    row: object,
    occurrence: ShortAnswerOccurrence,
) -> bool:
    if not isinstance(row, dict) or row.get("question_type") != "Short Answer":
        return False
    values = list(occurrence.values)
    correct_answer = " || ".join(values)
    blank_answers = "Blank 1: " + " / ".join(values)
    return (
        row.get("correct_answer") == correct_answer
        and _split(row.get("correct_answer")) == values
        and row.get("correct_answer_basis") == "text_entry"
        and not row.get("correct_response_ids")
        and row.get("fill_in_blank_count") == 1
        and row.get("fill_in_blank_answers_text") == blank_answers
        and not row.get("choices_text")
    )


def _short_answer_record_pairs(
    question: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> list[
    tuple[dict[str, Any], dict[str, Any], ShortAnswerOccurrence]
] | None:
    type_payload = question.get("type_payload")
    if not isinstance(type_payload, dict):
        return None
    raw_models = type_payload.get("raw_response_models")
    if (
        not isinstance(raw_models, list)
        or any(
            not isinstance(record, dict)
            or not isinstance(record.get("source_kind"), str)
            or not isinstance(record.get("payload"), dict)
            or not isinstance(record.get("source_evidence_keys"), list)
            or any(
                not isinstance(key, str)
                for key in record.get("source_evidence_keys", [])
            )
            or not isinstance(record.get("extensions"), dict)
            for record in raw_models
        )
    ):
        return None
    legacy_records = [
        record
        for record in raw_models
        if record.get("source_kind")
        == "canonical-extractor-row:Short Answer"
    ]
    fact_records = [
        record
        for record in raw_models
        if record.get("source_kind") == "d2l_qti_response_facts/0"
    ]
    if not legacy_records or len(legacy_records) != len(fact_records):
        return None

    occurrence_evidence: dict[tuple[str, str], list[str]] = {}
    occurrence_keys: set[str] = set()
    for evidence in evidence_rows:
        if (
            not isinstance(evidence, dict)
            or evidence.get("kind") != "xml_element"
            or not str(evidence.get("evidence_key", "")).startswith(
                "ev.occurrence."
            )
        ):
            continue
        evidence_key = str(evidence["evidence_key"])
        occurrence_keys.add(evidence_key)
        occurrence_evidence.setdefault(
            (str(evidence.get("source_ref", "")), str(evidence.get("locator", ""))),
            [],
        ).append(evidence_key)

    legacy_by_occurrence: dict[str, dict[str, Any]] = {}
    for legacy_record in legacy_records:
        legacy_payload = legacy_record.get("payload")
        legacy_evidence = legacy_record.get("source_evidence_keys")
        if (
            not isinstance(legacy_payload, dict)
            or not isinstance(legacy_evidence, list)
            or any(not isinstance(key, str) for key in legacy_evidence)
        ):
            return None
        locator = (
            str(legacy_payload.get("quiz_file", "")),
            f"quiz item {legacy_payload.get('quiz_item_number')}",
        )
        candidates = occurrence_evidence.get(locator, [])
        if (
            len(candidates) != 1
            or candidates[0] not in legacy_evidence
            or candidates[0] in legacy_by_occurrence
        ):
            return None
        legacy_by_occurrence[candidates[0]] = legacy_record

    fact_by_occurrence: dict[str, dict[str, Any]] = {}
    recognized_by_occurrence: dict[str, ShortAnswerOccurrence] = {}
    for fact_record in fact_records:
        fact_evidence = fact_record.get("source_evidence_keys")
        if (
            not isinstance(fact_evidence, list)
            or any(not isinstance(key, str) for key in fact_evidence)
            or any(
                key.startswith("ev.occurrence.")
                and key not in occurrence_keys
                for key in fact_evidence
            )
        ):
            return None
        candidates = [
            key
            for key in fact_evidence
            if key in occurrence_keys
        ]
        if (
            len(candidates) != 1
            or candidates[0] in fact_by_occurrence
        ):
            return None
        occurrence = _recognize_short_answer_occurrence(fact_record.get("payload"))
        if occurrence is None:
            return None
        fact_by_occurrence[candidates[0]] = fact_record
        recognized_by_occurrence[candidates[0]] = occurrence

    if set(legacy_by_occurrence) != set(fact_by_occurrence):
        return None
    pairs = [
        (
            legacy_by_occurrence[key],
            fact_by_occurrence[key],
            recognized_by_occurrence[key],
        )
        for key in legacy_by_occurrence
    ]
    if any(
        not _legacy_short_answer_row_matches(
            legacy_record.get("payload"),
            occurrence,
        )
        for legacy_record, _fact_record, occurrence in pairs
    ):
        return None
    return pairs


def _enrich_short_answer_question(
    question: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> bool:
    if (
        question.get("kind") != "short_answer"
        or question.get("source_kind") != "Short Answer"
        or question.get("feedback") != []
    ):
        return False
    pairs = _short_answer_record_pairs(question, evidence_rows)
    if not pairs:
        return False
    occurrences = [occurrence for _legacy, _fact, occurrence in pairs]
    if any(
        occurrence.signature != occurrences[0].signature
        for occurrence in occurrences[1:]
    ):
        return False
    values = list(occurrences[0].values)

    type_payload = question.get("type_payload")
    scoring = question.get("scoring")
    if (
        not isinstance(type_payload, dict)
        or not isinstance(scoring, dict)
        or set(type_payload) != {
            "options",
            "accepted_responses",
            "blanks",
            "match_pairs",
            "correct_order",
            "raw_response_models",
            "extensions",
        }
        or type_payload.get("options") != []
        or type_payload.get("match_pairs") != []
        or type_payload.get("correct_order") != []
        or not isinstance(type_payload.get("raw_response_models"), list)
        or not isinstance(type_payload.get("extensions"), dict)
        or set(scoring) != {
            "state",
            "mode",
            "maximum_points",
            "rules",
            "extensions",
        }
        or scoring.get("state") != "known"
        or scoring.get("maximum_points") is None
        or isinstance(scoring.get("maximum_points"), bool)
        or not isinstance(scoring.get("maximum_points"), (str, int, float))
        or scoring.get("maximum_points") == ""
        or scoring.get("rules") != []
        or not isinstance(scoring.get("extensions"), dict)
    ):
        return False
    accepted = type_payload.get("accepted_responses")
    if not isinstance(accepted, list) or len(accepted) != len(values):
        return False
    for response, value in zip(accepted, values):
        if (
            not isinstance(response, dict)
            or set(response) != {
                "value",
                "case_sensitive",
                "weight",
                "extensions",
            }
            or response.get("value") != value
            or (
                response.get("case_sensitive") is not None
                and response.get("case_sensitive") is not False
            )
            or (
                response.get("weight") is not None
                and (
                    not isinstance(response.get("weight"), str)
                    or response.get("weight") != SHORT_ANSWER_AWARD
                )
            )
            or not isinstance(response.get("extensions"), dict)
        ):
            return False
    if (
        not isinstance(scoring.get("mode"), str)
        or (
            scoring.get("mode") != "unknown"
            and scoring.get("mode") != "exact"
        )
    ):
        return False

    blanks = type_payload.get("blanks")
    first_legacy_record = pairs[0][0]
    if not isinstance(blanks, list) or len(blanks) != 1:
        return False
    blank = blanks[0]
    if (
        not isinstance(blank, dict)
        or set(blank) != {
            "blank_key",
            "accepted_responses",
            "source_evidence_keys",
            "extensions",
        }
        or blank.get("blank_key") != "blank-1"
        or blank.get("extensions") != {}
        or blank.get("source_evidence_keys")
        != first_legacy_record.get("source_evidence_keys")
    ):
        return False
    blank_responses = blank.get("accepted_responses")
    if not isinstance(blank_responses, list) or len(blank_responses) != 1:
        return False
    blank_response = blank_responses[0]
    if (
        not isinstance(blank_response, dict)
        or set(blank_response) != {
            "value",
            "case_sensitive",
            "weight",
            "extensions",
        }
        or blank_response.get("value") != " / ".join(values)
        or blank_response.get("case_sensitive") is not None
        or blank_response.get("weight") is not None
        or blank_response.get("extensions") != {}
    ):
        return False

    for response in accepted:
        response["case_sensitive"] = False
        response["weight"] = SHORT_ANSWER_AWARD
    scoring["mode"] = "exact"
    type_payload["blanks"] = []
    return True


def _match_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        value = json.loads(str(row.get("match_candidates_json", "[]") or "[]"))
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _question_alias(row: dict[str, Any]) -> tuple[str, str]:
    """Name a question by the source item's own durable identity.

    A matched question-library record is evidence *about* an item, not the
    item's name.  Preferring the library identity merged materially different
    quiz items -- different keyed answers, different images -- into one entity
    and kept only the first, so the library identifiers are deliberately absent
    from this priority list.  They are retained as aliases and, from the library
    tranche onward, as an explicit relationship.
    """
    priorities = [
        ("d2l.qmd_globalid", row.get("qmd_globalid")),
        ("d2l.qmd_displayid", row.get("qmd_displayid")),
        ("d2l.quiz_item.label", row.get("quiz_item_label")),
        ("d2l.quiz_item.ident", row.get("quiz_item_ident")),
    ]
    for namespace, value in priorities:
        if value:
            return namespace, str(value)
    return (
        "coursecraft.quiz_occurrence",
        f"{row.get('quiz_file')}:{row.get('quiz_item_number')}",
    )


def _all_question_aliases(row: dict[str, Any]) -> list[tuple[str, str]]:
    """Retain every observed D2L identifier, own and library alike.

    ``QUIZ_CONTRACT_FAMILY.md`` requires that all observed D2L identifiers
    remain in ``source_aliases``.  Collapsing the item's own identifier into the
    matched library one dropped the majority of them in four of five corpora, so
    the two families now carry distinct namespaces and both survive.
    """
    values = [
        ("d2l.qmd_globalid", row.get("qmd_globalid")),
        ("d2l.qmd_displayid", row.get("qmd_displayid")),
        ("d2l.quiz_item.label", row.get("quiz_item_label")),
        ("d2l.quiz_item.ident", row.get("quiz_item_ident")),
        ("d2l.library.qmd_globalid", row.get("pool_question_globalid")),
        ("d2l.library.qmd_displayid", row.get("pool_question_displayid")),
        ("d2l.library.question.label", row.get("pool_question_label")),
        ("d2l.library.question.ident", row.get("pool_question_ident")),
    ]
    result: list[tuple[str, str]] = []
    for namespace, value in values:
        pair = (namespace, str(value)) if value else None
        if pair and pair not in result:
            result.append(pair)
    return result


AUTHORED_VARIANT_BASIS = "authored variant"
AUTHORED_VARIANT_EQUIVALENCE = "authored_variant_equivalence"
QUESTION_LIBRARY_MATCH = "question_library_match"
LIBRARY_ROW_SOURCE = "question-library-row"
LIBRARY_VARIANT_EQUIVALENCE = "library_variant_equivalence"


def _library_question_alias(row: dict[str, Any]) -> tuple[str, str] | None:
    for namespace, field in (
        ("d2l.qmd_globalid", "qmd_globalid"),
        ("d2l.qmd_displayid", "qmd_displayid"),
        ("d2l.question.label", "question_label"),
        ("d2l.question.ident", "question_ident"),
    ):
        value = row.get(field)
        if value:
            return namespace, str(value)
    return None


def _library_match_alias(row: dict[str, Any]) -> tuple[str, str] | None:
    """Name the library record a quiz row was matched to, if any."""
    for namespace, field in (
        ("d2l.qmd_globalid", "pool_question_globalid"),
        ("d2l.qmd_displayid", "pool_question_displayid"),
        ("d2l.question.label", "pool_question_label"),
        ("d2l.question.ident", "pool_question_ident"),
    ):
        value = row.get(field)
        if value:
            return namespace, str(value)
    return None


def _variant_text(value: Any) -> str:
    """Whitespace- and entity-normalize authored text without casefolding.

    Casefolding under-splits: corpus checks found prompts that differ only in
    capitalization and are genuinely distinct authored variants.  Splitting is
    visible and recoverable; merging is neither.
    """
    return " ".join(html.unescape("" if value is None else str(value)).split())


def _variant_content(value: Any) -> list[str]:
    row = value if isinstance(value, dict) else {}
    return [str(row.get("format") or ""), _variant_text(row.get("content"))]


def _authored_variant_basis(
    question: dict[str, Any], asset_digests: list[str]
) -> list[Any]:
    """Describe what makes one authored question materially itself.

    Scoring is absent on purpose.  In Brightspace the weight belongs to the quiz
    item, and every reuse group in the ANAT control varies in points, so
    admitting it would split all real reuse.  Ordered asset content digests are
    present on purpose: when the image is the question, dropping it merges
    questions that share a prompt but ask about different pictures.
    """
    payload = question.get("type_payload") or {}
    return [
        str(question.get("kind") or ""),
        _variant_content(question.get("prompt")),
        [
            [
                _variant_content(option.get("content")),
                option.get("correct"),
            ]
            for option in payload.get("options") or []
            if isinstance(option, dict)
        ],
        [
            [
                _variant_text(answer.get("value")),
                answer.get("case_sensitive"),
                str(answer.get("weight") if answer.get("weight") is not None else ""),
            ]
            for answer in payload.get("accepted_responses") or []
            if isinstance(answer, dict)
        ],
        [
            [
                str(blank.get("blank_key") or ""),
                [
                    [
                        _variant_text(answer.get("value")),
                        answer.get("case_sensitive"),
                    ]
                    for answer in blank.get("accepted_responses") or []
                    if isinstance(answer, dict)
                ],
            ]
            for blank in payload.get("blanks") or []
            if isinstance(blank, dict)
        ],
        [
            [
                str(pair.get("left_key") or ""),
                str(pair.get("right_key") or ""),
            ]
            for pair in payload.get("match_pairs") or []
            if isinstance(pair, dict)
        ],
        [
            [str(entry.get("option_key") or ""), entry.get("position")]
            for entry in payload.get("correct_order") or []
            if isinstance(entry, dict)
        ],
        _variant_content(payload.get("manual_answer_key")),
        list(asset_digests),
    ]


def _question_asset_digests(model: dict[str, Any]) -> dict[str, list[str]]:
    """Map each question to its ordered asset content digests.

    Assets are bound by content digest rather than by path so the same export
    fingerprints identically in copy and reference asset modes.
    """
    digest_by_asset: dict[str, str] = {}
    for asset in model.get("assets", []):
        if not isinstance(asset, dict) or not asset.get("entity_key"):
            continue
        fingerprint = asset.get("fingerprint") or {}
        digest_by_asset[str(asset["entity_key"])] = str(
            fingerprint.get("digest") or asset.get("entity_key")
        )
    bindings: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        if relationship.get("kind") != "uses_asset":
            continue
        source = str(relationship.get("from_entity_key") or "")
        target = str(relationship.get("to_entity_key") or "")
        if not source or target not in digest_by_asset:
            continue
        ordinal = relationship.get("ordinal")
        bindings[source].append(
            (
                int(ordinal) if isinstance(ordinal, int) else 0,
                str(relationship.get("relationship_key") or ""),
                digest_by_asset[target],
            )
        )
    return {
        key: [digest for _ordinal, _relationship, digest in sorted(rows)]
        for key, rows in bindings.items()
    }


def _normalize_asset_ref(value: Any) -> str:
    """Compare asset references by what they point at, not how they are spelled.

    Brightspace writes the same reference more than one way in a single export:
    College Algebra carries ``csfiles\\home_dir\\Math Images\\x.png`` beside
    ``csfiles/home_dir/Math%20Images/x.png`` for the same five images on two
    placements of one question.  Treating the spelling as authored content
    reported those as different questions.
    """
    text = _variant_text(value).replace("\\", "/")
    return " ".join(unquote(text).split())


def authored_variant_row_digest(payload: dict[str, Any]) -> str:
    """Digest the authored semantics carried by one canonical extractor row.

    This is the row-level counterpart of the entity fingerprint.  It exists so
    a guard can ask the one question the collapse defect made unanswerable: do
    all the rows folded into this entity actually describe the same authored
    question?  Placement-scoped fields are excluded for the same reason they
    are excluded from the entity fingerprint.
    """
    images: list[str] = []
    for field in ("question_image_paths", "question_image_refs"):
        raw = payload.get(field)
        if isinstance(raw, str) and raw.strip():
            images = [
                _normalize_asset_ref(part)
                for part in re.split(r"\s*\|\|\s*|\s*;\s*|\n", raw)
                if part.strip()
            ]
            break
        if isinstance(raw, list) and raw:
            images = [_normalize_asset_ref(part) for part in raw]
            break
    else:
        primary = payload.get("question_primary_image_path")
        images = [_normalize_asset_ref(primary)] if primary else []
    basis = [
        _variant_text(payload.get("question_type")),
        _variant_text(payload.get("question_text")),
        [
            _variant_text(choice)
            for choice in re.split(
                r"\s*\|\|\s*", str(payload.get("choices_text") or "")
            )
            if choice.strip()
        ],
        _variant_text(payload.get("correct_answer")),
        _variant_text(payload.get("fill_in_blank_answers_text")),
        _variant_text(payload.get("matching_prompts_text")),
        _variant_text(payload.get("matching_options_text")),
        images,
    ]
    choice_display = payload.get("source_choice_display")
    if isinstance(choice_display, dict):
        # Duplicate visible labels can conceal different keyed native IDs.
        # Compare position/key semantics while excluding export-local ID text.
        basis.append([
            choice_display.get("recognized"),
            [[option.get("text"), option.get("correct"), option.get("position")]
             for option in choice_display.get("options", [])],
        ])
    return make_content_fingerprint(basis, basis=AUTHORED_VARIANT_BASIS)["digest"]


def variant_collision_report(model: dict[str, Any]) -> dict[str, Any]:
    """Report entities whose folded rows disagree on authored semantics.

    A run can be perfectly tamper-evident and still semantically unsafe.  This
    is the semantic half, kept separate from hash verification and reported in
    its own right so a collapsed answer key can never again pass unremarked.
    """
    collided: list[dict[str, Any]] = []
    occurrence_total = 0
    for question in model.get("questions", []):
        if not isinstance(question, dict):
            continue
        rows = [
            record.get("payload")
            for record in (question.get("type_payload") or {}).get(
                "raw_response_models"
            )
            or []
            if str(record.get("source_kind", "")).startswith("canonical-extractor-row:")
            and isinstance(record.get("payload"), dict)
        ]
        digests = {authored_variant_row_digest(row) for row in rows}
        if len(digests) > 1:
            occurrence_total += len(rows)
            collided.append(
                {
                    "entity_key": str(question.get("entity_key")),
                    "authored_variant_count": len(digests),
                    "occurrence_count": len(rows),
                }
            )
    collided.sort(key=lambda row: row["entity_key"])
    return {
        "variant_collision_count": len(collided),
        "collided_occurrence_count": occurrence_total,
        "state": "clean" if not collided else "collapsed_question_variants",
        "approval_safe": not collided,
        "entities": collided,
    }


def build_library_question_entities(
    model: dict[str, Any],
    payload: dict[str, Any],
    lineage: str,
    questiondb_evidence: str,
) -> None:
    """Give question-library records their own entities and match relationships.

    Without them the library is modelled only at pool granularity, so a match
    has nothing to point at, ``library_only_question_entity_count`` is
    structurally always zero, and a course's library questions are invisible
    even when the envelope reports library coverage.  A library record is now an
    entity with no placements, and a matched quiz item points at it with an
    explicit relationship rather than borrowing its name.
    """
    library_rows = [
        row for row in payload.get("library_question_rows", []) if isinstance(row, dict)
    ]
    if not library_rows:
        return

    existing = {
        str(question.get("entity_key"))
        for question in model["questions"]
        if isinstance(question, dict)
    }
    key_by_alias: dict[tuple[str, str], str] = {}
    for row in library_rows:
        alias = _library_question_alias(row)
        if alias is None:
            continue
        namespace, value = alias
        library_key = _entity_key("question", lineage, namespace, value)
        for candidate in (
            ("d2l.qmd_globalid", row.get("qmd_globalid")),
            ("d2l.qmd_displayid", row.get("qmd_displayid")),
            ("d2l.question.label", row.get("question_label")),
            ("d2l.question.ident", row.get("question_ident")),
        ):
            if candidate[1]:
                key_by_alias.setdefault(
                    (candidate[0], str(candidate[1])), library_key
                )
        if library_key in existing:
            continue
        existing.add(library_key)
        evidence_keys = [questiondb_evidence]
        question = {
            "entity_key": library_key,
            "identity": _identity(
                namespace,
                value,
                evidence_keys,
                {
                    "prompt": row.get("plain_text"),
                    "choices": row.get("choices_text"),
                    "correct_answer": None,
                    "question_type": row.get("question_type"),
                },
            ),
            "kind": QUESTION_KIND_MAP.get(str(row.get("question_type", "")), "unknown"),
            "source_kind": "questiondb.objectbank.item",
            "title": str(row.get("question_title") or "") or None,
            "prompt": _source_prompt_content(row, "plain_text"),
            # Built through the same typed-payload path as a placed question, so
            # options, accepted responses, blanks, match pairs, and ordering are
            # read by one parser rather than two.
            "type_payload": _type_payload(
                row, evidence_keys, row_source=LIBRARY_ROW_SOURCE
            ),
            "scoring": {
                "state": "known" if row.get("question_weight") else "unknown",
                "mode": "unknown",
                "maximum_points": row.get("question_weight") or None,
                "rules": [],
                "extensions": {"scope": "library_declared"},
            },
            "feedback": _feedback_rows(row, evidence_keys),
            "build_support": {
                "level": "extraction_only",
                "receipt_refs": [],
                "notes": [
                    "Question-library record; extracted for coverage, curation, "
                    "and matching. It is not placed in any quiz."
                ],
                "extensions": {},
            },
            "source_evidence_keys": evidence_keys,
            "diagnostic_ids": [],
            "extensions": {
                "storage": "questiondb",
                "library_only": True,
                # Stated rather than assumed: a reader must be able to tell a
                # record whose answer payload was parsed from one that carries
                # only a prompt, so a partial library can never look complete.
                "payload_state": str(row.get("payload_state") or "prompt_only"),
                "library_pool_ident": row.get("pool_ident"),
                "library_pool_path": row.get("pool_path"),
            },
        }
        question["identity"]["source_aliases"] = [
            _source_alias(alias_namespace, alias_value, evidence_keys)
            for alias_namespace, alias_value in (
                ("d2l.qmd_globalid", row.get("qmd_globalid")),
                ("d2l.qmd_displayid", row.get("qmd_displayid")),
                ("d2l.question.label", row.get("question_label")),
                ("d2l.question.ident", row.get("question_ident")),
            )
            if alias_value
        ]
        model["questions"].append(question)

    seen_matches: set[tuple[str, str]] = set()
    for row in payload.get("quiz_question_rows", []):
        if not isinstance(row, dict):
            continue
        match_alias = _library_match_alias(row)
        if match_alias is None:
            continue
        library_key = key_by_alias.get(match_alias)
        if not library_key:
            continue
        namespace, value = _question_alias(row)
        question_key = _entity_key("question", lineage, namespace, value)
        if question_key == library_key or (question_key, library_key) in seen_matches:
            continue
        seen_matches.add((question_key, library_key))
        model["relationships"].append(
            _relationship(
                "same_as",
                QUESTION_LIBRARY_MATCH,
                question_key,
                library_key,
                (
                    "proposed"
                    if row.get("evidence_level") == "inferred_similarity"
                    else "resolved"
                ),
                None,
                [f"ev.occurrence.{_token(str(row.get('quiz_file')), row.get('quiz_item_number'))}"],
                attributes={
                    "basis": str(row.get("match_basis") or "unknown"),
                    "evidence_level": str(row.get("evidence_level") or "unknown"),
                    "match_score": row.get("match_score"),
                    "candidates": _match_candidates(row),
                    "library_alias_namespace": match_alias[0],
                },
            )
        )


def apply_authored_variant_identity(model: dict[str, Any]) -> None:
    """Fingerprint authored variants and record equivalence deterministically.

    Identity stays anchored on the source item's own alias, so it survives a
    refreshed export.  Sameness of authored content is a separate, inspectable
    claim: each variant class names a stable representative and every other
    member points at it with one ``same_as`` relationship, so a class of N
    entities costs N-1 records rather than an all-pairs graph.

    Placed questions and question-library records are grouped in two separate
    passes under two different ``source_kind`` values.  Content sameness and
    "which record is the page a reviewer is sent to" are different decisions: a
    dormant library record must never become the canonical face of a quiz-review
    class, but duplication inside a library is exactly what a curator needs to
    see.  Keeping the channels apart serves both without letting either leak.
    """
    _apply_variant_equivalence(
        model,
        library_only=False,
        source_kind=AUTHORED_VARIANT_EQUIVALENCE,
    )
    _apply_variant_equivalence(
        model,
        library_only=True,
        source_kind=LIBRARY_VARIANT_EQUIVALENCE,
    )


def _apply_variant_equivalence(
    model: dict[str, Any], *, library_only: bool, source_kind: str
) -> None:
    """Fingerprint one population and link its exact duplicates."""
    asset_digests = _question_asset_digests(model)
    members_by_digest: dict[str, list[str]] = defaultdict(list)
    for question in model.get("questions", []):
        if not isinstance(question, dict) or not question.get("entity_key"):
            continue
        if bool((question.get("extensions") or {}).get("library_only")) != library_only:
            continue
        if question.get("type_payload", {}).get("extensions", {}).get("coursecraft.source_choice_projection", {}).get("state") == "unresolved":
            # Unknown keys must not make two authored questions look equivalent
            # merely because both projections have null correctness flags.
            continue
        key = str(question["entity_key"])
        fingerprint = make_content_fingerprint(
            _authored_variant_basis(question, asset_digests.get(key, [])),
            basis=AUTHORED_VARIANT_BASIS,
        )
        prints = question.setdefault("identity", {}).setdefault(
            "content_fingerprints", []
        )
        if not any(row.get("basis") == AUTHORED_VARIANT_BASIS for row in prints):
            prints.append(fingerprint)
        members_by_digest[fingerprint["digest"]].append(key)

    for digest, members in sorted(members_by_digest.items()):
        if len(members) < 2:
            continue
        ordered = sorted(members)
        representative = ordered[0]
        for member in ordered[1:]:
            model["relationships"].append(
                _relationship(
                    "same_as",
                    source_kind,
                    member,
                    representative,
                    "resolved",
                    None,
                    [],
                    attributes={
                        "basis": AUTHORED_VARIANT_BASIS,
                        "authored_variant_digest": digest,
                        "representative_entity_key": representative,
                        "class_size": len(ordered),
                    },
                )
            )


def _record_placement_scoring(question: dict[str, Any], row: dict[str, Any]) -> None:
    """Track every point value observed for a reused question.

    In Brightspace the weight belongs to the quiz item, not to the authored
    question, so one question can legitimately be worth different points in
    different quizzes.  When that happens the question-level value stops being
    authoritative and says so, rather than silently reporting whichever
    placement was read first.
    """
    weight = row.get("question_weight")
    if not weight:
        return
    scoring = question.setdefault("scoring", {})
    extensions = scoring.setdefault("extensions", {})
    extensions.setdefault("scope", "placement_observed")
    observed = extensions.setdefault("observed_maximum_points", [])
    if str(weight) not in observed:
        observed.append(str(weight))
    if len(observed) > 1:
        scoring["state"] = "unresolved"
        extensions["scope"] = "placement_scoped"


def _feedback_rows(row: dict[str, Any], evidence: list[str]) -> list[dict[str, Any]]:
    mapping = [
        ("general_feedback", "general"),
        ("correct_feedback", "correct"),
        ("incorrect_feedback", "incorrect"),
        ("answer_specific_feedback", "choice"),
    ]
    return [
        {
            "channel": channel,
            "source_kind": field,
            "content": _formatted(str(row[field])),
            "source_evidence_keys": evidence,
            "extensions": {},
        }
        for field, channel in mapping
        if row.get(field)
    ]


def _type_payload(
    row: dict[str, Any],
    evidence: list[str],
    *,
    row_source: str = "canonical-extractor-row",
) -> dict[str, Any]:
    kind = QUESTION_KIND_MAP.get(str(row.get("question_type", "")), "unknown")
    answers = _split(row.get("correct_answer"))
    choices = _split(row.get("choices_text"))
    options = [
        {
            "option_key": chr(ord("A") + index) if index < 26 else f"OPT_{index + 1}",
            "content": _formatted(choice),
            # Choice kinds are replaced from source facts below. Other legacy
            # type projections remain outside this bounded choice repair.
            "correct": None if kind in CHOICE_SOURCE_KINDS else choice in answers,
            "weight": None,
            "source_evidence_keys": evidence,
            "extensions": {},
        }
        for index, choice in enumerate(choices)
    ]
    accepted: list[dict[str, Any]] = []
    if kind in {"short_answer", "multi_short_answer", "fill_in_blanks"}:
        accepted = [
            {"value": answer, "case_sensitive": None, "weight": None, "extensions": {}}
            for answer in answers
        ]
    blanks: list[dict[str, Any]] = []
    for index, value in enumerate(
        _split(row.get("fill_in_blank_answers_text")), start=1
    ):
        answer = value.split(":", 1)[1].strip() if ":" in value else value
        blanks.append(
            {
                "blank_key": f"blank-{index}",
                "accepted_responses": [
                    {
                        "value": answer,
                        "case_sensitive": None,
                        "weight": None,
                        "extensions": {},
                    }
                ],
                "source_evidence_keys": evidence,
                "extensions": {},
            }
        )
    pairs = []
    for pair in answers if kind == "matching" else []:
        left, marker, right = pair.partition(" -> ")
        if marker:
            pairs.append(
                {
                    "left_key": left,
                    "right_key": right,
                    "weight": None,
                    "source_evidence_keys": evidence,
                    "extensions": {},
                }
            )
    correct_order = [
        {
            "option_key": value,
            "position": index,
            "source_evidence_keys": evidence,
            "extensions": {},
        }
        for index, value in enumerate(answers if kind == "ordering" else [], start=1)
    ]
    raw_response_models = [
        {
            # The marker names which producer row this came from. A
            # question-library record must not carry the quiz-question marker,
            # or occurrence joins and collision detection would count a dormant
            # library record as a placement.
            "source_kind": f"{row_source}:{row.get('question_type') or 'unknown'}",
            "payload": _safe_row(row),
            "source_evidence_keys": evidence,
            "extensions": {},
        }
    ]
    source_response_model = _source_response_raw_model(row, evidence)
    if source_response_model is not None:
        raw_response_models.append(source_response_model)

    manual_answer_key = None
    if row.get("correct_answer_basis") == "answer_key_material" and row.get(
        "correct_answer"
    ):
        # Brightspace long-answer answer_key_material is evaluator guidance,
        # not an automatically scored response.  Keep it typed separately so
        # reviewers can see it and so materially different guidance produces a
        # different authored-variant fingerprint.
        manual_answer_key = _formatted(str(row["correct_answer"]))

    payload = {
        "options": options,
        "accepted_responses": accepted,
        "blanks": blanks,
        "match_pairs": pairs,
        "correct_order": correct_order,
        "raw_response_models": raw_response_models,
        "extensions": {},
    }
    if manual_answer_key is not None:
        payload["manual_answer_key"] = manual_answer_key
    if kind in CHOICE_SOURCE_KINDS:
        source_projection = source_choice_projection(row.get("source_response_facts"))
        payload["options"] = [
            {"option_key": option["option_key"], "content": option["content"],
             "correct": option["correct"], "weight": None,
             "source_evidence_keys": list(evidence),
             "extensions": {"coursecraft.source_response_ident": option["source_ident"]}}
            for option in source_projection["options"]
        ]
        payload["correct_order"] = [
            {"option_key": option["option_key"], "position": option["position"],
             "source_evidence_keys": list(evidence), "extensions": {}}
            for option in source_projection["options"] if option["position"] is not None
        ]
        payload["extensions"]["coursecraft.source_choice_projection"] = {
            "state": "known" if source_projection["recognized"] else "unresolved",
            "reason": source_projection["reason"],
        }
    return payload


def _check_source_choice_occurrences(model: dict[str, Any]) -> None:
    """Refuse a single authoritative payload when same-identity facts conflict."""
    for question in model["questions"]:
        if question["kind"] not in CHOICE_SOURCE_KINDS:
            continue
        payload = question["type_payload"]
        legacy = [r for r in payload["raw_response_models"] if r["source_kind"].startswith(("canonical-extractor-row:", "question-library-row:"))]
        facts = [r for r in payload["raw_response_models"] if r["source_kind"] == "d2l_qti_response_facts/0"]
        projections = [source_choice_projection(r["payload"]) for r in facts]
        reasons = [p["reason"] for p in projections if not p["recognized"]]
        if len(legacy) != len(facts) or not facts or sorted(tuple(r["source_evidence_keys"]) for r in legacy) != sorted(tuple(r["source_evidence_keys"]) for r in facts):
            reasons.append("Source response facts do not cover every occurrence exactly once.")
        if any(QUESTION_KIND_MAP.get(str(r["payload"].get("source_question_type"))) != question["kind"] for r in facts):
            reasons.append("Question type differs across source occurrences.")
        signatures = {
            json.dumps([[o["content"], o["correct"], o["position"]] for o in p["options"]], sort_keys=True)
            for p in projections
        }
        conflict = len(signatures) > 1
        if conflict:
            reasons.append("Option material, order, or keyed source responses conflict across occurrences.")
        if not reasons:
            # Every occurrence now contributes evidence to the authoritative
            # options, while each occurrence's native identifiers stay raw.
            for option in payload["options"]:
                option["source_evidence_keys"] = list(question["source_evidence_keys"])
            continue
        code = "source_choice_occurrence_conflict" if conflict else "source_choice_projection_unresolved"
        diagnostic_id = f"diag.{code}.{_token(question['entity_key'])}"
        model["diagnostics"].append({
            "diagnostic_id": diagnostic_id, "severity": "error", "code": code,
            "message": "Source choice evidence cannot support one authoritative keyed projection.",
            "status": "open", "entity_keys": [question["entity_key"]],
            "evidence_keys": list(question["source_evidence_keys"]),
            "details": {"reasons": sorted(set(reasons))}, "extensions": {},
        })
        question["diagnostic_ids"].append(diagnostic_id)
        payload["extensions"]["coursecraft.source_choice_projection"] = {
            "state": "unresolved", "reason": " ".join(sorted(set(reasons))),
        }
        # Keep unambiguous rich option material for review, but remove all
        # authoritative keys. Conflicting material remains in the raw records.
        if conflict:
            payload["options"] = []
        for option in payload["options"]:
            option["correct"] = None
        payload["correct_order"] = []
        question["scoring"]["state"] = "unresolved"
        question["scoring"]["mode"] = "unknown"


def _relationship(
    kind: str,
    source_kind: str,
    from_key: str,
    to_key: str | None,
    status: str,
    ordinal: int | None,
    evidence: list[str],
    *,
    attributes: dict[str, Any] | None = None,
    diagnostics: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "relationship_key": f"cc:relationship:{_token(kind, source_kind, from_key, to_key, ordinal, evidence, attributes)}",
        "kind": kind,
        "source_kind": source_kind,
        "from_entity_key": from_key,
        "to_entity_key": to_key,
        "status": status,
        "ordinal": ordinal,
        "attributes": attributes or {},
        "candidates": [],
        "source_evidence_keys": evidence,
        "diagnostic_ids": diagnostics or [],
        "extensions": {},
    }


def build_normalized_model(
    payload: dict[str, Any],
    source_dir: Path,
    source_arg: Path,
    source_kind: str,
    source_lineage_key: str | None = None,
    run_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and validate a loss-aware ``coursecraft.quiz/1`` record."""
    source_sha256_cache: dict[Path, str] = {}
    digest, total_bytes, file_count = fingerprint_file_set(
        source_dir, sha256_cache=source_sha256_cache
    )

    def cached_source_sha256(path: Path) -> str:
        cache_key = path.resolve()
        file_digest = source_sha256_cache.get(cache_key)
        if file_digest is None:
            file_digest = sha256_file(path)
            source_sha256_cache[cache_key] = file_digest
        return file_digest

    source_key = make_source_key(digest)
    derived_lineage, lineage_basis = derive_source_lineage_key(source_arg, source_dir)
    lineage = source_lineage_key or derived_lineage
    run_id = (
        run_id
        or f"cc:run:quiz:{digest[:16]}:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    source_reference = source_arg.name
    source = {
        "source_key": source_key,
        "source_lineage_key": lineage,
        "source_kind": "d2l_export",
        "fingerprint": {"algorithm": "sha256", "digest": digest, "scope": "file_set"},
        "references": [source_reference],
        "extensions": {
            "file_count": file_count,
            "bytes": total_bytes,
            "intake_kind": source_kind,
            "lineage_basis": "explicit" if source_lineage_key else lineage_basis,
        },
    }
    model: dict[str, Any] = {
        "schema": "coursecraft.quiz/1",
        "model_id": f"cc:model:quiz:{digest[:24]}",
        "run_id": run_id,
        "source": source,
        "quizzes": [],
        "structures": [],
        "questions": [],
        "assets": [],
        "resources": [],
        "relationships": [],
        "evidence": [],
        "lineage": [],
        "settings_observations": [],
        "annotations": [],
        "diagnostics": [],
        "extensions": {
            "coursecraft.projection": "canonical quiz review payload",
            "legacy_summary": payload["summary"],
        },
    }

    quiz_keys: dict[str, str] = {}
    quiz_evidence: dict[str, str] = {}
    for quiz_row in payload["quiz_summary_rows"]:
        quiz_file = str(quiz_row["quiz_file"])
        evidence_key = f"ev.quiz.{_token(quiz_file)}"
        quiz_evidence[quiz_file] = evidence_key
        quiz_path = source_dir / quiz_file
        model["evidence"].append(
            {
                "evidence_key": evidence_key,
                "source_key": source_key,
                "kind": "file",
                "source_ref": quiz_file,
                "locator": "/questestinterop/assessment",
                "content": None,
                "content_ref": quiz_file,
                "sha256": (
                    cached_source_sha256(quiz_path) if quiz_path.exists() else None
                ),
                "extraction_method": "deterministic",
                "extensions": {},
            }
        )
        alias = quiz_file
        key = _entity_key("quiz", lineage, "brightspace.quiz_file", alias)
        quiz_keys[quiz_file] = key
        model["quizzes"].append(
            {
                "entity_key": key,
                "identity": _identity(
                    "brightspace.quiz_file",
                    alias,
                    [evidence_key],
                    {"title": quiz_row.get("quiz_title")},
                ),
                "title": quiz_row.get("quiz_title") or None,
                "source_evidence_keys": [evidence_key],
                "diagnostic_ids": [],
                "extensions": {"legacy_summary": quiz_row},
            }
        )

    section_rows = list(payload["quiz_section_rows"])
    section_path_counts = Counter(
        (str(row["quiz_file"]), str(row.get("section_path", "")))
        for row in section_rows
    )
    section_ident_counts = Counter(
        (str(row["quiz_file"]), str(row.get("section_ident", "")))
        for row in section_rows
        if row.get("section_ident")
    )
    raw_locator_counts = Counter(
        (str(row["quiz_file"]), str(row.get("section_source_locator", "")))
        for row in section_rows
        if row.get("section_source_locator")
    )
    structure_keys_by_row: dict[int, str] = {}
    structure_keys_by_locator: dict[tuple[str, str], str] = {}
    structure_keys_by_ident: dict[tuple[str, str], str] = {}
    structure_keys_by_path: dict[tuple[str, str], str] = {}
    structure_by_key: dict[str, dict[str, Any]] = {}

    for section_index, section in enumerate(section_rows, start=1):
        quiz_file = str(section["quiz_file"])
        evidence_key = quiz_evidence[quiz_file]
        path = str(section.get("section_path", ""))
        section_ident = str(section.get("section_ident", ""))
        raw_locator = str(section.get("section_source_locator", ""))
        locator = raw_locator or f"legacy-section-row[{section_index}]"
        if raw_locator and raw_locator_counts[(quiz_file, raw_locator)] > 1:
            locator = f"{raw_locator}::row[{section_index}]"

        path_alias = f"{quiz_file}::{path}"
        ident_alias = f"{quiz_file}::{section_ident}" if section_ident else ""
        locator_alias = f"{quiz_file}::{locator}"
        if section_path_counts[(quiz_file, path)] == 1:
            primary_namespace = "brightspace.quiz_section_path"
            primary_alias = path_alias
            identity_basis = "unique section path"
        elif section_ident and section_ident_counts[(quiz_file, section_ident)] == 1:
            primary_namespace = "brightspace.quiz_section_ident"
            primary_alias = ident_alias
            identity_basis = "section ident disambiguates repeated path"
        else:
            primary_namespace = "brightspace.quiz_section_locator"
            primary_alias = locator_alias
            identity_basis = "XML source locator disambiguates repeated aliases"

        key = _entity_key(
            "structure", lineage, primary_namespace, primary_alias
        )
        identity = _identity(primary_namespace, primary_alias, [evidence_key])
        identity_aliases: list[dict[str, Any]] = []
        seen_identity_aliases: set[tuple[str, str]] = set()
        for namespace, value in (
            (primary_namespace, primary_alias),
            ("brightspace.quiz_section_path", path_alias),
            ("brightspace.quiz_section_ident", ident_alias),
            ("brightspace.quiz_section_locator", locator_alias),
        ):
            if not value or (namespace, value) in seen_identity_aliases:
                continue
            identity_aliases.append(_source_alias(namespace, value, [evidence_key]))
            seen_identity_aliases.add((namespace, value))
        identity["source_aliases"] = identity_aliases

        is_draw = section.get("is_random_draw_section") == "yes"
        draw_count = (
            int(section["draw_count"])
            if str(section.get("draw_count", "")).isdigit()
            else None
        )
        structure = {
            "entity_key": key,
            "identity": identity,
            "kind": (
                "draw"
                if is_draw
                else (
                    "quiz_root"
                    if section.get("is_container_section") == "yes"
                    else "section"
                )
            ),
            "source_kind": "section",
            "title": section.get("section_title") or None,
            "ordinal": int(section.get("section_level") or 0),
            "selection": {
                "mode": "random" if is_draw else "fixed",
                "requested_count": draw_count,
                "available_count": int(section.get("quiz_section_item_count") or 0),
                "extensions": {},
            },
            "source_evidence_keys": [evidence_key],
            "diagnostic_ids": [],
            "extensions": {
                "section_ident": section_ident,
                "section_path": path,
                "section_source_locator": locator,
                "parent_section_source_locator": section.get(
                    "parent_section_source_locator", ""
                ),
                # A draw section may declare the points it awards per drawn
                # question independently of its library weight. Keep the raw
                # structural value so authoring projection can preserve that
                # draw's scoring without changing question-level evidence.
                "section_weight": section.get("section_weight") or None,
                "identity_basis": identity_basis,
            },
        }
        model["structures"].append(structure)
        structure_by_key[key] = structure
        structure_keys_by_row[section_index] = key
        if raw_locator and raw_locator_counts[(quiz_file, raw_locator)] == 1:
            structure_keys_by_locator[(quiz_file, raw_locator)] = key
        if section_ident and section_ident_counts[(quiz_file, section_ident)] == 1:
            structure_keys_by_ident[(quiz_file, section_ident)] = key
        if section_path_counts[(quiz_file, path)] == 1:
            structure_keys_by_path[(quiz_file, path)] = key

    for (quiz_file, path), count in sorted(section_path_counts.items()):
        if count < 2:
            continue
        collision_keys = [
            structure_keys_by_row[index]
            for index, section in enumerate(section_rows, start=1)
            if str(section["quiz_file"]) == quiz_file
            and str(section.get("section_path", "")) == path
        ]
        collision_keys = list(dict.fromkeys(collision_keys))
        if len(collision_keys) < 2:
            continue
        diagnostic_id = f"diag.structure-alias.{_token(quiz_file, path)}"
        model["diagnostics"].append(
            {
                "diagnostic_id": diagnostic_id,
                "severity": "warning",
                "code": "structure_alias_collision",
                "message": (
                    "More than one quiz section has the same human-readable path; "
                    "stable source identifiers keep the sections distinct."
                ),
                "status": "resolved",
                "entity_keys": collision_keys,
                "evidence_keys": [quiz_evidence[quiz_file]],
                "details": {
                    "quiz_file": quiz_file,
                    "section_path": path,
                    "occurrence_count": count,
                    "disambiguation": [
                        structure_by_key[key]["extensions"]["identity_basis"]
                        for key in collision_keys
                    ],
                },
                "extensions": {},
            }
        )
        for key in collision_keys:
            structure_by_key[key]["diagnostic_ids"].append(diagnostic_id)

    for section_index, section in enumerate(section_rows, start=1):
        quiz_file = str(section["quiz_file"])
        evidence_key = quiz_evidence[quiz_file]
        key = structure_keys_by_row[section_index]
        parent_locator = str(section.get("parent_section_source_locator", ""))
        parent_ident = str(section.get("parent_section_ident", ""))
        path = str(section.get("section_path", ""))
        parent_path = " > ".join(path.split(" > ")[:-1])
        parent_key = (
            structure_keys_by_locator.get((quiz_file, parent_locator))
            if parent_locator
            else None
        )
        if parent_key is None and parent_ident:
            parent_key = structure_keys_by_ident.get((quiz_file, parent_ident))
        if parent_key is None and parent_path:
            parent_key = structure_keys_by_path.get((quiz_file, parent_path))
        if parent_key is None:
            parent_key = quiz_keys.get(quiz_file)
        if parent_key:
            model["relationships"].append(
                _relationship(
                    "contains",
                    "nested section",
                    parent_key,
                    key,
                    "resolved",
                    int(section.get("section_level") or 0),
                    [evidence_key],
                )
            )

    pool_keys: dict[tuple[str, str], str] = {}
    for index, pool in enumerate(payload["pool_rows"]):
        alias = str(pool.get("pool_ident") or pool.get("pool_path") or f"pool-{index}")
        key = _entity_key("structure", lineage, "brightspace.question_pool", alias)
        pool_keys[
            (str(pool.get("pool_ident", "")), str(pool.get("pool_title", "")))
        ] = key
        questiondb_evidence = "ev.questiondb"
        if not any(
            row["evidence_key"] == questiondb_evidence for row in model["evidence"]
        ):
            qdb = source_dir / "questiondb.xml"
            model["evidence"].append(
                {
                    "evidence_key": questiondb_evidence,
                    "source_key": source_key,
                    "kind": "file",
                    "source_ref": "questiondb.xml",
                    "locator": "/questestinterop/objectbank",
                    "content": None,
                    "content_ref": "questiondb.xml",
                    "sha256": cached_source_sha256(qdb) if qdb.exists() else None,
                    "extraction_method": "deterministic",
                    "extensions": {},
                }
            )
        model["structures"].append(
            {
                "entity_key": key,
                "identity": _identity(
                    "brightspace.question_pool", alias, [questiondb_evidence]
                ),
                "kind": "pool",
                "source_kind": "questiondb.objectbank.section",
                "title": pool.get("pool_title") or None,
                "ordinal": index,
                "selection": {
                    "mode": "all",
                    "requested_count": None,
                    "available_count": int(pool.get("library_question_count") or 0),
                    "extensions": {},
                },
                "source_evidence_keys": [questiondb_evidence],
                "diagnostic_ids": [],
                "extensions": {"legacy_pool": pool},
            }
        )

    question_by_key: dict[str, dict[str, Any]] = {}
    question_lineage_by_key: dict[str, dict[str, Any]] = {}
    for row in payload["quiz_question_rows"]:
        quiz_file = str(row["quiz_file"])
        occurrence_evidence = (
            f"ev.occurrence.{_token(quiz_file, row.get('quiz_item_number'))}"
        )
        model["evidence"].append(
            {
                "evidence_key": occurrence_evidence,
                "source_key": source_key,
                "kind": "xml_element",
                "source_ref": quiz_file,
                "locator": f"quiz item {row.get('quiz_item_number')}",
                "content": None,
                "content_ref": quiz_file,
                "sha256": None,
                "extraction_method": "deterministic",
                "extensions": {},
            }
        )
        alias_namespace, alias_value = _question_alias(row)
        question_key = _entity_key("question", lineage, alias_namespace, alias_value)
        evidence_keys = [occurrence_evidence]
        if row.get("pool_question_ident") and any(
            item["evidence_key"] == "ev.questiondb" for item in model["evidence"]
        ):
            evidence_keys.append("ev.questiondb")
        if question_key not in question_by_key:
            kind = QUESTION_KIND_MAP.get(str(row.get("question_type", "")), "unknown")
            diagnostic_ids: list[str] = []
            question = {
                "entity_key": question_key,
                "identity": _identity(
                    alias_namespace,
                    alias_value,
                    evidence_keys,
                    {
                        "prompt": row.get("question_text"),
                        "choices": row.get("choices_text"),
                        "correct_answer": row.get("correct_answer"),
                        "question_type": row.get("question_type"),
                    },
                ),
                "kind": kind,
                "source_kind": str(row.get("question_type") or "[missing]"),
                "title": row.get("quiz_item_title")
                or row.get("pool_question_title")
                or None,
                "prompt": _source_prompt_content(row, "question_text"),
                "type_payload": _type_payload(row, evidence_keys),
                "scoring": {
                    "state": "known" if row.get("question_weight") else "unknown",
                    "mode": "unknown",
                    "maximum_points": row.get("question_weight") or None,
                    "rules": [],
                    "extensions": {
                        "scope": "placement_observed",
                        "observed_maximum_points": (
                            [str(row["question_weight"])]
                            if row.get("question_weight")
                            else []
                        ),
                    },
                },
                "feedback": _feedback_rows(row, evidence_keys),
                "build_support": {
                    "level": "extraction_only",
                    "receipt_refs": [],
                    "notes": [
                        "Extracted and normalized; builder round-trip is a later phase gate."
                    ],
                    "extensions": {},
                },
                "source_evidence_keys": evidence_keys,
                "diagnostic_ids": diagnostic_ids,
                "extensions": {
                    "storage": (
                        "questiondb" if row.get("pool_question_ident") else "quiz-local"
                    )
                },
            }
            question["identity"]["source_aliases"] = [
                _source_alias(namespace, value, evidence_keys)
                for namespace, value in (
                    _all_question_aliases(row) or [(alias_namespace, alias_value)]
                )
            ]
            model["questions"].append(question)
            question_by_key[question_key] = question
            lineage_row = {
                "lineage_id": f"lin.question.{_token(question_key)}",
                "subject_entity_key": question_key,
                "stage": "normalized",
                "method": "deterministic_normalization",
                "status": "lossless",
                "predecessor_entity_keys": [],
                "input_evidence_keys": list(evidence_keys),
                "actor": "scripts.extract_quiz_pool_review",
                "timestamp": None,
                "receipt_ref": None,
                "notes": ["Full canonical row preserved in raw_response_models."],
                "extensions": {},
            }
            model["lineage"].append(lineage_row)
            question_lineage_by_key[question_key] = lineage_row
        else:
            question = question_by_key[question_key]
            for evidence_key in evidence_keys:
                if evidence_key not in question["source_evidence_keys"]:
                    question["source_evidence_keys"].append(evidence_key)
                if (
                    evidence_key
                    not in question_lineage_by_key[question_key]["input_evidence_keys"]
                ):
                    question_lineage_by_key[question_key]["input_evidence_keys"].append(
                        evidence_key
                    )
            existing_aliases = {
                (alias["namespace"], alias["value"])
                for alias in question["identity"]["source_aliases"]
            }
            for namespace, value in _all_question_aliases(row):
                if (namespace, value) not in existing_aliases:
                    question["identity"]["source_aliases"].append(
                        _source_alias(namespace, value, evidence_keys)
                    )
                    existing_aliases.add((namespace, value))
            question["type_payload"]["raw_response_models"].append(
                {
                    "source_kind": f"canonical-extractor-row:{row.get('question_type') or 'unknown'}",
                    "payload": _safe_row(row),
                    "source_evidence_keys": evidence_keys,
                    "extensions": {"additional_occurrence": True},
                }
            )
            source_response_model = _source_response_raw_model(row, evidence_keys)
            if source_response_model is not None:
                question["type_payload"]["raw_response_models"].append(
                    source_response_model
                )
            _record_placement_scoring(question, row)
            existing_feedback = {
                (item["channel"], item["content"]["content"])
                for item in question["feedback"]
            }
            for feedback_row in _feedback_rows(row, evidence_keys):
                feedback_key = (
                    feedback_row["channel"],
                    feedback_row["content"]["content"],
                )
                if feedback_key not in existing_feedback:
                    question["feedback"].append(feedback_row)
                    existing_feedback.add(feedback_key)
            observed_fingerprint = make_content_fingerprint(
                {
                    "prompt": row.get("question_text"),
                    "choices": row.get("choices_text"),
                    "correct_answer": row.get("correct_answer"),
                    "question_type": row.get("question_type"),
                },
                basis="normalized projection",
            )
            original_fingerprint = question["identity"]["content_fingerprints"][0]
            if observed_fingerprint["digest"] != original_fingerprint["digest"]:
                diagnostic_id = (
                    f"diag.alias-conflict.{_token(question_key, occurrence_evidence)}"
                )
                model["diagnostics"].append(
                    {
                        "diagnostic_id": diagnostic_id,
                        "severity": "warning",
                        "code": "entity_alias_content_conflict",
                        "message": "The same durable source alias occurs with different normalized question content.",
                        "status": "open",
                        "entity_keys": [question_key],
                        "evidence_keys": [occurrence_evidence],
                        "details": {
                            "original_digest": original_fingerprint["digest"],
                            "observed_digest": observed_fingerprint["digest"],
                        },
                        "extensions": {},
                    }
                )
                if diagnostic_id not in question["diagnostic_ids"]:
                    question["diagnostic_ids"].append(diagnostic_id)

        section_locator = str(row.get("section_source_locator", ""))
        section_ident = str(row.get("section_ident", ""))
        section_path = str(row.get("section_path", ""))
        section_key = (
            structure_keys_by_locator.get((quiz_file, section_locator))
            if section_locator
            else None
        )
        if section_key is None and section_ident:
            section_key = structure_keys_by_ident.get((quiz_file, section_ident))
        if section_key is None:
            section_key = structure_keys_by_path.get((quiz_file, section_path))
        if section_key is None:
            section_key = quiz_keys[quiz_file]
        match_basis = str(row.get("match_basis", ""))
        is_itemref = match_basis.startswith("itemref")
        if is_itemref and row.get("evidence_level") == "unmatched":
            diagnostic_id = (
                f"diag.itemref.{_token(quiz_file, row.get('quiz_item_number'))}"
            )
            model["diagnostics"].append(
                {
                    "diagnostic_id": diagnostic_id,
                    "severity": "warning",
                    "code": "unresolved_itemref",
                    "message": str(
                        row.get("match_note") or "Quiz itemref could not be resolved."
                    ),
                    "status": "open",
                    "entity_keys": [question_key],
                    "evidence_keys": [occurrence_evidence],
                    "details": {
                        "match_basis": match_basis,
                        "linkrefid": row.get("quiz_item_label", ""),
                        "candidates": _match_candidates(row),
                    },
                    "extensions": {},
                }
            )
            question_by_key[question_key]["diagnostic_ids"].append(diagnostic_id)
            model["relationships"].append(
                _relationship(
                    "itemref",
                    match_basis,
                    section_key,
                    None,
                    "incomplete",
                    int(row.get("quiz_section_item_number") or 0),
                    [occurrence_evidence],
                    attributes={"linkrefid": row.get("quiz_item_label", "")},
                    diagnostics=[diagnostic_id],
                )
            )
        else:
            model["relationships"].append(
                _relationship(
                    "itemref" if is_itemref else "contains",
                    match_basis or "inline item",
                    section_key,
                    question_key,
                    "resolved",
                    int(row.get("quiz_section_item_number") or 0),
                    [occurrence_evidence],
                )
            )

        pool_key = pool_keys.get(
            (str(row.get("pool_ident", "")), str(row.get("pool_title", "")))
        )
        if pool_key:
            relation_status = (
                "resolved"
                if row.get("evidence_level") == "source_evidence"
                else "proposed"
            )
            model["relationships"].append(
                _relationship(
                    "member_of",
                    match_basis or "question-library match",
                    question_key,
                    pool_key,
                    relation_status,
                    None,
                    evidence_keys,
                    attributes={
                        "evidence_level": row.get("evidence_level"),
                        "match_score": row.get("match_score", ""),
                        "note": row.get("match_note", ""),
                        "candidates": _match_candidates(row),
                    },
                )
            )
        elif row.get("evidence_level") == "unmatched" and not is_itemref:
            diagnostic_id = (
                f"diag.match.{_token(quiz_file, row.get('quiz_item_number'))}"
            )
            model["diagnostics"].append(
                {
                    "diagnostic_id": diagnostic_id,
                    "severity": "info",
                    "code": "no_safe_library_match",
                    "message": str(
                        row.get("match_note")
                        or "No safe question-library match was found."
                    ),
                    "status": "open",
                    "entity_keys": [question_key],
                    "evidence_keys": [occurrence_evidence],
                    "details": {
                        "match_basis": match_basis,
                        "match_score": row.get("match_score", ""),
                        "candidates": _match_candidates(row),
                    },
                    "extensions": {},
                }
            )
            if diagnostic_id not in question_by_key[question_key]["diagnostic_ids"]:
                question_by_key[question_key]["diagnostic_ids"].append(diagnostic_id)

    for question in model["questions"]:
        _enrich_short_answer_question(question, model["evidence"])

    asset_rows = list(payload["question_image_rows"])
    asset_descriptors: list[dict[str, Any]] = []
    allowed_asset_statuses = {
        "resolved",
        "missing",
        "orphaned",
        "ambiguous",
        "unknown",
    }
    for image_index, image in enumerate(asset_rows):
        quiz_file = str(image["quiz_file"])
        source_path = str(image.get("image_ref_original", ""))
        package_path = str(image.get("image_file_path", "")) or None
        raw_status = str(image.get("image_status", "") or "unknown")
        status = raw_status if raw_status in allowed_asset_statuses else "unknown"
        resolved_path = source_dir / package_path if package_path else None
        fingerprint_digest = None
        if resolved_path is not None and resolved_path.is_file():
            fingerprint_digest = cached_source_sha256(resolved_path)
        media_type = mimetypes.guess_type(package_path or source_path)[0]
        material_signature = (
            status,
            package_path or "",
            fingerprint_digest or "",
            media_type or "",
        )
        if status == "resolved" and package_path and fingerprint_digest:
            identity_group = (
                "resolved_package",
                package_path,
                fingerprint_digest,
                media_type or "",
            )
        else:
            identity_group = (
                "exact_source_reference",
                source_path,
                *material_signature,
            )
        stable_aliases: list[tuple[str, str]] = []
        if package_path:
            stable_aliases.append(("brightspace.asset_path", package_path))
        if source_path:
            stable_aliases.append(("brightspace.asset_ref", source_path))
        if not stable_aliases:
            stable_aliases.append(
                (
                    "brightspace.asset_ref",
                    f"{quiz_file}::image[{image_index + 1}]",
                )
            )
        asset_descriptors.append(
            {
                "index": image_index,
                "image": image,
                "quiz_file": quiz_file,
                "evidence_key": quiz_evidence[quiz_file],
                "source_path": source_path,
                "package_path": package_path,
                "status": status,
                "media_type": media_type,
                "fingerprint_digest": fingerprint_digest,
                "material_signature": material_signature,
                "identity_group": identity_group,
                "stable_aliases": stable_aliases,
            }
        )

    descriptors_by_identity: dict[tuple[Any, ...], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    alias_identity_groups: dict[
        tuple[str, str], set[tuple[Any, ...]]
    ] = defaultdict(set)
    for descriptor in asset_descriptors:
        identity_group = descriptor["identity_group"]
        descriptors_by_identity[identity_group].append(descriptor)
        for alias in descriptor["stable_aliases"]:
            alias_identity_groups[alias].add(identity_group)

    conflicted_asset_groups = {
        identity_group
        for groups in alias_identity_groups.values()
        if len(groups) > 1
        for identity_group in groups
    }
    asset_key_by_image_index: dict[int, str] = {}
    asset_key_by_identity: dict[tuple[Any, ...], str] = {}
    asset_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for identity_group, descriptors in descriptors_by_identity.items():
        evidence_keys = list(
            dict.fromkeys(str(row["evidence_key"]) for row in descriptors)
        )
        package_paths = list(
            dict.fromkeys(
                str(row["package_path"])
                for row in descriptors
                if row["package_path"]
            )
        )
        source_paths = list(
            dict.fromkeys(
                str(row["source_path"])
                for row in descriptors
                if row["source_path"]
            )
        )
        primary_base_alias = (
            package_paths[0]
            if package_paths
            else (
                source_paths[0]
                if source_paths
                else str(descriptors[0]["stable_aliases"][0][1])
            )
        )
        if identity_group in conflicted_asset_groups:
            primary_namespace = "brightspace.asset_variant"
            primary_alias = (
                f"{primary_base_alias}::material-{_token(identity_group)}"
            )
            identity_basis = "material variant of conflicting source alias"
        else:
            primary_namespace = "brightspace.asset_path"
            primary_alias = primary_base_alias
            identity_basis = (
                "resolved package path and byte fingerprint"
                if identity_group[0] == "resolved_package"
                else "exact source reference and material signature"
            )
        key = _entity_key("asset", lineage, primary_namespace, primary_alias)
        identity = _identity(primary_namespace, primary_alias, evidence_keys)
        identity_aliases: list[dict[str, Any]] = []
        seen_aliases: set[tuple[str, str]] = set()
        for namespace, value in [
            (primary_namespace, primary_alias),
            *[
                alias
                for descriptor in descriptors
                for alias in descriptor["stable_aliases"]
            ],
        ]:
            if (namespace, value) in seen_aliases:
                continue
            identity_aliases.append(_source_alias(namespace, value, evidence_keys))
            seen_aliases.add((namespace, value))
        identity["source_aliases"] = identity_aliases

        first = descriptors[0]
        fingerprint_digest = first["fingerprint_digest"]
        fingerprint = (
            {
                "algorithm": "sha256",
                "digest": fingerprint_digest,
                "basis": "file bytes",
                "extensions": {},
            }
            if fingerprint_digest
            else None
        )
        resolution_bases = list(
            dict.fromkeys(
                str(row["image"].get("image_resolution_basis", ""))
                for row in descriptors
                if row["image"].get("image_resolution_basis")
            )
        )
        review_copy_paths = list(
            dict.fromkeys(
                str(row["image"].get("review_copy_path"))
                for row in descriptors
                if row["image"].get("review_copy_path")
            )
        )
        asset = {
            "entity_key": key,
            "identity": identity,
            "source_path": source_paths[0] if source_paths else None,
            "package_path": package_paths[0] if package_paths else None,
            "media_type": first["media_type"],
            "fingerprint": fingerprint,
            "status": first["status"],
            "source_evidence_keys": evidence_keys,
            "diagnostic_ids": [],
            "extensions": {
                "identity_basis": identity_basis,
                "source_paths": source_paths,
                "package_paths": package_paths,
                "resolution_basis": resolution_bases[0]
                if resolution_bases
                else "",
                "resolution_bases": resolution_bases,
                "review_copy_path": review_copy_paths[0]
                if review_copy_paths
                else None,
                "review_copy_paths": review_copy_paths,
            },
        }
        model["assets"].append(asset)
        asset_key_by_identity[identity_group] = key
        asset_by_identity[identity_group] = asset
        for descriptor in descriptors:
            asset_key_by_image_index[int(descriptor["index"])] = key

        if first["status"] != "resolved":
            diagnostic_id = f"diag.asset.{_token(key)}"
            asset["diagnostic_ids"].append(diagnostic_id)
            model["diagnostics"].append(
                {
                    "diagnostic_id": diagnostic_id,
                    "severity": "warning",
                    "code": "asset_unresolved",
                    "message": (
                        "Asset reference could not be resolved: "
                        f"{source_paths[0] if source_paths else primary_base_alias}"
                    ),
                    "status": "open",
                    "entity_keys": [key],
                    "evidence_keys": evidence_keys,
                    "details": {
                        "source_paths": source_paths,
                        "package_paths": package_paths,
                        "resolution_bases": resolution_bases,
                    },
                    "extensions": {},
                }
            )

    for (alias_namespace, alias_value), identity_groups in sorted(
        alias_identity_groups.items()
    ):
        if len(identity_groups) < 2:
            continue
        ordered_groups = sorted(identity_groups, key=lambda value: repr(value))
        entity_keys = [asset_key_by_identity[group] for group in ordered_groups]
        evidence_keys = list(
            dict.fromkeys(
                evidence_key
                for group in ordered_groups
                for evidence_key in asset_by_identity[group]["source_evidence_keys"]
            )
        )
        diagnostic_id = (
            f"diag.asset-conflict.{_token(alias_namespace, alias_value)}"
        )
        model["diagnostics"].append(
            {
                "diagnostic_id": diagnostic_id,
                "severity": "warning",
                "code": "asset_identity_conflict",
                "message": (
                    "The same stable asset alias resolves to materially distinct "
                    "asset records; each variant was retained."
                ),
                "status": "open",
                "entity_keys": entity_keys,
                "evidence_keys": evidence_keys,
                "details": {
                    "alias_namespace": alias_namespace,
                    "alias_value": alias_value,
                    "variants": [
                        {
                            "entity_key": asset_key_by_identity[group],
                            "status": asset_by_identity[group]["status"],
                            "package_path": asset_by_identity[group]["package_path"],
                            "media_type": asset_by_identity[group]["media_type"],
                            "fingerprint": asset_by_identity[group]["fingerprint"],
                            "source_paths": asset_by_identity[group]["extensions"]
                            ["source_paths"],
                        }
                        for group in ordered_groups
                    ],
                },
                "extensions": {},
            }
        )
        for group in ordered_groups:
            asset_by_identity[group]["diagnostic_ids"].append(diagnostic_id)

    question_rows_by_occurrence = {
        (str(row["quiz_file"]), int(row["quiz_item_number"])): row
        for row in payload["quiz_question_rows"]
    }
    for image_index, image in enumerate(asset_rows):
        quiz_file = str(image["quiz_file"])
        source_path = str(image.get("image_ref_original", ""))
        item_number = int(image["quiz_item_number"])
        key = asset_key_by_image_index[image_index]
        row_match = question_rows_by_occurrence.get((quiz_file, item_number))
        if row_match:
            namespace, value = _question_alias(row_match)
            question_key = _entity_key("question", lineage, namespace, value)
            occurrence_evidence = (
                f"ev.occurrence.{_token(quiz_file, item_number)}"
            )
            model["relationships"].append(
                _relationship(
                    "uses_asset",
                    "html asset reference",
                    question_key,
                    key,
                    (
                        "resolved"
                        if image.get("image_status") == "resolved"
                        else "proposed"
                    ),
                    int(image.get("image_number") or 0),
                    [occurrence_evidence],
                    attributes={"raw_ref": source_path},
                )
            )

    settings = collect_quiz_settings(source_dir)
    for quiz_file, rows in settings.items():
        quiz_key = quiz_keys.get(quiz_file)
        if not quiz_key:
            continue
        evidence_key = quiz_evidence[quiz_file]
        for observation in rows:
            model["settings_observations"].append(
                {
                    "target_entity_key": quiz_key,
                    "name": observation["name"],
                    "state": "observed",
                    "raw_value": observation["raw"],
                    "normalized_value": observation["normalized"],
                    "source_evidence_keys": [evidence_key],
                    "extensions": {
                        "source_kind": observation["source_kind"],
                        "sensitive": observation.get("sensitive", False),
                    },
                }
            )

    if not source_lineage_key and lineage_basis == "unresolved source label":
        model["diagnostics"].append(
            {
                "diagnostic_id": "diag.source-lineage-unresolved",
                "severity": "warning",
                "code": "source_lineage_unresolved",
                "message": "No source-backed course identity was found; pass --source-lineage-key to keep entity identities stable across renamed refreshes.",
                "status": "open",
                "entity_keys": [],
                "evidence_keys": [],
                "details": {"fallback_basis": source_arg.name},
                "extensions": {},
            }
        )

    question_keys = {
        str(row.get("entity_key"))
        for row in model.get("questions", [])
        if row.get("entity_key")
    }
    inline_occurrence_count = sum(
        row.get("kind") == "contains" and row.get("to_entity_key") in question_keys
        for row in model.get("relationships", [])
    )
    itemref_rows = [
        row
        for row in model.get("relationships", [])
        if row.get("kind") == "itemref"
    ]
    questiondb_present = (source_dir / "questiondb.xml").exists()
    if questiondb_present:
        coverage_kind = (
            "question_library_with_itemrefs"
            if itemref_rows
            else "question_library_inline"
        )
    else:
        coverage_kind = (
            "quiz_only_with_unresolved_itemrefs"
            if itemref_rows
            else "quiz_only_inline"
        )
    quiz_scope = {
        "coverage_kind": coverage_kind,
        "questiondb_present": questiondb_present,
        "inline_occurrence_count": inline_occurrence_count,
        "itemref_occurrence_count": len(itemref_rows),
        "resolved_itemref_count": sum(
            row.get("status") == "resolved" and row.get("to_entity_key") in question_keys
            for row in itemref_rows
        ),
        "unresolved_itemref_count": sum(
            row.get("status") != "resolved" or row.get("to_entity_key") not in question_keys
            for row in itemref_rows
        ),
    }
    model["source"]["extensions"]["quiz_scope"] = quiz_scope

    if any(row["evidence_key"] == "ev.questiondb" for row in model["evidence"]):
        build_library_question_entities(model, payload, lineage, "ev.questiondb")
    _check_source_choice_occurrences(model)
    apply_authored_variant_identity(model)

    issues = validate_contract(model, mode="transform")
    if issues:
        rendered = "\n".join(issue.render() for issue in issues)
        raise ValueError(
            f"Normalized quiz model failed contract validation:\n{rendered}"
        )
    return model, {
        "digest": digest,
        "bytes": total_bytes,
        "file_count": file_count,
        "questiondb_present": questiondb_present,
        "quiz_scope": quiz_scope,
    }


def artifact_record(
    path: Path, output_dir: Path, role: str, status: str = "emitted"
) -> dict[str, Any]:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "role": role,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "sha256": sha256_file(path) if path.exists() else None,
        "bytes": path.stat().st_size if path.exists() else None,
        "status": status,
        "extensions": {},
    }


def _producer_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def producer_tree_state() -> str:
    """Report whether the producing checkout was clean at extraction time.

    A commit alone does not identify the code that produced a run: a dirty tree
    can carry behavior the named commit does not contain.  The state is recorded
    so a reader can tell a reproducible run from an unreproducible one.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return "dirty" if result.stdout.strip() else "clean"


def build_run_receipt(
    model: dict[str, Any],
    source_meta: dict[str, Any],
    source_arg: Path,
    output_dir: Path,
    artifacts: list[tuple[Path, str]],
    started_at: str,
    finished_at: str,
    command: str,
) -> dict[str, Any]:
    artifact_rows = [
        artifact_record(path, output_dir, role) for path, role in artifacts
    ]
    asset_artifact_paths = [
        row["path"] for row in artifact_rows if "_assets/" in row["path"]
    ]
    question_library_status = (
        "completed" if source_meta["questiondb_present"] else "skipped"
    )
    receipt = {
        "schema": "coursecraft.quiz_run/1",
        "run_id": model["run_id"],
        "started_at": started_at,
        "finished_at": finished_at,
        "producer": {
            "name": "coursecraft-workbench canonical quiz extractor",
            "version": "phase-3",
            "commit": _producer_commit(),
            "release": None,
            "command": command,
            "extensions": {"tree_state": producer_tree_state()},
        },
        "source": model["source"],
        "contract_versions": [
            {"schema": schema_id, "schema_sha256": sha256_file(path), "extensions": {}}
            for schema_id, path in SCHEMA_REGISTRY.items()
        ],
        "inputs": [
            {
                "path": source_arg.name,
                "role": "input",
                "media_type": (
                    "application/zip"
                    if source_arg.is_file()
                    else "application/vnd.coursecraft.file-set"
                ),
                "sha256": (
                    sha256_file(source_arg)
                    if source_arg.is_file()
                    else source_meta["digest"]
                ),
                "bytes": (
                    source_arg.stat().st_size
                    if source_arg.is_file()
                    else source_meta["bytes"]
                ),
                "status": "read",
                "extensions": {"file_count": source_meta["file_count"]},
            }
        ],
        "artifacts": artifact_rows,
        "capabilities": [
            {
                "name": "safe_zip_or_folder_intake",
                "status": "completed",
                "started_at": None,
                "finished_at": None,
                "artifact_paths": [],
                "diagnostic_ids": [],
                "notes": [],
                "extensions": {},
            },
            {
                "name": "question_library_resolution",
                "status": question_library_status,
                "started_at": None,
                "finished_at": None,
                "artifact_paths": [],
                "diagnostic_ids": [],
                "notes": (
                    []
                    if source_meta["questiondb_present"]
                    else [
                        "questiondb.xml was not present; quiz-local content and unresolved itemrefs were retained."
                    ]
                ),
                "extensions": {"quiz_scope": source_meta["quiz_scope"]},
            },
            {
                "name": "legacy_review_projections",
                "status": "completed",
                "started_at": None,
                "finished_at": None,
                "artifact_paths": [
                    row["path"] for row in artifact_rows if row["role"] == "review"
                ],
                "diagnostic_ids": [],
                "notes": [],
                "extensions": {},
            },
            {
                "name": "normalize_quiz_contract",
                "status": "completed",
                "started_at": None,
                "finished_at": None,
                "artifact_paths": [
                    row["path"] for row in artifact_rows if row["role"] == "model"
                ],
                "diagnostic_ids": [],
                "notes": [],
                "extensions": {},
            },
            {
                "name": "copy_reviewer_assets",
                "status": "completed" if asset_artifact_paths else "skipped",
                "started_at": None,
                "finished_at": None,
                "artifact_paths": asset_artifact_paths,
                "diagnostic_ids": [],
                "notes": (
                    []
                    if asset_artifact_paths
                    else ["No reviewer asset copies were emitted."]
                ),
                "extensions": {},
            },
        ],
        "diagnostics": [],
        "extensions": {
            "receipt_self_path": f"{source_arg.stem}__quiz_pool_review.run.json"
        },
    }
    issues = validate_contract(receipt, mode="transform")
    if issues:
        rendered = "\n".join(issue.render() for issue in issues)
        raise ValueError(f"Quiz run receipt failed contract validation:\n{rendered}")
    return receipt


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
