"""eval.contracts：四个可插拔契约 + Project 组合（PLAN-AGENT-EVAL §2/§3.2-3.7）。

- Fixture：把世界带到起点状态，产 World（把手 dict，fixture 特有键进 extras，D14）；
- Task：给 agent 的指令（note 纯人读「这个场景想验什么」，D7）；
- Runner：跑 + 从 trace 提取 RunResult（只跑不评判）；
- Grader：吃 RunResult（+可选 world）出 Grade（D1/D2：主动 grader 只读不改）。

Grade 双形态（D3）：确定性轴 passed，LLM 轴 score（0-5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class Task:
    """给 agent 的指令 + 人读说明。grader 的期望值在 grader 构造参数里（D7）。"""

    text: str
    note: str = ""              # 这个场景想验什么（纯人读，报告里展示）
    max_turns: int | None = None


#: 世界把手：{app, client(TestClient), api(agent.client.ApiClient), workspace: Path,
#: session: dict|None, extras: dict}。固定键最小闭集 + extras 逃生口（D14）。
World = dict[str, Any]


@runtime_checkable
class Fixture(Protocol):
    def setup(self, tmp: Path) -> World: ...
    def teardown(self, world: World) -> None: ...


@runtime_checkable
class Runner(Protocol):
    name: str
    def run(self, world: World, task: Task, llm_factory: Callable[[], Any],
            run_dir: Path) -> Any: ...   # -> RunResult（result.py，避免循环导入不写死）


@runtime_checkable
class Grader(Protocol):
    name: str
    axis: str   # 工具序列 / 提案 / 结果 / 文字（+未来 格式 / 拟人）
    def grade(self, result: Any, world: World | None = None) -> Any: ...  # -> Grade


@dataclass
class Project:
    """一个评测项目 = fixture + task + runner + graders 的组合（§3.7）。"""

    id: str
    tags: tuple[str, ...]
    fixture: Fixture
    task: Task
    runner: Runner
    graders: list
    runs: int | None = None        # 项目级覆盖框架默认 N（§3.8）
    judge_model: str | None = None  # 项目级判官覆盖（先同款，D12）
