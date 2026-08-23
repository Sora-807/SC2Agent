"""flow engine：FakeGamePort 驱动 simple_push（formup 等待→advance move→exit_strategy）。

验证：formup 等待（不发包）→ group_count 达标 exit_step → advance 发 move（去重只一条）
→ strategy_elapsed 达标 exit_strategy。确定 + 动作去重。
"""
from driver.fake import FakeGamePort
from flow.engine import DEFAULT_MAX_STEP_TRANSITIONS, FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game.catalog import load_all
from game import GameState, Grid, Owner, Point2, Unit

CAT = load_all()

STRATEGY_YAML = """
id: simple_push
version: 1
group_slots: [main]
params:
  min_units: {type: int, default: 4}
variables: {}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: ">=", args: [{op: strategy_elapsed}, {const: 5.0}]}
        do: [{op: exit_strategy, kind: done, reason: SAFE}]
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [10.0, 10.0]}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
loop_limits: {max_step_transitions: 100}
"""

ASSEMBLY_YAML = """
id: test_assembly
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 4, target: 4, max: 4}
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
    eng = FlowEngine(parse_strategy(STRATEGY_YAML), parse_assembly(ASSEMBLY_YAML), port, catalog=CAT)
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
    eng = FlowEngine(parse_strategy(STRATEGY_YAML), parse_assembly(ASSEMBLY_YAML), port, catalog=CAT)
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
      - do: [{op: group_action, group_slot: main, type: terran/marine, action_atom: fly_to, params: {position: [1,1]}}]
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
      - when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: arrived, group: main, target: [10.0, 10.0], radius: 3.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [10.0, 10.0]}}
edges: [{from: formup, to: advance, kind: done, reason: FORMED}]
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


ARRIVED_ASSEMBLY = """
id: arrive_assembly
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: arrive_test
    bindings: {main: G1}
    params: {}
"""


def test_arrived_spatial_predicate():
    """formup→advance；advance 用 arrived(main,[10,10],3)：兵接近目标到 3 内→exit。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(ARRIVED_STRATEGY), parse_assembly(ARRIVED_ASSEMBLY), port, catalog=CAT)
    # 2 marines 接近 [10,10]：seq0 [0,0] formup→advance；seq1 [4,4] advance move（去重 1 条）；seq2 [8,8] arrived→exit
    eng.on_game_state(_gs_arrive(0, [(0, 0), (0, 0)]))
    eng.on_game_state(_gs_arrive(1, [(4, 4), (4, 4)]))
    eng.on_game_state(_gs_arrive(2, [(8, 8), (8, 8)]))
    moves = [op for op in port.submitted if op.action == "move_to"]
    assert len(moves) == 1, f"expected 1 move (dedup), got {len(moves)}"
    assert eng._done is True  # arrived at seq2 → exit_strategy

def test_region_name_in_action_params_resolved_at_emission():
    """ADR-0029 D1 端到端：do 动作里的 map 名在 emit 前解析成数值（driver 只见数值）。"""
    from tactical_map import BigRegion, RegionLayer

    layer = RegionLayer(
        map_name="t",
        size=(2, 2),
        big_grid=Grid(2, 2, [[1, 1], [1, 1]]),
        big_index={1: "main_base"},
        big_regions={"main_base": BigRegion(stable_id="main_base", anchor=Point2(1.5, 0.5))},
    )
    strategy = """
id: named_target
group_slots: [main]
params: {min_units: {type: int, default: 2}}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: main_base}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: named_target
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, region_layer=layer, catalog=CAT)
    eng.on_game_state(_gs(0, 2, 0.0))
    assert len(port.submitted) == 1
    op = port.submitted[0]
    assert op.action == "move_to"
    assert op.params == {"position": [1.5, 0.5]}  # map 名已解析为 [x, y]，不是字符串


def test_dedup_resend_when_params_change():
    """去重键 (slot,type,atom) 相同但 params 变 → 重发（spec-003 §2.1）。"""
    strategy = """
