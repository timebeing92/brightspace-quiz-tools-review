#!/usr/bin/env python3
"""View model for the Quiz Binder full-screen workbench (r4 Bindery Ledger).

Every displayed fact is derived from exactly four sources: the canonical
step plan, emitted progress events, disk facts those events name, and
renderer outcomes. The view model never invents state:

- the rail tracks only steps the canonical plan names; unknown event
  kinds change nothing here (the production Narrator renders them
  literally into the record, which is the surface that must stay honest);
- the record buffer receives only lines written by the production wizard
  surfaces (Narrator, header/plan/preflight/close/failure/interruption);
  the view model itself contributes no record vocabulary;
- screen geometry lives entirely outside this module: the stdout payload
  is independent of any terminal size or capability.

Exit-code vocabulary is inherited unchanged: 0 completed/verified-read/
clean leave, 2 refused/failed/renderer-refused, 130 interrupted.

r4 adds exactly two presentation-only fields over the r3 model, both
justified by the Bindery register the frames render:

- ``ascii_mode`` — a glyph-mode flag (True selects the rc1 ASCII glyph
  substitutions for chrome). It changes chrome texture only; the record
  buffer and ``stdout_payload`` remain the wizard's characters verbatim.
- ``boundary_blocker_codes`` — the blocker codes carried by the boundary
  step's ``step_end`` event, captured so the frames can gate the warm stop
  plate to the three known codes (rc1 R2). It records what the event
  already reported; it derives no new fact and never edits the record.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _ensure_scripts_on_path() -> None:
    """Make the production modules importable from either home.

    In proposed production this file lives in ``scripts/`` beside them
    (insertion is then a no-op in effect); in the candidate it lives
    under the design-proposal tree and walks up to the bundle root.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "upstream" / "workbench_pin.json").is_file():
            scripts = str(candidate / "scripts")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            return


_ensure_scripts_on_path()

from quiz_binder_journey_plan import (  # noqa: E402
    BOUNDARY_STEP_KEY,
    STEP_PLAN,
    STEP_TOTAL,
)


# Screens (one active at a time; overlays stack on top).
HOME = "home"
PATH_RUN = "path_run"
PATH_READ = "path_read"
TERMS = "terms"
RUNNING = "running"
CLOSED = "closed"
FAILED = "failed"
REFUSED = "refused"
READ = "read"
READ_REFUSED = "read_refused"

# Overlays (rendered above the active screen; Esc returns).
OVERLAY_HELP = "help"
OVERLAY_PLAN = "plan"
OVERLAY_OUTPUTS = "outputs"

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"

# Section headings recognized for pager navigation. These are the
# register's own headings (renderer + wizard); nothing here invents one.
SECTION_PREFIXES = (
    "QUIZ BINDER",
    "RUN PLAN",
    "TERMS OF THIS RUN",
    "RUN RECORD",
    "UNBIND",
    "STOP:",
    "COMPOSE",
    "SEAL",
    "PROOF LADDER",
    "RUN SUMMARY",
    "FAIL:",
)


class StepView:
    """Rail state for one canonical plan step."""

    def __init__(self, spec: dict[str, Any], index: int) -> None:
        self.key: str = spec["key"]
        self.index = index
        self.label: str = spec["label"]
        self.door: str = spec["door"]
        self.boundary: bool = bool(spec["boundary"])
        self.status = STEP_PENDING

    def status_words(self) -> str:
        """The rail's status vocabulary. Words carry the state; the
        scheduled stop is pre-declared while pending and keeps its
        `[boundary]` tag once recorded, mirroring the record's own tag."""
        if self.status == STEP_PENDING:
            return "scheduled stop" if self.boundary else "pending"
        if self.status == STEP_RUNNING:
            return "running"
        if self.status == STEP_COMPLETED:
            return "completed [boundary]" if self.boundary else "completed"
        return "failed"


