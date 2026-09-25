# Question type presentation and deterministic fresh intake

## Status and ownership

Local Workbench adoption candidate, developed from upstream `278889e` on
`feat/question-type-drafting`. CHEM 1035 is the first downstream exercise.
This is a new intake adapter into `coursecraft.quiz/1`; that canonical schema,
source-review schemas, identity joins and capability registry are unchanged.
No release, capability promotion, or live Brightspace verification is implied.

Workbench owns the shared type catalog, fresh-intake grammar, semantic validator,
canonical adapter and package-loss refusals. CHEM owns its exam-tab arrangement,
Google sheet IDs and preservation receipts.
The generic template builder now lives upstream as
`scripts/build_quiz_draft_templates.mjs`; CHEM-specific output naming and live
workbook presentation remain downstream.
The generic reviewer extractor now presents its existing `question_type` column
at width 24 in Quiz Questions and All Questions. It finds the column by header,
retains the source value and protection, and does not insert or rename columns.

## Operator path

Use the blank drafting XLSX or copy the synthetic nine-type example. The input
contains START HERE, Config, Questions, Responses, Pools, Draws and Types.

```bash
python3 scripts/quiz_draft_intake.py new-questions.xlsx \
  --output-dir workspace/review/new-question-intake-01
# The equivalent JSON shape is demonstrated in:
# workspace/reference/examples/quiz_draft/nine_types.json
```

A successful run writes `quiz.model.json`, `intake-report.json`, `readiness.json`
and `readiness.md`. The output directory must be new. The original workbook is
read only. Formula cells in input tables are refused, as are unknown tabs,
headers/fields, duplicates and dangling joins. Empty rows are ignored; populated
rows are never silently dropped. Dropdowns are guidance; validation still checks
pasted values. Proposer, reason and timestamp are not functional requirements.

### Normative input and report contracts

`workspace/reference/schemas/quiz/quiz_draft_intake_schema.json` defines every
input object, table, column, required field and permitted primitive type.
`quiz_draft_intake_report_schema.json` defines the successful intake report,
including `valid_intake=true`, `ready_to_build=false`, counts, per-question
capabilities and source/parser provenance. The implementation enforces both
schemas, then the type-specific and join rules below. A shape-valid input alone
is not a valid intake. These are adapter contracts; the canonical schema and
existing revision/review workbook contract are unchanged.

Only `.json` and `.xlsx` input files are accepted. JSON parsing rejects duplicate
object keys and nonstandard NaN/Infinity tokens. Decimal tokens retain their
precision until field validation. Workbook formulas, extra populated columns,
incorrect headers, unsupported cell value types and malformed input shapes
fail explicitly. Workbook diagnostics include the physical worksheet row even
when blank rows precede the record; JSON diagnostics include the object path.
No output directory is created for a validation refusal. A pre-existing output
directory is never overwritten.

Only `null`/an empty string means a blank optional text field. Numeric `0`,
boolean `False`, arrays and objects in prompt/key/feedback/response text are
refused with a field-specific diagnostic. String `"0"`, string `"False"` and
all other accepted strings retain their exact whitespace and bytes. Templates
format text fields as Text to reduce accidental spreadsheet coercion. This is
an explicit text contract, not an implicit conversion policy.

Points must be positive, finite, no greater than `2^53-1`, have no more than nine
fractional decimal places after trailing zeros are removed, and round-trip
through JSON's numeric representation without changing their decimal value.
Counts and ordering positions additionally require integral values. Numeric
strings and typed numeric cells use the same checks. Excess precision is refused,
never rounded. XLSX numeric tokens are read from the source XML before the
spreadsheet library can cast them to floating point. This bound matches the current nine-decimal QTI output; broader
precision needs a separate explicit target contract. Excel may already have
rounded a number before saving; the parser validates the saved value and cannot
recover earlier keystrokes. Use an exact decimal string when that distinction
matters.

## Identity and canonical mapping

