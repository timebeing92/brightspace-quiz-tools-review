# Brightspace Quiz Tools

Extract quizzes from a Brightspace course export, review questions in an editable
spreadsheet, collect approved changes, and build an import package when the
questions and settings meet the tool's supported build requirements.

This repository includes the Python source and helper modules, two terminal
interfaces, standalone commands, blank drafting templates, and synthetic examples.
It runs locally. A Brightspace or Google Drive account is not needed to run the
tools; installation downloads Python dependencies.

## Start here

You need **Python 3.11, 3.12, or 3.13**. This development snapshot was verified with
Python 3.13. Python 3.14 is not supported yet.

Clone this repository, or download and extract its ZIP, then open a terminal in
the resulting folder:

```sh
git clone https://github.com/timebeing92/brightspace-quiz-tools-review.git
cd brightspace-quiz-tools-review
```

### macOS or Linux

```sh
python3.13 scripts/bootstrap_env.py --locked
.venv/bin/python scripts/run_synthetic_journey.py --output-dir output/first-run
```

### Windows PowerShell

```powershell
py -3.13 scripts/bootstrap_env.py --locked
.venv\Scripts\python.exe scripts/run_synthetic_journey.py --output-dir output/first-run
```

Use the name of your installed supported Python interpreter if it differs.
The bootstrap creates a local `.venv` environment. You do not need to activate it
when using the interpreter paths shown here.

The first run uses included synthetic questions. It extracts a review workbook,
collects an example edit, and validates a package built from a separate approved
fixture. Its source-extraction branch is expected to report **not ready to build**;
that is part of the demonstration. The completed run writes
`output/first-run/journey.receipt.json`.

Use a new output folder for each demonstration run. See [installation](INSTALL.md)
for more setup details and [the demonstration guide](docs/SYNTHETIC_JOURNEY.md)
for an explanation of the results.

## Choose an interface

| Interface | Use it for | Start command |
| --- | --- | --- |
| **Quiz Workshop** | A guided workflow for an actual course export: extract, review, collect decisions, and check whether a quiz can be built | `scripts/quiz_terminal.py` |
| **Bindery Ledger** | A terminal display of the synthetic demonstration, plus an extraction route for actual exports | `scripts/quiz_binder_tui.py --output-dir output/ledger-demo` |
| **Standalone scripts** | Running individual steps from a terminal or another script | See [the command guide](docs/RUN_SCRIPTS.md) |

Prefix these commands with `.venv/bin/python`, or `.venv\Scripts\python.exe` on
Windows. Bindery Ledger uses a full-screen display on a supported POSIX terminal
and a plain display elsewhere; `--plain` selects the plain display explicitly.

## Extract and review a course

Start Quiz Workshop:

```sh
.venv/bin/python scripts/quiz_terminal.py
```

The interface calls its three stages **Unbind**, **Compose**, and **Rebind**:

1. **Unbind** reads a Brightspace export ZIP or unpacked export and creates
   workbooks, a local HTML Reading Room, copied assets, and extraction records.
2. **Compose** collects explicit decisions from the edited workbook and checks
   readiness for one, several, or all quizzes.
3. **Rebind** builds one eligible quiz into a locally validated package and ZIP.

The same workflow can be run with explicit commands:

```sh
.venv/bin/python scripts/quiz_terminal.py unbind "/path/to/course-export.zip" \
  --workspace output/my-course

# Edit and save output/my-course/compose/reviewer_working.xlsx before Compose.
.venv/bin/python scripts/quiz_terminal.py compose output/my-course --all-quizzes
.venv/bin/python scripts/quiz_terminal.py status output/my-course
```

For an eligible quiz, use its entity key from the status report:

```sh
.venv/bin/python scripts/quiz_terminal.py rebind output/my-course \
  --quiz-entity-key 'cc:quiz:YOUR_KEY'
```

These multiline examples use shell continuation syntax for macOS/Linux. On
PowerShell, put each command on one line and use the Windows interpreter path.

### What the editable output contains

Open `compose/reviewer_working.xlsx` and read its **START HERE** tab. Each
assessment has its own tab, with questions grouped by source pool or folder.
Question types, points, available feedback, and source information are visible.
Use the yellow fields for proposed revisions and review decisions.

Keep this complete folder together when moving it or sending it for review:

```text
compose/
  reviewer_working.xlsx                 Editable review copy
  reviewer_baseline_DO_NOT_EDIT.xlsx    Baseline for checking changes
  native_source_DO_NOT_EDIT.xlsx        Original workbook representation
  model.json                           Structured source content
  assessment-review.json               Layout and companion-file manifest
  Images/                              Locally copied image files
  README.md                            Packet-specific instructions
```

Image links are relative to the workbook. Original rich markup is retained
separately from readable cell text. Import checks source identity, protected cells,
file hashes, and question coverage before collecting changes. Image or equation
content that cannot safely be revised through text cells remains held for review.

An export containing only a question library receives a question inventory.
Quiz Workshop does not invent assessments for it. The `All Questions` sheet also
retains library questions that do not appear in an assessment.

Only explicitly accepted decisions are applied. Reviewer names, reasons, and
dates are optional by default; Compose can require them with
`--metadata-policy required`. Save changes before running Compose; further
workbook changes require another Compose run.

## Draft new questions

Copy the [blank workbook](workspace/reference/examples/quiz_draft/blank.xlsx) to
a new filename and follow its START HERE and Types sheets. The adjacent
[nine-type examples](workspace/reference/examples/quiz_draft/) show the XLSX and
JSON input formats.

```sh
.venv/bin/python scripts/quiz_draft_intake.py "/path/to/my-draft.xlsx" \
  --output-dir output/my-draft
```

