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
from agent.tools import InterjectionQueue
from agent.workspace import ApiWorkspace, ChangeLog

#: LLM 工厂默认实现（惰性；测试注入 FakeLLM 脚本）
DefaultLLMFactory = Callable[[], object]


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


#: 一轮对话的看门狗上限（秒）。**无论卡在哪**（LLM 停滞 / 工具死等 / 未知 await
#: 永不返回——2026-08-23 真机案例：run_start 后 20 分钟无连接无事件，整条对话
#: 通道被 _lock 锁死且无错误显形），超时即取消本轮并报错：宁可显形重来，不可静默挂死。
#: 局限：协程卡在**同步阻塞**（非 await）时循环线程本身被占，看门狗也救不了。
ROUND_TIMEOUT_SECONDS = 600.0

#: 对局跟随（2026-08-24 用户拍板：对局期间只要游戏没结束，就不让 agent 停）——
#: 每轮结束后查会话：游戏还活着 → 注入一条「跟随提醒」再跑一轮（引导 sleep→observe）。
#: 止损只剩总轮数一道（§0.52 用户拍板 200；「连续纯文字就放弃」已删 —— 启动期
#: agent 爱回文字，3 轮就「跟随停止」正是启动期停摆的根因）。提醒轮在历史里带
#: `nudge: True` 标记，前端渲染成系统条而不是用户气泡。
FOLLOW_MAX_ROUNDS = 200


def _system_prompt(agent) -> str:  # noqa: ANN001 —— AgentInstance，避免引 vendor 内部类型
    """本地组装系统提示词（§0.52 F 批，done 下线的第三件）。

    vendor 的 `agentic.prompt.build_system_prompt` 在无 WorkContract 时会固定追加
    「完成任务后调用 done 提交结果」—— done 已从工具集摘除，留着这行会引导 agent
    每次收尾都试调一个不存在的工具、白烧一轮。骨架照抄 vendor（任务 + 工具使用
    三行 + `tool_summary` 工具清单），done 行换成自然结束语义。vendor 源码不动。
    """
    from agentic.contract import tool_summary

    return f"""# 任务
{agent.spec.system_prompt()}

# 工具使用
- 所有路径都使用相对于工作区根目录的完整路径。
- 已存在的文件要先 read 才能写或改;工具返回 error: 时按提示重试。
- 回答完用户或做完一件事，直接用文字回复即可 —— 回复即回合自然结束（没有 done 工具）。

# 可用工具
{tool_summary(agent.tools)}"""


class RoundHandle:
    """一轮流式对话的把手：events 是跨线程队列（dict 流 + None 哨兵收尾）。"""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        #: 收尾后的最终载荷（round 事件也会走队列，这里只是同步镜像）
        self.result: dict | None = None

    def _finish(self, result: dict) -> None:
        if self.result is not None:
            return    # 幂等：看门狗与 runner 可能先后收尾，双哨兵会打乱 SSE 消费端
        self.result = result
        self.events.put({"type": "round", **result})
        self.events.put(None)


