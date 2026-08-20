"""Engine:多 agent 编排 + 共享工作区任务系统。

共享工作区模型:整个 agent 系统持有一个 Workspace;每个 agent 拿到一个
ScopedWorkspace 权限视图,写权由 Guard 归属登记划分。
- 主 agent(entry):未归属文件全权。
- 子 agent:拥有自己区域(agents/<target>/)+ dispatch 时额外 grant 的文件;
  区域外只读可见(hidden=True 则区域外不可见)。
- 子 agent done/error 后 release,归属回主。

dispatch 后台派子 agent,status 非阻塞感知,wait 挂起汇合。
"""
from __future__ import annotations

import asyncio

from ..config import Config
from ..contract import WorkContract
from ..guard import Guard
from ..llm import LLMClient, OpenAIClient
from ..runner import LLM_TURN_TIMEOUT, run
from ..store import Store
from ..trace import Tracer
from ..trace.events import RUN_START
from ..tools import make_dispatch_tool, make_done_tool, make_status_tool, make_wait_tool
from ..tools.toolset import ToolRegistry
from ..types import AgentInstance, Message, TaskStatus
from ..workspace import MemoryWorkspace, ScopedWorkspace, Workspace
from ..workspace.tools import make_workspace_tools


class Engine:
    def __init__(
        self, llm: LLMClient, tracer: Tracer, *,
        workspace: Workspace | None = None,
        store: Store | None = None,
        max_turns: int = 20, max_concurrent: int = 100,
        llm_timeout: int = LLM_TURN_TIMEOUT,
    ) -> None:
        self._llm = llm
        self._tracer = tracer
        self._shared = workspace or MemoryWorkspace()
        self._store = store
        self._max_turns = max_turns
        self._llm_timeout = llm_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._guard = Guard()
        self._specs: dict[str, object] = {}
        self._hidden: dict[str, bool] = {}
        self._entry: str | None = None
        self._agents: dict[str, AgentInstance] = {}
        self._children: dict[str, set[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._status: dict[str, TaskStatus] = {}
        self._tool_registry = ToolRegistry()
        self._seed_tools()

    def _seed_tools(self) -> None:
        """把内置工具工厂 seed 进 ToolRegistry;dispatch 三件套闭包捕获 engine。"""
        def workspace_tool_factory(name: str):
            def factory(agent):
                for tool in make_workspace_tools(agent.workspace):
                    if tool.name == name:
                        return tool
                return None
            return factory

        for name in ("ls", "read", "glob", "grep", "write", "append", "edit", "insert", "delete", "stat"):
            self._tool_registry.register(name, workspace_tool_factory(name))
        self._tool_registry.register("dispatch", lambda agent: make_dispatch_tool(self, agent, agent.version))
        self._tool_registry.register("status", lambda agent: make_status_tool(self, agent))
        self._tool_registry.register("wait", lambda agent: make_wait_tool(self, agent))
        self._tool_registry.register("done", lambda agent: make_done_tool(agent))

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    def register_tool(self, name: str, factory) -> None:
        """注册一个共享工具工厂(按名);spec 的 tools() 可用名字引用。"""
        self._tool_registry.register(name, factory)

    @classmethod
    def from_config(
        cls, config: Config, tracer: Tracer, *,
        workspace: Workspace | None = None,
        store: Store | None = None,
        max_turns: int = 20, max_concurrent: int = 100, llm_timeout: int = LLM_TURN_TIMEOUT,
    ) -> "Engine":
        return cls(
            OpenAIClient(
                api_key=config.api_key, base_url=config.base_url, model=config.model,
                enable_thinking=config.enable_thinking, reasoning_effort=config.reasoning_effort,
            ),
            tracer, workspace=workspace, store=store,
            max_turns=max_turns, max_concurrent=max_concurrent, llm_timeout=llm_timeout,
        )

    @property
    def shared_workspace(self) -> Workspace:
        return self._shared

    @property
    def guard(self) -> Guard:
        return self._guard

    def register(self, type_key: str, spec, *, entry: bool = False, hidden: bool = False) -> None:
        """注册一种 agent 类型。entry=True 标记入口;hidden=True 让子区域外不可见。"""
        self._specs[type_key] = spec
        self._hidden[type_key] = hidden
        if entry:
            self._entry = type_key

    def register_documents(self, documents: dict[str, str]) -> None:
        """把文档注册进共享工作区(内存模式常用;磁盘模式会写入根目录)。"""
        for path, content in documents.items():
            self._shared.add_document(path, content)

    def _scope_for(self, type_key: str, target: str, caller: AgentInstance | None) -> str:
        """可写区域前缀:主(caller=None)→ 全权;子 → agents/<target>/。"""
        return "" if caller is None else f"agents/{target}/"

    @staticmethod
    def _apply_contract(agent: AgentInstance) -> None:
        """把 agent.contract 同步到 ScopedWorkspace,用于写权限约束。"""
        workspace = getattr(agent, "workspace", None)
        set_contract = getattr(workspace, "set_contract", None)
        if callable(set_contract):
            set_contract(getattr(agent, "contract", None))

    async def create_or_get(
        self, type_key: str, target: str, version: str, *, caller: AgentInstance | None = None
    ) -> AgentInstance:
        if target in self._agents:
            return self._agents[target]
        spec = self._specs[type_key]
        hidden = self._hidden.get(type_key, False)
        prefix = self._scope_for(type_key, target, caller)
        workspace = ScopedWorkspace(self._shared, target, self._guard, hidden=hidden)
        agent = spec.create(target, version=version, engine=self, workspace=workspace)
        agent.role = "main" if caller is None else "sub"
        agent.workdir = prefix or None
        # 应用 spec 上声明的固定文件契约(如果子类 override 了 contract())
        contract_method = getattr(spec, "contract", None)
        if callable(contract_method):
            agent.contract = contract_method()
        self._apply_contract(agent)
        self._agents[target] = agent
        self._tracer.event(target, RUN_START, type_key=type_key, target=target, version=version,
                           caller_target=caller.target if caller else None)
        return agent

    async def dispatch(
        self, type_key: str, target: str, task: str = "",
        *, caller: AgentInstance | None = None, version: str | None = None,
        grant: list[str] | None = None, contract: WorkContract | None = None,
    ) -> TaskStatus:
        """后台派子 agent,立即返回 running(+ workdir)。grant=[paths] 额外授予子写权。"""
        if caller is None:
            return TaskStatus(target, type_key, "error", summary="dispatch requires a caller")
        prefix = self._scope_for(type_key, target, caller)
        self._tracer.dispatch(caller.target, target, type_key)
        self._children.setdefault(caller.target, set()).add(target)
        if target in self._status:
            return self._status[target]
        callee = await self.create_or_get(type_key, target, version, caller=caller)
        if contract is not None:
            callee.contract = contract
            self._apply_contract(callee)
        self._guard.add_region(target, prefix)
        for path in (grant or []):
            self._guard.grant(path, target)
        callee.state.inbox.append(Message("user", content=f"[workdir:{prefix}] {task}"))
        self._status[target] = TaskStatus(target, type_key, "running", workdir=prefix or None)
        self._tasks[target] = asyncio.create_task(self._run(callee))
        return self._status[target]

    def status(self, caller: AgentInstance, targets: list[str] | None = None) -> list[TaskStatus]:
        """非阻塞查看子 agent 状态。"""
        children = self._children.get(caller.target, set())
        wanted = list(targets) if targets else sorted(children)
        return [self._status.get(target, TaskStatus(target, "?", "pending")) for target in wanted]

    async def wait(
        self, caller: AgentInstance, targets: list[str] | None = None,
        *, mode: str = "all",
    ) -> list[TaskStatus]:
        """挂起 caller,直到 some(mode='any')/ all(mode='all') 指定子 agent 完成。"""
        children = self._children.get(caller.target, set())
        wanted = set(targets) if targets else set(children)
        if not wanted:
            return []
        pending = [self._tasks[target] for target in wanted
                   if target in self._tasks and not self._tasks[target].done()]
        if pending:
            if mode == "any":
                await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)
        return [self._status.get(target) for target in wanted]

    async def _run(self, agent: AgentInstance) -> None:
        """后台跑子 agent 到完成;done/paused/error 后 release 归属。"""
        type_key = agent.spec.type_key
        previous = self._status.get(agent.target)
        workdir = previous.workdir if previous else None
        try:
            outcome = await run(
                agent, self._llm, self._tracer,
                max_turns=self._max_turns, semaphore=self._semaphore, llm_timeout=self._llm_timeout,
            )
            self._status[agent.target] = TaskStatus(
                agent.target, type_key, outcome.outcome, workdir=workdir, result=outcome.result,
                summary=None if outcome.outcome == "done" else outcome.reason,
            )
        except Exception as error:  # noqa: BLE001
            self._status[agent.target] = TaskStatus(
                agent.target, type_key, "error", workdir=workdir, summary=str(error),
            )
        finally:
            self._guard.release(agent.target)

    async def start(self, target: str, task: str, *, version: str = "", contract: WorkContract | None = None) -> TaskStatus:
        """启动入口 agent,同步跑到完成。contract 提供文件契约模板。"""
        if self._entry is None:
            raise RuntimeError("no entry agent registered (register(..., entry=True))")
        self._guard.set_main(target)
        if self._store is not None:
            data = await self._store.load()
            for path, content in data.items():
                self._shared.add_document(path, content)
        # 保存初始工作区快照,配合 tool_call 事件可回放复原整个执行过程
        self._tracer.save_workspace_snapshot("initial", self._shared.snapshot())
        master = await self.create_or_get(self._entry, target, version, caller=None)
        if contract is not None:
            master.contract = contract
            self._apply_contract(master)
        master.state.inbox.append(Message("user", content=task))
        outcome = await run(
            master, self._llm, self._tracer,
            max_turns=self._max_turns, semaphore=self._semaphore, llm_timeout=self._llm_timeout,
        )
        final_files = self._shared.snapshot()
        self._tracer.save_workspace_snapshot("final", final_files)
        if self._store is not None:
            await self._store.save(final_files)
        return TaskStatus(
            target, self._entry, outcome.outcome, result=outcome.result,
            summary=None if outcome.outcome == "done" else outcome.reason,
        )
