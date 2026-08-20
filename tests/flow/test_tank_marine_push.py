"""步坦协同（循环蛙跳推进）离线确定性验证：T5 循环版。

循环 4 步（tank_hop→siege_gate→inf_hop→threat_gate→回 tank_hop），用 T1 siege +
T4 point_toward 动态点 + 去重量化。脚本化帧序列走 2 轮完整循环，断言：
- 组心每轮逼近 target（前沿点 10.4 → 20.8，≈ tank_cover 前进量）
- siege 仅在坦克入 target 10.4 内那一轮发（轮 1 SKIP 不发、轮 2 发）
- 脱队停等出现（inf_wait）
- 威胁大时卡 threat_gate、威胁解除继续
- 最终 arrived → done
- op 含 siege + 同前沿点 attack_move 不重发（去重量化；微移去重见 test_engine.T4）
"""
from pathlib import Path

import yaml

from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game import GameState, Grid, Owner, Point2, Unit

_DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "tank_marine_push.yaml"
_DATA = yaml.safe_load(_DOC.read_text(encoding="utf-8"))
# 测试用近目标（2 轮可达：0 → 10.4 → 20.8，距 [20,0] 0.8 < 8）；yaml 默认 [127.5,119.5] 留真机
_DATA["assembly"]["strategy_instances"][0]["params"]["target"] = [20.0, 0.0]
STRATEGY = yaml.safe_dump(_DATA["strategy"], sort_keys=False, allow_unicode=True)
ASSEMBLY = yaml.safe_dump(_DATA["assembly"], sort_keys=False, allow_unicode=True)

TARGET = Point2(20.0, 0.0)  # tank_cover=10.4；前沿点轮1=(10.4,0)，轮2=(20.8,0)


def _unit(tag, type_name, pos, owner=Owner.SELF):
    return Unit(tag=tag, type_name=type_name, position=pos, owner=owner,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(seq, inf_pos, tank_pos, enemies=()):
    # 20 步兵同位 → group_center(inf)=inf_pos；4 坦克同位 → group_center(armor)=tank_pos
    units = [_unit(100 + i, "MARINE", inf_pos) for i in range(20)]
    units += [_unit(200 + i, "SIEGETANK", tank_pos) for i in range(4)]
    for i, p in enumerate(enemies):
        units.append(_unit(300 + i, "ZERGLING", p, owner=Owner.ENEMY))
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq), minerals=0, vespene=0,
                     supply_used=24, supply_cap=100, units=units,
                     map_size=(176, 160), creep=g, visibility=g)


def _ops(port, action):
    return [o for o in port.submitted if o.action == action]


def test_tank_marine_loop_two_rounds_then_arrived():
    port = FakeGamePort(script=[])
    eng = FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY), port)

    # formup：20 步兵 + 4 坦克就绪 → FORMED → tank_hop
    eng.on_game_state(_gs(0, Point2(0, 0), Point2(0, 0)))
    assert eng._active_step == "tank_hop"

    # ===== 轮 1（离目标远，无 siege）=====
    eng.on_game_state(_gs(1, Point2(0, 0), Point2(0, 0)))      # tank_hop：坦克已在步兵组心 → IN_PLACE
    assert eng._active_step == "siege_gate"
    eng.on_game_state(_gs(2, Point2(0, 0), Point2(0, 0)))      # siege_gate：距目标 20 > 10.4 → SKIP
    assert eng._active_step == "inf_hop"
    assert len(_ops(port, "siege")) == 0                        # 轮 1 不架起（未入 10.4）
    eng.on_game_state(_gs(3, Point2(0, 0), Point2(0, 0)))      # inf_hop：前沿点 (10.4,0)，未到 → attack_move
    assert eng._active_step == "inf_hop"
    assert len(_ops(port, "attack_move_to")) == 1
    # 脱队：步兵跑到 (0,13)（距坦克 13>12）→ STRAGGLED → inf_wait
    eng.on_game_state(_gs(4, Point2(0, 13), Point2(0, 0)))
    assert eng._active_step == "inf_wait"
    # 停等 hold_secs=3：step_elapsed 1/2 停、3 续（hold 同参去重，只发 1 条）
    eng.on_game_state(_gs(5, Point2(0, 13), Point2(0, 0)))
    assert eng._active_step == "inf_wait"
    eng.on_game_state(_gs(6, Point2(0, 13), Point2(0, 0)))
    assert eng._active_step == "inf_wait"
    eng.on_game_state(_gs(7, Point2(0, 13), Point2(0, 0)))
    assert eng._active_step == "inf_hop"                        # RESUME
    # 步兵到前沿点 (10.4,0) → IN_PLACE → threat_gate
    eng.on_game_state(_gs(8, Point2(10.4, 0), Point2(0, 0)))
    assert eng._active_step == "threat_gate"
    # 威胁大（5 敌近前沿点）→ 卡 threat_gate；attack_move 同前沿点被去重（仍 1 条）
    near5 = [Point2(11 + i, 0) for i in range(5)]
    eng.on_game_state(_gs(9, Point2(10.4, 0), Point2(0, 0), near5))
    assert eng._active_step == "threat_gate"                   # 威胁大不推进
    assert len(_ops(port, "attack_move_to")) == 1              # 同前沿点 (10.4,0) 去重，不重发
    # 威胁解除（无近敌）→ CLEARED → tank_hop（轮 2）
    eng.on_game_state(_gs(10, Point2(10.4, 0), Point2(0, 0)))
    assert eng._active_step == "tank_hop"

    # ===== 轮 2（坦克入 10.4 内，发 siege）=====
    eng.on_game_state(_gs(11, Point2(10.4, 0), Point2(10.4, 0)))  # tank_hop：坦克→步兵组心 → IN_PLACE
    assert eng._active_step == "siege_gate"
    eng.on_game_state(_gs(12, Point2(10.4, 0), Point2(10.4, 0)))  # siege_gate：距目标 9.6 ≤ 10.4 → SIEGE
    assert eng._active_step == "inf_hop"
    assert len(_ops(port, "siege")) == 1                          # siege 仅此轮发（入 10.4 内）
    eng.on_game_state(_gs(13, Point2(10.4, 0), Point2(10.4, 0)))  # inf_hop：前沿点 (20.8,0)，未到 → attack_move
    assert eng._active_step == "inf_hop"
    assert len(_ops(port, "attack_move_to")) == 2                 # 第二个前沿点 (20.8,0)
    # 步兵到 (20.8,0)：距目标 0.8 < 8 → ARRIVED → done
    eng.on_game_state(_gs(14, Point2(20.8, 0), Point2(10.4, 0)))
    assert eng._done is True

    # ===== op 汇总断言 =====
    attacks = _ops(port, "attack_move_to")
    sieges = _ops(port, "siege")
    holds = _ops(port, "hold_position")
    # 组心每轮逼近：前沿点 (10.4,0) → (20.8,0)，每轮 ≈ tank_cover(10.4)
    assert [a.params["position"] for a in attacks] == [[10.4, 0.0], [20.8, 0.0]]
    assert len(sieges) == 1                                  # siege 仅轮 2（入 10.4 内才发）
    assert all(len(o.unit_tags) == 4 for o in sieges)        # siege 作用于 4 坦克
    assert len(holds) == 1                                  # 脱队停等 hold（同参去重）
    assert all(len(o.unit_tags) == 20 for o in attacks)      # attack 作用于 20 步兵
