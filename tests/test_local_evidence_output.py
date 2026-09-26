from __future__ import annotations

from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

import extract_quiz_pool_review
from local_evidence_output import RepoUnsafeOutputError, guard_repo_safe_output


def test_shared_guard_rejects_tracked_lane_and_accepts_ignored_lane() -> None:
    tracked = REPO_ROOT / "tracked-extractor-output-sentinel"
    ignored = (
        REPO_ROOT
        / "workspace"
        / "review"
        / "quiz_binder_runs"
        / "guard-test__extractor"
    )
    assert not tracked.exists()
    with pytest.raises(RepoUnsafeOutputError, match="not git-ignored"):
        guard_repo_safe_output(tracked, repo_root=REPO_ROOT)
    guard_repo_safe_output(ignored, repo_root=REPO_ROOT)
    guard_repo_safe_output(tracked, repo_root=REPO_ROOT, allow_tracked=True)
    assert not tracked.exists()
    assert not ignored.exists()


def test_extractor_requires_an_explicit_output_directory() -> None:
    with pytest.raises(SystemExit) as exc_info:
        extract_quiz_pool_review.main(
            [str(FIXTURE_ROOT / "quiz_only_inline_tf")]
        )
    assert exc_info.value.code == 2


def test_extractor_refuses_tracked_output_before_creating_it(capsys) -> None:
    output = REPO_ROOT / "tracked-extractor-output-sentinel"
    assert not output.exists()
    result = extract_quiz_pool_review.main(
        [
            str(FIXTURE_ROOT / "quiz_only_inline_tf"),
            "--output-dir",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "not git-ignored" in captured.err
    assert not output.exists()
