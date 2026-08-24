"""经济维持器：ADR-0030 的离线验收（1/2/3/4/5/6/7/8 —— 第 9 条要真机）。"""
import time

import pytest

from game import GameState, Grid, Order, Owner, Point2, Unit
from game.catalog import load_all
from production.economy import (
    RETASK_COOLDOWN_FRAMES,
    EconomyKeeper,
    EconomyPolicy,
    WorkerReservations,
)

CAT = load_all()


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



# ---- ADR-0030 第 1/3 步：三方共用一张 lease 表（端到端）----


def test_allocator_implements_worker_pool_port():
    """Allocator 就是 WorkerPoolPort：无主工兵 = 全部 - 组租用 - 征用。"""
    from flow.allocator import Allocator

    res = WorkerReservations()
    alloc = Allocator(CAT, reservations=res)
    alloc.create_group("G1", {"terran/scv": {"min": 2, "target": 2, "max": 2}})
    gs = _gs([_scv(i) for i in range(1, 6)], _patches(2), seq=0)
    alloc.refresh(gs)
    leased = set(alloc.expand("G1", "terran/scv"))
    assert len(leased) == 2
    unleased = set(alloc.unleased_workers(gs))
    assert unleased == {1, 2, 3, 4, 5} - leased
    # 征用一个 → 立刻退出维持器领地
    tag = sorted(unleased)[0]
    assert alloc.reserve("production/build#1", tag)
    assert tag not in set(alloc.unleased_workers(gs))
    alloc.release("production/build#1")
    assert tag in set(alloc.unleased_workers(gs))


def test_reserved_builder_cannot_be_leased_by_a_group():
    """P14 的结构性修法：正在盖房子的 SCV 不进 free 池，战斗组抢不走。"""
    from flow.allocator import Allocator

    res = WorkerReservations()
    alloc = Allocator(CAT, reservations=res)
    alloc.create_group("G1", {"terran/scv": {"min": 3, "target": 3, "max": 3}})
    gs = _gs([_scv(i) for i in range(1, 4)], _patches(1), seq=0)
    res.reserve("production/build#1", 2)  # 2 号正在建造
    alloc.refresh(gs)
    assert 2 not in alloc.expand("G1", "terran/scv")
    assert set(alloc.expand("G1", "terran/scv")) == {1, 3}  # 只租到 2 个（够不到 target）


def test_keeper_and_allocator_share_one_reservation_table():
    """会话装配：Allocator 与维持器共用同一份 WorkerReservations —— 一处征用，两处都看得见。"""
    from flow.allocator import Allocator

    res = WorkerReservations()
    alloc = Allocator(CAT, reservations=res)
    port = _Port()
    k = EconomyKeeper(CAT, port, pool=alloc, reservations=res)
    gs = _gs([_scv(1), _scv(2)], _patches(2), seq=0)
    alloc.refresh(gs)
    assert alloc.reserve("production/build#7", 2)
    k.on_game_state(gs)
    assert [t for t, _n in port.gathers()] == [1], "被征用的 2 号维持器不该碰"


# ---- 2026-08-24 真机事故修：往返送矿不被改派 + 外来订单不碰 ----


def _carrying_scv(tag, from_patch):
    """送矿途中的工人：Return 单（目标是基地）+ 携带矿。"""
    u = _scv(tag)
    return Unit(tag=u.tag, type_name="SCV", position=u.position, owner=Owner.SELF,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0,
                orders=[Order(ability="Return", target_tag=800)],
                is_carrying_minerals=True)


def test_returning_worker_is_not_reassigned():
    """事故根因锁：送矿途中（Return 单 + 携带）沿用记忆的在岗矿脉，不发任何改派。

    旧逻辑把它当"空闲"，冷却一过就 gather 去别的矿 → 采矿被打断、收入归零。
    """
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    k.on_game_state(_gs([_scv(1, gathering=900)], patches, seq=0))
    before = len(port.ops)
    later = RETASK_COOLDOWN_FRAMES + 1
    k.on_game_state(_gs([_carrying_scv(1, 900)], patches, seq=later))
    k.on_game_state(_gs([_carrying_scv(1, 900)], patches, seq=later + RETASK_COOLDOWN_FRAMES))
    assert port.gathers()[before:] == [], "送矿途中不得改派（记忆沿用，稳定态零命令）"


def test_returning_worker_resumes_after_delivery():
    """送完（不再携带、订单清空）→ 记忆已让它"在岗"，同样零命令；矿没了才重派。"""
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    k.on_game_state(_gs([_scv(1, gathering=900)], patches, seq=0))
    before = len(port.ops)
    k.on_game_state(_gs([_carrying_scv(1, 900)], patches, seq=30))
    # 已送达、订单空（真机里 SC2 会自动续采；观测帧没看到时维持器补一条**同矿** gather，
    # 幂等无害——关键是不会把它派去别的矿）
    new = port.gathers()[before:]
    assert new in ([], [(1, 900)])
    # 矿 900 采空消失、工人空闲 → 重派到 901
    k.on_game_state(_gs([_scv(1)], patches[1:], seq=90))
    new = port.gathers()[before:]
    assert new and new[0][1] == 901


