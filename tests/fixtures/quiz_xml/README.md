# Quiz XML Fixtures

This lane holds small unpacked Brightspace-style exports grouped by parser edge case.

Use these fixtures when you want:

- direct XML inspection
- lightweight parser tests
- reproducible examples for extractor/refactor work

Current fixture groups:

- `duplicate_pool_and_similarity/`
- `math_rendering/`
- `answer_key_types/`
- `matching_response_groups/`
- `ordering_response_group/`
- `fill_in_the_blanks_multiblank/`
- `long_answer_with_answer_key/`
- `mixed_inline_itemref_and_root_bank/`
- `occurrence_scoped_source_facts/`
- `short_answer_projection/`
- `quiz_only_inline_tf/`

The matching, ordering, multiblank and written-response fixtures retain native
Brightspace XML shapes from historical exports. All question prose, answer prose,
course titles, external resource links and quiz passwords were replaced or removed
for public regression use. Legacy numeric IDs remain only to exercise joins;
these are sanitized fixtures, not distributable course assessments.

`mixed_inline_itemref_and_root_bank/` is a sanitized composite fixture derived
from relationship shapes confirmed in BUMK 515, APN 505, and ANAT 1005. It does
not copy authored course questions.

`occurrence_scoped_source_facts/` is a fully sanitized, native-shaped export
with one itemref-backed occurrence and one inline occurrence that share durable
question metadata but intentionally differ in raw response facts. It exists to
prove that normalization retains both occurrence bundles.

`short_answer_projection/` is a fully sanitized, native-shaped export for the
exact literal Short Answer profile. It includes agreeing and conflicting
merged occurrences, both observed renderer tuples, and safe placeholder
synonyms spanning Unicode, punctuation, XML entities, colon, and bare slash.
It also includes an exact spaced-slash literal to prove the legacy blank is
only forward-compared and never reverse-parsed.

`quiz_only_inline_tf/` is a fully synthetic export with one inline True/False
item and no `questiondb.xml`. It proves that the extractor and Unbind path retain
quiz-local content while marking question-library resolution as skipped.

These are intentionally tiny and reviewable. They are not automatically proof of import-validity.
