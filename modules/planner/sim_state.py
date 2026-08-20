"""planner.sim_state：仿真态 + 从 GameState 快照派生。

SimState = planner 仿真循环用的可变态（从 GameState 派生起步，之后由仿真循环推进）。
derive_from(gs, catalog) 把一帧 live GameState 派生成 SimState（矿/气/supply/工人/建筑/单位/在途）。
对齐需求文档：planner 消费 GameState（world 输出）；不读 live 通道、不接管执行。

buildings/units/in_flight.type 一律按**稳定 ID**（如 "terran/factory"）建索引——与 Op.type、catalog 一致，
feasible/prereq/produced_by 直接比对，不绕 burnysc2 名转换。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import GameState, Owner
from game.catalog import Catalog, Role

from planner.curve import ProjectionPoint


@dataclass
class InFlight:
    """在建/在训项（仿真推进用）。"""

    type: str                       # 稳定 ID（"terran/factory" / "terran/siegetank"）
    kind: str                        # "build"（建筑）/ "train"（单位）
    build_time: int                  # catalog.build_time（落成阈值，秒）
    progress: float                  # 已耗秒（0..build_time）；到 build_time 落成
    producer: str | None = None      # train 的产建筑稳定 ID（build=None）


@dataclass
class SimState:
    """planner 仿真态：从 GameState 派生起步，由仿真循环每秒推进。"""

    t: float
    minerals: float
    gas: float
    supply_used: int
    supply_cap: int
    total_workers: int
    mineral_workers: int
    gas_workers: int
    idle_workers: int
    buildings: dict[str, int] = field(default_factory=dict)   # 稳定 ID -> count（已建成）
    units: dict[str, int] = field(default_factory=dict)       # 稳定 ID -> count（已训成）
    in_flight: list[InFlight] = field(default_factory=list)
    addons: dict[str, int] = field(default_factory=dict)      # 母建筑稳定 ID -> 反应堆数（+1 训练槽/反应堆）
    research_completed: set[str] = field(default_factory=set)  # 已完成升级稳定 ID

    def to_point(self) -> ProjectionPoint:
        """导出为 ProjectionPoint（曲线快照）。"""
        return ProjectionPoint(
            t=self.t, minerals=self.minerals, gas=self.gas,
            supply_used=self.supply_used, supply_cap=self.supply_cap,
            mineral_workers=self.mineral_workers, gas_workers=self.gas_workers,
            buildings=dict(self.buildings), units=dict(self.units),
            in_flight_count=len(self.in_flight),
        )


def derive_from(gs: GameState, catalog: Catalog) -> SimState:
    """从 live GameState 快照派生 SimState（planner 投影的起始态）。

    - 工人按 orders.target_tag 分类：矿脉→mineral、精炼厂→gas、无命令→idle
      （采气 order 目标是精炼厂 tag 不是气井——修正 live 检测里"气工=0"的同类坑）。
    - 建筑/单位按 catalog.role + build_progress：complete→buildings/units；<1→in_flight。
    - 索引一律稳定 ID（catalog.stable_id_for；未知型回退 burnysc2 名）。
    """
    mineral_tags = {u.tag for u in gs.resources if u.type_name.startswith("MINERALFIELD")}
    refinery_tags = {u.tag for u in gs.units
                     if u.owner is Owner.SELF and u.type_name == "REFINERY" and u.build_progress >= 1.0}
    total = mineral = gas = idle = 0
    buildings: dict[str, int] = {}
    units: dict[str, int] = {}
    in_flight: list[InFlight] = []
    for u in gs.units:
        if u.owner is not Owner.SELF:
            continue
        entry = catalog.by_burnysc2_name(u.type_name)
        role = entry.role if entry is not None else None
        sid = (catalog.stable_id_for(u.type_name) if entry is not None else None) or u.type_name
        if role == Role.WORKER:
            total += 1
            if any(o.target_tag in mineral_tags for o in u.orders):
                mineral += 1
            elif any(o.target_tag in refinery_tags for o in u.orders):
                gas += 1
            elif not u.orders:
                idle += 1
            # else: build/repair/移动等——计入 total 但不进 mineral/gas/idle（不产收入）
        elif role == Role.BUILDING:
            if u.build_progress >= 1.0:
                buildings[sid] = buildings.get(sid, 0) + 1
            else:
                bt = entry.build_time if entry is not None else 1
                in_flight.append(InFlight(sid, "build", bt, u.build_progress * bt, None))
        elif role == Role.COMBAT:
            if u.build_progress >= 1.0:
                units[sid] = units.get(sid, 0) + 1
            else:
                bt = entry.build_time if entry is not None else 1
                producer = entry.produced_by if entry is not None else None
                in_flight.append(InFlight(sid, "train", bt, u.build_progress * bt, producer))
    return SimState(
        t=gs.game_time, minerals=float(gs.minerals), gas=float(gs.vespene),
        supply_used=gs.supply_used, supply_cap=gs.supply_cap,
        total_workers=total, mineral_workers=mineral, gas_workers=gas, idle_workers=idle,
        buildings=buildings, units=units, in_flight=in_flight,
    )
