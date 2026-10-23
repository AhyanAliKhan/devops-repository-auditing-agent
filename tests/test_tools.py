from __future__ import annotations

from pathlib import Path

import pytest

from tools import ToolError, ToolRegistry


def test_list_files_discovers_structure(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    result = tools.list_files(".")
    assert result["count"] > 0
    joined = "\n".join(result["entries"])
    assert "app.py" in joined
    assert "README.md" in joined


def test_read_file_numbers_lines(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    result = tools.read_file("app.py")
    assert "1|" in result["content"]
    assert "API_TOKEN" in result["content"]


def test_read_file_rejects_path_escape(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    with pytest.raises(ToolError, match="escapes"):
        tools.read_file("../outside.txt")


def test_search_code_finds_pattern(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    result = tools.search_code(r"API_TOKEN\s*=")
    assert result["match_count"] >= 1
    assert result["matches"][0]["file"] == "app.py"


def test_git_log_returns_commits(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    result = tools.git_log(max_count=5)
    assert result["count"] >= 1
    assert "subject" in result["commits"][0]


def test_git_status_and_workflows(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    status = tools.git_status()
    assert status["tracked_file_count"] >= 1
    workflows = tools.list_ci_workflows()
    assert workflows["present"] is True
    assert any(w.endswith("ci.yml") for w in workflows["workflows"])


def test_dispatch_unknown_tool(sample_repo: Path) -> None:
    tools = ToolRegistry(sample_repo)
    with pytest.raises(ToolError, match="Unknown tool"):
        tools.dispatch("not_a_tool", {})
