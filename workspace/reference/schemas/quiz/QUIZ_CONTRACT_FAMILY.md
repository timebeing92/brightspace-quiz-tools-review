# CourseCraft Quiz Contract Family

Status: authoritative v1 contract family. Phase 4 local implementation
completed 2026-07-15; the bounded Phase 5 registry result was applied and
verified 2026-07-21. The exact promoted strict-model route, tested assets, and
named settings now carry the evidence levels recorded in the live registry;
that result does not make arbitrary extracted questions buildable.

These are the authoritative upstream quiz contracts. They are versioned and
tested. The canonical extractor emits the model/run contracts; the generic quiz
builder consumes a strict authoring profile of the model plus a settings receipt
and emits settings/run receipts; the validator checks the projection and receipt
checksums; and the differ prefers permanent question codes before fallback
content matching. Existing review artifacts and the workbook CLI remain
compatible.

## Contracts

| Contract | File | Purpose |
| --- | --- | --- |
| `coursecraft.quiz/1` | `quiz_model_schema.json` | Evidence-preserving normalized quizzes, structures, questions, assets, resources, relationships, evidence, lineage, settings observations, annotations, and diagnostics. |
| `coursecraft.quiz_run/1` | `quiz_run_schema.json` | Source/export fingerprint, source lineage, producer identity, schema hashes, inputs, emitted artifacts, and completed/skipped/failed/unresolved capabilities. |
| `coursecraft.quiz_settings/1` | `quiz_settings_schema.json` | Four-layer settings inputs plus a per-value resolution receipt recording precedence, defaults, and coercions. |

The family is deliberately smaller than the list of possible review or build
projections. XLSX, CSV, Markdown, HTML, and package XML remain human or platform
surfaces derived from versioned JSON facts.

### Fresh draft adapter interfaces

`scripts/quiz_draft_intake.py` separately validates two versioned interfaces:

| Interface | Schema file | Purpose |
| --- | --- | --- |
| `coursecraft.quiz_draft/1` | `quiz_draft_intake_schema.json` | Deterministic fresh question, response, pool and draw input; XLSX tabs map to this JSON shape. |
| `coursecraft.quiz_draft_intake_report/1` | `quiz_draft_intake_report_schema.json` | Intake result, source/parser provenance and counts. |

The adapter validates these schemas itself; the generic normalized-model
validator remains scoped to the three contracts above. Successful intake emits
`coursecraft.quiz/1` with every new question still `extraction_only`. This does
not approve content or grant builder support. Required values, blank/text rules,
numeric precision and diagnostic behavior are documented in
`docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md`.

The simplified XLSX projection therefore keeps its reviewer-facing inventory
and placement views distinct from its technical identity view. `All Questions`
is the complete library-aware source inventory, and `Quiz Occurrences` is the
ordered placement view. `Question Entities` is a derived technical appendix for
deduplication and joins; it is hidden by default and placed last because a
typical content reviewer does not need it. Hiding that projection does not
remove any entity from `coursecraft.quiz/1`.

`quiz_build_capabilities.json` is a machine-readable capability registry, not a
fourth data contract. It records question, structure, settings, and asset support
separately with evidence levels. In particular, the model's ability to preserve
an observed shape never promotes that shape to builder support.

## Evidence And Product Boundary

Do not describe the quiz family as awaiting proof of basic creation/import.
Different parts of the surface have different, already-recorded evidence:

- extraction/review is exercised across a broad real-export corpus, including
  26 audited corpus roots and all nine currently observed question kinds;
- the historical BUMG/MGT lineage preserves original SME workbook, translated
  workbook, generated packages, and later course-export XML; all three generated
  quiz resource codes reappear in the later course export;
- the generic builder has 2026-06-16 L4 import/re-export receipts for multiple
  choice, true/false, all-or-nothing multi-select, long answer, question-library
  banks, `itemref`, random draws, grade/module links, and library-only packages;
- the focused Phase 5 campaign subsequently raised only the exact promoted
  strict-model route, tested resolved-asset path forms, and named settings to
  the bounded L4 levels recorded in `quiz_build_capabilities.json`; route
  evidence remains distinct from question-instance eligibility.

The schema answers what can be represented without losing identity,
relationships, evidence, uncertainty, or unknown shapes. The registry and run
receipts answer what may be built and claimed. Product surfaces must preserve
that separation.

The accepted product orientation is one Quiz Binder service with two entry
paths—existing-export unbinding/review and new/rebuilt quiz composition—meeting
at a shared `coursecraft.quiz/1` review station. JSON remains machine truth;
XLSX/CSV/HTML and any later Binder interface are projections, not new contract
owners.

