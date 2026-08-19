"""world 适配层：RawGameState → GameState。

职责（docs/需求文档-v0.1.md §1；docs/P0-影响边界.md D1）：
- 字段对齐：health→hp、health_max→hp_max、alliance+type→Owner 枚举、orders adapt。
- 按 TYPE 判 neutral：矿脉/气井/装饰物（alliance=3 但非 enemy）过滤出 GameState.units
  （flow 的 enemy_count_near 不会误数矿脉；见 docs/driver_spike.md alliance 二义）。
- V1 no-op：position/creep/visibility/map_size 透传（spike 已证左下原点 + dims 对齐）。
- 稳定 type_name / ability 映射留 catalog（V1 透传 burnysc2 名）。
- 规则层（power/addon via mechanics.LayerComputer）D11 后加（届时 adapt 需注入 mechanics）。
"""
from __future__ import annotations

from game import GameState, Order, Owner, RawGameState, RawOrder, RawUnit, Unit

# 中性资源/装饰物类型（按 type 名判，过滤出 GameState.units；可扩）
NEUTRAL_TYPES: frozenset[str] = frozenset({
    "MINERALFIELD", "MINERALFIELD750", "MINERALFIELD1000", "MINERALFIELD1500",
    "VESPENEGEYSER", "RICHVESPENEGEYSER", "SPACEPLATFORMGEYSER",
    # doodads / destructibles（示例）
    "DESTRUCTIBLEROCK6X6", "DESTRUCTIBLEROCK4X4", "DESTRUCTIBLERAMPDIAGONAL",
    "XELNAGATOWER", "FORCEFIELD",
})


def is_neutral_resource(type_name: str) -> bool:
    return type_name in NEUTRAL_TYPES


def _alliance_to_owner(alliance: int) -> Owner:
    if alliance == 1:
        return Owner.SELF
    if alliance == 2:
        return Owner.ALLY
    if alliance == 3:
        return Owner.ENEMY
    return Owner.NEUTRAL


def adapt_order(o: RawOrder) -> Order:
    return Order(
        ability=o.ability_name,  # V1: burnysc2 ability 名；稳定 ID 映射留 catalog
        target_tag=o.target_tag,
        target_pos=o.target_pos,
        # is_auto 留 False（auto-order 检测后续；driver 按 auto-order 白名单设）
    )


def adapt_unit(u: RawUnit) -> Unit:
    return Unit(
        tag=u.tag,
        type_name=u.type_name,  # V1: burnysc2 名；稳定 ID 映射留 catalog
        position=u.position,
        owner=_alliance_to_owner(u.alliance),
        hp=u.health,
        hp_max=u.health_max,
        shield=u.shield,
        energy=u.energy,
        build_progress=u.build_progress,
        orders=[adapt_order(o) for o in u.orders],
        facing=u.facing,
        buffs=u.buffs,
        is_carrying_minerals=u.is_carrying_minerals,
        is_carrying_vespene=u.is_carrying_vespene,
    )


def adapt(raw: RawGameState) -> GameState:
    """RawGameState → GameState（V1：过滤 neutral + 字段对齐 + 透传）。"""
    units = [adapt_unit(u) for u in raw.units if not is_neutral_resource(u.type_name)]
    return GameState(
        seq=raw.seq,
        game_time=raw.game_time,
        minerals=raw.minerals,
        vespene=raw.vespene,
        supply_used=raw.supply_used,
        supply_cap=raw.supply_cap,
        units=units,
        map_size=raw.map_size,
        creep=raw.creep,  # V1 透传（左下原点 + dims 对齐，no-op）
        visibility=raw.visibility,
        # map_layers（power/addon）D11 后加
    )
