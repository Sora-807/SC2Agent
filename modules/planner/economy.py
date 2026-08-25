"""planner.economy：经济参数（收入速率）。

V1 线性近似（每 SCV 每秒固定速率）；非线性饱和递减 P5 偏差大再加。
数值**不信 wiki**——mineral/gas 速率以真机 log / burnysc2 game_data 校准（P5）。

供给提供量不在这里了（B6 三族单源）：catalog 条目的 supply_provided 字段
（`Catalog.supply_map()`），三族通用 —— planner/opening/initial_state/
worldsim/alerts 都从那取。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EconomyParams:
    """仿真经济参数（V1 线性；P5 校准）。"""

    mineral_per_scv_per_sec: float = 0.6     # 每 SCV 每秒矿物（P5 校准）
    gas_per_scv_per_sec: float = 0.6          # 每 SCV 每秒气（P5 校准）


# 默认经济参数（P5 校准）
DEFAULT_ECON = EconomyParams()
