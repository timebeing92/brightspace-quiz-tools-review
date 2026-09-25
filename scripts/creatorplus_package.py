#!/usr/bin/env python3
"""Index and diagnose Creator+ Practice relationships in a D2L package.

Creator+ ``data-file`` references are package relationships, not ordinary
browser-relative assets.  This module keeps one typed, evidence-oriented index
that package conformance and course reconstruction can share.

The index never renames or deletes payloads.  ``coursecraft`` profile enforces
the producer contract that referenced Practice JSON is emitted directly under
package-root ``practice/``.  ``external`` profile preserves arbitrary exports
for inspection while reporting non-root observations as warnings.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


PRACTICE_SUFFIX = ".practice.json"
PACKAGE_PROFILES = {"external", "coursecraft"}
FILENAME_POLICIES = {"preserve_observed", "config_id"}
CONFIG_ID_RE = re.compile(r"^config-(\d+)\.practice\.json$", re.IGNORECASE)
NUMERIC_ID_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class PracticePayload:
    path: str
    basename: str
    sha256: str
    payload_id: str | None
    parse_state: str
    parse_error: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "basename": self.basename,
            "sha256": self.sha256,
            "payload_id": self.payload_id,
            "parse_state": self.parse_state,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True)
class PracticeRelationship:
    html_path: str
    data_file: str
    iframe_id: str
    query_id: str
    activity_identity: str
    selected_path: str | None
    candidate_paths: tuple[str, ...]
    unsafe_reference: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "html_path": self.html_path,
            "data_file": self.data_file,
            "iframe_id": self.iframe_id,
            "query_id": self.query_id,
            "activity_identity": self.activity_identity,
            "selected_path": self.selected_path,
            "candidate_paths": list(self.candidate_paths),
            "unsafe_reference": self.unsafe_reference,
        }


@dataclass(frozen=True)
class CreatorPlusDiagnostic:
    severity: str
    code: str
    message: str
    html_path: str = ""
    data_file: str = ""
    payload_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "html_path": self.html_path,
            "data_file": self.data_file,
            "payload_paths": list(self.payload_paths),
        }


@dataclass
class CreatorPlusPackageReport:
    profile: str
    filename_policy: str
    payloads: list[PracticePayload] = field(default_factory=list)
    relationships: list[PracticeRelationship] = field(default_factory=list)
    diagnostics: list[CreatorPlusDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "filename_policy": self.filename_policy,
            "payloads": [row.to_dict() for row in self.payloads],
            "relationships": [row.to_dict() for row in self.relationships],
            "diagnostics": [row.to_dict() for row in self.diagnostics],
        }

    def relationship_for(
        self,
        *,
        package_root: Path,
        html_dir: Path,
        data_file: str,
    ) -> PracticeRelationship | None:
        try:
            html_parent = html_dir.resolve().relative_to(package_root.resolve()).as_posix()
        except ValueError:
            return None
        for row in self.relationships:
            if Path(row.html_path).parent.as_posix() == html_parent and row.data_file == data_file:
                return row
        return None


def _id_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _CreatorPlusIframeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.iframes: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "iframe":
            return
        values = {(name or "").casefold(): (value or "").strip() for name, value in attrs}
        data_file = values.get("data-file", "")
        src = values.get("src", "")
        if not (
            data_file.casefold().endswith(PRACTICE_SUFFIX)
            or "practices.lcs.brightspace.com" in src.casefold()
        ):
            return
        query_id = ""
        if src:
            query_id = (parse_qs(urlparse(html.unescape(src)).query).get("id") or [""])[0].strip()
        self.iframes.append(
            {
                "data_file": data_file,
                "iframe_id": values.get("id", ""),
                "query_id": query_id,
            }
        )


def _payloads(package_root: Path) -> list[PracticePayload]:
    root = package_root.resolve()
    rows: list[PracticePayload] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or not path.name.casefold().endswith(PRACTICE_SUFFIX):
            continue
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        payload_id: str | None = None
        parse_state = "parsed"
        parse_error = ""
        try:
            data = json.loads(resolved.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                parse_state = "invalid"
                parse_error = "top-level JSON value is not an object"
            else:
                payload_id = _id_text(data.get("id"))
                if payload_id is None:
                    parse_state = "invalid"
                    parse_error = "payload id is missing or invalid"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            parse_state = "invalid"
            parse_error = f"{type(exc).__name__}: {exc}"
        rows.append(
            PracticePayload(
                path=rel,
                basename=resolved.name,
                sha256=_sha256(resolved),
                payload_id=payload_id,
                parse_state=parse_state,
                parse_error=parse_error,
            )
        )
    return rows


def _local_target(ref: str) -> tuple[str, bool]:
    authored = html.unescape(unquote(ref or "")).strip().replace("\\", "/")
    target = authored.split("?", 1)[0].split("#", 1)[0]
    parsed = urlparse(target)
    if not target:
        return target, False
    if target.startswith("/") or parsed.scheme or parsed.netloc or "\x00" in target:
        return target, True
    return target, False


def _candidate_relative(base: Path, target: str, package_root: Path) -> tuple[str | None, bool]:
    try:
        candidate = (base / target).resolve()
        return candidate.relative_to(package_root.resolve()).as_posix(), False
    except (OSError, RuntimeError, ValueError):
        return None, True


def _direct_root_practice(path: str | None) -> bool:
    if not path:
        return False
    parts = Path(path).parts
    return len(parts) == 2 and parts[0].casefold() == "practice"


def _severity(code: str, profile: str) -> str:
    if code in {"CREATORPLUS_ROOT_PATH_REQUIRED", "CREATORPLUS_PAYLOAD_UNREFERENCED"}:
        return "error" if profile == "coursecraft" else "warning"
    if code == "CREATORPLUS_REDUNDANT_NESTED_COPY":
        return "warning"
    return "error"


def inspect_creatorplus_package(
    package_root: Path,
    *,
    profile: str = "external",
    filename_policy: str = "preserve_observed",
) -> CreatorPlusPackageReport:
    """Return a stable index and diagnostics for one unpacked package root."""

    if profile not in PACKAGE_PROFILES:
        raise ValueError(f"unsupported package profile: {profile}")
    if filename_policy not in FILENAME_POLICIES:
        raise ValueError(f"unsupported Creator+ filename policy: {filename_policy}")

    root = package_root.resolve()
    payloads = _payloads(root)
    by_path = {row.path: row for row in payloads}
    by_basename: dict[str, list[PracticePayload]] = {}
    for row in payloads:
        by_basename.setdefault(row.basename.casefold(), []).append(row)

    relationships: list[PracticeRelationship] = []
    for html_path in sorted(root.rglob("*")):
        if not html_path.is_file() or html_path.suffix.casefold() not in {".html", ".htm", ".xhtml"}:
            continue
        try:
            html_rel = html_path.resolve().relative_to(root).as_posix()
            raw = html_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        parser = _CreatorPlusIframeParser()
        parser.feed(raw)
        parser.close()
        for iframe in parser.iframes:
            data_file = iframe["data_file"]
            target, target_unsafe = _local_target(data_file)
            exact_paths: list[str] = []
            candidate_unsafe = target_unsafe
            if target and not target_unsafe:
                root_rel, root_unsafe = _candidate_relative(root, target, root)
                page_rel, page_unsafe = _candidate_relative(html_path.parent, target, root)
                candidate_unsafe = root_unsafe and page_unsafe
                for rel in (root_rel, page_rel):
                    if rel and rel in by_path and rel not in exact_paths:
                        exact_paths.append(rel)

            basename = Path(target).name.casefold() if target else ""
            basename_matches = [row.path for row in by_basename.get(basename, [])]
            selected: str | None = None
            for path in exact_paths:
                if _direct_root_practice(path):
                    selected = path
                    break
            if selected is None and exact_paths:
                selected = exact_paths[0]
            candidates = []
            for path in ([selected] if selected else []) + exact_paths + sorted(basename_matches):
                if path and path not in candidates:
                    candidates.append(path)
            selected_payload = by_path.get(selected or "")
            identity_id = (
                iframe["query_id"]
                or (_id_text(iframe["iframe_id"]) if NUMERIC_ID_RE.fullmatch(iframe["iframe_id"]) else None)
                or (selected_payload.payload_id if selected_payload else None)
                or (Path(target).stem if target else "unresolved")
            )
            relationships.append(
                PracticeRelationship(
                    html_path=html_rel,
                    data_file=data_file,
                    iframe_id=iframe["iframe_id"],
                    query_id=iframe["query_id"],
                    activity_identity=f"{html_rel}#creatorplus-{identity_id}",
                    selected_path=selected,
                    candidate_paths=tuple(candidates),
                    unsafe_reference=candidate_unsafe,
                )
            )

    diagnostics: list[CreatorPlusDiagnostic] = []
    diagnostic_keys: set[tuple[object, ...]] = set()

    def add(
        code: str,
        message: str,
        *,
        html_path: str = "",
        data_file: str = "",
        payload_paths: tuple[str, ...] = (),
    ) -> None:
        key = (code, html_path, data_file, payload_paths)
        if key in diagnostic_keys:
            return
        diagnostic_keys.add(key)
        diagnostics.append(
            CreatorPlusDiagnostic(
                severity=_severity(code, profile),
                code=code,
                message=message,
                html_path=html_path,
                data_file=data_file,
                payload_paths=payload_paths,
            )
        )

    for payload in payloads:
        if payload.parse_state != "parsed":
            add(
                "CREATORPLUS_PAYLOAD_INVALID",
                f"Creator+ payload {payload.path!r} is invalid: {payload.parse_error}",
                payload_paths=(payload.path,),
            )
        if filename_policy == "config_id":
            match = CONFIG_ID_RE.fullmatch(payload.basename)
            if match is None or payload.payload_id != match.group(1):
                add(
                    "CREATORPLUS_FILENAME_ID_MISMATCH",
                    f"Creator+ payload {payload.path!r} does not satisfy filename policy config_id",
                    payload_paths=(payload.path,),
                )

    related_paths: set[str] = set()
    for relation in relationships:
        related_paths.update(relation.candidate_paths)
        if relation.unsafe_reference:
            add(
                "CREATORPLUS_REFERENCE_UNSAFE",
                f"Creator+ data-file reference {relation.data_file!r} escapes or is not package-local",
                html_path=relation.html_path,
                data_file=relation.data_file,
            )
            continue
        if relation.selected_path is None:
            add(
                "CREATORPLUS_PAYLOAD_MISSING",
                f"Creator+ data-file reference {relation.data_file!r} has no resolvable payload",
                html_path=relation.html_path,
                data_file=relation.data_file,
                payload_paths=relation.candidate_paths,
            )
            continue
        if not _direct_root_practice(relation.selected_path):
            add(
                "CREATORPLUS_ROOT_PATH_REQUIRED",
                f"Creator+ reference {relation.data_file!r} selected non-canonical path {relation.selected_path!r}; expected a direct child of package-root practice/",
                html_path=relation.html_path,
                data_file=relation.data_file,
                payload_paths=(relation.selected_path,),
            )

        selected = by_path[relation.selected_path]
        competitors = [by_path[path] for path in relation.candidate_paths if path != relation.selected_path]
        if competitors:
            paths = (relation.selected_path, *(row.path for row in competitors))
            if all(row.sha256 == selected.sha256 for row in competitors):
                add(
                    "CREATORPLUS_REDUNDANT_NESTED_COPY",
                    f"Creator+ payload {relation.selected_path!r} has byte-identical competing copies",
                    html_path=relation.html_path,
                    data_file=relation.data_file,
                    payload_paths=paths,
                )
            else:
                add(
                    "CREATORPLUS_CONFLICTING_COPY",
                    f"Creator+ payload {relation.selected_path!r} has content-conflicting competing copies",
                    html_path=relation.html_path,
                    data_file=relation.data_file,
                    payload_paths=paths,
                )

        asserted_ids = []
        if NUMERIC_ID_RE.fullmatch(relation.iframe_id):
            asserted_ids.append(("iframe id", relation.iframe_id))
        if relation.query_id:
            asserted_ids.append(("iframe query id", relation.query_id))
        for label, asserted_id in asserted_ids:
            if selected.payload_id is not None and asserted_id != selected.payload_id:
                add(
                    "CREATORPLUS_WRAPPER_PAYLOAD_ID_MISMATCH",
                    f"Creator+ {label} {asserted_id!r} does not match payload id {selected.payload_id!r}",
                    html_path=relation.html_path,
                    data_file=relation.data_file,
                    payload_paths=(relation.selected_path,),
                )

    by_id: dict[str, list[PracticePayload]] = {}
    for payload in payloads:
        if payload.payload_id is not None:
            by_id.setdefault(payload.payload_id, []).append(payload)
    for payload_id, rows in sorted(by_id.items()):
        if len({row.basename.casefold() for row in rows}) > 1:
            paths = tuple(sorted(row.path for row in rows))
            add(
                "CREATORPLUS_ID_COLLISION",
                f"Creator+ payload id {payload_id!r} appears under multiple filenames",
                payload_paths=paths,
            )

    for payload in payloads:
        if payload.path not in related_paths:
            add(
                "CREATORPLUS_PAYLOAD_UNREFERENCED",
                f"Creator+ payload {payload.path!r} is not referenced by a package wrapper",
                payload_paths=(payload.path,),
            )

    diagnostics.sort(
        key=lambda row: (row.severity, row.code, row.html_path, row.data_file, row.payload_paths)
    )
    return CreatorPlusPackageReport(
        profile=profile,
        filename_policy=filename_policy,
        payloads=payloads,
        relationships=relationships,
        diagnostics=diagnostics,
    )


def format_diagnostic(diagnostic: CreatorPlusDiagnostic) -> str:
    context = []
    if diagnostic.html_path:
        context.append(f"html={diagnostic.html_path}")
    if diagnostic.data_file:
        context.append(f"data-file={diagnostic.data_file}")
    if diagnostic.payload_paths:
        context.append(f"payloads={','.join(diagnostic.payload_paths)}")
    suffix = f" ({'; '.join(context)})" if context else ""
    return f"[{diagnostic.code}] {diagnostic.message}{suffix}"
