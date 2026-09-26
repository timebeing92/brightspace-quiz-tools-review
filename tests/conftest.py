from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    """Keep private import receipt checks skipped in the public snapshot."""
    repo_root = Path(__file__).resolve().parents[1]
    if (repo_root / "docs/IMPORT_TEST_LOG.md").is_file():
        return
    skip = pytest.mark.skip(
        reason="private import receipts are not part of the public review snapshot"
    )
    for item in items:
        if item.name == "test_build_capability_registry_receipt_references_resolve":
            item.add_marker(skip)