id: chg
group_slots: [main]
params: {}
variables: {}
initial_step: s1
steps:
  - step_id: s1
    branches:
      - when: {op: "<", args: [{op: game_time}, {const: 3.0}]}
        do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [10.0, 10.0]}}
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [20.0, 20.0]}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: chg
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)
    for seq, t in [(0, 0.0), (1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0)]:
        eng.on_game_state(_gs(seq, 2, t))
    moves = [o for o in port.submitted if o.action == "move_to"]
    assert len(moves) == 2, f"params 变化应重发，got {len(moves)}"
    assert moves[0].params == {"position": [10.0, 10.0]}
    assert moves[1].params == {"position": [20.0, 20.0]}


def test_point_toward_in_action_param():
    """动作参数含 point_toward（T4）：position = point_toward(group_center, target, dist) 求值成 [x,y]。
    2 marines 在 (0,0) → group_center=(0,0)；朝 [20,0] 延伸 5 → (5,0)。"""
    strategy = """
id: pt
group_slots: [main]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: {op: point_toward, origin: {op: group_center, group: main}, toward: [20.0, 0.0], dist: 5.0}}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: pt
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)
    units = [Unit(tag=100 + i, type_name="MARINE", position=Point2(0.0, 0.0), owner=Owner.SELF,
                  hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0) for i in range(2)]
    g = Grid(1, 1, [[0]])
    eng.on_game_state(GameState(seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=2,
                                supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g))
    assert len(port.submitted) == 1
    assert port.submitted[0].params["position"] == [5.0, 0.0]


def test_dedup_quantization_suppresses_micro_move():
    """去重量化（T4）：动态点（组心）微移 0.4 不重发，跨整格（≥1）才重发。
    position = point_toward(group_center, [100,0], 10) ≈ group_center.x + 10；
    组心 0→0.4 量化同 [10,0] 不重发；0.4→1.4 量化跨 [10]→[11] 重发。实际下发保留精确值。"""
    strategy = """
id: q
group_slots: [main]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: {op: point_toward, origin: {op: group_center, group: main}, toward: [100.0, 0.0], dist: 10.0}}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: q
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)

    def gs(cx):
        units = [Unit(tag=100 + i, type_name="MARINE", position=Point2(cx, 0.0), owner=Owner.SELF,
                      hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0) for i in range(2)]
        g = Grid(1, 1, [[0]])
        return GameState(seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=2,
                         supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g)

    eng.on_game_state(gs(0.0))    # point (10.0,0) → 首次 emit
    eng.on_game_state(gs(0.4))    # point (10.4,0) → 量化 [10,0] 同键 → 不重发
    eng.on_game_state(gs(1.4))    # point (11.4,0) → 量化 [11,0] 异键 → 重发
    moves = [o for o in port.submitted if o.action == "move_to"]
    assert len(moves) == 2, f"微移不重发、跨格才重发，期望 2 条，got {len(moves)}"
    assert moves[0].params["position"] == [10.0, 0.0]      # 精确值（非量化）
    assert moves[1].params["position"] == [11.4, 0.0]


def test_dedup_key_separates_by_type():
    """同 slot 同 atom 不同 type → 各自独立去重键（同组多兵种协同）。"""
    strategy = """
id: multi
group_slots: [main]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [1.0, 1.0]}}
          - {op: group_action, group_slot: main, type: terran/scv, action_atom: move_to, params: {position: [2.0, 2.0]}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 1, target: 1, max: 1}
      terran/scv: {min: 1, target: 1, max: 1}
strategy_instances:
  - instance_id: s1
    strategy_ref: multi
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)
    units = [
        Unit(tag=1, type_name="MARINE", position=Point2(0, 0), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0),
        Unit(tag=2, type_name="SCV", position=Point2(0, 0), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0),
    ]
    g = Grid(1, 1, [[0]])
    eng.on_game_state(GameState(seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=2,
                                supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g))
    assert len(port.submitted) == 2  # 两个 type 各一条
    assert {o.params["position"][0] for o in port.submitted} == {1.0, 2.0}


def test_empty_group_action_is_noop():
    """空 group 上的动作 = no-op，不产 Operation（spec-003 §3.2）。"""
    strategy = """
id: emptyg
group_slots: [main]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [1.0, 1.0]}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: emptyg
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)
    eng.on_game_state(_gs(0, 0, 0.0))  # 没有任何 MARINE
    assert port.submitted == []
    assert not eng._done  # 引擎照常运行，不崩


