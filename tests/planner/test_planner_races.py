"""planner.planner 三族投影（N1a 测试先行 / REFACTOR B6）。

现状（写测试时预期红）：投影器只认 Terran ——
- 气收入封顶与 _gas_coming 写死 terran/refinery（P/Z 气工空转）；
- supply_provided 只列 CC/depot（pylon/hatchery 落成不涨供给）；
- Zerg 语义缺失：overlord 是单位（train 落成才涨供给）、建筑由 drone 变成（开工吞工）。

期望形态（D4 hybrid）：气矿/供给「哪些建筑」从 catalog 推导；种族语义差
（overlord 供给、drone 消耗）是显式钩子。本文件全部转绿 = N1 验收。
"""
import pytest

from game import Order, Owner
from game.catalog import load_all
from planner.build_order import Build, Train
from planner.planner import Planner
from planner.sim_state import derive_from
from planner.opening import opening_game_state
from tests.factories import make_gs, make_unit


# ---------------- 三族开局夹具 ----------------

# (基地名, 工人名, 额外开局单位, 开局 supply_cap)。Zerg 开局自带 1 只 overlord
# （Hatchery=4 + Overlord=8 = 12，与 12 农民正好 12/12）；T/P 基地自供 13。
_OPENINGS = {
    "terran": ("COMMANDCENTER", "SCV", (), 13),
    "protoss": ("NEXUS", "PROBE", (), 13),
    "zerg": ("HATCHERY", "DRONE", ("OVERLORD",), 12),
}


def _start_opening(race: str, *, minerals: int = 5000, vespene: int = 500, workers: int = 12):
    """标准开局 GameState：基地 + 12 农民挂矿 + （Zerg）1 只 overlord。"""
    base, worker, extras, cap = _OPENINGS[race]
    patch = make_unit(900, "MINERALFIELD", owner=Owner.NEUTRAL)
    units = [make_unit(1, base)]
    for i in range(workers):
        units.append(make_unit(100 + i, worker,
                               orders=[Order(ability="Gather", target_tag=900)]))
    for j, name in enumerate(extras):
        units.append(make_unit(300 + j, name))
    return make_gs(units, resources=[patch], minerals=minerals, vespene=vespene,
                   supply_used=workers, supply_cap=cap)


# 三族对称开局链：补给建筑 → 工人 → 产兵建筑 → 作战单位。
# 完成时刻全部由 catalog build_time 推出（T 校准 = 既有 simple_chain 测试同款）。
# 注：catalog 里 protoss/gateway 前置记为 nexus（真实应为 pylon）——按 catalog
# 真值断言，gateway 可与 pylon 同秒开工；数据欠账另行核对，不在这份测试里修。
_OPENING_CASES = {
    "terran": dict(
        ops=[Build("terran/supplydepot"), Train("terran/scv"),
             Build("terran/barracks"), Train("terran/marine")],
        done={"terran/scv": 12, "terran/supplydepot": 21,
              "terran/barracks": 67, "terran/marine": 85},
        supply_cap=21, supply_used=14, mineral_workers=13,
        buildings={"terran/supplydepot": 1, "terran/barracks": 1},
        units={"terran/marine": 1},
    ),
    "protoss": dict(
        ops=[Build("protoss/pylon"), Train("protoss/probe"),
             Build("protoss/gateway"), Train("protoss/zealot")],
        done={"protoss/probe": 12, "protoss/pylon": 18,
              "protoss/gateway": 46, "protoss/zealot": 73},
        supply_cap=21, supply_used=15, mineral_workers=13,
        buildings={"protoss/pylon": 1, "protoss/gateway": 1},
        units={"protoss/zealot": 1, "protoss/probe": 1},
    ),
    "zerg": dict(
        # 12/12 起步：drone 等 overlord@18 涨供给（cap 12→20）；spawningpool 同秒
        # 开工（FIFO 只按可行性门控）；zergling 等 spawningpool@64。drone 变建筑
        # 被吞：final 工人 12（训 1 吞 1），supply_used 12−1+1+1=13。
        ops=[Train("zerg/overlord"), Train("zerg/drone"),
             Build("zerg/spawningpool"), Train("zerg/zergling")],
        done={"zerg/overlord": 18, "zerg/drone": 30,
              "zerg/spawningpool": 64, "zerg/zergling": 81},
        supply_cap=20, supply_used=13, mineral_workers=12,
        buildings={"zerg/spawningpool": 1},
        units={"zerg/overlord": 2, "zerg/drone": 1, "zerg/zergling": 1},
    ),
}


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_opening_projection_three_races(race):
    """三族各一条完整开局投影：完成时刻、终态建筑/单位/工人、全程零死局。"""
    cat = load_all()
    p = Planner(cat)
    case = _OPENING_CASES[race]
    gs = _start_opening(race)
    curve = p.project(gs, case["ops"], until=30, until_complete=True)
    completed = {e.type: e.t for e in curve.events if e.kind == "completed"}
    for sid, t in case["done"].items():
        assert completed.get(sid) == t, f"{sid} 完成时刻（实得 {completed.get(sid)}）"
    assert curve.stalls() == [], [e.reason for e in curve.stalls()]
    last = curve.points[-1]
    assert last.supply_cap == case["supply_cap"]
    assert last.supply_used == case["supply_used"]
    assert last.mineral_workers == case["mineral_workers"]
    for sid, n in case["buildings"].items():
        assert last.buildings.get(sid) == n
    for sid, n in case["units"].items():
        assert last.units.get(sid) == n


