"""会话（B6；B3 之后这里换成真 live）：启动 / 手动推进 / 停止。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from game.catalog import load_all

router = APIRouter()


@router.get("/api/session")
def session_info(request: Request) -> dict:
    sess = request.app.state.session
    if sess is None:
        return {"state": "未连接", "detail": "沙盒会话未启动（POST /api/session/start）"}
    return sess.describe()


@router.get("/api/session/export")
def session_export(request: Request, id: str | None = Query(None)) -> dict:
    """从活跃会话导出 initial-state + 剩余队列（PLAN-V2 批 3，I6）。

    `id` 给了 = 顺手存成 initial-states/<id>.yaml（可复用/可再喂 simulate）；
    不给 = 只返回（一次性，from_session 走的同一份逻辑）。
    """
    state = request.app.state
    sess = state.session
    if sess is None:
        raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
    from api.session_export import export_snapshot

    out = export_snapshot(sess, load_all())
    if out.get("initial_state") is None:
        raise HTTPException(status_code=409, detail=out.get("error", "导不出状态（还没有帧）"))
    if id:
        saved = state.initial_states.save(id, out["initial_state"], load_all())
        if not saved.get("ok"):
            raise HTTPException(status_code=400, detail=saved)
        out["saved_as"] = id
    return out


@router.post("/api/session/speed")
def session_speed(request: Request, multiplier: float = Query(...)) -> dict:
    """仿真模式变速（即时生效，不重启）：multiplier=0 → 不限速（最快）。

    正常模式没有变速通道（游戏按真实流速走）→ 409 带原因；进程内沙盒同理。
    """
    state = request.app.state
    sess = state.session
    if multiplier != 0 and not (1 <= multiplier <= 64):
        raise HTTPException(status_code=400,
                            detail=f"multiplier 只能是 0（不限速/最快）或 1..64 的倍数，当前 {multiplier!r}")
    if sess is None:
        raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
    if not hasattr(sess, "proc"):
        raise HTTPException(status_code=409,
                            detail="进程内沙盒会话没有变速通道；仿真变速只在游戏会话（mode=fast）")
    try:
        return sess.set_speed(multiplier)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/api/session/tick")
async def session_tick(request: Request, count: int = Query(1, ge=1, le=600)) -> dict:
    """手动推进 N 个游戏秒（`autotick=false` 时用；也是"单步调试"的入口）。"""
    state = request.app.state
    sess = state.session
    if sess is None:
        raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
    for _ in range(count):
        sess.tick()
    return sess.describe()


@router.post("/api/session/stop")
async def session_stop(request: Request) -> dict:
    state = request.app.state
    task = state.session_task
    if task is not None:
        task.cancel()
    state.session_task = None
    sess = state.session
    if sess is not None:
        if hasattr(sess, "proc"):
            sess.stop()          # 子进程会话：真停
        else:
            sess.state = "已结束"
    state.session = None
    # 也要断开提案对会话的引用：否则停掉会话后新建的提案会基于**死会话**的世界
    # 算 anchor（拿到一个永远不会再变的 game_time），P5 的失效判断就失效了。
    state.proposals.session = None
    return {"state": "未连接"}
