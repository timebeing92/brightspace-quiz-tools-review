#!/usr/bin/env python3
"""Terminal driver for the Quiz Binder full-screen workbench.

Owns everything byte-level so the rest of the candidate stays pure:
capability probing, cbreak mode, the alternate screen, frame writing,
key decoding, and resize wake-ups.

Channel law (02 §T12): every frame and cursor-control byte goes to
**stderr**, the liveness channel. stdout is reserved for the record,
written once by the entry point after the alternate screen has closed.
A non-TTY process never constructs this driver at all, so a pipe can
never receive cursor controls from this module.

Color law (03 §4): ANSI-16 SGR only, whole lines only, chosen from the
role table; `NO_COLOR`, `TERM=dumb`, or `--mono` disables tinting while
leaving the text identical.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys

try:  # The plain wizard remains available on non-POSIX platforms.
    import termios
    import tty
except ImportError:  # pragma: no cover - exercised by platform CI/users
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

ENTER_ALT = "\x1b[?1049h\x1b[?25l"
LEAVE_ALT = "\x1b[?25h\x1b[?1049l"
HOME_CURSOR = "\x1b[H"
CLEAR_LINE_RIGHT = "\x1b[K"
CLEAR_BELOW = "\x1b[J"
RESET = "\x1b[0m"

SGR = {
    "plain": "",
    "emphasis": "\x1b[1m",
    "statusbar": "\x1b[7m",
    "good": "\x1b[32m",
    "boundary": "\x1b[36m",
    "danger": "\x1b[1;31m",
    "muted": "\x1b[2m",
}

KEY_ESCAPES = {
    "[A": "up",
    "[B": "down",
    "[C": "right",
    "[D": "left",
    "[H": "home",
    "[F": "end",
    "[5~": "pgup",
    "[6~": "pgdn",
    "[1~": "home",
    "[4~": "end",
    "[3~": "delete",
    "OA": "up",
    "OB": "down",
    "OC": "right",
    "OD": "left",
}


def is_interactive() -> bool:
    """The workbench needs an interactive keyboard and an interactive
    liveness channel; stdout may be a pipe (the record still lands there)."""
    return (
        termios is not None
        and tty is not None
        and os.name == "posix"
        and os.environ.get("TERM", "") not in ("", "dumb")
        and sys.stdin.isatty()
        and sys.stderr.isatty()
    )


def color_enabled(mono_flag: bool) -> bool:
    if mono_flag:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    return True


def encode_frame(rows: list[tuple[str, str]], *, color: bool) -> str:
    """The exact byte sequence a frame paints: home the cursor, write
    each row with erase-to-end, erase below. Pure, so committed session
    streams are reproducible byte-for-byte."""
    parts = [HOME_CURSOR]
    last = len(rows) - 1
    for index, (text, role) in enumerate(rows):
        code = SGR.get(role, "") if color else ""
        if code:
            parts.append(code + text + RESET)
        else:
            parts.append(text)
        parts.append(CLEAR_LINE_RIGHT)
        if index != last:
            parts.append("\r\n")
    parts.append(CLEAR_BELOW)
    return "".join(parts)


def terminal_size() -> tuple[int, int]:
    """Probe the interactive channels, not stdout: stdout may be a pipe
    (the record still lands there) while frames live on stderr."""
    for stream in (sys.stderr, sys.stdin):
        try:
            size = os.get_terminal_size(stream.fileno())
        except (OSError, ValueError):
            continue
        return size.columns, size.lines
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns, size.lines


class TerminalDriver:
    """Raw-mode lifecycle, frame writing, and key/resize reading."""

    def __init__(self, *, mono: bool) -> None:
        self.color = color_enabled(mono)
        self._fd = sys.stdin.fileno()
        self._saved: list | None = None
        self._wake_read: int | None = None
        self._wake_write: int | None = None
        self._old_wakeup = -1
        self._old_handler = None
        self._handler_changed = False
        self._wakeup_changed = False
        self._cbreak = False
        self._alt_screen = False
        self._active = False
        self._closed = False

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "TerminalDriver":
        if termios is None or tty is None:
            raise RuntimeError("full-screen terminal support is unavailable")
        try:
            self._wake_read, self._wake_write = os.pipe()
            os.set_blocking(self._wake_write, False)
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._cbreak = True
            if hasattr(signal, "SIGWINCH"):
                self._old_handler = signal.signal(
                    signal.SIGWINCH, lambda *_: None
                )
                self._handler_changed = True
                self._old_wakeup = signal.set_wakeup_fd(
                    self._wake_write, warn_on_full_buffer=False
                )
                self._wakeup_changed = True
            self._write(ENTER_ALT)
            self._alt_screen = True
            self._active = True
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active = False
        if self._alt_screen:
            self._write(LEAVE_ALT + RESET)
            self._alt_screen = False
        if self._wakeup_changed:
            try:
                signal.set_wakeup_fd(self._old_wakeup)
            except (OSError, ValueError):
                pass
            self._wakeup_changed = False
        if self._handler_changed and self._old_handler is not None:
            try:
                signal.signal(signal.SIGWINCH, self._old_handler)
            except (OSError, ValueError):
                pass
            self._handler_changed = False
        if self._cbreak and self._saved is not None and termios is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except (OSError, ValueError):
                pass
            self._cbreak = False
        # Drop any keys typed while no screen was reading them, so a
        # buffered keystroke can never fire an action the operator did
        # not see themselves take.
        if termios is not None:
            try:
                termios.tcflush(self._fd, termios.TCIFLUSH)
            except (OSError, ValueError):
                pass
        for descriptor in (self._wake_read, self._wake_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self._wake_read = None
        self._wake_write = None

    def flush_typeahead(self) -> None:
        if termios is None:
            return
        try:
            termios.tcflush(self._fd, termios.TCIFLUSH)
        except (OSError, ValueError):
            pass

    # -- output -----------------------------------------------------------

    def _write(self, data: str) -> None:
        try:
            sys.stderr.write(data)
            sys.stderr.flush()
        except (OSError, ValueError):
            # The terminal vanished mid-session. Frames are best-effort
            # liveness; the stdout record and on-disk truth are unaffected,
            # so the session finishes its honest close instead of dying
            # in a traceback.
            pass

    def write_frame(self, rows: list[tuple[str, str]]) -> None:
        self._write(encode_frame(rows, color=self.color))

    # -- input ------------------------------------------------------------

    def read_key(self, timeout: float | None = None) -> str | None:
        """One decoded key, ``"resize"``, or None on timeout.

        Ctrl-C is delivered by cbreak mode as SIGINT and surfaces as
        KeyboardInterrupt in the caller; it never appears as a key here.
        """
        ready, _, _ = select.select(
            [
                descriptor
                for descriptor in (self._fd, self._wake_read)
                if descriptor is not None
            ],
            [],
            [],
            timeout,
        )
        if self._wake_read is not None and self._wake_read in ready:
            try:
                os.read(self._wake_read, 512)
            except OSError:
                pass
            return "resize"
        if self._fd not in ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return "eof"
        byte = data[0]
        if byte == 0x1B:
            return self._read_escape()
        if byte in (0x0D, 0x0A):
            return "enter"
        if byte in (0x7F, 0x08):
            return "backspace"
        if byte == 0x15:
            return "ctrl_u"
        if byte == 0x09:
            return "tab"
        if 0x20 <= byte < 0x7F:
            return chr(byte)
        return "unknown"

    def _read_escape(self) -> str:
        sequence = ""
        while len(sequence) < 8:
            ready, _, _ = select.select([self._fd], [], [], 0.05)
            if not ready:
                break
            sequence += os.read(self._fd, 1).decode("ascii", "replace")
            if sequence in KEY_ESCAPES:
                return KEY_ESCAPES[sequence]
            if sequence.startswith("[") and sequence[-1] == "~":
                return KEY_ESCAPES.get(sequence, "unknown")
        return "esc" if not sequence else KEY_ESCAPES.get(sequence, "unknown")
