# Synthetic sandbox verification — September 26, 2026

This check exercises an actual Brightspace import and export, separately from
the offline regression suite. All authored questions and replacement media are
synthetic. Whole tenant exports are kept outside source control.

## Workflow and findings

1. Built and imported a five-question baseline: Multiple Choice, True/False,
   Multi-Select and Written Response, with a PNG and a five-of-five pool.
2. Re-exported that baseline. Brightspace had dropped the generated Multi-Select
   answer key. Local re-extraction had passed because the writer and reader
   agreed on a generic QTI structure that the native importer did not preserve.
3. Set the synthetic key in Brightspace's native editor and exported a reference.
   The sanitized single-question fixture is included as
   `tests/fixtures/quiz_source_choice_fidelity/native_multiselect.xml`.
4. Repaired the builder to emit native Add 1 / complementary Add 0 scoring.
   The parser recognizes that bounded profile; malformed complements and unkeyed
   programs stay unresolved. Package validation rejects the earlier generated
   profile and compares choice keys with the authoring model.
5. Unbound the native reference export, made the workbook edits below, ran
   Compose, supplied exact synthetic candidate authorization, and ran Rebind.
   The standalone builder also built the same model/settings/assets/receipts,
   overriding only the quiz title to identify the new import.

## Candidate B

The imported quiz is named **CC QA 20260926 B Workbook**. Import succeeded.
No question or setting was repaired in the native editor after this import.

| Check | Native editor or preview result | Re-export comparison |
| --- | --- | --- |
| Revised prompt and two response options | Present | Exact match |
| Revised Multiple Choice key from first to second answer | Second answer marked correct | Exact match |
| Unchanged Multi-Select key and scoring | First two correct; third incorrect; All or Nothing | Exact match |
| Replace blue PNG with new green PNG | Green image visibly rendered | SHA-256 matches replacement |
| Add a Multiple Choice question | Present in pool and preview | Exact match |
| Expand pool from five to six questions | Six members; draw remains five at one point each | Exact match |
| Change time limit and attempts | 17 minutes; three attempts; hidden | Exact match |

The image-only replacement intentionally leaves its original prompt unchanged.
An image change does not imply authorization to rewrite surrounding prose.

The scoped re-export completed and was manually downloaded. All six questions
matched by permanent display ID, with no missing, added or ambiguous matches and
zero broken joins. Exact normalized comparisons passed for every prompt, kind,
option body, key, scoring field and written-response guidance. The five-of-six
draw and one-point weighting survived. The replacement PNG is byte-identical.

The four explicitly accepted settings survived: 17-minute time limit, three
attempts, hidden status and enforced timing. One unedited default did change:
`show_clock=no` became `yes`. Clock visibility is therefore not preserved in this
tested tenant; do not treat the zero-break structural diff as zero settings drift.

Offline verification on Python 3.13: **650 passed, one intentional skip** from a
fresh extracted review archive; all 152 vendor targets verified. The executable
synthetic demonstration also passed from that archive. The development source
suite passed **1005 tests with two skips**. These are separate evidence sets.

Candidate ZIP SHA-256:
`740c58902bc4ef62f22092110968668fd74203f4baf1e2d95c66b76862898a36`.
Returned export SHA-256:
`22f830707574390780fc83bcdde14e32eabd85b4a1f6349ec3ef5b09eb02ebdb`.

## Preview scoring

A retained instructor preview attempt randomly selected all five objective
questions (excluding Written Response). The correct responses scored **5/5
(100%)** in the instructor's Preview Attempts view, including the repaired
Multi-Select, revised Multiple Choice key, replacement-image question, True/False
and new question. The learner summary showed Pending Evaluation; the recorded
instructor score was inspected without publishing or overriding it.

This is a positive-key scoring check. Incorrect/partial selections and other
scoring modes were not exercised live. Written Response remains manually graded.

## Limits

These observations cover this synthetic candidate, these question types, and
this tenant. They do not establish universal import support, student grading
correctness for every scoring mode or question type.
Written Response requires manual grading. No evidence classification or general
build approval is upgraded merely because a candidate was imported.

The public tests run offline. They include the native scoring regression and
the full terminal workbook-edit workflow; they do not connect to a tenant.

## Final review follow-up

Two additional synthetic regressions exercise rejected image replacements:
authored content remains unchanged when its asset relationship is missing, and
a moved replacement folder cannot redirect promotion outside the packet. Both
failed before the fixes and passed afterward. The source suite now passes
1007 tests with two skips; the extracted public archive passes 652 with its one
intentional private-evidence skip. The successful replacement and terminal
roundtrip cases still pass.

These follow-up changes guard rejection paths. They do not alter the scoring
serializer or expand the live-test claims above. The recorded candidate ZIP and
returned-export hashes still identify the actual sandbox artifacts.
