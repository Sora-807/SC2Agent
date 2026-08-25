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
from planner.slots import reactor_map


@dataclass
class InFlight:
    """在建/在训项（仿真推进用）。"""

    type: str                       # 稳定 ID（"terran/factory" / "terran/siegetank"）
    kind: str                        # "build"（建筑）/ "train"（单位）
    build_time: int                  # catalog.build_time（落成阈值，秒）
    progress: float                  # 已耗秒（0..build_time）；到 build_time 落成
    producer: str | None = None      # train 的产建筑稳定 ID（build=None）
    uid: str | None = None           # 来源队列项账本 ID（批 3：完成时刻归账）


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

    - 工人按 orders.target_tag 分类：矿脉→mineral、精炼厂→gas、无命令→idle；
      扛着矿/气且订单是采矿族（Gather/Harvest/Return）→ 按扛的货归池（送矿途中
      不是零收入，Return 单目标=基地查不到 target）；
      扛货但订单是外来能力（build/move…）→ 只计 total（不产收入，等它回来）。
    - 建筑/单位按 catalog.role + build_progress：complete→buildings/units；<1→in_flight。
    - 索引一律稳定 ID（catalog.stable_id_for；未知型回退 burnysc2 名）。
    """
    mineral_tags = {u.tag for u in gs.resources if u.type_name.startswith("MINERALFIELD")}
    # 气矿建筑走 catalog（三族 REFINERY/EXTRACTOR/ASSIMILATOR）——2026-08-25 审计批4
    # 清 "REFINERY" 硬编码（与 economy._gas_names 同源修法），Z/P 气矿工不再被漏分类
    gas_names = frozenset(e.burnysc2_name for e in catalog.where(capability="gas"))
    gas_building_tags = {u.tag for u in gs.units
                         if u.owner is Owner.SELF and u.type_name in gas_names
                         and u.build_progress >= 1.0}
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
            elif any(o.target_tag in gas_building_tags for o in u.orders):
                gas += 1
            elif not u.orders:
                idle += 1
            elif (getattr(u, "is_carrying_minerals", False)
                    or getattr(u, "is_carrying_vespene", False)):
                # 送矿/运气途中（Return 单目标=基地，不在上面两个集合里）——仍是
                # 产收入工，漏了会系统性低估收入、投影完工时刻逐帧后移（与
                # economy._current_assignment 的 harvest_mem 同一类坑）。扛货但被
                # 外来能力征走（build/move…）的不算 —— 按 mining 族能力名区分。
                mining_family = any(
                    k in (o.ability or "").lower() for o in u.orders
                    for k in ("gather", "harvest", "return"))
                if mining_family:
                    if getattr(u, "is_carrying_vespene", False):
                        gas += 1
                    else:
                        mineral += 1
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
        # B15：live 快照里挂件是独立建筑 —— 从建筑计数推出反应堆数，否则投影会把
        # 已有 reactor 的兵营当 1 槽（旧代码只在投影内落成时才更新 addons）。
        addons={
            parent: n
            for parent, sid in reactor_map(catalog).items()
            if (n := buildings.get(sid, 0)) > 0
        },
    )
