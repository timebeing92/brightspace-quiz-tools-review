#!/usr/bin/env python3
"""Create the Quiz Bundle virtual environment and install dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import venv


REPO_ROOT = Path(__file__).resolve().parents[1]
MINIMUM = (3, 11)
MAXIMUM_EXCLUSIVE = (3, 14)


class BootstrapError(RuntimeError):
    pass


def supported_python() -> bool:
    return MINIMUM <= sys.version_info[:2] < MAXIMUM_EXCLUSIVE


def venv_python(environment: Path) -> Path:
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def validate_environment_target(environment: Path) -> None:
    prohibited = {Path("/").resolve(), Path.home().resolve(), REPO_ROOT.resolve()}
    if environment in prohibited:
        raise BootstrapError(f"unsafe virtual-environment target: {environment}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=REPO_ROOT / ".venv")
    parser.add_argument("--locked", action="store_true", help="install the pinned runtime lock")
    parser.add_argument("--dev", action="store_true", help="install test dependencies after runtime")
    parser.add_argument("--check", action="store_true", help="validate bootstrap inputs without changing the environment")
    args = parser.parse_args()

    try:
        if not supported_python():
            raise BootstrapError(
                "Quiz Bundle requires Python 3.11, 3.12, or 3.13; "
                f"found {sys.version_info.major}.{sys.version_info.minor}."
            )
        runtime_requirements = REPO_ROOT / (
            "requirements-lock.txt" if args.locked else "requirements.txt"
        )
        required = [runtime_requirements]
        if args.dev:
            required.append(REPO_ROOT / "requirements-dev.txt")
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise BootstrapError("missing requirement files: " + ", ".join(missing))
        environment = args.venv.expanduser().resolve()
        validate_environment_target(environment)
        if args.check:
            print(
                f"bootstrap inputs OK: Python {sys.version_info.major}.{sys.version_info.minor}; "
                f"runtime={runtime_requirements.name}; dev={args.dev}"
            )
            return 0

        venv.EnvBuilder(with_pip=True).create(environment)
        python = venv_python(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(runtime_requirements)],
            cwd=REPO_ROOT,
            check=True,
        )
        if args.dev:
            subprocess.run(
                [str(python), "-m", "pip", "install", "-r", str(REPO_ROOT / "requirements-dev.txt")],
                cwd=REPO_ROOT,
                check=True,
            )
    except (OSError, subprocess.CalledProcessError, BootstrapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"environment ready: {environment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
