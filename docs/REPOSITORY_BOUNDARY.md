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

The distributed snapshot omits real course exports, generated course reviewbooks,
student data, credentials, live Brightspace evidence, the broader private fixture
corpus, and original Git history. The newer full-screen Workshop application is a
separate project and is outside this snapshot. Bindery Ledger's terminal display
is included.

## Feedback and contributions

Suggestions, bug reports, and pull requests are welcome in this repository and
will help improve the application and its supporting tools. Useful feedback
includes installation problems, unclear commands or output, extraction edge
cases, import validation gaps, and code organization. The source map in the
README and the included demonstration are starting points for exploring the code.

Testing with real course exports is encouraged. Reproductions may include real
course data or synthetic input. For a useful report, include:

- The course name and the affected quiz, question, or output file.
- The steps or exact command used, tool version or commit, Python version, and
  operating system.
- What you expected, what happened, and any exact error messages or relevant logs.
- The relevant export, excerpt, or generated output you have permission to share.

Pull requests may propose changes to any included code, including shared files.
Access to the original Workbench or bundle repositories is not required to
contribute here. Maintainers will coordinate accepted shared-code improvements
with Workbench and promote them through the bundle's vendor process. Terminal
presentation, setup, and documentation improvements are also welcome here.

The vendor check records whether shared files match their upstream pin. A proposed
edit to a pinned file can therefore cause `vendor_from_workbench.py --check` to
report drift. Describe the change in the pull request and leave the pin update to
the maintainer's reviewed promotion process.

Contributions will be acknowledged in this repository, with accepted improvements
credited in project documentation or release notes.

The full test suite runs in the development projects. This repository provides
the built-in synthetic demonstration and drift/input checks. No local check
establishes a successful live Brightspace import or rendering result.
