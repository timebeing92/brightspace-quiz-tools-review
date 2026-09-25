#!/usr/bin/env python3
"""Quiz Binder full-screen workbench (r4 "Bindery Ledger" entry point).

A window onto the run record, never a second narrator: the station rail
promises, the record pane testifies in the production register verbatim,
and the status line never claims above the ceiling.

Channel and fallback law:

- frames and every cursor-control byte go to stderr, only when stdin and
  stderr are both TTYs; a non-TTY invocation (or ``--plain``) delegates
  to the accepted plain wizard/renderer surfaces and emits no cursor
  controls at all;
- stdout receives the earned plain record exactly once, after the
  alternate screen has closed, so a TUI session is pipe-safe and its
  terminal scrollback keeps the durable copy;
- exit codes are the wizard's: 0 completed/verified-read/clean leave,
  2 refused/failed/renderer-refused, 130 interrupted.

The full-screen ledger retains its two synthetic-proof actions. The same entry
point also offers explicit plain-terminal commands for real-export Unbind and
verified completed-Unbind reading. Compose/Rebind remains present only in the
bounded synthetic capability proof; no general real-course build action is
offered.

r4 adds only presentation to the r3 entry point:

- ``--ascii`` (also auto when the locale is not UTF-8) selects the rc1
  ASCII glyph substitutions for chrome; it is passed through to the view
  model and changes chrome texture only.
- The Bindery accent (ANSI yellow) is a new whole-line chrome role. Rather
  than fork the terminal driver, the entry registers the ``accent`` role
  additively on the imported driver's role table (``register_accent_role``
  below). The registration is additive — it never overrides an existing
  role, leaves ``--mono`` / ``NO_COLOR`` untouched, and the driver already
  degrades an unregistered role to no colour — so the terminal driver is
  imported unchanged.
"""

from __future__ import annotations

import argparse
import locale
import sys
from pathlib import Path

_OWN_DIR = str(Path(__file__).resolve().parent)


def _own_dir_first() -> None:
    """Keep this module's own directory ahead of any path the view-model
    shim inserts, so a bare in-place run binds the sibling frames/view
    modules rather than same-named modules elsewhere on the path
    (adversarial finding R-A1 F1). In integrated production this file
    lives in ``scripts/`` beside every sibling, where this is a no-op in
    effect."""
    if _OWN_DIR in sys.path:
        sys.path.remove(_OWN_DIR)
    sys.path.insert(0, _OWN_DIR)


_own_dir_first()

from quiz_binder_tui_view import (
    CLOSED,
    FAILED,
    HOME,
    OVERLAY_HELP,
    OVERLAY_OUTPUTS,
    OVERLAY_PLAN,
    PATH_READ,
    PATH_RUN,
    READ,
    READ_REFUSED,
    REFUSED,
    RUNNING,
    TERMS,
    ViewModel,
)

# The view-model shim may have re-ordered sys.path; restore own-dir
# priority before binding the frame layer (R-A1 F1).
_own_dir_first()

import quiz_binder_tui_frames as frames
import quiz_binder_tui_session as session
import quiz_binder_tui_terminal as terminal
import quiz_binder_unbind_terminal as unbind_terminal
from quiz_binder_tui_shelf import discover_runs
from quiz_binder_tui_terminal import TerminalDriver, is_interactive, terminal_size

INTERRUPT_NOTE = (
    "ERROR: interrupted. If a run had started, its folder holds "
    "what completed; the completed-run renderer is the arbiter."
)

# The Bindery accent role (02 §6): ANSI yellow (SGR 33), chrome ornament
# only. Registered additively so the terminal driver stays unchanged.
ACCENT_SGR = "\x1b[33m"


def register_accent_role() -> None:
    """Ensure the driver knows the ``accent`` role without editing it.

    ``setdefault`` is additive: it installs the accent SGR only when the
    driver has not already defined the role, so this is a no-op wherever
    the role exists and never clobbers a semantic role. Colour remains
    gated by the driver's own ``color_enabled`` (``--mono`` / ``NO_COLOR``
    / dumb terminals stay uncoloured, text identical)."""
    terminal.SGR.setdefault(frames.ACCENT, ACCENT_SGR)


register_accent_role()


