"""B1 读模型：FlowEngine / Allocator / ProductionRuntime 的 snapshot() 与对应帧。

守的核心是两条：
1. **观测必须在当场记**：命中分支与转移原因事后从外部推断不出来；
2. **状态判定只有一份实现**：refill_state 与 refresh 同源、队列项状态由队首门控语义派生，
   UI 显示的东西不可能与引擎实际行为对不上。
"""
import pytest

from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game import GameState, Grid, Operation, Owner, Point2, Unit
from game.catalog import load_terran
from game.production import PlacementExact, QueueItem, QueueOp
from production.runtime import ProductionRuntime

from view.adapt import flow_frame, group_of_from_flow, ops_frame, production_frame, with_waited
from view.encode import to_json
from view.port import OpRing, RecordingPort

CAT = load_terran()

STRATEGY = """
id: obs_probe
version: 3
group_slots: [main]
params:
  min_units: {type: int, default: 2}
variables:
  checkpoint: {type: point, default: null}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - branch_id: b_ready
        when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - branch_id: b_wait
        do: []
  - step_id: advance
    branches:
      - branch_id: b_go
        do:
          - {op: group_action, group_slot: main, type: terran/marine, action_atom: move_to, params: {position: [10.0, 10.0]}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
loop_limits: {max_step_transitions: 50}
"""

ASSEMBLY = """
id: obs_assembly
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 2, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: obs_probe
    bindings: {main: G1}
    params: {}
"""


def _marine(tag: int, x: float) -> Unit:
    return Unit(tag=tag, type_name="MARINE", position=Point2(x, 0.0), owner=Owner.SELF,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(seq: int, t: float, units: list[Unit], **kw) -> GameState:
    g = Grid(1, 1, [[0]])
    base = dict(minerals=50, vespene=0, supply_used=len(units), supply_cap=20)
    base.update(kw)
    return GameState(seq=seq, game_time=t, units=units, map_size=(176, 160),
                     creep=g, visibility=g, **base)


def _engine():
    return FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY),
                      FakeGamePort(script=[]), catalog=CAT)


# ---------------- FlowEngine.snapshot ----------------

def test_snapshot_records_branch_hit_in_the_frame_it_happened():
    """命中分支必须当场记 —— 它不落到任何持久状态里，事后推不出来。"""
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0)]))     # 1 个兵 < min → 等待分支
    s = eng.snapshot()
    assert s["active_step"] == "formup"
    assert s["branch_hit"] == {"step_id": "formup", "branch_id": "b_wait", "index": 1}


def test_snapshot_branch_hit_is_none_when_nothing_matched():
    """没有命中分支就是 None（等待型 step 的真实状态），不留上一帧的残影。"""
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0), _marine(2, 1)]))  # 命中 b_ready → 转移
    assert eng.snapshot()["branch_hit"]["branch_id"] == "b_ready"
    eng._done = True                      # 模拟结束：on_game_state 直接 return
    eng.on_game_state(_gs(1, 1.0, []))
    assert eng.snapshot()["branch_hit"]["branch_id"] == "b_ready"  # 未重新求值 → 保留


def test_snapshot_records_transition_with_reason():
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0), _marine(2, 1)]))   # formup → advance
    s = eng.snapshot()
    assert s["active_step"] == "advance"
    assert s["transition_count"] == 1
    assert s["transitions"] == [
        {"from_step": "formup", "to": "advance", "kind": "done", "reason": "FORMED", "at": 0.0}
    ]
    assert s["transition_limit"] == 50


def test_step_entry_count_is_per_step_not_cumulative():
    """契约要的是"第几次进入**该** step"（环上才有意义），不是累计转移数。"""
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0), _marine(2, 1)]))
    s = eng.snapshot()
    assert s["step_entry_count"] == 1          # advance 第 1 次
    assert s["transition_count"] == 1


def test_snapshot_exposes_eval_diagnostics():
    """引擎记的"条件其实没求出来"必须能被 UI 看见（不静默）。"""
    eng = _engine()
    eng.eval_diagnostics[("formup", "none_compare", "group_center")] = 3
    d = eng.snapshot()["eval_diagnostics"]
    assert d == [{"step_id": "formup", "kind": "none_compare", "detail": "group_center", "count": 3}]