# ---------------- 气矿收入按本族气矿建筑封顶 ----------------

_GAS_CASES = [
    ("terran", "COMMANDCENTER", "REFINERY", "SCV"),
    ("protoss", "NEXUS", "ASSIMILATOR", "PROBE"),
    ("zerg", "HATCHERY", "EXTRACTOR", "DRONE"),
]


@pytest.mark.parametrize(("race", "base", "gas_building", "worker"), _GAS_CASES,
                         ids=["terran/refinery", "protoss/assimilator", "zerg/extractor"])
def test_gas_income_counts_race_gas_buildings(race, base, gas_building, worker):
    """3 气工对本族气矿建筑照常产气（0.6/工/秒）——封顶不得只数 terran/refinery。"""
    cat = load_all()
    p = Planner(cat)
    gas_b = make_unit(800, gas_building)
    units = [make_unit(1, base), gas_b]
    units += [make_unit(100 + i, worker, orders=[Order(ability="Gather", target_tag=800)])
              for i in range(3)]
    gs = make_gs(units, resources=[], minerals=0, vespene=0)
    curve = p.project(gs, [], until=60)
    assert curve.points[-1].gas == pytest.approx(3 * 0.6 * 60)
    assert curve.points[-1].gas_workers == 3


# ---------------- Zerg 语义：overlord 供给 / drone 消耗 ----------------

def test_zerg_overlord_train_raises_supply_cap():
    """Zerg 的供给建筑是单位：Train(overlord) 落成即 +8 供给（不是 build 才涨）。"""
    cat = load_all()
    p = Planner(cat)
    gs = _start_opening("zerg")   # 12/12
    curve = p.project(gs, [Train("zerg/overlord")], until=30)
    assert curve.points[-1].supply_cap == 20, "overlord 落成后 cap 12→20"


def test_zerg_build_consumes_drone():
    """Zerg 建筑由 drone 变成：开工即吞工（供给释放、矿池少一人），落成不回矿。"""
    cat = load_all()
    p = Planner(cat)
    gs = _start_opening("zerg")   # 12 农民 12/12
    curve = p.project(gs, [Build("zerg/spawningpool")], until=60)
    last = curve.points[-1]
    assert last.buildings.get("zerg/spawningpool") == 1
    assert last.mineral_workers == 11, "builder 被吞，不回矿"
    assert last.supply_used == 11, "drone 化为建筑，供给在开工时释放"


# ---------------- 三族开局种子（opening race 参数化） ----------------

@pytest.mark.parametrize(("race", "cap"), [("terran", 13), ("protoss", 13), ("zerg", 12)])
def test_opening_seed_three_races(race, cap):
    """opening_game_state(race=...)：基地+12 农民挂矿；Zerg 带 overlord（cap=4+8=12）。"""
    from planner.opening import base_supply, opening_game_state

    cat = load_all()
    gs = opening_game_state(cat, race=race)
    assert gs.supply_cap == cap == base_supply(cat, race)
    assert gs.supply_used == 12
    # 种子可直接进投影：derive_from 认得出（12 矿工 0 闲），收入起算
    st = derive_from(gs, cat)
    assert st.mineral_workers == 12 and st.idle_workers == 0
    if race == "zerg":
        assert st.units.get("zerg/overlord") == 1


# ---------------- basic_opening 模块 race 参数化（N1d） ----------------

def test_basic_opening_module_three_races():
    """params.race 三族：模板展开可投影、全程零死局、各自产兵建筑落成。"""
    import planner  # noqa: F401  触发内置模块注册
    from planner.build_order import ProductionModuleInstance

    prod_sid = {"terran": "terran/barracks", "protoss": "protoss/gateway",
                "zerg": "zerg/spawningpool"}
    army_sid = {"terran": "terran/barracks", "protoss": "protoss/gateway",
                "zerg": "zerg/zergling"}
    for race in ("terran", "protoss", "zerg"):
        cat = load_all()
        p = Planner(cat)
        gs = opening_game_state(cat, race=race)
        seq = [ProductionModuleInstance("m0", "basic_opening",
                                     params={"race": race, "scv_count": 3})]
        curve = p.project(gs, seq, until=60, until_complete=True)
        assert curve.stalls() == [], (race, [e.reason for e in curve.stalls()])
        completed = {e.type for e in curve.events if e.kind == "completed"}
        assert prod_sid[race] in completed, race
        assert army_sid[race] in completed, race


def test_basic_opening_module_rejects_unknown_race():
    """未知 race 当场 ValueError（plans.py 模板落地把它翻成 400）。"""
    import planner  # noqa: F401
    from planner.build_order import MODULE_REGISTRY

    with pytest.raises(ValueError, match="race"):
        MODULE_REGISTRY["basic_opening"]({"race": "xelnaga"})