class AgentTalk:
    """常驻对话顾问。一个实例 = 一场对话（同 target 同 Engine 同 trace 目录）。"""

    def __init__(self, client: ApiClient, *, llm_factory: DefaultLLMFactory,
                 trace_root: Path, workspace_root: Path,
                 target: str = "advisor#1", max_turns: int = 500,
                 history_path: Path | None = None,
                 recordings_dir: Path | None = None,
                 proposals_log: Path | None = None,
                 map_plans_dir: Path | None = None,
                 round_timeout: float = ROUND_TIMEOUT_SECONDS,
                 max_output_tokens: int = 1_000_000) -> None:
        self._client = client
        self._llm_factory = llm_factory
        self._trace_root = Path(trace_root)
        self._workspace_root = Path(workspace_root)
        # 只读区挂载点（I20）：录像/提案审计史由 create_app 传入（它知道持久化路径）；
        # traces 直接用 trace_root。None = 不挂该区（测试默认不持久化）。
        self._readonly_recordings = Path(recordings_dir) if recordings_dir else None
        self._readonly_proposals = Path(proposals_log) if proposals_log else None
        self._readonly_map_plans = Path(map_plans_dir) if map_plans_dir else None
        self._round_timeout = float(round_timeout)
        # 轮预算（2026-08-24 用户拍板）：turn 上限放开到 500（对局跟随一轮可以很久），
        # 真正的刹车 = **输出 token 预算**（默认 1M/轮，LLM usage 统计，trace 的
        # llm_call 一直在记）。用尽 → 客户端返回空响应走自然收轮 + 落史说明。
        self._max_output_tokens = int(max_output_tokens)
        self._llm = None                     # _ensure_engine 后持有（预算复位用）
        self._round_activity = time.monotonic()   # 活性看门狗的时间戳（事件到达刷新）
        # 记忆工作区种子（2026-08-23 用户拍板）：目录丢了/换机器，从随库模板自动补齐
        #（只补缺失，用户的修改永远优先）
        from agent.bootstrap import ensure_defaults
        from agent.memory_seed import seed_memory_workspace

        seed_memory_workspace(self._workspace_root)
        ensure_defaults(self._workspace_root)   # 批 5 初始化模块：只补缺失
        self._target = target
        self._max_turns = max_turns
        self._changes = ChangeLog()
        self._engine: Engine | None = None
        self._tracer: Tracer | None = None
        self._lock = asyncio.Lock()
        # 用户插话（2026-08-24）：对局跟随的轮很长，用户要能随时插话 —— 队列经
        # AdvisorSpec 注入工具层（sleep 早醒 / 工具结果捎带）；这里同时记轮内流水，
        # 轮末按真实时序写进历史（原话 → 插话 → 回复）。
        self.interjections = InterjectionQueue()
        self._round_interjects: list[str] = []   # 本轮到达的插话（历史用）
        self._round_active = False
        self._engine_error: str | None = None
        #: UI 读的历史（重启即空 —— 完整记录在 trace 目录，那是审计载体）。
        #: history_path 给了就持久化（JSON）：重启后 UI 历史还在，且引擎首次启动时
        #: 把历史喂回 state.messages —— **LLM 也记得**（真·跨重启续聊）。
        self._history_path = Path(history_path) if history_path else None
        #: UI 读的历史
        self.history: list[dict] = self._load_history()
        self._seeded = False
        #: 跟随循环见过对局活着吗（`_game_alive_retry` 只在「活过又取不到」时重试）
        self._follow_seen_alive = False
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
        # 对局跟随在 _round_with_follow 里：每轮独立看门狗（一轮超时不再烧掉整场跟随）
        return await self._round_with_follow(text)

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
                # 跟随循环里每个引擎轮各自经 _schedule_round（带独立看门狗），
                # 这里不再整体套表 —— 否则一场 10 分钟的对局会被 600s 看门狗腰斩
                result = await self._round_with_follow(text, emit=emit)
            except Exception as exc:  # noqa: BLE001 —— 流面错误也要走完事件流（不挂死 SSE）
                result = {"error": f"{type(exc).__name__}: {exc}"}
            handle._finish(result)

        fut = asyncio.run_coroutine_threadsafe(runner(), self._loop)
        fut.add_done_callback(lambda _f: handle.events.put(None))
        return handle

    def _schedule_round(self, coro):
        """协程排上专属循环 + 挂看门狗。say 与 start_round 共用 —— 两条路都不许挂死。

        看门狗是**活性制**（2026-08-24 §0.57，max_turns 500 的配套）：`_round_activity`
        在流式事件（delta/tool/turn）到达时刷新 —— 只要有事件流动就续期，连续闲置
        `round_timeout` 才杀。绝对上限会把对局跟随的长轮（sleep 一次最多 300s、
        一轮合法跑几十分钟）误杀。非流式路径（emit=None，测试用）没有事件可刷，
        退化为原来的绝对上限语义。
        局限：协程卡在**同步阻塞**里时循环线程本身被占，看门狗无法送达（2026-08-23
        真机案例复盘：无网络连接、无事件、循环空转——那种 await 永不返回正是要防的）。
        """
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self._round_activity = time.monotonic()

        def _watchdog() -> None:
            if fut.done():
                return
            idle = time.monotonic() - self._round_activity
            if idle < self._round_timeout:
                # 还有活性：睡完剩余闲置额度再查一次
                timer = threading.Timer(self._round_timeout - idle, _watchdog)
                timer.daemon = True
                timer.start()
                return
            fut.cancel()    # 连续闲置到上限：取消会传播进协程的 await 点自行收尾

        timer = threading.Timer(self._round_timeout, _watchdog)
        timer.daemon = True
        fut.add_done_callback(lambda _f: timer.cancel())
        timer.start()
        return fut

    # ---- 对局跟随（2026-08-24）：轮末查会话，游戏没结束就提醒它继续 ----

    def _game_alive(self) -> tuple[bool, float]:
        """对局是否仍在进行（真机/沙盒子进程会话）。返回 (alive, game_time)。

        只认 driver=sim/sc2 且 alive 的会话；进程内沙盒（offline）不是对局。
        后端取不到 = 不跟随（如实，不猜）。
        """
        try:
            info = self._client.session()
        except Exception:               # noqa: BLE001 —— 跟随是增强，不是依赖
            return False, 0.0
        if not isinstance(info, dict):
            return False, 0.0
        if info.get("driver") not in ("sim", "sc2") or not info.get("alive"):
            return False, 0.0
        return info.get("state") in ("启动中", "对局中"), float(info.get("game_time") or 0.0)

    def _last_round_slept(self) -> bool:
        """上一轮有没有用 sleep（steps 里的 tool 名判断）——跟随状态诊断用。"""
        for m in reversed(self.history):
            if m.get("role") == "agent":
                return any(s.get("kind") == "tool" and s.get("tool") == "sleep"
                           for s in (m.get("steps") or []))
        return False

    async def _game_alive_retry(self) -> tuple[bool, float]:
        """`_game_alive` 的容错版：**之前还活着、现在突然取不到**时短重试再判死。

        SC2 启动/收尾窗口后端正忙，session 接口偶发取不到 ≠ 对局结束 —— 直接判死
        会让跟随在启动期中断（§0.52）。从未活过（普通聊天轮）不重试，不多等。
        """
        alive, gt = self._game_alive()
        if alive or not getattr(self, "_follow_seen_alive", False):
            return alive, gt
        for _ in range(2):
            await asyncio.sleep(1.0)
            alive, gt = self._game_alive()
            if alive:
                return alive, gt
        return alive, gt

    async def _round_with_follow(self, text: str, emit=None) -> dict:
        """跑一轮；对局还在进行就注入「跟随提醒」继续跑，直到游戏结束/轮数上限。

        每个引擎轮独立经 _schedule_round（独立看门狗）—— 一轮超时只结束跟随，
        不会把整场对话锁死。止损只有总轮数一道；纯文字轮不放弃（系统会一直提醒
        到它用 sleep 为止 —— 提醒轮带 nudge 标记，UI 上是系统条不是用户消息）。
        """
        result = await self._run_one_round(text, emit)
        nudges = 0
        for _ in range(FOLLOW_MAX_ROUNDS):
            if result.get("error"):
                break              # 看门狗/引擎错误：跟随终止，错误已显形
            # 未送达的插话（没赶上工具检查点，比如刚好在纯思考时插的）优先补送一轮
            undelivered = self.interjections.drain()
            if undelivered:
                result = await self._run_one_round(
                    "（用户插话：" + "／".join(undelivered) + "）——请回应", emit)
                continue
            alive, gt = await self._game_alive_retry()
            if not alive:
                break              # 对局结束（或没有对局）：正常收尾
            self._follow_seen_alive = True
            nudges += 1
            nudge = (f"（跟随提醒·第 {nudges} 次）对局仍在进行：t={gt:.0f}s，你的回合"
                     "不会结束 —— 这是系统要求，不是用户在说话。立刻用 "
                     "sleep(game_seconds=…) 等游戏推进，然后 observe 检查（队列/收入/"
                     "警报），必要时 propose 调整；对局结束后先做终局总结（结果 + "
                     "关键时间线 + 值得记的教训）再停。")
            result = await self._run_one_round(nudge, emit, nudge=True)
        return result

    async def _run_one_round(self, text: str, emit=None, *, nudge: bool = False) -> dict:
        """一个引擎轮（带独立看门狗）；超时/异常转错误字典（G7：显形不抛炸）。"""
        try:
            return await asyncio.wrap_future(
                self._schedule_round(
                    self._say_on_engine_loop(text, emit=emit, nudge=nudge)))
        except asyncio.CancelledError:
            return {"error": f"本轮超过 {self._round_timeout:g}s 未完成，"
                             "看门狗已终止（停滞的轮会锁死整条对话通道，宁可报错重来）"}

    # ---- 以下全部跑在专属循环上 ----

    async def _say_on_engine_loop(
        self, text: str, emit: Callable[[StreamEvent], None] | None = None,
        *, nudge: bool = False,
    ) -> dict:
        async with self._lock:
            engine, tracer = await self._ensure_engine()
            if engine is None:
                return {"error": self._engine_error}
            # turn_no 归零：见模块 docstring（终身累计 vs 每轮预算）
            agent = await engine.create_or_get("advisor", self._target, "")
            agent.state.turn_no = 0
            if not self._seeded:
                self._seed_history(agent)
            self._seeded = True
            events_before = len(tracer.read_events(self._target))
            self._round_mark = events_before
            messages_before = len(agent.state.messages)   # segments 只看本轮新增
            # 轮预算复位（每轮独立）：FakeLLM 等测试替身没有该协议 → 跳过（不设限）
            if hasattr(self._llm, "reset_round_budget"):
                self._llm.reset_round_budget(self._max_output_tokens)
            self.running_steps = []
            self._round_active = True
            self._round_interjects = []
            reply = None
            outcome = None
            round_t0 = time.monotonic()
            self._first_delta_ms = None
            try:
                if emit is None:
                    status = await engine.start(self._target, text)
                    outcome = getattr(status, "state", None)
                    reply = getattr(status, "result", None)
                else:
                    # 流式：start_stream 逐事件外发（delta/turn/tool），末条 run_end 带终态
                    async for ev in engine.start_stream(self._target, text):
                        emit(ev)
                        # 活性看门狗的戳：只要有事件流动（哪怕只是思考分片）就续期 ——
                        # 对局跟随的长轮不该被绝对墙钟误杀
                        self._round_activity = time.monotonic()
                        # 流式诊断（2026-08-24「token 流很慢」）：首个分片距发话多久 ——
                        # 大 = 端点不流式/思考服务端整段做/提示词过大（prefill 慢）
                        if self._first_delta_ms is None and ev.type == "delta":
                            self._first_delta_ms = int((time.monotonic() - round_t0) * 1000)
                        if ev.type == "run_end":
                            outcome = ev.outcome
                            reply = ev.result
            finally:
                self._round_mark = None
                self.running_steps = []
                self._round_active = False
            steps = self._round_steps(tracer, events_before)
            # 交错时间线（A 批，2026-08-24）：正文/思考/工具/插话按真实顺序全序列化 ——
            # 旧 {steps,text} 两段式渲染时工具间正文被吞（用户实测「轮完成瞬间清空正文」）
            segments = self._round_segments(
                tracer, events_before, messages_before, agent,
                self.interjections.take_drained())
            tracer.finalize_summary()
            if not reply:
                # 纯文本回复也算回复（聊天语义）—— 只有 result=None 且没有任何
                # assistant 文本（比如一上来就 done 且没带参数）才落到提示。
                for msg in reversed(agent.state.messages):
                    if msg.role == "assistant" and (msg.content or "").strip():
                        reply = msg.content.strip()
                        break
            reply = reply or "（这轮没有产出 —— 换个说法再试一次）"
            if outcome == "paused":
                # max_turns 截停（2026-08-24 用户报「工具调用之后没有下文」）：最后
                # 一轮常是纯工具调用（无正文），落史若无说明 = 用户侧凭空断线。
                # 附带下一步指引；模型侧 _seed_history 也会看到，知道自己是被截的。
                reply = (reply.rstrip() + "\n\n" if reply.strip() else "") + \
                    "（本轮达到轮数上限暂停，任务还没收尾 —— 回复「继续」接着做。）"
            # 轮用量统计 + 预算刹车说明（2026-08-24 用户拍板：turn 放开，token 做上限）
            round_tokens = int(getattr(self._llm, "round_output_tokens", 0) or 0)
            budget_hit = (self._max_output_tokens is not None and self._max_output_tokens > 0
                          and round_tokens >= self._max_output_tokens)
            if budget_hit:
                reply = (reply.rstrip() + "\n\n" if reply.strip() else "") + \
                    (f"（输出 token 预算 {self._max_output_tokens:,} 已用完"
                     f"（本轮累计 {round_tokens:,}）—— 回复「继续」会开新预算接着做。）")
            # 轮末改动清单：写钩子（规划文件）与 propose（对局域）攒的 ChangeRecord，
            # 随消息进历史 —— 前端把它渲染成可点击的跳转 chip（刷新后仍在）。
            round_changes = [c.to_json() for c in self._changes.drain()]
            self.history.append({"role": "user", "text": text, "at": time.time(),
                                 **({"nudge": True} if nudge else {})})
            # 本轮到达的插话按真实时序落在原话与回复之间（工具结果里模型已经见过）
            for note in self._round_interjects:
                self.history.append({"role": "user", "text": note, "at": time.time(),
                                     "interjection": True})
            self._round_interjects = []
            self.history.append({"role": "agent", "text": reply, "at": time.time(),
                                 **({"output_tokens": round_tokens} if round_tokens else {}),
                                 **({"segments": segments} if segments else {}),
                                 **({"steps": steps} if steps else {}),
                                 **({"changes": round_changes} if round_changes else {})})
            self._save_history()
            return {
                "reply": reply,
                "outcome": outcome,
                "changes": round_changes,
                "messages": self.history,
                **({"output_tokens": round_tokens} if round_tokens else {}),
            }

    def _seed_history(self, agent) -> None:  # noqa: ANN001 —— AgentInstance，避免引 vendor 内部类型
        """把历史喂回 state.messages —— LLM 侧也「记得」聊过什么；首轮（历史空）也种。

        runner 只在 messages 为空时自己加系统提示 —— 而 vendor 的 `build_system_prompt`
        会固定追加「完成任务后调用 done 提交结果」（done 已下线，§0.52 F 批），所以
        **每一轮都预种** system 头（本地组装），runner 的自组路径永远不触发。
        steps 不回灌（那是过程展示，不是对话内容）。
        """
        from agentic.types import Message

        role_map = {"user": "user", "agent": "assistant"}
        msgs = [Message("system", _system_prompt(agent))]
        for m in self.history:
            role = role_map.get(m.get("role"))
            text = str(m.get("text") or "").strip()
            if role and text:
                msgs.append(Message(role, text))
        agent.state.messages = msgs

    def interject(self, text: str) -> dict:
        """用户插话（轮内）：进队列（sleep 早醒 / 下一个工具结果捎带给模型）。

        没有进行中的轮 → queued=False（前端走正常发送即可）。轮间隙（跟随轮之间）
        的小窗口同样 False —— 前端重发一次就会排进下一轮。
        """
        text = (text or "").strip()
        if not text:
            return {"queued": False, "reason": "要说点什么（空消息）"}
        if not self._round_active:
            return {"queued": False, "reason": "当前没有进行中的轮 —— 直接正常发送即可"}
        self.interjections.add(text)
        self._round_interjects.append(text)
        return {"queued": True}

    async def clear_context(self) -> dict:
        """清空对话上下文（/clean，2026-08-24）：历史清零 + 引擎会话消息态重置。

        **记忆文件不动**（memory/、improvement-notes 等都在磁盘上，下一轮照常读）。
        用途：提示词/工具集改版多轮后，旧上下文里的过时认知（如"我没有 stop 工具"）
        会一直误导模型 —— 清掉从空白开始。跑在 _lock 内：跟轮串行，不清正在写的状态。
        """
        async with self._lock:
            self.history = []
            self._save_history()
            self._seeded = False
            if self._engine is not None:
                try:
                    # create_or_get(类型, target, 版本)：与 _say_on_engine_loop 同参（advisor 入口）
                    agent = await self._engine.create_or_get("advisor", self._target, "")
                    agent.state.messages = []   # runner 下一轮自己补系统提示
                    agent.state.turn_no = 0
                except Exception:               # noqa: BLE001 —— 引擎态重置失败不拦清历史
                    pass
            return {"ok": True, "messages": []}

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
        if pending_tool is not None:
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

    def _round_segments(self, tracer: Tracer, events_before: int,
                        messages_before: int, agent,             # noqa: ANN001 —— 避免引 vendor 内部类型
                        interjections: list[tuple[str, float]]) -> list[dict]:
        """本轮的**交错时间线**（A 批，2026-08-24）：正文/思考/工具/用户插话按真实
        顺序序列化进 history（segments 字段）。

        配对规则：trace 的每个 `llm_call` 事件对应本轮新增的一条 assistant 消息 ——
        先落该轮思考（reasoning_ref blob）再落正文（msg.content）；其后的 `tool_call`
        事件顺次落工具段。插话按排空时刻插到「正在跑的那个工具」之前（用户说话时
        该工具还在运行，工具完成事件的 ts ≥ 排空时刻）。独立插话条目（喂 LLM 用）
        仍照旧写入 history；前端渲染以 segments 为准。
        """
        from datetime import datetime

        assistant = [m for m in agent.state.messages[messages_before:]
                     if m.role == "assistant"]
        pending_user = list(interjections)
        segs: list[dict] = []
        ai = 0
        for ev in tracer.read_events(self._target)[events_before:]:
            et = ev.get("type")
            if et == "llm_call":
                ref = ev.get("reasoning_ref")
                if ref:
                    try:
                        text = (tracer.trace_dir / str(ref)).read_text(encoding="utf-8").strip()
                    except OSError:
                        text = ""
                    if text:
                        segs.append({"kind": "reasoning", "text": _clip_text(text, 2000)})
                msg = assistant[ai] if ai < len(assistant) else None
                ai += 1
                if msg is not None and (msg.content or "").strip():
                    segs.append({"kind": "text", "text": msg.content.strip()})
            elif et == "tool_call":
                ts = ev.get("ts")
                ts_epoch = None
                if isinstance(ts, str):
                    try:
                        ts_epoch = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        ts_epoch = None
                while pending_user and ts_epoch is not None and pending_user[0][1] <= ts_epoch:
                    segs.append({"kind": "user", "text": pending_user.pop(0)[0]})
                segs.append({
                    "kind": "tool",
                    "tool": ev.get("tool", "?"),
                    "args": _clip_text(json.dumps(ev.get("args", {}), ensure_ascii=False), 200),
                    "preview": _clip_text(str(ev.get("result_preview") or ""), 300),
                    "duration_ms": ev.get("duration_ms", 0),
                })
        # 没对上工具的插话（轮末才排空 / ts 缺失）如实放末尾
        for text, _ in pending_user:
            segs.append({"kind": "user", "text": text})
        return segs

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
        self._llm = llm
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
                              AdvisorSpec(self._client, changes=self._changes,
                                          pending=self.interjections,
                                          map_plans_dir=self._readonly_map_plans),
                              entry=True)
        self._engine_error = None
        return self._engine, self._tracer
