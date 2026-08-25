"""world 适配层：RawGameState → GameState。

职责（docs/contract/需求文档-v0.1.md §1；docs/contract/P0-影响边界.md D1）：
- 字段对齐：health→hp、health_max→hp_max、alliance+type→Owner 枚举、orders adapt。
- 按名称模式判 neutral：矿脉/气井/岩石/装饰物（alliance=3 但非 enemy）过滤出
  GameState.units（flow 的 enemy_count_near 不会误数矿脉；关键词表单一事实源在
  game.catalog.neutral_kind，I25；见 docs/reference/driver_spike.md alliance 二义）。
- V1 no-op：position/creep/visibility/map_size 透传（spike 已证左下原点 + dims 对齐）。
- 稳定 type_name / ability 映射留 catalog（V1 透传 burnysc2 名）。
- 规则层（power/addon via mechanics.LayerComputer）D11 后加（届时 adapt 需注入 mechanics）。
"""
from __future__ import annotations

from game import GameState, Order, Owner, RawGameState, RawOrder, RawUnit, Unit
from game.catalog import neutral_kind

#: 模式判不了的非资源中立物（法术效果等），显式补（I25）
_EXTRA_NEUTRAL_TYPES: frozenset[str] = frozenset({"FORCEFIELD"})


def is_neutral_resource(type_name: str) -> bool:
    """按名称模式判中立物（单一事实源 `game.catalog.neutral_kind`；I25 根治）。

    旧实现是 12 个 type 名的硬编码白名单——SC2 可破坏障碍物每张图自带几十种子类型，
    白名单外的岩石 alliance=3 被归成 Owner.ENEMY，是"假敌方警报困 Agent 空转"的根因
    （ISSUES I25）。矿脉/气井/瞭望塔/岩石/残骸等一律按关键词归中性。
    """
    return str(type_name).upper() in _EXTRA_NEUTRAL_TYPES or neutral_kind(type_name) is not None


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
    """RawGameState → GameState（V1：过滤 neutral + 字段对齐 + 透传）。

    中性资源（矿脉/气井）不进 units（flow 谓词不数它们），拆到 resources：
    WorkerAllocator / 生产约束需要节点位置（docs/contract/test-plan.md world 节 resource_nodes）。
    """
    units = [adapt_unit(u) for u in raw.units if not is_neutral_resource(u.type_name)]
    resources = [adapt_unit(u) for u in raw.units if is_neutral_resource(u.type_name)]
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
        resources=resources,  # 资源节点（矿脉/气井）
        # map_layers（power/addon）D11 后加
    )
