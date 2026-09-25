#!/usr/bin/env python3
"""Canonical step plan for the Quiz Binder synthetic journey.

This module is the single source of truth for step identity, order, door
membership, and human labels. The journey engine executes it, the receipt's
``commands[]`` mirrors it, progress events cite it, and every narrator
(terminal today, a hosted bench later) renders from it. A step that is not
in this plan does not exist; a plan change is a format-contract change.

Design rule: step ``key`` values are machine identity and match the receipt's
``commands[].step`` tokens exactly. ``label`` values are honest plain-language
descriptions safe for any consumer. Presentation registers (station names,
glyphs, warmth) belong to narrators, never to this plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


RECEIPT_FORMAT = "brightspace-quiz-bundle.synthetic-journey/1"
RECEIPT_NAME = "journey.receipt.json"
SUMMARY_NAME = "JOURNEY_SUMMARY.md"

# Draft progress-event schema id. Deliberately bundle-scoped and version 0:
# the ratified ecosystem decision (2026-07-19 productization panel) names
# coursecraft.quiz_progress/1 as the eventual same-grammar sibling of
# coursecraft.progress/1, but ratifying that name remains a separate
# Workbench/operator decision. This draft proves the grammar so that
# ratification can be evidence-based; renaming is a one-constant change.
PROGRESS_SCHEMA = "brightspace-quiz-bundle.quiz-progress/0"

# The known instance-level build-approval blocker codes. Mirrors the
# completed-run renderer's registered set; unknown codes must stay literal.
KNOWN_BLOCKER_CODES = (
    "missing_permanent_question_code",
    "question_kind_not_buildable",
    "question_not_approved_for_build",
)


class PlanError(RuntimeError):
    """Raised when the plan and an observed run disagree."""


#: The nine journey steps, in execution order.
#: key         -- receipt commands[].step token (machine identity)
#: entrypoint  -- the pinned producer script the engine invokes
#: door        -- "unbind" | "compose" (receipt door membership)
#: act         -- "unbind" | "compose" | "seal" (narrative grouping)
#: label       -- honest plain description for events and step boards
#: boundary    -- True for the step that ends at the expected honest stop
STEP_PLAN: tuple[dict[str, Any], ...] = (
    {
        "key": "unbind",
        "entrypoint": "scripts/extract_quiz_pool_review.py",
        "door": "unbind",
        "act": "unbind",
        "label": "Unbind the export into evidence",
        "boundary": False,
    },
    {
        "key": "materialize_marginalia",
        "entrypoint": "scripts/quiz_review_workbook_reingest.py",
        "door": "unbind",
        "act": "unbind",
        "label": "Read back marginalia decisions",
        "boundary": False,
    },
    {
        "key": "promote_accepted_marginalia",
        "entrypoint": "scripts/quiz_promote_revisions.py",
        "door": "unbind",
        "act": "unbind",
        "label": "Promote accepted marginalia",
        "boundary": False,
    },
    {
        "key": "render_review_station",
        "entrypoint": "scripts/quiz_binder_station.py",
        "door": "unbind",
        "act": "unbind",
        "label": "Render the review station",
        "boundary": False,
    },
    {
        "key": "check_unbound_readiness",
        "entrypoint": "scripts/check_quiz_authoring_readiness.py",
        "door": "unbind",
        "act": "unbind",
        "label": "Check rebind readiness",
        "boundary": True,
    },
    {
        "key": "check_compose_readiness",
        "entrypoint": "scripts/check_quiz_authoring_readiness.py",
        "door": "compose",
        "act": "compose",
        "label": "Check compose readiness",
        "boundary": False,
    },
    {
        "key": "rebind",
        "entrypoint": "scripts/build_quiz_package_from_workbook.py",
        "door": "compose",
        "act": "compose",
        "label": "Rebind the approved model into a package",
        "boundary": False,
    },
    {
        "key": "seal_validate",
        "entrypoint": "scripts/validate_quiz_package.py",
        "door": "compose",
        "act": "seal",
        "label": "Seal: strict package validation",
        "boundary": False,
    },
    {
        "key": "seal_local_equivalence",
        "entrypoint": "scripts/diff_packages.py",
        "door": "compose",
        "act": "seal",
        "label": "Seal: folder/ZIP equivalence",
        "boundary": False,
    },
)

STEP_KEYS: tuple[str, ...] = tuple(step["key"] for step in STEP_PLAN)
STEP_LABELS: tuple[str, ...] = tuple(step["label"] for step in STEP_PLAN)
STEP_TOTAL = len(STEP_PLAN)
BOUNDARY_STEP_KEY = next(step["key"] for step in STEP_PLAN if step["boundary"])


def step_by_key(key: str) -> dict[str, Any]:
    for step in STEP_PLAN:
        if step["key"] == key:
            return step
    raise PlanError(f"unknown journey step key: {key}")


def step_index(key: str) -> int:
    """1-based plan position of a step key."""
    for index, step in enumerate(STEP_PLAN, start=1):
        if step["key"] == key:
            return index
    raise PlanError(f"unknown journey step key: {key}")


def assert_receipt_matches_plan(receipt: dict[str, Any]) -> None:
    """Fail loudly if a receipt's commands diverge from this plan.

    Called by consumers that narrate completed runs from the plan; the
    receipt remains the durable authority, so divergence means the narrator
    must refuse rather than improvise.
    """
    commands = receipt.get("commands")
    if not isinstance(commands, list):
        raise PlanError("receipt has no commands[] to compare against the plan")
    observed = tuple(
        (row.get("step"), row.get("entrypoint"))
        for row in commands
        if isinstance(row, dict)
    )
    expected = tuple(
        (step["key"], step["entrypoint"])
        for step in STEP_PLAN
    )
    if observed != expected:
        raise PlanError(
            "receipt commands do not match the canonical step plan: "
            f"{observed!r} != {expected!r}"
        )


def find_repo_root(start: Path) -> Path:
    """Locate the bundle root by walking up to the workbench pin."""
    probe = start.resolve()
    for candidate in (probe, *probe.parents):
        if (candidate / "upstream" / "workbench_pin.json").is_file():
            return candidate
    raise PlanError(f"cannot locate the bundle root above {start}")
