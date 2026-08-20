"""OpenAI 兼容 LLM 客户端。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx
from openai import OpenAI

from ..utils.retry_helper import with_retry


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMTurn:
    content: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    raw_assistant_message: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: Optional[str] = None,
        timeout: float = 300.0,
        trust_env: bool = True,
    ):
        if not api_key or not model_name:
            raise ValueError("LLM API Key 和模型名不能为空")
        kwargs = {
            "api_key": api_key,
            "max_retries": 0,
            "http_client": httpx.Client(trust_env=trust_env, timeout=timeout),
        }
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model_name = model_name
        self.base_url = base_url
        self.timeout = timeout

    @with_retry(max_retries=3, initial_delay=2.0)
    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: Optional[float] = None,
    ) -> str:
        cutoff = datetime.now().astimezone().isoformat(timespec="minutes")
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"当前数据检索时间：{cutoff}\n{user_prompt}",
                },
            ],
            "timeout": self.timeout,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        response = self.client.chat.completions.create(**payload)
        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()

    @with_retry(max_retries=3, initial_delay=2.0)
    def invoke_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = "auto",
        temperature: Optional[float] = None,
    ) -> LLMTurn:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "timeout": self.timeout,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        response = self.client.chat.completions.create(**payload)
        if not response.choices:
            return LLMTurn()
        message = response.choices[0].message
        tool_calls = []
        raw_calls = []
        for item in list(getattr(message, "tool_calls", None) or []):
            function = getattr(item, "function", None)
            name = getattr(function, "name", "") if function is not None else ""
            arguments = getattr(function, "arguments", "") if function is not None else ""
            call_id = str(getattr(item, "id", "") or "") or f"call_{len(tool_calls)}"
            tool_calls.append(LLMToolCall(id=call_id, name=str(name or ""), arguments=str(arguments or "{}")))
            raw_calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": str(name or ""), "arguments": str(arguments or "{}")},
            })
        raw_message: dict[str, Any] = {"role": "assistant", "content": message.content}
        if raw_calls:
            raw_message["tool_calls"] = raw_calls
        return LLMTurn(
            content=(message.content or "").strip(),
            tool_calls=tool_calls,
            raw_assistant_message=raw_message,
        )
