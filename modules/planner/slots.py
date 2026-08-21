"""planner.slots：产槽模型（B15）—— 科技槽 / 普通槽分开算。

背景（用户实测反馈）：旧 `_feasible` 只数「母建筑数 − busy」——techlab 母建筑被当成
普通产槽（坦克只需 factorytechlab 建过一次，之后普通工厂也能"造"坦克）、reactor 双倍
产能也只是粗略加到总数上。本模块把产槽拆成两类：

- **科技槽** = techlab 数（挂件 1:1 占用母建筑，如 terran/techlab → barracks）；
- **普通槽** = (母建筑数 − techlab − reactor) + reactor × 2（反应堆双倍，占用也 1:1）。

分配规则（用户拍板）：科技单位（前置含本产建筑的 techlab）只能用科技槽；普通单位用普通槽，
普通满后可溢出到空闲科技槽。聚合模型不追踪"哪座建筑在产什么"，reactor 优先由容量公式
隐式体现（反应堆兵营的 2 槽都在普通池里，普通单位天然先占满它们）。

判定全部从 catalog 推导（挂件条目 capabilities 含 "addon"、build_ability 区分反应堆/科技实验室），
不硬编码 stable_id。
"""
from __future__ import annotations

from game.catalog import Catalog, CatalogEntry


def reactor_map(catalog: Catalog) -> dict[str, str]:
    """母建筑稳定 ID → 反应挂件稳定 ID（如 barracks → terran/reactor）。"""
    return {
        e.produced_by: e.stable_id
        for e in catalog.where()
        if "addon" in e.capabilities
        and e.produced_by is not None
        and "BUILD_REACTOR" in (e.build_ability or "")
    }


def techlab_map(catalog: Catalog) -> dict[str, str]:
    """母建筑稳定 ID → 科技挂件稳定 ID（如 factory → terran/factorytechlab）。"""
    return {
        e.produced_by: e.stable_id
        for e in catalog.where()
        if "addon" in e.capabilities
        and e.produced_by is not None
        and "BUILD_TECHLAB" in (e.build_ability or "")
    }


def is_tech_unit(entry: CatalogEntry, catalog: Catalog) -> bool:
    """科技单位：前置里含「本产建筑的 techlab 挂件」（如坦克的 factorytechlab）。"""
    if entry.produced_by is None:
        return False
    techlab_sid = techlab_map(catalog).get(entry.produced_by)
    return techlab_sid is not None and techlab_sid in entry.prerequisites


def slot_capacity(
    producer: str,
    buildings: dict[str, int],
    reactor: dict[str, int],
    techlab_sid: str | None,
) -> tuple[int, int]:
    """(普通槽, 科技槽)。挂件 1:1 占用母建筑；反应堆让被占母建筑出 2 个普通槽。"""
    producers = buildings.get(producer, 0)
    tech = techlab_sid and buildings.get(techlab_sid, 0) or 0
    reac = reactor.get(producer, 0)
    # 数据异常兜底：挂件比母建筑还多时不出现负槽
    plain = max(0, producers - tech - reac)
    return plain + reac * 2, tech
