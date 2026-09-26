# Brightspace Quiz Tools

Extract quizzes from a Brightspace course export, review questions in an editable
spreadsheet, collect approved changes, and build an import package when the
questions and settings meet the tool's supported build requirements.

This repository includes the Python source and helper modules, two terminal
interfaces, standalone commands, blank drafting templates, and synthetic examples.
It runs locally. A Brightspace or Google Drive account is not needed to run the
tools; installation downloads Python dependencies.

## What's included

| Location | Contents |
| --- | --- |
| `scripts/` | Quiz Workshop, Bindery Ledger, and the extraction, review, drafting, package-building and validation code they call |
| `workspace/reference/examples/quiz_draft/` | Blank XLSX drafting template and synthetic XLSX/JSON examples |
| `workspace/reference/schemas/quiz/` | Contracts for structured quiz data, review records and build capabilities |
| `tests/` | Runnable regression tests, synthetic fixtures and sanitized native XML examples |
| `docs/` | Installation/workflow details, independent commands, design contracts and verification records |
| `upstream/workbench_pin.json` | Source commit and file hashes for the shared implementation included here |

## Start here

You need **Python 3.11, 3.12, or 3.13**. Python 3.14 is not supported yet.

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
2. **Compose** collects explicit decisions from the edited workbook, applies
   accepted changes to a model copy, and checks readiness for one, several, or
   all quizzes.
3. **Rebind** builds one eligible quiz into a locally validated package and ZIP.

Extraction reads quiz XML together with the available Question Library so
question bodies, answer options, keys, pool membership and assets can be joined
by their source identities. The resulting `model.json` holds that structured
content and its source evidence. The workbook provides the editable review view.

Compose writes a decision overlay and a promotion receipt showing which accepted
changes were applied or excluded. It also collects accepted quiz-setting changes.
The original export and protected baseline remain available for comparison.
Rebind uses the reviewed model, settings and assets; it checks the generated XML,
asset references and ZIP contents and writes validation reports alongside them.

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
  Images/                              Original images (protected)
  Replacement Images/                  Add replacement files here when needed
  README.md                            Packet-specific instructions
