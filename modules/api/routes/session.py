"""会话（B6；B3 之后这里换成真 live）：启动 / 手动推进 / 停止。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from api.live import LiveSession
from api.state import ensure_pump
from game.catalog import load_all

router = APIRouter()


class _StartBody(BaseModel):
    """start 的可选请求体：query 放不下结构化参数（§0.52 C 批）。"""

    production: dict[str, int] | None = None


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
                        strategy: str | None = Query(None),
                        spawn: str | None = Query(None),
                        loadout: str | None = Query(None),
                        mode: str | None = Query(None),
                        speed: float = Query(0.0),
                        body: _StartBody | None = None) -> dict:
    """建会话。`driver`：`offline`（进程内假世界）/ `sim`（子进程假世界，验进程分离）/
    `sc2`（子进程真机）。`autotick=false` 时不自动推进（测试与单步调试用）。
    `map_plan`：地图规划 id —— 会话装配用它（进入游戏加载哪一份地图规划），
    缺省 = 手写出厂模板。
    `strategy`：策略文件 id（二十七轮「开放写策略」）—— 缺省 = 内置常量；
    热改不存在：正在跑的策略不受影响，换策略 = 用新 id 起新会话。
    `loadout`（B1）：装配清单 id（`runtime/loadouts/<id>.yaml`，三件套引用）——
    给了它就以它为底，显式 query 参数（map_plan/strategy/spawn）覆盖同名字段；
    它的 `plan` 引用的生产规划**自动 submit 入队**（main 队列，帧边界生效）。
    `mode`（「开启游戏」两模式，2026-08-23，仅 driver=sc2）：`normal` = 正常模式
    （玩家可见、实时流速）；`fast` = 仿真模式（快进跑完看实际游戏结果，配 `speed`）。
    `driver` 的 offline/sim 是**测试驱动**（假世界），不走 mode 这套。
    `production`（请求体，§0.52 C 批）：开局生产力默认值 —— 采集配额目标值
    `{"mineral_workers": 8, "gas_workers": 3}`（简写 mineral/gas、reserve_idle 也认），
    会话建立后立即下发（`set_worker_target`，幂等目标值语义）。

    必须是 `async def`：`asyncio.create_task` 需要运行中的事件循环，
    而 FastAPI 把同步 endpoint 丢到线程池里跑（那里没有 loop）。

    **幂等守卫（防多开 SC2）**：同 driver 且现有子进程会话还活着 → 直接返回现状，
    **不重启**。真机上一个会话就是一个 SC2 游戏进程，重复 start 会一个接一个地
    开游戏（且 V1 时代旧 SC2 不会被连带杀掉，全成桌面黑屏孤儿）。
    换 driver 才允许换会话：旧会话先 stop（含树杀），再起新的。
    """
    state = request.app.state
    production_pairs = _production_pairs((body.production if body else None) or {})
    # 「开启游戏」两模式参数校验（不静默：错配直接 400 说清楚）
    if mode is not None and mode not in ("normal", "fast"):
        raise HTTPException(status_code=400,
                            detail=f"mode 只能是 normal（正常，实时流速）或 fast（仿真，快进），当前 {mode!r}")
    if speed != 0 and not (1 <= speed <= 64):
        raise HTTPException(status_code=400,
                            detail=f"speed 只能是 0（不限速/最快）或 1..64 的倍数，当前 {speed!r}")
    if mode == "normal" and speed:
        raise HTTPException(status_code=400,
                            detail="正常模式按实时流速跑；倍数（speed）属于仿真模式（mode=fast）")
    if mode is not None and driver != "sc2":
        raise HTTPException(status_code=400,
                            detail="mode（正常/仿真）只适用于开启游戏（driver=sc2）；"
                                   "offline/sim 是测试驱动（假世界），没有模式概念")
    # sc2 缺省 = 正常模式（此前的默认行为：真机实时配速）
    mode_resolved = mode or ("normal" if driver == "sc2" else None)
    speed_resolved = float(speed) if mode_resolved == "fast" else 0.0
    if loadout:
        lo = state.loadouts.get(loadout)
        if lo is None:
            raise HTTPException(
                status_code=400,
                detail=f"装配清单 {loadout!r} 不存在（可用：{state.loadouts.ids() or '无'}）")
        map_plan = map_plan or lo.get("map_plan")
        strategy = strategy or lo.get("strategy")
        spawn = spawn or lo.get("spawn")
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
    queue_items: list | None = None
    if loadout:
        plan_id = (state.loadouts.get(loadout) or {}).get("plan")
        if plan_id:
            queue_items = _loadout_queue_items(state, plan_id, map_plan)
    old = state.session
    if old is not None and hasattr(old, "proc"):
        alive = old.describe().get("alive")
        same_driver = getattr(old, "driver", None) == driver
        same_plan = getattr(old, "map_plan_path", None) == plan_path
        same_strategy = getattr(old, "strategy_path", None) == strategy_path
        # 模式/起始倍速也是会话身份的一部分（换模式 = 换一种会话，重启；
        # 运行中调倍数走 POST /api/session/speed，不重启）
        same_mode = getattr(old, "session_mode", None) == mode_resolved
        same_speed = getattr(old, "session_speed", None) == speed_resolved
        if alive and same_driver and same_plan and same_strategy and same_mode and same_speed:
            return old.describe()   # 已在同一种会话上：幂等返回，不多开
        old.stop()                 # 换驱动/换规划（或旧会话已死）：先收尾（树杀，防孤儿 SC2）
        state.session = None
    if driver in ("sim", "sc2"):
        sess = LiveSession(driver=driver, map_plan=plan_path,
                           strategy_path=strategy_path, spawn=spawn,
                           # 「开启游戏」两模式：normal=实时（此前默认）；fast=仿真快进 + 倍速
                           realtime=(driver == "sc2" and mode_resolved != "fast"),
                           speed=speed_resolved,
                           record_dir=state.recordings_dir)
        sess.map_plan_path = plan_path   # noqa: B010 —— 幂等守卫要读
        sess.map_plan_id = map_plan      # noqa: B010 —— I8：限定引用「规划名/点位」要对着它核
        sess.strategy_path = strategy_path  # noqa: B010
        sess.session_mode = mode_resolved   # noqa: B010 —— 幂等守卫要读（normal/fast）
        sess.session_speed = speed_resolved  # noqa: B010
        state.session = sess
        state.proposals.session = state.session
        if queue_items is not None:
            # B1：loadout 的生产序列自动入队 —— 命令走子进程通道（stdin/控制文件），
            # 帧边界生效；失败要让调用方看见（400），不能静默空队列开局
            try:
                sess.queue_op("submit", "main", items=queue_items)
            except (RuntimeError, OSError, ValueError) as exc:
                raise HTTPException(status_code=400,
                                    detail=f"loadout 生产序列入队失败：{exc}") from None
        for task, count in production_pairs:
            sess.set_worker_target(task, count)
        return state.session.describe()
    from api.session import OfflineSession

    sess = OfflineSession(load_all(), map_plan=plan_path,
                          strategy_path=strategy_path, spawn=spawn)
    state.session = sess
    sess.map_plan_path = plan_path      # noqa: B010
    sess.map_plan_id = map_plan         # noqa: B010 —— 同上
    sess.strategy_path = strategy_path  # noqa: B010
    if queue_items is not None:
        sess.queue_op("submit", "main", items=queue_items)
    for task, count in production_pairs:
        sess.set_worker_target(task, count)
    # 提案要能算双投影、要能 apply → 必须认识会话；
    # 反过来会话的帧生产器要认识提案 → 提案变化时会发 `proposals` 帧
    state.proposals.session = sess
    sess.producer.proposals = state.proposals
    if autotick:
        ensure_pump(state)
    return sess.describe()


def _production_pairs(production: dict) -> list[tuple[str, int]]:
    """开局生产力默认值 → `[(task, count)]`（先于建会话校验，坏参数不留半配会话）。

    键认全称 `mineral_workers`/`gas_workers`/`reserve_idle` 与简写 `mineral`/`gas`/`idle`
    （同一目标值语义，economy.set_target 的三种 task）。
    """
    alias = {"mineral_workers": "mineral", "gas_workers": "gas", "reserve_idle": "idle",
             "mineral": "mineral", "gas": "gas", "idle": "idle"}
    out: list[tuple[str, int]] = []
    for key, value in production.items():
        task = alias.get(str(key))
        if task is None:
            raise HTTPException(
                status_code=400,
                detail=f"production 键只能是 {'/'.join(sorted(set(alias)))}，当前 {key!r}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HTTPException(
                status_code=400,
                detail=f"production.{key} 必须是 ≥0 的整数（目标值语义），当前 {value!r}")
        out.append((task, int(value)))
    return out


def _loadout_queue_items(state, plan_id: str, map_plan_id: str | None) -> list:
    """loadout.plan 引用的生产规划 → QueueItem 列表（含 I8 限定引用解析）。"""
    from fastapi import HTTPException as _HTTP

    from view.plans import resolve_placement_refs
    from view.proposals import parse_item

    doc = state.plans.get(plan_id)
    if doc is None:
        raise _HTTP(status_code=400,
                    detail=f"loadout 引用的生产规划 {plan_id!r} 不存在（可用："
                           f"{[p['id'] for p in state.plans.list()]}）")
    items = [parse_item(i) for i in (doc.get("queue") or [])]
    items, ref_err = resolve_placement_refs(items, map_plan_id)
    if ref_err is not None:
        raise _HTTP(status_code=400, detail=f"loadout 生产序列：{ref_err}")
    return items


@router.post("/api/session/swap")
def session_swap(request: Request, strategy: str = Query(...)) -> dict:
    """热切 V1（批 C）：对**运行中的会话**（含 live）换策略，帧边界生效。

    约束（不满足 → 409 带原因，会话不受影响）：
    - 新策略文件编译通过（编译红在这里就 400）；
    - 新策略 `group_slots` 与当前装配一致（组结构/绑定装配期固定，热切不碰装配）。
    续位：新策略含同名 active_step → 停留（locals/timers 保留）；不含 → 从 initial_step 起。
    转移历史记 `swap` 事件（from 旧策略@版本 → to 新策略，续位或重起），observe 可见。
    """
    state = request.app.state
    sess = state.session
    if sess is None:
        raise HTTPException(status_code=409, detail="没有运行中的会话（先 POST /api/session/start）")
    if hasattr(sess, "proc") and not sess.describe().get("alive"):
        raise HTTPException(status_code=409, detail="会话子进程已退出，换不了策略（重开会话）")
    path = state.strategies.file_path(strategy)
    if path is None:
        raise HTTPException(status_code=400,
                            detail=f"策略 {strategy!r} 不存在或没有落盘文件（可用："
                                   f"{[r['id'] for r in state.strategies.list()]}）")
    from pathlib import Path as _P

    from api.session import DEFAULT_STRATEGY
    from flow.manifest import parse_strategy
    from view.strategies import load_strategy_file

    try:
        manifest, _asm = load_strategy_file(_P(path))
    except Exception as exc:                     # noqa: BLE001 —— 编译红原样给调用方
        raise HTTPException(status_code=400, detail=f"策略 {strategy!r} 编译失败：{exc}") from None
    # 当前装配的 slots：offline 有 manifest；live 父进程只有帧 —— 从策略文件/内置常量取
    if hasattr(sess, "proc"):
        current_path = getattr(sess, "strategy_path", None)
        current = (load_strategy_file(_P(current_path))[0] if current_path
                   else parse_strategy(DEFAULT_STRATEGY))
    else:
        current = sess.manifest
    if set(manifest.group_slots) != set(current.group_slots):
        raise HTTPException(
            status_code=409,
            detail=f"新策略 {manifest.id!r} 的 group_slots {manifest.group_slots} 与当前装配"
                   f" {current.group_slots} 不一致 —— 热切不碰装配（组结构/绑定装配期固定）；"
                   "要换编组就重开会话")
    if hasattr(sess, "proc"):
        out = sess.swap_strategy(str(path))      # 子进程通道（stdin / 控制文件）
        return {**sess.describe(), "swap": out}
    out = sess.swap_strategy(manifest)           # offline：pending，下一帧边界应用
    return {**sess.describe(), "swap": out}


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
