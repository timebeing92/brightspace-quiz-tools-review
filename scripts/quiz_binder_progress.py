#!/usr/bin/env python3
"""Progress-event emission for the Quiz Binder synthetic journey.

Grammar: a same-grammar draft sibling of ``coursecraft.progress/1`` (the
ratified NDJSON pipeline-progress contract authored in the blueprint bundle
and consumed by the blueprint terminal wizard and the Workshop hub):

    run_start -> (step_start step_end){9} -> run_end

with quiz-specific additive keys and one quiz-specific extra event kind
(``journey_note``). Consumers follow the progress/1 consumer rules: non-JSON
lines pass through, unknown keys are ignored, unknown event kinds are
ignored or rendered literally, never given invented prose.

Truth rules enforced here, not merely promised:

- every artifact row carries a run-relative path and a SHA-256 computed
  from the file on disk at emission time — never copied from intent;
- absolute paths are refused at emission (events are content-minimized to
  the same standard as the journey receipt);
- events describe only what has already happened; nothing is emitted for
  a step that has not completed except its ``step_start``.

The schema id is deliberately ``brightspace-quiz-bundle.quiz-progress/0``:
the ratified panel decision names ``coursecraft.quiz_progress/1`` as the
eventual sibling contract, but minting that name remains a separate
Workbench/operator ratification. See the r2 event-boundary proposal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, IO

from quiz_binder_journey_plan import (
    BOUNDARY_STEP_KEY,
    PROGRESS_SCHEMA,
    RECEIPT_FORMAT,
    STEP_LABELS,
    STEP_PLAN,
    STEP_TOTAL,
    step_index,
)


EVENT_KINDS = ("run_start", "step_start", "step_end", "journey_note", "run_end")
PATH_FIELDS = {
    "path",
    "record",
    "report",
    "package_zip",
    "settings_receipt",
    "validation_note",
    "receipt",
    "summary",
}

EventSink = Callable[[dict[str, Any]], None]


class EventEmissionError(RuntimeError):
    """Raised when an event would state something the disk does not back."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_artifact(path: Path, run_root: Path) -> dict[str, str]:
    """Build a verified artifact row: relative path + on-disk SHA-256."""
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(run_root.resolve()).as_posix()
    except ValueError as exc:
        raise EventEmissionError(
            f"event artifact escapes the run directory: {path}"
        ) from exc
    if not resolved.is_file():
        raise EventEmissionError(f"event artifact does not exist: {rel}")
    return {"path": rel, "sha256": sha256_file(resolved)}


