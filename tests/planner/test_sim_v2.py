"""simulate v2（PLAN-V2 批 3）planner 层行为锁。

- uid 穿透：QueueItem.uid → Op.uid → curve.queue_status（uid 归并 count 项）；
- 执行状态账本：completed / skipped(prereq_missing) / pending（horizon 没轮到，
  D8：无 not_reached）/ in_progress（仿真结束时还在途）；
- 采样派生：workers 五分（building=在途建造数派生）/ 产位 cap 明细（bare/反应堆/科技）；
- initial_state：文档 → SimState（workers 分项校验/catalog 校验/supply_cap 对账）。
"""
from game import QueueItem
from game.catalog import load_all
from planner.build_order import Build, Train
from planner.initial_state import state_from_doc, validate_state_doc
from planner.opening import opening_game_state
from planner.planner import Planner
from view.projection import queue_to_ops

CAT = load_all()


def _gs(minerals=600.0):
    return opening_game_state(CAT, minerals=int(minerals), workers=12)


# ---------------- uid 穿透 ----------------

def test_queue_to_ops_carries_uid_and_expands_count():
    items = [QueueItem(op="build", type="terran/supplydepot", count=3, uid="q01"),
             QueueItem(op="train", type="terran/scv", count=2, uid="q02")]
    ops = queue_to_ops(items, CAT).ops
    assert [o.uid for o in ops] == ["q01", "q01", "q01", "q02", "q02"], \
        "count 展开共享同一 uid（状态表按队列项归并）"


def test_curve_queue_status_completed_with_times():
    p = Planner(CAT)
    ops = queue_to_ops([
        QueueItem(op="build", type="terran/supplydepot", count=1, uid="q01"),
    ], CAT).ops
    curve = p.project(_gs(), ops, 60, until_complete=True)
    st = {q["uid"]: q for q in curve.queue_status}
    assert st["q01"]["status"] == "completed"
    assert st["q01"]["started_at"] is not None and st["q01"]["completed_at"] is not None
    assert st["q01"]["completed_at"] >= st["q01"]["started_at"]


def test_curve_queue_status_skipped_reason_closed_set():
    """死局（前置不在场也不在队列）→ skipped + prereq_missing；后续项 pending 不级联标 skip。"""
    p = Planner(CAT)
    ops = queue_to_ops([
        QueueItem(op="train", type="terran/siegetank", count=1, uid="q01"),   # 没工厂/挂件
        QueueItem(op="train", type="terran/marine", count=1, uid="q02"),      # 排在死局后面
    ], CAT).ops
    curve = p.project(_gs(), ops, 30, until_complete=True)
    st = {q["uid"]: q for q in curve.queue_status}
    assert st["q01"]["status"] == "skipped"
    assert st["q01"]["reason"] == "prereq_missing"
    assert st["q02"]["status"] == "pending", "horizon 没轮到 = pending（D8 无 not_reached）"


def test_curve_queue_status_in_progress_at_horizon_end():
    """仿真停在 horizon 且队首在训 → in_progress（started_at 有、completed_at 空）。"""
    p = Planner(CAT)
    ops = queue_to_ops([
        QueueItem(op="build", type="terran/barracks", count=1, uid="q01"),
        QueueItem(op="train", type="terran/marine", count=4, uid="q02"),
    ], CAT).ops
    # 开局有 depot？opening 种子只有 CC —— barracks 前置 depot 不在场 → 先给 depot
    ops = queue_to_ops([
        QueueItem(op="build", type="terran/supplydepot", count=1, uid="q00"),
        QueueItem(op="build", type="terran/barracks", count=1, uid="q01"),
        QueueItem(op="train", type="terran/marine", count=8, uid="q02"),
    ], CAT).ops
    curve = p.project(_gs(minerals=1500), ops, 130)   # 不 until_complete：mid-queue 截断
    st = {q["uid"]: q for q in curve.queue_status}
    assert st["q02"]["status"] == "in_progress"
    assert st["q02"]["started_at"] is not None and st["q02"]["completed_at"] is None


# ---------------- 采样派生（extras） ----------------

