#!/usr/bin/env python3
"""Shared helpers for the extraction/generation/validation scripts.

This module started as the home for ``local_name`` and ``clean``, which were
defined identically in seven scripts. Extended 2026-07-10 (backported from the
blueprint share bundle's consolidation): the remaining purpose-neutral helpers
that had been copy-pasted — and in one case had quietly diverged
(``html_to_text``) — live here now so there is one definition to read, test,
and maintain. Renderer-flavored helpers (``clean_text``/``clean_label``/
``xml_safe_text``/``should_divide_labeled_sections``) deliberately stay with
the blueprint renderer scripts: they serve one output surface, not the corpus.

Import from a sibling script with ``from common_xml import local_name, clean``
— because every script is invoked as ``python3 scripts/<name>.py`` (including
under pytest, which shells out via subprocess), the ``scripts/`` directory is
on ``sys.path[0]`` and the bare import resolves without any package machinery.

Note: ``build_quiz_package_from_workbook.py`` intentionally keeps its own
``clean`` — it additionally strips the ``.0`` suffix openpyxl adds to integer
cells, which is workbook-specific behavior and must not leak into XML parsing.
"""
from __future__ import annotations

import html
import re
import tempfile
import zipfile
from pathlib import Path


# --------------------------------------------------------------------------- #
# XML basics
# --------------------------------------------------------------------------- #
def local_name(tag: str) -> str:
    """Return an XML tag without its ``{namespace}`` prefix."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def clean(value: object) -> str:
    """Coerce a value to a stripped string; ``None`` becomes ``""``."""
    return "" if value is None else str(value).strip()


# --------------------------------------------------------------------------- #
# Labels and filenames
# --------------------------------------------------------------------------- #
def safe_label(name: str) -> str:
    """Reduce a free-form name to a filesystem-safe output stem."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


# --------------------------------------------------------------------------- #
# Export location
# --------------------------------------------------------------------------- #
def load_export_root(path: Path, holder: list, prefix: str = "course_export_") -> Path:
    """Return a directory for the export, unpacking a ZIP into a tempdir.

    ``holder`` keeps the TemporaryDirectory alive for the caller's lifetime.
    """
    if path.is_dir():
        return path
    if path.is_file() and zipfile.is_zipfile(path):
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        holder.append(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp.name)
        return Path(tmp.name)
    raise SystemExit(f"error: not an export directory or zip: {path}")


def find_manifests(root: Path) -> list[Path]:
    """Every ``imsmanifest.xml`` under ``root``, shallowest first.

    Real exports keep the manifest at the export root, but a ZIP re-zipped
    with a wrapping folder (or a package that nests a second manifest) is
    common enough that callers should not assume ``root/imsmanifest.xml``.

    Scope note: this tolerance is for scripts that READ exports people hand
    them. Scripts that VALIDATE generated packages (the conformance gate, the
    quiz package validator) must keep requiring the manifest at the package
    root — there, a nested manifest is a defect to report, not an input quirk.
    """
    candidate = root / "imsmanifest.xml"
    if candidate.is_file():
        return [candidate]
    return sorted(root.rglob("imsmanifest.xml"), key=lambda p: (len(p.parts), str(p)))


def find_manifest(root: Path) -> Path | None:
    """The most plausible manifest under ``root`` (shallowest), or ``None``."""
    matches = find_manifests(root)
    return matches[0] if matches else None


def resolve_export_root(root: Path) -> tuple[Path, Path | None]:
    """Return ``(effective_root, manifest_path)`` for an unpacked export.

    When the manifest sits below ``root`` (wrapped-folder ZIPs), the export's
    sibling files (``grades_d2l.xml`` etc.) sit next to the manifest — so the
    manifest's parent becomes the effective root. ``manifest_path`` is ``None``
    when no manifest exists anywhere under ``root``.
    """
    manifest = find_manifest(root)
    if manifest is None:
        return root, None
    return manifest.parent, manifest


# --------------------------------------------------------------------------- #
# HTML text flattening
# --------------------------------------------------------------------------- #
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def flatten_html_text(raw: str) -> str:
    """Strip tags and collapse whitespace without dropping script/style bodies."""
    stripped = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(stripped)).strip()


def html_to_text(raw: str) -> str:
    """Flatten HTML to reviewer-facing text; script/style payloads are dropped."""
    return flatten_html_text(_SCRIPT_STYLE.sub(" ", raw))


# --------------------------------------------------------------------------- #
# Image / link evidence
# --------------------------------------------------------------------------- #
URL_RCODE = re.compile(r"r[Cc]ode=([A-Za-z0-9._-]+)")
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT_ATTR = re.compile(r"""\balt\s*=\s*("[^"]*[^"\s][^"]*"|'[^']*[^'\s][^']*')""", re.IGNORECASE)
SRC_ATTR = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)


def images_missing_alt(raw_html: str) -> int:
    """Count ``<img>`` tags with no meaningful ``alt`` attribute."""
    count = 0
    for img in IMG_TAG.findall(raw_html):
        if not ALT_ATTR.search(img):
            count += 1
    return count


def missing_alt_image_refs(raw: str) -> list[str]:
    """``src`` references for images missing meaningful alt text."""
    refs: list[str] = []
    for img in IMG_TAG.findall(raw or ""):
        if ALT_ATTR.search(img):
            continue
        match = SRC_ATTR.search(img)
        src = ""
        if match:
            src = next((group for group in match.groups() if group), "").strip()
        refs.append(html.unescape(src) if src else "(image src not found)")
    return refs
