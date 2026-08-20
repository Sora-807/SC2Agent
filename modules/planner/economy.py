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
            # Terran 默认（P5 校准）
            object.__setattr__(self, "supply_provided", {
                "terran/commandcenter": 15,
                "terran/supplydepot": 8,
            })


# 默认 Terran 经济参数（P5 校准）
DEFAULT_ECON = EconomyParams()
