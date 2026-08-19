"""步坦协同（蛙跳推进）离线确定性验证：现有 flow 词表表达复杂协同节奏。

节奏（3 跳 + 总攻，与 docs/tank_marine_push.yaml 同源）：
- formup：20 步兵 + 4 坦克就绪 → 第 1 跳
- hopX_tank：坦克推进到枪兵上一位（R：先让坦克推进到枪兵身边驻扎）
- hopX_siege：距下一目标 ≤ 架起射程 80%（tank_cover=10.4）才驻扎（hold 占位，真机换 siege）
- hopX_inf：步兵 attack_move 到坦克覆盖内的下一点；距坦克 > inf_hold_dist → STRAGGLED →
  hopX_wait 停 hold_secs（step_elapsed 计时）再续进（R：脱队停等）
- hopX_clear：enemy_count_near < threat_limit 且 group_hp_ratio > 0.6 → 下一跳（R：威胁小才推进）
- final_assault：总攻敌方主矿 → arrived → exit_strategy ARRIVED
"""
from pathlib import Path

import yaml

from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game import GameState, Grid, Owner, Point2, Unit
from tactical_map import BigRegion, RegionLayer
from tactical_map.placement import PosMark

_DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "tank_marine_push.yaml"
_DATA = yaml.safe_load(_DOC.read_text(encoding="utf-8"))
STRATEGY = yaml.safe_dump(_DATA["strategy"], sort_keys=False, allow_unicode=True)
ASSEMBLY = yaml.safe_dump(_DATA["assembly"], sort_keys=False, allow_unicode=True)

P = {"p1": Point2(70, 50), "p2": Point2(77, 57), "p3": Point2(84, 64), "p4": Point2(91, 71)}
TARGET = Point2(127.5, 119.5)
INF0, TANK0 = Point2(60, 40), Point2(62, 42)


def _layer():
    return RegionLayer(
        map_name="push_test",
        size=(176, 160),
        big_grid=Grid(176, 160, [[1] * 176 for _ in range(160)]),
        big_index={1: "field"},
        big_regions={"field": BigRegion(stable_id="field", anchor=Point2(88, 80))},
        pos_marks={name: PosMark(name, pos) for name, pos in P.items()},
    )


def _unit(tag, type_name, pos, owner=Owner.SELF):
    return Unit(tag=tag, type_name=type_name, position=pos, owner=owner,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(seq, inf_pos, tank_pos, enemies=()):
    units = [_unit(100 + i, "MARINE", inf_pos) for i in range(20)]
    units += [_unit(200 + i, "SIEGETANK", tank_pos) for i in range(4)]
    for i, p in enumerate(enemies):
        units.append(_unit(300 + i, "ZERGLING", p, owner=Owner.ENEMY))
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq), minerals=0, vespene=0,
                     supply_used=24, supply_cap=100, units=units,
                     map_size=(176, 160), creep=g, visibility=g)


def test_tank_marine_leapfrog_full_chain():
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY), port, region_layer=_layer())

    # formup：20 步兵 + 4 坦克就绪 → FORMED → hop1_tank
    eng.on_game_state(_gs(0, INF0, TANK0))
    assert eng._active_step == "hop1_tank"
    # 坦克推进到 p1（R：先坦克）
    eng.on_game_state(_gs(1, INF0, TANK0))
    assert eng._active_step == "hop1_tank"
    eng.on_game_state(_gs(2, INF0, P["p1"]))  # 坦克到 p1 → IN_PLACE
    assert eng._active_step == "hop1_siege"
    # 驻扎：距 p2 9.9 ≤ 架起射程 80%（10.4）→ SIEGED
    eng.on_game_state(_gs(3, INF0, P["p1"]))
    assert eng._active_step == "hop1_inf"
    # 步兵脱队（距坦克 14.1 > 12）→ STRAGGLED → 停 3 秒（step_elapsed 计时）
    eng.on_game_state(_gs(4, INF0, P["p1"]))
    assert eng._active_step == "hop1_wait"
    for s in (5, 6, 7):
        eng.on_game_state(_gs(s, INF0, P["p1"]))
    assert eng._active_step == "hop1_inf"  # RESUME
    # 步兵跟上（距坦克 5.7 ≤ 12）→ attack_move 到 p2 → 到位 → 清理评估
    eng.on_game_state(_gs(8, Point2(74, 54), P["p1"]))
    assert eng._active_step == "hop1_inf"
    eng.on_game_state(_gs(9, P["p2"], P["p1"]))
    assert eng._active_step == "hop1_clear"
    # 威胁大（5 敌近）→ 不推进，留在清理
    near5 = [Point2(78 + i * 0.3, 58) for i in range(5)]
    eng.on_game_state(_gs(10, P["p2"], P["p1"], near5))
    assert eng._active_step == "hop1_clear"
    # 威胁小 → 下一跳（R：威胁小才推进）
    eng.on_game_state(_gs(11, P["p2"], P["p1"], [Point2(90, 90)]))
    assert eng._active_step == "hop2_tank"

    # 第 2 跳：坦克推进到枪兵身边 p2 驻扎，枪兵 → p3
    eng.on_game_state(_gs(12, P["p2"], P["p1"]))
    eng.on_game_state(_gs(13, P["p2"], P["p2"]))
    eng.on_game_state(_gs(14, P["p2"], P["p2"]))
    assert eng._active_step == "hop2_inf"
    eng.on_game_state(_gs(15, P["p2"], P["p2"]))
    eng.on_game_state(_gs(16, P["p3"], P["p2"]))
    assert eng._active_step == "hop2_clear"
    eng.on_game_state(_gs(17, P["p3"], P["p2"]))
    assert eng._active_step == "hop3_tank"

    # 第 3 跳：坦克 → p3，枪兵 → p4
    eng.on_game_state(_gs(18, P["p3"], P["p2"]))
    eng.on_game_state(_gs(19, P["p3"], P["p3"]))
    eng.on_game_state(_gs(20, P["p3"], P["p3"]))
    assert eng._active_step == "hop3_inf"
    eng.on_game_state(_gs(21, P["p3"], P["p3"]))
    eng.on_game_state(_gs(22, P["p4"], P["p3"]))
    assert eng._active_step == "hop3_clear"
    eng.on_game_state(_gs(23, P["p4"], P["p3"]))
    assert eng._active_step == "final_assault"

    # 总攻：坦克最后在 p4 掩护（arrived 优先于脱队判定——完整部署里点位链铺到目标，
    # 总攻全程覆盖；示例在 p4 收尾）→ arrived → done
    eng.on_game_state(_gs(24, P["p4"], P["p4"]))
    assert eng._active_step == "final_assault" and eng._done is False
    eng.on_game_state(_gs(25, Point2(124, 116), P["p4"]))
    assert eng._done is True

    # op 汇总：3 次坦克 move（p1/p2/p3）+ 4 次步兵 attack_move（p2/p3/p4/敌方主矿）
    # + 2 次 hold（坦克驻扎、步兵停等；同类同参被去重）
    moves = [o for o in port.submitted if o.action == "move_to"]
    attacks = [o for o in port.submitted if o.action == "attack_move_to"]
    holds = [o for o in port.submitted if o.action == "hold_position"]
    assert len(moves) == 3
    assert len(attacks) == 4
    assert len(holds) == 2
    # 坦克 move 只作用于坦克组（4 个 tag），步兵 attack 只作用于步兵组（20 个 tag）
    assert all(len(o.unit_tags) == 4 for o in moves)
    assert all(len(o.unit_tags) == 20 for o in attacks)