def test_exit_step_stops_remaining_do_items():
    """运行时兜底：exit 之后的 do 项被跳过（spec-003 §3）。

    编译期已拒绝这种写法（test_manifest.test_action_after_exit_rejected）；
    这里绕过编译直接构造 manifest，验证 engine 执行路径同样安全。
    """
    from flow.manifest import FlowAssembly, GroupSpec, StrategyInstance, StrategyManifest

    m = StrategyManifest(
        id="exitstop", version=1, group_slots=["main"], params={}, variables={},
        initial_step="s1",
        steps={
            "s1": {"branches": [{"do": [
                {"op": "group_action", "group_slot": "main", "type": "terran/marine",
                 "action_atom": "move_to", "params": {"position": [1.0, 1.0]}},
                {"op": "exit_step", "kind": "done", "reason": "GO"},
                {"op": "group_action", "group_slot": "main", "type": "terran/marine",
                 "action_atom": "move_to", "params": {"position": [2.0, 2.0]}},
            ]}]},
            "s2": {"branches": [{"do": []}]},
        },
        edges=[{"from": "s1", "to": "s2", "kind": "done", "reason": "GO"}],
        loop_limits={},
    )
    a = FlowAssembly(
        id="a",
        groups=[GroupSpec("G1", {"terran/marine": {"min": 2, "target": 2, "max": 2}})],
        strategy_instances=[StrategyInstance("s1", "exitstop", {"main": "G1"}, {})],
    )
    port = FakeGamePort(script=[])
    eng = FlowEngine(m, a, port, catalog=CAT)
    eng.on_game_state(_gs(0, 2, 0.0))
    assert len(port.submitted) == 1  # exit 之后的第二条 move 被跳过
    assert eng._active_step == "s2"


def _loopy_engine(loop_limits: dict):
    """无出口自环 + 可配上限的引擎（直构 manifest 绕过编译期图校验）。

    无出口的环在编译期已被拒（test_manifest.test_cycle_without_exit_rejected，ADR-0021 §4）；
    运行期兜底必须独立成立，所以这里绕过 parse_strategy 直接构造。
    """
    from flow.manifest import FlowAssembly, GroupSpec, StrategyInstance, StrategyManifest

    m = StrategyManifest(
        id="loopy", version=1, group_slots=["main"], params={}, variables={},
        initial_step="s1",
        steps={"s1": {"branches": [
            {"do": [{"op": "exit_step", "kind": "done", "reason": "LOOP"}]},
        ]}},
        edges=[{"from": "s1", "to": "s1", "kind": "done", "reason": "LOOP"}],
        loop_limits=loop_limits,
    )
    a = FlowAssembly(
        id="a",
        groups=[GroupSpec("G1", {"terran/marine": {"min": 2, "target": 2, "max": 2}})],
        strategy_instances=[StrategyInstance("s1", "loopy", {"main": "G1"}, {})],
    )
    return FlowEngine(m, a, FakeGamePort(script=[]), catalog=CAT)


def test_loop_limit_caps_step_transitions():
    """有界环兜底：超过 loop_limits.max_step_transitions → 以 LOOP_LIMIT 结束（ADR-0021 验收 #4）。"""
    eng = _loopy_engine({"max_step_transitions": 2})
    for seq in range(5):
        eng.on_game_state(_gs(seq, 2, float(seq)))
    assert eng._step_transition_count == 3  # 第 3 次转移超上限
    assert eng._done is True
    assert eng.exit_record == {"kind": "failed", "reason": "LOOP_LIMIT", "limit": 2}


def test_undeclared_loop_limit_falls_back_to_global_default():
    """未声明 loop_limits 也绝不无限转移：引擎用 DEFAULT_MAX_STEP_TRANSITIONS 兜底。"""
    eng = _loopy_engine({})
    for seq in range(DEFAULT_MAX_STEP_TRANSITIONS + 5):
        eng.on_game_state(_gs(seq, 2, float(seq)))
    assert eng._done is True
    assert eng._step_transition_count == DEFAULT_MAX_STEP_TRANSITIONS + 1
    assert eng.exit_record["reason"] == "LOOP_LIMIT"
    assert eng.exit_record["limit"] == DEFAULT_MAX_STEP_TRANSITIONS


