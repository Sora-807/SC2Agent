"""constraint.checks：生产可行性校验 (GameState, 意图) → ConstraintResult。

V1 门控项（docs/测试计划.md constraint 节；S4 只判当前可做性，不模拟未来）：
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


def check_build(gs: GameState, catalog: Catalog, stable_id: str, position: Point2) -> ConstraintResult:
    """build 可行性：类型存在 + 资源 + 前置 + 放置格点空闲。"""
    e = _entry(catalog, stable_id)
    if e is None:
        return ConstraintResult(False, (f"未知类型 {stable_id!r}",))
    reasons = check_resources(gs, e.cost) + check_prerequisites(gs, catalog, stable_id)
    cell = (int(position.x), int(position.y))
    for u in gs.units:  # V1 单格近似；footprint 闭区间（ADR-0027）后补
        if u.owner is Owner.SELF and (int(u.position.x), int(u.position.y)) == cell:
            reasons.append(f"格点 {cell} 已被 {u.type_name} 占据（tag={u.tag}）")
            break
    return ConstraintResult(not reasons, tuple(reasons))


def check_train(gs: GameState, catalog: Catalog, stable_id: str) -> ConstraintResult:
    """train 可行性：类型存在 + 资源 + 供给 + 就绪产出建筑。"""
    e = _entry(catalog, stable_id)
    if e is None:
        return ConstraintResult(False, (f"未知类型 {stable_id!r}",))
    reasons = check_resources(gs, e.cost)
    if gs.supply_used + e.cost.supply > gs.supply_cap:
        reasons.append(f"供给不足 {gs.supply_used}+{e.cost.supply}>{gs.supply_cap}")
    if e.produced_by and not _ready_self_buildings(gs, catalog, e.produced_by):
        reasons.append(f"缺就绪产出建筑 {e.produced_by}")
    return ConstraintResult(not reasons, tuple(reasons))


def check_assign_workers() -> ConstraintResult:
    """assign_workers 无资源门控（P0：立即发，饱和/溢出由 WorkerAllocator 处理）。"""
    return ConstraintResult(True)
