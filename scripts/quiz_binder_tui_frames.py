#!/usr/bin/env python3
"""Pure frame rendering for the Quiz Binder full-screen workbench — r4
"Bindery Ledger" register.

``render(view, width, height)`` returns a list of ``(text, role)`` rows
and is a pure function: no I/O, no terminal state, no time. Styles are
whole-line roles only, so a monochrome rendering is the identical text —
color is a redundant tint by construction, never a carrier of state.

r4 gives the r3 chassis (station rail, record pane, status bar) the
Bindery's voice. The register is *chrome warmth around unchanged
testimony*: every warm element is strippable to r3's austerity with zero
fact loss.

Two laws separate chrome from testimony:

- **Glyph law.** The glyph family ``✓ ▸ ■ ✗ · ─ ═ §`` appears in
  CHROME only, each welded to its word, with the rc1 ASCII substitution
  (``ok > STOP FAIL - - = (section)``) under ``view.ascii_mode``. The
  record pane rows and the stdout payload stay byte-identical to the
  wizard's characters at every geometry and in both glyph modes — the
  frame chrome never rewraps, abbreviates, or paraphrases a record line,
  and never lends the record a glyph.
- **Accent law.** The Binder accent is ANSI yellow (SGR 33, the ``accent``
  role), used on chrome ornament only — plate/lane rules, the colophon
  frame. It never tints record text or a good/boundary/danger surface, is
  never load-bearing, and vanishes with color entirely (mono/``NO_COLOR``
  text is identical).

Width tiers (02 §6): ``>= 110`` columns render the 30-column station rail
beside the record pane; ``80-109`` render a two-line act strip above the
full-width pane; below ``80x12`` render a deliberate fallback notice whose
plain-wizard commands are wrapped, never cropped. The stdout record is
unaffected by any of this.
"""

from __future__ import annotations

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
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_RUNNING,
    TERMS,
    ViewModel,
    _ensure_scripts_on_path,
)

_ensure_scripts_on_path()

from quiz_binder_journey_plan import (  # noqa: E402
    BOUNDARY_STEP_KEY,
    KNOWN_BLOCKER_CODES,
)

MIN_WIDTH = 80
MIN_HEIGHT = 12
SPLIT_WIDTH = 110
RAIL_WIDTH = 30

# Roles (02 §6). Text is identical with roles ignored. ``accent`` is the
# only role r4 adds; the driver degrades an unknown role to no colour, so
# a driver that has not registered it renders accent lines as plain text
# with the identical characters.
PLAIN = "plain"
EMPHASIS = "emphasis"
STATUSBAR = "statusbar"
GOOD = "good"
BOUNDARY = "boundary"
DANGER = "danger"
MUTED = "muted"
ACCENT = "accent"

ROLES = (PLAIN, EMPHASIS, STATUSBAR, GOOD, BOUNDARY, DANGER, MUTED, ACCENT)

Row = tuple[str, str]

# -- glyph family (chrome only; welded to words) --------------------------

_GLYPHS = {
    "check": ("✓", "ok"),        # ✓ past-tense fact
    "open": ("▸", ">"),          # ▸ open / working
    "stop": ("■", "STOP"),       # ■ full stop / boundary
    "fail": ("✗", "FAIL"),       # ✗ refusal / failure
    "dot": ("·", "-"),           # · pending / separator
    "rule": ("─", "-"),          # ─ single rule
    "drule": ("═", "="),         # ═ double rule (colophon only)
    "section": ("§", "(section)"),  # § colophon only
    "emdash": ("—", "-"),        # — chrome punctuation
}


def glyph(view: ViewModel, name: str) -> str:
    unicode_glyph, ascii_glyph = _GLYPHS[name]
    return ascii_glyph if getattr(view, "ascii_mode", False) else unicode_glyph


def _asciify(text: str) -> str:
    """Fold the chrome glyph family to its rc1 ASCII substitutes.

    Used for any protected chrome string that carries a glyph literal
    (e.g. the stop essay's em dash) when ``ascii_mode`` is set, so ASCII
    mode changes texture, not content.
    """
    for unicode_glyph, ascii_glyph in _GLYPHS.values():
        text = text.replace(unicode_glyph, ascii_glyph)
    return text


def _chrome(view: ViewModel, text: str) -> str:
    return _asciify(text) if getattr(view, "ascii_mode", False) else text


# -- stations (rc1 02 §1 names + plain glosses) --------------------------
#
# Station nouns come only from the ratified lexicon (rc1 03 §1): Reading
# Room, Marginalia, Fair Copy, Assemble, Rebind, Seal. Each is welded to a
# plain gloss on every surface it appears. The rail uses short forms so
# name + gloss + status fit 30 columns; the threshold and terms plates use
# the full ``Station (plain name)`` form.

