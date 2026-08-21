"""planner.slots：产槽模型（B15）—— 科技槽 / 普通槽分开算。

用户实测反馈的问题：投影把 techlab 母建筑当普通产槽（坦克在普通工厂上也能"同时开工"），
也不体现 reactor 双倍产能。这里锁死四条规则：
1. 科技单位（前置含本产建筑 techlab）只能用科技槽；
2. 普通单位优先普通槽、普通满才溢出到空闲科技槽；
3. reactor 让被占母建筑出 2 个普通槽；
4. live 快照里已有的挂件要被 derive_from 数进去（旧代码只在投影内落成时才数）。
"""
import pytest

from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_terran
from planner.build_order import Train
from planner.planner import Planner
from planner.sim_state import derive_from
from planner.slots import is_tech_unit, reactor_map, slot_capacity, techlab_map


def _unit(tag, type_name, owner=Owner.SELF, build_progress=1.0, orders=()):
    return Unit(tag=tag, type_name=type_name, position=Point2(0, 0), owner=owner,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0, build_progress=build_progress,
                orders=list(orders))


def _gs(units, resources=(), minerals=5000, vespene=500, supply_used=10, supply_cap=60, t=0.0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=t, minerals=minerals, vespene=vespene,
                     supply_used=supply_used, supply_cap=supply_cap, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


CAT = load_terran()


def test_tech_unit_classification():
    """坦克是科技单位（factorytechlab 前置）；机枪兵不是。"""
    tank = CAT.by_stable_id("terran/siegetank")
    marine = CAT.by_stable_id("terran/marine")
    assert tank is not None and marine is not None
    assert is_tech_unit(tank, CAT) is True
    assert is_tech_unit(marine, CAT) is False
    # 挂件映射从 catalog 推导，不硬编码名字
    assert techlab_map(CAT)["terran/factory"] == "terran/factorytechlab"
    assert techlab_map(CAT)["terran/barracks"] == "terran/techlab"
    assert reactor_map(CAT)["terran/factory"] == "terran/factoryreactor"


def test_slot_capacity_math():
    """2 工厂 + 1 techlab + 1 reactor → 普通槽 (2−1−1)+1×2=2、科技槽 1。"""
    buildings = {"terran/factory": 2, "terran/factorytechlab": 1, "terran/factoryreactor": 1}
    reactor = {"terran/factory": 1}
    normal, tech = slot_capacity("terran/factory", buildings, reactor, "terran/factorytechlab")
    assert (normal, tech) == (2, 1)


def test_tank_only_uses_tech_slots():
    """2 普通工厂 + 1 techlab：坦克同时最多 1 个在产（科技槽=1），普通工厂再多也不行。"""
    gs = _gs([
        _unit(1, "COMMANDCENTER"),
        _unit(2, "FACTORY"), _unit(3, "FACTORY"), _unit(4, "FACTORYTECHLAB"),
        _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)]),
    ], resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)])
    curve = Planner(CAT).project(gs, [Train("terran/siegetank")] * 3, until=200)
    # 同一时刻在产坦克 ≤ 1（逐秒数曲线事件：started 与 completed 严格交替）
    active = 0
    peak = 0
    for e in curve.events:
        if e.kind == "started" and e.type == "terran/siegetank":
            active += 1
            peak = max(peak, active)
        elif e.kind == "completed" and e.type == "terran/siegetank":
            active -= 1
    assert peak == 1, "科技槽=1：坦克必须串行"
    # 串行排队是**等待**（槽忙，坦克落成即空出），不是警报 —— 不产生 stalled 事件
    assert curve.stalls() == [], [e.reason for e in curve.stalls()]
    # 3 辆最终都完成（串行不等于造不出）
    completed = [e for e in curve.events
                 if e.kind == "completed" and e.type == "terran/siegetank"]
    assert len(completed) == 3


def test_normal_unit_overflows_to_tech_slot():
    """0 普通兵营 + 1 techlab 兵营：第 1 个机枪兵溢出到科技槽；第 2 个必须排队到
    第 1 个完成（18s）才能启动 —— 同一时刻在产 ≤ 1，期间卡「无产槽」。"""
    gs = _gs([
        _unit(1, "COMMANDCENTER"),
        _unit(2, "BARRACKS"), _unit(3, "BARRACKSTECHLAB"),
        _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)]),
    ], resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)])
    curve = Planner(CAT).project(gs, [Train("terran/marine")] * 2, until=30)
    started = [e for e in curve.events
               if e.kind == "started" and e.type == "terran/marine"]
    # 两个都能溢出启动，但第 2 个必须等第 1 个完成（同一时刻在产 ≤ 科技槽=1）
    assert len(started) == 2
    assert started[0].t == 0.0
    assert started[1].t == 18.0, "普通槽=0：第 2 个枪兵只能等第 1 个落成（18s）"
    # 排队等待不产生 stalled（等待不是警报；见 planner._feasible 的 wait 语义）
    assert curve.stalls() == [], [e.reason for e in curve.stalls()]


def test_reactor_doubles_normal_slots():
    """1 兵营 + 1 reactor：机枪兵 2 个并行（普通槽=2），第 3 个卡。"""
    gs = _gs([
        _unit(1, "COMMANDCENTER"),
        _unit(2, "BARRACKS"), _unit(3, "BARRACKSREACTOR"),
        _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)]),
    ], resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)])
    curve = Planner(CAT).project(gs, [Train("terran/marine")] * 3, until=30)
    active = 0
    peak = 0
    for e in curve.events:
        if e.kind == "started" and e.type == "terran/marine":
            active += 1
            peak = max(peak, active)
        elif e.kind == "completed" and e.type == "terran/marine":
            active -= 1
    assert peak == 2, "反应堆：普通槽 = (1−0−1) + 1×2 = 2，两枪兵并行"
    # 第 3 个排队是等待（槽忙），不是警报
    assert curve.stalls() == [], [e.reason for e in curve.stalls()]


def test_derive_from_counts_existing_addons():
    """live 快照里已有的 reactor/techlab 必须进 SimState（旧代码漏数 → 兵营被当 1 槽）。"""
    gs = _gs([
        _unit(1, "COMMANDCENTER"),
        _unit(2, "BARRACKS"), _unit(3, "BARRACKSREACTOR"),
        _unit(4, "FACTORY"), _unit(5, "FACTORYTECHLAB"),
        _unit(100, "SCV", orders=[Order(ability="Gather", target_tag=900)]),
    ], resources=[_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)])
    st = derive_from(gs, CAT)
    assert st.addons == {"terran/barracks": 1}
    assert st.buildings["terran/barracks"] == 1
    assert st.buildings["terran/reactor"] == 1
    assert st.buildings["terran/factorytechlab"] == 1
