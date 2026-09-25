#!/usr/bin/env python3
"""Session engine for the Quiz Binder full-screen workbench (r3 candidate).

The production engine remains the only executor and the production wizard
surfaces remain the only record vocabulary. This module contributes
control flow, not language: it mirrors the wizard's `action_run` exactly
(same exception classes, same surfaces, same exit codes) while writing
into the view model's record buffer instead of a terminal, and it reads
completed runs through the real renderer module.

The TUI never raises a run above what these calls earn: `close_lines`
prints its verified close only after `render_completed_run` has re-hashed
the receipt's artifacts in-process, and a renderer refusal reaches the
record as the wizard's own NOT CLOSED surface with the ladder withheld.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from quiz_binder_tui_view import ViewModel, _ensure_scripts_on_path

_ensure_scripts_on_path()

from quiz_binder_journey_plan import PlanError  # noqa: E402
from quiz_binder_progress import EventEmissionError  # noqa: E402
import quiz_binder_wizard as wizard  # noqa: E402
import render_quiz_binder_log as renderer  # noqa: E402
import run_synthetic_journey as journey  # noqa: E402


class _LineSink:
    """Adapts the Narrator's ``IO[str]`` contract to a per-line callback.

    The Narrator always writes whole lines (``line + "\\n"``); a trailing
    partial line would indicate a torn write and is delivered on flush of
    the session close rather than silently dropped.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._pending = ""

    def write(self, text: str) -> None:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._callback(line)

    def flush(self) -> None:  # pragma: no cover - Narrator flushes lines
        pass

    def close_pending(self) -> None:
        if self._pending:
            self._callback(self._pending)
            self._pending = ""


def run_session(
    view: ViewModel,
    run_dir: Path,
    run_dir_display: str,
    *,
    write_events_file: bool,
    redraw: Callable[[], None],
    argv_transform: journey.ArgvTransform | None = None,
) -> int:
    """Execute the synthetic proof exactly as the wizard does.

    Returns the session exit code (0 completed+verified, 2 refused/
    failed/renderer-refused, 130 interrupted) and leaves the complete
    stdout record in ``view.record``.
    """
    view.diagnostics.clear()
    view.run_dir = run_dir
    view.run_dir_display = run_dir_display

    for line in (
        wizard.header_lines()
        + wizard.plan_lines()
        + wizard.preflight_lines(run_dir_display)
    ):
        view.append_record(line)

    record_sink = _LineSink(view.append_record)
    liveness_sink = _LineSink(view.set_liveness)
    narrator = wizard.Narrator(record_sink, liveness_sink, run_dir)
    events_file = run_dir / wizard.EVENTS_NAME if write_events_file else None

    def sink(event: dict) -> None:
        narrator(event)
        view.on_event(event)
        redraw()

    try:
        try:
            receipt, _ = journey.run_journey(
                run_dir,
                event_sinks=(sink,),
                events_file=events_file,
                argv_transform=argv_transform,
            )
            view.events_file_written = (
                events_file is not None and events_file.is_file()
            )
        except journey.JourneyRefused as exc:
            view.add_diagnostic(f"ERROR: {exc}")
            view.add_diagnostic(
                "The folder was preserved untouched. Choose a fresh working "
                "folder; a previous run's evidence stays exactly where it is."
            )
            view.outcome = "refused"
            view.exit_code = 2
            return 2
        except journey.JourneyInterrupted as exc:
            view.events_file_written = (
                events_file is not None and events_file.is_file()
            )
            for line in wizard.interruption_lines(
                exc,
                run_dir,
                events_file_written=view.events_file_written,
                posted=narrator.completed,
            ):
                view.append_record(line)
            view.outcome = "interrupted"
            view.exit_code = 130
            return 130
        except journey.JourneyError as exc:
            view.add_diagnostic(f"ERROR: {exc}")
            failed = None
            if narrator.run_end_event is not None:
                failed = narrator.run_end_event.get("failed_step")
            for line in wizard.failure_lines(narrator, failed):
                view.append_record(line)
            view.outcome = "failed"
            view.exit_code = 2
            return 2
        except (OSError, ValueError, EventEmissionError, PlanError) as exc:
            view.add_diagnostic(f"ERROR: {exc}")
            failed = None
            if narrator.run_end_event is not None:
                failed = narrator.run_end_event.get("failed_step")
            for line in wizard.failure_lines(narrator, failed):
                view.append_record(line)
            view.outcome = "failed"
            view.exit_code = 2
            return 2
        finally:
            record_sink.close_pending()

        render_verified = False
        try:
            renderer.render_completed_run(run_dir)
            render_verified = True
        except (OSError, ValueError, renderer.LogRenderError) as exc:
            view.add_diagnostic(
                f"WARNING: completed-run renderer refused this run: {exc}"
            )

        for line in wizard.close_lines(
            receipt,
            run_dir_display,
            events_file_written=view.events_file_written,
            render_verified=render_verified,
        ):
            view.append_record(line)
        view.render_verified = render_verified
        view.outcome = "completed"
        view.exit_code = 0 if render_verified else 2
        return view.exit_code
    except KeyboardInterrupt:
        # The wizard's remaining-window rule: no path ends in a traceback,
        # and the renderer stays the arbiter of anything already on disk.
        view.add_diagnostic(
            "ERROR: interrupted. If a run had started, its folder holds "
            "what completed; the completed-run renderer is the arbiter."
        )
        view.outcome = "interrupted"
        view.exit_code = 130
        return 130


def read_session(view: ViewModel, run_dir: Path) -> int:
    """Read a completed run through the real renderer.

    A verified read is the renderer's text byte-for-byte; a refusal keeps
    stdout empty, records the renderer's exact diagnostic, and shows no
    ladder and no closed stamp anywhere.
    """
    view.diagnostics.clear()
    try:
        text = renderer.render_completed_run(run_dir)
    except (OSError, ValueError, renderer.LogRenderError) as exc:
        view.add_diagnostic(f"ERROR: {exc}")
        view.outcome = "read_refused"
        view.exit_code = 2
        return 2
    view.set_read_document(text)
    view.outcome = "read_verified"
    view.exit_code = 0
    return 0


def plain_argv(args) -> list[str]:
    """Map TUI CLI arguments onto the accepted plain wizard surface."""
    argv: list[str] = []
    if args.read is not None:
        argv += ["--read", str(args.read)]
    elif args.plan:
        argv += ["--plan"]
        if args.output_dir is not None:
            argv += ["--output-dir", str(args.output_dir)]
    elif args.output_dir is not None:
        argv += ["--output-dir", str(args.output_dir)]
        if args.yes:
            argv += ["--yes"]
        if args.no_events_file:
            argv += ["--no-events-file"]
    return argv


def run_plain(args) -> int:
    """The non-TTY and --plain path: the accepted wizard, unchanged."""
    return wizard.main(plain_argv(args))
