# Repository scope and source provenance

`brightspace-quiz-tools-review` is a standalone, runnable source snapshot of
Quiz Workshop, Bindery Ledger, and their shared quiz tools. It begins with new
Git history and includes the runtime, documentation, schemas, drafting templates,
and synthetic fixtures needed by the demonstration.

## Source projects

| Project | Responsibility |
| --- | --- |
| `coursecraft_workbench` | Quiz schemas, extraction and normalization, review semantics, capability evidence, and shared regression tests |
| `brightspace-quiz-bundle` | Terminal interfaces, installation, distribution, and the shared-code pin |
| This repository | A runnable snapshot for use and review, with its own introductory documentation |

No checkout of either source project is needed at runtime. All required Python
implementation is included under `scripts/`.

`REVIEW_MANIFEST.json` records the originating bundle commit, Workbench commit,
release base, and file hashes for this snapshot. Its `candidate_commit` belongs
to the originating bundle; this repository has its own commit history. The
manifest excludes its own bytes and generated or ignored files.

`upstream/workbench_pin.json` records hashes for the shared files included here.
It is the runtime subset of the source bundle's larger pin. `VERSION` records the
`0.1.0-rc.8` base; the assessment-review additions are development changes above
that release. There is no release tag for this snapshot.

## Included and omitted

The source snapshot includes both terminal interfaces, standalone extraction and
review commands, guarded decision import, draft intake, readiness checks, package
assembly, and local validation. It also includes synthetic examples and blank
drafting templates.

Real course exports, generated course reviewbooks, student data, credentials,
live Brightspace evidence, the broader private fixture corpus, and original Git
history are omitted. The newer full-screen Workshop application is a separate
project and is outside this snapshot. Bindery Ledger's terminal display is included.

## Reviewing and changing code

Use the source map in the README and the synthetic demonstration to explore the
implementation. Useful feedback includes installation problems, unclear commands
or output, extraction edge cases, import validation gaps, and code organization.
Describe the command and observed behavior, and use synthetic input when sharing
a reproduction.

Shared behavior should be fixed in Workbench and promoted through the bundle's
vendor process. Changes to terminal presentation, setup, or introductory
documentation belong to the bundle or this review snapshot. Directly changing a
pinned shared file here causes `vendor_from_workbench.py --check` to report drift;
that check should not be bypassed by editing hashes to match.

The full test suite runs in the development projects. This repository provides
the built-in synthetic demonstration and drift/input checks. No local check
establishes a successful live Brightspace import or rendering result.
