"""Inspection tools available to the DevOps auditing agent.

All tools operate on a local clone and never modify the inspected repository.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


# Directories commonly large / low-signal for structure discovery
_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    ".eggs",
    "coverage",
    "htmlcov",
    ".idea",
    ".vscode",
    "vendor",
    "target",
}


class ToolError(Exception):
    """Raised when a tool cannot complete safely."""


class ToolRegistry:
    """Bound tool implementations for a single repository clone."""

    def __init__(self, repo_root: Path, *, tool_timeout_seconds: int = 30) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise ToolError(f"Repository root does not exist: {self.repo_root}")
        self.tool_timeout_seconds = tool_timeout_seconds

    def _resolve(self, relative_path: str | None = None) -> Path:
        rel = (relative_path or ".").replace("\\", "/").lstrip("/")
        if rel in {"", "."}:
            candidate = self.repo_root
        else:
            candidate = (self.repo_root / rel).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError as exc:
            raise ToolError(
                f"Path escapes repository root: {relative_path!r}"
            ) from exc
        return candidate

    def list_files(self, path: str = ".", max_entries: int = 400) -> dict[str, Any]:
        """Discover repository files and directory structure."""
        root = self._resolve(path)
        if not root.exists():
            raise ToolError(f"Path not found: {path}")
        if not root.is_dir():
            raise ToolError(f"Not a directory: {path}")

        entries: list[str] = []
        truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".git")
            )
            rel_dir = Path(dirpath).relative_to(self.repo_root).as_posix()
            prefix = "." if rel_dir == "." else rel_dir
            for name in sorted(dirnames):
                entries.append(f"{prefix}/{name}/" if prefix != "." else f"{name}/")
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break
            for name in sorted(filenames):
                entries.append(f"{prefix}/{name}" if prefix != "." else name)
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "path": path,
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }

    def read_file(
        self,
        path: str,
        *,
        max_bytes: int = 100_000,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Inspect a source or configuration file (text only)."""
        target = self._resolve(path)
        if not target.exists():
            raise ToolError(f"File not found: {path}")
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        if target.stat().st_size > max_bytes * 4:
            raise ToolError(
                f"File too large to read safely ({target.stat().st_size} bytes): {path}"
            )

        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"Unable to read {path}: {exc}") from exc

        if b"\x00" in raw[:8192]:
            raise ToolError(f"Refusing to read binary file: {path}")

        text = raw[:max_bytes].decode("utf-8", errors="replace")
        truncated = len(raw) > max_bytes
        lines = text.splitlines()

        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1)
            e = end_line or len(lines)
            e = min(len(lines), max(s, e))
            slice_lines = lines[s - 1 : e]
            numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(slice_lines, start=s))
            return {
                "path": path,
                "start_line": s,
                "end_line": e,
                "total_lines": len(lines),
                "truncated": truncated,
                "content": numbered,
            }

        numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(lines, start=1))
        return {
            "path": path,
            "start_line": 1,
            "end_line": len(lines),
            "total_lines": len(lines),
            "truncated": truncated,
            "content": numbered,
        }

    def search_code(
        self,
        pattern: str,
        *,
        path: str = ".",
        max_matches: int = 50,
        case_insensitive: bool = False,
    ) -> dict[str, Any]:
        """Search repository text files for a regex pattern."""
        if not pattern or not pattern.strip():
            raise ToolError("search_code requires a non-empty pattern")

        flags = re.MULTILINE
        if case_insensitive:
            flags |= re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"Invalid regex pattern: {exc}") from exc

        root = self._resolve(path)
        if not root.exists():
            raise ToolError(f"Path not found: {path}")

        matches: list[dict[str, Any]] = []
        truncated = False
        files_scanned = 0

        paths: list[Path]
        if root.is_file():
            paths = [root]
        else:
            paths = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
                for name in filenames:
                    paths.append(Path(dirpath) / name)

        for file_path in paths:
            if truncated:
                break
            if not file_path.is_file():
                continue
            if file_path.stat().st_size > 1_000_000:
                continue
            try:
                data = file_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue

            files_scanned += 1
            rel = file_path.relative_to(self.repo_root).as_posix()
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "file": rel,
                            "line": line_no,
                            "text": _redact_secrets(line.strip())[:300],
                        }
                    )
                    if len(matches) >= max_matches:
                        truncated = True
                        break

        return {
            "pattern": pattern,
            "path": path,
            "files_scanned": files_scanned,
            "match_count": len(matches),
            "truncated": truncated,
            "matches": matches,
        }

    def git_log(self, max_count: int = 20, path: str | None = None) -> dict[str, Any]:
        """Inspect recent commit history."""
        max_count = max(1, min(int(max_count), 100))
        cmd = [
            "git",
            "-C",
            str(self.repo_root),
            "log",
            f"-n{max_count}",
            "--pretty=format:%H|%an|%ae|%ad|%s",
            "--date=short",
        ]
        if path:
            resolved = self._resolve(path)
            rel = resolved.relative_to(self.repo_root).as_posix()
            cmd.extend(["--", rel])

        output = self._run_git(cmd)
        commits: list[dict[str, str]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            sha, author, email, date, subject = parts
            commits.append(
                {
                    "sha": sha,
                    "author": author,
                    "email": email,
                    "date": date,
                    "subject": subject,
                }
            )
        return {"count": len(commits), "commits": commits}

    def git_status(self) -> dict[str, Any]:
        """Show working-tree status (should be clean for a fresh clone)."""
        porcelain = self._run_git(
            ["git", "-C", str(self.repo_root), "status", "--porcelain"]
        )
        branch = self._run_git(
            ["git", "-C", str(self.repo_root), "rev-parse", "--abbrev-ref", "HEAD"]
        ).strip()
        tracked = self._run_git(
            ["git", "-C", str(self.repo_root), "ls-files"]
        ).splitlines()
        suspicious = [
            p
            for p in tracked
            if p.endswith((".env", ".pem", ".key", ".p12"))
            or "/.env" in f"/{p}"
            or p.lower().endswith("id_rsa")
            or "credentials" in p.lower()
        ]
        return {
            "branch": branch,
            "porcelain": porcelain.strip(),
            "tracked_file_count": len(tracked),
            "suspicious_tracked_paths": suspicious[:50],
        }

    def list_ci_workflows(self) -> dict[str, Any]:
        """List GitHub Actions workflow files if present."""
        workflows_dir = self.repo_root / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return {"present": False, "workflows": []}
        files = sorted(
            p.relative_to(self.repo_root).as_posix()
            for p in workflows_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}
        )
        return {"present": True, "workflows": files}

    def _run_git(self, cmd: list[str]) -> str:
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.tool_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"git command timed out: {' '.join(cmd)}") from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise ToolError(f"git command failed: {err}")
        return completed.stdout

    def dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        handlers: dict[str, Callable[..., Any]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_code": self.search_code,
            "git_log": self.git_log,
            "git_status": self.git_status,
            "list_ci_workflows": self.list_ci_workflows,
        }
        if name not in handlers:
            raise ToolError(f"Unknown tool: {name}")
        return handlers[name](**arguments)


