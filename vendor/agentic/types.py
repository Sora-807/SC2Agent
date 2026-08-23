"""Agent 运行时核心数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ToolCall:
    """LLM 发起的一次工具调用(已解析 arguments)。"""
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Message:
    """OpenAI 消息格式。"""
    role: str  # system / user / assistant / tool
    content: str | None = None
    tool_calls: list[ToolCall] | None = None      # assistant only
    tool_call_id: str | None = None               # tool role only


@dataclass
class Tool:
    """agent 可调用的闭包工具:声明(name/description/parameters)+ 执行(function)。"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema(function parameters)
    function: Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class LLMDelta:
    """LLM 一次 complete 内部的流式增量分片(on_delta 回调逐条给出)。

    kind:"reasoning"(思考链)| "content"(正文)| "tool_call"(工具调用 arguments 分片)。
    tool_call 的 index 是同一条回复里第几个 tool_call(从 0 起)。
    """
    kind: str
    text: str
    index: int | None = None


@dataclass
class LLMResponse:
    """LLM 一次 complete 的返回。"""
    message: Message
    input_tokens: int
    output_tokens: int
    model: str
    reasoning: str | None = None
    cached_tokens: int | None = None


# ---- 流式事件类型(Engine.start_stream 的内存事件通道词汇;不进 trace,见 ADR-0007)----
# runner 发 delta/turn/tool 五种;run_end 由 engine 发(它拥有 TaskStatus 终态)。
STREAM_DELTA = "delta"            # LLM 增量分片(带 delta: LLMDelta + turn_no)
STREAM_TURN_START = "turn_start"  # 一轮开始
STREAM_TOOL_CALL = "tool_call"    # 一轮中一次工具执行完成(带 tool/args/result_preview)
STREAM_TURN_END = "turn_end"      # 一轮结束
STREAM_LLM_TIMEOUT = "llm_timeout"  # 一轮 LLM 调用超时(会重试)
STREAM_RUN_END = "run_end"        # 一个 agent 的 run 终态(带 outcome/result/summary)

STREAM_TYPES = (STREAM_DELTA, STREAM_TURN_START, STREAM_TOOL_CALL, STREAM_TURN_END, STREAM_LLM_TIMEOUT, STREAM_RUN_END)


@dataclass
class StreamEvent:
    """start_stream 产出的一条事件:入口/子 agent 的增量与阶段动态。

    agent_id 标归属(dispatch 出去的子 agent 事件与入口混在同一条流里);
    字段按 type 取用,其余为 None。
    """
    agent_id: str
    type: str
    turn_no: int | None = None
    delta: LLMDelta | None = None         # type=STREAM_DELTA
    tool: str | None = None               # type=STREAM_TOOL_CALL
    args: dict | None = None              # type=STREAM_TOOL_CALL
    result_preview: str | None = None     # type=STREAM_TOOL_CALL
    outcome: str | None = None            # type=STREAM_RUN_END: done/paused/error
    result: str | None = None             # type=STREAM_RUN_END: done 提交的 result
    summary: str | None = None            # type=STREAM_RUN_END: paused/error 原因


@dataclass
class RunOutcome:
    """runner 一次 run 的结果。"""
    outcome: str  # "done" | "paused"
    workspace_out: dict[str, str] | None = None
    reason: str | None = None
    result: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AgentState:
    """agent 运行态。"""
    messages: list[Message] = field(default_factory=list)
    turn_no: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    inbox: list[Message] = field(default_factory=list)
    status: str = "idle"  # idle / running / paused / done
    result: str | None = None


@dataclass
class AgentInstance:
    """一个 agent 实例(spec + target + workspace + tools + state)。"""
    spec: object
    target: str
    version: str
    workspace: object
    tools: list[Tool] = field(default_factory=list)
    state: AgentState = field(default_factory=AgentState)
    role: str = ""               # "main" | "sub"
    workdir: str | None = None   # 子 agent 的可写区域前缀,主 agent 为 None
    contract: object | None = None  # WorkContract,单 agent 文件契约场景使用


@dataclass
class TaskStatus:
    """dispatch 出去的子 agent 的状态快照。"""
    target: str
    type_key: str
    state: str  # running / done / paused / error
    workdir: str | None = None
    result: str | None = None
    summary: str | None = None
