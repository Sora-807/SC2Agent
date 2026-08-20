"""AgentSpec:agent 类型基类。

子类声明 type_key + system_prompt(+ hooks)+ can_dispatch;默认工具集是文件契约
基础工具 + done;can_dispatch=True 再追加 dispatch/status/wait。
"""
from __future__ import annotations

from .tools.toolset import ToolSet, assemble
from .types import AgentInstance, AgentState


class AgentSpec:
    """agent 类型基类。子类 override system_prompt(+ hooks + 可选 tools())。"""
    type_key: str = ""
    can_dispatch: bool = False

    def system_prompt(self) -> str:
        raise NotImplementedError

    def contract(self):
        """可选的固定文件契约。子类 override 后,该类型的每个 agent 实例都会使用。"""
        return None

    def hooks(self) -> dict:
        return {}

    def tools(self) -> ToolSet:
        """声明本 agent 类型的工具集(默认:文件契约工具 + done;can_dispatch 加派发三件套)。"""
        if self.can_dispatch:
            return ToolSet.file_contract_with_multi_agent()
        return ToolSet.file_contract()

    def create(self, target, *, version, engine, workspace):
        agent = AgentInstance(
            spec=self, target=target, version=version,
            workspace=workspace, tools=[], state=AgentState(),
        )
        agent.tools = assemble(self.tools(), agent, engine.tool_registry)
        return agent