Durable evidence and product rationale:
`docs/project/quiz-consolidation/QUIZ_BINDER_EVIDENCE_AND_PRODUCT_BOUNDARY_2026-07-20.md`.
Live receipts: `docs/IMPORT_TEST_LOG.md`.

## Identity Rules

Every source export/package receives two different identities:

- `source_key` identifies one exact artifact or file set and is derived from its
  SHA-256 fingerprint. A refreshed export therefore receives a new source key.
- `source_lineage_key` identifies the durable course/package family across
  refreshes. It is assigned or reconciled from defensible course-level evidence,
  not guessed from bank titles.

Every quiz, structure, question, asset, and package resource has an
`entity_key`. Key priority is:

1. permanent `question_code` or another deliberately assigned durable code;
2. a stable source alias such as a scoped D2L global identifier, combined with
   `source_lineage_key`;
3. a persisted assigned key when no stable alias exists.

For a question, the stable source alias is **the source item's own** identifier.
A matched question-library record is evidence about an item, not the item's
name, and never takes priority in key derivation. Preferring it merged
materially different quiz items — different keyed answers, different images —
into one entity where only the first survived.

All observed D2L identifiers remain in `source_aliases`, the item's own and any
matched library record's alike, under distinct namespaces.

In reviewer projections, `display_identifier` is a CourseCraft-selected display
convenience: permanent code first, then a preserved source alias, then the
normalized entity key. `question_entity_key` is CourseCraft-generated and is
not package-native. The values in `source_aliases` come from the package, while
their namespaces, scopes, and `namespace=value` presentation are CourseCraft's
organizing schema. These distinctions are documentation clarifications within
v1, not a contract-version change.

Content fingerprints are matching hints only: wording, options, feedback, or
scoring can change while the entity remains the same. That rule answers a
*cross-refresh* question and is what lets a reviewer decision survive an edited
export. It does not license a *within-export* collision, so it is bounded by:

> **Within a single extraction, every set of rows sharing an entity key must
> resolve to exactly one authored-variant fingerprint.**

Divergence in placement-scoped facts — points, quiz, section, ordinal, pool,
draw — never violates this. Divergence in authored semantics always does. In
Brightspace the question weight belongs to the quiz item, so points are
placement-scoped and are never identity-bearing.

Sameness of authored content is recorded rather than inferred from key
collisions. Questions whose authored-variant fingerprints agree are linked by
`same_as` relationships with `source_kind: authored_variant_equivalence`, using a
stable representative and N-1 records. A question-library match is a separate
`same_as` with `source_kind: question_library_match` pointing at a
library-question entity, so a library link and authored sameness stay
distinguishable.

A `library_only` entity is a coverage record when extraction has not recovered
its complete answer payload. It may retain identifiers, prompt, type, and pool
location and may be the target of `question_library_match`, but it does not
receive an authored-variant fingerprint and cannot join or represent an
`authored_variant_equivalence` class. Missing answer semantics cannot prove
content equality.

Library-match status follows evidence strength. A direct source reference and
a unique exact-content match may be resolved. A similarity-only match remains
`proposed`, with score and candidates retained for review; similarity never
becomes resolved merely because it is the best available candidate.

`scripts/quiz_contracts.py` intentionally excludes the exact export fingerprint
from entity-key generation, so a refreshed export does not mint new identities
for unchanged entities.

Source observations behind these rules, with the corpora that support them, are
recorded in `BRIGHTSPACE_QUIZ_XML_SOURCE_OBSERVATIONS.md`.

## Evidence, Lineage, And Judgment

The model keeps these layers separately attributable:

- exact or referenced source evidence;
- deterministic extraction;
- deterministic normalization;
- human reviewer annotations and proposed revisions;
- approved changes;
- model-assisted translations or proposals;
- generated package values and platform rewrites.

Relationships are records, not implied columns. Inline membership, direct
`itemref`, bank membership, pool/draw selection, asset use, manifest resources,
derivation, generation, and equivalence can each retain source evidence and
diagnostics. An incomplete relationship uses `status: incomplete` and a null
target; an ambiguous relationship retains candidate keys/scores rather than
choosing one silently.

## Evolution And Unknown Shapes

The v1 envelopes require identity, provenance, relationship-safe routing, and
explicit state while leaving authored or type-specific data optional. The
following distinctions are first-class:

- `absent`
- `unknown`
- `not_applicable`
- `unresolved`
- `unsupported`

