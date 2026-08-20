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

from view.encode import to_json
from view.schema import REV, TOPICS
from view.statics import schema_static

from api.sources import SourceRegistry

#: WS 的推送节拍（真实秒）。0.2s 一次 × rate 决定推进多少游戏秒。
TICK_SECONDS = 0.2
#: 默认帧源目录（夹具与录制都落这里）
DEFAULT_FRAME_DIR = Path("web/public/fixtures")


def create_app(frame_dir: Path | str | None = None) -> FastAPI:
    registry = SourceRegistry(Path(frame_dir) if frame_dir else DEFAULT_FRAME_DIR)
    registry.load_labels_from_index()

    app = FastAPI(title="sc2Agent view API", version=str(REV))
    app.state.registry = registry

    def _source(source_id: str):
        src = registry.get(source_id)
        if src is None:
            raise HTTPException(status_code=404, detail=f"没有帧源 {source_id!r}")
        return src

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
        return [to_json(info) for info in registry.list()]

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
        src = registry.get(source)
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

        async def pump() -> None:
            nonlocal cursor, playing
            while True:
                await asyncio.sleep(TICK_SECONDS)
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
