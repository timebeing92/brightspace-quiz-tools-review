# Installation

Quiz Bundle uses a local Python virtual environment and direct,
inspectable script entry points. It is not a background service and does not
contact Brightspace during installation or use.

## Python

- Supported: Python 3.11, 3.12, or 3.13.
- The assessment-review candidate has been checked locally with Python 3.13.
- Python 3.14 is not yet admitted.

## Create the environment

From the cloned repository or extracted ZIP folder:

```bash
python3.13 scripts/bootstrap_env.py --locked
```

The default environment is `.venv/`. The bootstrap script does not delete an
existing environment or write outside the selected path. Replace `python3.13`
with the local name of a supported interpreter if needed.

Run commands from the repository/extracted bundle folder. On Windows, use
`.venv\Scripts\python.exe` wherever these instructions show `.venv/bin/python`.
The initial bootstrap downloads Python dependencies. Extraction, review and
building then run locally without a Google Drive or Brightspace login.

## Start the guided workflow

```bash
.venv/bin/python scripts/quiz_terminal.py
```

Choose **Unbind a Brightspace export for review**. The wizard asks for the ZIP
or unpacked export and a new output folder, then shows the exact reads, writes,
asset behavior, and no-network boundary before it runs.

The new workflow contains:

- a verified Unbind receipt and content-minimized status report;
- a local authored-content Reading Room;
- a detailed workbook and a reviewer-facing workbook;
- `compose/reviewer_working.xlsx`, with START HERE and editable assessment tabs;
- `compose/reviewer_baseline_DO_NOT_EDIT.xlsx`, which preserves source state;
- `compose/Images/`, a model, a native baseline and a layout manifest for portable review;
- a workflow record used to re-verify evidence before later steps.

Run the wizard again and choose **Continue a reviewed workflow with Compose**
after proposals and approvals are saved. Compose can check one, several, or all
quizzes and produces a separate readiness report for each. Rebind is offered
one quiz/package at a time only when that exact report is ready.

Choose **Rebind an already reviewed, ready quiz** to resume that stage later.
The wizard checks the prior Compose files, lets you select a ready quiz and a
new output folder, and confirms the local build. **Quit** closes the wizard.

Review names, reasons and dates may be blank by default. Explicit acceptance is
still required to apply a revision; missing metadata never creates acceptance.
Choose the required metadata policy in Compose if your review process needs it.
Saving workbook changes after Compose requires another Compose run before
Rebind. The tool checks file hashes, so even a presentation-only save counts as
a changed workbook; current row/column visibility is not rewritten by Compose.

Unbind itself defaults to the complete export: all quizzes and the available
Question Library are parsed together. This is necessary because quiz XML often
contains placements or references while reusable question bodies live in
`questiondb.xml`. Quiz selection controls later readiness work; it does not
discard library evidence.

Detailed commands and boundaries are in
[docs/TERMINAL_WORKFLOW.md](docs/TERMINAL_WORKFLOW.md).

## Scripted workflow

```bash
.venv/bin/python scripts/quiz_terminal.py unbind /path/to/export.zip \
  --workspace output/course__quiz_workflow
.venv/bin/python scripts/quiz_terminal.py status output/course__quiz_workflow
.venv/bin/python scripts/quiz_terminal.py compose output/course__quiz_workflow \
  --all-quizzes
.venv/bin/python scripts/quiz_terminal.py rebind output/course__quiz_workflow \
  --quiz-entity-key 'cc:quiz:...'
```

By default, available source images/files are copied into the local evidence
run and assessment review folder. Keep the complete `compose/` folder together
so workbook image links and re-import continue to work. `--asset-mode reference`
retains the older native workbook layout and source file links; it does not
create the portable grouped packet.

An Unbind workspace contains authored questions and answers. Share the review
folder or its Reading Room export intentionally; do not publish the entire
workspace without reviewing its contents.

## Verify this snapshot

The demonstration uses the synthetic fixtures included in this repository.
Choose a new output directory each time:

```bash
.venv/bin/python scripts/run_synthetic_journey.py --output-dir output/install-check
.venv/bin/python scripts/render_quiz_binder_log.py --run-dir output/install-check
.venv/bin/python scripts/vendor_from_workbench.py --check
.venv/bin/python scripts/make_release_asset.py --check-only
```

For the focused synthetic reviewer suite, create the environment with test
dependencies and run:

```bash
python3.13 scripts/bootstrap_env.py --locked --dev
.venv/bin/python -m pytest -q tests/test_quiz_sme_edit_scenarios.py
```

On Windows, use `py -3.13` for bootstrap and
`.venv\Scripts\python.exe -m pytest -q tests/test_quiz_sme_edit_scenarios.py`
for the focused suite. It has 21 synthetic reviewer scenarios, all passing on
the promoted candidate. To run the broader included regression corpus, use
`.venv/bin/python -m pytest -q tests` (Windows:
`.venv\Scripts\python.exe -m pytest -q tests`). The included corpus covers
the quiz tooling and contains synthetic fixtures; it is not the full private
Workbench corpus and does not reproduce its full test result. At this snapshot,
637 tests collect: 636 pass and one skips because its private import-receipt
log is not included.

## Advanced synthetic proof

```bash
.venv/bin/python scripts/quiz_binder_tui.py \
  --output-dir output/synthetic-journey
```

The full-screen proof requires a POSIX TTY with at least 80×12 cells. `--plain`
uses its linear screen-reader/automation surface, `--ascii` substitutes ASCII
chrome, and `--mono` removes color.

## Dependency and license policy

- `requirements.txt` gives compatible runtime ranges.
- `requirements-lock.txt` records the exact runtime resolution.
- `requirements-dev.txt` adds the source-tree test runner.
- `sbom/runtime.cdx.json` is the CycloneDX runtime inventory.

The included code uses AGPL-3.0-or-later with commercial terms available
by agreement. See `LICENSE`, `LICENSE_POSTURE.md`, and `COMMERCIAL.md`.
