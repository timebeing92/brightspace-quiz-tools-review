# Brightspace Quiz XML — Source Observations

Status: **evidence-scoped reference.** Not a normative JSON contract and not an
official D2L specification. Every statement below is an observation from local
exports in this workbench, with the corpus that supports it named. Where D2L
documents behavior authoritatively, the documentation wins.

Date: 2026-08-03
Corpora observed: ANAT 1005, BIOL 1010, BIOL 1020, BIOL 1055, CHEM 1010,
CHEM 1020, PHYS 1010, APN 505, BUMG 660, BUMK 515, College Algebra, and the
2026-07-24 Short Answer round trip.

All are from one institution and largely template-derived, so they likely share
an authoring toolchain. A negative observed across all of them is strong
evidence about how these courses are authored and weak evidence about what
Brightspace permits.

This file exists because the Unbind fidelity correction turned on questions of
fact about the source format — what identifies a question, what belongs to a
placement — and those facts were previously implicit in code.

## 1. Two files, two roles

- `questiondb.xml` holds the question library: an `<objectbank>` containing
  `<item>` elements, optionally nested in `<section>` pools.
- `quiz_d2l_<id>.xml` holds one quiz: an `<assessment>` with `<section>`
  elements that either inline their own `<item>` elements or reference library
  items by `<itemref linkrefid="...">`.

A whole-course export usually contains both. A selective quiz-only export may
contain neither a `questiondb.xml` nor any `itemref`, and extraction must still
succeed. Observed in `quiz_only_inline_tf`.

## 2. Identifiers, and which of them identify a question

Each `<item>` carries QTI metadata fields:

| Field | Observation |
| --- | --- |
| `qmd_globalid` | A GUID on the item itself. Present on every item in every corpus observed. |
| `qmd_displayid` | A human-facing identifier, course-scoped. |
| `item/@ident`, `item/@label` | Platform identifiers, stable within an export. |

**An inline quiz item carries its own `qmd_globalid`, distinct from the library
item it was copied from.** In ANAT, 775 quiz items carry 775 distinct
`qmd_globalid` values while only 442 library records are referenced. Respondus-
generated pools in particular produce many quiz-local copies of one library
question, each with its own GUID.

Consequence for identity: the identifier belonging to a *matched library
record* does not identify the quiz item that matched it. Using it as the item's
name merges materially different questions. The item's own identifier is the
name; the library link is a relationship.

**A library record can be duplicated.** In ANAT, 122 of 522 distinct library
prompts appear more than once in `questiondb.xml`. A library identifier is
therefore not a unique name for authored content either.

**Identifiers survive a Brightspace import and re-export.** In the 2026-07-24
round trip, `qmd_globalid`, `qmd_displayid`, and `quiz_item_label` all survived
at 100% across import and re-export. Scope limit: that package was
Coursecraft-generated with deterministic GUIDs, so this shows Brightspace
*preserves* supplied identifiers. It does not show what a course copy, term
rollover, or Respondus regeneration does.

## 3. Points belong to the placement, not the question

`qmd_weighting` is recorded on the quiz `<item>`. The same authored question
placed in two quizzes can carry two different weights.

In ANAT, **every one** of the 166 groups of identical authored content varies in
points — typically 1.0 in a module quiz and 2.5 in the Midterm. Treating the
weight as part of a question's identity therefore destroys all real reuse.

Consequence: points are placement-scoped. A question-level maximum is only an
observation of one placement and must say so when placements disagree.

### 3a. A draw section can also declare a weight

A `RAND_` draw section may carry its own `qmd_weighting` in its direct
`qtimetadata`, alongside `qmd_numberofitems`. Counts of sections carrying a
weight: BIOL 1055 149, PHYS 1010 127, BIOL 1010 305.

**The section weight has never been observed to disagree with the item weight.**
Across 1,255 items sitting under a weighted section in BIOL 1055 and PHYS 1010,
agreement is 100% and disagreements are zero. Nor is the section weight ever the
only source: **no item in any observed export lacks its own `qmd_weighting`**
(BIOL 1055 544/544, PHYS 1010 852/852, BIOL 1010 18/18).

So the section value is a redundant declaration of the same award, not an
override, and reading points from the item is correct on all evidence to date.
The section weight is nevertheless retained on the draw structure as
`extensions.section_weight`, because the structure that declares an award cannot
otherwise be reconstructed from the model.

