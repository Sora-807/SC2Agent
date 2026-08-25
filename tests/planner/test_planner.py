"""planner.planner：project 仿真主循环（完成时间/事件/卡点/可行性门控）。"""
import pytest

from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_all
from tests.factories import make_gs, make_unit
from planner.build_order import Build, ProductionModuleInstance, Train, register_module
from planner.planner import Planner

import planner  # noqa: F401  触发内置模块注册


def _unit(tag, type_name, owner=Owner.SELF, build_progress=1.0, orders=()):
    return make_unit(tag, type_name, owner, 0.0, 0.0, hp=100.0, hp_max=100.0,
                     progress=build_progress, orders=orders)


def _gs(units, resources=(), minerals=5000, vespene=500, supply_used=12, supply_cap=15, t=0.0):
    return make_gs(units, resources, seq=0, game_time=t, minerals=minerals, vespene=vespene,
                   supply_used=supply_used, supply_cap=supply_cap)


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
    cat = load_all()
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
    cat = load_all()
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
    cat = load_all()
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
    cat = load_all()
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
    cat = load_all()
    p = Planner(cat)
    gs = _start_with_cc_and_scv(minerals=0, gas=0)   # 0 矿 12 矿工
    register_module("test_depot_only", lambda p: [Build("terran/supplydepot")])
    curve = p.project(gs, [ProductionModuleInstance("m0", "test_depot_only")], until=60)
    assert curve.stalls() == []
    completed = {e.type for e in curve.events if e.kind == "completed"}
    assert "terran/supplydepot" in completed, "攒够钱后正常开工落成"


def test_deadlock_gas_never_coming():
    """产建筑齐全、气为 0、无精炼厂无气工 → 气永远为 0 → 死局警报「缺气」。"""
    cat = load_all()
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
    cat = load_all()
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


# ---------------- 二十三轮：until_complete（曲线不再在生产中途截断） ----------------

def test_until_complete_runs_past_horizon_to_finish_queue():
    """horizon=30 时队列还没跑完（marine 85s 才出）—— until_complete 继续跑到完成，
    曲线覆盖全程而不是在 30s 截断。"""
    cat = load_all()
    p = Planner(cat)
    gs = _start_with_cc_and_scv()
    seq = [ProductionModuleInstance("m0", "test_chain")]
    curve = p.project(gs, seq, until=30, until_complete=True)
    completed = {e.type: e.t for e in curve.events if e.kind == "completed"}
    assert completed.get("terran/marine") == 85          # 跑完了
    assert curve.points[-1].t >= 85                       # 曲线延到完成之后


def test_without_flag_stays_bounded():
    """不传 until_complete（live 投影的窗口语义）仍在 until 停 —— 行为不变。"""
    cat = load_all()
    p = Planner(cat)
    gs = _start_with_cc_and_scv()
    seq = [ProductionModuleInstance("m0", "test_chain")]
    curve = p.project(gs, seq, until=30)
    assert curve.points[-1].t < 30   # 逐秒循环在 until 前的最后一个整秒停
    assert not [e for e in curve.events if e.kind == "completed" and e.t > 30]


def test_until_complete_deadlock_stops_at_cap():
    """死局（无收入缺矿）不会无限跑：越过 until 后最多 COMPLETION_CAP 秒停住。"""
    from planner.planner import COMPLETION_CAP
    cat = load_all()
    p = Planner(cat)
    gs = _gs([_unit(1, "COMMANDCENTER")], minerals=0, vespene=0)  # 无 SCV 无收入
    seq = [ProductionModuleInstance("m0", "test_chain")]
    curve = p.project(gs, seq, until=10, until_complete=True)
    assert curve.points[-1].t <= 10 + COMPLETION_CAP
    assert curve.stalls(), "死局要留卡点事件"


def _gs_with_trained_marines(progresses):
    """一间兵营 + 若干在训机枪兵（不同进度 = 第一条在训、其余排队，I10 快照形态）。"""
    gs = _start_with_cc_and_scv()
    gs.units.append(_unit(9001, "BARRACKS"))
    gs.units += [_unit(9100 + i, "MARINE", build_progress=p)
                 for i, p in enumerate(progresses)]
    return gs


def test_inflight_trains_queue_on_producer_slot():
    """I10：快照里同兵营的 6 条在训机枪兵必须 18s 链式完成，不是同秒齐落。

    旧版 in_flight 并行 +1：排队中的单位与在训单位同时到点，投影泳道堆出
    假并行。修复后与 _feasible 的 B15 槽模型对齐：一间兵营每秒只推进一条。
    """
    p = Planner(load_all())
    gs = _gs_with_trained_marines([0.9, 0.05, 0.05, 0.05, 0.05, 0.05])
    curve = p.project(gs, [], until=200)
    finishes = sorted(t for kind, t in
                      ((e.kind, e.t) for e in curve.events
                       if e.type == "terran/marine" and e.kind == "completed"))
    assert len(finishes) == 6
    # 第一条 ~2s 后落成；其后每条 +18s（一槽一队列）
    assert finishes[0] <= 4
    for a, b in zip(finishes, finishes[1:]):
        assert b - a == 18


def test_tail_extends_past_last_completion():
    """二十七轮：队列跑空后再留 30s —— 最后事件完成后看得到经济余势。"""
    p = Planner(load_all())
    gs = _start_with_cc_and_scv()
    from planner.build_order import register_module
    register_module("tail_probe", lambda p_: [Build("terran/supplydepot")])
    from planner.build_order import ProductionModuleInstance
    curve = p.project(gs, [ProductionModuleInstance("m", "tail_probe", 1, {})],
                      until=10, until_complete=True, tail=30.0)
    completes = [e.t for e in curve.events if e.kind == "completed"]
    last = max(completes)
    assert abs(curve.points[-1].t - (last + 30)) <= 1, \
        f"曲线末端应是最后完成事件 {last} + 30s，实际 {curve.points[-1].t}"
    # 不传 tail = 行为不变（跑空即停）
    curve2 = p.project(gs, [ProductionModuleInstance("m2", "tail_probe", 1, {})],
                       until=10, until_complete=True)
    assert abs(curve2.points[-1].t - last) <= 1
