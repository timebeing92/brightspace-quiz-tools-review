# Fresh quiz draft examples

`blank.xlsx` is the fresh-authoring template. `nine_types.json` and
`nine_types.xlsx` are synthetic examples with no course questions. They exercise
all nine intake types; five remain draft-only for package generation.

The normative structural contracts are
`workspace/reference/schemas/quiz/quiz_draft_intake_schema.json` and
`quiz_draft_intake_report_schema.json`. The adoption note below specifies the
additional type-specific semantic rules. The input adapter remains separate
from the canonical `coursecraft.quiz/1` schema.

## Rebuild the generic workbooks

The Workbench owns `scripts/build_quiz_draft_templates.mjs`. It reads the checked-in
synthetic JSON and asks the runtime intake module for current headers and the
shared catalog; no CHEM-specific path, name, sheet ID or course content is used.
`template-spec.json` is a generated snapshot, not a second catalog authority.

Template authoring requires Node and `@oai/artifact-tool` (verified with the
bundled 2.8.58+ API), plus Python with the Workbench requirements. Point
`--artifact-modules` to the dependency environment containing `node_modules`.
It does not install dependencies. Example:

```bash
node scripts/build_quiz_draft_templates.mjs \
  --output-dir /tmp/quiz-draft-templates-new \
  --python .venv/bin/python \
  --artifact-modules /path/to/artifact-runtime \
  --render
```

The output directory must not exist. Outputs are `blank.xlsx`, `nine_types.xlsx`,
the current `template-spec.json` and a compact inspection receipt. `--render`
adds PNGs for visual review of all seven tabs. After validation, refresh these
three checked-in fixture files from the generated outputs in a separate step.
The workbook values, formats, validation and layout are reproducible; XLSX
archive metadata is not used as content identity.

Runtime intake needs only Python and makes no model or network calls:

```bash
python scripts/quiz_draft_intake.py workspace/reference/examples/quiz_draft/nine_types.xlsx \
  --output-dir /tmp/quiz-draft-intake-new
```

Text columns are explicitly formatted as Text. Enter a literal answer such as
`0` or `False` as text. If pasted data produces a numeric or boolean cell in a
text field, intake refuses it with the sheet, physical row and field instead
of discarding or coercing the value.

See `docs/project/quiz-consolidation/DETERMINISTIC_QUIZ_DRAFT_INTAKE_2026-09-16.md`.
