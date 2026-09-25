#!/usr/bin/env python3
"""Quiz Binder terminal wizard — first slice (r2).

The live surface is the run record being written. stdout carries only
earned postings in the completed-run renderer's vocabulary, so a piped
transcript is deterministic and token-compatible with the verified log;
stderr carries liveness (step openings) and prompts. The two actions are
the two that exist: run the synthetic proof, and read a completed run
through the real renderer.

Truth rules this surface enforces:

- a posting appears only after the engine reports the step completed and
  its artifacts hashed (live lines say "SHA-256 recorded"; only the
  renderer's re-verification may say "verified");
- the boundary is recorded as completed work at success volume, with the
  readiness record's Message/Action text verbatim;
- unknown blocker codes stay literal and receive the registered fallback,
  never invented prose;
- interruption closes with the five residue facts and forewarns that the
  renderer will refuse the incomplete folder — that refusal is correct;
- a seal failure brands the on-disk ZIP unvalidated and forbids import;
- registered capability evidence never appears as run proof;
- the events sidecar is labeled testimony; the receipt is the only proof.

Exit codes: 0 completed · 2 refused or failed · 130 interrupted.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import textwrap
from typing import Any, IO

from quiz_binder_journey_plan import (
    KNOWN_BLOCKER_CODES,
    PlanError,
    RECEIPT_FORMAT,
    RECEIPT_NAME,
    STEP_PLAN,
    STEP_TOTAL,
    SUMMARY_NAME,
    find_repo_root,
    step_index,
)
from quiz_binder_progress import EventEmissionError, sha256_file
import run_synthetic_journey as journey


REPO_ROOT = find_repo_root(Path(__file__).resolve().parent)
EVENTS_NAME = "journey.events.ndjson"
WIDTH = 78


def _relative_display(path: Path) -> str:
    """Show a path relative to the invocation folder when that is shorter,
    so the printed command is runnable from where the operator sits."""
    import os

    try:
        rel = os.path.relpath(path, Path.cwd())
    except ValueError:
        return str(path)
    return rel if len(rel) < len(str(path)) else str(path)


def _load_renderer():
    """Import the real completed-run renderer as a module (thin adapter)."""
    path = REPO_ROOT / "scripts" / "render_quiz_binder_log.py"
    spec = importlib.util.spec_from_file_location("render_quiz_binder_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def wrap(text: str, *, indent: str = "", first: str | None = None) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=first if first is not None else indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def field(label: str, value: str) -> list[str]:
    """Renderer-style wrapped Message/Action field."""
    return textwrap.wrap(
        value,
        width=WIDTH,
        initial_indent=f"   {label}: ",
        subsequent_indent="     ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def path_block(label: str, path: str, *, note: str = "") -> list[str]:
    return [f"{label}:", f"  {path}{note}"]


class Narrator:
    """Consumes engine events and writes the live run record."""

    def __init__(self, out: IO[str], err: IO[str], run_dir: Path) -> None:
        self.out = out
        self.err = err
        self.run_dir = run_dir
        self.completed: list[str] = []
        self.artifact_count = 0
        self.package_zip: str | None = None
        self.applied_change_count = 0
        self.seal_break_count: int | None = None
        self.last_opened: str | None = None
        self.run_end_event: dict[str, Any] | None = None
        self._record_opened = False

    def post(self, lines: list[str]) -> None:
        for line in lines:
            self.out.write(line + "\n")
        self.out.flush()

    def status(self, line: str) -> None:
        self.err.write(line + "\n")
        self.err.flush()

    # -- event dispatch ---------------------------------------------------

    def __call__(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "run_start":
            pass  # the record opens with its first earned posting
        elif kind == "step_start":
            if not self._record_opened:
                self.post(["", "RUN RECORD (live)"])
                self._record_opened = True
            self.last_opened = event.get("step")
            self.status(
                f"  ... entry {event['index']} of {event['total']}: "
                f"{event['step']} - running"
            )
        elif kind == "step_end":
            self.on_step_end(event)
        elif kind == "journey_note":
            self.on_note(event)
        elif kind == "run_end":
            self.run_end_event = event
        elif isinstance(kind, str):
            # Unknown event kinds stay literal and receive no invented prose.
            self.post([f"event: {kind} (unrecognized; recorded without narration)"])

    def on_note(self, event: dict[str, Any]) -> None:
        if event.get("note") == "synthetic_marginalia_simulation":
            self.artifact_count += len(event.get("artifacts", []))
            self.post(
                wrap(
                    "note: synthetic marginalia simulation - no human reviewed "
                    "this decision; the proof simulates one (baseline and "
                    "edited workbooks recorded)",
                    indent="  ",
                    first="",
                )
            )
        else:
            note = event.get("note", "unnamed")
            self.post([f"note: {note} (unrecognized; recorded without narration)"])

    def on_step_end(self, event: dict[str, Any]) -> None:
        step = event["step"]
        if event.get("status") != "ok":
            return  # failure close is rendered by the caller
        self.completed.append(step)
        self.artifact_count += len(event.get("artifacts", []))
        boundary = " [boundary]" if event.get("boundary") else ""
        self.post(
            [
                f"entry {event['index']} of {event['total']}: {step} - "
                f"completed (exit {event['exit_code']}){boundary}"
            ]
        )
        detail = getattr(self, f"detail_{step}", None)
        if detail:
            self.post(detail(event))

    # -- per-step detail lines (facts come from the event, nowhere else) --

    def detail_unbind(self, event: dict[str, Any]) -> list[str]:
        lines = [f"  {event['question_count']} questions unbound into evidence"]
        for artifact in event.get("artifacts", []):
            lines += ["  evidence:", f"    {artifact['path']} (SHA-256 recorded)"]
        return lines

    def detail_materialize_marginalia(self, event: dict[str, Any]) -> list[str]:
        count = event.get("accepted_change_count")
        noun = "change" if count == 1 else "changes"
        return [f"  {count} {noun} accepted"]

    def detail_promote_accepted_marginalia(self, event: dict[str, Any]) -> list[str]:
        count = event.get("applied_change_count")
        self.applied_change_count = count if isinstance(count, int) else 0
        noun = "revision" if count == 1 else "revisions"
        return [f"  {count} accepted {noun} promoted; source evidence unchanged"]

    def detail_render_review_station(self, event: dict[str, Any]) -> list[str]:
        return ["  review station rendered for browsing"]

    def detail_check_unbound_readiness(self, event: dict[str, Any]) -> list[str]:
        lines = [""]
        lines.append("STOP: not ready to rebind. This stop is expected behavior.")
        lines.append(f"readiness: {event.get('readiness', 'unknown')}")
        lines.append("Extracted questions are evidence, not build-approved records.")
        lines += path_block("report", event["report"])
        lines += path_block("record", event["record"], note=" (SHA-256 recorded)")
        codes = list(event.get("blocker_codes", []))
        lines.append(f"blockers ({len(codes)}):")
        pairs = self._issue_pairs(event)
        for number, code in enumerate(codes, start=1):
            lines.append(f"{number}. {code}")
            known = pairs.get(code, set())
            if code in KNOWN_BLOCKER_CODES and len(known) == 1:
                message, remediation = next(iter(known))
                lines += field("Message", message)
                lines += field("Action", remediation)
            else:
                lines += field(
                    "Message",
                    "No registered explanation is available in this narrator.",
                )
                lines += field(
                    "Action",
                    f"Open {event['report']} for this code's recorded message "
                    "and remediation.",
                )
        applied = self.applied_change_count
        noun = "decision" if applied == 1 else "decisions"
        lines += wrap(
            "Nothing was lost by stopping here. Every artifact recorded so far "
            "remains on disk, its SHA-256 recorded; the promoted copy holds "
            f"{applied} accepted {noun}."
        )
        lines += wrap(
            "Route evidence is not instance evidence: a verified build route "
            "does not make an extracted question build-approved."
        )
        lines += wrap(
            "The proof's fixed sequence continues through the compose lane."
        )
        lines.append("")
        return lines

    def _issue_pairs(self, event: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
        """Verbatim pairs from the event-hash-verified readiness record."""
        grouped: dict[str, set[tuple[str, str]]] = {}
        record_relative = event.get("record")
        if not isinstance(record_relative, str):
            return grouped
        matches = [
            row
            for row in event.get("artifacts", [])
            if isinstance(row, dict) and row.get("path") == record_relative
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("sha256"), str):
            return grouped
        try:
            record_path = (self.run_dir / record_relative).resolve()
            record_path.relative_to(self.run_dir.resolve())
            if sha256_file(record_path) != matches[0]["sha256"]:
                return grouped
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return grouped
        except ValueError:
            return grouped
        for issue in payload.get("issues", []):
            if not isinstance(issue, dict) or issue.get("severity") != "error":
                continue
            code = issue.get("code")
            message = issue.get("message")
            remediation = issue.get("remediation")
            if (
                isinstance(code, str)
                and isinstance(message, str)
                and isinstance(remediation, str)
            ):
                grouped.setdefault(code, set()).add((message, remediation))
        return grouped

    def detail_check_compose_readiness(self, event: dict[str, Any]) -> list[str]:
        lines = [
            "  authoring readiness: {} - {} questions, {} draw{}, {} asset{}".format(
                event.get("readiness", "unknown"),
                event.get("selected_question_count"),
                event.get("reachable_draw_count"),
                "" if event.get("reachable_draw_count") == 1 else "s",
                event.get("selected_asset_count"),
                "" if event.get("selected_asset_count") == 1 else "s",
            )
        ]
        lines += ["  report:", f"    {event['report']}"]
        lines += ["  record:", f"    {event['record']} (SHA-256 recorded)"]
        return lines

    def detail_rebind(self, event: dict[str, Any]) -> list[str]:
        self.package_zip = event.get("package_zip")
        return [
            "  package:",
            f"    {event['package_zip']} (SHA-256 recorded)",
            "  settings receipt and run receipt written beside the package",
        ]

    def detail_seal_validate(self, event: dict[str, Any]) -> list[str]:
        return [
            "  strict validation: 0 errors",
            "  validation note:",
            f"    {event['validation_note']}",
        ]

    def detail_seal_local_equivalence(self, event: dict[str, Any]) -> list[str]:
        breaks = event.get("local_equivalence_break_count")
        self.seal_break_count = breaks
        return [
            f"  folder and ZIP compared structurally: {breaks} breaks",
            "  local-equivalence report:",
            f"    {event['report']}",
            "  (folder-vs-ZIP comparison, local only - no Brightspace contact)",
        ]


# -- static surfaces ------------------------------------------------------


def header_lines() -> list[str]:
    return [
        "QUIZ BINDER - synthetic proof run",
        "=================================",
        "mode: live run record; synthetic proof - specimen fixtures, both",
        "lanes, one fixed sequence",
        f"format: {RECEIPT_FORMAT}",
    ]


def plan_lines() -> list[str]:
    lines = [
        "",
        "RUN PLAN (9 entries, 2 lanes; lanes use separate fixtures and share",
        "no data)",
    ]
    for door in ("unbind", "compose"):
        lines.append(f"{door} lane")
        for step in STEP_PLAN:
            if step["door"] != door:
                continue
            index = step_index(step["key"])
            marker = " [scheduled stop]" if step["boundary"] else ""
            lines.append(f"  {index}. {step['key']} - {step['label']}{marker}")
    lines += wrap(
        "The stop at entry 5 is designed: extracted evidence is not "
        "automatically build-approved. It will be recorded as completed "
        "work, not failure."
    )
    return lines


def preflight_lines(run_dir_display: str) -> list[str]:
    lines = ["", "TERMS OF THIS RUN"]
    lines += wrap(
        "reads: synthetic fixtures inside this repository - read, never "
        "modified"
    )
    lines += ["writes:", f"  {run_dir_display}", "  run evidence goes nowhere else"]
    lines.append("network: none. Brightspace operations: none. Telemetry: none.")
    lines += wrap(
        "receipt: journey.receipt.json records counts, states, paths, and "
        "SHA-256 values. It never records question or answer text."
    )
    lines += wrap(
        "keeps: everything stays in the working folder until you remove it"
    )
    lines += wrap(
        "honesty: this run's proof ceiling is Validated locally. No "
        "Brightspace operation will run."
    )
    return lines


LADDER_LINES = [
    "[x] Drafted - package and run receipt exist",
    "[x] Validated locally - package validation and folder/ZIP equivalence passed",
    "[ ] Import verified - not recorded for this run",
    "[ ] Round-trip verified - Brightspace round trip: not_performed",
    "run proof: Validated locally - earned by this run's receipts alone",
]

CAPABILITY_LINES = [
    "Registered capability evidence describes the tool, not this package; it does",
    "not raise this run's proof rung.",
]


def close_lines(
    receipt: dict[str, Any],
    run_dir_display: str,
    *,
    events_file_written: bool,
    render_verified: bool,
) -> list[str]:
    if not render_verified:
        return [
            "",
            "FAIL: the completed-run renderer refused this run record.",
            f"receipt: {RECEIPT_NAME} exists but did not earn a verified close",
            "proof ladder: withheld - do not treat the package as sealed or import it",
            "RUN RECORD NOT CLOSED - inspect the renderer diagnostic on stderr",
        ]
    lines = ["", "RUN RECORD COMPLETE - receipt written"]
    lines.append(f"receipt: {RECEIPT_NAME}")
    lines.append(f"summary: {SUMMARY_NAME}")
    if events_file_written:
        lines.append(f"events: {EVENTS_NAME} (testimony; not receipt-verified)")
    lines.append("")
    lines.append(f"PROOF LADDER: {receipt['doors']['compose']['package_zip']}")
    lines += LADDER_LINES
    lines += CAPABILITY_LINES
    lines.append("")
    entry_count = len(receipt.get("commands", []))
    artifact_count = len(receipt.get("artifacts", []))
    lines += wrap(
        "close verified: the completed-run renderer re-verified all "
        f"{artifact_count} artifact hashes from the receipt and raised "
        "no refusal."
    )
    renderer_display = shlex.quote(
        _relative_display(REPO_ROOT / "scripts" / "render_quiz_binder_log.py")
    )
    python_display = shlex.quote(_relative_display(Path(sys.executable)))
    run_display = shlex.quote(run_dir_display)
    lines.append("verify again any time, from this folder:")
    lines.append(f"  {python_display} {renderer_display} \\")
    lines.append(f"    --run-dir {run_display}")
    lines += wrap(
        "The receipt records counts, states, paths, and SHA-256 values. It "
        "never records question or answer text."
    )
    lines.append(
        f"RUN RECORD CLOSED - {entry_count} entries, {artifact_count} "
        f"artifacts, {receipt.get('network_operations', 0)} network operations"
    )
    return lines


def interruption_lines(
    exc: journey.JourneyInterrupted,
    run_dir: Path,
    *,
    events_file_written: bool,
    posted: list[str] | None = None,
) -> list[str]:
    # The record's own postings are the kept-truth: a step whose completion
    # the narrator posted is kept even when the interrupt landed before the
    # engine finished its bookkeeping for that step.
    kept_steps = list(posted) if posted is not None else list(exc.completed)
    if exc.step_key and exc.step_key in kept_steps:
        where = f"just after {exc.step_key} completed"
    elif exc.step_key:
        where = f"during {exc.step_key}"
    else:
        where = "in a gap between steps"
    lines = ["", f"STOP: interrupted {where} - that is fine."]
    if kept_steps:
        entries = ", ".join(kept_steps)
        kept = f"kept: completed entries ({entries}); their files remain on disk"
        if events_file_written:
            kept += " with SHA-256 values recorded in the event stream"
        lines += wrap(kept)
    else:
        lines += wrap("kept: no entry had completed yet")
    lines += wrap(
        "not done: everything after the interruption - no completion was "
        "recorded for it"
    )
    zip_hazard = (
        exc.step_key in ("rebind", "seal_validate", "seal_local_equivalence")
        or "rebind" in kept_steps
    )
    untrusted = (
        "untrusted: any partial output from the interrupted step - an "
        "interrupted or unsealed rebind can leave a complete-looking package "
        "folder and ZIP; do not import it"
        if zip_hazard
        else "untrusted: any partial output from the interrupted step; do not "
        "treat it as completed work"
    )
    lines += wrap(untrusted)
    receipt_on_disk = (run_dir / RECEIPT_NAME).is_file()
    if receipt_on_disk:
        lines += wrap(
            "receipt: a journey.receipt.json exists in this folder - the run "
            "may have finished its record just before the interruption, or "
            "the file may be incomplete. The renderer is the arbiter: if it "
            "verifies this folder, trust it; if it refuses, treat the folder "
            "as incomplete."
        )
    else:
        lines += wrap(
            "receipt: journey.receipt.json is written only when a run "
            "completes; this folder has no receipt"
        )
    lines += wrap(
        "next: run again with a fresh working folder; finished evidence in "
        "this one stays exactly where it is"
    )
    if events_file_written:
        lines += wrap(
            f"events: {EVENTS_NAME} records what completed (testimony; not "
            "receipt-verified)"
        )
    if not receipt_on_disk:
        lines += wrap(
            "note: the completed-run renderer will refuse this folder as "
            "incomplete; that refusal is correct."
        )
    return lines


def failure_lines(narrator: Narrator, failed_step: str | None) -> list[str]:
    if failed_step is None:
        step = "journey (internal check)"
        door = "journey"
    else:
        step = failed_step
        door = "unknown"
        for spec in STEP_PLAN:
            if spec["key"] == failed_step:
                door = spec["door"]
                break
    lines = ["", f"FAIL: {step} refused - no completion was recorded for this step"]
    lines.append(f"lane: {door} ({step})")
    lines += wrap(
        "cause: the diagnostic is the ERROR line on stderr; the step's "
        "report, when it writes one, is in the working folder"
    )
    if step in ("seal_validate", "seal_local_equivalence") and narrator.package_zip:
        lines += wrap(
            f"recovery: the package folder and ZIP ({narrator.package_zip}) "
            "from rebind remain on disk and did NOT pass the seal - do not "
            "import them. Fix the flagged items and re-run into a fresh "
            "folder."
        )
    else:
        lines += wrap(
            "recovery: completed entries stay on disk; fix the reported "
            "cause and re-run into a fresh working folder."
        )
    return lines


# -- actions --------------------------------------------------------------


def action_read(run_dir: Path, out: IO[str], err: IO[str]) -> int:
    try:
        renderer = _load_renderer()
        rendered = renderer.render_completed_run(run_dir)
    except (OSError, ValueError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    except renderer.LogRenderError as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    out.write(rendered)
    return 0


def action_run(
    run_dir: Path,
    run_dir_display: str,
    out: IO[str],
    err: IO[str],
    *,
    assume_yes: bool,
    write_events_file: bool,
    argv_transform: journey.ArgvTransform | None = None,
) -> int:
    for line in header_lines() + plan_lines() + preflight_lines(run_dir_display):
        out.write(line + "\n")
    out.flush()

    interactive = sys.stdin.isatty() and out.isatty() and err.isatty()
    if not assume_yes:
        if not interactive:
            err.write(
                "ERROR: refusing to run without confirmation on a non-interactive "
                "stream; pass --yes to run the synthetic proof\n"
            )
            return 2
        err.write(f"\nEnter runs the proof into {run_dir_display}; q cancels: ")
        err.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer in ("q", "quit", "n", "no"):
            err.write("cancelled; nothing was written\n")
            return 0

    narrator = Narrator(out, err, run_dir)
    events_file = run_dir / EVENTS_NAME if write_events_file else None
    events_file_written = False
    try:
        receipt, _ = journey.run_journey(
            run_dir,
            event_sinks=(narrator,),
            events_file=events_file,
            argv_transform=argv_transform,
        )
        events_file_written = events_file is not None and events_file.is_file()
    except journey.JourneyRefused as exc:
        err.write(f"ERROR: {exc}\n")
        err.write(
            "The folder was preserved untouched. Choose a fresh working "
            "folder; a previous run's evidence stays exactly where it is.\n"
        )
        return 2
    except journey.JourneyInterrupted as exc:
        events_file_written = events_file is not None and events_file.is_file()
        for line in interruption_lines(
            exc,
            run_dir,
            events_file_written=events_file_written,
            posted=narrator.completed,
        ):
            out.write(line + "\n")
        out.flush()
        return 130
    except journey.JourneyError as exc:
        # The diagnostic must actually reach the operator: the engine folded
        # the failing step's stderr into the exception message.
        err.write(f"ERROR: {exc}\n")
        err.flush()
        failed = None
        if narrator.run_end_event is not None:
            failed = narrator.run_end_event.get("failed_step")
        for line in failure_lines(narrator, failed):
            out.write(line + "\n")
        out.flush()
        return 2
    except (OSError, ValueError, EventEmissionError, PlanError) as exc:
        err.write(f"ERROR: {exc}\n")
        err.flush()
        failed = None
        if narrator.run_end_event is not None:
            failed = narrator.run_end_event.get("failed_step")
        for line in failure_lines(narrator, failed):
            out.write(line + "\n")
        out.flush()
        return 2

    render_verified = False
    try:
        renderer = _load_renderer()
        renderer.render_completed_run(run_dir)
        render_verified = True
    except (OSError, ValueError) as exc:
        err.write(f"WARNING: completed-run renderer refused this run: {exc}\n")
    except renderer.LogRenderError as exc:
        err.write(f"WARNING: completed-run renderer refused this run: {exc}\n")

    for line in close_lines(
        receipt,
        run_dir_display,
        events_file_written=events_file_written,
        render_verified=render_verified,
    ):
        out.write(line + "\n")
    out.flush()
    return 0 if render_verified else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Working folder for a new synthetic proof run (must be new or empty).",
    )
    parser.add_argument(
        "--read",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Read a completed run through the verified renderer instead of running.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the run plan and terms, then exit without running.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run without the interactive confirmation (required when piped).",
    )
    parser.add_argument(
        "--no-events-file",
        action="store_true",
        help="Do not write the journey.events.ndjson sidecar into the run folder.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    out, err = sys.stdout, sys.stderr

    try:
        if args.read is not None:
            return action_read(args.read.expanduser().resolve(), out, err)

        if args.plan:
            for line in header_lines() + plan_lines() + preflight_lines(
                str(args.output_dir)
                if args.output_dir
                else "<your empty working folder>"
            ):
                out.write(line + "\n")
            return 0

        if args.output_dir is None:
            err.write(
                "ERROR: choose an action: --output-dir DIR to run the synthetic "
                "proof, --read RUN_DIR to read a completed run, or --plan\n"
            )
            return 2

        return action_run(
            args.output_dir.expanduser().resolve(),
            str(args.output_dir),
            out,
            err,
            assume_yes=args.yes,
            write_events_file=not args.no_events_file,
        )
    except KeyboardInterrupt:
        # Interrupts inside the engine surface as JourneyInterrupted and are
        # handled in action_run; this catches the remaining windows (the
        # confirmation prompt, pre-run preparation, the close) so no path
        # ever ends in a traceback.
        err.write(
            "\nERROR: interrupted. If a run had started, its folder holds "
            "what completed; the completed-run renderer is the arbiter.\n"
        )
        return 130
    except (OSError, ValueError, EventEmissionError, PlanError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
