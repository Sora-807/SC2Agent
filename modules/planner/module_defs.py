"""planner.module_defs：内置 production module（code 注册）。

V1 用 code 函数注册模块（module_ref → params → op 序列）；YAML authoring 后续。
P6 设计 20+4 build order 时补全/调整这里（加更多模块、调参数）。
"""
from __future__ import annotations

from planner.build_order import AssignWorkers, Build, Train, register_module


def _factory_chain(params: dict):
    """工厂链：factory → factorytechlab → train siegetank×tank_count。"""
    tank_count = int(params.get("tank_count", 4))
    ops = [Build("terran/factory"), Build("terran/factorytechlab")]
    ops += [Train("terran/siegetank") for _ in range(tank_count)]
    return ops


def _basic_opening(params: dict):
    """简单开局（测试用）：depot → scv×N → barracks。P6 换成真实开局。"""
    scv_count = int(params.get("scv_count", 12))
    return [Build("terran/supplydepot"),
            *[Train("terran/scv") for _ in range(scv_count)],
            Build("terran/barracks")]


# 导入即注册（planner/__init__ 导入本模块触发）
register_module("factory_chain", _factory_chain)
register_module("basic_opening", _basic_opening)
