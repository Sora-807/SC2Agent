"""planner.planner：project 仿真主循环（完成时间/事件/卡点/可行性门控）。"""
import pytest

from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_terran
from planner.build_order import Build, ProductionModuleInstance, Train, register_module
from planner.planner import Planner

import planner  # noqa: F401  触发内置模块注册


def _unit(tag, type_name, owner=Owner.SELF, build_progress=1.0, orders=()):
    return Unit(tag=tag, type_name=type_name, position=Point2(0, 0), owner=owner,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0, build_progress=build_progress,
                orders=list(orders))


def _gs(units, resources=(), minerals=5000, vespene=500, supply_used=12, supply_cap=15, t=0.0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=t, minerals=minerals, vespene=vespene,
                     supply_used=supply_used, supply_cap=supply_cap, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


def _start_with_cc_and_scv(minerals=5000, gas=500):
    cc = _unit(1, "COMMANDCENTER")
    patch = _unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)
    scvs = [_unit(100 + i, "SCV", orders=[Order(ability="Gather", target_tag=900)]) for i in range(12)]
    return _gs([cc] + scvs, resources=[patch], minerals=minerals, vespene=gas)


# 测试用模块：depot → barracks → marine
register_module("test_chain", lambda p: [
    Build("terran/supplydepot"), Build("terran/barracks"), Train("terran/marine")])


def test_project_simple_chain_completion_times():
    """资源充足时：depot@21 → barracks@67(21+46) → marine@85(67+18)，全程无卡。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _start_with_cc_and_scv()
    seq = [ProductionModuleInstance("m0", "test_chain")]
    curve = p.project(gs, seq, until=100)
    # 完成事件
    completed = {e.type: e.t for e in curve.events if e.kind == "completed"}
    assert completed.get("terran/supplydepot") == 21
    assert completed.get("terran/barracks") == 67
    assert completed.get("terran/marine") == 85
    # 终态
    last = curve.points[-1]
    assert last.buildings.get("terran/supplydepot") == 1
    assert last.buildings.get("terran/barracks") == 1
    assert last.units.get("terran/marine") == 1
    # 全程无死局卡点：barracks 等 depot、marine 等 barracks 都是「前置在途」的
    # 时序等待（新语义：等待不产生 stalled 事件，只有死局才报）
    assert curve.stalls() == [], [e.reason for e in curve.stalls()]
    # time_to
    assert curve.time_to(lambda pt: pt.units.get("terran/marine", 0) >= 1) == 85


def test_project_stall_when_no_minerals():
    """无矿无收入 → depot 永久卡住（缺矿），全程不完成。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _gs([_unit(1, "COMMANDCENTER")], minerals=0, vespene=0)  # 无 SCV 无收入
    seq = [ProductionModuleInstance("m0", "test_chain")]
    curve = p.project(gs, seq, until=50)
    assert len(curve.stalls()) >= 1
    assert all(e.type == "terran/supplydepot" for e in curve.stalls())  # 队首 depot 卡
    completed = {e.type for e in curve.events if e.kind == "completed"}
    assert "terran/supplydepot" not in completed  # 没完成


def test_project_train_blocked_until_producer_ready():
    """marine 的产建筑（barracks）没建好前，train 卡（前置没）；barracks 完成后启动。"""
    cat = load_terran()
    p = Planner(cat)
    # 起始无 barracks，直接 train marine
    register_module("test_train_only", lambda p: [Train("terran/marine")])
    gs = _start_with_cc_and_scv()
    seq = [ProductionModuleInstance("m0", "test_train_only")]
    curve = p.project(gs, seq, until=10)
    stalls = curve.stalls()
    assert len(stalls) >= 1
    assert any("前置" in (e.reason or "") for e in stalls)


def test_project_income_accumulates():
    """12 SCV 采矿物，收入累积（minerals 递增，扣除建造花费后仍增）。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _start_with_cc_and_scv(minerals=0, gas=0)
    seq = []  # 空队列：纯挂机采矿物
    curve = p.project(gs, seq, until=10)
    # 10 秒收入：12 × 0.6 × 10 = 72（浮点 approx）
    assert curve.points[-1].minerals == pytest.approx(72.0)
    assert curve.points[-1].mineral_workers == 12


# ---------------- 等待 vs 死局（stalled 只报死局） ----------------

def test_wait_not_recorded_mineral_shortage_with_income():
    """缺矿但有矿工在采 = 时序等待（攒钱），不产生 stalled 警报。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _start_with_cc_and_scv(minerals=0, gas=0)   # 0 矿 12 矿工
    register_module("test_depot_only", lambda p: [Build("terran/supplydepot")])
    curve = p.project(gs, [ProductionModuleInstance("m0", "test_depot_only")], until=60)
    assert curve.stalls() == []
    completed = {e.type for e in curve.events if e.kind == "completed"}
    assert "terran/supplydepot" in completed, "攒够钱后正常开工落成"


def test_deadlock_gas_never_coming():
    """产建筑齐全、气为 0、无精炼厂无气工 → 气永远为 0 → 死局警报「缺气」。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _gs(
        [_unit(1, "COMMANDCENTER"), _unit(2, "FACTORY"), _unit(3, "FACTORYTECHLAB"),
         _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)])],
        resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)],
        minerals=5000, vespene=0)
    curve = p.project(gs, [Train("terran/siegetank")], until=30)
    stalls = curve.stalls()
    assert stalls and stalls[0].reason == "缺气"


def test_deadlock_supply_at_cap_200():
    """供给顶到 200 上限：守卫不再插 depot（插了也不涨），「缺供给」浮出为死局。"""
    cat = load_terran()
    p = Planner(cat)
    gs = _gs(
        [_unit(1, "COMMANDCENTER"), _unit(2, "BARRACKS"),
         _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)])],
        resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)],
        minerals=5000, vespene=500, supply_used=200, supply_cap=200)
    curve = p.project(gs, [Train("terran/marine")] * 1, until=20)
    stalls = curve.stalls()
    assert stalls and stalls[0].reason == "缺供给"
    assert all(pt.supply_cap == 200 for pt in curve.points), "守卫不得在 200 后插 depot"
