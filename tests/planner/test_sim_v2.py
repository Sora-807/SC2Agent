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

import pytest

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


# ---------------- I32：终态/零余量项不进仿真（类规则直测，2026-08-25 审计批3d） ----------------
# 原修复（40210ce）只被 drain 顺带盖住：这里对账本四值闭集 × 余量逐格直测过滤规则，
# 防第 5 个终态或新的零余量形态再漏成「整条历史每帧重仿真」的幻影条。


@pytest.mark.parametrize("status,count,want_ops", [
    pytest.param("completed", 1, 0, id="completed"),
    pytest.param("completed", 3, 0, id="completed-count3"),
    pytest.param("skipped", 1, 0, id="skipped"),
    pytest.param("in_progress", 0, 0, id="in-progress-all-sent"),
    pytest.param("pending", 0, 0, id="pending-zero-remainder"),
    pytest.param("in_progress", 2, 2, id="in-progress-partial"),
    pytest.param("pending", 1, 1, id="pending-untouched"),
])
def test_queue_to_ops_filters_terminal_and_empty(status, count, want_ops):
    """completed/skipped（终态历史）与 count<=0（零余量）不进仿真；活项余量照常投影。"""
    items = [QueueItem(op="train", type="terran/marine", count=count, uid="q01",
                       status=status)]
    assert len(queue_to_ops(items, CAT).ops) == want_ops


def test_queue_to_ops_history_plus_live_mix():
    """I32 原案形态：整条终态历史（录像 q01-q07 场景的缩影）+ 在途/待办混排 ——
    只有活项进仿真，幻影条/幻影矿开销从根上不可能再产生。"""
    items = [
        QueueItem(op="build", type="terran/supplydepot", count=0, uid="q01", status="completed"),
        QueueItem(op="train", type="terran/marine", count=0, uid="q02", status="completed"),
        QueueItem(op="build", type="terran/barracks", count=0, uid="q03", status="skipped"),
        QueueItem(op="train", type="terran/scv", count=2, uid="q04", status="in_progress"),
        QueueItem(op="train", type="terran/marine", count=2, uid="q05", status="pending"),
    ]
    ops = queue_to_ops(items, CAT).ops
    assert [o.uid for o in ops] == ["q04", "q04", "q05", "q05"]


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
    """死局（前置不在场也不在队列）→ skipped + prereq_missing；后续项 pending 不级联标 skip。

    批 6 清偿②后语义归一（D6/D8）：矿/气/人口缺 = pending（哪怕无收入死等），
    只有 classify 判 skip（前置根本不在）才 skipped —— 夹具带够气，锁真前置缺口。
    """
    from planner.initial_state import state_from_doc
    p = Planner(CAT)
    ops = queue_to_ops([
        QueueItem(op="train", type="terran/siegetank", count=1, uid="q01"),   # 没工厂/挂件
        QueueItem(op="train", type="terran/marine", count=1, uid="q02"),      # 排在死局后面
    ], CAT).ops
    st0 = state_from_doc({"minerals": 600, "gas": 300, "supply_used": 8,
                          "supply_cap": 13,   # 坦克吃 3 人口：留余量，别让供给挡住前置判定
                          "workers": {"mineral": 12},
                          "buildings": {"terran/commandcenter": 1}}, CAT)
    curve = p.project(_gs(), ops, 30, until_complete=True, initial=st0)
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


def test_bio_tank_module_completes_without_supply_deadlock():
    """2026-08-24 真机/干跑双卡死回归：模块 V2 依赖已删除的 supply_guard，
    12/13 人口下整队死等（只执行 1 个 SCV）。V3 显式插 depot 后必须全程完成。"""
    from collections import Counter

    from planner.build_order import ProductionModuleInstance, expand
    from planner.opening import opening_game_state

    ops = expand([ProductionModuleInstance("m0", "bio_tank_opening", 1, {})])
    assert sum(1 for o in ops if getattr(o, "type", None) == "terran/supplydepot") >= 4, \
        "V3 必须显式带补给站（supply_guard 已删，没人替它插）"
    for i, o in enumerate(ops, 1):
        o.uid = f"q{i:02d}"
    curve = Planner(CAT).project(opening_game_state(CAT), ops, 120,
                                 until_complete=True, tail=30)
    dist = Counter(q["status"] for q in curve.queue_status)
    assert dist.get("skipped", 0) == 0 and dist.get("pending", 0) == 0, \
        f"步坦协同开局必须全程可执行（V2 卡死事故的验收）: {dist}"
    fs = curve.final_state
    assert fs.supply_used >= 55 and fs.supply_cap >= 60   # 59/66 终态锚
    assert fs.units.get("terran/marine", 0) >= 20 and fs.units.get("terran/siegetank", 0) >= 4
