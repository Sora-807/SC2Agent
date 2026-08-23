"""agentic:文件契约式 agent 框架。

一个引擎持有一个共享 Workspace(内存模式或磁盘模式);权限由 Guard 归属登记划分。
单 agent 用 WorkContract 描述任务、输入、输出目录;多 agent 用 dispatch/status/wait 协作。
"""
from .config import Config
from .contract import WorkContract, build_contract_prompt
from .engine import Engine
from .guard import Guard
from .llm import FakeLLMClient, LLMClient, OpenAIClient, parse_assistant, to_openai_messages, to_openai_tools
from .prompt import build_system_prompt
from .runner import LLM_TURN_TIMEOUT, Spec, run
from .spec import AgentSpec
from .store import DirStore, Store
from .trace import Tracer, load_trace, render_trace_html
from .tools import make_dispatch_tool, make_done_tool, make_status_tool, make_wait_tool
from .tools.toolset import ToolFactory, ToolRegistry, ToolSet, assemble
from .types import (
    AgentInstance,
    AgentState,
    LLMDelta,
    LLMResponse,
    Message,
    RunOutcome,
    StreamEvent,
    TaskStatus,
    Tool,
    ToolCall,
)
from .workspace import DiskWorkspace, MemoryWorkspace, ScopedWorkspace, Workspace
from .workspace.tools import make_workspace_tools

__all__ = [
    # types
    "ToolCall", "Message", "Tool", "LLMDelta", "LLMResponse", "RunOutcome",
    "AgentState", "AgentInstance", "TaskStatus", "StreamEvent",
    # config + llm
    "Config", "LLMClient", "OpenAIClient", "FakeLLMClient",
    "to_openai_messages", "to_openai_tools", "parse_assistant",
    # contract + workspace
    "WorkContract", "build_contract_prompt",
    "Workspace", "MemoryWorkspace", "DiskWorkspace", "ScopedWorkspace", "make_workspace_tools",
    # runner
    "Spec", "run", "LLM_TURN_TIMEOUT", "AgentSpec",
    # engine + guard + store
    "Engine", "Guard", "Store", "DirStore",
    # tools
    "make_dispatch_tool", "make_status_tool", "make_wait_tool", "make_done_tool",
    "ToolSet", "ToolFactory", "ToolRegistry", "assemble",
    # prompt
    "build_system_prompt",
    # trace
    "Tracer", "load_trace", "render_trace_html",
]
