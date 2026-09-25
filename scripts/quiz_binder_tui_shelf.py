#!/usr/bin/env python3
"""Bounded completed-run discovery for the workbench start screen.

Scan law (02 §T14): no default scanning at all. Discovery happens only
when the operator names a root with ``--runs-root``, looks exactly one
level deep (the root's immediate children), skips symlinks, and checks
nothing but the presence of ``journey.receipt.json``. Listing a run
claims only "receipt present"; every verification claim belongs to the
renderer at open time. Authored content is never read.
"""

from __future__ import annotations

from pathlib import Path

from quiz_binder_tui_view import _ensure_scripts_on_path

_ensure_scripts_on_path()

from quiz_binder_journey_plan import RECEIPT_NAME  # noqa: E402

SHELF_LIMIT = 50


def discover_runs(root: Path) -> tuple[list[str], str | None]:
    """Immediate children of ``root`` that carry a receipt file.

    Returns (sorted child names, note). The note reports an unusable
    root, an empty result, or an explicit cap - silence never stands in
    for coverage.
    """
    if not root.is_dir():
        return [], "runs root not found or not a folder"
    names: list[str] = []
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        return [], f"runs root could not be listed: {exc.__class__.__name__}"
    for child in children:
        if child.is_symlink():
            continue
        if not child.is_dir():
            continue
        if (child / RECEIPT_NAME).is_file():
            names.append(child.name)
    if not names:
        return [], (
            "no completed runs found (looked one level deep; symlinks "
            "skipped)"
        )
    if len(names) > SHELF_LIMIT:
        hidden = len(names) - SHELF_LIMIT
        return names[:SHELF_LIMIT], (
            f"showing the first {SHELF_LIMIT} of {len(names)} runs; "
            f"{hidden} not shown"
        )
    return names, None
