"""WorkerAllocator：矿 2 / 气 3 饱和 + 分摊 + idle 解放 + catalog 选工兵。"""
from collections import Counter

import pytest

from game import Order, Owner, Point2, WorkerTask
from game.catalog import load_all
from production.worker import WorkerAllocator
from tests.factories import make_gs, make_unit

CAT = load_all()
ALLOC = WorkerAllocator(CAT)


def _u(tag, type_name="SCV", owner=Owner.SELF, orders=()):
    return make_unit(tag, type_name, owner=owner, hp=45.0, hp_max=45.0, orders=orders)


def _res(tag, type_name):
    return make_unit(tag, type_name, owner=Owner.NEUTRAL, hp=0.0, hp_max=0.0)


def _gs(units=(), resources=()):
    return make_gs(units, resources, minerals=50, supply_used=0, supply_cap=20)


def test_mineral_round_robin_distribution():
    """2 矿 2 SCV：矿脉间分摊；4 SCV：每矿补到 2（饱和上限）。"""
    minerals = [_res(10, "MINERALFIELD"), _res(11, "MINERALFIELD")]
    scvs = [_u(1), _u(2)]
    e = ALLOC.assign(_gs(scvs, minerals), WorkerTask.MINERAL, 2)
    assert {x.params["target_unit"] for x in e} == {10, 11}  # 分摊而不是都压一个矿
    e4 = ALLOC.assign(_gs([_u(i) for i in range(1, 5)], minerals), WorkerTask.MINERAL, 4)
    counts = Counter(x.params["target_unit"] for x in e4)
    assert set(counts.values()) == {2}  # 每个矿 2 = 饱和


def test_saturation_derived_from_existing_orders():
    """饱和度从 gs 派生：SCV1 已在采矿 10 → 再派 2 个 → 矿 10 补 1、矿 11 派 1。"""
    minerals = [_res(10, "MINERALFIELD"), _res(11, "MINERALFIELD")]
    scvs = [_u(1, orders=[Order(ability="Gather", target_tag=10)]), _u(2), _u(3)]
    e = ALLOC.assign(_gs(scvs, minerals), WorkerTask.MINERAL, 2)
    counts = Counter(x.params["target_unit"] for x in e)
    assert counts[10] == 1 and counts[11] == 1


def test_gas_saturation_three():
    refinery = _u(99, "REFINERY")  # 气井需先建精炼厂
    refinery.position = Point2(0, 0)
    e = ALLOC.assign(_gs([_u(i) for i in range(1, 5)] + [refinery], [_res(20, "VESPENEGEYSER")]),
                     WorkerTask.GAS, 4)
    assert len(e) == 3  # 气井饱和 3，第 4 个不派


@pytest.mark.parametrize("gas_building", ["EXTRACTOR", "ASSIMILATOR"])
def test_gas_node_detection_is_race_agnostic(gas_building):
    """批4（2026-08-25 审计）：气矿建筑识别走 catalog（where(capability="gas")），
    不再写死 "REFINERY" —— Zerg EXTRACTOR / Protoss ASSIMILATOR 同样是合法 gas 节点
    （否则 Z/P 对局里气矿工被当 idle、气收入=0）。"""
    b = _u(99, gas_building)
    b.position = Point2(0, 0)
    e = ALLOC.assign(_gs([_u(i) for i in range(1, 5)] + [b], [_res(20, "VESPENEGEYSER")]),
                     WorkerTask.GAS, 4)
    assert len(e) == 3, "气矿建筑识别与饱和上限对三族一致"


def test_no_nodes_or_no_workers_noop():
    assert ALLOC.assign(_gs([], []), WorkerTask.MINERAL, 2) == []
    assert ALLOC.assign(_gs([_u(1)], []), WorkerTask.MINERAL, 2) == []


def test_fully_saturated_noop():
    minerals = [_res(10, "MINERALFIELD")]
    scvs = [_u(i, orders=[Order(ability="Gather", target_tag=10)]) for i in (1, 2)]
    assert ALLOC.assign(_gs(scvs, minerals), WorkerTask.MINERAL, 2) == []


def test_idle_frees_gatherers_only():
    """idle：只解放正在采矿/采气的 SCV（stop），不碰闲着的。"""
    scvs = [
        _u(1, orders=[Order(ability="Gather", target_tag=10)]),
        _u(2),  # 空闲
        _u(3, orders=[Order(ability="Gather", target_tag=11)]),
    ]
    minerals = [_res(10, "MINERALFIELD"), _res(11, "MINERALFIELD")]
    e = ALLOC.assign(_gs(scvs, minerals), WorkerTask.IDLE, 2)
    assert {x.unit_tags[0] for x in e} == {1, 3}
    assert all(x.action == "stop" for x in e)


def test_enemy_workers_not_used():
    e = ALLOC.assign(_gs([_u(9, owner=Owner.ENEMY)], [_res(10, "MINERALFIELD")]),
                     WorkerTask.MINERAL, 1)
    assert e == []  # catalog role=worker 但只派己方

def test_gas_requires_refinery_on_geyser():
    """gas 节点 = 精炼厂 building：无精炼厂不派；2 精炼厂各饱和 3。"""
    scvs = [_u(i) for i in range(1, 7)]
    e = ALLOC.assign(_gs(scvs, []), WorkerTask.GAS, 6)
    assert e == []  # 无精炼厂 → 不派
    r1 = _u(98, "REFINERY"); r1.position = Point2(0, 0)
    r2 = _u(99, "REFINERY"); r2.position = Point2(5, 0)
    e2 = ALLOC.assign(_gs(scvs + [r1, r2], []), WorkerTask.GAS, 6)
    assert len(e2) == 6  # 两个精炼厂 → 各饱和 3
    assert {x.params["target_unit"] for x in e2} == {98, 99}  # 目标=精炼厂 tag


def test_idle_respects_skip():
    """P7：本帧已被派去建造的 SCV 不该被 IDLE 顺手 stop 掉（同帧同单位命令会被去重丢单）。"""
    minerals = [_u(10, "MINERALFIELD", owner=Owner.NEUTRAL)]
    scvs = [_u(1, "SCV", orders=[Order(ability="Gather", target_tag=10)]),
            _u(2, "SCV", orders=[Order(ability="Gather", target_tag=10)])]
    gs = _gs(scvs, resources=minerals)
    all_stopped = ALLOC.assign(gs, WorkerTask.IDLE, 2)
    assert {e.unit_tags[0] for e in all_stopped} == {1, 2}
    kept = ALLOC.assign(gs, WorkerTask.IDLE, 2, skip=frozenset({1}))
    assert {e.unit_tags[0] for e in kept} == {2}
