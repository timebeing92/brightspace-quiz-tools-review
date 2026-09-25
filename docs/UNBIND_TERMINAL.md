# Real-export Unbind in the Quiz Binder terminal

The ordinary Quiz Workshop now stages real-export **Unbind**, human review,
**Compose**, and readiness-gated local **Rebind**. The older full-screen
synthetic proof remains a separate advanced surface.

Run the guided workflow:

```bash
python3 scripts/quiz_terminal.py
```

Or run Unbind directly:

```bash
python3 scripts/quiz_terminal.py unbind /path/to/brightspace-export.zip \
  --workspace /path/to/new-workflow-folder
```

The workspace contains the canonical `coursecraft.quiz/1` model, detailed and
reviewer workbooks, the compact review projection, extraction and Unbind
receipts, authoring-readiness report, status station, and the quiz-first Reading
Room. Resolved image assets are copied by default so the workbook and Reading
Room remain portable together.

Edit `compose/reviewer_working.xlsx`; the terminal preserves and hash-checks
`compose/reviewer_baseline_DO_NOT_EDIT.xlsx` before Compose. The default workbook
has assessment tabs and local image links. Share the complete `compose/` folder,
including Images and the import companions.

Bindery Ledger's `quiz_binder_tui.py --unbind EXPORT --output-dir OUTPUT` route
creates the same packet at `OUTPUT/review/assessment/`. Its read command checks
the packet's baseline, model and image hashes as well as the Unbind evidence.

The executable producer scripts and schemas are contained in Quiz Bundle. They
are byte-pinned to an approved Workbench commit for provenance, but neither the
terminal nor a downstream Hall deployment connects to or imports from the
private Workbench repository at runtime.

Read a completed run later:

```bash
python3 scripts/quiz_terminal.py status /path/to/workflow-folder
```

The reader re-hashes the complete declared evidence chain and Reading Room
before it prints the content-minimized run summary. It does not print question
or answer text.

An aggregate export containing several quizzes may close successfully while
the initial authoring-readiness report records `quiz_selection_required`. That
is a transition gate, not an extraction error. Unbind defaults to all quizzes
and the complete available Question Library because quiz placements and
reusable question bodies can be split between quiz XML and `questiondb.xml`.
Its optional `--quiz-entity-key` affects the initial readiness report only; it
does not filter evidence.

## Compose/Rebind boundary

Compose is now guided for a real Unbind workspace, but it does not make
extracted questions build-approved. It selects one, several, or all quizzes,
materializes explicit reviewer decisions once, and shows a separate readiness
result for each selection. Rebind is exposed one quiz/package at a time only
when that exact result is ready under the pinned bounded capability profile. It
generates and validates locally; it offers no package import or live
Brightspace action.

Use the synthetic journey to inspect a known build-ready path:

```bash
python3 scripts/quiz_binder_tui.py --output-dir output/proof --yes
```

Unbind performs no network operation and no Brightspace import/re-export.
