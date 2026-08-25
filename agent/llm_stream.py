"""agent.llm_stream：LLM 流式客户端层（2026-08-25 审计批6c 从 talk.py 拆出）。

talk.py 曾是 864 行 god class（六个职责）；本文件收走**LLM 流式**一职：
思考流容错客户端（ReasoningTolerantClient）+ 环境工厂 + SSE 事件字典化。

名字经 agent.talk re-export（app.py 的 lazy import / eval / 测试都从 talk 拿）——
import 路径是形状面，别改。
"""
from __future__ import annotations

import asyncio

from agentic.llm import OpenAIClient
from agentic.types import LLMResponse, StreamEvent


class ReasoningTolerantClient(OpenAIClient):
    """OpenAIClient 的思考流容错版（2026-08-24「token 流很慢」排查）：

    父类只认 delta.reasoning_content（百炼/Qwen 系）；端点若用 **reasoning**
    （OpenAI o 系/部分代理），思考期间一个分片都不发 —— 表现为"卡住很久，
    思考完才收到内容"。vendor 不改，子类把两种字段名都认。
    """

    #: 轮输出 token 预算（AgentTalk 每轮 reset_round_budget 复位；None = 不设限）
    output_budget: int | None = None
    round_output_tokens: int = 0

    async def _stream_call(self, kwargs: dict, on_delta=None) -> LLMResponse:
        """一次流式调用 + **零分片重试**（2026-08-24 用户报「断流无下文」）：
        连接类瞬断在**一个分片都没发**时重试一次 —— 已发过分片就不重试
        （重发会让前端正文重复），异常照抛给引擎记 run_end error（trace 可见）。

        另带**轮输出 token 预算**（§0.57 用户拍板）：AgentTalk 每轮 reset_round_budget；
        累计到上限 → 不再调 API，返回空响应（无工具调用 → runner 自然收轮，
        talk 侧检测计数落「预算用尽」说明 —— 不走异常路径，SSE 不炸）。"""
        budget = getattr(self, "output_budget", None)
        used = getattr(self, "round_output_tokens", 0)
        if budget is not None and budget > 0 and used >= budget:
            from agentic.types import LLMResponse as _R, Message as _M

            return _R(_M("assistant", None), 0, 0, "budget-stop")
        emitted = False

        def _guarding(delta) -> None:
            nonlocal emitted
            emitted = True
            if on_delta is not None:
                on_delta(delta)

        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = await self._stream_call_once(kwargs, _guarding if on_delta else None)
                self.round_output_tokens = used + int(resp.output_tokens or 0)
                return resp
            except Exception as exc:  # noqa: BLE001 —— 重试策略在这里定，透传给引擎的语义不变
                last_exc = exc
                if attempt == 1 and not emitted:
                    await asyncio.sleep(1.0)
                    continue
                raise
        raise last_exc  # 不可达（循环要么 return 要么 raise），保静态检查通过

    def reset_round_budget(self, budget: int | None) -> None:
        """每轮预算复位（AgentTalk 轮首调）：budget=None/0 = 不设限（测试替身语义）。"""
        self.output_budget = budget
        self.round_output_tokens = 0

    async def _stream_call_once(self, kwargs: dict, on_delta=None) -> LLMResponse:
        import json as _json

        from agentic.types import LLMDelta, LLMResponse, Message, ToolCall

        stream = await self._client.chat.completions.create(**kwargs)
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        usage = None
        async for chunk in stream:
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # 两种思考流字段都认：reasoning_content（百炼/Qwen/DeepSeek）与 reasoning（o 系）
            rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if rc:
                reasoning_parts.append(rc)
                if on_delta:
                    on_delta(LLMDelta("reasoning", rc))
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta(LLMDelta("content", delta.content))
            if delta.tool_calls:
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
                args = _json.loads(slot["arguments"] or "{}")
            except _json.JSONDecodeError:
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


def openai_from_env() -> object:
    return ReasoningTolerantClient.from_env()


def _event_dict(ev: StreamEvent) -> dict:
    """StreamEvent → 前端友好 dict（None 字段剔除，SSE 载荷只带有的东西）。"""
    out: dict = {"type": ev.type}
    if ev.turn_no is not None:
        out["turn_no"] = ev.turn_no
    if ev.type == "delta" and ev.delta is not None:
        out["kind"] = ev.delta.kind
        out["text"] = ev.delta.text
        if ev.delta.index is not None:
            out["index"] = ev.delta.index
    if ev.tool is not None:
        out["tool"] = ev.tool
    if ev.args is not None:
        out["args"] = ev.args
    if ev.result_preview is not None:
        out["result_preview"] = ev.result_preview
    if ev.outcome is not None:
        out["outcome"] = ev.outcome
    if ev.result is not None:
        out["result"] = ev.result
    if ev.summary is not None:
        out["summary"] = ev.summary
    return out
