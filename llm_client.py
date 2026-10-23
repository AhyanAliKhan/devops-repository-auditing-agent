"""Thin LLM client wrappers (OpenAI / Anthropic SDKs only — no agent frameworks)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    raw_assistant_message: dict[str, Any]


class LLMError(RuntimeError):
    pass


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


# Google Gemini OpenAI-compatible endpoint (basic SDK only — no agent frameworks)
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.1,
        api_key_env: str = "OPENAI_API_KEY",
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError(
                "openai package is required. Install with: pip install -r requirements.txt"
            ) from exc

        key = (
            api_key
            or os.getenv(api_key_env)
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )
        if not key:
            raise LLMError(
                f"{api_key_env} is not set (also checked OPENAI_API_KEY / GOOGLE_API_KEY / GEMINI_API_KEY)."
            )

        kwargs: dict[str, Any] = {"api_key": key}
        url = base_url or os.getenv("OPENAI_BASE_URL")
        if url:
            kwargs["base_url"] = url
        self._client = OpenAI(**kwargs)
        self.model = model
        self.temperature = temperature

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - surface provider errors cleanly
            raise LLMError(f"OpenAI API error: {exc}") from exc

        message = response.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_args(tc.function.arguments),
                )
            )

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.content,
        }
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in message.tool_calls
            ]

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            raw_assistant_message=assistant_msg,
        )


class AnthropicClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.1,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "anthropic package is required. Install with: pip install -r requirements.txt"
            ) from exc

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")

        self._client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.temperature = temperature

    @staticmethod
    def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for tool in tools:
            fn = tool.get("function", tool)
            converted.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return converted

    @staticmethod
    def _to_anthropic_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system = None
        out: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system = msg.get("content") or ""
                continue
            if role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls") or []:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": _parse_args(tc["function"].get("arguments")),
                        }
                    )
                out.append({"role": "assistant", "content": content or ""})
                continue
            if role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg["tool_call_id"],
                                "content": msg.get("content") or "",
                            }
                        ],
                    }
                )
                continue
            # user
            out.append({"role": "user", "content": msg.get("content") or ""})
        return system, out

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        system, anth_messages = self._to_anthropic_messages(messages)
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=self.temperature,
                system=system or "",
                tools=self._to_anthropic_tools(tools),
                messages=anth_messages,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Anthropic API error: {exc}") from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        assistant_tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
                assistant_tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(args),
                        },
                    }
                )

        content = "\n".join(text_parts) if text_parts else None
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if assistant_tool_calls:
            assistant_msg["tool_calls"] = assistant_tool_calls

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            raw_assistant_message=assistant_msg,
        )


def create_llm_client(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> OpenAICompatibleClient | AnthropicClient:
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).strip().lower()
    model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
    temp = (
        temperature
        if temperature is not None
        else float(os.getenv("AGENT_TEMPERATURE", "0.1"))
    )

    if provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleClient(model=model, temperature=temp)
    if provider in {"google", "gemini"}:
        # Gemini via official OpenAI-compatible API (supports tool/function calling)
        return OpenAICompatibleClient(
            model=model or "gemini-3.8-flash",
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or GEMINI_OPENAI_BASE_URL,
            temperature=temp,
            api_key_env="GOOGLE_API_KEY",
        )
    if provider == "anthropic":
        return AnthropicClient(model=model, temperature=temp)
    raise LLMError(
        f"Unsupported LLM_PROVIDER={provider!r}. "
        "Use openai, openai_compatible, google, gemini, or anthropic."
    )