_RAIL_STATION = {
    "unbind": "Reading Room",
    "materialize_marginalia": "Marginalia",
    "promote_accepted_marginalia": "Fair Copy",
    "render_review_station": "Reading Room",
    "check_unbound_readiness": "Rebind check",
    "check_compose_readiness": "Rebind check",
    "rebind": "Assemble",
    "seal_validate": "Seal",
    "seal_local_equivalence": "Seal",
}

_STATION_GLOSS = {
    "unbind": "unbind the export",
    "materialize_marginalia": "read back decisions",
    "promote_accepted_marginalia": "promote revisions",
    "render_review_station": "station opens",
    "check_unbound_readiness": "the full stop",
    "check_compose_readiness": "the model is ready",
    "rebind": "bind the package",
    "seal_validate": "strict validation",
    "seal_local_equivalence": "folder / ZIP match",
}

SCREEN_TAGS = {
    HOME: "start",
    PATH_RUN: "choose folder",
    PATH_READ: "choose run",
    TERMS: "terms",
    RUNNING: "live record",
    FAILED: "failed",
    REFUSED: "refused",
    READ: "read-only",
    READ_REFUSED: "refused",
}

PLAIN_COMMANDS = (
    "  .venv/bin/python scripts/quiz_binder_wizard.py --output-dir DIR",
    "  .venv/bin/python scripts/quiz_binder_wizard.py --read RUN_DIR",
)

# The stop essay — rc1 03 §2 state 6, EXACT strings (protected copy: the two
# opening sentences, the two-truths sentence, the preservation sentence).
# Rendered as chrome only, and only when the boundary earned exactly the
# three known blocker codes (rc1 R2). Warmth on unknown conditions is
# banned, so an unrecognized code keeps the terse register the record
# already speaks.
STOP_ESSAY = (
    'A binder that cannot say "not yet" could never be trusted to say '
    '"done." This stop is the bindery working, not you failing.',
    "What you unbound is evidence of what Brightspace held. Your revisions "
    "and codes join the working model when a person approves them — but "
    "build eligibility is a different kind of truth: it comes only from "
    "recorded evidence, never from preference.",
    "Nothing was lost by stopping here.",
)

# The colophon couplet — rc1 03 §2 close, the run's entire closing ornament;
# it vanishes in plain / ASCII-stripped renderings.
COLOPHON_COUPLET = (
    "The bindery keeps the ledger.",
    "The ledger keeps the truth.",
)


# -- safe display + padding ------------------------------------------------


def screen_safe(text: str) -> str:
    """Return reversible ASCII for terminal-controlled display text.

    Canonical stdout remains byte-for-byte untouched. Frames, however, may
    receive operator paths, filesystem names, or diagnostics; control bytes
    there must be visible characters rather than executable terminal input.
    Trusted chrome (the register's own glyphs and rules) is written
    directly and never passes through here, so its glyph family survives.
    """
    rendered: list[str] = []
    for character in str(text):
        codepoint = ord(character)
        if 0x20 <= codepoint <= 0x7E:
            rendered.append(character)
        elif codepoint <= 0xFF:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\U{codepoint:08x}")
    return "".join(rendered)


def _pad(text: str, width: int) -> str:
    """Pad or crop trusted text to exactly ``width`` code points.

    Chrome is constructed to fit, so this pads; the crop is only a
    backstop. It never escapes, so an already-safe or trusted string keeps
    its characters (glyphs included). Every glyph in the family is one
    column wide, so ``len == width`` matches the painted width.
    """
    width = max(0, width)
    return text[:width].ljust(width)


def _safe_pad(text: str, width: int) -> str:
    return _pad(screen_safe(text), width)


def _wrap_segments(text: str, width: int) -> list[str]:
    """Word-aware, character-preserving soft wrap.

    Breaks at spaces where possible and character-exact for long tokens
    (paths, hashes), adding and dropping nothing: ``"".join(segments) ==
    text`` and every segment is at most ``width`` columns. The breaking
    space stays on the segment that ends with it, so a path is never split
    across a lost space (fixes the r3 ``SHA-2 / 56`` mid-token wrap while
    keeping the no-truncation law).
    """
    width = max(1, width)
    if len(text) <= width:
        return [text]
    segments: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if length - index <= width:
            segments.append(text[index:])
            break
        limit = index + width
        brk = text.rfind(" ", index + 1, limit)
        if brk == -1:
            segments.append(text[index:limit])
            index = limit
        else:
            segments.append(text[index : brk + 1])
            index = brk + 1
    return segments


def expand_lines(lines: list[str], width: int) -> list[str]:
    """Visual rows for dynamic content: escape each line to safe display
    text, then soft-wrap it word-aware. Adds no characters, so the
    segments re-join to the escaped line."""
    rows: list[str] = []
    for raw_line in lines:
        rows.extend(_wrap_segments(screen_safe(raw_line), width))
    return rows


