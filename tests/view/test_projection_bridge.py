"""view.projection：队列 → 投影的桥（解决"当前队列投影不出来"的缺口）。

守两件事：
1. `expand()` 透传裸 Op —— 否则这条桥只能靠另建模块注册的歪路；
2. `assign_workers` 是**目标值**语义（ADR-0030 D2），planner 与运行时一致 ——
   不一致的投影会让警报也不可信（AlertService 的卡人口/缺前置都从投影来）。
"""
import pytest

from game.catalog import load_terran
from game.geometry import Grid, Point2
from game.production import PlacementExact, QueueItem, QueueOp, WorkerTask
from game.state import GameState, Order, Owner, Unit
from planner.build_order import AssignWorkers, Build, ProductionModuleInstance, Research, Train, expand
from planner.planner import Planner

from view.projection import project_queue, queue_to_ops

CAT = load_terran()


def _unit(tag, name, ready=True, orders=()) -> Unit:
    return Unit(tag=tag, type_name=name, position=Point2(30.0, 30.0), owner=Owner.SELF,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0,
                build_progress=1.0 if ready else 0.3, orders=list(orders))


def _gs(minerals=500, gas=200) -> GameState:
    g = Grid(1, 1, [[0]])
    nodes = [Unit(tag=900 + i, type_name="MINERALFIELD", position=Point2(26.0 + i, 35.0),
                  owner=Owner.NEUTRAL, hp=1.0, hp_max=1.0, shield=0.0, energy=0.0,
                  build_progress=1.0, orders=[]) for i in range(8)]
    scvs = [_unit(100 + i, "SCV", orders=[Order(ability="HARVEST_GATHER", target_tag=900 + (i % 8))])
            for i in range(12)]
    cc = _unit(1, "COMMANDCENTER")
    return GameState(seq=1, game_time=0.0, minerals=minerals, vespene=gas,
                     supply_used=12, supply_cap=15, units=[cc, *scvs],
                     map_size=(176, 160), creep=g, visibility=g, resources=nodes)


# ---------------- expand 透传 ----------------

def test_expand_passes_through_raw_ops():
    ops = [Build("terran/supplydepot"), Train("terran/marine")]
    assert expand(ops) == ops


def test_expand_accepts_mixed_modules_and_ops():
    mixed = [ProductionModuleInstance(instance_id="m0", module_ref="basic_opening", version=1,
                                      params={}),
             Train("terran/marine")]
    out = expand(mixed)
    assert out[-1] == Train("terran/marine")
    assert len(out) > 1


def test_expand_still_rejects_unknown_module():
    bad = [ProductionModuleInstance(instance_id="x", module_ref="nope", version=1, params={})]
    with pytest.raises(ValueError, match="未知 production module"):
        expand(bad)


# ---------------- 队列 → ops ----------------

def test_translation_is_one_to_one_and_expands_count():
    items = [
        QueueItem(op=QueueOp.BUILD, type="terran/barracks",
                  placement=PlacementExact(mark="rax_1")),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=3),
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=3),
    ]
    out = queue_to_ops(items, CAT)
    assert out.ops == [
        Build("terran/barracks"),
        Train("terran/marine"), Train("terran/marine"), Train("terran/marine"),
        AssignWorkers("gas", 3),
    ]
    assert out.skipped == []


def test_placement_does_not_enter_projection():
    """planner 只数建筑数不放置（position 归 live runtime）。"""
    out = queue_to_ops([QueueItem(op=QueueOp.BUILD, type="terran/barracks",
                                  placement=PlacementExact(mark="rax_1"))], CAT)
    assert out.ops == [Build("terran/barracks")]


def test_unprojectable_items_are_skipped_with_reason():
    items = [
        QueueItem(op=QueueOp.CANCEL, type="terran/marine"),
        QueueItem(op=QueueOp.BUILD),                                  # 缺 type
        QueueItem(op=QueueOp.TRAIN, type="terran/nonexistent"),        # catalog 没登记
        QueueItem(op=QueueOp.ASSIGN_WORKERS),                          # 缺 task
    ]
    out = queue_to_ops(items, CAT)
    assert out.ops == []
    reasons = dict(out.skipped)
    assert "取消不进投影" in reasons["cancel"]
    assert reasons["build"] == "缺 type"
    assert "没登记" in reasons["train"]
    assert reasons["assign_workers"] == "缺 task"


def test_research_is_projectable_even_though_runtime_rejects_it():
    """投影**领先于**运行时的一处：planner 能算 research，运行时会 dropped。

    这不是矛盾，而是要在 UI 上说清 —— 提案预览里"投影里能跑"不等于"真机能跑"。
    """
    out = queue_to_ops([QueueItem(op=QueueOp.RESEARCH, type="terran/marine")], CAT)
    assert out.ops == [Research("terran/marine")]


# ---------------- 端到端：真投影 ----------------

def test_project_queue_produces_a_real_curve():
    items = [
        QueueItem(op=QueueOp.BUILD, type="terran/supplydepot"),
        QueueItem(op=QueueOp.BUILD, type="terran/barracks"),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=4),
    ]
    curve, tr = project_queue(Planner(CAT), _gs(), items, until=120.0, catalog=CAT)
    assert tr.skipped == []
    assert len(curve.points) > 60
    started = [e.stable_id if hasattr(e, "stable_id") else e.type
               for e in curve.events if e.kind == "started"]
    assert "terran/barracks" in started
    assert "terran/marine" in started


def test_assign_workers_is_absolute_in_projection():
    """目标值语义：连续两条 `gas=3` 不该把 6 个人派去采气。"""
    p = Planner(CAT)
    gs = _gs()
    once, _ = project_queue(p, gs, [QueueItem(op=QueueOp.ASSIGN_WORKERS,
                                              task=WorkerTask.GAS, count=3)],
                            until=5.0, catalog=CAT)
    twice, _ = project_queue(p, gs, [
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=3),
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=3),
    ], until=5.0, catalog=CAT)
    assert once.points[-1].gas_workers == 3
    assert twice.points[-1].gas_workers == 3, "幂等：设两次还是 3，不是 6"


def test_lowering_the_quota_returns_workers_to_minerals():
    """目标值下调要把人退回另一池（不凭空消失）。"""
    p = Planner(CAT)
    curve, _ = project_queue(p, _gs(), [
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=6),
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=2),
    ], until=5.0, catalog=CAT)
    last = curve.points[-1]
    assert last.gas_workers == 2
    assert last.mineral_workers + last.gas_workers == 12, "总人数守恒"
