"""frame/economy（B11）：维持器状态 → 帧。

守的是"只 join、不复算规则"：目标值、容量（矿 2 / 气 3）一律取维持器给的，
前端与 adapt 都不重写这套判断（红线 C3）。
"""
import pytest

from game.catalog import load_terran
from game.geometry import Grid, Point2
from game.ports import ApplyResult
from game.state import GameState, Order, Owner, Unit
from production.economy import EconomyKeeper, EconomyPolicy

from view.adapt import economy_frame
from view.encode import to_json

CAT = load_terran()


class _Sink:
    def __init__(self):
        self.ops: list = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)


def _scv(tag: int, x: float, target: int | None = None) -> Unit:
    orders = [Order(ability="HARVEST_GATHER", target_tag=target)] if target else []
    return Unit(tag=tag, type_name="SCV", position=Point2(x, 10.0), owner=Owner.SELF,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0, orders=orders)


def _node(tag: int, name: str, x: float) -> Unit:
    return Unit(tag=tag, type_name=name, position=Point2(x, 12.0), owner=Owner.NEUTRAL,
                hp=1.0, hp_max=1.0, shield=0.0, energy=0.0, build_progress=1.0, orders=[])


def _gs(units, resources, seq: int = 1) -> GameState:
    g = Grid(1, 1, [[0]])
    return GameState(seq=seq, game_time=float(seq), minerals=50, vespene=0,
                     supply_used=len(units), supply_cap=30, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


def _keeper(**policy_kw) -> tuple[EconomyKeeper, _Sink]:
    sink = _Sink()
    keeper = EconomyKeeper(CAT, sink, policy=EconomyPolicy(**policy_kw) if policy_kw else None)
    return keeper, sink


def test_frame_reports_targets_and_actuals():
    """4 个工兵 + 2 个矿脉（每矿上限 2）→ 矿目标 4；收敛后实际也是 4。"""
    keeper, _ = _keeper()
    nodes = [_node(500, "MINERALFIELD", 8.0), _node(501, "MINERALFIELD", 9.0)]
    scvs = [_scv(1, 1.0), _scv(2, 2.0), _scv(3, 3.0), _scv(4, 4.0)]
    gs = _gs(scvs, nodes)
    keeper.on_game_state(gs)                      # 第一帧：把闲人派去采矿

    # 第二帧：把上一帧的分配反映进 orders（真机由 driver+SC2 完成）
    plan = keeper.snapshot(gs)["assigned"]
    assigned = [_scv(int(t), float(t), plan[t]) for t in plan]
    gs2 = _gs(assigned, nodes, seq=2)
    keeper.on_game_state(gs2)

    f = economy_frame(keeper.snapshot(gs2))
    by_task = {t.task: t for t in f.tasks}
    assert by_task["mineral"].target == 4          # 2 矿 × 上限 2
    assert by_task["mineral"].actual == 4
    assert by_task["mineral"].quota is None        # 没设绝对配额 → 按 per-node 比例
    assert f.domain_workers == 4


def test_capacity_and_saturation_come_from_keeper():
    """容量（矿 2 / 气 3）是维持器的真机常数，帧里原样带出，前端不硬编码。"""
    keeper, _ = _keeper()
    nodes = [_node(500, "MINERALFIELD", 8.0)]
    scvs = [_scv(1, 1.0, 500), _scv(2, 2.0, 500)]
    gs = _gs(scvs, nodes)
    keeper.on_game_state(gs)
    f = economy_frame(keeper.snapshot(gs))
    assert len(f.nodes) == 1
    n = f.nodes[0]
    assert (n.kind, n.capacity) == ("mineral", 2)
    assert n.workers == 2 and n.saturated is True


def test_quota_persists_even_when_unreachable_this_frame():
    """**这就是 issues P9 的修复**：精炼厂还没建好时 quota 挂着、target 为 0。

    只给 target 的话用户会以为意图又蒸发了 —— 所以帧里两个数都要有。
    """
    keeper, _ = _keeper(gas_workers=6)
    f = economy_frame(keeper.snapshot(_gs([], [])))   # 一个气矿都没有
    gas = {t.task: t for t in f.tasks}["gas"]
    assert gas.quota == 6, "配额是持久的"
    assert gas.target == 0, "但本帧可达目标为 0（没有气矿）"
    assert {t.task: t for t in f.tasks}["mineral"].quota is None


def test_set_target_is_absolute_not_delta():
    """ADR-0030 D2：`assign_workers(task, n)` = 维持 n 个（幂等），不是"再派 n 个"。

    连续设两次同一个值，配额仍是那个值 —— delta 语义在每帧重放里必然累加。
    """
    keeper, _ = _keeper()
    keeper.set_target("gas", 3)
    keeper.set_target("gas", 3)
    f = economy_frame(keeper.snapshot(_gs([], [])))
    assert {t.task: t.quota for t in f.tasks}["gas"] == 3


def test_stable_state_emits_nothing():
    """ADR-0030 验收 6：目标与实际一致时不发任何 op → `emitted_count == 0`。"""
    keeper, sink = _keeper()
    nodes = [_node(500, "MINERALFIELD", 8.0)]
    scvs = [_scv(1, 1.0, 500), _scv(2, 2.0, 500)]
    gs = _gs(scvs, nodes)
    keeper.on_game_state(gs)
    n_first = len(sink.ops)
    keeper.on_game_state(_gs(scvs, nodes, seq=2))
    assert len(sink.ops) == n_first, "稳定态不该再发命令"
    assert economy_frame(keeper.snapshot(gs)).emitted_count == 0


def test_reservations_appear_in_frame():
    """建造征用要能看见 —— 否则"这个 SCV 为什么不采矿"没法解释。"""
    keeper, _ = _keeper()
    keeper.reservations.reserve("production/build#1", 7)
    f = economy_frame(keeper.snapshot(_gs([], [])))
    assert [(r.tag, r.owner) for r in f.reserved] == [(7, "production/build#1")]


def test_frame_is_json_serializable():
    keeper, _ = _keeper()
    d = to_json(economy_frame(keeper.snapshot(_gs([], []))))
    assert set(d) == {"tasks", "nodes", "reserved", "emitted_count", "domain_workers", "quotas"}
    assert d["quotas"]["mineral_per_patch"] >= 1


def _cc(tag: int, x: float) -> Unit:
    return Unit(tag=tag, type_name="COMMANDCENTER", position=Point2(x, 10.0),
                owner=Owner.SELF, hp=1500.0, hp_max=1500.0, shield=0.0, energy=0.0,
                build_progress=1.0, orders=[])


def test_node_base_tag_is_nearest_dropoff_building():
    """B12：节点带 base_tag（最近的己方 dropoff 建筑 tag）——前端拿它与
    frame/world.units 按 tag join，主基地标签写「矿 12/16 气 3/6」不需要空间匹配。"""
    keeper, _ = _keeper()
    cc = _cc(900, 8.0)
    nodes = [_node(500, "MINERALFIELD", 8.5), _node(501, "MINERALFIELD", 9.0)]
    gs = _gs([cc], nodes)
    keeper.on_game_state(gs)
    f = economy_frame(keeper.snapshot(gs))
    assert {n.base_tag for n in f.nodes} == {900}
    assert to_json(f)["nodes"][0]["base_tag"] == 900


def test_node_base_tag_none_without_base():
    """没有己方基地时 base_tag 为 None（如实，不端出编造的归属）。"""
    keeper, _ = _keeper()
    nodes = [_node(500, "MINERALFIELD", 8.0)]
    gs = _gs([], nodes)
    keeper.on_game_state(gs)
    f = economy_frame(keeper.snapshot(gs))
    assert len(f.nodes) == 1 and f.nodes[0].base_tag is None


def test_node_base_tag_prefers_nearest_of_multiple_bases():
    """多基地时各节点归最近的那个（多基地语义天然成立，V1 单基地只是它的特例）。"""
    keeper, _ = _keeper()
    cc_a = _cc(900, 5.0)
    cc_b = _cc(901, 40.0)
    nodes = [_node(500, "MINERALFIELD", 6.0), _node(501, "MINERALFIELD", 41.0)]
    gs = _gs([cc_a, cc_b], nodes)
    keeper.on_game_state(gs)
    f = economy_frame(keeper.snapshot(gs))
    by_tag = {n.tag: n.base_tag for n in f.nodes}
    assert by_tag[500] == 900 and by_tag[501] == 901