def _chrome_rows(view: ViewModel, text: str, width: int, role: str) -> list[Row]:
    """Wrap a trusted chrome string (glyphs preserved) into padded rows."""
    return [
        (_pad(segment, width), role)
        for segment in _wrap_segments(_chrome(view, text), width)
    ]


def _para_rows(
    view: ViewModel, text: str, width: int, role: str, indent: str = "  "
) -> list[Row]:
    """Wrap a chrome paragraph with a hanging indent, glyphs preserved.

    Chrome only (the stop essay, the colophon prose): unlike the record
    pane's ``expand_lines``, this may pretty-wrap because nothing here must
    re-join to a canonical line — the record pane carries the testimony,
    this carries welcome."""
    inner = max(1, width - len(indent))
    rows: list[Row] = []
    for segment in _wrap_segments(_chrome(view, text), inner):
        rows.append((_pad(indent + segment.rstrip(), width), role))
    return rows


def _finalize(view: ViewModel, body: list[Row], width: int) -> list[Row]:
    """Pad assembled body rows to width without escaping.

    Every builder that reaches here has already escaped its dynamic
    content through ``expand_lines`` (paths, diagnostics, shelf names,
    record lines), so the remaining strings are either that escaped text
    or trusted chrome. ``_chrome`` folds the glyph family to ASCII in ASCII
    mode and is a no-op on already-escaped ASCII; ``_pad`` never escapes,
    so the register's glyphs survive while control bytes never do."""
    return [(_pad(_chrome(view, text), width), role) for text, role in body]


def _rule(view: ViewModel, width: int, label: str = "", *, double: bool = False) -> str:
    ch = glyph(view, "drule" if double else "rule")
    if not label:
        return ch * width
    lead = f" {ch}{ch} {label} "
    fill = width - len(lead)
    if fill < 0:
        return _pad(lead, width)
    return lead + ch * fill


def _tagline(left: str, tag: str, width: int) -> str:
    right = f"[ {tag} ]"
    space = width - len(left) - len(right) - 1
    if space < 1:
        return _pad(screen_safe(left), width)
    return f"{left}{' ' * space}{right} "


def _statusbar(left: str, right: str, width: int) -> Row:
    text = f" {left}"
    if right:
        gap = width - len(text) - len(right) - 1
        if gap >= 2:
            text = text + " " * gap + right + " "
    return (_safe_pad(text, width), STATUSBAR)


def _header(view: ViewModel, width: int) -> list[Row]:
    tag = SCREEN_TAGS.get(view.screen, "")
    if view.screen == CLOSED:
        tag = "closed" if view.render_verified else "not closed"
    sep = glyph(view, "dot")
    title = f" QUIZ BINDER {sep} the bindery workbench"
    return [
        (_pad(_tagline(title, tag, width), width), EMPHASIS),
        (_pad(_rule(view, width), width), ACCENT),
    ]


# -- record roles ----------------------------------------------------------


def _record_role(line: str) -> str:
    if line.startswith(("STOP:", "  STOP:")):
        return BOUNDARY
    if line.startswith(("FAIL:", "ERROR:")) or "NOT CLOSED" in line:
        return DANGER
    if line.startswith("RUN RECORD CLOSED") or line.startswith(
        "RUN RECORD COMPLETE"
    ):
        return GOOD
    if line.startswith(("QUIZ BINDER", "RUN PLAN", "TERMS OF THIS RUN",
                        "RUN RECORD", "PROOF LADDER", "RUN SUMMARY",
                        "UNBIND", "COMPOSE", "SEAL", "ACTIONS", "HELP")):
        return EMPHASIS
    return PLAIN


def _pane_rows(lines: list[str], width: int) -> list[Row]:
    return [(_safe_pad(line, width), _record_role(line)) for line in lines]


# -- the station rail (>= 110 columns) ------------------------------------


def _status_token(view: ViewModel, step) -> tuple[str, str]:
    """(glyph + welded word, role) for one station's status."""
    if step.status == STEP_COMPLETED:
        if step.boundary:
            return f"{glyph(view, 'stop')} [boundary]", BOUNDARY
        return f"{glyph(view, 'check')} done", GOOD
    if step.status == STEP_RUNNING:
        return f"{glyph(view, 'open')} working", EMPHASIS
    if step.status == STEP_FAILED:
        return f"{glyph(view, 'fail')} failed", DANGER
    if step.boundary:
        return f"{glyph(view, 'dot')} stop ahead", BOUNDARY
    return f"{glyph(view, 'dot')} pending", MUTED


