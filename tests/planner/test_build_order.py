"""planner.build_order：expand 把 production_sequence 展平成 op 序列。"""
import pytest

from planner.build_order import (
    AssignWorkers, Build, ProductionModuleInstance, Train, expand, register_module,
)
import planner  # noqa: F401  触发 module_defs 注册内置模块


def test_expand_single_module():
    seq = [ProductionModuleInstance("m0", "factory_chain", params={"tank_count": 4})]
    ops = expand(seq)
    assert ops == [Build("terran/factory"), Build("terran/factorytechlab")] + [Train("terran/siegetank")] * 4
    assert len(ops) == 6


def test_expand_default_params():
    seq = [ProductionModuleInstance("m0", "factory_chain")]  # tank_count default 4
    ops = expand(seq)
    assert ops.count(Train("terran/siegetank")) == 4
    assert ops[0] == Build("terran/factory") and ops[1] == Build("terran/factorytechlab")


def test_expand_multiple_instances_concat():
    seq = [ProductionModuleInstance("m0", "basic_opening", params={"scv_count": 2}),
           ProductionModuleInstance("m1", "factory_chain", params={"tank_count": 1})]
    ops = expand(seq)
    # basic_opening: depot, scv, scv, barracks; factory_chain: factory, techlab, tank
    assert ops == [Build("terran/supplydepot"), Train("terran/scv"), Train("terran/scv"),
                   Build("terran/barracks"), Build("terran/factory"),
                   Build("terran/factorytechlab"), Train("terran/siegetank")]


def test_expand_unknown_module_raises():
    with pytest.raises(ValueError, match="未知"):
        expand([ProductionModuleInstance("m0", "no_such_module")])


def test_register_module_duplicate_raises():
    def fn(p):
        return [Build("terran/supplydepot")]

    register_module("dup_test", fn)
    with pytest.raises(ValueError, match="重复注册"):
        register_module("dup_test", fn)
