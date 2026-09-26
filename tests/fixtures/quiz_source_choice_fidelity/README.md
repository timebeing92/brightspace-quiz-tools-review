# Synthetic source-choice fidelity fixture

Authored solely for regression testing; contains no course question-bank text.

`native_multiselect.xml` is the single synthetic question exported after its
answer key was set in Brightspace's native editor on September 26, 2026. Tenant
object/response IDs were replaced with synthetic identifiers, preserving joins.
It establishes the native Add 1 / complementary Add 0 all-or-nothing structure.
The earlier generic Set 100 / empty fallback structure lost its key on import.
The complete sandbox export is not included.

- Duplicate labels have distinct native response IDs and one keyed choice.
- Two choices contain only HTML images with empty alternative text.
- MathML and a literal ` || ` remain inside rich option material.
- Genuine Multi-Select uses the existing all-or-nothing conjunction form.
- Genuine Ordering uses D2L's positive/complement counter form with explicit
  zero defaults. Its omitted `continue` attributes match the preserved native
  `quiz_xml/ordering_response_group` corpus; they are recognized as that bounded
  exporter profile, not interpreted using generic QTI default-No semantics.
- True/False retains its native type and explicit response conditions.

Negative tests mutate copies in memory or a pytest temporary directory. They
exercise incomplete/ambiguous scoring, conflicting occurrence keys, missing
facts, metadata disagreement, outcome bounds/defaults, continuation, and unknown
scoring operations without changing the source fixture. Sparse single-choice
conditions honor the bounded QTI 1.2 defaults: implicit integer `SCORE=0`, an
omitted `decvar defaultval=0`, and omitted `setvar varname=SCORE/action=Set`.
Named variables such as `que_score` require a declaration when omitted clauses
depend on their initial value; contradictory defaults or unsupported programs
remain unresolved. These rules come from the [QTI 1.2 XML Binding](https://www.imsglobal.org/question/qtiv1p2/imsqti_asi_bindv1p2.html),
sections 3.5.24, 3.6.19, and 3.6.21. `continue` retains its standard default-No
behavior outside the explicitly documented native D2L Ordering profile above.
