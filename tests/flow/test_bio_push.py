"""bio_push 全战术链：formup→advance→combat→advance(loop)→retreat→hold。

脚本化 GameState 逐帧触发各 step 转移，验多谓词（group_count/enemy_count_near/
group_hp_ratio/arrived/region_center）+ 动作去重 + exit_strategy。
"""
from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game.catalog import load_terran
from game import GameState, Grid, Owner, Point2, Unit
from tactical_map import BigRegion, RegionLayer

CAT = load_terran()

BIO_STRATEGY = """
id: bio_push_test
version: 1
group_slots: [main]
params: {min_units: {type: int, default: 6}}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, args: [main]}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: ">", args: [{op: enemy_count_near, args: [{op: group_center, args: [main]}, 15]}, 8]}
        do: [{op: exit_step, kind: interrupted, reason: AMBUSHED}]
      - when: {op: "<", args: [{op: group_hp_ratio, args: [main]}, 0.4]}
        do: [{op: exit_step, kind: failed, reason: UNITS_LOST}]
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: attack_move_to, params: {position: [50.0, 50.0]}}
  - step_id: combat
    branches:
      - when: {op: "==", args: [{op: enemy_count_near, args: [{op: group_center, args: [main]}, 12]}, 0]}
        do: [{op: exit_step, kind: done, reason: ENEMY_CLEARED}]
      - when: {op: "<", args: [{op: group_count, args: [main, terran/marine]}, 6]}
        do: [{op: exit_step, kind: failed, reason: UNITS_LOST}]
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: focus_fire, params: {target_unit: 200}}
  - step_id: retreat
    branches:
      - when: {op: arrived, args: [main, {op: region_center, args: [main_base]}, 8]}
        do: [{op: exit_step, kind: done, reason: SAFE}]
      - do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [50.0, 50.0]}}
  - step_id: hold
    branches:
      - do: [{op: exit_strategy, kind: done, reason: SAFE}]
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
  - {from: advance, to: combat, kind: interrupted, reason: AMBUSHED}
  - {from: combat, to: advance, kind: done, reason: ENEMY_CLEARED}
  - {from: advance, to: retreat, kind: failed, reason: UNITS_LOST}
  - {from: combat, to: retreat, kind: failed, reason: UNITS_LOST}
  - {from: retreat, to: hold, kind: done, reason: SAFE}
on_exit: release
"""

BIO_ASSEMBLY = """
id: bio_assembly
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 6, target: 6, max: 6}
strategy_instances:
  - instance_id: s1
    strategy_ref: bio_push_test
    bindings: {main: G1}
    params: {}
"""


def _gs_bio(seq, marines, enemies):
    units = []
    for tag, (x, y), hp in marines:
        units.append(Unit(tag=tag, type_name="MARINE", position=Point2(x, y), owner=Owner.SELF,
                          hp=hp, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0))
    for tag, (x, y) in enemies:
        units.append(Unit(tag=tag, type_name="ZERGLING", position=Point2(x, y), owner=Owner.ENEMY,
                          hp=35.0, hp_max=35.0, shield=0.0, energy=0.0, build_progress=1.0))
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq), minerals=50, vespene=0,
                     supply_used=len(marines), supply_cap=20, units=units,
                     map_size=(176, 160), creep=g, visibility=g)


def test_bio_push_full_tactical_chain():
    # 区域模型：整图一个大区 main_base（锚点 50,50），retreat 的 region_center(main_base) 用它
    layer = RegionLayer(
        map_name="bio_test",
        size=(176, 160),
        big_grid=Grid(176, 160, [[1] * 176 for _ in range(160)]),
        big_index={1: "main_base"},
        big_regions={"main_base": BigRegion(stable_id="main_base", anchor=Point2(50, 50))},
    )
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(BIO_STRATEGY), parse_assembly(BIO_ASSEMBLY), port, region_layer=layer, catalog=CAT)

    M = [(100 + i, (0, 0), 45.0) for i in range(6)]  # 6 marines @ (0,0) hp45
    Mlow = [(t, (0, 0), 10.0) for t, _, _ in M]  # 低血
    Mnear = [(t, (49, 49), 10.0) for t, _, _ in M]  # 近 main_base
    E9 = [(200 + i, (10, i)) for i in range(9)]  # 9 敌 @ x=10,y=0..8（距 (0,0) ≤15）

    eng.on_game_state(_gs_bio(0, M[:5], []))  # 5 marine -> formup 等
    assert eng._active_step == "formup"
    eng.on_game_state(_gs_bio(1, M[:5], []))
    assert eng._active_step == "formup"
    eng.on_game_state(_gs_bio(2, M, []))  # 6 marine -> FORMED -> advance
    assert eng._active_step == "advance"
    eng.on_game_state(_gs_bio(3, M, []))  # advance 无敌满血 -> attack_move_to
    assert len([o for o in port.submitted if o.action == "attack_move_to"]) == 1
    eng.on_game_state(_gs_bio(4, M, E9))  # 9 敵近 -> AMBUSHED -> combat
    assert eng._active_step == "combat"
    eng.on_game_state(_gs_bio(5, M, E9))  # combat 有敵、marine=6 -> focus_fire
    assert len([o for o in port.submitted if o.action == "focus_fire"]) == 1
    eng.on_game_state(_gs_bio(6, M, []))  # 无敵 -> ENEMY_CLEARED -> advance(loop)
    assert eng._active_step == "advance"
    eng.on_game_state(_gs_bio(7, Mlow, []))  # 低血 -> UNITS_LOST -> retreat
    assert eng._active_step == "retreat"
    eng.on_game_state(_gs_bio(8, Mnear, []))  # 近 main_base -> SAFE -> hold
    assert eng._active_step == "hold"
    eng.on_game_state(_gs_bio(9, Mnear, []))  # hold -> exit_strategy
    assert eng._done is True

    # 总 op：1 attack_move_to(f3) + 1 focus_fire(f5)；f6 move 去重、f8 retreat exit 无 move
    assert len(port.submitted) == 2, f"expected 2 ops, got {len(port.submitted)}: {port.submitted}"
