# Portable assessment review

`scripts/quiz_assessment_review.py` adapts a native extraction workbook into a
portable review folder. It generalizes the CHEM assessment layout without course
names, Drive IDs, or course content in the implementation.

## Prepare and edit

First extract a Brightspace export with copied assets:

```sh
python scripts/extract_quiz_pool_review.py /path/to/export.zip \
  --asset-mode copy --output-dir output/extraction
```

Use the generated reviewer workbook and matching model:

```sh
python scripts/quiz_assessment_review.py prepare \
  --source-workbook output/extraction/EXPORT__quiz_pool_review_reviewer.xlsx \
  --model output/extraction/EXPORT__quiz_pool_review.model.json \
  --output-dir output/assessment-review
```

Replace `EXPORT` with the generated filename prefix. The output folder must be
new or empty. The source extraction remains unchanged.

Open `reviewer_working.xlsx`. START HERE explains editing. Each assessment has an
editable tab with source pool/folder labels, collapsible groups, question types,
wrapped text, frozen headings and yellow revision fields. The original Quiz
Questions sheet is a protected hidden snapshot. Quiz Settings retains its native
editable fields. All Questions is a complete inventory, including library-only
questions; these library-only records are source inventory, not assessment edits.
An export containing only a Question Library receives a protected inventory with
portable image links and no invented assessment tabs. It has no assessment edits
to import; use the drafting template when authoring a new assessment.

Names, reasons and dates are optional under the packet import command. Acceptance
is always explicit. Existing row/column visibility and formatting are left alone
when importing an edited workbook. Never regenerate over a review in progress.

## Share the folder

The folder includes working and baseline workbooks, a native source workbook,
`model.json`, `assessment-review.json`, and `Images/`. Image links are relative
to the workbook. Send the complete folder (or ZIP it), and extract it before
opening the workbook. Moving the entire folder preserves its links. No Google
Drive account is required. External web links, when supplied by a source, retain
their existing URLs. Missing source images are not fabricated.

## Collect edits

```sh
python scripts/quiz_assessment_review.py import \
  --packet output/assessment-review --output output/decisions.json
```

For a separately saved edited copy, add `--edited-workbook /path/to/edited.xlsx`.
The native `quiz_review_workbook_reingest.py` also recognizes grouped baselines
automatically. Use the packet's grouped baseline and keep its companions beside
it. A native baseline combined with grouped edits is rejected.

The adapter verifies companion hashes and every protected value and image link,
checks assessment/group membership through the baseline, collects revisions by
stable occurrence identity, then calls the existing importer. Missing or moved
questions, altered source/markup cells, heading-row reviews and formulas are
rejected. Presentation-only changes import with zero content revisions.

## Rich source and limits

Readable prompts, choices, keys and feedback are display projections. Exact
source fragments and option identities stay in the markup columns and complete
model. Oversized markup cells point to the model instead of truncating its only
copy. The bounded renderer handles source-declared superscripts, subscripts,
fractions and common MathML; unsupported expressions retain their source and a
review diagnostic. This adapter does not reconstruct equations from plain text.

Accepted text/choice/key revisions on questions containing equations or diagrams
are held for a supported rich-content path. Open drafts and notes can be
collected. Point values and feedback are source observations, not new authoring
controls. Import emits a decision overlay, not an LMS import ZIP. Use the original
complete extraction workspace for readiness and building; the review packet's
model retains its original asset references.

For supported choice and written-response questions, normalization preserves
bounded source prompt HTML separately from the readable workbook display.
Unsupported native prompt material blocks building. A source image must remain
referenced in projected question content; copying its file alone is insufficient.

Image replacement is not yet an authoring control. Uploading a new file and
describing it in `reviewer_note` records a request only. Changing the existing
source image or its protected link is rejected. Use an existing question row for
the note; added instruction rows are outside the original review coverage.

Accepted revisions to supported question types can be applied to an extracted
model copy before its first sandbox import. This leaves the question's
`extraction_only` build-support status unchanged. Readiness and package generation
still require recorded import evidence or an exact local candidate authorization;
review acceptance alone does not make the package import-verified.

Blank question-authoring templates and `quiz_draft_intake.py` remain a separate
workflow. Do not append new source-question rows to an extracted reviewbook.

## Verification

Run `python -m pytest tests/test_quiz_assessment_review.py
tests/test_quiz_review_workbook_reingest.py tests/test_quiz_sme_edit_scenarios.py`.
The SME scenarios can also target a distribution checkout by setting
`QUIZ_TOOLS_REPO=/absolute/path/to/checkout`. Tests use synthetic questions and
exercise relocation, image links, unchanged import, accepted/draft edits, source
protection, hidden layout, changed companions and rich-content holds.
