"""view.projection：队列 → 投影的桥（解决"当前队列投影不出来"的缺口）。

守两件事：
1. `expand()` 透传裸 Op —— 否则这条桥只能靠另建模块注册的歪路；
2. `assign_workers` 是**目标值**语义（ADR-0030 D2），planner 与运行时一致 ——
   不一致的投影会让警报也不可信（AlertService 的卡人口/缺前置都从投影来）。
"""
import pytest

from game.catalog import load_all
from game.geometry import Grid, Point2
from game.production import PlacementExact, QueueItem, QueueOp, WorkerTask
from game.state import GameState, Order, Owner, Unit
from planner.build_order import AssignWorkers, Build, ProductionModuleInstance, Research, Train, expand
from planner.planner import Planner

from view.projection import project_queue, queue_to_ops

CAT = load_all()


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
        Build("terran/barracks", mark="rax_1"),
        Train("terran/marine"), Train("terran/marine"), Train("terran/marine"),
        AssignWorkers("gas", 3),
    ]
    assert out.skipped == []


def test_placement_mark_enters_but_position_does_not():
    """放置近似（2026-08-24）：exact 的**槽位名**进投影（近似模型按名占位），
    in_region/null = 自动找位（mark=None）；坐标永远不进（position 归 live runtime）。"""
    out = queue_to_ops([QueueItem(op=QueueOp.BUILD, type="terran/barracks",
                                  placement=PlacementExact(mark="rax_1"))], CAT)
    assert out.ops == [Build("terran/barracks", mark="rax_1")]
    out2 = queue_to_ops([QueueItem(op=QueueOp.BUILD, type="terran/barracks")], CAT)
    assert out2.ops == [Build("terran/barracks", mark=None)]


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


def test_ledger_history_not_resimulated():
    """ADR-0032 账本化回归修（2026-08-25 用户报「泳道图每帧整体后移、完全对不上」）：
    completed/skipped 历史项与 count=0 的全发射在途项**不进仿真**。完成项永久留队后
    不过滤 = 整条历史每帧重仿真成幻影条 + 幻影开销（录像 rec-20260825-104557 实锤：
    q01-q07 全 completed，投影每帧仍画 9 条 started@T 的条，T 每帧 +3s 整体右移）。
    在途实体由 derive_from 按真实 build_progress 建模，不需要 op 再造一份。"""
    items = [
        QueueItem(op=QueueOp.TRAIN, type="terran/scv", uid="q01",
                  status="completed", count=0),
        QueueItem(op=QueueOp.BUILD, type="terran/supplydepot", uid="q02",
                  status="completed", count=0),
        QueueItem(op=QueueOp.BUILD, type="terran/refinery", uid="q03",
                  status="skipped", count=0, reason="placement_collision"),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", uid="q04",
                  status="in_progress", count=0),   # 原始 8 条全在途（世界带进度）
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", uid="q05", count=2),
        QueueItem(op=QueueOp.BUILD, type="terran/barracks", uid="q06"),
    ]
    out = queue_to_ops(items, CAT)
    assert out.ops == [
        Train("terran/marine", uid="q05"), Train("terran/marine", uid="q05"),
        Build("terran/barracks", uid="q06"),
    ]
    # 历史项是「不重仿真」不是「不可投影」——不进 skipped（否则每帧刷一屏噪音）
    assert out.skipped == []


def test_project_queue_curve_has_no_phantom_rebuilds():
    """端到端锁：已完成 depot 不再出现在投影事件里（旧代码每帧从 T 重画它一遍）。"""
    gs = _gs()   # 世界里只有 CC + 12 SCV（depot 已建成会在 buildings 里）
    gs.units.append(_unit(2, "SUPPLYDEPOT"))
    items = [
        QueueItem(op=QueueOp.BUILD, type="terran/supplydepot", uid="q01",
                  status="completed", count=0),
        QueueItem(op=QueueOp.BUILD, type="terran/barracks", uid="q02"),
    ]
    curve, tr = project_queue(Planner(CAT), gs, items, until=60.0, catalog=CAT)
    started = [e.type for e in curve.events if e.kind == "started"]
    assert "terran/supplydepot" not in started   # 不再重画已建成的 depot
    assert "terran/barracks" in started


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


# ---------------- 回退门（§0.51 投影爆炸事故）----------------

