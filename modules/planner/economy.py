"""planner.economy：经济参数（收入速率 + 供给提供量）。

V1 线性近似（每 SCV 每秒固定速率）；非线性饱和递减 P5 偏差大再加。
数值**不信 wiki**——mineral/gas 速率、supply_provided 以真机 log / burnysc2 game_data 校准（P5）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EconomyParams:
    """仿真经济参数（V1 线性；P5 校准）。"""

    mineral_per_scv_per_sec: float = 0.6     # 每 SCV 每秒矿物（P5 校准）
    gas_per_scv_per_sec: float = 0.6          # 每 SCV 每秒气（P5 校准）
    # 建筑提供的供给（稳定 ID → supply_cap 增量）；Terran V1（P5 校准）
    supply_provided: dict[str, int] = None    # type: ignore  (frozen dataclass 用 default_factory 不便，改 None 兜底)

    def __post_init__(self):
        if self.supply_provided is None:
            # **单一真相源**（opening 种子与 worldsim 的 _supply_cap 都从这里取）。
            # 数值以本机游戏数据为准：docs/data/game_data_dump.json 的 food_provided
            # （CommandCenter=13、SupplyDepot=8），并经真机录像对照（开局 1 CC +
            # 0 补给站 → supply_cap=13，2026-08-23 那局首帧）。三族值（Nexus=13/
            # Pylon=8/Hatchery 族=4/Overlord=8，同 dump）等 planner race-agnostic
            # （REFACTOR B6）时一并进来。
            object.__setattr__(self, "supply_provided", {
                "terran/commandcenter": 13,
                "terran/supplydepot": 8,
            })


# 默认 Terran 经济参数（P5 校准）
DEFAULT_ECON = EconomyParams()