Two cautions. This has not been tested against an export where an item under a
weighted draw section carries no weight of its own; if one appears, which value
Brightspace awards is an open question that these observations cannot answer.
And a weighted section is not evidence that the weight applies to anything —
`qmd_numberofitems` is what marks the section as a draw.

## 4. Images live in the prompt HTML

Question images appear as `<img src="...">` inside the `<mattext>` HTML, and
sometimes as a `<matimage uri="...">` sibling. Paths are export-relative and use
backslashes in some corpora (`quizzing\Image.png`).

**The image can be the question.** ANAT's Module 1 Practical Quiz contains ten
items sharing one prompt sentence — "Identify what body plane was used to take
the following image." — with ten different images and ten different keyed
answers. Rendered plain text is identical across all ten.

Consequences:

- text-only comparison cannot distinguish these items; ordered asset bindings
  are part of what makes an authored question itself;
- bind assets by content digest rather than path, because the same export
  yields different paths under copy and reference asset modes;
- an `alt` attribute participates in rendered text and therefore in text
  matching, which is why the regression fixture uses an empty `alt`.

## 5. Answer keys by question kind

The keyed answer is not in one place:

| Kind | Where the key lives |
| --- | --- |
| Multiple choice, true/false, multi-select | `<respcondition>` with `<setvar>` 100 naming a `<varequal>` response ident |
| Short answer, multi-short answer | `<varequal>` text values under `<response_str>` |
| Fill in the blanks | one `<response_str>` per blank |
| Matching | `<respcondition>` pairs across `response_grp` blocks |
| Ordering | `response_grp` with `rcardinality="Ordered"` |
| Long answer | no computed key; `answer_key_material` may carry evaluator guidance |

Consequence: a fidelity check that reads only `correct_answer` misses the key
for five of the nine observed kinds. Typed answer payloads must all be part of
an authored-variant comparison.

## 6. Library coverage and match confidence

The canonical extractor currently recovers library-record identifiers, prompt,
type, choices, declared weight, and pool location from `questiondb.xml`, but it
does not recover a complete typed answer payload for dormant records that have
no placed quiz copy.

Consequence: these `library_only` records prove inventory and match coverage,
not full authored equivalence. They cannot receive an authored-variant
fingerprint or serve as the representative of a placed question's reuse class.
A placed quiz copy with its typed response payload remains the reviewable
question.

Library linkage also has three materially different evidence levels:

- a direct `<itemref>` is source-backed and needs no inferred duplicate match;
- a unique exact-content match may be resolved;
- a similarity-only match is a proposal, even when it is the best candidate,
  and retains its score and candidate list.

This distinction fixed a reporting undercount: BUMK 515 has 80 direct library
references and CHEM 1020 has 397, both of which an earlier relationship-only
metric reported as zero.

## 7. Sections, pools, and draws

- A quiz `<section>` may be a plain container, a wrapper D2L emits around the
  whole quiz, or a random-draw section.
- Random draws appear as sections with a requested count drawing from a pool.
- Section titles are frequently non-unique or empty. ANAT's Midterm has twelve
  sections all titled `Quiz Section`; other quizzes use a `Quiz Root Wrapper`
  title that is structural rather than authored.

Consequence: section titles are not identifiers and must be disambiguated for
display without being treated as evidence of distinctness.

## 8. Fields that look like content but are placement-scoped

These appear beside authored content in extracted rows and must never enter an
authored-variant comparison:

`correct_response_ids` (embeds the quiz item's own identifiers, e.g.
`QUES_24909_25365_A100384`), `question_weight`, `quiz_file`, `quiz_title`,
`quiz_item_*`, `section_*`, `pool_*`, `draw_count`, `is_random_draw_section`,
and the match metadata `evidence_level`, `match_basis`, `match_score`,
`match_note`, `match_candidates_json`.

`correct_response_ids` is the most dangerous: it varies per placement for the
same authored question, so admitting it would split every reuse group in the
corpus while still passing a small fixture.

## 9. What this reference does not establish

- It does not describe D2L's internal behavior or any documented API.
- It does not predict identifier behavior across course copy or rollover.
- It does not claim the observed corpora cover every Brightspace export shape;
  nine question kinds and one asset layout are what these exports contain.
- It makes no capability claim. Preservation of a shape in extraction is not
  evidence that the shape can be built or imported.
