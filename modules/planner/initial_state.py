"""planner.initial_state：initial-state 文档 → SimState（PLAN-V2 批 3，I2）。

文档形态（与 initial-states/<id>.yaml 同构，内联对象同款）：
`{minerals, gas, supply_used, supply_cap, workers{mineral/gas/building/scouting/idle},
  buildings{类型:数量}, units{类型:数量}（不含 SCV）, upgrades[...]}`

校验（validate_state_doc → 中文错误列表，REST 400 / 工作区写共用）：
- catalog 类型存在（buildings/units/upgrades）；
- units 不含 SCV（工人在 workers 分项里）；
- workers 各分项之和 = SCV 总数（分项间不允许凭空多/少）；
- supply_cap 与建筑构成一致（econ.supply_provided 单源：CC=13/depot=8，min(200)）。

近似处（如实说明）：building/scouting 工人在 SimState 里并入 idle（仿真侧没有
编组/征用账本 —— 侦查是 D3 的 Group 派生，批 5 落显示面）。
"""
from __future__ import annotations

from planner.economy import DEFAULT_ECON
from planner.sim_state import SimState
from planner.slots import reactor_map
from game.catalog import Catalog, Role

#: 文档里工人分项的闭集（顺序 = 输出顺序：干活在前空闲在末尾）
WORKER_KEYS = ("mineral", "gas", "building", "scouting", "idle")


def validate_state_doc(doc: dict, catalog: Catalog) -> list[str]:
    """文档校验 → 中文错误列表（空 = 通过）。"""
    errs: list[str] = []
    workers = doc.get("workers") or {}
    for k, v in workers.items():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            errs.append(f"workers.{k} 必须是 ≥0 的整数（当前 {v!r}）")
    unknown = set(workers) - set(WORKER_KEYS)
    if unknown:
        errs.append(f"workers 不认识的键 {sorted(unknown)}（闭集 {list(WORKER_KEYS)}）")
    for area, types in (("buildings", doc.get("buildings") or {}),
                        ("units", doc.get("units") or {})):
        for sid, n in types.items():
            if catalog.by_stable_id(sid) is None:
                errs.append(f"{area} 里的类型 {sid!r} catalog 不认")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                errs.append(f"{area}.{sid} 必须是 ≥0 的整数（当前 {n!r}）")
    for sid in doc.get("upgrades") or []:
        if catalog.by_stable_id(sid) is None:
            errs.append(f"upgrades 里的 {sid!r} catalog 不认")
    for sid in (doc.get("units") or {}):
        entry = catalog.by_stable_id(sid)
        if entry is not None and entry.role == Role.WORKER:
            errs.append(f"units 不含 SCV（{sid} 属于 workers 分项，别两边都写）")
    # supply_cap 与建筑构成对账（单源 econ.supply_provided；SC2 上限 200）
    if "supply_cap" in doc and doc.get("supply_cap") is not None:
        computed = min(200, sum(DEFAULT_ECON.supply_provided.get(sid, 0) * n
                                for sid, n in (doc.get("buildings") or {}).items()))
        declared = doc.get("supply_cap")
        if isinstance(declared, int) and declared != computed:
            errs.append(f"supply_cap={declared} 与建筑构成不符（按 CC=13/depot=8 算应为 {computed}）")
    if "supply_used" in doc and doc.get("supply_used") is not None \
            and "supply_cap" in doc:
        used, cap = doc.get("supply_used"), doc.get("supply_cap")
        if isinstance(used, int) and isinstance(cap, int) and used > cap:
            errs.append(f"supply_used={used} > supply_cap={cap}（人口不能超上限）")
    return errs


def state_from_doc(doc: dict, catalog: Catalog) -> SimState:
    """文档 → SimState（先校验，坏文档 ValueError 带全部理由）。"""
    errs = validate_state_doc(doc, catalog)
    if errs:
        raise ValueError("；".join(errs))
    workers = doc.get("workers") or {}
    mineral = int(workers.get("mineral", 0))
    gas = int(workers.get("gas", 0))
    # building/scouting 并入 idle（近似：仿真无编组/征用账本，见模块 docstring）
    idle = int(workers.get("idle", 0)) + int(workers.get("building", 0)) \
        + int(workers.get("scouting", 0))
    buildings = {str(k): int(v) for k, v in (doc.get("buildings") or {}).items()}
    return SimState(
        t=0.0,
        minerals=float(doc.get("minerals", 0)),
        gas=float(doc.get("gas", 0)),
        supply_used=int(doc.get("supply_used", 0)),
        supply_cap=int(doc.get("supply_cap", 0)),
        total_workers=mineral + gas + idle,
        mineral_workers=mineral, gas_workers=gas, idle_workers=idle,
        buildings=buildings,
        units={str(k): int(v) for k, v in (doc.get("units") or {}).items()},
        in_flight=[],
        addons={
            parent: n
            for parent, sid in reactor_map(catalog).items()
            if (n := buildings.get(sid, 0)) > 0
        },
        research_completed={str(u) for u in (doc.get("upgrades") or [])},
    )