def _rail_rows(view: ViewModel, height: int) -> list[Row]:
    """The vertical station rail (>= 110 columns), RAIL_WIDTH wide.

    Two rows per station: the station name welded to its status word on
    the first, the plain gloss on the second. The r3 essay paragraph is
    gone (it duplicated the record pane); its space now carries the
    glosses. The rail is a promise; the record pane beside it testifies.
    """
    rows: list[Row] = [
        (_pad(" THE BINDERY LEDGER", RAIL_WIDTH), EMPHASIS),
        (_pad(" nine entries, two doors", RAIL_WIDTH), MUTED),
    ]
    for door in ("unbind", "compose"):
        rows.append((_pad(_rule(view, RAIL_WIDTH, door), RAIL_WIDTH), ACCENT))
        for step in view.steps:
            if step.door != door:
                continue
            station = _RAIL_STATION.get(step.key, step.key)
            token, role = _status_token(view, step)
            left = f"  {step.index} {station}"
            gap = RAIL_WIDTH - len(left) - len(token) - 1
            if gap < 1:
                line = _pad(left, RAIL_WIDTH)
            else:
                line = f"{left}{' ' * gap}{token} "
            rows.append((_pad(line, RAIL_WIDTH), role))
            gloss = _STATION_GLOSS.get(step.key, "")
            rows.append((_pad(f"      {gloss}", RAIL_WIDTH), MUTED))
    return rows[:height]


def _strip_rows(view: ViewModel, width: int) -> list[Row]:
    """The two-line act strip (80-109 columns): a lane label and each
    station's canonical status words. Ceremony yields to evidence under
    space pressure, so the strip keeps the status words verbatim (the
    warmth in this tier lives in the header and the stop plate). The
    running lane is emphasised as a whole line rather than by a glyph, so
    the strip stays exactly as wide as r3 and never crops its own tag."""
    rows: list[Row] = []
    for door in ("unbind", "compose"):
        cells = []
        for step in view.steps:
            if step.door != door:
                continue
            cells.append(f"{step.index} {step.status_words()}")
        line = f" {door:<7} " + " ".join(cells)
        role = EMPHASIS if any(
            s.door == door and s.status == STEP_RUNNING for s in view.steps
        ) else PLAIN
        rows.append((_pad(_chrome(view, line), width), role))
    return rows


# -- the stop plate (chrome; gated to the three known codes) ---------------


def stop_plate_eligible(view: ViewModel) -> bool:
    """The warm stop essay is licensed only when the boundary step
    completed ok AND its blocker codes are exactly the three known codes
    (rc1 R2). Any other set — including an unknown code — keeps the terse
    register the record already carries."""
    if not view.boundary_recorded():
        return False
    codes = view.boundary_blocker_codes
    if codes is None:
        return False
    return sorted(codes) == sorted(KNOWN_BLOCKER_CODES)


def _stop_plate_rows(view: ViewModel, width: int) -> list[Row]:
    label = _chrome(view, f"{glyph(view, 'stop')} the full stop, in the "
                    "Bindery's voice")
    rows: list[Row] = [(_pad(_rule(view, width, label), width), ACCENT)]
    for sentence in STOP_ESSAY:
        rows += _para_rows(view, sentence, width, PLAIN)
    rows.append((_pad(_rule(view, width), width), ACCENT))
    return rows


# -- the colophon (double rule + couplet; verified close only) -------------


def _colophon_rows(view: ViewModel, width: int) -> list[Row]:
    """The one ceremonial peak: the double rule (rendered here and only
    here) and the rc1 couplet, the run's sole closing ornament. The facts
    are the receipt-backed close lines already in the record pane; the
    colophon adds ornament, never a new fact."""
    section = glyph(view, "section")
    couplet = f"{section} " + "  ".join(COLOPHON_COUPLET) + f" {section}"
    return [
        (_pad(_rule(view, width, double=True), width), ACCENT),
        (_pad(_chrome(view, f" {couplet}"), width), ACCENT),
    ]


# -- section bookmarks -----------------------------------------------------


def _is_bookmark(line: str) -> bool:
    from quiz_binder_tui_view import SECTION_PREFIXES

    stripped = line.strip()
    if not stripped:
        return False
    if line.startswith(SECTION_PREFIXES):
        return True
    # Receipt-step bookmarks: each earned posting line "entry n of 9: ...".
    return line.startswith("entry ") and " of " in line


def section_positions(lines: list[str], width: int) -> list[tuple[int, str]]:
    """Section headings and receipt-step postings in visual-row
    coordinates (both are short and never wrap, so each maps to its first
    visual row)."""
    positions: list[tuple[int, str]] = []
    row = 0
    for line in lines:
        rows = len(_wrap_segments(screen_safe(line), width))
        if _is_bookmark(line):
            positions.append((row, line.strip()))
        row += rows
    return positions