def _redact_secrets(text: str) -> str:
    """Best-effort redaction so tool output never echoes obvious secrets."""
    patterns = [
        (re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[=:]\s*\S+"), r"\1=***REDACTED***"),
        (re.compile(r"sk-[A-Za-z0-9]{10,}"), "sk-***REDACTED***"),
        (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***REDACTED***"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    ]
    redacted = text
    for regex, repl in patterns:
        redacted = regex.sub(repl, redacted)
    return redacted


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Discover repository files and directory structure under a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (default '.')",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return (default 400)",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file with optional line range. Lines are numbered as N|content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "start_line": {"type": "integer", "description": "1-based start line"},
                    "end_line": {"type": "integer", "description": "1-based end line"},
                    "max_bytes": {"type": "integer", "description": "Max bytes to read"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search text files with a Python regex for suspicious or relevant patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Relative path to limit search"},
                    "max_matches": {"type": "integer"},
                    "case_insensitive": {"type": "boolean"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Inspect recent commit history (optionally for a path).",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "Number of commits (1-100)"},
                    "path": {"type": "string", "description": "Optional path filter"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show branch, porcelain status, and suspicious tracked paths.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_ci_workflows",
            "description": "List GitHub Actions workflow files under .github/workflows.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": (
                "Submit the final audit report when investigation is complete. "
                "Call exactly once with all violations (or an empty list)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "violations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "rule_id": {"type": "string"},
                                "category": {"type": "string"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["critical", "high", "medium", "low"],
                                },
                                "file": {
                                    "anyOf": [{"type": "string"}, {"type": "null"}],
                                },
                                "line": {
                                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                                },
                                "description": {"type": "string"},
                                "evidence": {"type": "string"},
                                "recommendation": {"type": "string"},
                            },
                            "required": [
                                "rule_id",
                                "category",
                                "severity",
                                "file",
                                "line",
                                "description",
                                "evidence",
                                "recommendation",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["violations"],
                "additionalProperties": False,
            },
        },
    },
]