def state_to_doc(st, catalog: Catalog) -> dict:
    """SimState → initial-state 文档（export_snapshot 用：导出可再喂回 simulate）。

    近似处如实标注：building/scouting 工人在 SimState 无账（并入 idle 导出）；
    upgrades 只含仿真侧已完成的研发。
    """
    workers = {
        "mineral": st.mineral_workers, "gas": st.gas_workers,
        "building": sum(1 for f in st.in_flight if f.kind == "build"),
        "scouting": 0,
        "idle": st.idle_workers,
    }
    units = {sid: n for sid, n in st.units.items()
             if (e := catalog.by_stable_id(sid)) is None or e.role != Role.WORKER}
    return {
        "minerals": round(float(st.minerals), 1),
        "gas": round(float(st.gas), 1),
        "supply_used": int(st.supply_used), "supply_cap": int(st.supply_cap),
        "workers": workers,
        "buildings": dict(st.buildings),
        "units": units,
        "upgrades": sorted(st.research_completed),
    }


def static_check(items: list, catalog: Catalog, buildings: dict[str, int],
                 supply_cap: int = 0) -> list[dict]:
    """horizon=0 的静态体检（D2：simulate_plan(queue_name, horizon=0) = audit 路径）。

    不跑投影，只扫顺序/结构：前置不在场也不在更早队列（prereq_missing）、
    产出建筑缺失（同族）、累计人口超可用且队列无供给建筑（supply_overspend）。
    返回 alerts 列表（severity/kind/text_zh/uid），与 audit_queue 的诊断口径对齐。
    """
    from planner.economy import DEFAULT_ECON

    alerts: list[dict] = []
    queued_builds: dict[str, int] = {}
    planned_cap = 0
    used = 0.0
    for i, it in enumerate(items):
        uid = getattr(it, "uid", None) or f"#{i}"
        sid = getattr(it, "type", None)
        op = getattr(it, "op", None)
        op = op.value if hasattr(op, "value") else op
        entry = catalog.by_stable_id(sid) if sid else None
        if sid and entry is None:
            alerts.append({"severity": "error", "kind": "unknown_type",
                           "text_zh": f"{uid} {op} {sid}：catalog 不认 —— 先修类型名",
                           "uid": uid})
            continue
        if entry is None:
            continue
        zh = entry.display_name_zh
        for req in entry.prerequisites:
            if req == sid or req == entry.produced_by:
                continue  # 产出建筑缺失由下方 produced_by 分支报（同源不双报）
            if buildings.get(req, 0) + queued_builds.get(req, 0) < 1:
                alerts.append({
                    "severity": "error", "kind": "prereq_missing",
                    "text_zh": (f"{uid} {zh}：前置 {catalog.by_stable_id(req).display_name_zh if catalog.by_stable_id(req) else req}"
                                f" 不在场、队列更早处也没有 —— 在 {uid} 前插其建造项"),
                    "uid": uid})
        if op == "build":
            queued_builds[sid] = queued_builds.get(sid, 0) + max(1, int(it.count))
            planned_cap += DEFAULT_ECON.supply_provided.get(sid, 0) * max(1, int(it.count))
        elif op == "train":
            pb = entry.produced_by
            if pb and buildings.get(pb, 0) + queued_builds.get(pb, 0) < 1:
                alerts.append({
                    "severity": "error", "kind": "prereq_missing",
                    "text_zh": (f"{uid} {zh}：产出建筑"
                                f"{catalog.by_stable_id(pb).display_name_zh if catalog.by_stable_id(pb) else pb}"
                                f"不在场、队列里也没排 —— 先建它（该项会被 skip）"),
                    "uid": uid})
            used += entry.cost.supply * max(1, int(it.count))
            cap_total = supply_cap + planned_cap
            if supply_cap and used > cap_total and cap_total < 200:
                alerts.append({
                    "severity": "error", "kind": "supply_overspend",
                    "text_zh": (f"{uid} {zh}×{max(1, int(it.count))}：累计要人口 {used:.0f} >"
                                f" 可用 {cap_total:.0f} —— 在 {uid} 前插补给站（一座 +8）"),
                    "uid": uid})
    return alerts