Config declares `coursecraft.quiz_draft/1`, a stable `lineage_code`, and optionally
a quiz code/title. Both quiz fields must be blank for a library-only intake.
Question codes are unique within a lineage. Entity keys use namespace, lineage
and explicit codes, never question wording. Editing a prompt retains identity;
changing its code or lineage creates another identity. The input SHA-256 is
recorded separately in source provenance, model/run IDs and references. Identical
input produces identical intake records; there are no model calls or timestamps.
The capability registry and generated catalog SHA-256 are recorded in extensions.
Source provenance and the intake report additionally record input format,
parser contract version and input-schema SHA-256. XLSX uses canonical
`source_kind=workbook`. JSON uses `source_kind=unknown` with explicit
`input_format=json`, since the canonical enum has no JSON member. CLI runs hash
the original file bytes. Direct in-memory calls without a supplied file digest
record a canonical-JSON fingerprint basis and unknown canonical fingerprint
scope rather than falsely claiming a file hash.

Questions become canonical question entities; Responses become options,
accepted responses, blanks, matching pairs or order entries. Pools become pool
structures and `member_of` relationships. Each Draw declares one pool, a positive
integer count no larger than its membership, and positive points per drawn item.
It becomes a `draw` plus resolved `contains`/`draws_from` joins. Pool points and
question maximum points remain distinct. V1 allows one pool per question and
one draw per pool in a quiz; multiple quizzes, overlapping pools and more general
placement require a later grammar, not duplicated/ambiguous rows.

## Type-specific contract

| Type | Required structure | Package boundary |
|---|---|---|
| Multiple Choice | At least two option rows, exactly one correct | Current projection: 2–5 sequential A…E keys |
| True/False | T=True then F=False, plain text, exactly one correct | Current projection supported |
| Multi-Select | At least two options, at least one correct | All-or-nothing only; 2–5 A…E keys |
| Written Response | No response rows; optional manual evaluator key | Current projection supported |
| Short Answer | Accepted answers with explicit case sensitivity | Draft only |
| Multi-Short Answer | Accepted answers in at least two named groups | Draft only |
| Fill in the Blanks | Named answer groups, exactly matching `[[blank:code]]` placeholders | Draft only; placeholder conversion is deferred |
| Matching | At least two complete one-to-one left/right pairs | Draft only; reused/distractor matching is deferred |
| Ordering | At least two options, unique contiguous 1…N positions | Draft only |

Every response has an explicit response key; accepted-answer keys and their rich
format stay in extensions where the canonical payload lacks dedicated slots.
Left/right matching roles are also retained in option extensions. Canonical kind
and display label resolve deterministically; `Long Answer` is an accepted alias
for `Written Response`. Unknown types fail. Fields incompatible with the chosen
type fail if populated; a dropdown change cannot quietly discard old scoring,
matching or answer structures. This adapter does not implement revision-type
conversion for source-review workbooks.

## Math, feedback and package safety

`content_format` is explicit: plain_text, html, mathml or latex. Questions uses
one format for prompt, manual key and general feedback; each response has its own
format. Exact strings, including whitespace, are preserved. The canonical rich
text enum has no LaTeX member, so LaTeX is stored as plain_text plus the explicit
`coursecraft.source_encoding=latex` extension. Accepted responses retain a full
`coursecraft.formatted_content` extension. This preserves source; it does not
validate equation syntax, reconstruct math from display text, or claim rendering.

Inspection found the existing package projection omitted non-answer-key
feedback. `question_projection_issues` now blocks that loss for both readiness and
the builder. It also refuses standalone MathML/LaTeX and other formats without an
explicit package conversion. Existing HTML/XHTML fragments remain the supported
rich-content path; fresh fragments still require real rendering evidence.
General feedback remains intact in intake JSON even when package projection is
blocked. Conditional/per-option feedback, partial-credit rules, images/assets,
and independent formats for prompt/key/feedback are deferred extensions; do not
flatten them into this v1 grammar.

All fresh question instances retain `build_support=extraction_only` with empty
receipt references, including the four types whose generic registry capability
is verified. Validation does not manufacture approval or reuse somebody else's
import evidence. Existing reviewed promotion and exact candidate authorization
processes remain required before package generation. A valid draft and a locally
validated package are separate results.

## Adoption and later work

Adopt the catalog and adapter as bounded Workbench capabilities after review.
Keep CHEM exam layouts downstream; other review profiles may choose their own
presentation. No new canonical schema version is needed for this adapter.

Retain the earlier roadmap for deterministic Word-bank intake, typed-equation
conversion, native Brightspace MathJax handling, richer feedback/scoring, type
conversion proposals, and improved authoring UX. The structured intake can later
serve as the deterministic target for a Word parser, without making Word text or
an AI interpretation canonical. New formats must include fixtures, exact source
preservation, negative cases and an explicit target capability check.
