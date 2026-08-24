"""constraint 模块：生产可行性校验 (GameState, 意图) → ConstraintResult。

规则来源 = game catalog（造价/时间/前置单点权威）；只判当前可做性（S4），
不模拟未来、不 import sc2。placement 的 footprint 闭区间（ADR-0027）待升级。
"""
from constraint.checks import (
    ConstraintResult,
    check_addon,
    check_build,
    check_gas,
    check_prerequisites,
    check_resources,
    check_train,
    occupied_cells,
)

__all__ = [
    "ConstraintResult",
    "check_addon",
    "check_build",
    "check_gas",
    "check_prerequisites",
    "check_resources",
    "check_train",
    "occupied_cells",
]
