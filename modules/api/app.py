"""api.app：REST 静态面 + 帧源清单 + WS 帧流（B2）。

设计对着前端的 `FrameSource`（`plan-frontend.md` §4）：
- `GET /api/sources`              帧源清单（含中文标签、时间范围、topic 列表）
- `GET /api/sources/{id}/statics` 三个静态面（每局一次的东西不该走 WS）
- `GET /api/sources/{id}/jsonl`   整份 JSONL（复盘/夹具直接 fetch，就是现在的 `JsonlFrameSource`）
- `GET /api/schema`               不依赖任何会话的 flow 词表（编辑器可以先加载）
- `WS  /api/frames?source=&topics=&rate=`  按**游戏时间**节拍推帧 + 客户端控制

WS 的时间基准是 `game_time`（ADR-0025 §6：所有节拍对齐 game seq/time，不用多套漂移的墙钟定时器）。
`rate` = 每真实秒推进多少游戏秒；`rate=0` = 只在收到 `seek`/`play` 时动（给"拖时间线"用）。

**不做**：鉴权、多用户、HTTPS（localhost 单用户，写在计划的不做清单里）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from game.catalog import load_terran

from view.encode import envelope, to_json
from view.schema import REV, STATIC_TOPICS, TOPICS
from view.statics import schema_static
from view.adapt import projection_frame, session_frame
from view.alerts import AlertService
from view.observe import frames_by_topic, observation_packet
from view.projection import queue_to_ops
from view.proposals import ProposalStore, parse_item
from planner.opening import opening_game_state
from planner.planner import Planner

from api.commands import CommandResult, QueueCommand, WorkerCommand
from api.live import LiveSession
from api.session import OfflineSession, StaleObservation
from api.sources import SourceRegistry, SourceInfo

#: WS 的推送节拍（真实秒）。0.2s 一次 × rate 决定推进多少游戏秒。
TICK_SECONDS = 0.2
#: 默认帧源目录（夹具与录制都落这里）
DEFAULT_FRAME_DIR = Path("web/public/fixtures")
#: 离线沙盒会话的推进节拍（真实秒）。live 源按它 tick，前端就像看真对局。
SESSION_TICK = 0.25
#: 提案日志的**建议**落点（`tools/serve_api.py` 显式传它）。
#: **不放帧源目录**：那个目录是静态服务的（会被公开），而且提案是运行时数据、不该进版本库。
#: 注意 `create_app` 的默认是**不持久化**（见下）—— 默认落到一个共享文件会让每个
#: 不传路径的测试互相污染（实测踩过：手动跑的 35 条提案混进了单测）。
DEFAULT_PROPOSAL_LOG = Path("runtime/proposals.jsonl")
#: 规划文件的**建议**落点（同提案日志的姿态：runtime 数据不进版本库）。
#: `create_app` 默认**不持久化**（内存态）—— 理由同上：测试不传路径就不互污染。
DEFAULT_PLANS_DIR = Path("runtime/plans")
#: 地图规划文件的**建议**落点（同上）
DEFAULT_MAP_PLANS_DIR = Path("runtime/map-plans")


def create_app(frame_dir: Path | str | None = None,
               proposal_log: Path | str | None = None,
               plans_dir: Path | str | None = None,
               map_plans_dir: Path | str | None = None) -> FastAPI:
    registry = SourceRegistry(Path(frame_dir) if frame_dir else DEFAULT_FRAME_DIR)
    registry.load_labels_from_index()

    app = FastAPI(title="sc2Agent view API", version=str(REV))
    # CORS：驾驶舱是独立 vite dev server（localhost:5273），API 在 127.0.0.1:8770 ——
    # 跨源。没这个中间件浏览器会把 probe/命令/提案全部拦掉，前端永远显示「后端未连接」
    # （真机教训：start.bat 时代用 Node 脚本验证时同源没问题，浏览器一上就断）。
    # 开发驾驶舱只绑 127.0.0.1；来源收在 localhost/127.0.0.1 任意端口，不构成暴露面。
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.registry = registry
    #: 离线沙盒会话。惰性创建：没人访问 `source=live` 就不建，也就不烧 CPU。
    app.state.session = None
    app.state.session_task = None
    #: 上一份观察包的 seq —— ADR-0009 的"替换而非追加"：新包 supersedes 旧包
    app.state.last_observation_seq = None
    #: 提案存储（B7）。落盘在帧源目录旁边 —— 提案的价值一半在历史，追加日志天然保留。
    # 默认 path=None = 只在内存里（进程结束就没了）。持久化必须显式要求 ——
    # 默认持久化到共享文件会让测试互相污染，而且"哪些提案属于这次运行"变得说不清。
    app.state.proposals = ProposalStore(
        load_terran(), path=Path(proposal_log) if proposal_log else None)
    #: 规划存储（P0）：一个规划一个 YAML 文件；默认内存态，serve_api 显式传 runtime/plans。
    from view.plans import PlanStore
    app.state.plans = PlanStore(load_terran(), Path(plans_dir) if plans_dir else None)
    #: 地图规划文件（P2）：默认地图锁定 + 复制新建；默认内存态。
    from view.map_plans import MapPlanStore
    app.state.map_plans = MapPlanStore(
        Path(map_plans_dir) if map_plans_dir else None, catalog=load_terran())

    def _session() -> OfflineSession | None:
        """当前会话（可能为 None）。**不惰性创建**：以前 source=live 无人访问不建、
        一访问就顺手建一个进程内假世界 —— 假帧（出厂模板槽位 + 逐秒假数据）曾在
        前端被误读为「SC2 已连上但地图是空的」。无会话就是无会话，如实说。"""
        return app.state.session

    def _resolve(source_id: str):
        """帧源解析：`live` = 当前会话（**无会话时如实返回 None**，不再惰性建假世界 ——
        那个假世界曾在前端被误读成「SC2 已连上但地图数据是空的」）；其余 = 目录里的 JSONL。"""
        if source_id == "live":
            return _session()
        return registry.get(source_id)

    def _source(source_id: str):
        src = _resolve(source_id)
        if src is None:
            detail = ("没有活跃会话（先 POST /api/session/start）" if source_id == "live"
                      else f"没有帧源 {source_id!r}")
            raise HTTPException(status_code=404, detail=detail)
        return src

    async def _pump_session() -> None:
        """按 SESSION_TICK 推进**进程内**沙盒会话。子进程会话（B3）自己 tick，不归这里管。

        单线程 asyncio → 命令与 tick 天然互斥，不需要锁。
        """
        while True:
            await asyncio.sleep(SESSION_TICK)
            sess = app.state.session
            if sess is None:
                continue
            if not hasattr(sess, "tick"):
                continue          # LiveSession：子进程自己推进
            try:
                sess.tick()
            except Exception as exc:            # noqa: BLE001
                sess.state = "崩溃"
                sess.error = f"{type(exc).__name__}: {exc}"
                return

    def _ensure_pump() -> None:
        if app.state.session_task is None or app.state.session_task.done():
            app.state.session_task = asyncio.create_task(_pump_session())

    # ---- REST ----

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "rev": REV, "topics": list(TOPICS),
                "frame_dir": str(registry.root), "sources": registry.ids()}

    @app.get("/api/schema")
    def schema() -> dict:
        """flow 词表：不依赖任何会话，编辑器可以先加载（形状同 `static/schema` 的 payload）。"""
        return to_json(schema_static())

    @app.get("/api/sources")
    def sources() -> list[dict]:
        rows = [to_json(info) for info in registry.list()]
        sess = app.state.session
        if sess is not None:
            rows.insert(0, to_json(sess.info()))
        return rows

    # ---- 会话（B6；B3 之后这里换成真 live）----

    @app.get("/api/session")
    def session_info() -> dict:
        sess = app.state.session
        if sess is None:
            return {"state": "未连接", "detail": "沙盒会话未启动（POST /api/session/start）"}
        return sess.describe()

    @app.post("/api/session/start")
    async def session_start(autotick: bool = Query(True),
                            driver: str = Query("offline"),
                            map_plan: str | None = Query(None)) -> dict:
        """建会话。`driver`：`offline`（进程内假世界）/ `sim`（子进程假世界，验进程分离）/
        `sc2`（子进程真机）。`autotick=false` 时不自动推进（测试与单步调试用）。
        `map_plan`：地图规划 id —— 会话装配用它（进入游戏加载哪一份地图规划），
        缺省 = 手写出厂模板。

        必须是 `async def`：`asyncio.create_task` 需要运行中的事件循环，
        而 FastAPI 把同步 endpoint 丢到线程池里跑（那里没有 loop）。

        **幂等守卫（防多开 SC2）**：同 driver 且现有子进程会话还活着 → 直接返回现状，
        **不重启**。真机上一个会话就是一个 SC2 游戏进程，重复 start 会一个接一个地
        开游戏（且 V1 时代旧 SC2 不会被连带杀掉，全成桌面黑屏孤儿）。
        换 driver 才允许换会话：旧会话先 stop（含树杀），再起新的。
        """
        plan_path: str | None = None
        if map_plan:
            plan_path = app.state.map_plans.file_path(map_plan)
            if plan_path is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"地图规划 {map_plan!r} 不存在或没有落盘文件"
                           "（子进程会话需要真文件；检查 --map-plans 目录）")
        old = app.state.session
        if old is not None and hasattr(old, "proc"):
            alive = old.describe().get("alive")
            same_driver = getattr(old, "driver", None) == driver
            same_plan = getattr(old, "map_plan_path", None) == plan_path
            if alive and same_driver and same_plan:
                return old.describe()   # 已在同一种会话上：幂等返回，不多开
            old.stop()                 # 换驱动/换规划（或旧会话已死）：先收尾（树杀，防孤儿 SC2）
            app.state.session = None
        if driver in ("sim", "sc2"):
            sess = LiveSession(driver=driver, map_plan=plan_path)
            sess.map_plan_path = plan_path   # noqa: B010 —— 幂等守卫要读
            app.state.session = sess
            app.state.proposals.session = app.state.session
            return app.state.session.describe()
        sess = OfflineSession(load_terran(), map_plan=plan_path)
        app.state.session = sess
        sess.map_plan_path = plan_path      # noqa: B010
        # 提案要能算双投影、要能 apply → 必须认识会话；
        # 反过来会话的帧生产器要认识提案 → 提案变化时会发 `proposals` 帧
        app.state.proposals.session = sess
        sess.producer.proposals = app.state.proposals
        if autotick:
            _ensure_pump()
        return sess.describe()

    @app.post("/api/session/tick")
    async def session_tick(count: int = Query(1, ge=1, le=600)) -> dict:
        """手动推进 N 个游戏秒（`autotick=false` 时用；也是"单步调试"的入口）。"""
        sess = _live()
        for _ in range(count):
            sess.tick()
        return sess.describe()

    @app.post("/api/session/stop")
    async def session_stop() -> dict:
        task = app.state.session_task
        if task is not None:
            task.cancel()
        app.state.session_task = None
        sess = app.state.session
        if sess is not None:
            if hasattr(sess, "proc"):
                sess.stop()          # 子进程会话：真停
            else:
                sess.state = "已结束"
        app.state.session = None
        # 也要断开提案对会话的引用：否则停掉会话后新建的提案会基于**死会话**的世界
        # 算 anchor（拿到一个永远不会再变的 game_time），P5 的失效判断就失效了。
        app.state.proposals.session = None
        return {"state": "未连接"}

    # ---- 命令写入面（B6）：UI 与 agent 共用同一入口 ----

    def _live() -> OfflineSession:
        sess = app.state.session
        if sess is None:
            raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
        return sess

    def _guard(sess: OfflineSession, based_on_seq: int) -> None:
        try:
            sess.check_seq(based_on_seq)
        except StaleObservation as exc:
            # 409 而不是 400：这不是请求写错了，而是世界变了 —— 重取最新帧再试
            raise HTTPException(status_code=409, detail={
                "reason": str(exc), "based_on_seq": exc.based_on_seq,
                "current_seq": exc.current_seq,
            }) from None

    @app.post("/api/commands/queue/{op}", response_model=CommandResult)
    def queue_command(op: str, body: QueueCommand) -> CommandResult:
        sess = _live()
        _guard(sess, body.based_on_seq)
        try:
            detail = sess.queue_op(op, body.name, items=body.to_items(),
                                   index=body.index, order=body.order)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return CommandResult(ok=True, detail=detail, accepted_seq=sess.seq)

    @app.post("/api/commands/workers", response_model=CommandResult)
    def worker_command(body: WorkerCommand) -> CommandResult:
        sess = _live()
        _guard(sess, body.based_on_seq)
        try:
            detail = sess.set_worker_target(body.task, body.count)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return CommandResult(ok=True, detail=detail, accepted_seq=sess.seq)

    @app.get("/api/sources/{source_id}/statics")
    def statics(source_id: str) -> list[dict]:
        return _source(source_id).statics()

    @app.get("/api/sources/{source_id}/frames")
    def frames_at(source_id: str, game_time: float = Query(...)) -> list[dict]:
        """服务端 seek：每个 topic 给 `<= game_time` 的最后一帧（语义与前端 seek 逐字一致）。"""
        return _source(source_id).latest_at(game_time)

    @app.get("/api/sources/{source_id}/jsonl", response_class=PlainTextResponse)
    def jsonl(source_id: str) -> str:
        return _source(source_id).path.read_text(encoding="utf-8")

    # ---- agent 读面（B10）：ObservationPacket = 帧的投影，不是第二条摘要路径 ----

    @app.get("/api/observation")
    def observation(source: str = Query("live"), text: bool = Query(False)) -> dict:
        """当前观察包（ADR-0009）。`text=true` 额外给可直接进 prompt 的渲染文本。

        规则是**替换**而不是追加：每次取都是一份新的"当前事实"，旧的靠 `supersedes` 指向。
        `facts.based_on_seq` 是下命令时必须回填的东西（R8 的闭环）。
        """
        src = _source(source)
        info = src.info()
        frames = frames_by_topic(src.latest_at(info.to_time))
        prev = app.state.last_observation_seq
        packet = observation_packet(frames, catalog=load_terran(), supersedes=prev)
        app.state.last_observation_seq = packet.seq
        body = {
            "seq": packet.seq, "game_time": packet.game_time,
            "supersedes": packet.supersedes,
            "sections": packet.sections, "facts": packet.facts,
        }
        if text:
            body["text"] = packet.render()
        return body

    @app.get("/api/agent/tools")
    def agent_tools() -> dict:
        """agent 的**写面**清单：与 UI 完全同一套入口（决策 U7 / §6 P4）。

        刻意把"能做什么"和"为什么不能做"放在一起 —— agent 最容易犯的错是
        试一个不存在的动作，然后在错误里反复打转。
        """
        schema = to_json(schema_static())
        return {
            "commands": [
                {"method": "POST", "path": "/api/commands/queue/{op}",
                 "ops": ["submit", "append", "prepend", "clear", "remove", "reorder"],
                 "body": {"based_on_seq": "必填（取自观察包 facts.based_on_seq）",
                          "name": "队列名，默认 main",
                          "items": "submit/append/prepend 用",
                          "index": "remove 用", "order": "reorder 用（0..n-1 的排列）"},
                 "note": "队列 op 轻量、不走 validate/compile；执行时按 constraint 门控（S11）"},
                {"method": "POST", "path": "/api/commands/workers",
                 "body": {"based_on_seq": "必填", "task": "mineral|gas|idle",
                          "count": "**维持** N 个（目标值、幂等），不是再派 N 个"}},
                {"method": "POST", "path": "/api/proposals",
                 "body": {"kind": "production_queue（V1 只有这个能应用）",
                          "title_zh": "一句话", "rationale_zh": "**必填**：没有理由的提案不可接受",
                          "target": {"queue": "main"},
                          "hunks": "[{id, kind: insert|delete|modify|reorder, text_zh, payload}]"},
                 "note": "改变别人计划的事走提案通道，由用户审批；直接下命令只适合明确授权的操作"},
            ],
            "rules": [
                "所有命令必带 based_on_seq；落后超过阈值会返 409 并回报当前 seq —— 重取观察再试（R8）",
                "live 中不能创建/编辑模块与 Strategy（R5）",
                "flow 提交必须 validate + compile（R6）；生产队列 op 不需要",
                "不支持的东西会返 400 并带原因，别重试同一个动作",
            ],
            "unsupported": {
                "queue_ops": schema["queue"]["unsupported_ops"],
                "flow": schema["forbidden"],
            },
            "max_stale_seq": _live_max_stale(),
        }

    def _live_max_stale() -> int:
        from api.session import MAX_STALE_SEQ

        return MAX_STALE_SEQ

    # ---- 提案（B7）：agent 的唯一产出面 ----

    @app.get("/api/proposals")
    def proposals_list() -> list[dict]:
        return app.state.proposals.list()

    @app.post("/api/proposals")
    def proposals_create(body: dict) -> dict:
        """新建提案。**校验不通过也存**（§6 P2：不可接受，但必须可见 —— agent 要学、用户要诊断）。"""
        try:
            return app.state.proposals.create(body).to_json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/proposals/{pid}/preview")
    def proposals_preview(pid: str, horizon: float = Query(120.0, gt=0, le=600)) -> dict:
        """双投影：当前队列 vs 提案后的队列 —— 接受前先看未来。"""
        try:
            return app.state.proposals.preview_pair(pid, horizon=horizon)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/proposals/{pid}/accept")
    def proposals_accept(pid: str, body: dict | None = None) -> dict:
        payload = body or {}
        try:
            p = app.state.proposals.accept(
                pid, hunk_ids=payload.get("hunk_ids"), comment=payload.get("comment_zh"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            # 409：不是请求写错了，而是提案的状态/校验不允许接受
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return p.to_json()

    @app.post("/api/proposals/{pid}/reject")
    def proposals_reject(pid: str, body: dict) -> dict:
        try:
            p = app.state.proposals.reject(pid, str(body.get("comment_zh") or ""))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return p.to_json()

    # ---- 规划（P0）：离线生产规划的文件存储 + 无会话干跑 ----

    @app.get("/api/plans")
    def plans_list() -> list[dict]:
        return app.state.plans.list()

    @app.get("/api/plans/{pid}")
    def plans_get(pid: str) -> dict:
        p = app.state.plans.get(pid)
        if p is None:
            raise HTTPException(status_code=404, detail=f"没有规划 {pid!r}")
        return p

    @app.post("/api/plans")
    def plans_create(body: dict) -> dict:
        try:
            return app.state.plans.create(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/plans/{pid}")
    def plans_save(pid: str, body: dict) -> dict:
        try:
            return app.state.plans.save(pid, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/plans/{pid}")
    def plans_delete(pid: str) -> dict:
        try:
            app.state.plans.remove(pid)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有规划 {pid!r}") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"ok": True}

    @app.post("/api/plans/simulate")
    def plans_simulate(body: dict) -> dict:
        """离线干跑（P0）：标准开局种子 + 真 planner 投影 —— **不需要会话**。

        与提案预览（`/api/proposals/{id}/preview`，要会话当起点）互补：规划是
        authoring 数据，仿真只需要开局种子。前端与 agent 都走这里（A3：不本地算）。
        请求体 `{items: [{op,type,count,placement?,task?}], horizon?, plan_id?}`；
        返回 frame/projection 的 payload —— ProjectionBoard 直接渲染，
        stalled 事件即「前瞻警报」（缺矿/缺气/前置没……）。
        """
        try:
            horizon = min(600.0, max(1.0, float(body.get("horizon") or 300.0)))
            items = [parse_item(x) for x in (body.get("items") or [])]
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        catalog = load_terran()
        translated = queue_to_ops(items, catalog)
        curve = Planner(catalog).project(
            opening_game_state(catalog), list(translated.ops), horizon)
        frame = projection_frame(
            curve, based_on_seq=0, based_on_game_time=0.0, horizon=horizon,
            plan_id=str(body.get("plan_id") or "draft"), skipped=translated.skipped)
        # 前瞻警报与实时警报同一数据模型（AlertView）、同一渲染组件 —— 干跑无冷却
        alerts = AlertService(catalog).from_curve(curve)
        return {**to_json(frame), "alerts": to_json(alerts)}

    # ---- 地图规划文件（P2 切片 1）：默认地图锁定 + 复制新建 + 出生点读取 ----

    @app.get("/api/map-plans")
    def map_plans_list() -> list[dict]:
        return app.state.map_plans.list()

    @app.get("/api/map-plans/{pid}")
    def map_plans_payload(pid: str) -> dict:
        """该规划的 static/map 形状 payload（规划自带地图+出生点，画布直接渲染）。"""
        try:
            return app.state.map_plans.payload(pid)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/map-plans")
    def map_plans_create(body: dict) -> dict:
        try:
            return app.state.map_plans.create(body)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/map-plans/{pid}")
    def map_plans_save(pid: str, body: dict) -> dict:
        """离线保存：hunks 应用到该规划（与 map_plan 提案同一套校验，不走审批）。"""
        try:
            out = app.state.map_plans.save(pid, list(body.get("hunks") or []))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out)   # 结构化 errors 给前端/agent
        return out

    @app.delete("/api/map-plans/{pid}")
    def map_plans_delete(pid: str) -> dict:
        try:
            app.state.map_plans.remove(pid)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"ok": True}

    # ---- WS ----

    @app.websocket("/api/frames")
    async def frames_ws(
        ws: WebSocket,
        source: str = Query(...),
        topics: str | None = Query(None),
        rate: float = Query(4.0),
        start: float | None = Query(None),
    ) -> None:
        await ws.accept()
        is_live = source == "live"
        if is_live:
            # 无会话**不建假世界**（见 _session 注释）：握手照常，等会话启动后 pump 接上。
            _ensure_pump()
        src = _resolve(source)
        if src is None and not is_live:
            await ws.send_text(json.dumps(
                {"topic": "_error", "detail": f"没有帧源 {source!r}"}, ensure_ascii=False))
            await ws.close(code=1008)
            return

        wanted = {t.strip() for t in topics.split(",") if t.strip()} if topics else None
        info = src.info() if src is not None else SourceInfo(
            id="live", label="等待会话", kind="live", envelopes=0,
            from_time=0.0, to_time=0.0, topics=[], snapshots=[])
        cursor = info.from_time if start is None else max(info.from_time, min(info.to_time, start))
        playing = rate > 0

        # 握手：先告诉客户端契约版本与范围，再补上当前游标下的完整快照。
        # 顺序很重要：前端要先能判 rev 不匹配（红线 C8），再渲染。
        await ws.send_text(json.dumps({
            "topic": "_hello", "rev": REV, "source": source, "kind": info.kind,
            "from": info.from_time, "to": info.to_time, "rate": rate,
        }, ensure_ascii=False))
        if src is None:
            # live 无会话：合成一条 `frame/session`「未连接」—— 前端能立刻渲染真实状态
            # （顶栏「等待会话」），而不是一片空白里猜「是不是连上了」。
            await ws.send_text(json.dumps(envelope(
                "frame/session", 0, 0.0,
                session_frame("未连接", frame_source="live", game_time=0.0), 0),
                ensure_ascii=False))
        else:
            for frame in src.latest_at(cursor, wanted):
                await ws.send_text(json.dumps(frame, ensure_ascii=False))

        # live 源走「跟随」语义：新帧一律转发，不受 rate 限制（rate 是回放语义）。
        # 静态面走「快照出现即发」，不受 game_time 游标门控：真机首帧 game_time=0.0
        # （burnysc2 首个 on_step 的 bot.time 就是 0），而 below 的 between() 只转发
        # game_time > cursor 的帧 —— 前端在会话出帧前 attach（真机常态，开机要 1-2 分钟）
        # 时 cursor 从 0 起步，static/map 会永远落在游标后面，一次都发不出去
        # （实测症状：生产页有数据、地图页一直「等待 static/map」）。
        # sim 沙盒没暴露是因为 worldsim 先 t+=dt 再产帧，静态帧 game_time=1.0 能过过滤。
        sent_statics = 0
        #: 当前服务的会话对象 —— 换会话（含「无 → 有」）就重置游标与静态面进度，
        #: 新会话的全量快照由此补发；WS 连接不用重连（前端 attach 一次即可等会话）。
        cur_sess = src

        async def pump() -> None:
            nonlocal cursor, playing, info, sent_statics, cur_sess
            while True:
                await asyncio.sleep(TICK_SECONDS)
                if is_live:
                    sess = app.state.session
                    if sess is None:
                        cur_sess = None
                        continue
                    if sess is not cur_sess:
                        cur_sess = sess
                        sent_statics = 0
                        cursor = 0.0
                    info = sess.info()
                    statics = sess.statics() if hasattr(sess, "statics") else []
                    if len(statics) > sent_statics:
                        for frame in statics[sent_statics:]:
                            if wanted is None or frame["topic"] in wanted:
                                await ws.send_text(json.dumps(frame, ensure_ascii=False))
                        sent_statics = len(statics)
                    for frame in sess.between(cursor, info.to_time, wanted):
                        if frame.get("topic") in STATIC_TOPICS:
                            continue        # 静态帧由上面的快照通道发，不重复
                        await ws.send_text(json.dumps(frame, ensure_ascii=False))
                    cursor = max(cursor, info.to_time)
                    continue
                if not playing:
                    continue
                nxt = min(info.to_time, cursor + rate * TICK_SECONDS)
                if nxt <= cursor:
                    playing = False          # 播完就停（不刷屏；客户端可 seek 回去重播）
                    await ws.send_text(json.dumps(
                        {"topic": "_eof", "game_time": cursor}, ensure_ascii=False))
                    continue
                for frame in src.between(cursor, nxt, wanted):
                    await ws.send_text(json.dumps(frame, ensure_ascii=False))
                cursor = nxt

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                op = msg.get("op")
                if op == "seek":
                    cur = app.state.session if is_live else src
                    if cur is None:
                        continue
                    cursor = max(info.from_time, min(info.to_time, float(msg.get("game_time", 0))))
                    for frame in cur.latest_at(cursor, wanted):
                        await ws.send_text(json.dumps(frame, ensure_ascii=False))
                elif op == "play":
                    rate = float(msg.get("rate", rate)) or rate
                    playing = True
                elif op == "pause":
                    playing = False
                elif op == "ping":
                    await ws.send_text(json.dumps({"topic": "_pong"}, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()

    return app


#: 供 `uvicorn api.app:app` 直接用（frame_dir 取默认；开发起服用 `tools/serve_api.py`，
#: 那个壳会先把 `modules/` 塞进 sys.path）
app = create_app()