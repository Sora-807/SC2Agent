"""flow 形态变体归一化端到端（T3）：坦克架起后 type_name 变 SIEGETANKSIEGED，
flow 经 catalog 反向归一仍把它算作 SIEGETANK 组——计数/lease/下发不失联。

对照：不传 catalog 时归一透传，架起态漏计（回归基线）。
"""
from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_terran
from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy

CAT = load_terran()

FORMUP_STRATEGY = """
id: tank_formup
group_slots: [armor]
params:
  min_tanks: {type: int, default: 4}
variables: {}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, group: armor, type: terran/siegetank}, {param: min_tanks}]}
        do: [{op: exit_strategy, kind: done, reason: FORMED}]
      - do: []
"""

ADVANCE_STRATEGY = """
id: tank_advance
group_slots: [armor]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: armor, type: terran/siegetank, action_atom: move_to, params: {position: [10.0, 10.0]}}
"""

# assembly 的 strategy_ref 必须与加载的 strategy id 一致（validate_assembly 校验）；
# __REF__ 占位按测试加载的策略注入。
_ARMOR_ASSEMBLY = """
id: tank_assembly
groups:
  - group_id: G1
    composition:
      terran/siegetank: {min: 4, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: __REF__
    bindings: {armor: G1}
    params: {}
"""


def _assembly(strategy_ref: str):
    return _ARMOR_ASSEMBLY.replace("__REF__", strategy_ref)


def _tank(tag, type_name, x=0.0, y=0.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=Owner.SELF,
                hp=160.0, hp_max=160.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(seq, units, game_time=0.0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=game_time, minerals=50, vespene=0,
                     supply_used=len(units) * 2, supply_cap=20, units=units,
                     map_size=(176, 160), creep=g, visibility=g)


def _four_sieged():
    """4 辆全部已架起（type_name=SIEGETANKSIEGED）。"""
    return [_tank(200 + i, "SIEGETANKSIEGED", float(i)) for i in range(4)]


def test_formup_counts_sieged_as_siegetank_with_catalog():
    """4 辆架起态坦克 + catalog → group_count(armor, SIEGETANK)==4 → formup 直达 done。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(FORMUP_STRATEGY), parse_assembly(_assembly("tank_formup")),
                     port, catalog=CAT)
    eng.on_game_state(_gs(0, _four_sieged()))
    assert eng._done is True  # 架起态归一为 SIEGETANK，formup 门达标 → exit_strategy


def test_catalog_is_required_at_construction():
    """T1/D1：catalog 必传 —— 没有 catalog 就无法把 stable id 翻译成实体名，
    旧的"不传 catalog 则静默漏计架起态"回归基线被这条构造期报错取代（不静默）。"""
    import pytest
    port = FakeGamePort(script=[])
    with pytest.raises(ValueError, match="catalog"):
        FlowEngine(parse_strategy(FORMUP_STRATEGY), parse_assembly(_assembly("tank_formup")), port)


def test_group_action_targets_sieged_tanks_with_catalog():
    """架起态坦克 + catalog → Allocator.expand 归一 → move_to op 命中全部 4 辆（含架起态）。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(ADVANCE_STRATEGY), parse_assembly(_assembly("tank_advance")),
                     port, catalog=CAT)
    eng.on_game_state(_gs(0, _four_sieged()))
    moves = [o for o in port.submitted if o.action == "move_to"]
    assert len(moves) == 1
    assert sorted(moves[0].unit_tags) == [200, 201, 202, 203]  # 4 辆全命中


def test_unknown_stable_id_in_composition_rejected_at_construction():
    """composition 误写 burnysc2 名（旧词汇）→ 构造期 validate_assembly 拒绝（R6），不再静默漏 lease。"""
    import pytest
    port = FakeGamePort(script=[])
    bad = _ARMOR_ASSEMBLY.replace("__REF__", "tank_advance").replace(
        "terran/siegetank: {min: 4", "SIEGETANK: {min: 4")
    with pytest.raises(AssertionError, match="stable id"):
        FlowEngine(parse_strategy(ADVANCE_STRATEGY), parse_assembly(bad), port, catalog=CAT)


def test_mixed_sieged_and_unsieged_counted_together():
    """2 未架起 + 2 已架起 + catalog → group_count(armor, SIEGETANK)==4 → done（架起循环不丢）。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(FORMUP_STRATEGY), parse_assembly(_assembly("tank_formup")),
                     port, catalog=CAT)
    units = [_tank(200, "SIEGETANK"), _tank(201, "SIEGETANK"),
            _tank(202, "SIEGETANKSIEGED"), _tank(203, "SIEGETANKSIEGED")]
    eng.on_game_state(_gs(0, units))
    assert eng._done is True