def test_no_private_field_access_needed():
    """读模型覆盖契约要的每一个字段 —— 外部不需要碰任何 `_` 字段。"""
    s = _engine().snapshot()
    for key in ("instance_id", "strategy_ref", "version", "params", "variables", "locals",
                "definitions", "active_step", "step_entered_at", "step_elapsed",
                "step_entry_count", "branch_hit", "transitions", "transition_count",
                "transition_limit", "done", "exit_record", "bindings",
                "eval_diagnostics", "groups"):
        assert key in s, f"snapshot 缺 {key}"


# ---------------- Allocator.snapshot ----------------

@pytest.mark.parametrize(
    "count,expected",
    [(0, "补兵中"), (1, "补兵中"), (2, "滞回区"), (3, "滞回区"), (4, "满足")],
)
def test_refill_state_matches_hysteresis_rule(count, expected):
    """min=2 target=4：<2 补兵中；[2,4) 滞回区；>=4 满足。

    判定与 `Allocator.refresh` 同源（同一套 floor/cap/need），所以 UI 显示的状态
    不可能和引擎实际行为对不上。
    """
    eng = _engine()
    units = [_marine(10 + i, float(i)) for i in range(count)]
    eng.on_game_state(_gs(0, 0.0, units))
    g = eng.snapshot()["groups"][0]
    assert g["composition"]["terran/marine"]["current"] == count
    assert g["refill_state"] == expected


def test_group_center_and_hp_from_real_units_not_guessed():
    eng = _engine()
    units = [_marine(1, 0.0), _marine(2, 4.0)]
    gs = _gs(0, 0.0, units)
    eng.on_game_state(gs)
    f = flow_frame(eng.snapshot(), gs)
    grp = f.groups[0]
    assert grp.center == (2.0, 0.0)
    assert grp.hp_ratio == 1.0
    assert grp.leased_tags == [1, 2]


def test_flow_frame_without_gamestate_gives_none_center():
    """没有 gs 就不算组心（不猜）。"""
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0.0)]))
    f = flow_frame(eng.snapshot())
    assert f.groups[0].center is None and f.groups[0].hp_ratio is None


def test_flow_frame_strategies_is_a_list_even_with_one_instance():
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0.0)]))
    f = flow_frame(eng.snapshot())
    assert isinstance(f.strategies, list) and len(f.strategies) == 1
    assert to_json(f)["strategies"][0]["instance_id"] == "s1"


def test_group_of_join_feeds_world_frame():
    eng = _engine()
    eng.on_game_state(_gs(0, 0.0, [_marine(1, 0.0), _marine(2, 1.0)]))
    assert group_of_from_flow(eng.snapshot()) == {1: "G1", 2: "G1"}


# ---------------- ProductionRuntime.snapshot ----------------

def _runtime():
    return ProductionRuntime(CAT, FakeGamePort(script=[]))


def test_blocked_head_freezes_queue_and_reports_reason():
    """队首缺矿 → 队首阻塞 + 原因；后续项一律"未处理"（队首门控语义）。"""
    rt = _runtime()
    rt.submit_queue("main", [
        QueueItem(op=QueueOp.BUILD, type="terran/barracks", placement=PlacementExact(mark="rax_1")),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=3),
    ])
    rt.on_game_state(_gs(0, 100.0, [], minerals=0))
    snap = rt.snapshot()
    q = snap["queues"][0]
    assert q["head_status"] == "阻塞"
    assert q["items"][0]["status"] == "队首阻塞"
    assert "晶体矿" in q["items"][0]["block_reason"]
    assert q["items"][1]["status"] == "未处理"
    assert q["items"][1]["block_reason"] is None
    assert q["blocked"]["since"] == 100.0 and q["blocked"]["warned"] is False


