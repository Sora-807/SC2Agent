"""planner：生产投影模块（对齐需求文档 S5）。

planner(GameState, production_sequence) → 投影曲线：逐帧/逐秒顺序模拟 production_sequence 消费
（可行性门控 constraint+mechanics），输出未来资源/单位曲线。
无状态、不依赖 flow 运行期、不模拟战斗（R4）、planner 是唯一预测源。

P1：数据模型 + derive_from（从 GameState 派生 SimState）。
"""
from planner.curve import ProjectionCurve, ProjectionEvent, ProjectionPoint
from planner.sim_state import InFlight, SimState, derive_from
from planner.build_order import (
    Op, Build, Train, AssignWorkers, Research,
    ProductionModuleInstance, expand, register_module,
)
from planner.economy import DEFAULT_ECON, EconomyParams
from planner.planner import Planner
from planner import module_defs  # noqa: F401  导入即注册内置 production module

__all__ = [
    "ProjectionCurve", "ProjectionEvent", "ProjectionPoint",
    "SimState", "InFlight", "derive_from",
    "Op", "Build", "Train", "AssignWorkers", "Research",
    "ProductionModuleInstance", "expand", "register_module",
    "EconomyParams", "DEFAULT_ECON", "Planner",
]