An unseen question uses `kind: unknown`, preserves its exact `source_kind`,
keeps raw response/scoring material or a durable source reference, carries
diagnostics, and makes no build-support claim. Namespaced additive information
belongs in `extensions`; consumers must preserve extension members they do not
understand. A consumer may inspect and preserve an unknown contract version
with a warning, but transformation must stop until that version is understood.

Breaking changes to required fields, meanings, names, or join semantics require
a new contract major version. Additive optional fields and new extension members
do not.

## Settings Resolution

Build inputs use the accepted precedence order:

1. `hard_default`
2. `profile`
3. `quiz_decision`
4. `question_override`

`source_observation` is a separate evidence lane and never silently becomes a
build instruction. Each resolution records all considered inputs, the winner,
the effective state/value, whether a hard default won, and any coercion.

The first approachable quiz-level decision projection, derived from the BUMG
workbook/package lineage and observed XML vocabulary, is:

- active/inactive state;
- attempts allowed;
- time limit, clock display, and enforcement;
- forward-only navigation;
- section/pool draw counts;
- question points and answer-choice shuffle;
- feedback/hint/solution display decisions when the source or build purpose
  makes them consequential.

Dates, grade/module links, paging, submission views, security/lockdown flags,
late rules, calculation modes, passwords, and uncommon vendor settings remain
available as observed facts or profile/advanced inputs. They should not become
an intimidating default workbook column set merely because the XML contains
them.

## Phase 4 Authoring Profile

The authoring projection is a strict, documented profile of
`coursecraft.quiz/1`; it is not another overlapping schema. The builder accepts
one selected quiz only when:

- all model/schema semantics validate in transformation mode;
- the quiz reaches one or more resolved `draw` structures through `contains`;
- every draw has exactly one resolved `draws_from` edge to a bank/pool;
- every included question has a resolved `member_of` edge, a permanent
  question code, and `import_verified` or `roundtrip_verified` build support;
- the question kind is listed as round-trip verified in
  `quiz_build_capabilities.json`;
- referenced assets use resolved `uses_asset` edges, checksum-matching source
  files, and safe package-relative paths; and
- every known build setting has an approved projection.

The current field-proven build boundary remains multiple choice, true/false,
all-or-nothing multi-select, and long answer, plus question-library banks,
`itemref` joins, random draws, optional grade/module links, and library-only
packages. The exact promoted Phase 5 route also has bounded L4 evidence for the
tested nested/shared/percent-encoded asset forms and the named settings listed
in the registry. This is not a general promotion for untested paths, settings,
question kinds, or extraction-only question instances.

The builder writes `quiz_build.settings.json` and `quiz_build.run.json` to a
sibling receipt directory. That directory is excluded from the import ZIP. The
materialized settings receipt records hard defaults for any consequential value
not present in the supplied settings receipt, including per-question answer
randomization.

## Examples And Validation

- `examples/mixed_storage.example.json`: root bank item, section-nested item,
  direct `itemref`, inline item, fixed/random sections, settings evidence, and
  review annotation.
- `examples/observed_question_kinds.example.json`: all nine question kinds
  observed in Phase 0/1, with extraction and build-support claims separated.
- `examples/unknown_incomplete.example.json`: unknown hotspot-like response,
  missing optional content, vendor extensions, raw evidence, unsupported build
  status, and an incomplete relationship.
- `examples/settings_resolution.example.json`: all four settings layers plus a
  source observation and unresolved setting.
- `examples/run_receipt.example.json`: producer/source identity, contract hashes,
  files, and step outcomes.

Validate records with:

```bash
python3 scripts/validate_quiz_contract.py \
  workspace/reference/schemas/quiz/examples/*.json \
  --mode transform
```

Inspection mode warns and preserves unknown versions. Transformation mode fails
on an unknown version or semantic break. JSON Schema validity is supplemented
by checks for unique identities, dangling evidence/diagnostic/entity joins,
relationship targets, settings precedence, receipt hashes, and capability
artifact references.

## Compatibility And Migration

`workspace/reference/schemas/quiz_intake/QUIZ_INTAKE_CONTRACT_DRAFT.md` remains
a useful record of the experimental v0 workbook vocabulary and its verified
builder input. It is superseded as the machine-contract proposal by this family,
but the current builder workbook contract remains supported. Phase 3 delivered
the extractor adapter while preserving JSON/XLSX/Markdown outputs. Phase 4
delivered the strict model-to-builder authoring profile, settings/run receipts,
contract-aware validation, safe asset projection, and stable-code-first diffing.
No extraction shape becomes buildable merely because this contract can
represent it. Phase 5 supplied capability-specific closure for the exact
promoted model/assets/settings path; it did not invalidate the prior L4
four-type structural boundary or broaden support beyond the registry's named
scope.
