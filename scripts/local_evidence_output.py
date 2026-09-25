"""Shared repository boundary for local course-evidence outputs.

Extractor and Unbind outputs may contain authored prompts, answer keys,
feedback, and large machine artifacts.  Inside the Workbench they therefore
belong in a git-ignored lane unless an operator explicitly overrides the guard.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


class RepoUnsafeOutputError(ValueError):
    """An output path would place local course evidence in a tracked lane."""


def git_ignores(path: Path, *, repo_root: Path) -> bool | None:
    """Return True/False when git can answer, or None when it cannot."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path)],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def guard_repo_safe_output(
    output: Path,
    *,
    repo_root: Path,
    allow_tracked: bool = False,
    ignored_lane_example: str = "workspace/review/quiz_binder_runs/<label>",
) -> None:
    """Refuse an unignored output inside ``repo_root`` before anything is made."""
    if allow_tracked:
        return
    output = output.resolve()
    repo_root = repo_root.resolve()
    try:
        output.relative_to(repo_root)
    except ValueError:
        return
    if git_ignores(output, repo_root=repo_root) is False:
        raise RepoUnsafeOutputError(
            "output directory is inside the workbench but is not git-ignored; "
            f"use an ignored local-evidence lane such as {ignored_lane_example} "
            "or pass --allow-tracked-output"
        )