```

Image links are relative to the workbook. Original rich markup is retained
separately from readable cell text. Import checks source identity, protected cells,
file hashes, and question coverage before collecting changes. Image or equation
content that cannot safely be revised through text cells remains held for review.
To replace an image, add a PNG/JPEG/GIF under `Replacement Images/`, enter its
relative filename on the matching `Image Replacements` row, and mark it accepted.
Compose checks the bytes and Rebind includes the replacement file.

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
Within an extracted assessment packet, use `New Questions` and `New Responses`
to add questions to an existing pool. Copy the target identities from `Target
Pools` and explicitly accept the new question. The pool grows; its random draw
count stays unchanged. Use the blank template for independent drafting and new
assessments. Keep the protected source rows unchanged. [Read the intake contract](docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md).

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
| [`quiz_promote_revisions.py`](scripts/quiz_promote_revisions.py) | Applying accepted changes to a new model copy and recording the result |
| [`quiz_draft_intake.py`](scripts/quiz_draft_intake.py) | XLSX/JSON drafting intake |
| [`check_quiz_authoring_readiness.py`](scripts/check_quiz_authoring_readiness.py), [`quiz_build_support.py`](scripts/quiz_build_support.py) | Checking selected questions, pools, assets and build requirements |
| [`build_quiz_package_from_workbook.py`](scripts/build_quiz_package_from_workbook.py) | Package assembly for eligible inputs |
| [`validate_quiz_package.py`](scripts/validate_quiz_package.py), [`diff_packages.py`](scripts/diff_packages.py) | Checking package structure and comparing generated or re-exported packages |
| [`run_synthetic_journey.py`](scripts/run_synthetic_journey.py) | Reproducible demonstration and validation |

The [command guide](docs/RUN_SCRIPTS.md) covers independent extraction, review
packet preparation, import, and draft intake. Run a command with `--help` for its
arguments. Shared schemas and examples are under `workspace/reference/`.

## Run the tests and checks

Run the included demonstration, then recheck its recorded files:

```sh
.venv/bin/python scripts/render_quiz_binder_log.py --run-dir output/first-run
.venv/bin/python scripts/vendor_from_workbench.py --check
.venv/bin/python scripts/make_release_asset.py --check-only
```

Install development dependencies, then run the included regression suite:

```sh
python3.13 scripts/bootstrap_env.py --locked --dev
.venv/bin/python -m pytest tests
```

On Windows, use `py -3.13` for bootstrap and `.venv\Scripts\python.exe` for
the test command. To run just the reviewer-edit scenarios and complete terminal
workflow, select their test files:

```sh
.venv/bin/python -m pytest tests/test_quiz_sme_edit_scenarios.py tests/test_quiz_terminal_roundtrip.py
```

| Test area | What it exercises |
| --- | --- |
| Extraction and normalization | Source identities, question types, rich content, answer keys, assets and pool relationships |
| Workbook review | Accepted/open/rejected decisions, prompt/options/key edits, settings, protected cells and companion-file integrity |
| Images and new questions | Replacement-file checks, unchanged content on rejection, added pool members and retained draw counts |
| Package and terminal workflow | Unbind → workbook edits → Compose → authorized candidate build → validation → re-extraction |
| Distribution | Archive completeness, pinned-source integrity and sanitized fixture metadata |

The repository's GitHub Actions workflow runs the public suite and demonstration on
Python 3.11, 3.12 and 3.13, and checks the pinned source and distribution inputs.
It does not approve or merge pull requests; maintainers review contributions.

The fixtures are synthetic or sanitized native XML shapes. Real course prose,
resource links, access settings and raw tenant exports are excluded. Source
checkouts and generated review archives include the same public test corpus.
One historical evidence check is intentionally skipped because it requires a
private import-receipt log that is not distributed. See
[`tests/README.md`](tests/README.md) for coverage, recorded results and limits.

## Build requirements and limits

Review acceptance does not grant build approval: extracted questions need
accepted permanent codes, resolved scoring where required, and import evidence
or an exact sandbox candidate authorization before Rebind.

Extraction success does not establish package readiness. Unsupported content,
unresolved references, missing assets, or missing build approval can prevent
Rebind. Local package validation does not prove that Brightspace will import or
render the package correctly. The tools do not log in to Brightspace or import
packages automatically. Word intake and typed-equation conversion are not
implemented.

The default `output/` folder is ignored so routine generated outputs stay out of
source control. Included examples are synthetic; feedback can use real course
exports as described below.

## Source, pins and dependencies

This repository carries a complete copy of its runtime source. Shared extraction,
review and build behavior originates in CourseCraft Workbench; terminal and
distribution tooling originates in Quiz Bundle. Neither source repository is
needed to install, run, test or propose changes here.

The [Workbench pin](upstream/workbench_pin.json) records the source commit and
SHA-256 hash of each shared file copied into this repository. The vendor check
compares those local files with the recorded hashes; it does not download code
or connect to Workbench. A passing pin check confirms source consistency, not
that a particular quiz is approved to build or import.

Python dependencies are listed separately in `requirements.txt` and pinned in
`requirements-lock.txt`; `sbom/runtime.cdx.json` inventories those runtime
dependencies. The Workbench pin records code provenance, not packages to install.

`VERSION` identifies the `0.1.0-rc.8` release base; this development snapshot
includes subsequent changes. In source checkouts, `REVIEW_MANIFEST.json` records
the originating commits and snapshot file hashes. Generated archives include
`RELEASE_MANIFEST.json` for their packaged files. Use the Git commit and these
records when identifying an exact version in a bug report. See
[repository scope](docs/REPOSITORY_BOUNDARY.md) for ownership and promotion details.

## Feedback and contributions

[Issues and suggestions](https://github.com/timebeing92/brightspace-quiz-tools-review/issues)
and [pull requests](https://github.com/timebeing92/brightspace-quiz-tools-review/pulls)
are welcome. Feedback from real course exports helps improve the application and
its supporting tools. Reproductions may use real course data or synthetic input.
Include the course name, steps or command used, exact error messages, and what
you expected to happen, along with relevant files you have permission to share.

You can propose changes to any part of the included code here. Maintainers will
coordinate shared-code improvements with the source projects and update the
pinned files through the vendor process. If a proposed change touches a pinned
file, the vendor check may report drift while that coordination is pending;
explain the change in the PR and leave the pin update to the maintainer.
Contributions will be acknowledged in this repository, with accepted improvements
credited in project documentation
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
