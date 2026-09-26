# Focused reviewer tests

Run this suite from the repository root after following [INSTALL.md](../INSTALL.md):

```sh
.venv/bin/python -m pytest -q tests/test_quiz_sme_edit_scenarios.py
```

The 24 focused scenarios exercise synthetic assessment review and guarded
import:

- accepted, open, and rejected question edits;
- tampered source images, protected workbook fields, and unsupported rows;
- answer-option validation, assessment settings, and blank-template intake;
- accepted image replacement and question additions, including package and
  re-extraction checks;
- rejected replacements leave authored content and assets unchanged, including
  when a replacement folder redirects outside the review packet.

The fixtures in `fixtures/quiz_source_choice_fidelity/` contain synthetic questions
and a sanitized native export of a synthetic Multi-Select question. They contain
no course question-bank text. They check duplicate
answer labels with distinct source IDs, image-only options, MathML, a literal
delimiter, and native True/False, Multi-Select, and Ordering behavior. Its
[fixture notes](fixtures/quiz_source_choice_fidelity/README.md) describe the
coverage.

The broader synthetic and sanitized regression corpus in this directory covers the included
quiz tooling. It does not contain real course exports or the full private
Workbench test corpus. Passing these tests is not an import test against a
Brightspace tenant.
One Workbench check that resolves registry links against its private live-import
receipt log is skipped here because that log is intentionally not included.

Package tests build the package directory and ZIP, check package conformance,
validate strict asset closure, and unbind/re-extract synthetic image-replacement
and new-question packages. A dedicated scenario also accepts a revised prompt,
response options, and answer key, builds a scoped package under an exact,
short-lived synthetic candidate authorization, and checks all three values
after re-extraction. The question remains `extraction_only` throughout; this
test authorization is only for the synthetic candidate and does not change its
build evidence. A package that passes these checks is locally validated and
still needs a Brightspace sandbox import and roundtrip before it can be
described as tenant-verified.

## Terminal and distribution integration

`test_quiz_terminal_roundtrip.py` runs Unbind → workbook edits → Compose → scoped
candidate authorization → Rebind → Unbind. It checks changed prompt/options/key,
a new pool member, a replacement image, and accepted settings together. It also
requires Compose to stop if an accepted revision cannot be applied.

`test_public_distribution.py` builds and extracts the downloadable archive and
checks its full vendor pin, test inclusion, and sanitized fixture metadata.
Run all included checks with `python -m pytest tests`. On Python 3.13 the current
result is 652 passed and one intentional skip. Test counts describe this checkout,
not the larger private development tree or a live tenant.

GitHub Actions repeats the full suite, source-pin and distribution checks, and
synthetic demonstration on Python 3.11, 3.12 and 3.13. The workflow uses read-only
repository permissions and does not approve or merge contributions. Proposals
that change pinned source files can report vendor drift until a maintainer
coordinates and verifies the upstream promotion.