from planner.build_order import ProductionModuleInstance  # noqa: E402
from view.producer import FrameProducer  # noqa: E402


class _FakeRuntime:
    """只答 queue() 的最小 runtime —— `_project` 不碰其它面。"""

    def __init__(self, items):
        from game.production import Queue
        self._q = Queue(name="main", items=items)

    def queue(self, name):
        return self._q if name == "main" else None


def _producer(runtime) -> FrameProducer:
    return FrameProducer(
        catalog=CAT, runtime=runtime, planner=Planner(CAT),
        projection_plan=[ProductionModuleInstance(
            instance_id="m0", module_ref="basic_opening", version=1, params={})],
        horizon=60.0)


def test_never_falls_back_after_live_queue_seen():
    """见过 live 队列后，队列清空必须投空队列 —— 参考计划的条目一条都不许出现。"""
    p = _producer(_FakeRuntime([QueueItem(op=QueueOp.TRAIN, type="terran/scv")]))
    gs = _gs()
    _, frame = p._project(gs)
    assert frame.source["kind"] == "live_queue"

    # 单项被发出（emit 即出队）→ 队列空：仍是 live_queue，且没有参考计划的事件
    p = _producer(_FakeRuntime([]))
    p._live_seen = True
    _, frame = p._project(_gs())
    assert frame.source["kind"] == "live_queue"
    assert frame.source["queue_name"] == "main"
    started = [e.stable_id for e in frame.events if e.kind == "started"]
    assert started == [], "空队列不许画出参考计划的条目"
    assert len(frame.points) > 30, "空队列仍有收入外推曲线"


def test_reference_plan_only_before_any_live_queue():
    """从未见过 live 队列（开局）：参考计划照常可用，标 draft。"""
    p = _producer(_FakeRuntime([]))
    _, frame = p._project(_gs())
    assert frame.source["kind"] == "draft"
    started = [e.stable_id for e in frame.events if e.kind == "started"]
    assert "terran/scv" in started, "开局参考计划包含农民条目"


def test_world_inflight_completes_in_empty_queue_projection():
    """队列空时，世界里在建的建筑（build_progress<1）在投影里照常落成 ——
    在途可见性来自世界帧派生，不需要参考计划。"""
    g = _gs()
    depot = Unit(tag=77, type_name="SUPPLYDEPOT", position=Point2(40.0, 40.0),
                 owner=Owner.SELF, hp=100.0, hp_max=100.0, shield=0.0, energy=0.0,
                 build_progress=0.5, orders=[])
    g.units.append(depot)
    p = _producer(_FakeRuntime([]))
    p._live_seen = True
    _, frame = p._project(g)
    done = [e for e in frame.events if e.kind == "completed"]
    assert any(e.stable_id == "terran/supplydepot" for e in done), \
        "在建的补给站应在空队列投影里落成"


def test_world_frame_with_enemies_renders_clusters():
    """批 6 崩溃回归（真机启动即挂）：adapt 用了 EnemyClusterView 却没导入——
    带敌方单位的 world_frame 必须能产帧且聚类非空。"""
    from game import GameState, Grid, Owner, Point2, Unit
    from game.catalog import load_all
    from view.adapt import world_frame

    CAT = load_all()
    g = Grid(1, 1, [[0]])
    gs = GameState(seq=1, game_time=5.0, minerals=100, vespene=0,
                   supply_used=12, supply_cap=13,
                   units=[Unit(tag=1, type_name="COMMANDCENTER",
                               position=Point2(30, 30), owner=Owner.SELF,
                               hp=1, hp_max=1, shield=0, energy=0, build_progress=1.0),
                          Unit(tag=2, type_name="MARINE",
                               position=Point2(31, 31), owner=Owner.ENEMY,
                               hp=1, hp_max=1, shield=0, energy=0, build_progress=1.0),
                          Unit(tag=3, type_name="MARINE",
                               position=Point2(32, 31), owner=Owner.ENEMY,
                               hp=1, hp_max=1, shield=0, energy=0, build_progress=1.0)],
                   map_size=(176, 160), creep=g, visibility=g, resources=[])
    f = world_frame(gs, CAT)
    assert f.enemy_clusters and f.enemy_clusters[0].count == 2
    assert f.enemy_clusters[0].by_stable_id == {"terran/marine": 2}
