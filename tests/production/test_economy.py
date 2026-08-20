"""经济维持器：ADR-0030 的离线验收（1/2/3/4/5/6/7/8 —— 第 9 条要真机）。"""
import time

import pytest

from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_terran
from production.economy import (
    RETASK_COOLDOWN_FRAMES,
    EconomyKeeper,
    EconomyPolicy,
    WorkerReservations,
)

CAT = load_terran()


class _Port:
    def __init__(self):
        self.ops = []

    def submit_operations(self, ops):
        self.ops.extend(ops)

    def gathers(self):
        return [(o.unit_tags[0], o.params["target_unit"]) for o in self.ops if o.action == "gather"]


def _u(tag, type_name, x=0.0, y=0.0, owner=Owner.SELF, orders=(), progress=1.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=progress,
                orders=list(orders))


def _scv(tag, gathering=None):
    orders = [Order(ability="Gather", target_tag=gathering)] if gathering else []
    return _u(tag, "SCV", orders=orders)


def _gs(units, resources=(), seq=0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq) * 0.05, minerals=0, vespene=0,
                     supply_used=len(units), supply_cap=50, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


def _patches(n, tag0=900):
    return [_u(tag0 + i, "MINERALFIELD", x=float(i), owner=Owner.NEUTRAL) for i in range(n)]


def _keeper(port, **kw):
    return EconomyKeeper(CAT, port, **kw)


# ---- 验收 1：新造 SCV 自动进矿（不需要任何人下令）----


def test_new_scv_is_assigned_without_any_command():
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    k.on_game_state(_gs([_scv(1)], patches, seq=0))
    assert port.gathers() == [(1, 900)]
    # 新造一个 → 下一帧自动进矿（旧实现：没人再下 assign_workers 就永久闲置）
    k.on_game_state(_gs([_scv(1, gathering=900), _scv(2)], patches, seq=1))
    assert (2, 900) in port.gathers() or (2, 901) in port.gathers()


# ---- 验收 2：矿采空 → 工兵被重派 ----


def test_mined_out_patch_workers_are_reassigned():
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    k.on_game_state(_gs([_scv(1), _scv(2)], patches, seq=0))
    before = len(port.ops)
    # 900 采空（从 resources 消失）：两个工兵的 order 目标已不存在 → 应被重派到 901
    later = RETASK_COOLDOWN_FRAMES + 1
    k.on_game_state(_gs([_scv(1, gathering=900), _scv(2, gathering=900)], patches[1:], seq=later))
    new_ops = port.gathers()[before:]
    assert new_ops, "采空后应重派（旧实现：永久闲置）"
    assert all(node == 901 for _tag, node in new_ops)


# ---- 验收 3：gas 目标在精炼厂建好前设置 → 建好后自动补满（issues P9）----


def test_gas_target_survives_until_refinery_ready():
    port = _Port()
    k = _keeper(port)
    k.set_target("gas", 3)  # 精炼厂还不存在（甚至还没开始建）
    patches = _patches(1)
    workers = [_scv(i) for i in (1, 2, 3, 4, 5)]
    k.on_game_state(_gs(workers, patches, seq=0))
    assert not [n for _t, n in port.gathers() if n == 500], "还没气矿，不该派人去采气"
    # 精炼厂建好（build_progress=1）→ 目标仍在 → 自动补 3 个
    refinery = _u(500, "REFINERY", progress=1.0)
    mining = [_scv(1, gathering=900), _scv(2, gathering=900), _scv(3), _scv(4), _scv(5)]
    k.on_game_state(_gs(mining + [refinery], patches, seq=RETASK_COOLDOWN_FRAMES + 1))
    gas_assigned = [t for t, n in port.gathers() if n == 500]
    assert len(set(gas_assigned)) == 3, f"应补满 3 个气工，实际 {gas_assigned}"


# ---- 验收 4：建造征用期间不改派；释放后自动回矿 ----


def test_reserved_builder_is_never_retasked():
    port = _Port()
    res = WorkerReservations()
    k = _keeper(port, reservations=res)
    patches = _patches(2)
    assert res.reserve("production/build#1", 2)
    k.on_game_state(_gs([_scv(1), _scv(2)], patches, seq=0))
    assert [t for t, _n in port.gathers()] == [1], "被征用的 2 号不该收到采矿命令"
    res.release("production/build#1")
    k.on_game_state(_gs([_scv(1, gathering=900), _scv(2)], patches, seq=1))
    assert 2 in [t for t, _n in port.gathers()], "释放后应自动回矿"


