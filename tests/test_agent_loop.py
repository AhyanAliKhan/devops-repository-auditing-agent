from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_loop import DevOpsAuditingAgent
from llm_client import LLMResponse, ToolCall
from tools import ToolRegistry


@dataclass
class ScriptedLLM:
    """Deterministic LLM stand-in that drives a real tool loop."""

    script: list[LLMResponse]
    calls: int = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        if self.calls >= len(self.script):
            raise AssertionError("ScriptedLLM ran out of responses")
        response = self.script[self.calls]
        self.calls += 1
        return response


def _assistant_with_tools(tool_calls: list[ToolCall]) -> LLMResponse:
    raw_calls = [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.name,
                "arguments": json.dumps(tc.arguments),
            },
        }
        for tc in tool_calls
    ]
    return LLMResponse(
        content=None,
        tool_calls=tool_calls,
        raw_assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": raw_calls,
        },
    )


def test_agent_loop_uses_tools_then_submits(sample_repo: Path) -> None:
    llm = ScriptedLLM(
        script=[
            _assistant_with_tools(
                [ToolCall(id="1", name="list_files", arguments={"path": "."})]
            ),
            _assistant_with_tools(
                [ToolCall(id="2", name="list_ci_workflows", arguments={})]
            ),
            _assistant_with_tools(
                [
                    ToolCall(
                        id="3",
                        name="submit_report",
                        arguments={
                            "violations": [
                                {
                                    "rule_id": "CI-001",
                                    "category": "ci_cd",
                                    "severity": "high",
                                    "file": ".github/workflows/ci.yml",
                                    "line": 3,
                                    "description": "Workflow grants write-all permissions",
                                    "evidence": "permissions: write-all in ci.yml",
                                    "recommendation": "Set least-privilege permissions",
                                }
                            ]
                        },
                    )
                ]
            ),
        ]
    )
    agent = DevOpsAuditingAgent(
        llm=llm,
        tools=ToolRegistry(sample_repo),
        max_steps=10,
    )
    result = agent.run("https://github.com/example/sample-repo")
    assert result.steps_used == 3
    assert len(result.report["violations"]) == 1
    assert result.report["violations"][0]["rule_id"] == "CI-001"
    tool_names = [
        step["tool"] for step in result.trace.steps if step.get("phase") == "execute"
    ]
    assert "list_files" in tool_names
    assert "list_ci_workflows" in tool_names


def test_agent_rejects_invalid_report_then_accepts(sample_repo: Path) -> None:
    llm = ScriptedLLM(
        script=[
            _assistant_with_tools(
                [
                    ToolCall(
                        id="1",
                        name="submit_report",
                        arguments={
                            "violations": [
                                {
                                    "rule_id": "X",
                                    "category": "security",
                                    "severity": "not-a-severity",
                                    "file": None,
                                    "line": None,
                                    "description": "bad",
                                    "evidence": "bad",
                                    "recommendation": "bad",
                                }
                            ]
                        },
                    )
                ]
            ),
            _assistant_with_tools(
                [
                    ToolCall(
                        id="2",
                        name="submit_report",
                        arguments={"violations": []},
                    )
                ]
            ),
        ]
    )
    agent = DevOpsAuditingAgent(llm=llm, tools=ToolRegistry(sample_repo), max_steps=5)
    result = agent.run("https://github.com/example/sample-repo")
    assert result.report["violations"] == []
