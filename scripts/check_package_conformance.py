#!/usr/bin/env python3
"""Structural conformance check for generated Brightspace import packages.

Catches the class of defect that internal-consistency validation misses: a
package that is self-consistent but does not match the canonical structure
D2L's Course Package Converter expects. The flagship symptom is the
imsmanifest.xml namespace signature — D2L recognizes a package as its own by
the canonical namespace declarations and prefixes. ElementTree, left without
register_namespace calls, emits invented ns0/ns1 prefixes, and D2L then fails
with "Plugin not found for conversion". A wrong LOM namespace (imsmd_v1p2
instead of imsmd_rootv1p2p1) is the same failure mode.

This check asserts the manifest matches the signature seen in every known-good
D2L export (DSW 821, CHEM 1020, the reference bundle) and that material_types
are ones D2L actually emits. Run it as a pre-import gate for ANY generated
package, regardless of builder:

    python3 scripts/check_package_conformance.py path/to/package
    python3 scripts/check_package_conformance.py package.zip --strict

Exit code 0 = conformant, 1 = nonconformant (or warnings under --strict), 2 = error.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

from common_xml import local_name
from creatorplus_package import (
    FILENAME_POLICIES,
    PACKAGE_PROFILES,
    format_diagnostic as format_creatorplus_diagnostic,
    inspect_creatorplus_package,
)

IMSCP_NS = "http://www.imsglobal.org/xsd/imscp_v1p1"
D2L_NS = "http://desire2learn.com/xsd/d2lcp_v2p0"
IMSMD_CANONICAL = "http://www.imsglobal.org/xsd/imsmd_rootv1p2p1"
IMSMD_WRONG = "http://www.imsglobal.org/xsd/imsmd_v1p2"

# material_type values observed across canonical D2L exports (DSW 821, CHEM 1020)
# plus the quiz-package types. D2L has more than this, so an unknown type is a
# warning, not a hard error.
KNOWN_MATERIAL_TYPES = {
    "content", "contentlink", "contentmodule", "orgunitconfig",
    "d2lgrades", "d2lrubrics", "d2ldropbox", "d2ldiscussion", "d2lchecklist",
    "d2lquiz", "d2lquestionlibrary", "d2lnews", "d2lintelligentagents",
    "d2lsyllabus", "d2lcourseimage",
}

XMLNS_RE = re.compile(r'xmlns(?::([A-Za-z0-9_.\-]+))?\s*=\s*"([^"]*)"')


def find_manifest_path(root: Path) -> Path:
    if root.is_file() and root.name == "imsmanifest.xml":
        return root
    candidates = sorted(root.rglob("imsmanifest.xml"))
    if not candidates:
        raise FileNotFoundError(f"No imsmanifest.xml found under {root}")
    # Prefer the shallowest (package-root) manifest.
    return min(candidates, key=lambda p: len(p.parts))


def root_namespaces(manifest_text: str) -> dict[str, str]:
    """Map prefix ('' for default) -> uri, parsed from the opening <manifest> tag.

    Read from raw text on purpose: ElementTree normalizes prefixes away, but the
    prefix is exactly what D2L's converter is sensitive to.
    """
    match = re.search(r"<manifest\b[^>]*>", manifest_text)
    if not match:
        raise ValueError("No <manifest> element found in manifest text")
    return {prefix or "": uri for prefix, uri in XMLNS_RE.findall(match.group(0))}


def check_namespaces(ns: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    uri_to_prefix = {uri: prefix for prefix, uri in ns.items()}

    for prefix in ns:
        if re.fullmatch(r"ns\d+", prefix):
            errors.append(
                f"invented namespace prefix '{prefix}' (ElementTree default) — "
                "D2L will not recognize the package as D2L ('Plugin not found for "
                "conversion'); register canonical prefixes in the builder"
            )
    if ns.get("") != IMSCP_NS:
        errors.append(f"default xmlns is {ns.get('')!r}; expected {IMSCP_NS}")
    if D2L_NS not in uri_to_prefix:
        errors.append(f"missing the D2L namespace {D2L_NS}")
    elif uri_to_prefix[D2L_NS] != "d2l_2p0":
        errors.append(
            f"D2L namespace bound to prefix {uri_to_prefix[D2L_NS]!r}; canonical is 'd2l_2p0'"
        )
    if IMSMD_WRONG in uri_to_prefix:
        errors.append(
            f"non-canonical LOM namespace {IMSMD_WRONG}; D2L emits {IMSMD_CANONICAL}"
        )
    if IMSMD_CANONICAL in uri_to_prefix and uri_to_prefix[IMSMD_CANONICAL] != "imsmd":
        warnings.append(
            f"LOM namespace bound to prefix {uri_to_prefix[IMSMD_CANONICAL]!r}; canonical is 'imsmd'"
        )
    return errors, warnings


def check_material_types(manifest_path: Path) -> list[str]:
    warnings: list[str] = []
    root = ET.parse(manifest_path).getroot()
    seen: set[str] = set()
    for elem in root.iter():
        for key, value in elem.attrib.items():
            if local_name(key) == "material_type" and value:
                seen.add(value)
    for material_type in sorted(seen - KNOWN_MATERIAL_TYPES):
        warnings.append(
            f"material_type '{material_type}' not seen in canonical exports — "
            "verify it is a real D2L type"
        )
    return warnings


def check_package(
    path: Path,
    *,
    package_profile: str = "external",
    creatorplus_filename_policy: str = "preserve_observed",
) -> tuple[list[str], list[str], Path]:
    if path.is_file() and path.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        search_root = tmp
    else:
        search_root = path
    manifest_path = find_manifest_path(search_root)
    text = manifest_path.read_text(encoding="utf-8-sig")
    errors, warnings = check_namespaces(root_namespaces(text))
    warnings += check_material_types(manifest_path)
    creatorplus = inspect_creatorplus_package(
        manifest_path.parent,
        profile=package_profile,
        filename_policy=creatorplus_filename_policy,
    )
    for diagnostic in creatorplus.diagnostics:
        rendered = format_creatorplus_diagnostic(diagnostic)
        if diagnostic.severity == "error":
            errors.append(rendered)
        else:
            warnings.append(rendered)
    return errors, warnings, manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", type=Path, help="Package directory or .zip file")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument(
        "--package-profile",
        choices=sorted(PACKAGE_PROFILES),
        default="external",
        help="Creator+ severity profile (default: external evidence inspection)",
    )
    parser.add_argument(
        "--creatorplus-filename-policy",
        choices=sorted(FILENAME_POLICIES),
        default="preserve_observed",
        help="Preserve observed Creator+ filenames or enforce config-<id> naming",
    )
    args = parser.parse_args(argv)

    try:
        errors, warnings, manifest_path = check_package(
            args.path.expanduser().resolve(),
            package_profile=args.package_profile,
            creatorplus_filename_policy=args.creatorplus_filename_policy,
        )
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("# Package Conformance Check")
    print(f"- Manifest: {manifest_path}")
    print(f"- Errors: {len(errors)}")
    print(f"- Warnings: {len(warnings)}")
    for error in errors:
        print(f"  ERROR: {error}")
    for warning in warnings:
        print(f"  WARN: {warning}")
    if errors or (args.strict and warnings):
        print("RESULT: NONCONFORMANT")
        return 1
    print("RESULT: CONFORMANT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
