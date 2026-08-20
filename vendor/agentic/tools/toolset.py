"""工具声明接缝:ToolSet / ToolRegistry / assemble。

ToolSet 是有序的工具声明策略,支持 add / drop(copy-on-write);
提供两个文件契约预设,业务方也可以从零构造自己的组合。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..types import AgentInstance, Tool

ToolFactory = Callable[[AgentInstance], "Tool | None"]

FILE_CONTRACT_TOOLS = (
    "ls", "read", "glob", "grep", "write", "append", "edit", "insert", "delete", "stat", "done",
)
MULTI_AGENT_TOOLS = ("dispatch", "status", "wait")


@dataclass
class ToolEntry:
    """工具声明条目:名字(registry 查找键 / drop 靶子)或直接 factory。"""
    name: str | None
    factory: ToolFactory | None


class ToolSet:
    """有序的工具声明策略,支持 add / drop(copy-on-write)。"""

    def __init__(self, *names: str) -> None:
        self._entries: list[ToolEntry] = [ToolEntry(name, None) for name in names]

    @staticmethod
    def file_contract() -> "ToolSet":
        """文件契约基础工具集:ls/read/glob/grep/write/append/edit/insert/delete/stat/done。"""
        return ToolSet(*FILE_CONTRACT_TOOLS)

    @staticmethod
    def file_contract_with_multi_agent() -> "ToolSet":
        """文件契约基础工具集 + 多 agent 派发三件套。"""
        return ToolSet(*FILE_CONTRACT_TOOLS, *MULTI_AGENT_TOOLS)

    def add(self, factory: ToolFactory, *, name: str | None = None) -> "ToolSet":
        """追加一个用户工具(直接给 factory;name 可选,用于 drop)。"""
        new = self._copy()
        new._entries.append(ToolEntry(name, factory))
        return new

    def drop(self, *names: str) -> "ToolSet":
        """按名去掉工具。"""
        dropped = set(names)
        new = self._copy()
        new._entries = [entry for entry in new._entries if entry.name not in dropped]
        return new

    def _copy(self) -> "ToolSet":
        new = ToolSet()
        new._entries = list(self._entries)
        return new

    def __iter__(self):
        for entry in self._entries:
            yield entry.name, entry.factory

    def names(self) -> list[str | None]:
        return [entry.name for entry in self._entries]


class ToolRegistry:
    """name -> ToolFactory 表,由 Engine 在启动时 seed 内置工具。"""

    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(self, name: str, factory: ToolFactory) -> None:
        self._factories[name] = factory

    def get(self, name: str) -> ToolFactory:
        return self._factories[name]

    def __contains__(self, name: object) -> bool:
        return name in self._factories


def assemble(toolset: ToolSet, agent: AgentInstance, registry: ToolRegistry) -> list[Tool]:
    """把 ToolSet 按 registry + agent 解析成最终工具列表。

    名字条目经 registry 解析;直接 factory 直接调用;factory 返回 None 则跳过;
    重名直接报错(runner 的 tool_map 是 last-wins,这里先拦住)。
    """
    tools: list[Tool] = []
    for name, factory in toolset:
        resolved_factory = factory if factory is not None else registry.get(name)
        tool = resolved_factory(agent)
        if tool is None:
            continue
        tools.append(tool)
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ValueError(f"{type(agent.spec).__name__}: duplicate tool names {duplicates}")
    return tools