def test_set_variable_roundtrip_through_when():
    """set_variable 写入 → 下一帧 when 经 {var: name} 读回（spec-003 §5）。"""
    strategy = """
id: varflow
group_slots: [main]
params: {}
variables: {stage: {type: int, default: 0}}
initial_step: s1
steps:
  - step_id: s1
    branches:
      - when: {op: "==", args: [{var: stage}, {const: 1}]}
        do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [9.0, 9.0]}}
          - {op: exit_strategy, kind: done, reason: SAFE}
      - do:
          - {op: set_variable, name: stage, value: {const: 1}}
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 2, max: 2}
strategy_instances:
  - instance_id: s1
    strategy_ref: varflow
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, catalog=CAT)
    eng.on_game_state(_gs(0, 2, 0.0))  # stage 0 → else → set stage=1
    assert port.submitted == []
    eng.on_game_state(_gs(1, 2, 1.0))  # stage 1 → 命中 → move + exit
    assert len(port.submitted) == 1
    assert eng._done is True


def test_enemy_visible_in_via_engine_with_region_layer():
    """区域谓词走完整引擎链路：敌人在 main_base → exit_strategy。"""
    from tactical_map import BigRegion, RegionLayer

    layer = RegionLayer(
        map_name="t", size=(4, 4),
        big_grid=Grid(4, 4, [[1, 1, 2, 2]] * 4),
        big_index={1: "main_base", 2: "field"},
        big_regions={
            "main_base": BigRegion(stable_id="main_base", anchor=Point2(1, 1)),
            "field": BigRegion(stable_id="field", anchor=Point2(3, 3)),
        },
    )
    strategy = """
id: ewatch
group_slots: [main]
params: {}
variables: {}
initial_step: watch
steps:
  - step_id: watch
    branches:
      - when: {op: enemy_visible_in, region: main_base}
        do: [{op: exit_strategy, kind: done, reason: SAFE}]
      - do: []
"""
    assembly = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 1, target: 1, max: 1}
strategy_instances:
  - instance_id: s1
    strategy_ref: ewatch
    bindings: {main: G1}
    params: {}
"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(strategy), parse_assembly(assembly), port, region_layer=layer, catalog=CAT)
    eng.on_game_state(_gs(0, 1, 0.0))  # 无敌人
    assert not eng._done
    units = [
        Unit(tag=1, type_name="MARINE", position=Point2(0, 0), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0),
        Unit(tag=9, type_name="ZERGLING", position=Point2(1.5, 0.5), owner=Owner.ENEMY,
             hp=35.0, hp_max=35.0, shield=0.0, energy=0.0, build_progress=1.0),
    ]
    g = Grid(1, 1, [[0]])
    eng.on_game_state(GameState(seq=1, game_time=1.0, minerals=50, vespene=0, supply_used=1,
                                supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g))
    assert eng._done is True  # 敌人进入 main_base → exit





# ---- F1：去重键必须含单位集合 ----

HOLD_POINT = """
id: hold_point
group_slots: [main]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: attack_move_to, params: {position: [50.0, 50.0]}}
edges: []
"""

HOLD_ASSEMBLY = """
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 4, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: hold_point
    bindings: {main: G1}
    params: {}
"""


def test_refilled_units_receive_current_order():
    """F1：组补兵后新成员必须收到当前命令（固定目标点也要重发）。

    旧实现的去重键只有 (slot, type, atom, params)，且在展开 tags 之前判定 —— 新兵永远待命。
    真机表现：garrison 用固定 garrison_pos，后造出来的枪兵站在兵营不动。
    """
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(HOLD_POINT), parse_assembly(HOLD_ASSEMBLY), port, catalog=CAT)
    eng.on_game_state(_gs(0, 2, 0.0))
    assert [o.unit_tags for o in port.submitted] == [[100, 101]]
    eng.on_game_state(_gs(1, 4, 1.0))  # 补兵到 4
    assert [o.unit_tags for o in port.submitted] == [[100, 101], [100, 101, 102, 103]]
    eng.on_game_state(_gs(2, 4, 2.0))  # 成员与参数都没变 → 仍然去重，不刷命令
    assert len(port.submitted) == 2


def test_empty_group_does_not_poison_dedup():
    """F1 变体：首次求值时组是空的，不能写去重键 —— 否则之后有兵了也永远不发。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(HOLD_POINT), parse_assembly(HOLD_ASSEMBLY), port, catalog=CAT)
    eng.on_game_state(_gs(0, 0, 0.0))   # 空组：no-op
    assert port.submitted == []
    eng.on_game_state(_gs(1, 2, 1.0))   # 有兵了 → 必须下发
    assert [o.unit_tags for o in port.submitted] == [[100, 101]]