class ViewModel:
    def __init__(
        self,
        *,
        path_prefill: str = "output/tui-journey",
        runs_root_display: str | None = None,
        ascii_mode: bool = False,
    ) -> None:
        self.screen = HOME
        self.overlay: str | None = None
        self.overlay_scroll = 0

        # r4 glyph-mode flag: chrome texture only (rc1 ASCII substitutions
        # when True). Never touches the record buffer or stdout payload.
        self.ascii_mode = ascii_mode

        self.steps = [
            StepView(spec, index) for index, spec in enumerate(STEP_PLAN, start=1)
        ]
        self.total = STEP_TOTAL

        # The stdout record (earned lines only, production vocabulary).
        self.record: list[str] = []
        # Diagnostics replayed to stderr after the alternate screen closes.
        self.diagnostics: list[str] = []
        # Transient liveness (the Narrator's stderr line); never recorded.
        self.liveness: str | None = None

        self.outcome: str | None = None
        self.render_verified = False
        self.exit_code = 0

        self.run_dir: Path | None = None
        self.run_dir_display: str | None = None
        self.events_file_written = False

        self.path_field = path_prefill
        self.path_note: str | None = None
        self.terms_note: str | None = None

        self.read_text = ""
        self.read_lines: list[str] = []

        self.scroll = 0
        self.follow_tail = True

        self.runs_root_display = runs_root_display
        self.shelf_rows: list[str] = []
        self.shelf_note: str | None = None
        self.shelf_selected = 0
        self.shelf_scroll = 0

        # Facts collected from events for the outputs overlay. Every value
        # is a run-relative path carried by an emitted event.
        self.facts: dict[str, str] = {}
        self.run_end_event: dict[str, Any] | None = None

        # r4 stop-plate gate (rc1 R2): the boundary step's own reported
        # blocker codes, captured verbatim from its step_end event so the
        # frames can decide whether the warm stop essay is licensed (only
        # for exactly the three known codes) or the terse register applies.
        self.boundary_blocker_codes: tuple[str, ...] | None = None

    # -- event folding ----------------------------------------------------

    def on_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind == "step_start":
            step = self._step(event.get("step"))
            if step is not None:
                step.status = STEP_RUNNING
        elif kind == "step_end":
            step = self._step(event.get("step"))
            if step is not None:
                step.status = (
                    STEP_COMPLETED if event.get("status") == "ok" else STEP_FAILED
                )
            if (
                event.get("step") == BOUNDARY_STEP_KEY
                and event.get("status") == "ok"
            ):
                codes = event.get("blocker_codes")
                if isinstance(codes, list):
                    self.boundary_blocker_codes = tuple(
                        str(code) for code in codes
                    )
            self.liveness = None
            self._collect_facts(event)
        elif kind == "run_end":
            self.run_end_event = event
            self.liveness = None
        # run_start, journey_note, and unknown kinds change no rail state;
        # the record narrator is their honest renderer.

    def _step(self, key: Any) -> StepView | None:
        for step in self.steps:
            if step.key == key:
                return step
        return None

    def _collect_facts(self, event: dict[str, Any]) -> None:
        if event.get("status") != "ok":
            return
        step = event.get("step")
        for field in ("report", "record", "package_zip", "settings_receipt",
                      "validation_note"):
            value = event.get(field)
            if isinstance(value, str) and value:
                self.facts[f"{step}.{field}"] = value

    # -- record / diagnostics sinks ---------------------------------------

    def append_record(self, line: str) -> None:
        self.record.append(line)

    def set_liveness(self, line: str) -> None:
        self.liveness = line.strip()

    def add_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)

    # -- derived state -----------------------------------------------------

    def completed_count(self) -> int:
        return sum(1 for step in self.steps if step.status == STEP_COMPLETED)

    def running_step(self) -> StepView | None:
        for step in self.steps:
            if step.status == STEP_RUNNING:
                return step
        return None

    def boundary_recorded(self) -> bool:
        return any(
            step.boundary and step.status == STEP_COMPLETED for step in self.steps
        )

    def active_lines(self) -> list[str]:
        """The lines the main pane shows for the current screen."""
        if self.screen in (READ, READ_REFUSED):
            return self.read_lines
        return self.record

    def sections(self, lines: list[str]) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            if line.startswith(SECTION_PREFIXES) and line.strip():
                found.append((index, line.strip()))
        return found

    def set_read_document(self, text: str) -> None:
        self.read_text = text
        self.read_lines = text.splitlines()
        self.scroll = 0
        self.follow_tail = False

    # -- stdout payload ----------------------------------------------------

    def stdout_payload(self) -> str:
        """What stdout receives once, after the alternate screen closes.

        A verified read emits the renderer's text byte-for-byte; a run
        session emits the record lines; a session where nothing ran emits
        nothing. Screen geometry never influences this value.
        """
        if self.outcome == "read_verified":
            return self.read_text
        if self.record:
            return "\n".join(self.record) + "\n"
        return ""
