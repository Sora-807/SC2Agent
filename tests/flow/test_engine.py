"""flow engine：FakeGamePort 驱动 simple_push（formup 等待→advance move→exit_strategy）。

验证：formup 等待（不发包）→ group_count 达标 exit_step → advance 发 move（去重只一条）
→ strategy_elapsed 达标 exit_strategy。确定 + 动作去重。
"""
from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game import GameState, Grid, Owner, Point2, Unit

STRATEGY_YAML = """
id: simple_push
version: 1
group_slots: [main]
params:
  min_units: {type: int, default: 4, live_editable: true}
variables: {}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, args: [main]}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: ">=", args: [{op: strategy_elapsed}, {const: 5.0}]}
        do: [{op: exit_strategy, kind: done, reason: SAFE}]
      - do:
          - {op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {position: [10.0, 10.0]}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
on_exit: release
loop_limits: {max_step_transitions: 100}
"""

ASSEMBLY_YAML = """
id: test_assembly
groups:
  - group_id: G1
    composition:
      MARINE: {min: 4, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: simple_push
    bindings: {main: G1}
    params: {}
"""


def _gs(seq: int, marine_count: int, game_time: float) -> GameState:
    units = [
        Unit(tag=100 + i, type_name="MARINE", position=Point2(float(i), 0.0), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)
        for i in range(marine_count)
    ]
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=game_time, minerals=50, vespene=0,
                     supply_used=marine_count, supply_cap=20, units=units,
                     map_size=(176, 160), creep=g, visibility=g)


def test_formup_waits_then_advances_move_once_then_exit():
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(STRATEGY_YAML), parse_assembly(ASSEMBLY_YAML), port)
    # seq0-3 兵力<4 formup 等待；seq4 达4 exit_step→advance；seq5-6 advance elapsed<5 发 move（去重一条）；seq7 elapsed>=5 exit；seq8 done
    for seq, mc, t in [(0, 2, 0.0), (1, 2, 0.5), (2, 3, 1.0), (3, 3, 1.5),
                       (4, 4, 2.0), (5, 4, 3.0), (6, 4, 4.0), (7, 4, 5.0), (8, 4, 6.0)]:
        eng.on_game_state(_gs(seq, mc, t))

    moves = [op for op in port.submitted if op.action == "move_to"]
    assert len(moves) == 1, f"expected 1 move (dedup), got {len(moves)}: {moves}"
    assert moves[0].unit_tags == [100, 101, 102, 103]  # 4 marines leased
    assert moves[0].params == {"position": [10.0, 10.0]}
    assert moves[0].seq == 5  # first emitted at seq5
    assert eng._done is True  # exit_strategy reached by seq7


def test_no_emit_while_formup_waiting():
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(STRATEGY_YAML), parse_assembly(ASSEMBLY_YAML), port)
    for seq, mc, t in [(0, 1, 0.0), (1, 2, 0.5), (2, 3, 1.0)]:
        eng.on_game_state(_gs(seq, mc, t))
    assert port.submitted == []  # formup 等待，不发 op
    assert eng._active_step == "formup"


def test_parse_and_validate_rejects_unmatched_exit_step():
    bad = """
id: bad
group_slots: [main]
params: {}
initial_step: a
steps:
  - step_id: a
    branches:
      - do: [{op: exit_step, kind: done, reason: NOPE}]
edges: []
"""
    import pytest
    with pytest.raises(AssertionError):
        parse_strategy(bad)


def test_unknown_action_atom_rejected():
    import pytest
    bad = """
id: bad2
group_slots: [main]
params: {}
initial_step: a
steps:
  - step_id: a
    branches:
      - do: [{op: group_action, group_slot: main, type: MARINE, action_atom: fly_to, params: {position: [1,1]}}]
edges: []
"""
    with pytest.raises(AssertionError):
        parse_strategy(bad)


# ---- 空间谓词测试（tactical_map：arrived）----

ARRIVED_STRATEGY = """
id: arrive_test
group_slots: [main]
params: {min_units: {type: int, default: 2}}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, args: [main]}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: arrived, args: [main, [10.0, 10.0], 3.0]}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - do:
          - {op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {position: [10.0, 10.0]}}
edges: [{from: formup, to: advance, kind: done, reason: FORMED}]
on_exit: release
"""


def _gs_arrive(seq, positions):
    units = [
        Unit(tag=100 + i, type_name="MARINE", position=Point2(x, y), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)
        for i, (x, y) in enumerate(positions)
    ]
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq), minerals=50, vespene=0, supply_used=2,
                     supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g)


def test_arrived_spatial_predicate():
    """formup→advance；advance 用 arrived(main,[10,10],3)：兵接近目标到 3 内→exit。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(ARRIVED_STRATEGY), parse_assembly(ASSEMBLY_YAML), port)
    # 2 marines 接近 [10,10]：seq0 [0,0] formup→advance；seq1 [4,4] advance move（去重 1 条）；seq2 [8,8] arrived→exit
    eng.on_game_state(_gs_arrive(0, [(0, 0), (0, 0)]))
    eng.on_game_state(_gs_arrive(1, [(4, 4), (4, 4)]))
    eng.on_game_state(_gs_arrive(2, [(8, 8), (8, 8)]))
    moves = [op for op in port.submitted if op.action == "move_to"]
    assert len(moves) == 1, f"expected 1 move (dedup), got {len(moves)}"
    assert eng._done is True  # arrived at seq2 → exit_strategy
