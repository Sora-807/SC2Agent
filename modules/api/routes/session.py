"""会话（B6；B3 之后这里换成真 live）：启动 / 手动推进 / 停止。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api.live import LiveSession
from api.state import ensure_pump
from game.catalog import load_all

router = APIRouter()


@router.get("/api/session")
def session_info(request: Request) -> dict:
    sess = request.app.state.session
    if sess is None:
        return {"state": "未连接", "detail": "沙盒会话未启动（POST /api/session/start）"}
    return sess.describe()


@router.post("/api/session/start")
async def session_start(request: Request,
                        autotick: bool = Query(True),
                        driver: str = Query("offline"),
                        map_plan: str | None = Query(None),
                        strategy: str | None = Query(None)) -> dict:
    """建会话。`driver`：`offline`（进程内假世界）/ `sim`（子进程假世界，验进程分离）/
    `sc2`（子进程真机）。`autotick=false` 时不自动推进（测试与单步调试用）。
    `map_plan`：地图规划 id —— 会话装配用它（进入游戏加载哪一份地图规划），
    缺省 = 手写出厂模板。
    `strategy`：策略文件 id（二十七轮「开放写策略」）—— 缺省 = 内置常量；
    热改不存在：正在跑的策略不受影响，换策略 = 用新 id 起新会话。

    必须是 `async def`：`asyncio.create_task` 需要运行中的事件循环，
    而 FastAPI 把同步 endpoint 丢到线程池里跑（那里没有 loop）。

    **幂等守卫（防多开 SC2）**：同 driver 且现有子进程会话还活着 → 直接返回现状，
    **不重启**。真机上一个会话就是一个 SC2 游戏进程，重复 start 会一个接一个地
    开游戏（且 V1 时代旧 SC2 不会被连带杀掉，全成桌面黑屏孤儿）。
    换 driver 才允许换会话：旧会话先 stop（含树杀），再起新的。
    """
    state = request.app.state
    plan_path: str | None = None
    if map_plan:
        plan_path = state.map_plans.file_path(map_plan)
        if plan_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"地图规划 {map_plan!r} 不存在或没有落盘文件"
                       "（子进程会话需要真文件；检查 --map-plans 目录）")
    strategy_path: str | None = None
    if strategy:
        strategy_path = state.strategies.file_path(strategy)
        if strategy_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"策略 {strategy!r} 不存在或没有落盘文件"
                       "（会话装配要真文件；检查 --strategies 目录）")
    old = state.session
    if old is not None and hasattr(old, "proc"):
        alive = old.describe().get("alive")
        same_driver = getattr(old, "driver", None) == driver
        same_plan = getattr(old, "map_plan_path", None) == plan_path
        same_strategy = getattr(old, "strategy_path", None) == strategy_path
        if alive and same_driver and same_plan and same_strategy:
            return old.describe()   # 已在同一种会话上：幂等返回，不多开
        old.stop()                 # 换驱动/换规划（或旧会话已死）：先收尾（树杀，防孤儿 SC2）
        state.session = None
    if driver in ("sim", "sc2"):
        sess = LiveSession(driver=driver, map_plan=plan_path,
                           strategy_path=strategy_path,
                           # 真机实时配速 + 对局记录（二十六轮两个用户 issue 的修点）
                           realtime=(driver == "sc2"),
                           record_dir=state.recordings_dir)
        sess.map_plan_path = plan_path   # noqa: B010 —— 幂等守卫要读
        sess.map_plan_id = map_plan      # noqa: B010 —— I8：限定引用「规划名/点位」要对着它核
        sess.strategy_path = strategy_path  # noqa: B010
        state.session = sess
        state.proposals.session = state.session
        return state.session.describe()
    from api.session import OfflineSession

    sess = OfflineSession(load_all(), map_plan=plan_path,
                          strategy_path=strategy_path)
    state.session = sess
    sess.map_plan_path = plan_path      # noqa: B010
    sess.map_plan_id = map_plan         # noqa: B010 —— 同上
    sess.strategy_path = strategy_path  # noqa: B010
    # 提案要能算双投影、要能 apply → 必须认识会话；
    # 反过来会话的帧生产器要认识提案 → 提案变化时会发 `proposals` 帧
    state.proposals.session = sess
    sess.producer.proposals = state.proposals
    if autotick:
        ensure_pump(state)
    return sess.describe()


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
