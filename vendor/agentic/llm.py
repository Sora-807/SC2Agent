"""LLM client:OpenAI 直连(async)+ Fake(测试)。

OpenAIClient 用 .env(OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL)。
暴露 ``complete(messages, tools)`` —— runner 用它跑 tool loop;可选 ``on_delta``
回调把流式分片(LLMDelta)外发给上层(start_stream 的流式通道,见 ADR-0007)。
"""
from __future__ import annotations

import json
import os
from typing import Callable, Protocol

from .types import LLMDelta, LLMResponse, Message, Tool, ToolCall


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        *,
        model: str | None = None,
        on_delta: Callable[[LLMDelta], None] | None = None,
    ) -> LLMResponse: ...


def to_openai_messages(messages: list[Message]) -> list[dict]:
    """Message -> OpenAI dict。"""
    out: list[dict] = []
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            out.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": tool_call.id, "type": "function",
                     "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.args, ensure_ascii=False)}}
                    for tool_call in message.tool_calls
                ],
            })
        elif message.role == "tool":
            out.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content})
        else:  # system / user
            out.append({"role": message.role, "content": message.content})
    return out


def to_openai_tools(tools: list[Tool]) -> list[dict]:
    """Tool -> OpenAI function schema。"""
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


def parse_assistant(message: dict) -> Message:
    """OpenAI 返回的 assistant message -> Message(解析 tool_calls arguments)。"""
    tool_calls = None
    raw_tool_calls = message.get("tool_calls")
    if raw_tool_calls:
        tool_calls = []
        for raw_tool_call in raw_tool_calls:
            function = raw_tool_call.get("function", {})
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": function.get("arguments")}
            tool_calls.append(ToolCall(id=raw_tool_call.get("id", ""), name=function.get("name", ""), args=args))
    return Message(role="assistant", content=message.get("content"), tool_calls=tool_calls)


