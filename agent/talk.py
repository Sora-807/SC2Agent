"""agent.talk：对话式顾问 —— 离线商量回路（P3 切片 4，用户 2026-08-22 拍板方向）。

每条玩家消息 = 同一 target 上的一轮 `engine.start`。BaseAgent 的 `create_or_get`
复用同一个 agent 实例、runner 退出时把 messages 写回 `state.messages` —— 对话
历史天然延续，不用自己搬历史。三个必须知道的运行时事实（都验过源码/实测）：

- `state.turn_no` 是**终身累计**的，而 max_turns 按它比较 —— 不重置的话聊几轮后
  每轮一进来就 max_turns 退出（paused、无回复）。所以每轮说话前归零
  （「每轮预算」才是聊天语义；动的是实例属性，不改 vendor 代码）。
- 一个 Tracer 一个 run_id：整场对话共用一个 trace 目录，每轮收尾
  `finalize_summary()` 重算 trace.html —— 聊到一半也能打开看（可视化常开）。
- **引擎必须跑在专属事件循环线程里**（2026-08-22 真 LLM 实测踩死锁）：工具面走
  ApiClient（同步 urllib）回环访问**本服务**的 REST —— 若引擎跑在 uvicorn 的
  事件循环上，同步 urllib 会阻塞该循环，回环请求永远进不来（list_plans 超时 ×2，
  agent 只能回报失败）。专属线程 + 常驻循环：urllib 阻塞的是对话线程，服务循环
  空闲接回环，各得其所。

LLM 惰性构造：没配密钥时 `OpenAIClient.from_env` 会抛 KeyError —— 不在启动时炸，
第一条消息时才显形（G7：禁用必须带理由，而不是服务起不来）。
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agentic import Engine, Tracer
from agentic.llm import OpenAIClient
from agentic.types import StreamEvent

from agent.client import ApiClient
from agent.spec import AdvisorSpec
from agent.readonly import default_areas
from agent.workspace import ApiWorkspace, ChangeLog

#: LLM 工厂默认实现（惰性；测试注入 FakeLLM 脚本）
DefaultLLMFactory = Callable[[], object]


def openai_from_env() -> object:
    return OpenAIClient.from_env()


def _clip_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


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


class RoundHandle:
    """一轮流式对话的把手：events 是跨线程队列（dict 流 + None 哨兵收尾）。"""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        #: 收尾后的最终载荷（round 事件也会走队列，这里只是同步镜像）
        self.result: dict | None = None

    def _finish(self, result: dict) -> None:
        self.result = result
        self.events.put({"type": "round", **result})
        self.events.put(None)


class AgentTalk:
    """常驻对话顾问。一个实例 = 一场对话（同 target 同 Engine 同 trace 目录）。"""

    def __init__(self, client: ApiClient, *, llm_factory: DefaultLLMFactory,
                 trace_root: Path, workspace_root: Path,
                 target: str = "advisor#1", max_turns: int = 8,
                 history_path: Path | None = None,
                 recordings_dir: Path | None = None,
                 proposals_log: Path | None = None,
                 map_plans_dir: Path | None = None) -> None:
        self._client = client
        self._llm_factory = llm_factory
        self._trace_root = Path(trace_root)
        self._workspace_root = Path(workspace_root)
        # 只读区挂载点（I20）：录像/提案审计史由 create_app 传入（它知道持久化路径）；
        # traces 直接用 trace_root。None = 不挂该区（测试默认不持久化）。
        self._readonly_recordings = Path(recordings_dir) if recordings_dir else None
        self._readonly_proposals = Path(proposals_log) if proposals_log else None
        self._readonly_map_plans = Path(map_plans_dir) if map_plans_dir else None
        self._target = target
        self._max_turns = max_turns
        self._changes = ChangeLog()
        self._engine: Engine | None = None
        self._tracer: Tracer | None = None
        self._lock = asyncio.Lock()
        self._engine_error: str | None = None
        #: UI 读的历史（重启即空 —— 完整记录在 trace 目录，那是审计载体）。
        #: history_path 给了就持久化（JSON）：重启后 UI 历史还在，且引擎首次启动时
        #: 把历史喂回 state.messages —— **LLM 也记得**（真·跨重启续聊）。
        self._history_path = Path(history_path) if history_path else None
        #: UI 读的历史
        self.history: list[dict] = self._load_history()
        self._seeded = False
        #: 本轮进行中的步骤（流式动效的燃料）：say 开始记 events_before，
        #: GET 轮询时读增量；最后一个 tool_call 无结果即「运行中」，
        #: 最后一个 llm_call 的 reasoning 在下一个事件到来前 = 思考中。
        self._round_mark: int | None = None
        self.running_steps: list[dict] = []
        # 专属事件循环线程（见模块 docstring 的死锁教训）；daemon = 进程退出即走
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        threading.Thread(target=self._loop_main, daemon=True,
                         name="agent-talk").start()

    def _load_history(self) -> list[dict]:
        if self._history_path is None or not self._history_path.exists():
            return []
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []      # 坏文件不当包袱：从零开始聊

    def _save_history(self) -> None:
        if self._history_path is None:
            return
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.history, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self._history_path)

    def _loop_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    # ---- 状态面（任意线程可读） ----

    def describe(self) -> dict:
        return {
            "target": self._target,
            "turns": len([m for m in self.history if m["role"] == "user"]),
            "messages": self.history,
            "trace_html": (str(self._tracer.trace_dir / "trace.html")
                           if self._tracer is not None else None),
            "running_steps": self.running_steps,
        }

    # ---- 对话面（在调用方的循环上 await，实际跑在专属线程里） ----

    async def say(self, text: str) -> dict:
        """玩家说一句 → 跑一轮 → 回 agent 的结论（done 的 result 文本）。"""
        text = text.strip()
        if not text:
            return {"error": "要说点什么（空消息）"}
        self._loop_ready.wait(timeout=5)
        loop = self._loop
        if loop is None:
            return {"error": "对话循环线程没起来（内部错误）"}
        fut = asyncio.run_coroutine_threadsafe(self._say_on_engine_loop(text), loop)
        return await asyncio.wrap_future(fut)

    def start_round(self, text: str) -> RoundHandle:
        """流式开一轮：立即返回把手，事件经跨线程队列流式涌出（SSE 端点消费）。

        引擎仍跑在专属循环线程上（死锁教训见模块 docstring）；事件用 queue.Queue
        跨线程递到 uvicorn 循环 —— SSE handler 用 to_thread 拉取即可。
        """
        handle = RoundHandle()
        text = (text or "").strip()
        if not text:
            handle._finish({"error": "要说点什么（空消息）"})
            return handle
        self._loop_ready.wait(timeout=5)
        loop = self._loop
        if loop is None:
            handle._finish({"error": "对话循环线程没起来（内部错误）"})
            return handle

        def emit(ev: StreamEvent) -> None:
            handle.events.put(_event_dict(ev))

        async def runner() -> None:
            try:
                result = await self._say_on_engine_loop(text, emit=emit)
            except Exception as exc:  # noqa: BLE001 —— 流面错误也要走完事件流（不挂死 SSE）
                result = {"error": f"{type(exc).__name__}: {exc}"}
            handle._finish(result)

        fut = asyncio.run_coroutine_threadsafe(runner(), loop)
        fut.add_done_callback(lambda _f: handle.events.put(None))
        return handle

    # ---- 以下全部跑在专属循环上 ----

    async def _say_on_engine_loop(
        self, text: str, emit: Callable[[StreamEvent], None] | None = None,
    ) -> dict:
        async with self._lock:
            engine, tracer = await self._ensure_engine()
            if engine is None:
                return {"error": self._engine_error}
            # turn_no 归零：见模块 docstring（终身累计 vs 每轮预算）
            agent = await engine.create_or_get("advisor", self._target, "")
            agent.state.turn_no = 0
            if not self._seeded and self.history:
                self._seed_history(agent)
            self._seeded = True
            events_before = len(tracer.read_events(self._target))
            self._round_mark = events_before
            self.running_steps = []
            reply = None
            outcome = None
            try:
                if emit is None:
                    status = await engine.start(self._target, text)
                    outcome = getattr(status, "state", None)
                    reply = getattr(status, "result", None)
                else:
                    # 流式：start_stream 逐事件外发（delta/turn/tool），末条 run_end 带终态
                    async for ev in engine.start_stream(self._target, text):
                        emit(ev)
                        if ev.type == "run_end":
                            outcome = ev.outcome
                            reply = ev.result
            finally:
                self._round_mark = None
                self.running_steps = []
            steps = self._round_steps(tracer, events_before)
            tracer.finalize_summary()
            if not reply:
                # 纯文本回复也算回复（聊天语义）—— 只有 result=None 且没有任何
                # assistant 文本（比如一上来就 done 且没带参数）才落到提示。
                for msg in reversed(agent.state.messages):
                    if msg.role == "assistant" and (msg.content or "").strip():
                        reply = msg.content.strip()
                        break
            reply = reply or "（这轮没有产出 —— 换个说法再试一次）"
            # 轮末改动清单：写钩子（规划文件）与 propose（对局域）攒的 ChangeRecord，
            # 随消息进历史 —— 前端把它渲染成可点击的跳转 chip（刷新后仍在）。
            round_changes = [c.to_json() for c in self._changes.drain()]
            self.history.append({"role": "user", "text": text, "at": time.time()})
            self.history.append({"role": "agent", "text": reply, "at": time.time(),
                                 **({"steps": steps} if steps else {}),
                                 **({"changes": round_changes} if round_changes else {})})
            self._save_history()
            return {
                "reply": reply,
                "outcome": outcome,
                "changes": round_changes,
                "messages": self.history,
            }

    def _seed_history(self, agent) -> None:  # noqa: ANN001 —— AgentInstance，避免引 vendor 内部类型
        """重启后把持久化历史喂回 state.messages —— LLM 侧也「记得」聊过什么。

        runner 只在 messages 为空时自己加系统提示，所以种子必须自带 system 头。
        steps 不回灌（那是过程展示，不是对话内容）。
        """
        from agentic.prompt import build_system_prompt
        from agentic.types import Message

        role_map = {"user": "user", "agent": "assistant"}
        msgs = [Message("system", build_system_prompt(agent))]
        for m in self.history:
            role = role_map.get(m.get("role"))
            text = str(m.get("text") or "").strip()
            if role and text:
                msgs.append(Message(role, text))
        agent.state.messages = msgs

    def refresh_running_steps(self) -> None:
        """轮询面：从 trace 增量推「进行中步骤」（GET /api/agent/chat 调）。

        规则：本轮事件里最后一个 tool_call 还没写 result_preview = 工具运行中；
        否则最后一个 llm_call 带 reasoning = 思考中（blob 是流式追加的，读最新）。
        专属循环线程在跑引擎、本方法在服务循环上读文件 —— 都是小读，无锁安全。
        """
        mark = self._round_mark
        if mark is None or self._tracer is None:
            self.running_steps = []
            return
        steps: list[dict] = []
        try:
            events = self._tracer.read_events(self._target)[mark:]
        except OSError:
            self.running_steps = []
            return
        pending_tool: dict | None = None
        last_reasoning_ref: str | None = None
        for ev in events:
            if ev.get("type") == "tool_call":
                pending_tool = ev
                last_reasoning_ref = None      # 工具已接管，思考段视为结束
            elif ev.get("type") == "llm_call" and ev.get("reasoning_ref"):
                last_reasoning_ref = str(ev["reasoning_ref"])
                pending_tool = None
        if pending_tool is not None and pending_tool.get("tool") != "done":
            steps.append({
                "kind": "tool",
                "tool": pending_tool.get("tool", "?"),
                "args": _clip_text(json.dumps(pending_tool.get("args", {}), ensure_ascii=False), 200),
                "preview": "", "duration_ms": 0,
            })
        elif last_reasoning_ref is not None:
            try:
                text = (self._tracer.trace_dir / last_reasoning_ref).read_text(encoding="utf-8")
            except OSError:
                text = ""
            if text.strip():
                steps.append({"kind": "reasoning", "text": _clip_text(text.strip(), 2000)})
        self.running_steps = steps

    def _round_steps(self, tracer: Tracer, events_before: int) -> list[dict]:
        """本轮的可见过程：工具调用（名称/参数摘要/结果摘要/耗时）+ 思考全文。

        事件按追加序切片（turn_no 被我们每轮归零，不能当过滤键）。reasoning_ref
        指向 trace 目录里的 blob 文件，读出来给前端折叠展示 —— 思考过程不该
        只存在于 trace.html 里，商量时就要看得见。
        """
        steps: list[dict] = []
        for ev in tracer.read_events(self._target)[events_before:]:
            et = ev.get("type")
            if et == "tool_call":
                steps.append({
                    "kind": "tool",
                    "tool": ev.get("tool", "?"),
                    "args": _clip_text(json.dumps(ev.get("args", {}), ensure_ascii=False), 200),
                    "preview": _clip_text(str(ev.get("result_preview") or ""), 300),
                    "duration_ms": ev.get("duration_ms", 0),
                })
            elif et == "llm_call":
                ref = ev.get("reasoning_ref")
                if not ref:
                    continue
                blob = tracer.trace_dir / str(ref)
                try:
                    reasoning = blob.read_text(encoding="utf-8")
                except OSError:
                    continue
                if reasoning.strip():
                    steps.append({"kind": "reasoning",
                                  "text": _clip_text(reasoning.strip(), 2000)})
        return steps

    async def _ensure_engine(self) -> tuple[Engine | None, Tracer | None]:
        if self._engine is not None:
            return self._engine, self._tracer
        try:
            llm = self._llm_factory()
        except Exception as exc:  # noqa: BLE001 —— 密钥缺失等启动期错误要变成 G7 理由
            missing = [k for k in ("OPENAI_API_KEY", "LLM_MODEL") if k in str(exc)]
            self._engine_error = (
                "LLM 未配置（" + ("、".join(missing) + " 缺失）" if missing
                                  else f"{type(exc).__name__}: {exc}")
                + "）—— 在 .env 里配好 OPENAI_API_KEY / LLM_MODEL 再试")
            return None, None
        self._tracer = Tracer(self._trace_root)
        self._engine = Engine(
            llm, self._tracer,
            # 与 agent.run 同一纪律：虚拟文件工作区（plans//map-plans/ 走规划 API，
            # 只读区挂运行时产物，scratch 是磁盘自留地）—— R5 的机制保证不变
            workspace=ApiWorkspace(
                self._client, self._workspace_root, self._changes,
                readonly=default_areas(
                    client=self._client, trace_root=self._trace_root,
                    recordings_dir=self._readonly_recordings,
                    proposals_log=self._readonly_proposals,
                    map_plans_dir=self._readonly_map_plans)),
            max_turns=self._max_turns,
        )
        self._engine.register("advisor",
                              AdvisorSpec(self._client, changes=self._changes), entry=True)
        self._engine_error = None
        return self._engine, self._tracer
