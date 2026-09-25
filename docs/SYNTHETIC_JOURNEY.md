# Synthetic two-door journey

The release-candidate journey is executable proof, not a simulated product
tour. It uses only committed synthetic fixtures and performs no network or
Brightspace operation.

Run it with:

```bash
.venv/bin/python scripts/run_synthetic_journey.py \
  --output-dir output/synthetic-journey
```

After the run completes, render its verified plain log with:

```bash
.venv/bin/python scripts/render_quiz_binder_log.py \
  --run-dir output/synthetic-journey
```

To watch the run as a live record instead, use the wizard:

```bash
.venv/bin/python scripts/quiz_binder_wizard.py \
  --output-dir output/synthetic-journey
```

The wizard prints the canonical nine-step plan and the run's terms, then
streams one earned posting per completed step (liveness goes to stderr, so
piped stdout is deterministic). The expected stop at the readiness boundary
is pre-declared as a scheduled stop and recorded at the same volume as
success. Live postings say "SHA-256 recorded"; only the completed-run
renderer, which the wizard invokes at its close, says "verified". Ctrl-C
closes cleanly: completed entries stay on disk, the interrupted step's
residue is named untrusted, and the exit code is 130. A receipt normally does
not exist; if interruption lands after it is written, the wizard names the
renderer as the arbiter. `--read RUN_DIR` replays a completed run through the
verified renderer unchanged.

The full-screen workbench (`scripts/quiz_binder_tui.py`) is a window
onto this same record: frames go to stderr only, the engine remains the
only executor, and the stdout record written after the alternate screen
closes is byte-identical to the wizard's for the same run. A non-TTY
invocation delegates to the wizard and emits no cursor controls; a
renderer refusal shows no proof ladder and no closed stamp.

The rc.4 Bindery Ledger register changes only this temporary presentation:
named stations, terms and stop plates, a renderer-authorized close colophon,
and a brass accent sit around the unchanged record. `--ascii` substitutes
ASCII chrome and `--mono` removes color; both retain the same words, states,
plain fallback, and stdout bytes.

The full-screen driver is enabled only for a POSIX stdin/stderr TTY with a
usable terminal type; `TERM=dumb`, non-POSIX systems, pipes, and `--plain`
delegate to the linear surface. At short heights the terms page is scrollable,
and execution stays disabled until its final line is visible. Dynamic paths,
diagnostics, and opt-in shelf names are escaped before frame rendering so
filesystem text cannot become terminal control input. A process terminated by
terminal death or SIGHUP cannot promise a final stdout record; completed disk
evidence remains untrusted unless the completed-run renderer later verifies a
receipt.

The renderer is read-only. It requires `journey.receipt.json`, verifies every
artifact named and hashed by that receipt, and then reads the readiness JSON
and nested build receipt for the displayed facts. A missing receipt means the
run is incomplete; the renderer exits 2 without narrating success or changing
the directory. Unknown blocker codes remain literal and fall back to the
operator-facing readiness report instead of rendering unreviewed prose.

## Door 1: Unbind and review

The journey extracts the mixed-storage fixture through the real canonical
extractor, preserves the generated reviewer workbook as an immutable baseline,
creates a separate edited copy, records one accepted permanent-code proposal
with distinct synthetic proposer and approver identities, re-ingests it, and
promotes the accepted decision into a new model copy. It also renders the local
review station.

The readiness check is expected to stop this lane. Extracted question instances
remain `extraction_only`; they are evidence and are not silently upgraded to
build-approved authoring records. The receipt names the blocker codes.

## Door 2: Compose and rebind

The journey sends the build-approved four-question synthetic authoring model
and settings receipt through readiness, package assembly, asset copying, run
receipt generation, strict validation, and ZIP creation.

## Seal

The generated folder and ZIP are structurally compared and must have zero
breaks. This proves local package closure and archive equivalence. It is not a
Brightspace import/re-export receipt and the summary says so explicitly.

## Live progress events (draft)

`--progress-events` makes the runner emit an NDJSON event stream to stdout
(`run_start`, `step_start`/`step_end` pairs, one `journey_note` marking the
proof's simulated marginalia decision, `run_end`), and `--events-file`
creates the same stream as a sidecar. The draft schema id is
`brightspace-quiz-bundle.quiz-progress/0`
(`docs/reference/quiz_progress_draft.schema.json`), a same-grammar sibling
of `coursecraft.progress/1`; ratifying `coursecraft.quiz_progress/1` from
it remains a separate Workbench/operator decision. This bundle draft does not
perform that ratification. Event
artifact rows carry run-relative paths and SHA-256 values computed from
disk at emission; the stream is testimony, and `journey.receipt.json`
remains the only proof. Without these flags the runner's stdout is exactly
one JSON line on success, unchanged.

## Output contract

Each run writes:

- `JOURNEY_SUMMARY.md` for people;
- `journey.receipt.json` with the format
  `brightspace-quiz-bundle.synthetic-journey/1`;
- the Unbind model, baseline and edited workbooks, overlay, promoted model,
  promotion receipt, review station, and readiness report;
- the Compose readiness report, package folder and ZIP, build receipts,
  validation note, and local-equivalence report.

The receipt is content-minimized. It records counts, states, entrypoints,
artifact paths, and SHA-256 values, but not authored question or answer text.

The completed-run log preserves the receipt's step names, readiness states,
blocker codes, counts, report paths, and local proof boundary. Registered L4
capability evidence cannot raise this run above **Validated locally** because
the run itself records `brightspace_roundtrip: not_performed`.