def test_foreign_orders_are_not_touched():
    """带 build/move 等外来订单的工人：不改派（旧逻辑的 gather 会顶掉排队的 build 单
    —— 真机"提案自动应用了但对局没反应"的链条），也不占采矿名额。"""
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    builder = _u(1, "SCV", orders=[Order(ability="Build", target_tag=None)])
    idle = _scv(2)
    k.on_game_state(_gs([builder, idle], patches, seq=0))
    ops = port.gathers()   # [(工人tag, 矿tag)]
    assert all(w != 1 for w, _ in ops), "建造中的工人不许被派去采矿"
    assert any(w == 2 for w, _ in ops), "真空闲的工人照常补位"


def test_carrying_worker_with_foreign_order_is_not_touched():
    """§0.53 根因③：扛着矿但订单是外来能力（SCV 被派去建造时矿还在手上）→ 同样
    不接管。真机链条：flight 实体一出现就放人 → 维持器看到"扛货无记忆"的它 →
    gather 顶掉刚发的建造单 → 0% 补给站被弃。"""
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    builder = Unit(tag=1, type_name="SCV", position=Point2(0.0, 0.0), owner=Owner.SELF,
                   hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0,
                   orders=[Order(ability="SupplyDepot", target_tag=None)],
                   is_carrying_minerals=True)   # 扛着矿被派去建造
    idle = _scv(2)
    k.on_game_state(_gs([builder, idle], patches, seq=0))
    ops = port.gathers()
    assert all(w != 1 for w, _ in ops), "扛货但被外来能力征走的工人不许被派去采矿"
    assert any(w == 2 for w, _ in ops), "真空闲的工人照常补位"


def test_mining_family_by_ability_survives_missing_target_tag():
    """§0.53 根因①的行为面：Gather 单丢了 target_tag（驱动翻译事故的现场形态）时，
    按**能力名**仍判为采矿族 —— 归维持器管（可重派活矿），而不是误判成外来订单。"""
    port = _Port()
    k = _keeper(port)
    patches = _patches(2)
    # 订单是 Gather 但 target 丢了：矿 900 已采空（patches 只给 901）
    orphan = _u(1, "SCV", orders=[Order(ability="Gather", target_tag=None)])
    k.on_game_state(_gs([orphan], patches[1:], seq=0))
    ops = port.gathers()
    assert (1, 901) in ops, "采矿族（按能力名判）仍归维持器重派到活矿"


def test_stale_build_order_reclaimed_after_completion():
    """真机事故（2026-08-24 用户实战）：SCV 建完房站着不回矿。

    根因：SC2 完工后**不清**工兵的建造单（实测挂 45s+），维持器把残留单
    当外来订单整体跳过 → 永不接管。修法：订单指向的建筑已完工 = 残留单，
    安全接管派回矿；在建中（progress<1）仍是外来单，保护不动。"""
    from game import GameState, Grid, Owner, Point2, Unit, Order
    from game.catalog import load_all
    from production.economy import EconomyKeeper

    CAT = load_all()

    def u(tag, name, x, y, orders=(), prog=1.0, owner=Owner.SELF):
        return Unit(tag=tag, type_name=name, position=Point2(x, y), owner=owner,
                    hp=1, hp_max=1, shield=0, energy=0, build_progress=prog,
                    orders=list(orders))

    g = Grid(1, 1, [[0]])
    patches = [Unit(tag=900 + i, type_name="MINERALFIELD", position=Point2(126 + i * 2, 118),
                    owner=Owner.NEUTRAL, hp=1, hp_max=1, shield=0, energy=0,
                    build_progress=1.0) for i in range(8)]
    miners = [u(100 + i, "SCV", 126 + i * 2, 117,
                orders=[Order(ability="Gather", target_tag=900 + i)]) for i in range(7)]

    class _P:
        submitted = []

        def submit_operations(self, ops):
            self.submitted.extend(ops)

    def _run(extra_units, probe_tag):
        _P.submitted.clear()
        gs = GameState(seq=100, game_time=15.0, minerals=200, vespene=0,
                       supply_used=9, supply_cap=13,
                       units=[u(1, "COMMANDCENTER", 131, 120)] + miners + extra_units,
                       map_size=(176, 160), creep=g, visibility=g, resources=patches)
        k = EconomyKeeper(CAT, _P())
        k.set_target("mineral", 8)
        k.set_target("gas", 0)
        k.on_game_state(gs)
        return [o for o in _P.submitted if probe_tag in o.unit_tags]

    # 残留单（指向已完工 depot）→ 接管，gather 派回矿
    ops = _run([
        u(800, "SUPPLYDEPOT", 132, 108, prog=1.0),
        u(4338745345, "SCV", 133, 108,
          orders=[Order(ability="SupplyDepot", target_pos=Point2(132, 108))]),
    ], 4338745345)
    assert ops and ops[0].action == "gather", "完工残留单必须被派回矿"

    # 在建单（depot progress 0.5）→ 保护 intact，不接管
    ops = _run([
        u(801, "SUPPLYDEPOT", 134, 108, prog=0.5),
        u(4338745346, "SCV", 134, 108,
          orders=[Order(ability="SupplyDepot", target_pos=Point2(134, 108))]),
    ], 4338745346)
    assert ops == [], "在建中的建造单不许碰（§0.46 保护）"
