"""Explicit Observe → Reason → Select Tool → Execute → Interpret → Report agent loop.

No LangChain / LangGraph / n8n. Tool dispatch and control flow are implemented here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from prompts import SYSTEM_PROMPT, build_user_prompt
from report import ReportValidationError, build_report
from tools import TOOL_DEFINITIONS, ToolError, ToolRegistry

logger = logging.getLogger(__name__)


class SupportsChat(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any: ...


@dataclass
class AgentTrace:
    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(self, **kwargs: Any) -> None:
        self.steps.append(kwargs)


@dataclass
class AgentResult:
    report: dict[str, Any]
    trace: AgentTrace
    steps_used: int


class AgentLoopError(RuntimeError):
    pass


class DevOpsAuditingAgent:
    """LLM-directed repository auditor with explicit tool dispatch."""

    def __init__(
        self,
        llm: SupportsChat,
        tools: ToolRegistry,
        *,
        max_steps: int = 30,
        verbose: bool = False,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max(1, max_steps)
        self.verbose = verbose
        self.trace = AgentTrace()

    def run(self, repo_url: str) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(repo_url, str(self.tools.repo_root)),
            },
        ]

        final_violations: list[dict[str, Any]] | None = None

        for step in range(1, self.max_steps + 1):
            if self.verbose:
                print(f"[agent] step {step}/{self.max_steps}: reasoning...")

            response = self.llm.chat(messages, TOOL_DEFINITIONS)
            messages.append(response.raw_assistant_message)

            self.trace.add(
                step=step,
                phase="reason",
                content=(response.content or "")[:500],
                tool_calls=[
                    {"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls
                ],
            )

            if not response.tool_calls:
                # Model tried to finish in plain text — nudge it to use submit_report
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You must finish by calling the submit_report tool with your "
                            "violations array (empty if none). Do not reply with plain text only."
                        ),
                    }
                )
                continue

            # Execute tool calls; submit_report ends the loop
            for tc in response.tool_calls:
                if tc.name == "submit_report":
                    violations = tc.arguments.get("violations", [])
                    try:
                        report = build_report(repo_url, violations)
                    except ReportValidationError as exc:
                        tool_payload = {
                            "ok": False,
                            "error": f"Invalid report: {exc}. Fix and call submit_report again.",
                        }
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(tool_payload),
                            }
                        )
                        self.trace.add(step=step, phase="submit_report_rejected", error=str(exc))
                        break

                    # Acknowledge other parallel tool calls if any (shouldn't happen)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps({"ok": True, "accepted": True}),
                        }
                    )
                    final_violations = report["violations"]
                    self.trace.add(
                        step=step,
                        phase="submit_report",
                        violation_count=len(final_violations),
                    )
                    if self.verbose:
                        print(
                            f"[agent] report accepted with {len(final_violations)} violation(s)"
                        )
                    break

                result_payload = self._execute_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result_payload, ensure_ascii=False)[:50_000],
                    }
                )
                self.trace.add(
                    step=step,
                    phase="execute",
                    tool=tc.name,
                    arguments=tc.arguments,
                    ok=result_payload.get("ok", False),
                )
                if self.verbose:
                    status = "ok" if result_payload.get("ok") else "error"
                    print(f"[agent]   {tc.name} → {status}")
            else:
                # for-else: no submit_report in this batch
                continue

            if final_violations is not None:
                break
        else:
            raise AgentLoopError(
                f"Agent exceeded max steps ({self.max_steps}) without submit_report. "
                "Increase AGENT_MAX_STEPS or check LLM connectivity."
            )

        report = {"repository": repo_url, "violations": final_violations or []}
        return AgentResult(report=report, trace=self.trace, steps_used=step)

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.tools.dispatch(name, arguments)
            return {"ok": True, "tool": name, "result": result}
        except ToolError as exc:
            return {"ok": False, "tool": name, "error": str(exc)}
        except TypeError as exc:
            return {
                "ok": False,
                "tool": name,
                "error": f"Invalid arguments for {name}: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected tool failure: %s", name)
            return {"ok": False, "tool": name, "error": f"Unexpected error: {exc}"}
