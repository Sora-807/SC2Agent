"""WS 帧流（B2）：按**游戏时间**节拍推帧 + 客户端控制（seek/play/pause/ping）。

WS 的时间基准是 `game_time`（ADR-0025 §6：所有节拍对齐 game seq/time，不用多套漂移的
墙钟定时器）。`rate` = 每真实秒推进多少游戏秒；`rate=0` = 只在收到 `seek`/`play` 时动
（给"拖时间线"用）。

pump 的 inner 函数带着每连接状态（cursor/playing/静态面进度/会话换轨检测），与下面的
receive 循环经 nonlocal 共享 —— 抽成类反而要把这套共享状态搬两遍，留在本文件里最直白。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.sources import SourceInfo
from api.state import ensure_pump, resolve
from view.adapt import session_frame
from view.encode import envelope
from view.schema import REV, STATIC_TOPICS

router = APIRouter()

#: WS 的推送节拍（真实秒）。0.2s 一次 × rate 决定推进多少游戏秒。
TICK_SECONDS = 0.2


@router.websocket("/api/frames")
async def frames_ws(
    ws: WebSocket,
    source: str = Query(...),
    topics: str | None = Query(None),
    rate: float = Query(4.0),
    start: float | None = Query(None),
) -> None:
    await ws.accept()
    state = ws.app.state
    is_live = source == "live"
    if is_live:
        # 无会话**不建假世界**（见 api.state.resolve 注释）：握手照常，等会话启动后 pump 接上。
        ensure_pump(state)
    src = resolve(state, source)
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
                sess = state.session
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
                cur = state.session if is_live else src
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
