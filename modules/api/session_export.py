"""api.session_export：从活跃会话导出 initial-state + 剩余队列（PLAN-V2 批 3，I6）。

两种会话同一输出形态：
- 离线（进程内世界）：`derive_from(world.game_state())` → `state_to_doc`（最准）；
- live（子进程，父进程只有帧）：frame/world（经济+单位）join frame/economy（工人
  分任务 actual）+ frame/production（队列 uid/status）。

近似处如实标注：live 的 upgrades 为空表（runtime research 记账未建 —— 队列里
research 项本就 V1 不支持）；工人 building 数从在途建造订单推导不到帧级精度，
用 production.in_flight 数近似（= 正在盖的项数）。
"""
from __future__ import annotations


def export_snapshot(sess, catalog) -> dict:
    """会话 → {initial_state: doc, queue: [...(uid/status 带账)]}。"""
    if hasattr(sess, "proc"):          # LiveSession：优先子进程直出（清偿③），
        out = sess.export_via_subprocess()  # 有 GameState 的一侧算最准
        if out is not None and out.get("initial_state") is not None:
            return out
        return _export_live(sess, catalog)  # 回退：帧拼装（近似处见 docstring）
    from planner.initial_state import state_to_doc
    from planner.sim_state import derive_from

    st = derive_from(sess.world.game_state(), catalog)
    doc = state_to_doc(st, catalog)
    queue = _queue_rows_offline(sess)
    return {"initial_state": doc, "queue": queue,
            "game_time": round(float(sess.game_time), 1)}


def _queue_rows_offline(sess) -> list[dict]:
    q = sess.runtime.queue("main")
    items = list(q.items) if q else []
    return [{"op": it.op.value, "type": it.type, "count": it.count,
             "placement": None, "task": it.task.value if it.task else None,
             "uid": it.uid, "status": it.status, "reason": it.reason}
            for it in items]


def _latest_topic(sess, topic: str) -> dict | None:
    with sess._lock:
        frames = [f for f in sess.frames if f["topic"] == topic]
    return frames[-1]["payload"] if frames else None


def _export_live(sess, catalog) -> dict:
    world = _latest_topic(sess, "frame/world")
    econ = _latest_topic(sess, "frame/economy")
    prod = _latest_topic(sess, "frame/production")
    if world is None:
        return {"initial_state": None, "queue": [], "error": "还没有帧（会话刚启动）"}

    eco = world.get("economy") or {}
    buildings: dict[str, int] = {}
    units: dict[str, int] = {}
    worker_total = 0
    in_flight_builds = 0
    for u in world.get("units") or []:
        if u.get("owner") != "self":
            continue
        sid = u.get("stable_id") or ""
        entry = catalog.by_stable_id(sid)
        role = entry.role if entry is not None else None
        if role and role.value == "worker":
            worker_total += 1
        elif role and role.value == "building":
            if (u.get("build_progress") or 0) >= 1.0:
                buildings[sid] = buildings.get(sid, 0) + 1
            else:
                in_flight_builds += 1
        elif role and role.value == "combat":
            if (u.get("build_progress") or 0) >= 1.0:
                units[sid] = units.get(sid, 0) + 1
    # 工人分任务：frame/economy 的 actual（mineral/gas/idle 闭集）；
    # building 用 production.in_flight 数近似（正在盖的项数，帧级精度拿不到）
    tasks = {t.get("task"): int(t.get("actual") or 0) for t in (econ or {}).get("tasks") or []}
    mineral = tasks.get("mineral", 0)
    gas = tasks.get("gas", 0)
    idle = tasks.get("idle", 0)
    other = max(0, worker_total - mineral - gas - idle)
    doc = {
        "minerals": int(eco.get("minerals") or 0),
        "gas": int(eco.get("vespene") or 0),
        "supply_used": int(eco.get("supply_used") or 0),
        "supply_cap": int(eco.get("supply_cap") or 0),
        "workers": {"mineral": mineral, "gas": gas,
                    "building": in_flight_builds, "scouting": 0, "idle": idle},
        "buildings": buildings, "units": units,
        "upgrades": [],   # runtime research 记账未建（如实空表，见模块 docstring）
    }
    queue: list[dict] = []
    for q in (prod or {}).get("queues") or []:
        if q.get("name") != "main":
            continue
        for it in q.get("items") or []:
            if it.get("status") in ("completed",):
                continue    # 剩余队列：completed 是历史；skipped 保留（审计上下文）
            queue.append({"op": it.get("op"), "type": it.get("stable_id"),
                          "count": max(1, int(it.get("count") or 1)),
                          "placement": it.get("placement"),
                          "task": it.get("task"),
                          "uid": it.get("uid"), "status": it.get("status"),
                          "reason": it.get("reason")})
    return {"initial_state": doc, "queue": queue,
            "game_time": round(float(sess.game_time), 1),
            "note": "workers.building 为在途建造数近似；upgrades 需 research 记账（未建）"}