def _resolve_ascii_mode(explicit: bool) -> bool:
    """--ascii forces ASCII chrome; otherwise auto-detect a non-UTF-8
    locale (rc1 04), where the Unicode glyph family would not render."""
    if explicit:
        return True
    encoding = locale.getpreferredencoding(False) or ""
    return "utf-8" not in encoding.lower() and "utf8" not in encoding.lower()


class _Quit(Exception):
    pass


class App:
    def __init__(self, view: ViewModel, driver: TerminalDriver, args) -> None:
        self.view = view
        self.driver = driver
        self.args = args
        self.size = terminal_size()

    # -- rendering --------------------------------------------------------

    def redraw(self) -> None:
        self.size = terminal_size()
        rows = frames.render(self.view, self.size[0], self.size[1])
        self.driver.write_frame(rows)

    def _pane_rows(self) -> int:
        width, height = self.size
        if self.view.overlay is not None:
            return max(1, height - 3)
        if width >= frames.SPLIT_WIDTH and self.view.screen != READ:
            return max(1, height - 3)
        strip = 2 if self.view.screen != READ else 0
        return max(1, height - 3 - strip)

    def _pane_width(self) -> int:
        width, _ = self.size
        if width >= frames.SPLIT_WIDTH and self.view.screen != READ:
            return width - frames.RAIL_WIDTH - 2
        return width

    def _visual_total(self) -> int:
        return len(
            frames.expand_lines(self.view.active_lines(), self._pane_width())
        )

    # -- actions ----------------------------------------------------------

    def start_run(self) -> None:
        view = self.view
        try:
            run_dir = Path(view.path_field).expanduser().resolve()
        except OSError as exc:
            view.screen = PATH_RUN
            view.path_note = (
                "the working folder could not be resolved "
                f"({exc.__class__.__name__}); edit the path and try again"
            )
            return
        view.screen = RUNNING
        view.follow_tail = True
        self.redraw()
        session.run_session(
            view,
            run_dir,
            view.path_field,
            write_events_file=not self.args.no_events_file,
            redraw=self.redraw,
        )
        self.driver.flush_typeahead()
        if view.outcome == "interrupted":
            raise _Quit()
        if view.outcome == "refused":
            view.screen = REFUSED
        elif view.outcome == "failed":
            view.screen = FAILED
        else:
            view.screen = CLOSED
        self.redraw()

    def open_read(self, target: str) -> None:
        view = self.view
        view.path_field = target
        try:
            run_dir = Path(target).expanduser().resolve()
        except OSError as exc:
            view.diagnostics.clear()
            view.add_diagnostic(
                "ERROR: the run folder could not be resolved "
                f"({exc.__class__.__name__})"
            )
            view.outcome = "read_refused"
            view.exit_code = 2
            code = 2
        else:
            code = session.read_session(view, run_dir)
        view.screen = READ if code == 0 else READ_REFUSED
        view.follow_tail = False
        view.scroll = 0

    # -- key handling ------------------------------------------------------

    def handle_key(self, key: str) -> None:
        view = self.view
        width, height = self.size
        if width < frames.MIN_WIDTH or height < frames.MIN_HEIGHT:
            if key == "q":
                raise _Quit()
            return
        if view.overlay is not None:
            self._handle_overlay_key(key)
            return
        if key == "?":
            view.overlay = OVERLAY_HELP
            view.overlay_scroll = 0
            return
        handler = {
            HOME: self._key_home,
            PATH_RUN: self._key_path,
            PATH_READ: self._key_path,
            TERMS: self._key_terms,
            CLOSED: self._key_record,
            FAILED: self._key_record,
            REFUSED: self._key_refused,
            READ: self._key_record,
            READ_REFUSED: self._key_read_refused,
        }.get(view.screen)
        if handler is not None:
            handler(key)

    def _handle_overlay_key(self, key: str) -> None:
        view = self.view
        if key in ("esc", "q"):
            view.overlay = None
        elif key == "?" and view.overlay == OVERLAY_HELP:
            view.overlay = None
        elif key == "o" and view.overlay == OVERLAY_OUTPUTS:
            view.overlay = None
        elif key in ("j", "down"):
            view.overlay_scroll += 1
        elif key in ("k", "up"):
            view.overlay_scroll = max(0, view.overlay_scroll - 1)
        elif key == "pgdn":
            view.overlay_scroll += self._pane_rows() - 2
        elif key == "pgup":
            view.overlay_scroll = max(
                0, view.overlay_scroll - (self._pane_rows() - 2)
            )
        elif key == "g":
            view.overlay_scroll = 0

    def _key_home(self, key: str) -> None:
        view = self.view
        width, height = self.size
        if key == "q":
            raise _Quit()
        if key == "r":
            view.screen = PATH_RUN
            view.path_note = None
        elif key == "v":
            view.screen = PATH_READ
            if view.shelf_rows and view.runs_root_display is not None:
                selected = view.shelf_rows[view.shelf_selected]
                view.path_field = str(
                    Path(view.runs_root_display) / selected
                )
            elif self.args.read is not None:
                view.path_field = str(self.args.read)
            else:
                view.path_field = ""
            view.path_note = None
        elif key == "p":
            view.overlay = OVERLAY_PLAN
            view.overlay_scroll = 0
        elif key == "up" and view.shelf_rows:
            view.shelf_selected = max(0, view.shelf_selected - 1)
            _, ranges = frames.shelf_visual_rows(view, width)
            view.shelf_scroll = ranges[view.shelf_selected][0]
        elif key == "down" and view.shelf_rows:
            view.shelf_selected = min(
                len(view.shelf_rows) - 1, view.shelf_selected + 1
            )
            _, ranges = frames.shelf_visual_rows(view, width)
            view.shelf_scroll = ranges[view.shelf_selected][0]
        elif key == "pgdn" and view.shelf_rows:
            view.shelf_scroll += max(1, height // 3)
        elif key == "pgup" and view.shelf_rows:
            view.shelf_scroll = max(0, view.shelf_scroll - max(1, height // 3))
        elif key == "enter" and view.shelf_rows:
            selected = view.shelf_rows[view.shelf_selected]
            self.open_read(str(Path(view.runs_root_display) / selected))

    def _key_path(self, key: str) -> None:
        view = self.view
        reading = view.screen == PATH_READ
        if key == "esc":
            view.screen = HOME
            view.path_note = None
        elif key == "enter":
            target = view.path_field.strip()
            if not target:
                view.path_note = "name a folder first"
                return
            if reading:
                self.open_read(target)
                return
            probe = Path(target).expanduser()
            caution: str | None = None
            try:
                if probe.exists():
                    if not probe.is_dir():
                        caution = (
                            "this path exists but is not a folder; the engine "
                            "will refuse it and preserve it. Enter again "
                            "proceeds to that refusal."
                        )
                    elif any(probe.iterdir()):
                        caution = (
                            "this folder exists and is not empty; the engine "
                            "will refuse it and preserve it. Enter again "
                            "proceeds to that refusal."
                        )
            except OSError as exc:
                caution = (
                    "this path could not be inspected "
                    f"({exc.__class__.__name__}); the engine remains the "
                    "refusal authority. Enter again proceeds to its check."
                )
            if caution is not None and view.path_note is None:
                view.path_note = caution
                return
            view.screen = TERMS
            view.path_note = None
            view.terms_note = None
            view.scroll = 0
            view.follow_tail = False
        elif key == "backspace":
            view.path_field = view.path_field[:-1]
            view.path_note = None
        elif key == "ctrl_u":
            view.path_field = ""
            view.path_note = None
        elif key == "q" and not view.path_field:
            raise _Quit()
        elif len(key) == 1 and key.isprintable():
            view.path_field += key
            view.path_note = None

    def _key_terms(self, key: str) -> None:
        view = self.view
        if key == "enter":
            if self._terms_at_end():
                view.terms_note = None
                self.start_run()
            else:
                view.terms_note = "review the final terms before running"
        elif key == "esc":
            view.screen = PATH_RUN
            view.terms_note = None
            view.scroll = 0
        elif key == "q":
            raise _Quit()
        elif key in ("j", "down"):
            view.scroll = min(self._terms_max_scroll(), view.scroll + 1)
            view.terms_note = None
        elif key in ("k", "up"):
            view.scroll = max(0, view.scroll - 1)
            view.terms_note = None
        elif key == "pgdn":
            visible = max(1, self.size[1] - 3)
            view.scroll = min(
                self._terms_max_scroll(),
                view.scroll + max(1, visible - 2),
            )
            view.terms_note = None
        elif key == "pgup":
            visible = max(1, self.size[1] - 3)
            view.scroll = max(0, view.scroll - max(1, visible - 2))
            view.terms_note = None
        elif key == "g":
            view.scroll = 0
            view.terms_note = None
        elif key == "G":
            view.scroll = self._terms_max_scroll()
            view.terms_note = None

    def _terms_max_scroll(self) -> int:
        width, height = self.size
        visible = max(1, height - 3)
        return max(
            0,
            len(frames.terms_visual_rows(self.view, width)) - visible,
        )

    def _terms_at_end(self) -> bool:
        return self.view.scroll >= self._terms_max_scroll()

    def _key_record(self, key: str) -> None:
        view = self.view
        top = max(0, self._visual_total() - 1)
        page = max(1, self._pane_rows() - 2)
        if key == "q":
            raise _Quit()
        if key in ("j", "down"):
            view.follow_tail = False
            view.scroll = min(top, view.scroll + 1)
        elif key in ("k", "up"):
            view.follow_tail = False
            view.scroll = max(0, view.scroll - 1)
        elif key == "pgdn":
            view.follow_tail = False
            view.scroll = min(top, view.scroll + page)
        elif key == "pgup":
            view.follow_tail = False
            view.scroll = max(0, view.scroll - page)
        elif key == "g":
            view.follow_tail = False
            view.scroll = 0
        elif key == "G":
            view.follow_tail = True
        elif key in ("n", "p"):
            self._jump_section(forward=key == "n")
        elif (
            key == "o"
            and view.screen == CLOSED
            and view.render_verified
        ):
            view.overlay = OVERLAY_OUTPUTS
            view.overlay_scroll = 0

    def _jump_section(self, *, forward: bool) -> None:
        view = self.view
        sections = [
            row
            for row, _ in frames.section_positions(
                view.active_lines(), self._pane_width()
            )
        ]
        if not sections:
            return
        current = view.scroll if not view.follow_tail else max(
            0, self._visual_total() - self._pane_rows()
        )
        if forward:
            later = [row for row in sections if row > current]
            target = later[0] if later else sections[-1]
        else:
            earlier = [row for row in sections if row < current]
            target = earlier[-1] if earlier else sections[0]
        view.follow_tail = False
        view.scroll = target

    def _key_refused(self, key: str) -> None:
        view = self.view
        if key == "q":
            raise _Quit()
        if key == "e":
            # Nothing was earned by the refused attempt: the engine wrote
            # nothing, so the intent lines are cleared rather than letting
            # a later record carry two thresholds. Diagnostics remain.
            view.record.clear()
            view.diagnostics.clear()
            view.outcome = None
            view.exit_code = 0
            view.screen = PATH_RUN
            view.path_note = None

    def _key_read_refused(self, key: str) -> None:
        view = self.view
        if key == "q":
            raise _Quit()
        if key == "v":
            view.diagnostics.clear()
            view.outcome = None
            view.exit_code = 0
            view.screen = PATH_READ
            view.path_note = None

    # -- main loop ---------------------------------------------------------

    def loop(self) -> int:
        view = self.view
        while True:
            self.redraw()
            key = self.driver.read_key(timeout=None)
            if key is None:
                continue
            if key == "resize":
                continue
            if key == "eof":
                raise _Quit()
            self.handle_key(key)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Working folder for a new synthetic proof run (must be new or "
        "empty); opens the workbench on the terms screen.",
    )
    parser.add_argument(
        "--read",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Open a completed run through the verified renderer.",
    )
    parser.add_argument(
        "--unbind",
        type=Path,
        default=None,
        metavar="EXPORT",
        help="Run canonical real-export Unbind in the plain terminal. Requires "
        "--output-dir and performs extraction/review only.",
    )
    parser.add_argument(
        "--read-unbind",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Re-verify and read a completed real-export Unbind run.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        metavar="DIR",
        help="List completed runs found directly under DIR on the start "
        "screen (one level deep; symlinks skipped). No other scanning "
        "happens.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Skip the full-screen surface and use the accepted plain "
        "wizard (the screen-reader and automation path).",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the run plan and terms on stdout and exit (plain "
        "surface; no full screen).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Passed through to the plain wizard for non-interactive runs; "
        "the full-screen surface always shows the terms screen instead.",
    )
    parser.add_argument(
        "--no-events-file",
        action="store_true",
        help="Do not write the journey.events.ndjson sidecar into the run "
        "folder.",
    )
    parser.add_argument(
        "--source-lineage-key",
        default="",
        help="Optional stable cc:lineage key for a real-export Unbind run.",
    )
    parser.add_argument(
        "--quiz-entity-key",
        default="",
        help="Optional quiz key for the Unbind authoring-readiness projection.",
    )
    parser.add_argument(
        "--asset-mode",
        choices=("self-contained", "reference"),
        default="self-contained",
        help="For Unbind, copy resolved assets by default or explicitly keep references.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Optional assertion of the pinned capability-registry path for Unbind.",
    )
    parser.add_argument(
        "--allow-tracked-output",
        action="store_true",
        help="Pass the explicit tracked-output override to canonical Unbind.",
    )
    parser.add_argument(
        "--mono",
        action="store_true",
        help="Disable color tinting (the text is identical either way).",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Render chrome glyphs as their ASCII words (auto on a "
        "non-UTF-8 locale). The record and stdout are unaffected.",
    )
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for label, value in (
        ("output directory", args.output_dir),
        ("read directory", args.read),
        ("Unbind export", args.unbind),
        ("Unbind read directory", args.read_unbind),
        ("registry", args.registry),
        ("runs root", args.runs_root),
    ):
        if value is None:
            continue
        raw = str(value)
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
            sys.stderr.write(
                f"ERROR: {label} contains a control character; choose a "
                "literal filesystem path\n"
            )
            return 2

    selected_actions = sum(
        bool(value)
        for value in (args.read, args.unbind, args.read_unbind, args.plan)
    )
    if selected_actions > 1:
        sys.stderr.write(
            "ERROR: choose only one of --read, --unbind, --read-unbind, or --plan\n"
        )
        return 2
    if args.unbind is not None:
        if args.output_dir is None:
            sys.stderr.write("ERROR: --unbind requires --output-dir\n")
            return 2
        return unbind_terminal.run_real_unbind(
            export_path=args.unbind,
            output_dir=args.output_dir,
            source_lineage_key=args.source_lineage_key,
            quiz_entity_key=args.quiz_entity_key,
            asset_mode=args.asset_mode.replace("-", "_"),
            registry_path=args.registry,
            allow_tracked_output=args.allow_tracked_output,
        )
    if args.read_unbind is not None:
        if args.output_dir is not None:
            sys.stderr.write("ERROR: --read-unbind does not accept --output-dir\n")
            return 2
        return unbind_terminal.read_real_unbind(args.read_unbind)

    if args.plain or args.plan or not is_interactive():
        return session.run_plain(args)

    view = ViewModel(
        path_prefill=(
            str(args.output_dir) if args.output_dir is not None
            else "output/tui-journey"
        ),
        runs_root_display=(
            str(args.runs_root) if args.runs_root is not None else None
        ),
        ascii_mode=_resolve_ascii_mode(args.ascii),
    )
    if args.runs_root is not None:
        rows, note = discover_runs(args.runs_root.expanduser())
        view.shelf_rows = rows
        view.shelf_note = note

    exit_code = 0
    interrupted_outside_session = False
    driver = TerminalDriver(mono=args.mono)
    try:
        with driver:
            app = App(view, driver, args)
            if args.read is not None:
                app.open_read(str(args.read))
            elif args.output_dir is not None:
                view.screen = TERMS
            try:
                app.loop()
            except _Quit:
                pass
        exit_code = view.exit_code
    except KeyboardInterrupt:
        driver.close()
        if view.outcome is None or view.outcome == "interrupted":
            view.add_diagnostic(INTERRUPT_NOTE)
        view.exit_code = 130
        exit_code = 130
        interrupted_outside_session = True
    finally:
        payload = view.stdout_payload()
        if payload:
            sys.stdout.write(payload)
            sys.stdout.flush()
        if view.outcome is None and not interrupted_outside_session:
            view.add_diagnostic(
                "left the workbench; nothing was run and nothing was "
                "written."
            )
        if view.diagnostics:
            sys.stderr.write(
                "\n".join(frames.screen_safe(line) for line in view.diagnostics)
                + "\n"
            )
            sys.stderr.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