def _pane_window(
    lines: list[str], view: ViewModel, rows: int, width: int
) -> tuple[list[str], str]:
    """Visible slice of a line buffer, in visual rows, plus a position
    note."""
    rows = max(1, rows)
    visual = expand_lines(lines, width)
    total = len(visual)
    if total <= rows:
        return visual + [""] * (rows - total), f"rows 1-{total} of {total}"
    if view.follow_tail:
        start = total - rows
    else:
        start = max(0, min(view.scroll, total - rows))
    window = visual[start : start + rows]
    return window, f"rows {start + 1}-{start + rows} of {total}"


def shelf_visual_rows(
    view: ViewModel, width: int
) -> tuple[list[Row], list[tuple[int, int]]]:
    """Render every shelf entry into safe, exact visual rows.

    Ranges are half-open visual-row coordinates used by the app and frame
    renderer to keep the selected entry visible without selecting an unseen
    run. A very long selected name remains pageable instead of being cropped.
    """
    visual: list[Row] = []
    ranges: list[tuple[int, int]] = []
    for index, name in enumerate(view.shelf_rows):
        marker = ">" if index == view.shelf_selected else " "
        source = (
            f"  {marker} {name}  journey.receipt.json present "
            "(verified only on open)"
        )
        start = len(visual)
        visual.extend((line, PLAIN) for line in expand_lines([source], width))
        ranges.append((start, len(visual)))
    return visual, ranges


# -- screens ---------------------------------------------------------------


def _home_body(view: ViewModel, width: int, rows: int) -> list[Row]:
    dot = glyph(view, "dot")
    body: list[Row] = [
        (" local only: network: none. Brightspace operations: none. "
         "Telemetry: none.", PLAIN),
        (" proof ceiling: Validated locally - reading and running here "
         "cannot raise it.", PLAIN),
        (" fixtures: synthetic specimen fixtures only - no live course "
         "data.", PLAIN),
        ("", PLAIN),
        (" One ledger opens for this run. Every completed step writes one "
         "entry,", PLAIN),
        (" and every entry points at a file you can open.", PLAIN),
        ("", PLAIN),
        (_chrome(view, _rule(view, width, "two doors, one fixed proof run")),
         ACCENT),
    ]
    body += [
        (f"  r  run {dot} walk the bindery", EMPHASIS),
        ("       Unbind a synthetic export into evidence, stop honestly at "
         "the rebind-", PLAIN),
        ("       readiness check, then compose and seal a separately-"
         "approved model.", PLAIN),
        ("       Both lanes, one fixed sequence, ending at a verified "
         "close.", MUTED),
        (f"  v  read {dot} a closed ledger", EMPHASIS),
        ("       Reopen a completed run through the verified renderer.",
         PLAIN),
        ("", PLAIN),
    ]
    body += [
        (" This walk ends its first lane at a scheduled stop: extracted "
         "evidence is", PLAIN),
        (" not automatically build-approved. It is recorded as completed "
         "work, not", PLAIN),
        (" failure.", PLAIN),
        ("", PLAIN),
        (" ACTIONS", EMPHASIS),
        (f"  r  run   v  read   p  plan and terms   ?  map   q  leave "
         "(nothing has run)", PLAIN),
    ]
    if view.runs_root_display is not None:
        body.append(("", PLAIN))
        body.extend(
            (line, EMPHASIS)
            for line in expand_lines(
                [f" COMPLETED RUNS under {view.runs_root_display}"], width
            )
        )
        if view.shelf_note:
            body.extend(
                (line, MUTED)
                for line in expand_lines([f"  {view.shelf_note}"], width)
            )
        if view.shelf_rows:
            shelf, ranges = shelf_visual_rows(view, width)
            available = max(1, rows - len(body) - 2)
            maximum = max(0, len(shelf) - available)
            start = max(0, min(view.shelf_scroll, maximum))
            selected_start, selected_end = ranges[view.shelf_selected]
            if selected_end <= start:
                start = selected_start
            elif selected_start >= start + available:
                start = min(selected_start, maximum)
            body.extend(shelf[start : start + available])
            body.append(
                (
                    f"  selected {view.shelf_selected + 1} of "
                    f"{len(view.shelf_rows)}; shelf rows "
                    f"{start + 1}-{min(start + available, len(shelf))} "
                    f"of {len(shelf)}",
                    MUTED,
                )
            )
            body.append(
                (
                    "  up/down select; PgUp/PgDn page; Enter reads selected",
                    MUTED,
                )
            )
    return _finalize(view, body, width)[:rows]


