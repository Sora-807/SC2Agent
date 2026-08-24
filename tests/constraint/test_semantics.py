"""semantics.classify 行为锁（PLAN-V2 批 1，ADR-0032）：三态判定全矩阵 —— 表驱动。

消费方三处（runtime / planner 仿真 / simulate 健康检查）共享这一份语义，
所以这里锁的是**语义**不是实现：改 classify 必须先过这张表。
"""
from game.catalog import load_all
from game.production import QueueItem

from constraint.semantics import (
    SKIP_PLACEMENT_COLLISION,
    SKIP_PREREQ_MISSING,
    STATUS_ZH,
    VerdictKind,
    classify,
    is_capacity_wait,
)
from constraint.semantics import ExecView  # noqa: F401（测试装配用）

CAT = load_all()


def _view(**over):
    base = dict(
        minerals=1000, vespene=500, supply_used=10, supply_cap=20,
        ready_types=frozenset({"terran/commandcenter", "terran/supplydepot"}),
        inflight_types=frozenset(),
        queued_types=frozenset(),
        free_producers=frozenset(),
    )
    base.update(over)
    return ExecView(**base)


def _item(op="train", type_="terran/marine", **kw):
    return QueueItem(op=op, type=type_, **kw)


# ---- ready ----

def test_train_ready_with_free_producer():
    v = classify(_item(), CAT, _view(ready_types=frozenset(
        {"terran/commandcenter", "terran/supplydepot", "terran/barracks"}),
        free_producers=frozenset({"terran/barracks"})))
    assert v.kind is VerdictKind.READY and v.reason is None


def test_build_ready_when_prereq_on_field():
    v = classify(_item("build", "terran/barracks"), CAT, _view(minerals=150))
    assert v.kind is VerdictKind.READY


def test_assign_workers_always_ready_without_gating():
    v = classify(QueueItem(op="assign_workers", task="gas", count=3), CAT,
                 _view(minerals=0, vespene=0))
    assert v.kind is VerdictKind.READY


# ---- pending：矿/气/人口 ----

def test_build_pending_minerals_with_amounts():
    v = classify(_item("build", "terran/supplydepot"), CAT, _view(minerals=49))
    assert v.kind is VerdictKind.PENDING
    assert "晶体矿不足" in v.reason and "49" in v.reason


def test_build_pending_gas():
    # 兵工厂 150 矿/100 气
    v = classify(_item("build", "terran/factory"), CAT,
                 _view(ready_types=frozenset({"terran/commandcenter", "terran/supplydepot",
                                              "terran/barracks"}),
                       vespene=99))
    assert v.kind is VerdictKind.PENDING and "高能瓦斯不足" in v.reason


def test_train_pending_supply():
    v = classify(_item(), CAT, _view(ready_types=frozenset(
        {"terran/commandcenter", "terran/supplydepot", "terran/barracks"}),
        free_producers=frozenset({"terran/barracks"}),
        supply_used=20, supply_cap=20))
    assert v.kind is VerdictKind.PENDING and "供给不足" in v.reason


# ---- pending：前置在途 / 在队列 ----

def test_build_pending_prereq_inflight():
    # 兵营前置 supplydepot：不在场但在途 → 等
    v = classify(_item("build", "terran/barracks"), CAT, _view(
        ready_types=frozenset({"terran/commandcenter"}),
        inflight_types=frozenset({"terran/supplydepot"}), minerals=150))
    assert v.kind is VerdictKind.PENDING and "等前置" in v.reason


def test_build_pending_prereq_queued_earlier():
    v = classify(_item("build", "terran/barracks"), CAT, _view(
        ready_types=frozenset({"terran/commandcenter"}),
        queued_types=frozenset({"terran/supplydepot"}), minerals=150))
    assert v.kind is VerdictKind.PENDING and "等前置" in v.reason


def test_train_pending_producer_inflight():
    # scv：prerequisites 为空、produced_by=CC —— 单独覆盖产出建筑等待分支
    v = classify(_item("train", "terran/scv"), CAT, _view(
        ready_types=frozenset(), inflight_types=frozenset({"terran/commandcenter"})))
    assert v.kind is VerdictKind.PENDING and "等产出建筑" in v.reason


def test_train_pending_producer_queued():
    v = classify(_item("train", "terran/scv"), CAT, _view(
        ready_types=frozenset(), queued_types=frozenset({"terran/commandcenter"})))
    assert v.kind is VerdictKind.PENDING and "等产出建筑" in v.reason


def test_train_pending_when_prereq_branch_covers_producer():
    """marine 的 prerequisites 本就含兵营：前置分支先命中，文案是「等前置」——同语义。"""
    v = classify(_item(), CAT, _view(
        ready_types=frozenset({"terran/commandcenter", "terran/supplydepot"}),
        inflight_types=frozenset({"terran/barracks"})))
    assert v.kind is VerdictKind.PENDING and "等前置" in v.reason


# ---- pending：production_capacity（槽满瞬态归等待，PLAN-V2 §7 拍板）----

def test_train_pending_capacity_when_producer_ready_but_busy():
    v = classify(_item(), CAT, _view(ready_types=frozenset(
        {"terran/commandcenter", "terran/supplydepot", "terran/barracks"}),
        free_producers=frozenset()))
    assert v.kind is VerdictKind.PENDING
    assert "production_capacity" in v.reason
    assert is_capacity_wait(v.reason)


def test_capacity_wait_is_not_resource_wait_mark():
    """capacity 等待不属于资源等待词表（alerts 层按 60s 升级 warn，不按 30s error）。"""
    assert is_capacity_wait("晶体矿不足") is False


# ---- skip：prereq_missing（不在场/不在途/不在队列）----

def test_train_skip_when_producer_nowhere():
    v = classify(_item(), CAT, _view())  # 场上只有 CC+depot
    assert v.kind is VerdictKind.SKIP
    assert v.reason == SKIP_PREREQ_MISSING
    assert "兵营" in v.detail


def test_build_skip_when_prereq_nowhere():
    v = classify(_item("build", "terran/barracks"), CAT,
                 _view(ready_types=frozenset({"terran/commandcenter"}), minerals=150))
    assert v.kind is VerdictKind.SKIP and v.reason == SKIP_PREREQ_MISSING


def test_skip_reason_closed_set_and_zh():
    from constraint.semantics import SKIP_REASONS, SKIP_REASON_ZH
    assert set(SKIP_REASONS) == {SKIP_PREREQ_MISSING, SKIP_PLACEMENT_COLLISION}
    assert set(SKIP_REASON_ZH) == set(SKIP_REASONS)
    assert all(SKIP_REASON_ZH.values())


def test_status_zh_covers_four_values():
    from constraint.semantics import QUEUE_STATUSES
    assert set(QUEUE_STATUSES) == {"pending", "in_progress", "completed", "skipped"}
    assert STATUS_ZH["pending"] == "等待中"
    assert STATUS_ZH["skipped"] == "已跳过"
