# Run the tools

Run these commands from the bundle folder after [installation](../INSTALL.md).
On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.
Quote paths containing spaces. Every output directory shown below should be new.
Multiline commands below use macOS/Linux shell continuation syntax. In PowerShell,
put each command on one line and use the Windows interpreter path.

## Choose a route

| Task | Entry point | Output |
| --- | --- | --- |
| Guided real-course workflow | `quiz_terminal.py` | Extraction, editable assessment review, decisions, readiness; a package only when eligible |
| Real-course workflow in scripts | `quiz_terminal.py unbind/compose/rebind/status` | The same workspace and checks |
| Bindery Ledger demonstration | `quiz_binder_tui.py` or `quiz_binder_wizard.py` | Synthetic end-to-end proof with an example approved change |
| Bindery Ledger real extraction | `quiz_binder_tui.py --unbind` | Extraction and an editable assessment review packet |
| Independent extraction | `quiz_unbind.py` | Native workbooks, model, assets, Reading Room and receipts |
| Group native output for review | `quiz_assessment_review.py prepare` | Portable assessment workbook and import companions |
| Collect edited assessment tabs | `quiz_assessment_review.py import` | Decision overlay; no package building |
| Draft new questions | `quiz_draft_intake.py` | Unapproved model and intake/readiness reports |

All entry points are under `scripts/`. Run any script with `--help` for its flags.

## Quiz Workshop

```sh
.venv/bin/python scripts/quiz_terminal.py
```

Or use explicit commands:

```sh
.venv/bin/python scripts/quiz_terminal.py unbind /path/to/export.zip \
  --workspace output/my-course
```

Edit `output/my-course/compose/reviewer_working.xlsx`. Start with START HERE and
use the yellow fields on assessment tabs. Keep the full `compose/` folder.

```sh
.venv/bin/python scripts/quiz_terminal.py compose output/my-course --all-quizzes
.venv/bin/python scripts/quiz_terminal.py status output/my-course
```

Read the readiness report. For an eligible quiz, use its key from status/the
guided selection, then run:

```sh
.venv/bin/python scripts/quiz_terminal.py rebind output/my-course \
  --quiz-entity-key 'cc:quiz:YOUR_KEY'
```

An extraction can succeed while Rebind remains unavailable. Extracted source
questions do not automatically gain the build evidence required by the registry.

## Independent extraction and review

```sh
.venv/bin/python scripts/quiz_unbind.py /path/to/export.zip \
  --output-dir output/extraction
```

The native reviewer workbook and matching `.model.json` are in
`output/extraction/extraction/`. Substitute their generated filename prefix for
`EXPORT` below:

```sh
.venv/bin/python scripts/quiz_assessment_review.py prepare \
  --source-workbook output/extraction/extraction/EXPORT__quiz_pool_review_reviewer.xlsx \
  --model output/extraction/extraction/EXPORT__quiz_pool_review.model.json \
  --output-dir output/review-packet
```

After editing `output/review-packet/reviewer_working.xlsx`:

```sh
.venv/bin/python scripts/quiz_assessment_review.py import \
  --packet output/review-packet --output output/review-decisions.json
```

Add `--edited-workbook /path/to/returned.xlsx` for a separately saved review copy.
The packet's native model, baseline, manifest and images must remain together.
The source folder and original export stay unchanged. See the
[packet contract](project/quiz-consolidation/ASSESSMENT_REVIEW_PACKETS.md).

## Blank drafting template

Copy `workspace/reference/examples/quiz_draft/blank.xlsx` to a new filename.
START HERE and Types explain the input sheets. `nine_types.xlsx` and
`nine_types.json` provide synthetic examples.

```sh
.venv/bin/python scripts/quiz_draft_intake.py /path/to/my-draft.xlsx \
  --output-dir output/my-draft
```

Nine types can be drafted; supported intake does not mean every type can be
built into an LMS package. Inspect the intake/readiness reports. The extracted
review workbook is for revising existing source questions; use this separate
template for new questions.

## Synthetic proof without course content

```sh
.venv/bin/python scripts/run_synthetic_journey.py --output-dir output/proof
```

This plain command runs the demonstration used by Bindery Ledger. It creates
an assessment review packet, simulates one accepted edit and exercises a separate
build-ready fixture through package validation. `journey.receipt.json` records
the results. No Brightspace login, import or Google Drive account is involved.

## Reviewing the implementation

Start with `quiz_terminal.py` for orchestration, `quiz_assessment_review.py` for
the layout/import adapter, and `quiz_review_workbook_reingest.py` for decision
collection. `extract_quiz_pool_review.py` and `quiz_normalization.py` own source
extraction and normalization. The Workbench pin identifies the exact shared code.

This repository includes the built-in synthetic proof and its fixtures. Run that
proof to exercise the included runtime. The full `python -m pytest` suite belongs
to the originating development checkout and is not included in this snapshot.
See [repository scope](REPOSITORY_BOUNDARY.md) for source ownership and change
guidance.
