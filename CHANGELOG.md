# Release changes

## Unreleased — assessment review handoff, 2026-09-25

- Add one editable tab per assessment with labelled source pools, collapsible
  groups, visible question types, source points and available feedback.
- Package relative workbook image links with a local Images folder. Fix missing
  links caused by moving the working workbook away from its extracted assets.
- Preserve readable display text separately from original source markup and the
  full model, including source option identities.
- Collect assessment-tab revisions through the existing importer and verify
  source cells, companion hashes and rich-content revision holds.
- Connect the adapter to Quiz Workshop, Bindery Ledger real-export Unbind and
  the synthetic demonstration. Add independent prepare/import commands.

This is a local development candidate. No new release or Brightspace import has
been published. The recorded VERSION still identifies the rc.8 release base;
the exact candidate code is identified by its Git commit and Workbench pin.

## 0.1.0-rc.8 — 2026-09-17

- Show source question types and preserve choice identity, rich response content
  and bounded QTI scoring defaults during extraction and normalization.
- Add a separate deterministic XLSX/JSON fresh-draft intake command, synthetic
  templates, explicit question types, pools, draw counts, points and feedback.
  Valid intake remains unapproved and extraction-only.
- Refuse target identifier collisions, unresolved source projection, unsupported
  feedback loss and missing or ambiguous rich-content assets before building.
- Preserve explicit HTML/XHTML and safely escape declared plain text, including
  prompts, options and evaluator keys. Account for MathML fallback images and
  refuse unsupported base-URL changes without rewriting authored content.
- Ship the versioned draft contracts and operator guidance through the public
  runtime allowlist; retain the guided Quiz Workshop and existing synthetic TUI.

Workbench producer: `a2ceeff6955cf015f93fba761cbe57846aa92613`.
This release does not add Word intake, typed-equation conversion, a real-workflow
full-screen TUI, new quiz serializers, automatic approval or Brightspace import
and rendering evidence. Course-specific reviewer layouts remain downstream.

## 0.1.0-rc.7 — 2026-09-15

- Finalize the guided Quiz Workshop with direct resumption of ready Rebind
  results and an explicit Quit action.
- Cancel on end-of-input instead of accepting default prompt confirmations.
- Allow blank descriptive review metadata by default in Quiz Workshop, while
  preserving explicit acceptance, validating supplied dates and providing a
  required policy. The generic producer retains its strict default.
- Verify workbook and Compose-artifact hashes before displaying readiness or
  building a package; require recomposition after changes.
- Retain local-only, readiness-gated package creation and the synthetic proof.

The full-screen real-export interface, Word intake, rich-math review adapters
and typed-equation conversion remain future work. Local package validation does
not establish a Brightspace import or round trip.

## 0.1.0-rc.6

First public runtime-minimized guided Unbind, Compose and Rebind workflow,
including full-library extraction and one/several/all quiz selection.
