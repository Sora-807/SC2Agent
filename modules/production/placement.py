"""production.placement：placement 标记 → 放置点（REFACTOR G2 从 runtime 抽出）。

纯函数（region_layer/catalog/in_flight 全部显式入参，无隐藏状态）：
ADR-0029/0027 的 V1 近似解析。从 runtime 抽出是因为它是**可独立测的决策块**
（槽位类别过滤 / 尺寸过滤 / 在途预留），而 runtime 的编排逻辑不关心这些细节。
"""
from __future__ import annotations

from game import GameState, Point2, QueueItem
from game.catalog import Catalog
from game.production import PlacementExact, PlacementInRegion
from constraint.checks import occupied_cells
from tactical_map.placement import BuildSlot


def resolve_placement(
    region_layer,
    catalog: Catalog,
    in_flight: dict[str, list[dict]],
    head: QueueItem,
    gs: GameState,
    attempted: frozenset[str] = frozenset(),
) -> tuple[Point2 | None, str | None, str | None]:
    """placement 标记 → 放置点（跳过 attempted 里已失败的候选位）。

    返回 (point, slot_name, None) = 解析成功；
    (None, slot_name, reason) = 作者错误（丢弃并继续，不阻塞整队）；
    (None, None, None) = 瞬时无解（slot 全被占/全被尝试过）→ 阻塞等待或重试。
    """
    layer = region_layer
    if layer is None:
        return (None, None, "build 缺 region_layer（placement 解析需要区域层）")
    placement = head.placement
    if placement is None:
        # ADR-0027 修订（PLAN-V2 批 2）：placement null = **自动放置** ——
        # 默认图层（home 区 = 默认规划的裸名槽位表）按声明序找空位；
        # 无位可放 = placement_collision skip（批 1 语义），不是作者错误。
        placement = PlacementInRegion("home")
    if isinstance(placement, PlacementExact):
        if placement.mark in attempted:
            return (None, None, None)  # 唯一候选已失败
        pm = layer.pos_marks.get(placement.mark)
        if pm is not None:
            return (pm.pos, placement.mark, None)
        bs = layer.build_slots.get(placement.mark)
        if bs is not None:
            return (slot_point(bs), placement.mark, None)
        return (None, None, f"placement 标记 {placement.mark!r} 未登记")
    if isinstance(placement, PlacementInRegion):
        r = layer.regions.get(placement.region)
        if r is None:
            return (None, None, f"区域 {placement.region!r} 未登记")
        slots = [layer.build_slots[n] for n in r.build_slots if n in layer.build_slots]
        if not slots:
            return (None, None, f"区域 {placement.region!r} 无 build_slots")
        if placement.index is not None:
            if placement.index >= len(slots):
                return (None, None, f"区域 {placement.region!r} slot index {placement.index} 越界")
            bs = slots[placement.index]
            return (slot_point(bs), bs.name, None)
        # 按槽位类别 + footprint 尺寸过滤：supply→补给位、production→生产位，
        # addon 位不参与放置（挂件由母建筑吸附，几何预留）；兵营(3×3)不放补给站(2×2)位（真机踩过）
        entry = catalog.by_stable_id(head.type)
        if entry is not None and "supply" in entry.capabilities:
            slots = [bs for bs in slots if bs.kind == "supply"]
        else:
            slots = [bs for bs in slots if bs.kind == "production"]
        if not slots:
            return (None, None, f"区域 {placement.region!r} 无匹配类别的 build_slots")
        target_size = entry.size if entry is not None else None
        if target_size is not None:
            slots = [bs for bs in slots if bs.size == target_size]
            if not slots:
                return (None, None, f"区域 {placement.region!r} 无 size={target_size} 的 build_slots")
        occupied = occupied_cells(gs, catalog)
        # 在途建造预留：命令已发但实体未出现的放置位不能被别的项重选
        # （真机教训 full_flow.log：未预留 → 重选已下单位 → SC2 静默拒绝 → 丢补给站）
        for f in (f for flights in in_flight.values() for f in flights):
            ep = f.get("expect_pos")
            if ep is None or f.get("builder") is None:
                continue
            fe = catalog.by_stable_id(f.get("type"))
            fs = fe.size if fe is not None else 2
            ftl = BuildSlot.tl_from_reported(ep, fs)
            occupied |= {(x, y) for x in range(ftl.x, ftl.x + fs) for y in range(ftl.y, ftl.y + fs)}
        for bs in slots:  # index=None：按声明顺序找第一个未被占/未尝试过的 slot（P0）
            if bs.name in attempted:
                continue
            if not (slot_cells(bs) & occupied):
                return (slot_point(bs), bs.name, None)
        return (None, None, None)  # 全被占/全试过 → 阻塞等待或重试
    return (None, None, f"未知 placement 类型 {type(placement).__name__}")


def slot_cells(bs) -> set[tuple[int, int]]:
    return {(x, y) for x in range(bs.tl.x, bs.br.x + 1) for y in range(bs.tl.y, bs.br.y + 1)}


def slot_point(bs) -> Point2:
    """下发 driver 的世界建造点：校准值优先（BuildSlot.build_point）。"""
    return bs.build_point