class ProgressEmitter:
    """Composes and fans out journey progress events.

    ``sinks`` are callables receiving each event dict; ``streams`` are
    text streams receiving one JSON line per event (NDJSON), flushed
    immediately so a consumer sees an event no later than the work it
    describes has finished.
    """

    def __init__(
        self,
        run_root: Path,
        *,
        sinks: tuple[EventSink, ...] = (),
        streams: tuple[IO[str], ...] = (),
    ) -> None:
        self.run_root = run_root
        self._sinks = tuple(sinks)
        self._streams = tuple(streams)
        self._step_started_at: dict[str, float] = {}
        self._run_end_emitted = False
        self.emitted: list[dict[str, Any]] = []

    # -- plumbing ---------------------------------------------------------

    def _emit(self, payload: dict[str, Any]) -> None:
        self._reject_absolute_paths(payload)
        self.emitted.append(payload)
        for sink in self._sinks:
            sink(payload)
        for stream in self._streams:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()

    def _reject_absolute_paths(self, payload: Any, field: str | None = None) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                self._reject_absolute_paths(value, key)
        elif isinstance(payload, list):
            for value in payload:
                self._reject_absolute_paths(value, field)
        elif isinstance(payload, str):
            if payload.startswith(("/", "\\")) or (
                len(payload) > 2 and payload[1] == ":" and payload[2] in "/\\"
            ):
                raise EventEmissionError(
                    f"event payload carries an absolute path: {payload!r}"
                )
            if field in PATH_FIELDS:
                parts = payload.split("/")
                if "\\" in payload or any(part in ("", ".", "..") for part in parts):
                    raise EventEmissionError(
                        f"event payload carries an unsafe relative path: {payload!r}"
                    )

    def artifact(self, path: Path) -> dict[str, str]:
        return relative_artifact(path, self.run_root)

    # -- event kinds ------------------------------------------------------

    def run_start(self) -> None:
        self._emit(
            {
                "event": "run_start",
                "schema": PROGRESS_SCHEMA,
                "label": "Quiz Binder synthetic journey",
                "total": STEP_TOTAL,
                "steps": list(STEP_LABELS),
                "step_keys": [step["key"] for step in STEP_PLAN],
                "doors": {
                    "unbind": [
                        step["key"] for step in STEP_PLAN if step["door"] == "unbind"
                    ],
                    "compose": [
                        step["key"] for step in STEP_PLAN if step["door"] == "compose"
                    ],
                },
                "receipt_format": RECEIPT_FORMAT,
                "fixture_policy": "synthetic_only",
                "proof_ceiling": "validated_locally",
                "boundary_step": BOUNDARY_STEP_KEY,
            }
        )

    def step_start(self, key: str) -> None:
        index = step_index(key)
        self._step_started_at[key] = time.monotonic()
        self._emit(
            {
                "event": "step_start",
                "schema": PROGRESS_SCHEMA,
                "index": index,
                "total": STEP_TOTAL,
                "step": key,
                "label": STEP_LABELS[index - 1],
                "door": next(s["door"] for s in STEP_PLAN if s["key"] == key),
                "act": next(s["act"] for s in STEP_PLAN if s["key"] == key),
            }
        )

    def step_end(
        self,
        key: str,
        *,
        status: str,
        exit_code: int,
        artifacts: tuple[Path, ...] = (),
        facts: dict[str, Any] | None = None,
    ) -> None:
        if status not in ("ok", "error"):
            raise EventEmissionError(f"step_end status must be ok|error: {status}")
        index = step_index(key)
        started = self._step_started_at.get(key)
        seconds = round(time.monotonic() - started, 3) if started else None
        payload: dict[str, Any] = {
            "event": "step_end",
            "schema": PROGRESS_SCHEMA,
            "index": index,
            "total": STEP_TOTAL,
            "step": key,
            "label": STEP_LABELS[index - 1],
            "status": status,
            "exit_code": exit_code,
            "seconds": seconds,
            "artifacts": [self.artifact(path) for path in artifacts],
        }
        if facts:
            payload.update(facts)
        self._emit(payload)

    def journey_note(
        self,
        note: str,
        *,
        artifacts: tuple[Path, ...] = (),
        facts: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "journey_note",
            "schema": PROGRESS_SCHEMA,
            "note": note,
            "artifacts": [self.artifact(path) for path in artifacts],
        }
        if facts:
            payload.update(facts)
        self._emit(payload)

    def run_end(
        self,
        *,
        status: str,
        outcome: str,
        facts: dict[str, Any] | None = None,
    ) -> None:
        if status not in ("ok", "partial", "error"):
            raise EventEmissionError(f"run_end status must be ok|partial|error: {status}")
        if outcome not in ("completed", "refused", "failed", "interrupted"):
            raise EventEmissionError(f"unknown run_end outcome: {outcome}")
        if self._run_end_emitted:
            # An interrupt can land during the completed run_end emission;
            # a second, contradicting run_end must never follow the first.
            return
        self._run_end_emitted = True
        payload: dict[str, Any] = {
            "event": "run_end",
            "schema": PROGRESS_SCHEMA,
            "status": status,
            "outcome": outcome,
        }
        if facts:
            payload.update(facts)
        self._emit(payload)
