from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

import quiz_variant_control_matrix as control_matrix
from quiz_variant_control_matrix import acceptance_failures, measure, render_markdown


def test_control_matrix_gates_every_fidelity_boundary() -> None:
    row = {
        "variant_collisions": 1,
        "variant_class_injectivity_failures": 1,
        "dropped_source_identifiers": 1,
        "library_equivalence_edges": 1,
        "hidden_variant_representatives": 1,
        "resolved_similarity_matches": 1,
        "collision_approval_safe": False,
    }
    assert len(acceptance_failures(row)) == 7


def test_cli_exit_uses_complete_approval_gate_not_collision_count(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        control_matrix,
        "measure",
        lambda _label, _export: {
            "label": "Synthetic unsafe",
            "occurrences": 1,
            "placed_entities": 1,
            "authored_variant_classes": 1,
            "variant_collisions": 0,
            "approval_safe": False,
        },
    )
    assert (
        control_matrix.main(
            ["--label", "Synthetic unsafe", "/does/not/need/to/exist"]
        )
        == 1
    )


def test_real_fixture_reports_direct_library_references_and_exits_safe() -> None:
    row = measure(
        "Mixed fixture", FIXTURE_ROOT / "mixed_inline_itemref_and_root_bank"
    )
    assert row["direct_library_references"] == 2
    assert row["inferred_exact_library_matches"] == 0
    assert row["inferred_similarity_library_matches"] == 0
    assert row["library_matched_questions"] == 2
    assert row["dropped_source_identifiers"] == 0
    assert row["acceptance_failures"] == []
    assert row["approval_safe"] is True
    assert "Direct refs" in render_markdown([row])


def test_similarity_fixture_never_resolves_similarity_only_match() -> None:
    row = measure(
        "Similarity fixture", FIXTURE_ROOT / "duplicate_pool_and_similarity"
    )
    assert row["inferred_similarity_library_matches"] > 0
    assert row["resolved_similarity_matches"] == 0
    assert row["approval_safe"] is True