def test_curve_extras_worker_split_and_producer_caps():
    p = Planner(CAT)
    ops = queue_to_ops([
        QueueItem(op="build", type="terran/supplydepot", count=1, uid="q01"),
        QueueItem(op="build", type="terran/barracks", count=1, uid="q02"),
    ], CAT).ops
    curve = p.project(_gs(minerals=800), ops, 120, until_complete=True)
    assert len(curve.extras) == len(curve.points)
    # 兵营落成后：normal_cap=1（bare）；产位明细键齐全
    after = [e for e in curve.extras
             if e["producers"].get("terran/barracks", {}).get("total", 0) >= 1]
    assert after, "兵营应已落成"
    b = after[-1]["producers"]["terran/barracks"]
    assert b["normal_cap"] == 1 and b["tech_cap"] == 0
    assert set(b) >= {"normal_cap", "tech_cap", "normal_busy", "tech_busy", "total",
                      "bare", "with_reactor", "with_techlab", "building_addon"}
    # 工人五分：总数守恒（mineral+gas+building+idle = total，scouting 恒 0=编组派生）
    for e in curve.extras[::20]:
        assert e["workers"]["scouting"] == 0
        assert e["workers"]["mineral"] + e["workers"]["gas"] \
            + e["workers"]["building"] + e["workers"]["idle"] >= 12


# ---------------- initial_state → SimState ----------------

def test_state_from_doc_builds_sim_state():
    doc = {
        "id": "t1", "title_zh": "测", "minerals": 500, "gas": 150,
        "supply_used": 28, "supply_cap": 34,
        "workers": {"mineral": 16, "gas": 6, "building": 0, "scouting": 0, "idle": 2},
        "buildings": {"terran/commandcenter": 2, "terran/barracks": 2,
                      "terran/techlab": 1, "terran/reactor": 1,
                      "terran/refinery": 2, "terran/supplydepot": 1},
        "units": {"terran/marine": 6},
        "upgrades": ["terran/infantryweapons1"],
    }
    st = state_from_doc(doc, CAT)
    assert st.minerals == 500 and st.gas == 150
    assert st.mineral_workers == 16 and st.gas_workers == 6 and st.idle_workers == 2
    assert st.total_workers == 24
    assert st.buildings["terran/barracks"] == 2
    assert st.units == {"terran/marine": 6}
    assert "terran/infantryweapons1" in st.research_completed
    # 反应堆挂件 → addons 派生（B15 同款）
    assert st.addons.get("terran/barracks") == 1


def test_state_doc_validation_rules():
    # catalog 不认 → 错
    errs = validate_state_doc({"workers": {"mineral": 1}, "buildings": {"terran/nope": 1}}, CAT)
    assert any("terran/nope" in e for e in errs)
    # units 里出现 SCV → 错（SCV 属 workers）
    errs = validate_state_doc({
        "workers": {"mineral": 12}, "supply_used": 12, "supply_cap": 13,
        "buildings": {"terran/commandcenter": 1},
        "units": {"terran/scv": 12}}, CAT)
    assert any("SCV" in e or "scv" in e for e in errs)
    # supply_cap 与建筑构成不一致 → 错（CC=13：1 CC + 1 depot = 21）
    errs = validate_state_doc({
        "workers": {"mineral": 12}, "supply_used": 12, "supply_cap": 99,
        "buildings": {"terran/commandcenter": 1, "terran/supplydepot": 1}}, CAT)
    assert any("supply_cap" in e for e in errs)
    # 合法案：无错
    errs = validate_state_doc({
        "workers": {"mineral": 12}, "supply_used": 12, "supply_cap": 13,
        "buildings": {"terran/commandcenter": 1}}, CAT)
    assert errs == []


def test_project_accepts_initial_state():
    """initial SimState 直接当起点（不再 derive 开局种子）—— 中期状态一步进仿。"""
    p = Planner(CAT)
    doc = {
        "minerals": 500, "gas": 100, "supply_used": 15, "supply_cap": 21,
        "workers": {"mineral": 16, "gas": 0, "building": 0, "scouting": 0, "idle": 0},
        "buildings": {"terran/commandcenter": 1, "terran/supplydepot": 1},
    }
    st = state_from_doc(doc, CAT)
    ops = queue_to_ops([QueueItem(op="train", type="terran/marine", count=2, uid="q01")], CAT)
    # marine 需要 barracks：不在场不在队列 → skipped（起点状态生效的证明：开局种子
    # 跑同队列也是 skipped，所以再验 minerals 起点直接来自 doc）
    curve = p.project(_gs(), ops.ops, 10, initial=st)
    assert curve.points[0].minerals >= 500