def test_unit_death_reasserts_order_to_survivors():
    """成员减少也算集合变化 → 给存活者重新确认命令（幂等，不会累积命令风暴）。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(HOLD_POINT), parse_assembly(HOLD_ASSEMBLY), port, catalog=CAT)
    eng.on_game_state(_gs(0, 4, 0.0))
    assert len(port.submitted) == 1
    eng.on_game_state(_gs(1, 3, 1.0))  # 死了一个（tag 100..102 存活）
    assert [o.unit_tags for o in port.submitted][-1] == [100, 101, 102]
    eng.on_game_state(_gs(2, 3, 2.0))
    assert len(port.submitted) == 2  # 稳定后不再重发


# ---------------- 二十六轮 T8：timer 族 + locals + engaged/under_attack ----------------


def _asm(strategy_ref: str):
    return parse_assembly(ASSEMBLY_YAML.replace("strategy_ref: simple_push",
                                                f"strategy_ref: {strategy_ref}"))


TIMER_STRATEGY = """
id: timer_probe
version: 1
group_slots: [main]
params: {}
variables: {}
initial_step: wait
steps:
  - step_id: wait
    locals: [burst]
    branches:
      - when: {op: ">=", args: [{local: burst}, {const: 2.0}]}
        do: [{op: exit_strategy, kind: done, reason: READ}]
      - when: {op: ">=", args: [{op: timer_elapsed, name: raid}, {const: 1.0}]}
        do: [{op: set_local, name: burst, value: {op: timer_elapsed, name: raid}}
        ]
      - do: [{op: start_timer, name: raid}]
"""


def test_timer_start_elapsed_stop_and_local_carry():
    """T8 落地：start_timer 幂等起算 → timer_elapsed 增长 → {local} 在同 step 内可读写。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(TIMER_STRATEGY), _asm("timer_probe"),
                     port, catalog=CAT)
    # t=0 起表（else 分支；幂等 = 后续帧重执行不归零）；t=1 elapsed>=1 → burst=1；
    # t=2 burst=2；t=3 burst>=2 → exit_strategy READ
    eng.on_game_state(_gs(0, 4, 0.0))
    eng.on_game_state(_gs(1, 4, 1.0))
    eng.on_game_state(_gs(2, 4, 2.0))
    eng.on_game_state(_gs(3, 4, 3.0))
    snap = eng.snapshot()
    assert snap["exit_record"] == {"kind": "done", "reason": "READ"}
    assert snap["locals"] == {"burst": 2.0}
    assert snap["timers"] == {"raid": 3.0}


def test_stop_timer_freezes_elapsed():
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(TIMER_STRATEGY.replace(
        "- do: [{op: start_timer, name: raid}]",
        "- do: [{op: start_timer, name: raid}, {op: stop_timer, name: raid}]")),
        _asm("timer_probe"), port, catalog=CAT)
    # 表起即停：elapsed 冻结在 0，永远到不了 3 —— 留在 wait（未启动语义的镜像：停着的表不计时）
    for seq, t in [(0, 0.0), (1, 5.0), (2, 30.0)]:
        eng.on_game_state(_gs(seq, 4, t))
    snap = eng.snapshot()
    assert snap["active_step"] == "wait" and not snap["done"]
    assert snap["timers"] == {"raid": 0.0}


