#!/usr/bin/env python3
"""Shared contracts, source fingerprints, and stable keys for course artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "workspace" / "reference" / "schemas" / "course"
SCHEMA_REGISTRY = {
    "coursecraft.activities/1": SCHEMA_ROOT / "activities_schema.json",
    "coursecraft.structure/1": SCHEMA_ROOT / "structure_schema.json",
    "coursecraft.run/1": SCHEMA_ROOT / "run_identity_schema.json",
    "coursecraft.component_package_receipt/1": SCHEMA_ROOT / "component_package_receipt_schema.json",
    "coursecraft.blueprint/4": REPO_ROOT / "workspace" / "reference" / "schemas" / "blueprint" / "blueprint_schema.json",
    "coursecraft.rubrics/1": REPO_ROOT / "workspace" / "reference" / "schemas" / "rubrics" / "rubrics_schema.json",
    "coursecraft.page_kit/1": REPO_ROOT / "workspace" / "reference" / "schemas" / "page-kit" / "page_kit.schema.json",
    "coursecraft.page/1": REPO_ROOT / "workspace" / "reference" / "schemas" / "page-kit" / "page.schema.json",
    "coursecraft.module_composition/1": REPO_ROOT / "workspace" / "reference" / "schemas" / "page-kit" / "module_composition.schema.json",
    "coursecraft.page_build/1": REPO_ROOT / "workspace" / "reference" / "schemas" / "page-kit" / "page_build.schema.json",
}
EXPORT_NAME_PATTERN = re.compile(
    r"D2LExport_(?P<org_unit_id>\d+)_(?P<course_code>.+?)_(?P<timestamp>\d{8,14})(?:_|$|\.)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scoped_key(prefix: str, *parts: object, length: int = 32) -> str:
    digest = canonical_sha256([str(part) for part in parts])
    return f"cc:{prefix}:{digest[:length]}"


def logical_file_set_fingerprint(root: Path) -> dict[str, Any]:
    """Hash a logical unpacked package independent of ZIP container metadata."""
    root = root.resolve()
    aggregate = hashlib.sha256()
    file_count = 0
    byte_count = 0
    skipped_symlinks = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            skipped_symlinks += 1
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        digest = sha256_file(path)
        aggregate.update(json.dumps([relative, size, digest], separators=(",", ":")).encode("utf-8"))
        aggregate.update(b"\n")
        file_count += 1
        byte_count += size
    return {
        "algorithm": "sha256",
        "digest": aggregate.hexdigest(),
        "scope": "logical_file_set",
        "file_count": file_count,
        "bytes": byte_count,
        "extensions": {"skipped_symlink_count": skipped_symlinks},
    }


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _child_text(root: ET.Element, name: str) -> str | None:
    for child in root.iter():
        if _local_name(child.tag) == name and (child.text or "").strip():
            return (child.text or "").strip()
    return None


def observe_export_identity(source_arg: Path, logical_root: Path) -> dict[str, Any]:
    """Recover only identity evidence already present in names or orgunit XML."""
    identity: dict[str, Any] = {
        "source_name": source_arg.name,
        "org_unit_id": None,
        "course_code": None,
        "export_timestamp": None,
        "orgunit_identifier": None,
        "orgunit_code": None,
        "orgunit_name": None,
    }
    candidates = [source_arg.stem if source_arg.is_file() else source_arg.name, logical_root.name]
    for candidate in candidates:
        match = EXPORT_NAME_PATTERN.search(candidate or "")
        if match:
            identity["org_unit_id"] = match.group("org_unit_id")
            identity["course_code"] = match.group("course_code")
            identity["export_timestamp"] = match.group("timestamp")
            break
    orgunit = logical_root / "orgunitconfig" / "orgunitconfig.xml"
    if orgunit.is_file():
        try:
            root = ET.parse(orgunit).getroot()
        except ET.ParseError:
            root = None
        if root is not None and _local_name(root.tag) == "orgunit":
            identity["orgunit_identifier"] = next(
                (value for key, value in root.attrib.items() if _local_name(key) == "identifier"),
                None,
            )
            identity["orgunit_code"] = _child_text(root, "code")
            identity["orgunit_name"] = _child_text(root, "name")
    return identity


def build_source_identity(
    source_arg: Path,
    logical_root: Path,
    *,
    observed_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logical = logical_file_set_fingerprint(logical_root)
    observed = dict(observed_identity or observe_export_identity(source_arg, logical_root))
    evidence_order = (
        ("orgunit_identifier", observed.get("orgunit_identifier")),
        ("org_unit_id", observed.get("org_unit_id")),
        ("orgunit_code", observed.get("orgunit_code")),
        ("course_code", observed.get("course_code")),
    )
    lineage_basis, lineage_value = next(
        ((name, str(value)) for name, value in evidence_order if value not in (None, "")),
        ("logical_fingerprint", logical["digest"]),
    )
    lineage_state = "resolved" if lineage_basis != "logical_fingerprint" else "unresolved"
    transport = None
    if source_arg.is_file():
        transport = {
            "algorithm": "sha256",
            "digest": sha256_file(source_arg),
            "scope": "transport_file",
            "bytes": source_arg.stat().st_size,
            "extensions": {},
        }
    return {
        "source_lineage_key": _scoped_key("lineage", lineage_basis, lineage_value),
        "source_instance_key": f"cc:source:{logical['digest']}",
        "lineage_state": lineage_state,
        "lineage_basis": lineage_basis,
        "source_name": source_arg.name,
        "logical_fingerprint": logical,
        "transport_fingerprint": transport,
        "observed_identity": observed,
        "extensions": {},
    }


def load_source_identity(
    context_path: Path | None,
    *,
    source_arg: Path,
    logical_root: Path,
    observed_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if context_path is not None:
        return json.loads(context_path.read_text(encoding="utf-8"))
    return build_source_identity(source_arg, logical_root, observed_identity=observed_identity)


def new_run_id() -> str:
    return f"cc:run:{uuid.uuid4()}"


def _alias_rows(record: dict[str, Any], fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            rows.append({"kind": field, "value": str(value)})
    return rows


def add_entity_identity(
    record: dict[str, Any],
    *,
    source: dict[str, Any],
    entity_kind: str,
    aliases: tuple[str, ...],
    fallback_value: str,
) -> dict[str, Any]:
    alias_rows = _alias_rows(record, aliases)
    if alias_rows:
        selected = alias_rows[0]
        if selected["kind"] == "resource_code":
            quality = "durable_source_id"
        elif selected["kind"] in {"identifier", "id", "ident", "topic_id", "forum_id"}:
            quality = "scoped_source_id"
        else:
            quality = "observed_reference"
        basis = selected["kind"]
        value = selected["value"]
    else:
        quality = "fallback"
        basis = "source_context"
        value = fallback_value or entity_kind
    record["entity_key"] = _scoped_key(
        "entity", source["source_lineage_key"], entity_kind, basis, value
    )
    record["identity"] = {
        "quality": quality,
        "basis": basis,
        "source_lineage_key": source["source_lineage_key"],
        "source_aliases": alias_rows,
        "extensions": {},
    }
    return record


def annotate_activity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload["source"]
    alias_entities: dict[str, set[str]] = {}
    configs = {
        "condition_sets": ("condition_set", ("resource_code", "tool"), ("tool", "operator")),
        "dropbox_folders": ("assignment", ("resource_code", "id"), ("source_file", "name")),
        "discussions": ("discussion", ("resource_code", "topic_id", "forum_id", "id"), ("source_file", "title")),
        "checklists": ("checklist", ("resource_code", "id", "checklist_id"), ("source_file", "title", "name")),
        "quizzes": ("quiz", ("resource_code", "ident", "id"), ("source_file", "title")),
        "grade_items": ("grade_item", ("resource_code", "identifier", "id"), ("name", "short_name")),
    }
    for collection, (base_kind, aliases, fallback_fields) in configs.items():
        for index, record in enumerate(payload.get(collection, []) or []):
            entity_kind = str(record.get("kind") or base_kind)
            fallback = "|".join(
                str(record.get(field) or "") for field in fallback_fields
            ) or f"{collection}:{index}"
            add_entity_identity(
                record,
                source=source,
                entity_kind=entity_kind,
                aliases=aliases,
                fallback_value=fallback,
            )
            for alias in record["identity"]["source_aliases"]:
                alias_entities.setdefault(alias["value"], set()).add(record["entity_key"])

    relation_counts: dict[tuple[str, ...], int] = {}
    for relation in payload.get("joins", []) or []:
        source_value = str(relation.get("source_code") or relation.get("source") or "")
        target_value = str(relation.get("target") or "")
        semantic_basis = (
            str(relation.get("source_kind") or ""),
            source_value,
            str(relation.get("join_type") or "join"),
            target_value,
            str(relation.get("resolved") or ""),
        )
        duplicate_ordinal = relation_counts.get(semantic_basis, 0)
        relation_counts[semantic_basis] = duplicate_ordinal + 1
        source_matches = alias_entities.get(source_value, set())
        target_matches = alias_entities.get(target_value, set())
        source_entity_key = next(iter(source_matches)) if len(source_matches) == 1 else None
        target_entity_key = next(iter(target_matches)) if len(target_matches) == 1 else None
        relation["source_entity_key"] = source_entity_key
        relation["target_entity_key"] = target_entity_key
        if source_entity_key and target_entity_key:
            relationship_quality = "resolved_endpoints"
        elif source_entity_key or target_entity_key:
            relationship_quality = "partial_endpoints"
        else:
            relationship_quality = "evidence_only"
        relation["relationship_key"] = _scoped_key(
            "relationship",
            source["source_lineage_key"],
            *semantic_basis,
            duplicate_ordinal,
        )
        relation["identity"] = {
            "quality": relationship_quality,
            "source_lineage_key": source["source_lineage_key"],
            "basis": ["source_kind", "source_code", "join_type", "target", "resolved"],
            "duplicate_ordinal": duplicate_ordinal,
            "extensions": {},
        }
    return payload


def annotate_structure_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload["source"]

    def visit(node: dict[str, Any], path: tuple[int, ...], parent_key: str | None) -> None:
        fallback = "/".join(str(part) for part in path) + f"|{node.get('kind', '')}|{node.get('title', '')}"
        add_entity_identity(
            node,
            source=source,
            entity_kind=f"structure:{node.get('kind') or 'unknown'}",
            aliases=("identifier", "rcode", "resource_code", "identifierref", "href"),
            fallback_value=fallback,
        )
        node["parent_entity_key"] = parent_key
        for index, child in enumerate(node.get("children", []) or []):
            if isinstance(child, dict):
                visit(child, (*path, index), node["entity_key"])

    for index, root in enumerate(payload.get("tree", []) or []):
        if isinstance(root, dict):
            visit(root, (index,), None)
    for index, topic in enumerate(payload.get("html_topics", []) or []):
        fallback = "|".join(
            str(topic.get(field) or "")
            for field in ("href", "source_file", "manifest_title", "html_title")
        ) or f"html_topics:{index}"
        add_entity_identity(
            topic,
            source=source,
            entity_kind="html_topic_body",
            aliases=("identifier", "resource_code", "href", "source_file", "manifest_title"),
            fallback_value=fallback,
        )
    return payload


@dataclass(frozen=True)
class ContractIssue:
    severity: str
    code: str
    message: str

    def render(self) -> str:
        return f"{self.severity.upper()} {self.code}: {self.message}"


def validate_contract(payload: dict[str, Any], *, mode: str = "transform") -> list[ContractIssue]:
    schema_id = payload.get("schema")
    schema_path = SCHEMA_REGISTRY.get(schema_id)
    if schema_path is None:
        severity = "warning" if mode == "inspect" else "error"
        return [ContractIssue(severity, "unknown_schema", f"Unrecognized schema {schema_id!r}.")]
    try:
        import jsonschema
    except ImportError:
        return [ContractIssue("warning", "jsonschema_unavailable", "jsonschema is not installed.")]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    errors = sorted(
        validator_class(schema, format_checker=jsonschema.FormatChecker()).iter_errors(payload),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    issues = []
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "(root)"
        issues.append(ContractIssue("error", "schema_validation", f"{location}: {error.message}"))
    return issues


def producer_identity(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    release_manifest = repo_root / "RELEASE_MANIFEST.json"
    if release_manifest.is_file():
        try:
            release = json.loads(release_manifest.read_text(encoding="utf-8"))
            source = release.get("source", {})
            return {
                "component": "coursecraft_workbench",
                "identity_state": "release",
                "version": release.get("version"),
                "repository": source.get("repository"),
                "ref": source.get("ref"),
                "commit": source.get("commit"),
                "dirty": False,
                "extensions": {"release_schema": release.get("schema")},
            }
        except (OSError, json.JSONDecodeError):
            pass

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def normalized_remote(value: str) -> str | None:
        text = value.strip()
        if not text:
            return None
        if text.startswith("git@") and ":" in text:
            host_path = text.split("@", 1)[1]
            host, path = host_path.split(":", 1)
            return f"https://{host}/{path}"
        if text.startswith(("http://", "https://")):
            parts = urlsplit(text)
            host = parts.hostname or parts.netloc
            if parts.port:
                host += f":{parts.port}"
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
        return text

    commit = git("rev-parse", "HEAD")
    if commit:
        remote = normalized_remote(git("remote", "get-url", "origin"))
        return {
            "component": "coursecraft_workbench",
            "identity_state": "git",
            "version": None,
            "repository": remote,
            "ref": git("branch", "--show-current") or None,
            "commit": commit,
            "dirty": bool(git("status", "--porcelain")),
            "extensions": {},
        }
    return {
        "component": "coursecraft_workbench",
        "identity_state": "unknown",
        "version": None,
        "repository": None,
        "ref": None,
        "commit": None,
        "dirty": None,
        "extensions": {},
    }


def contract_receipts(schema_ids: list[str]) -> list[dict[str, Any]]:
    receipts = []
    for schema_id in schema_ids:
        path = SCHEMA_REGISTRY[schema_id]
        receipts.append(
            {
                "schema": schema_id,
                "schema_path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": sha256_file(path),
                "extensions": {},
            }
        )
    return receipts


def artifact_receipts(
    bundle_dir: Path,
    *,
    contract_by_name: dict[str, str],
    exclude_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    receipts = []
    excluded = exclude_paths or set()
    for path in sorted(
        item for item in bundle_dir.rglob("*") if item.is_file() and not item.is_symlink()
    ):
        relative = path.relative_to(bundle_dir).as_posix()
        if relative in excluded:
            continue
        receipts.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "contract": contract_by_name.get(relative, contract_by_name.get(path.name)),
                "extensions": {},
            }
        )
    return receipts


def build_run_identity(
    *,
    run_id: str,
    source: dict[str, Any],
    bundle_dir: Path,
    receipt_name: str,
    started_at: str,
    steps: list[dict[str, Any]],
    parameters: dict[str, Any],
    contract_by_name: dict[str, str],
    diagnostics: list[Any] | None = None,
) -> dict[str, Any]:
    schema_ids = sorted({"coursecraft.run/1", *[value for value in contract_by_name.values() if value]})
    status = "partial" if any(step.get("status") in {"failed", "unresolved"} for step in steps) else "ok"
    return {
        "schema": "coursecraft.run/1",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": utc_now(),
        "source": source,
        "producer": producer_identity(),
        "contracts": contract_receipts(schema_ids),
        "parameters": parameters,
        "steps": steps,
        "emitted_files": artifact_receipts(
            bundle_dir,
            contract_by_name=contract_by_name,
            exclude_paths={receipt_name},
        ),
        "receipt_path": receipt_name,
        "diagnostics": list(diagnostics or []),
        "extensions": {"coursecraft.live_brightspace_operations": "not_performed"},
    }


def verify_run_identity(receipt: dict[str, Any], bundle_dir: Path) -> list[str]:
    problems = []
    for artifact in receipt.get("emitted_files", []):
        path = bundle_dir / artifact["path"]
        if path.is_symlink():
            problems.append(f"symlink artifact is not portable: {artifact['path']}")
            continue
        try:
            path.resolve().relative_to(bundle_dir.resolve())
        except ValueError:
            problems.append(f"unsafe artifact path: {artifact['path']}")
            continue
        if not path.is_file():
            problems.append(f"missing artifact: {artifact['path']}")
            continue
        actual = sha256_file(path)
        if actual != artifact["sha256"]:
            problems.append(
                f"artifact checksum mismatch: {artifact['path']} expected {artifact['sha256']} got {actual}"
            )
    return problems
