"""Clone and manage temporary working copies of public GitHub repositories."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


GITHUB_REPO_RE = re.compile(
    r"^https?://(www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class ClonedRepo:
    url: str
    path: Path
    owner: str
    name: str


def normalize_github_url(repo_url: str) -> tuple[str, str, str]:
    """Validate and normalize a public GitHub repository URL.

    Returns (canonical_https_url, owner, repo_name).
    """
    raw = (repo_url or "").strip()
    if not raw:
        raise ValueError("Repository URL is required.")

    # Accept owner/repo shorthand
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", raw):
        owner, repo = raw.split("/", 1)
        repo = repo.removesuffix(".git")
        return f"https://github.com/{owner}/{repo}", owner, repo

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"Unsupported repository URL scheme '{parsed.scheme}'. "
            "Provide an https://github.com/<owner>/<repo> URL."
        )
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError(
            "Only public github.com repositories are supported. "
            f"Got host: {parsed.netloc}"
        )

    match = GITHUB_REPO_RE.match(raw.rstrip("/"))
    if not match:
        # Try parsing path segments for URLs with extra trailing paths
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1].removesuffix(".git")
            if re.fullmatch(r"[A-Za-z0-9_.-]+", owner) and re.fullmatch(
                r"[A-Za-z0-9_.-]+", repo
            ):
                return f"https://github.com/{owner}/{repo}", owner, repo
        raise ValueError(
            "Invalid GitHub repository URL. Expected "
            "https://github.com/<owner>/<repo>"
        )

    owner = match.group("owner")
    repo = match.group("repo")
    return f"https://github.com/{owner}/{repo}", owner, repo


def clone_repository(
    repo_url: str,
    *,
    work_root: Path | None = None,
    timeout_seconds: int = 120,
) -> ClonedRepo:
    """Shallow-clone a public repository into a temporary directory."""
    canonical, owner, name = normalize_github_url(repo_url)

    if shutil.which("git") is None:
        raise RuntimeError(
            "git is not installed or not on PATH. Install Git to clone repositories."
        )

    root = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="devops_audit_"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{owner}__{name}"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    cmd = [
        "git",
        "clone",
        "--depth",
        "50",
        "--no-tags",
        canonical + ".git",
        str(target),
    ]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out cloning repository after {timeout_seconds}s: {canonical}"
        ) from exc

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"Failed to clone {canonical} (exit {completed.returncode}). {err}"
        )

    return ClonedRepo(url=canonical, path=target, owner=owner, name=name)


def cleanup_clone(cloned: ClonedRepo | Path) -> None:
    path = cloned.path if isinstance(cloned, ClonedRepo) else Path(cloned)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        parent = path.parent
        if parent.name.startswith("devops_audit_") and parent.exists():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
