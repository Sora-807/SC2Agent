"""game.raw：SC2 原生形态的状态类型（D1 状态两面的"raw 面"，docs/contract/P0-影响边界.md）。

driver 抽取 burnysc2 对象产出 RawGameState；world 消费并适配成 game.state.GameState。
字段名对齐 burnysc2 原生：alliance（int，非 Owner 枚举）、health（非 hp）、
ability_name（burnysc2 AbilityData.button_name）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game.geometry import Grid, Point2


@dataclass(slots=True)
class RawOrder:
    ability_name: str  # burnysc2 AbilityData.button_name，如 "Gather"/"Move"/"SupplyDepot"
    target_tag: int | None = None
    target_pos: Point2 | None = None


@dataclass(slots=True)
class RawUnit:
    tag: int
    type_name: str  # burnysc2 UnitTypeId.name，如 "SCV"/"COMMANDCENTER"（原始名）
    position: Point2
    alliance: int  # 原始：1=self, 2=ally, 3=enemy, 4=neutral（矿脉也标 3；world 按 TYPE 再区分）
    health: float
    health_max: float
    shield: float
    energy: float
    build_progress: float
    orders: list[RawOrder] = field(default_factory=list)
    facing: float = 0.0
    buffs: tuple[str, ...] = ()
    is_carrying_minerals: bool = False
    is_carrying_vespene: bool = False


@dataclass(slots=True)
class RawGameState:
    seq: int
    game_time: float
    minerals: int
    vespene: int
    supply_used: int
    supply_cap: int
    units: list[RawUnit]  # 全量可见单位（己/盟/敌/中性矿脉等）
    map_size: tuple[int, int]
    creep: Grid  # 原始，dims = map_size
    visibility: Grid