def _path_body(view: ViewModel, width: int, rows: int) -> list[Row]:
    reading = view.screen == PATH_READ
    if reading:
        title = " CHOOSE A COMPLETED RUN TO REOPEN"
        explain = (
            " The renderer re-verifies this folder's receipt before it "
            "narrates anything."
        )
        enter = " Enter opens the run through the verified renderer;"
    else:
        title = " CHOOSE A WORKING FOLDER FOR THIS RUN"
        explain = (
            " The run writes only into this folder; it must be new or empty."
        )
        enter = " Enter opens the terms of this run;"
    head: list[Row] = [
        (title, EMPHASIS),
        (_chrome(view, _rule(view, width)), ACCENT),
        (explain, PLAIN),
        (" Relative paths resolve from the folder this workbench was "
         "launched in.", MUTED),
        ("", PLAIN),
    ]
    footer: list[Row] = [
        ("", PLAIN),
        (enter, MUTED),
        (" typing edits; backspace deletes; Ctrl-U clears; Esc goes back.",
         MUTED),
    ]
    note: list[Row] = []
    if view.path_note:
        note.append(("", PLAIN))
        note.extend(
            (f" {line}", DANGER)
            for line in expand_lines([view.path_note], width - 2)
        )
    field = [
        (line, EMPHASIS)
        for line in expand_lines([f" folder: {view.path_field}_"], width)
    ]
    budget = max(1, rows - len(head) - len(footer) - len(note))
    if len(field) > budget:
        viewport_note = (
            " path is longer than this field; final rows shown. "
            "The complete path is reviewable on the terms screen."
        )
        note_rows = [
            (line, MUTED) for line in expand_lines([viewport_note], width)
        ]
        keep = max(1, budget - len(note_rows))
        field = note_rows[: max(0, budget - keep)] + field[-keep:]
    body = head + field + footer + note
    return _finalize(view, body, width)[:rows]


def terms_visual_rows(view: ViewModel, width: int) -> list[Row]:
    """The terms card: the wizard's six verbatim preflight lines framed in
    chrome. The frame is ornament; the terms are the wizard's exact lines,
    unrewrapped and unglossed, so the scroll gate reaches the final term
    unchanged."""
    import quiz_binder_wizard as wizard

    body: list[Row] = [
        (_chrome(view, _rule(view, width, "the terms of this run")), ACCENT),
        (" Enter runs the proof into the folder named in the writes: term "
         "below.", EMPHASIS),
        (" Esc goes back without running; q leaves.", PLAIN),
        ("", PLAIN),
    ]
    body += _strip_rows(view, width)
    body.append((_chrome(view, _rule(view, width)), ACCENT))
    preflight: list[Row] = []
    for line in wizard.preflight_lines(view.path_field):
        preflight.extend(
            (segment, _record_role(line))
            for segment in expand_lines([line], width)
        )
    body += preflight
    body.append((_chrome(view, _rule(view, width)), ACCENT))
    return _finalize(view, body, width)


def _terms_body(
    view: ViewModel, width: int, rows: int
) -> tuple[list[Row], str, bool]:
    visual = terms_visual_rows(view, width)
    total = len(visual)
    maximum = max(0, total - rows)
    start = max(0, min(view.scroll, maximum))
    window = visual[start : start + rows]
    position = f"rows {start + 1}-{min(start + rows, total)} of {total}"
    return window, position, (start >= maximum)


def _refused_body(view: ViewModel, width: int, rows: int) -> list[Row]:
    fail = glyph(view, "fail")
    body: list[Row] = [
        (_chrome(view, f" {fail} RUN REFUSED - the working folder was "
         "preserved"), DANGER),
        (_chrome(view, _rule(view, width)), ACCENT),
        ("", PLAIN),
    ]
    for segment in expand_lines(view.diagnostics, width - 2):
        body.append((f" {segment}", PLAIN))
    body += [
        ("", PLAIN),
        ("  e  choose another folder    q  leave", PLAIN),
    ]
    return _finalize(view, body, width)[:rows]


def _read_refused_body(view: ViewModel, width: int, rows: int) -> list[Row]:
    fail = glyph(view, "fail")
    body: list[Row] = [
        (_chrome(view, f" {fail} RENDERER REFUSAL - this folder did not "
         "verify"), DANGER),
        (_chrome(view, _rule(view, width)), ACCENT),
        (" The completed-run renderer refused this folder; its reason:",
         PLAIN),
    ]
    for segment in expand_lines(view.diagnostics[-2:], width - 4):
        body.append((f"   {segment}", DANGER))
    body += [
        ("", PLAIN),
        (" No proof ladder or closed stamp is shown for an unverified "
         "folder.", PLAIN),
        (" A folder whose run was interrupted before its receipt exists is "
         "refused", PLAIN),
        (" by design; that refusal is correct behavior.", PLAIN),
        ("", PLAIN),
        ("  v  choose another run    q  leave", PLAIN),
    ]
    return _finalize(view, body, width)[:rows]


