# Quiz package projection fidelity gates

These corrections apply to existing Workbench authoring projection. They do not
promote question-instance evidence or add question serializers.

An explicit `coursecraft.source_choice_projection.state=unresolved` blocks
readiness and direct building even if an operator populates option keys or the
question already declares verified build support. Resolve the preserved source
evidence through the reviewed normalization path; filling cells cannot override
an unresolved source projection.

## Target identity

Historical target identifiers remain unchanged for non-colliding inputs. The
shared `safe_ident` mapping is intentionally retained for compatibility, but
readiness and direct building now reject collisions after both pool-code
projection and final QTI identifier normalization. For example, `Q-A`, `Q_A`
and `q.a` cannot coexist as separate questions in one package. Distinct pool
codes must not merge bank memberships. Draw identifiers are checked as well.
Repeated references to the same pool are not themselves identifier collisions;
the existing duplicate-placement restrictions still apply independently.

The diagnostic identifies the target namespace and stable source owner.
Resolve colliding authoring codes through review; the builder never changes
canonical entity keys or silently appends a suffix. Rejection happens before
package output is created. Legacy workbook builds receive the same target
collision protection.

## Explicit content formats

Model prompt, option and evaluator-key formats survive the authoring projection
into serialization. `plain_text` escapes `<`, `>` and `&` before entering an HTML
mattext field. Thus literal `<b>` remains visible text rather than formatting,
and a literal `&amp;` is not silently interpreted as an entity. Explicit HTML and
XHTML retain their original fragment content. The legacy workbook input, which
has no declared format columns, retains its historical heuristic.

True/False uses the previously verified plain-text True then False labels.
Formatted, reordered or custom model labels are refused rather than silently
replaced. Standalone MathML/LaTeX and other unsupported encodings continue to
require separately reviewed conversion. These checks establish local
serialization behavior, not browser or Brightspace rendering proof.

## Rich HTML media coverage

Readiness and direct projection inspect HTML/XHTML prompts, options, manual keys
and feedback using an HTML attribute parser. Each local resource reference must
resolve to exactly one selected asset through that question's resolved
`uses_asset` relationship. The asset must pass the existing in-root source,
package-path and checksum checks. Reference matching uses the existing
percent-decoded archive-member mapping; query strings and fragments do not
change the required file. A source path is not an implicit package-path rewrite.
The original HTML is retained exactly.

The reference scan includes MathML `altimg` fallback images. URL matching trims
only ASCII space; ASCII control characters are refused, and Unicode whitespace
remains part of the filename. A nonbreaking space therefore cannot silently
bind to a differently named packaged file.

Missing, ambiguous, unsafe and unbound references block generation before any
package output. Having an asset elsewhere in the model or referenced by another
question is insufficient. Byte-identical asset aliases still require one
unambiguous binding for each reference.

HTTP(S), protocol-relative URLs, mail links and fragment links retain the
validator's established external/non-packaged semantics. No remote fetch or
availability claim is made. Tenant-relative paths, data URIs, unsupported
schemes, duplicate reference attributes, base URLs (including `object.codebase`
and `xml:base`), CSS resource expressions,
`srcset`, `srcdoc` and similar unsupported reference containers require an
explicit reviewed delivery profile instead of being silently skipped.
Plain-text fields are escaped and never scanned as active HTML. This is a
bounded check of authored HTML references, not recursive validation of arbitrary
JavaScript or dependencies inside binary/media files.

## Feedback accounting

The current serializer has one supported evaluator-key slot for Written
Response. It accepts either one nonempty manual key or one nonempty
`answer_key` feedback record. It rejects:

- general, correct, incorrect, choice, hint and other unrepresented channels;
- multiple answer-key feedback records, including identical duplicates;
- a feedback key shadowed by a manual key;
- an answer-key feedback record or manual key on another question kind;
- an empty key that would otherwise trigger invented fallback guidance.

The canonical model and source records remain intact on refusal. Broader
feedback serialization requires its own target mapping and verification; the
operator should not delete feedback merely to clear a readiness gate.

Synthetic checks live in `tests/test_quiz_projection_fidelity.py`; they exercise
both readiness and direct builds, inspect serialized prompt/option/key content,
assert source immutability, and retain historical workbook behavior.
