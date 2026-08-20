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
class LLMResponse:
    """LLM 一次 complete 的返回。"""
    message: Message
    input_tokens: int
    output_tokens: int
    model: str
    reasoning: str | None = None
    cached_tokens: int | None = None


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