def _record_screen(
    view: ViewModel, width: int, height: int
) -> tuple[list[Row], str]:
    """Shared body for RUNNING / CLOSED / FAILED / READ screens.

    The stop plate (a chrome band) renders above the pane on RUNNING when
    the boundary earned its three known codes; the colophon (double rule +
    couplet) renders below the pane on a verified close. Both are chrome —
    the pane's record lines stay verbatim."""
    top_band: list[Row] = []
    bottom_band: list[Row] = []
    if view.screen == RUNNING and stop_plate_eligible(view):
        top_band = _stop_plate_rows(view, width)
    if view.screen == CLOSED and view.render_verified:
        bottom_band = _colophon_rows(view, width)

    body_rows = max(1, height - 3 - len(top_band) - len(bottom_band))
    lines = view.active_lines()
    main: list[Row] = []
    if width >= SPLIT_WIDTH and view.screen != READ:
        pane_width = width - RAIL_WIDTH - 2
        rail = _rail_rows(view, body_rows)
        window, position = _pane_window(lines, view, body_rows, pane_width)
        pane = _pane_rows(window, pane_width)
        for i in range(body_rows):
            rail_text, rail_role = (
                rail[i] if i < len(rail) else (" " * RAIL_WIDTH, PLAIN)
            )
            pane_text, pane_role = pane[i] if i < len(pane) else ("", PLAIN)
            merged = f"{rail_text}| {pane_text}"
            role = pane_role if pane_role != PLAIN else rail_role
            main.append((_pad(merged, width), role))
    else:
        strip = _strip_rows(view, width) if view.screen != READ else []
        pane_rows = max(1, body_rows - len(strip))
        window, position = _pane_window(lines, view, pane_rows, width)
        main += strip
        main += _pane_rows(window, width)
    return top_band + main + bottom_band, position


def _help_lines(view: ViewModel) -> list[str]:
    return [
        "",
        " global    ?  toggle help    q  leave    Ctrl-C  interrupt cleanly",
        " start     r run   v read   p plan and terms",
        "           up/down select a completed run; PgUp/PgDn page shelf;",
        "           Enter opens the visible selected run",
        " folder    type to edit; backspace deletes; Ctrl-U clears;",
        "           Enter continues; Esc goes back (q types into the",
        "           path here; press Esc first to leave with q)",
        " terms     j/k or PgUp/PgDn review; G final terms; Enter runs",
        "           only after the final term is visible; Esc goes back",
        " record    j/k or arrows scroll; PgUp/PgDn page; g top; G bottom;",
        "           n/p jump between record sections and earned entries",
        " closed    o outputs of this run",
        " read      the same scroll and section keys, over the verified log",
        "",
        " Chrome is a window over the record, never a second copy: the "
        "glyphs,",
        " rules, and Bindery voice carry welcome; plain language carries "
        "every",
        " state, code, path, and proof fact. --ascii swaps the glyphs for "
        "words;",
        " --mono / NO_COLOR drop the colour. The stdout record and the "
        "plain",
        " surfaces carry everything at any size:",
        *PLAIN_COMMANDS,
        " Screen readers and pipes reach the same facts there, with no",
        " cursor-control codes.",
    ]


def _outputs_lines(view: ViewModel) -> list[str]:
    lines = [
        "",
        f" working folder (as typed): {view.run_dir_display}",
        "",
        " receipt      journey.receipt.json (renderer re-verified at close)",
        " summary      JOURNEY_SUMMARY.md",
    ]
    if view.events_file_written:
        lines.append(
            " events       journey.events.ndjson (testimony; not "
            "receipt-verified)"
        )
    labels = (
        ("package", "rebind.package_zip"),
        ("settings", "rebind.settings_receipt"),
        ("readiness", "check_unbound_readiness.report"),
        ("  record", "check_unbound_readiness.record"),
        ("compose", "check_compose_readiness.report"),
        ("  record", "check_compose_readiness.record"),
        ("validation", "seal_validate.validation_note"),
        ("equivalence", "seal_local_equivalence.report"),
    )
    for label, key in labels:
        value = view.facts.get(key)
        if value:
            lines.append(f" {label:<12} {value}")
    lines += [
        "",
        " Paths are relative to the working folder. Artifact hashes were",
        " re-verified by the renderer at close; the .md reports are the",
        " operator-facing companions of their .json records.",
        "",
        " The verify command is printed in the close block of the record",
        " (and again on stdout after this screen closes).",
        "",
        " This workbench does not import packages into Brightspace.",
    ]
    return lines


