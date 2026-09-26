from __future__ import annotations

from pathlib import Path
import sys
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "quiz_xml"
sys.path.insert(0, str(SCRIPT_ROOT))

import extract_quiz_pool_review
from local_evidence_output import RepoUnsafeOutputError, guard_repo_safe_output


@pytest.fixture
def git_repo(tmp_path):
    # Archives have no .git directory. Exercise this guard against an isolated
    # temporary repository so its test never depends on distribution format.
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("/workspace/review/quiz_binder_runs/\n")
    return root


def test_shared_guard_rejects_tracked_lane_and_accepts_ignored_lane(git_repo) -> None:
    tracked = git_repo / "tracked-extractor-output-sentinel"
    ignored = (
        git_repo
        / "workspace"
        / "review"
        / "quiz_binder_runs"
        / "guard-test__extractor"
    )
    assert not tracked.exists()
    with pytest.raises(RepoUnsafeOutputError, match="not git-ignored"):
        guard_repo_safe_output(tracked, repo_root=git_repo)
    guard_repo_safe_output(ignored, repo_root=git_repo)
    guard_repo_safe_output(tracked, repo_root=git_repo, allow_tracked=True)
    assert not tracked.exists()
    assert not ignored.exists()


def test_extractor_requires_an_explicit_output_directory() -> None:
    with pytest.raises(SystemExit) as exc_info:
        extract_quiz_pool_review.main(
            [str(FIXTURE_ROOT / "quiz_only_inline_tf")]
        )
    assert exc_info.value.code == 2


def test_extractor_refuses_tracked_output_before_creating_it(capsys, monkeypatch, git_repo) -> None:
    monkeypatch.setattr(extract_quiz_pool_review, "WORKBENCH_ROOT", git_repo)
    output = git_repo / "tracked-extractor-output-sentinel"
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