def test_locals_reset_on_step_entry():
    """换 step 清空 locals（spec-003 §3.2）：上个 step 写的值不进新 step，{local} 读到 None。"""
    port = FakeGamePort(script=[])
    st = """
id: timer_probe
group_slots: [main]
params: {}
variables: {}
initial_step: wait
steps:
  - step_id: wait
    locals: [burst]
    branches:
      - when: {op: ">=", args: [{op: timer_elapsed, name: raid}, {const: 1.0}]}
        do: [{op: set_local, name: burst, value: {const: 7}},
             {op: exit_step, kind: done, reason: GO}]
      - do: [{op: start_timer, name: raid}]
  - step_id: after
    locals: [burst]
    branches:
      - when: {op: "==", args: [{local: burst}, {const: 7}]}
        do: [{op: exit_strategy, kind: failed, reason: LEAKED}]
      - do: [{op: exit_strategy, kind: done, reason: CLEAN}]
edges:
  - {from: wait, to: after, kind: done, reason: GO}
"""
    eng = FlowEngine(parse_strategy(st), _asm("timer_probe"), port, catalog=CAT)
    eng.on_game_state(_gs(0, 4, 0.0))
    eng.on_game_state(_gs(1, 4, 1.0))
    eng.on_game_state(_gs(2, 4, 2.0))
    snap = eng.snapshot()
    # wait 里写过的 burst=7 不该漏进 after（局部 = step 作用域；转场即清空）
    assert snap["exit_record"] == {"kind": "done", "reason": "CLEAN"}
    assert snap["locals"] == {}


COMBAT_STRATEGY = """
id: combat_probe
version: 1
group_slots: [main]
params: {}
variables: {}
initial_step: watch
steps:
  - step_id: watch
    branches:
      - when: {op: under_attack, group: main}
        do: [{op: exit_strategy, kind: failed, reason: HIT}]
      - when: {op: engaged, group: main}
        do: [{op: exit_step, kind: done, reason: FIGHT}]
      - do: []
  - step_id: fight
    branches:
      - do: [{op: exit_strategy, kind: done, reason: OK}]
edges:
  - {from: watch, to: fight, kind: done, reason: FIGHT}
"""


def _gs_with_enemy(seq: int, t: float, *, enemy_pos=None, hp: float = 45.0,
                   attack_order: bool = False):
    gs = _gs(seq, 4, t)
    for u in gs.units:
        u.hp = hp
        if attack_order:
            from game import Order
            u.orders = [Order(ability="Attack", target_pos=Point2(9.0, 9.0))]
    if enemy_pos is not None:
        from game import Order as _O
        gs.units.append(Unit(tag=900, type_name="ZERGLING", position=enemy_pos,
                             owner=Owner.ENEMY, hp=35.0, hp_max=35.0, shield=0.0,
                             energy=0.0, build_progress=1.0, orders=[_O(ability="Attack")]))
    return gs


def test_engaged_via_enemy_in_range():
    """敌人贴脸（射程+缓冲内）→ engaged 成立 → 转场。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(COMBAT_STRATEGY), _asm("combat_probe"),
                     port, catalog=CAT)
    eng.on_game_state(_gs_with_enemy(0, 0.0))                 # 无敌：等待
    assert eng.snapshot()["active_step"] == "watch"
    eng.on_game_state(_gs_with_enemy(1, 1.0, enemy_pos=Point2(1.5, 0.0)))  # 敌入射程
    assert eng.snapshot()["active_step"] == "fight"


def test_engaged_via_attack_order_even_at_range():
    """远敌但有攻击命令 → engaged（在打）成立。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(COMBAT_STRATEGY), _asm("combat_probe"),
                     port, catalog=CAT)
    eng.on_game_state(_gs_with_enemy(0, 0.0, attack_order=True))
    assert eng.snapshot()["active_step"] == "fight"


def test_under_attack_via_hp_drop_within_window():
    """掉血后 5s 内算受击；窗口过后不再算（hp 历史推导）。"""
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(COMBAT_STRATEGY), _asm("combat_probe"),
                     port, catalog=CAT)
    eng.on_game_state(_gs_with_enemy(0, 0.0))                    # 基线快照
    eng.on_game_state(_gs_with_enemy(1, 1.0, hp=30.0))           # 掉血（帧尾记账）
    assert eng.snapshot()["active_step"] == "watch"              # 本帧还看不见（上一帧为准）
    eng.on_game_state(_gs_with_enemy(2, 2.0, hp=30.0))           # 下一帧起窗口内 → HIT
    snap = eng.snapshot()
    assert snap["exit_record"] == {"kind": "failed", "reason": "HIT"}

    # 另一局：掉血后过了窗口（无新伤）→ under_attack False，无敌也不 engaged → 留在 watch
    eng2 = FlowEngine(parse_strategy(COMBAT_STRATEGY), _asm("combat_probe"),
                      port, catalog=CAT)
    eng2.on_game_state(_gs_with_enemy(0, 0.0))
    eng2.on_game_state(_gs_with_enemy(1, 10.0, hp=30.0))         # 掉血发生在 t=10
    eng2.on_game_state(_gs_with_enemy(2, 16.0, hp=30.0))         # 6s 后，窗口已过
    snap2 = eng2.snapshot()
    assert snap2["active_step"] == "watch" and not snap2["done"]


