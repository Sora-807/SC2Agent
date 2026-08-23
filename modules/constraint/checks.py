"""constraint.checks：生产可行性校验 (GameState, 意图) → ConstraintResult。

V1 门控项（docs/contract/test-plan.md constraint 节；S4 只判当前可做性，不模拟未来）：
- build：资源（catalog cost）/ 前置（catalog prerequisites）/ 放置格点未被己方建筑占据
- train：资源 / 供给 / 存在就绪产出建筑
- assign_workers：无门控（P0：立即发）
research/cancel：待 upgrade 数据目录（cost/time/前置/科技挂件），生产运行时暂不支持（显式记录）。

规则来源 = game catalog（造价/时间/前置单点权威，改 JSON 不改代码）。
footprint 重叠为单格近似；ADR-0027 的 TL+BR 闭区间判定待 placement 阶段升级。
"""
from __future__ import annotations

from dataclasses import dataclass

from game import Cost, GameState, Owner, Point2
from game.catalog import Catalog
from tactical_map.placement import BuildSlot


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    """可行性结论（不可行时带结构化原因，供门控等待/告警展示）。"""

    ok: bool
    reasons: tuple[str, ...] = ()  # 不可行原因（资源缺额/前置/供给/放置等）


def _entry(catalog: Catalog, stable_id: str):
    return catalog.by_stable_id(stable_id)


def _ready_self_buildings(gs: GameState, catalog: Catalog, stable_id: str) -> list:
    """某类型就绪的己方建筑（build_progress == 1）。"""
    name = catalog.burnysc2_name_for(stable_id)
    if name is None:
        return []
    return [
        u for u in gs.units
        if u.owner is Owner.SELF and u.type_name == name and u.build_progress >= 1.0
    ]


def check_resources(gs: GameState, cost: Cost) -> list[str]:
    reasons: list[str] = []
    if gs.minerals < cost.minerals:
        reasons.append(f"晶体矿不足 {gs.minerals}<{cost.minerals}")
    if gs.vespene < cost.vespene:
        reasons.append(f"高能瓦斯不足 {gs.vespene}<{cost.vespene}")
    return reasons


def check_prerequisites(gs: GameState, catalog: Catalog, stable_id: str) -> list[str]:
    reasons: list[str] = []
    e = _entry(catalog, stable_id)
    if e is None:
        return reasons
    for req in e.prerequisites:
        if not _ready_self_buildings(gs, catalog, req):
            reasons.append(f"前置未满足 {req}")
    return reasons


def occupied_cells(gs: GameState, catalog: Catalog) -> set[tuple[int, int]]:
    """己方建筑占据的格点（完整 footprint：catalog size + 报告位置反推 TL；非建筑不占位）。

    换算公式见 tactical_map.placement（ADR-0027 §3，真机锁定）：TL = floor(R - size/2)。
    """
    cells: set[tuple[int, int]] = set()
    for u in gs.units:
        if u.owner is not Owner.SELF:
            continue
        entry = catalog.by_burnysc2_name(u.type_name)
        size = entry.size if entry is not None else None
        if size is None:
            continue
        tl = BuildSlot.tl_from_reported(u.position, size)
        cells |= {(x, y) for x in range(tl.x, tl.x + size) for y in range(tl.y, tl.y + size)}
    return cells


def _check_type_and_resources(gs: GameState, catalog: Catalog, stable_id: str):
    """公共门控：类型存在 + 资源 + 前置。返回 (entry | None, reasons)。"""
    e = _entry(catalog, stable_id)
    if e is None:
        return None, [f"未知类型 {stable_id!r}"]
    return e, check_resources(gs, e.cost) + check_prerequisites(gs, catalog, stable_id)


def check_build(gs: GameState, catalog: Catalog, stable_id: str, position: Point2) -> ConstraintResult:
    """build 可行性：类型存在 + 资源 + 前置 + 放置 footprint 空闲（锁定公式）。"""
    e, reasons = _check_type_and_resources(gs, catalog, stable_id)
    if e is None:
        return ConstraintResult(False, tuple(reasons))
    size = e.size
    if size is not None:
        tl = BuildSlot.tl_from_pos(position, size)
        placed = {(x, y) for x in range(tl.x, tl.x + size) for y in range(tl.y, tl.y + size)}
        clash = placed & occupied_cells(gs, catalog)
        if clash:
            reasons.append(f"放置 footprint 与己方建筑重叠 {sorted(clash)[:4]}")
    return ConstraintResult(not reasons, tuple(reasons))


def check_addon(gs: GameState, catalog: Catalog, stable_id: str) -> ConstraintResult:
    """挂件可行性：资源 + 前置（放置由母建筑吸附到右下 2×2，不走格点检查；
    母建筑是否空闲由生产运行时选）。"""
    e, reasons = _check_type_and_resources(gs, catalog, stable_id)
    return ConstraintResult(not reasons, tuple(reasons))


def check_gas(gs: GameState, catalog: Catalog, stable_id: str) -> ConstraintResult:
    """气矿建筑可行性：资源 + 前置（气井占用由生产运行时选井时判）。"""
    e, reasons = _check_type_and_resources(gs, catalog, stable_id)
    return ConstraintResult(not reasons, tuple(reasons))


def check_train(gs: GameState, catalog: Catalog, stable_id: str) -> ConstraintResult:
    """train 可行性：类型存在 + 资源 + 供给 + **前置** + 就绪产出建筑。

    前置必须查（P1）：坦克的 prerequisites 是 factorytechlab，而 produced_by 只是 factory ——
    只查产出建筑时"工厂就绪但没挂 techlab"会判可行 → 发单 → SC2 静默拒 → 队首被消费 →
    坦克订单永久蒸发且无任何记录。这是"不静默"红线在跨进程边界上的漏洞。
    """
    e = _entry(catalog, stable_id)
    if e is None:
        return ConstraintResult(False, (f"未知类型 {stable_id!r}",))
    reasons = check_resources(gs, e.cost)
    if gs.supply_used + e.cost.supply > gs.supply_cap:
        reasons.append(f"供给不足 {gs.supply_used}+{e.cost.supply}>{gs.supply_cap}")
    reasons.extend(check_prerequisites(gs, catalog, stable_id))
    if e.produced_by and not _ready_self_buildings(gs, catalog, e.produced_by):
        reasons.append(f"缺就绪产出建筑 {e.produced_by}")
    return ConstraintResult(not reasons, tuple(reasons))


def check_assign_workers() -> ConstraintResult:
    """assign_workers 无资源门控（P0：立即发，饱和/溢出由 WorkerAllocator 处理）。

    注：生产运行时不调它（自己直接处理 ASSIGN_WORKERS），目前只有单测在用 —— 属"被测试养着的死 API"，
    清理挂在 docs/REFACTOR.md §3 死代码清单（经济维持器已落地 ADR-0030）。
    """
    return ConstraintResult(True)