def test_production_frame_waited_computed_from_current_time():
    """`since` 在快照里，"现在"只有 caller 知道 → waited 由 with_waited 算。"""
    rt = _runtime()
    rt.submit_queue("main", [QueueItem(op=QueueOp.TRAIN, type="terran/marine")])
    rt.on_game_state(_gs(0, 100.0, [], minerals=0))
    f = with_waited(production_frame(rt.snapshot(), CAT), game_time=142.0)
    assert f.queues[0].blocked is not None
    assert f.queues[0].blocked.waited == 42.0


def test_empty_queue_reports_empty_head():
    rt = _runtime()
    rt.submit_queue("main", [])
    rt.on_game_state(_gs(0, 0.0, []))
    assert rt.snapshot()["queues"][0]["head_status"] == "空"


def test_unsupported_op_dropped_with_reason_and_no_timestamp():
    """research 不支持 → 进 dropped 带原因；后端没记时间 → at=None（UI 显示"未知"，不编时间）。"""
    rt = _runtime()
    rt.submit_queue("main", [QueueItem(op=QueueOp.RESEARCH, type="terran/marine")])
    rt.on_game_state(_gs(0, 5.0, []))
    f = production_frame(rt.snapshot(), CAT)
    assert len(f.dropped) == 1
    assert f.dropped[0].op == "research"
    assert f.dropped[0].at is None
    assert f.dropped[0].reason


def test_placement_encoded_as_discriminated_union():
    rt = _runtime()
    rt.submit_queue("main", [
        QueueItem(op=QueueOp.BUILD, type="terran/barracks", placement=PlacementExact(mark="rax_1")),
    ])
    rt.on_game_state(_gs(0, 0.0, [], minerals=0))
    item = production_frame(rt.snapshot(), CAT).queues[0].items[0]
    assert item.placement == {"kind": "exact", "mark": "rax_1"}


# ---------------- RecordingPort ----------------

def test_recording_port_tags_origin_without_touching_operation():
    """origin 是调用方的属性，不是命令的属性 —— 所以走装饰器，Operation 形态不变。"""
    ring = OpRing()
    inner = FakeGamePort(script=[])
    flow_port = RecordingPort(inner, "flow", ring, clock=lambda: 12.5)
    prod_port = RecordingPort(inner, "production", ring, clock=lambda: 12.5)

    flow_port.submit_operations([Operation(op_id=1, unit_tags=[1], action="move_to",
                                           params={"position": [1.0, 2.0]}, seq=0)])
    prod_port.submit_operations([Operation(op_id=2, unit_tags=[9], action="train",
                                           params={"type": "terran/marine"}, seq=0)])

    f = ops_frame(ring)
    assert [o.origin for o in f.ops] == ["flow", "production"]
    assert [o.action for o in f.ops] == ["move_to", "train"]
    assert f.ops[0].at == 12.5
    # 命令仍然真的转发给了下层
    assert [op.op_id for op in inner.submitted] == [1, 2]
    # Operation 本身没有 origin 字段（形态未变）
    assert not hasattr(inner.submitted[0], "origin")


def test_recording_port_rejects_unknown_origin():
    with pytest.raises(ValueError, match="未知 origin"):
        RecordingPort(FakeGamePort(script=[]), "somebody", OpRing())


def test_ops_ring_is_bounded():
    ring = OpRing(maxlen=3)
    port = RecordingPort(FakeGamePort(script=[]), "flow", ring)
    for i in range(10):
        port.submit_operations([Operation(op_id=i, unit_tags=[i], action="stop", params={}, seq=0)])
    assert [o.op_id for o in ops_frame(ring).ops] == [7, 8, 9]


def test_apply_filled_from_result_detail_stays_none_until_b9():
    """D6 之前 detail 只能是 None —— 不编失败原因（不静默）。"""
    ring = OpRing()
    port = RecordingPort(FakeGamePort(script=[]), "flow", ring)
    port.submit_operations([Operation(op_id=1, unit_tags=[1], action="stop", params={}, seq=0)])
    op = ops_frame(ring).ops[0]
    assert op.apply is not None and op.apply.detail is None
    assert op.landing is None