Draft intake creates a structured, unapproved draft and validation reports.
It supports nine question types at intake; package-building support is narrower.
Use the blank template for new questions and the extracted workbook for revisions
to existing questions. [Read the intake contract](docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md).

## Run individual steps or review the source

All runtime implementation is included under `scripts/`; access to the original
Workbench repository is not required. The scripts contain ordinary Python
functions and helper modules. The command-line entry points are the documented
interface; internal Python functions are not a versioned public API.

| Start with | Responsibility |
| --- | --- |
| [`quiz_terminal.py`](scripts/quiz_terminal.py) | Quiz Workshop prompts, saved state, and workflow orchestration |
| [`quiz_binder_tui.py`](scripts/quiz_binder_tui.py) | Bindery Ledger entry point and terminal selection |
| [`quiz_unbind.py`](scripts/quiz_unbind.py) | Independent extraction workflow and receipts |
| [`extract_quiz_pool_review.py`](scripts/extract_quiz_pool_review.py), [`quiz_normalization.py`](scripts/quiz_normalization.py) | Source extraction and structured question model |
| [`quiz_assessment_review.py`](scripts/quiz_assessment_review.py) | Assessment tabs, portable images, and guarded import |
| [`quiz_review_workbook_reingest.py`](scripts/quiz_review_workbook_reingest.py) | Collecting workbook revisions and review decisions |
| [`quiz_draft_intake.py`](scripts/quiz_draft_intake.py) | XLSX/JSON drafting intake |
| [`build_quiz_package_from_workbook.py`](scripts/build_quiz_package_from_workbook.py) | Package assembly for eligible inputs |
| [`run_synthetic_journey.py`](scripts/run_synthetic_journey.py) | Reproducible demonstration and validation |

The [command guide](docs/RUN_SCRIPTS.md) covers independent extraction, review
packet preparation, import, and draft intake. Run a command with `--help` for its
arguments. Shared schemas and examples are under `workspace/reference/`.

## Verification and current limits

Run the included demonstration, then recheck its recorded files:

```sh
.venv/bin/python scripts/render_quiz_binder_log.py --run-dir output/first-run
.venv/bin/python scripts/vendor_from_workbench.py --check
.venv/bin/python scripts/make_release_asset.py --check-only
```

The originating development checkout passed **795 tests with one intentional
skip** on Python 3.13 before this snapshot was prepared. This repository includes
the executable synthetic demonstration and its fixtures. The broader private
test corpus is not included, so `pytest` here does not reproduce that result.

Extraction success does not establish package readiness. Unsupported content,
unresolved references, missing assets, or missing build approval can prevent
Rebind. Local package validation does not prove that Brightspace will import or
render the package correctly. The tools do not log in to Brightspace or import
packages automatically. Word intake and typed-equation conversion are not
implemented.

This is a development snapshot based on `0.1.0-rc.8`, with newer assessment-review
changes. `VERSION` identifies that release base.
[REVIEW_MANIFEST.json](REVIEW_MANIFEST.json) records the originating commits and
snapshot file hashes; [the upstream pin](upstream/workbench_pin.json) identifies
the shared implementation. See [repository scope](docs/REPOSITORY_BOUNDARY.md)
for ownership and change guidance.

The default `output/` folder is ignored so routine generated outputs stay out of
source control. Included examples are synthetic; feedback can use real course
exports as described below.

## Feedback and contributions

[Issues and suggestions](https://github.com/timebeing92/brightspace-quiz-tools-review/issues)
and [pull requests](https://github.com/timebeing92/brightspace-quiz-tools-review/pulls)
are welcome. Feedback from real course exports helps improve the application and
its supporting tools. Reproductions may use real course data or synthetic input.
Include the course name, steps or command used, exact error messages, and what
you expected to happen, along with relevant files you have permission to share.

You can propose changes to any part of the included code here. Maintainers will
coordinate shared-code improvements with the source projects and update the
pinned files through the vendor process. Contributions will be acknowledged in
this repository, with accepted improvements credited in project documentation
or release notes. See [the contribution guidance](docs/REPOSITORY_BOUNDARY.md#feedback-and-contributions)
for reporting details and source-project coordination.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE), [license details](LICENSE_POSTURE.md),
and [commercial terms](COMMERCIAL.md).

## Part of the CourseCraft ecosystem

Brightspace Quiz Tools is part of a broader CourseCraft tooling ecosystem being
documented, packaged, and prepared in batches for public sharing. Each tool is
released independently, with its own supported features and level of readiness.

| Tool | What it does | Public link |
| --- | --- | --- |
| **Blueprint Wizard** | A guided local workflow that turns a Brightspace course export into a course blueprint and review reports. | [Repository and installation guide](https://github.com/timebeing92/brightspace-blueprint-runner) |
| **Rubric Loom** | Local tools for extracting, reviewing, drafting, and packaging Brightspace rubrics. | [Repository and installation guide](https://github.com/timebeing92/brightspace-rubric-loom-runner) |
| **Workshop Hall** | A hosted preview that brings selected CourseCraft tools together in a browser. | [Public hosted preview](https://huggingface.co/spaces/timebeing92/coursecraft-workshop) |

Workshop Hall has its own browser interface and run environment, using separately
versioned tool releases. Its Quiz Binder bench currently uses an older release and
will be updated after testing. Features and output layouts can therefore differ
from the local tools in this repository. For now, the Hall's quiz bench handles
extraction and review (**Unbind**); **Compose** and **Rebind** are available through
the local Quiz Workshop.

For the local tools, follow each repository's installation guide and release notes
for the current downloads, supported features, and limitations.
