# Focused reviewer tests

Run this suite from the repository root after following [INSTALL.md](../INSTALL.md):

```sh
.venv/bin/python -m pytest -q tests/test_quiz_sme_edit_scenarios.py
```

The 18 scenarios exercise synthetic assessment review and guarded import:

- accepted, open, and rejected question edits;
- tampered source images, protected workbook fields, and unsupported rows;
- answer-option validation, assessment settings, and blank-template intake.

The fixture in `fixtures/quiz_source_choice_fidelity/` was authored solely for
regression testing and contains no course question-bank text. It checks duplicate
answer labels with distinct source IDs, image-only options, MathML, a literal
delimiter, and native True/False, Multi-Select, and Ordering behavior. Its
[fixture notes](fixtures/quiz_source_choice_fidelity/README.md) describe the
coverage.

Three accepted-edit scenarios are marked as strict expected failures because this
repository snapshot currently excludes accepted prompt, response-option, and
answer-key revisions. They remain visible in the test run; if a later snapshot
fixes them, pytest reports an unexpected pass until the markers are removed.

This is a focused reviewer suite, not the full Workbench test corpus or an import
test against a Brightspace tenant.