def _overlay_frame(view: ViewModel, width: int, height: int) -> list[Row]:
    if view.overlay == OVERLAY_HELP:
        title, lines = " HELP - keys and surfaces", _help_lines(view)
        hint = "? or Esc close help"
    elif view.overlay == OVERLAY_PLAN:
        import quiz_binder_wizard as wizard

        title = " RUN PLAN AND TERMS (the plain wizard prints these exact lines)"
        display = view.path_field or "<your empty working folder>"
        lines = (
            wizard.header_lines()
            + wizard.plan_lines()
            + wizard.preflight_lines(display)
        )
        hint = "Esc close plan"
    else:
        title, lines = " OUTPUTS OF THIS RUN", _outputs_lines(view)
        hint = "o or Esc close outputs"
    rows: list[Row] = [
        (_safe_pad(title, width), EMPHASIS),
        (_pad(_rule(view, width), width), ACCENT),
    ]
    pane_rows = max(1, height - 3)
    visual = expand_lines(lines, width)
    total = len(visual)
    start = max(0, min(view.overlay_scroll, max(0, total - pane_rows)))
    window = visual[start : start + pane_rows]
    rows += _pane_rows(window, width)
    while len(rows) < height - 1:
        rows.append((_pad("", width), PLAIN))
    position = f"lines {start + 1}-{min(start + pane_rows, total)} of {total}"
    rows.append(_statusbar(f"{hint}   j/k scroll  PgUp/PgDn page", position, width))
    return rows


def _fallback_frame(view: ViewModel, width: int, height: int) -> list[Row]:
    """The deliberate below-minimum notice. Its two plain-wizard commands
    are wrapped, never cropped: at the width where the fallback matters
    most, the copy-paste lifeline must stay copy-pasteable (r3 friction)."""
    safe_width = max(1, width)
    text_lines = [
        "QUIZ BINDER",
        f"This full-screen workbench needs at least {MIN_WIDTH}x{MIN_HEIGHT};"
        f" the window is {width}x{height}.",
        "Resize to continue - the session state is kept - or press q to "
        "leave.",
        "The plain surfaces work at any size:",
    ]
    rows: list[Row] = []
    for line in text_lines:
        rows.extend(
            (_safe_pad(segment, safe_width), PLAIN)
            for segment in _wrap_segments(line, safe_width)
        )
    for command in PLAIN_COMMANDS:
        rows.extend(
            (_safe_pad(segment, safe_width), EMPHASIS)
            for segment in _wrap_segments(command.strip(), safe_width)
        )
    return rows[: max(1, height)]


def render(view: ViewModel, width: int, height: int) -> list[Row]:
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return _fallback_frame(view, width, height)
    if view.overlay is not None:
        return _overlay_frame(view, width, height)

    rows = _header(view, width)
    body_rows = height - 3
    position = ""

    if view.screen == HOME:
        body = _home_body(view, width, body_rows)
        status = _statusbar(
            "start - nothing has run",
            "r run  v read  p plan  ? map  q leave",
            width,
        )
    elif view.screen in (PATH_RUN, PATH_READ):
        body = _path_body(view, width, body_rows)
        status = _statusbar(
            "choose a folder (typed as it will appear in the record)",
            "Enter continue  Esc back",
            width,
        )
    elif view.screen == TERMS:
        body, position, at_end = _terms_body(view, width, body_rows)
        if at_end:
            left = view.terms_note or "terms reviewed"
            right = f"Enter run  Esc back  {position}"
        else:
            left = view.terms_note or "review every term before running"
            right = f"j/k scroll  G final terms  {position}"
        status = _statusbar(left, right, width)
    elif view.screen == REFUSED:
        body = _refused_body(view, width, body_rows)
        status = _statusbar(
            "refused: working folder not empty - folder preserved",
            "e choose another  q leave",
            width,
        )
    elif view.screen == READ_REFUSED:
        body = _read_refused_body(view, width, body_rows)
        status = _statusbar(
            "renderer refused this folder - no ladder is shown",
            "v choose another  q leave",
            width,
        )
    else:
        body, position = _record_screen(view, width, height)
        if view.screen == RUNNING:
            left = (
                view.liveness.lstrip(". ")
                if view.liveness
                else f"{view.completed_count()} of {view.total} entries completed"
            )
            status = _statusbar(left, "Ctrl-C interrupts cleanly", width)
        elif view.screen == FAILED:
            status = _statusbar(
                "FAIL recorded - no completion for the failed step",
                f"j/k scroll  n/p section  q leave  {position}",
                width,
            )
        elif view.screen == READ:
            status = _statusbar(
                "read-only - receipt verified by the renderer",
                f"j/k scroll  n/p section  g/G ends  q leave  {position}",
                width,
            )
        elif view.screen == CLOSED and view.render_verified:
            status = _statusbar(
                "RUN RECORD CLOSED - renderer verified",
                f"j/k scroll  n/p section  o outputs  q leave  {position}",
                width,
            )
        else:
            status = _statusbar(
                "RUN RECORD NOT CLOSED - renderer refused; do not import",
                f"j/k scroll  n/p section  q leave  {position}",
                width,
            )

    while len(body) < body_rows:
        body.append((_pad("", width), PLAIN))
    return rows + body[:body_rows] + [status]
