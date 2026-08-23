"""Agent · runner:单 agent 的 LLM run loop。

loop:complete → 有 tool_calls 则执行 → 回喂 tool message → 继续;
无 tool_calls 或 done 工具触发状态后走 before_finalize 校验 → 过即 done。

可选 ``emit``(同步回调,收 StreamEvent):把 delta/turn/tool 进度发到内存流式通道
(start_stream 消费);不发 run_end —— run 终态由 engine 归口(TaskStatus)。
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import time
from typing import Callable, Protocol, runtime_checkable

from ..llm import LLMClient
from ..trace import Tracer
from ..trace.events import LLM_CALL, LLM_TIMEOUT, RUN_END, TOOL_CALL, TURN_END, TURN_START
from ..types import (
    STREAM_DELTA,
    STREAM_LLM_TIMEOUT,
    STREAM_TOOL_CALL,
    STREAM_TURN_END,
    STREAM_TURN_START,
    AgentInstance,
    Message,
    RunOutcome,
    StreamEvent,
)

LLM_TURN_TIMEOUT = 300


def message_to_dict(message: Message) -> dict:
    return dataclasses.asdict(message)


def message_preview(message: Message) -> str:
    if message.content:
        return message.content[:150]
    if message.tool_calls:
        return "[tool_calls: " + ", ".join(call.name for call in message.tool_calls) + "]"
    return ""


async def await_if_awaitable(value):
    """同步/异步钩子都支持。"""
    if inspect.iscoroutine(value):
        return await value
    return value


@runtime_checkable
class Spec(Protocol):
    """runner 需要的 spec 接口。"""
    def system_prompt(self) -> str: ...
    def hooks(self) -> dict: ...


async def run(
    agent: AgentInstance,
    llm: LLMClient,
    tracer: Tracer,
    *,
    max_turns: int = 20,
    semaphore=None,
    llm_timeout: int = LLM_TURN_TIMEOUT,
    emit: Callable[[StreamEvent], None] | None = None,
) -> RunOutcome:
    """跑 agent LLM loop 到 done 或 max_turns(paused)。emit 可选:流式进度事件 sink。"""
    spec = agent.spec
    state = agent.state
    state.status = "running"
    target = agent.target

    if state.messages:
        messages = list(state.messages)
    else:
        from ..prompt import build_system_prompt
        messages = []
        system_text = build_system_prompt(agent)
        tracer.log_message(target, message_to_dict(Message("system", system_text)))
        messages.append(Message("system", system_text))

    for message in state.inbox:
        tracer.log_message(target, message_to_dict(message))
        messages.append(message)
    state.inbox = []

    def append_message(message: Message) -> None:
        tracer.log_message(target, message_to_dict(message))
        messages.append(message)

    tools = agent.tools
    tool_map = {tool.name: tool for tool in tools}
    hooks = spec.hooks()
    after_turn = hooks.get("after_turn")
    before_finalize = hooks.get("before_finalize")

    def emit_event(type: str, **fields) -> None:
        if emit is not None:
            emit(StreamEvent(agent_id=target, type=type, **fields))

    while state.turn_no < max_turns:
        state.turn_no += 1
        tracer.event(target, TURN_START, turn_no=state.turn_no)
        emit_event(STREAM_TURN_START, turn_no=state.turn_no)

        input_count = len(messages)
        started_at = tracer.now()
        start_time = time.monotonic()

        def on_delta(delta) -> None:
            emit_event(STREAM_DELTA, turn_no=state.turn_no, delta=delta)

        # on_delta 只在开了流式时才传:未升级的自定义 LLMClient 仍按旧签名工作
        llm_kwargs: dict = {"on_delta": on_delta} if emit is not None else {}
        try:
            if semaphore is not None:
                async with semaphore:
                    response = await asyncio.wait_for(llm.complete(messages, tools, **llm_kwargs), timeout=llm_timeout)
            else:
                response = await asyncio.wait_for(llm.complete(messages, tools, **llm_kwargs), timeout=llm_timeout)
        except asyncio.TimeoutError:
            tracer.event(target, LLM_TIMEOUT, turn_no=state.turn_no)
            emit_event(STREAM_LLM_TIMEOUT, turn_no=state.turn_no)
            append_message(Message("user", f"[system: LLM call timed out ({llm_timeout}s). Retry or simplify.]"))
            continue
        duration_ms = int((time.monotonic() - start_time) * 1000)

        state.input_tokens += response.input_tokens
        state.output_tokens += response.output_tokens
        cached = response.cached_tokens or 0
        uncached = max(0, response.input_tokens - cached)
        reasoning_ref = None
        if response.reasoning:
            reasoning_ref = tracer.save_blob(target, f"turn_{state.turn_no}_reasoning", response.reasoning)
        tracer.event(
            target, LLM_CALL, turn_no=state.turn_no,
            model=response.model, input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            cached_tokens=cached, uncached_tokens=uncached,
            started_at=started_at, duration_ms=duration_ms, input_count=input_count,
            response_preview=message_preview(response.message),
            reasoning_ref=reasoning_ref, reasoning_len=len(response.reasoning or ""),
        )
        append_message(response.message)

        if response.message.tool_calls:
            for tool_call in response.message.tool_calls:
                tool = tool_map.get(tool_call.name)
                tool_started_at = tracer.now()
                tool_start_time = time.monotonic()
                if tool is None:
                    result = f"error: unknown tool {tool_call.name}"
                    tool_duration_ms = 0
                else:
                    try:
                        result = await tool.function(tool_call.args)
                    except Exception as error:  # noqa: BLE001
                        result = f"error: {error}"
                    tool_duration_ms = int((time.monotonic() - tool_start_time) * 1000)
                result_text = str(result)
                tracer.event(
                    target, TOOL_CALL, turn_no=state.turn_no,
                    call_id=tool_call.id, tool=tool_call.name, args=tool_call.args,
                    started_at=tool_started_at, duration_ms=tool_duration_ms,
                    result_preview=result_text[:150],
                )
                emit_event(STREAM_TOOL_CALL, turn_no=state.turn_no,
                           tool=tool_call.name, args=tool_call.args, result_preview=result_text[:150])
                append_message(Message("tool", content=result_text, tool_call_id=tool_call.id))
            if after_turn is not None:
                await await_if_awaitable(after_turn(agent))
        tracer.event(target, TURN_END, turn_no=state.turn_no,
                     input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        emit_event(STREAM_TURN_END, turn_no=state.turn_no)

        if state.status == "done" or not response.message.tool_calls:
            if before_finalize is not None:
                ok, reason = await await_if_awaitable(before_finalize(agent))
            else:
                ok, reason = True, None
            if ok:
                state.messages = messages
                state.status = "done"
                tracer.event(target, RUN_END, outcome="done",
                             total_input_tokens=state.input_tokens,
                             total_output_tokens=state.output_tokens)
                return RunOutcome(
                    outcome="done", result=state.result,
                    workspace_out=agent.workspace.workspace_out(),
                    input_tokens=state.input_tokens, output_tokens=state.output_tokens,
                )
            if state.status == "done":
                state.status = "running"
            append_message(Message("user", content=f"finalize rejected: {reason}. Fix and finalize."))

    state.messages = messages
    state.status = "paused"
    tracer.event(target, RUN_END, outcome="paused", reason="max_turns")
    return RunOutcome(
        outcome="paused", reason="max_turns",
        input_tokens=state.input_tokens, output_tokens=state.output_tokens,
    )
