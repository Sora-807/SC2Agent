"""planner.sim_state：derive_from 从 GameState 快照派生 SimState（稳定 ID 索引）。"""
from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_terran
from planner.sim_state import derive_from


def _unit(tag, type_name, owner=Owner.SELF, build_progress=1.0, orders=()):
    return Unit(tag=tag, type_name=type_name, position=Point2(0, 0), owner=owner,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0, build_progress=build_progress,
                orders=list(orders))


def _gather(target_tag):
    return Order(ability="Gather", target_tag=target_tag)


def _gs(units, resources=(), minerals=200, vespene=50, supply_used=10, supply_cap=20, t=100.0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=t, minerals=minerals, vespene=vespene,
                     supply_used=supply_used, supply_cap=supply_cap, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


def test_derive_from_state_fields():
    cat = load_terran()
    gs = _gs([_unit(1, "COMMANDCENTER"), _unit(2, "MARINE")])
    st = derive_from(gs, cat)
    assert st.t == 100.0 and st.minerals == 200.0 and st.gas == 50.0
    assert st.supply_used == 10 and st.supply_cap == 20


def test_derive_from_workers_minerals_gas_idle_other():
    cat = load_terran()
    mineral_patch = _unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)
    refinery = _unit(800, "REFINERY", build_progress=1.0)
    scv_min = [_unit(100 + i, "SCV", orders=[_gather(900)]) for i in range(3)]
    scv_gas = [_unit(200 + i, "SCV", orders=[_gather(800)]) for i in range(2)]
    scv_idle = _unit(300, "SCV")
    scv_build = _unit(301, "SCV", orders=[Order(ability="Build", target_pos=Point2(1, 1))])
    gs = _gs([refinery] + scv_min + scv_gas + [scv_idle, scv_build], resources=[mineral_patch])
    st = derive_from(gs, cat)
    assert st.mineral_workers == 3
    assert st.gas_workers == 2
    assert st.idle_workers == 1
    assert st.total_workers == 7


def test_derive_from_gas_targets_refinery_not_geyser():
    """采气 order 目标是精炼厂 tag（不是气井）——修正 live 检测里'气工=0'的同类坑。"""
    cat = load_terran()
    geyser = _unit(950, "GEYSER", owner=Owner.NEUTRAL)
    refinery = _unit(810, "REFINERY", build_progress=1.0)
    scv_gas = _unit(201, "SCV", orders=[_gather(810)])
    scv_wrong = _unit(202, "SCV", orders=[_gather(950)])
    gs = _gs([refinery, scv_gas, scv_wrong], resources=[geyser])
    st = derive_from(gs, cat)
    assert st.gas_workers == 1
    assert st.total_workers == 2


def test_derive_from_buildings_units_inflight_stable_ids():
    cat = load_terran()
    cc = _unit(1, "COMMANDCENTER")
    rax = _unit(2, "BARRACKS")
    refinery = _unit(3, "REFINERY", build_progress=1.0)
    factory = _unit(4, "FACTORY", build_progress=0.5)
    marine = _unit(5, "MARINE")
    tank = _unit(6, "SIEGETANK", build_progress=0.25)
    gs = _gs([cc, rax, refinery, factory, marine, tank])
    st = derive_from(gs, cat)
    assert st.buildings == {"terran/commandcenter": 1, "terran/barracks": 1, "terran/refinery": 1}
    assert st.units == {"terran/marine": 1}
    assert len(st.in_flight) == 2
    fac = next(f for f in st.in_flight if f.type == "terran/factory")
    assert fac.kind == "build" and fac.build_time == 43 and fac.progress == 0.5 * 43
    assert fac.producer is None
    tk = next(f for f in st.in_flight if f.type == "terran/siegetank")
    assert tk.kind == "train" and tk.build_time == 32 and tk.progress == 0.25 * 32
    assert tk.producer == "terran/factory"


def test_to_point_snapshot():
    cat = load_terran()
    gs = _gs([_unit(1, "COMMANDCENTER"), _unit(2, "MARINE")])
    st = derive_from(gs, cat)
    pt = st.to_point()
    assert pt.t == 100.0 and pt.minerals == 200.0
    assert pt.buildings == {"terran/commandcenter": 1}
    assert pt.units == {"terran/marine": 1}
    assert pt.in_flight_count == 0