# ---------------- 热切 V1（批 C，2026-08-23）：续位的 locals/timers 语义 ----------------

def _swap_gs(seq, t):
    """带 1 个工人的最小 GameState（引擎只求值，不产 op）。"""
    from game import GameState, Grid, Owner, Point2, Unit

    g = Grid(1, 1, [[0]])
    u = Unit(tag=1, type_name="SCV", position=Point2(0, 0), owner=Owner.SELF,
             hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)
    return GameState(seq=seq, game_time=t, minerals=0, vespene=0,
                     supply_used=1, supply_cap=10, units=[u],
                     map_size=(176, 160), creep=g, visibility=g)


_A = """
id: a
group_slots: [main]
variables: {}
initial_step: s
steps:
  - step_id: s
    locals: [x]
    branches:
      - do:
          - {op: set_local, name: x, value: 7}
          - {op: start_timer, name: t}
"""

_B_SAME_STEP = """
id: b
group_slots: [main]
variables: {}
initial_step: s
steps:
  - step_id: s
    locals: [x]
    branches:
      - do: []
"""

_C_OTHER_STEP = """
id: c
group_slots: [main]
variables: {}
initial_step: w
steps:
  - step_id: w
    branches:
      - do: []
"""

_SWAP_ASM = """
id: swap_asm
groups:
  - group_id: G1
    composition: {terran/scv: {min: 1, target: 1, max: 1}}
strategy_instances:
  - instance_id: s1
    strategy_ref: a
    bindings: {main: G1}
    params: {}
"""


def test_swap_same_step_keeps_locals_and_timers():
    """续位的可证语义：同名 step 的 locals/timers 原样保留；异名重起清零。"""
    from driver.fake import FakeGamePort
    from game.catalog import load_all

    eng = FlowEngine(parse_strategy(_A), parse_assembly(_SWAP_ASM),
                     FakeGamePort(script=[]), catalog=load_all())
    eng.on_game_state(_swap_gs(0, 0.0))
    assert eng.snapshot()["locals"] == {"x": 7}
    assert "t" in eng.snapshot()["timers"]

    # 同名 step 续位：locals/timers 保留
    eng.swap_strategy(parse_strategy(_B_SAME_STEP))
    eng.on_game_state(_swap_gs(1, 5.0))
    snap = eng.snapshot()
    assert snap["active_step"] == "s"
    assert snap["locals"] == {"x": 7}
    assert snap["timers"]["t"] == 5.0        # 表还在走（没被 swap 归零）

    # 异名重起：locals/timers 清零、从 initial_step 起
    eng.swap_strategy(parse_strategy(_C_OTHER_STEP))
    eng.on_game_state(_swap_gs(2, 6.0))
    snap = eng.snapshot()
    assert snap["active_step"] == "w"
    assert snap["locals"] == {}
    assert snap["timers"] == {}


def test_swap_clears_dedup_keys_and_revives_done():
    """去重键清空（新策略首帧不被旧签名吞）+ 对已结束策略 swap = 复活。"""
    from driver.fake import FakeGamePort
    from game.catalog import load_all

    done_doc = """
id: d
group_slots: [main]
variables: {}
initial_step: fin
steps:
  - step_id: fin
    branches:
      - do: [{op: exit_strategy, kind: done, reason: DONE}]
"""
    asm_d = _SWAP_ASM.replace("strategy_ref: a", "strategy_ref: d")
    eng = FlowEngine(parse_strategy(done_doc), parse_assembly(asm_d),
                     FakeGamePort(script=[]), catalog=load_all())
    eng.on_game_state(_swap_gs(0, 0.0))
    assert eng.snapshot()["done"] is True
    eng._last_emitted[("main", "terran/marine", "attack_move_to")] = (("stale",), "pkey")

    eng.swap_strategy(parse_strategy(_B_SAME_STEP))
    eng.on_game_state(_swap_gs(1, 1.0))
    snap = eng.snapshot()
    assert snap["done"] is False and snap["active_step"] == "s"
    assert eng._last_emitted == {}