def test_reservation_pruned_when_worker_dies():
    res = WorkerReservations()
    res.reserve("o1", 7)
    k = _keeper(_Port(), reservations=res)
    k.on_game_state(_gs([_scv(1)], _patches(1), seq=0))  # 7 号不在场
    assert res.tags() == frozenset()


# ---- 验收 5：被战斗组租用的工兵不在领地内 ----


class _Pool:
    """WorkerPoolPort 的假实现：leased 之外的才是维持器领地。"""

    def __init__(self, leased):
        self._leased = set(leased)

    def unleased_workers(self, gs):
        names = {"SCV"}
        return [u.tag for u in gs.units
                if u.owner is Owner.SELF and u.type_name in names and u.tag not in self._leased]

    def reserve(self, owner, tag):
        return True

    def release(self, owner):
        pass

    def reserved_tags(self):
        return frozenset()


def test_group_leased_workers_are_out_of_domain():
    port = _Port()
    k = _keeper(port, pool=_Pool(leased={2, 3}))
    k.on_game_state(_gs([_scv(1), _scv(2), _scv(3)], _patches(2), seq=0))
    assert [t for t, _n in port.gathers()] == [1], "租给战术组的 2/3 不该被维持器碰"


# ---- 验收 6：稳定态零命令（幂等）----


def test_steady_state_emits_nothing():
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    k.on_game_state(_gs([_scv(1), _scv(2)], patches, seq=0))
    n = len(port.ops)
    assert n > 0
    settled = [_scv(1, gathering=900), _scv(2, gathering=901)]
    for seq in range(1, 6):
        k.on_game_state(_gs(settled, patches, seq=seq * 100))
    assert len(port.ops) == n, "已在目标上就不该再发命令（每帧重发 = 命令风暴 + 被 SC2 去重丢单）"


# ---- 验收 7：不满采可控 ----


def test_mineral_per_patch_controls_saturation():
    port = _Port()
    k = _keeper(port, policy=EconomyPolicy(mineral_per_patch=1))
    patches = _patches(2)
    k.on_game_state(_gs([_scv(i) for i in (1, 2, 3, 4)], patches, seq=0))
    assigned = port.gathers()
    assert len(assigned) == 2, f"每矿 1 人 × 2 矿 = 2 个，实际 {assigned}"
    assert len({n for _t, n in assigned}) == 2


def test_absolute_worker_target_caps_assignment():
    port = _Port()
    k = _keeper(port)
    k.set_target("mineral", 3)
    k.on_game_state(_gs([_scv(i) for i in range(1, 7)], _patches(4), seq=0))
    assert len(port.gathers()) == 3


def test_set_target_rejects_negative():
    k = _keeper(_Port())
    with pytest.raises(ValueError):
        k.set_target("gas", -1)
    with pytest.raises(ValueError):
        k.set_target("nope", 1)


# ---- 验收 8：性能护栏 ----


def test_perf_budget_300_units():
    """300 单位 + 10 矿点 + 2 气矿：单帧 < 2 ms（issues P18 —— 生产侧此前零性能护栏）。"""
    port = _Port()
    k = _keeper(port)
    patches = _patches(10)
    refineries = [_u(500 + i, "REFINERY", progress=1.0) for i in range(2)]
    scvs = [_scv(i, gathering=900 + (i % 10)) for i in range(1, 61)]
    filler = [_u(2000 + i, "MARINE") for i in range(240)]
    units = scvs + refineries + filler
    frames = 50
    t0 = time.perf_counter()
    for seq in range(frames):
        k.on_game_state(_gs(units, patches, seq=seq))
    per_frame_ms = (time.perf_counter() - t0) / frames * 1000
    assert per_frame_ms < 2.0, f"每帧 {per_frame_ms:.3f} ms 超预算"


def test_snapshot_exposes_targets_and_reservations():
    port = _Port()
    res = WorkerReservations()
    res.reserve("production/build#1", 9)
    k = _keeper(port, reservations=res)
    k.set_target("gas", 2)
    # 8 个在领地内 + 1 个被征用；气目标 2 → 矿上限 = min(3 矿×2, 8-2) = 6
    units = [_scv(i) for i in range(1, 9)] + [_scv(9), _u(500, "REFINERY", progress=1.0)]
    gs = _gs(units, _patches(3), seq=0)
    k.on_game_state(gs)
    snap = k.snapshot(gs)
    assert snap["targets"]["mineral"] == 6  # 3 矿 × 2
    assert snap["targets"]["gas"] == 2
    assert snap["reserved"] == {"production/build#1": 9}
    assert len(snap["nodes"]) == 4