class OpenAIClient:
    """async OpenAI chat.completions + 原生 tools。流式接收,累积后整条返回(对 caller 透明)。

    思考模型(glm-5.2 默认开思考)若用非流式,思考阶段不吐任何 token,HTTP body 直到
    "思考+回答"完成才有数据 → 客户端看成"几分钟无响应"(= 我们之前遇到的 hang)。
    流式后 reasoning_content 持续流出,read-timeout 每收到一个 chunk 就重置,不再假超时。
    """

    def __init__(
        self, *, api_key: str, base_url: str | None, model: str,
        enable_thinking: bool | None = None, reasoning_effort: str | None = "high",
    ) -> None:
        import httpx
        from openai import AsyncOpenAI
        self._model = model
        # enable_thinking=None → 不传,用服务端默认(GLM 默认开);True/False 显式开关。
        self._enable_thinking = enable_thinking
        # reasoning_effort:GLM 思考深度控制("high"|"max")。注意 thinking_budget(token 级)是
        # Qwen3 专属,GLM-5.2 不支持——GLM 只能用 reasoning_effort + max_tokens 卡思考。
        self._reasoning_effort = reasoning_effort
        # timeout:120s read(流式下=两 chunk 间最长间隔,思考模型正常会持续吐 reasoning);
        # 10s connect(不可达快速失败)。max_retries=0:关 SDK 内部重试,由下方 retries 单一重试。
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
            max_retries=0,
        )

    @classmethod
    def from_env(cls) -> "OpenAIClient":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ.get("OPENAI_BASE_URL") or None
        model = os.environ["LLM_MODEL"]
        # LLM_ENABLE_THINKING: "true"/"false" 显式开关;不设=None(服务端默认,GLM 默认开)
        et = os.environ.get("LLM_ENABLE_THINKING", "").strip().lower()
        enable_thinking = True if et == "true" else (False if et == "false" else None)
        # LLM_REASONING_EFFORT: "high"(默认,有界)| "max"(更深更慢);空串=不传
        effort = os.environ.get("LLM_REASONING_EFFORT", "high").strip() or None
        return cls(api_key=api_key, base_url=base_url, model=model,
                   enable_thinking=enable_thinking, reasoning_effort=effort)

    async def complete(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        *,
        model: str | None = None,
        on_delta: Callable[[LLMDelta], None] | None = None,
        retries: int = 4,
    ) -> LLMResponse:
        import asyncio

        kwargs: dict = {
            "model": model or self._model,
            "messages": to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},  # 末 chunk 带 usage
        }
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        # 思考配置(GLM-5.2 via 百炼 → extra_body,非 top-level)
        extra: dict = {}
        if self._enable_thinking is not None:
            extra["enable_thinking"] = self._enable_thinking
        if self._reasoning_effort:
            extra["reasoning_effort"] = self._reasoning_effort
        if extra:
            kwargs["extra_body"] = extra
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                return await self._stream_call(kwargs, on_delta)
            except Exception as e:  # noqa: BLE001 — rate limit / API / 网络,简单重试
                last_exc = e
                await asyncio.sleep(2 ** attempt)  # 1, 2, 4, 8s
        assert last_exc is not None
        raise last_exc

    async def _stream_call(self, kwargs: dict, on_delta=None) -> LLMResponse:
        """流式拉取 + 累积,整条 LLMResponse 返回;on_delta 逐分片外发(可选)。"""
        stream = await self._client.chat.completions.create(**kwargs)
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments}
        usage = None
        async for chunk in stream:
            if chunk.usage:  # 末 chunk(choices 可能空)带 usage
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)  # 思考链(独立字段)
            if rc:
                reasoning_parts.append(rc)
                if on_delta:
                    on_delta(LLMDelta("reasoning", rc))
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta(LLMDelta("content", delta.content))
            if delta.tool_calls:  # index-based,arguments 分片到达,需拼接
                for tool_call_delta in delta.tool_calls:
                    index = tool_call_delta.index if tool_call_delta.index is not None else 0
                    slot = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if tool_call_delta.id:
                        slot["id"] = tool_call_delta.id
                    if tool_call_delta.function and tool_call_delta.function.name:
                        slot["name"] = tool_call_delta.function.name
                    if tool_call_delta.function and tool_call_delta.function.arguments:
                        slot["arguments"] += tool_call_delta.function.arguments
                        if on_delta:
                            on_delta(LLMDelta("tool_call", tool_call_delta.function.arguments, index=index))
        content = "".join(content_parts)
        parsed_tcs: list[ToolCall] = []
        for idx in sorted(tool_calls):
            slot = tool_calls[idx]
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            parsed_tcs.append(ToolCall(id=slot["id"], name=slot["name"], args=args))
        return LLMResponse(
            message=Message("assistant", content or None, tool_calls=parsed_tcs or None),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=kwargs["model"],
            reasoning="".join(reasoning_parts) or None,
            cached_tokens=(
                getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
                if usage else None
            ),
        )


class FakeLLMClient:
    """测试用:脚本化响应。responses 可为 list(队列)或 callable(messages, tools)-> LLMResponse。

    on_delta 模拟:整条 reasoning / content 各作为单个分片发出(足够测流式管道)。
    """

    def __init__(self, responses) -> None:
        if isinstance(responses, list):
            self._queue = list(responses)
            self._fn = None
        else:
            self._queue = None
            self._fn = responses
        self.calls: list[tuple[list[Message], list[Tool] | None]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        *,
        model: str | None = None,
        on_delta=None,
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        if self._fn is not None:
            response = self._fn(messages, tools)
        elif self._queue:
            response = self._queue.pop(0)
        else:
            raise RuntimeError("FakeLLMClient: response queue exhausted")
        if on_delta is not None:
            if response.reasoning:
                on_delta(LLMDelta("reasoning", response.reasoning))
            if response.message.content:
                on_delta(LLMDelta("content", response.message.content))
        return response
