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

from view.encode import to_json
from view.schema import REV, TOPICS
from view.statics import schema_static

from view.observe import frames_by_topic, observation_packet
from view.proposals import ProposalStore

from api.commands import CommandResult, QueueCommand, WorkerCommand
from api.session import OfflineSession, StaleObservation
from api.sources import SourceRegistry

#: WS 的推送节拍（真实秒）。0.2s 一次 × rate 决定推进多少游戏秒。
TICK_SECONDS = 0.2
#: 默认帧源目录（夹具与录制都落这里）
DEFAULT_FRAME_DIR = Path("web/public/fixtures")
#: 离线沙盒会话的推进节拍（真实秒）。live 源按它 tick，前端就像看真对局。
SESSION_TICK = 0.25
#: 提案日志的默认落点。**不放帧源目录**：那个目录是静态服务的（会被公开），
#: 而且提案是运行时数据、不该进版本库。
DEFAULT_PROPOSAL_LOG = Path("runtime/proposals.jsonl")


def create_app(frame_dir: Path | str | None = None,
               proposal_log: Path | str | None = None) -> FastAPI:
    registry = SourceRegistry(Path(frame_dir) if frame_dir else DEFAULT_FRAME_DIR)
    registry.load_labels_from_index()

    app = FastAPI(title="sc2Agent view API", version=str(REV))
    app.state.registry = registry
    #: 离线沙盒会话。惰性创建：没人访问 `source=live` 就不建，也就不烧 CPU。
    app.state.session = None
    app.state.session_task = None
    #: 上一份观察包的 seq —— ADR-0009 的"替换而非追加"：新包 supersedes 旧包
    app.state.last_observation_seq = None
    #: 提案存储（B7）。落盘在帧源目录旁边 —— 提案的价值一半在历史，追加日志天然保留。
    app.state.proposals = ProposalStore(
        load_terran(), path=Path(proposal_log) if proposal_log else DEFAULT_PROPOSAL_LOG)

    def _session(create: bool = True) -> OfflineSession | None:
        if app.state.session is None and create:
            app.state.session = OfflineSession(load_terran())
            # 提案要能算双投影、要能 apply → 必须认识会话；
            # 反过来会话的帧生产器要认识提案 → 提案变化时会发 `proposals` 帧
            app.state.proposals.session = app.state.session
            app.state.session.producer.proposals = app.state.proposals
        return app.state.session

    def _resolve(source_id: str):
        """帧源解析：`live` = 离线沙盒会话，其余 = 目录里的 JSONL。两者接口同形。"""
        if source_id == "live":
            return _session()
        return registry.get(source_id)

    def _source(source_id: str):
        src = _resolve(source_id)
        if src is None:
            raise HTTPException(status_code=404, detail=f"没有帧源 {source_id!r}")
        return src

    async def _pump_session() -> None:
        """按 SESSION_TICK 推进沙盒会话。单线程 asyncio → 命令与 tick 天然互斥，不需要锁。"""
        while True:
            await asyncio.sleep(SESSION_TICK)
            sess = app.state.session
            if sess is not None:
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
    async def session_start(autotick: bool = Query(True)) -> dict:
        """建会话。`autotick=false` 时不自动推进 —— 测试与"单步调试"需要手动 tick。

        必须是 `async def`：`asyncio.create_task` 需要运行中的事件循环，
        而 FastAPI 把同步 endpoint 丢到线程池里跑（那里没有 loop）。
        """
        sess = _session()
        assert sess is not None
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
        if source == "live":
            _session()
            _ensure_pump()
        src = _resolve(source)
        if src is None:
            await ws.send_text(json.dumps(
                {"topic": "_error", "detail": f"没有帧源 {source!r}"}, ensure_ascii=False))
            await ws.close(code=1008)
            return

        wanted = {t.strip() for t in topics.split(",") if t.strip()} if topics else None
        info = src.info()
        cursor = info.from_time if start is None else max(info.from_time, min(info.to_time, start))
        playing = rate > 0

        # 握手：先告诉客户端契约版本与范围，再补上当前游标下的完整快照。
        # 顺序很重要：前端要先能判 rev 不匹配（红线 C8），再渲染。
        await ws.send_text(json.dumps({
            "topic": "_hello", "rev": REV, "source": source, "kind": info.kind,
            "from": info.from_time, "to": info.to_time, "rate": rate,
        }, ensure_ascii=False))
        for frame in src.latest_at(cursor, wanted):
            await ws.send_text(json.dumps(frame, ensure_ascii=False))

        live = info.kind == "live"

        async def pump() -> None:
            nonlocal cursor, playing, info
            while True:
                await asyncio.sleep(TICK_SECONDS)
                if live:
                    # live 源**跟随**：新帧一律转发，不受 rate 限制（rate 是回放语义）
                    info = src.info()
                    for frame in src.between(cursor, info.to_time, wanted):
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
                    cursor = max(info.from_time, min(info.to_time, float(msg.get("game_time", 0))))
                    for frame in src.latest_at(cursor, wanted):
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