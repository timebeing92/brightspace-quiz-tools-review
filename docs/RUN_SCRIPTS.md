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
  --packet output/review-packet --output output/review-packet/decision-overlay.json
```

Add `--edited-workbook /path/to/returned.xlsx` for a separately saved review copy.
The packet's native model, baseline, manifest and images must remain together.
Keep the decision overlay inside the packet: accepted replacement-image paths
are resolved relative to that file's folder during promotion.
The source folder and original export stay unchanged. See the
[packet contract](project/quiz-consolidation/ASSESSMENT_REVIEW_PACKETS.md).

To apply accepted decisions to a new model copy independently:

```sh
.venv/bin/python scripts/quiz_promote_revisions.py \
  --model output/review-packet/model.json \
  --overlay output/review-packet/decision-overlay.json \
  --registry workspace/reference/schemas/quiz/quiz_build_capabilities.json \
  --output-model output/review-packet/promoted.model.json \
  --output-receipt output/review-packet/promotion.receipt.json
```

Inspect the receipt for excluded decisions before proceeding. Promotion records
accepted changes; it does not grant build approval. For independent package work,
use `check_quiz_authoring_readiness.py`, `build_quiz_package_from_workbook.py`,
and `validate_quiz_package.py` with the same model, quiz key, settings receipt,
promotion receipt, and any exact candidate authorization. Repeat `--asset-root`
for the original extraction folder and review packet so both original and
replacement files are available. Each command's `--help` lists its arguments.
Quiz Workshop's Compose and Rebind orchestrate these steps and collect settings
decisions automatically.

## Blank drafting template

Copy `workspace/reference/examples/quiz_draft/blank.xlsx` to a new filename.
START HERE and Types explain the input sheets. `nine_types.xlsx` and
`nine_types.json` provide synthetic examples.

```sh
.venv/bin/python scripts/quiz_draft_intake.py /path/to/my-draft.xlsx \
  --output-dir output/my-draft
```

Nine types can be drafted; supported intake does not mean every type can be
built into an LMS package. Inspect the intake/readiness reports. Use the blank
template for independent drafting and new assessments. To add questions to an
existing extracted quiz pool, use the review packet's `New Questions` and
`New Responses` sheets, with target identities from `Target Pools`.

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

This repository and its review archive include the synthetic demonstration and
public regression corpus. Run `python -m pytest tests` after installing the dev
dependencies; see [test coverage](../tests/README.md). The larger private Workbench
suite and its raw import evidence are outside this distribution.
See [repository scope](REPOSITORY_BOUNDARY.md) for source ownership and change
guidance.
