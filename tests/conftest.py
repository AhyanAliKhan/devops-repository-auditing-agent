from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Copy the fixture tree into a temporary git repository."""
    import shutil

    dest = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE_REPO, dest)
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=dest, check=True)
    subprocess.run(["git", "add", "."], cwd=dest, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial fixture commit"],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    return dest
