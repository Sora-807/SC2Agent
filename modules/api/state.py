"""api.state：create_app 装配出的**运行时状态**的共享辅助（REFACTOR G1 拆分产物）。

状态本体挂在 `app.state` 的扁平键上（session / proposals / plans / …）—— 刻意不包一层
State 对象：测试与 serve_api 直接摸 `app.state.session` 等键，包一层会把它们全改一遍，
而"扁平键 + 无状态辅助函数"同样能消除 930 行闭包（辅助函数显式吃 `state`，不靠闭包捕获）。

这里的函数都是**纯协调**：不解析请求体、不构造响应 —— 那是 routes/ 各文件的事。
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException

from api.session import StaleObservation

#: 离线沙盒会话的推进节拍（真实秒）。live 源按它 tick，前端就像看真对局。
SESSION_TICK = 0.25


# ---- 沙盒泵 ----

async def pump_session(state) -> None:
    """按 SESSION_TICK 推进**进程内**沙盒会话。子进程会话（B3）自己 tick，不归这里管。

    单线程 asyncio → 命令与 tick 天然互斥，不需要锁。
    """
    while True:
        await asyncio.sleep(SESSION_TICK)
        sess = state.session
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


def ensure_pump(state) -> None:
    """保证沙盒泵在跑（会话启动 / WS attach live 时调用；重复调用无害）。"""
    if state.session_task is None or state.session_task.done():
        state.session_task = asyncio.create_task(pump_session(state))


# ---- 帧源解析 ----

def resolve(state, source_id: str):
    """帧源解析：`live` = 当前会话（**无会话时如实返回 None**，不惰性建假世界 ——
    那个假世界曾在前端被误读成「SC2 已连上但地图数据是空的」）；其余先查夹具
    目录（frame_dir registry），再回落**对局记录**目录（I39：录像回看——recordings
    与夹具同信封 JSONL 格式，JsonlSource 直接吃）。"""
    if source_id == "live":
        return state.session
    src = state.registry.get(source_id)
    if src is None:
        rec_registry = getattr(state, "recording_registry", None)
        if rec_registry is not None:
            src = rec_registry.get(source_id)
    return src


def require_source(state, source_id: str):
    src = resolve(state, source_id)
    if src is None:
        if source_id == "live":
            detail = "没有活跃会话（先 POST /api/session/start）"
        elif source_id.startswith("rec-"):
            # I39：录像 id 说清「没有录像」并列可用（此前统一的「没有帧源」让
            # agent 误判成会话问题）
            ids = (state.recording_registry.ids()
                   if getattr(state, "recording_registry", None) is not None else [])
            detail = (f"没有录像 {source_id!r}（可用：{', '.join(ids[:8]) or '无'}；"
                      "GET /api/recordings 看清单）")
        else:
            detail = f"没有帧源 {source_id!r}"
        raise HTTPException(status_code=404, detail=detail)
    return src


# ---- 命令面 ----

def live_session(state):
    """当前会话，无会话即 409（命令是"对活世界下命令"，没世界就是冲突态）。"""
    sess = state.session
    if sess is None:
        raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
    return sess


def guard_seq(sess, based_on_seq: int) -> None:
    """R8 新鲜度门：`based_on_seq` 落后太多 → 409 并回报当前 seq（客户端重取观察再试）。"""
    try:
        sess.check_seq(based_on_seq)
    except StaleObservation as exc:
        # 409 而不是 400：这不是请求写错了，而是世界变了 —— 重取最新帧再试
        raise HTTPException(status_code=409, detail={
            "reason": str(exc), "based_on_seq": exc.based_on_seq,
            "current_seq": exc.current_seq,
        }) from None
