"""view.adapt：动态面 —— 派生量必须在后端算完（红线 C2/C3），且不猜。"""
import base64

import pytest

from game.catalog import load_all
from game.geometry import Grid, Point2
from game.state import GameState, Order, Owner, Unit
from planner.build_order import ProductionModuleInstance
from planner.planner import Planner
from tactical_map.base import instantiate_spawn, load_ladder_map

from view.adapt import grids_of, projection_frame, session_frame, world_frame
from view.encode import to_json


def _grid(w=4, h=3, v=0) -> Grid:
    return Grid(width=w, height=h, data=[[v] * w for _ in range(h)])


def _unit(tag, type_name, x, y, **kw) -> Unit:
    base = dict(
        owner=Owner.SELF, hp=45.0, hp_max=45.0, shield=0.0, energy=0.0,
        build_progress=1.0, orders=[], facing=0.0, buffs=(),
        is_carrying_minerals=False, is_carrying_vespene=False,
    )
    base.update(kw)
    return Unit(tag=tag, type_name=type_name, position=Point2(x=x, y=y), **base)


def _state(units, resources=(), **kw) -> GameState:
    base = dict(
        seq=10, game_time=123.0, minerals=350, vespene=88,
        supply_used=21, supply_cap=30, map_size=(176, 160),
        creep=_grid(), visibility=_grid(v=2),
    )
    base.update(kw)
    return GameState(units=list(units), resources=list(resources), **base)


CAT = load_all()


# ---------------- 类型翻译 ----------------

def test_translates_burnysc2_name_to_stable_id():
    f = world_frame(_state([_unit(1, "MARINE", 10, 10)]), CAT)
    assert f.units[0].stable_id == "terran/marine"
    assert f.units[0].form is None


def test_variant_normalized_with_form_kept():
    """架起的坦克归一到主名，形态另存 form —— flow 只认主名，UI 要能画出架起状态。"""
    f = world_frame(_state([_unit(2, "SIEGETANKSIEGED", 30, 30, hp=175, hp_max=175)]), CAT)
    assert f.units[0].stable_id == "terran/siegetank"
    assert f.units[0].form == "sieged"


def test_unregistered_type_is_marked_unknown_never_burnysc2_name():
    """catalog 没登记的类型显式打 unknown/ 前缀。

    红线 C1 禁止动态帧出现 burnysc2 名；而且这样 UI 能显示"后端没登记这个类型"，
    是可发现的缺口而不是静默污染。
    """
    f = world_frame(_state([_unit(3, "ZZZ_FAKE", 5, 5, owner=Owner.NEUTRAL)]), CAT)
    assert f.units[0].stable_id == "unknown/zzz_fake"
    assert "ZZZ" not in to_json(f)["units"][0]["stable_id"]


# ---------------- footprint（ADR-0027） ----------------

def test_footprint_matches_the_build_slot_it_sits_on():
    """把建筑放在某槽位的 reported_position 上，反解出的 footprint 必须等于该槽位。

    这条把 adapt 与 statics 钉在一起：两边都必须用同一份 ADR-0027 换算。
    """
    tpl = load_ladder_map()
    spawn, layout = sorted(tpl.spawns.items())[0]
    layer = instantiate_spawn(tpl, layout, Point2(x=layout.origin.x, y=layout.origin.y))
    slot = next(s for s in layer.build_slots.values() if s.size == 3)

    rp = slot.reported_position
    f = world_frame(_state([_unit(9, "BARRACKS", rp.x, rp.y, hp=1000, hp_max=1000)]), CAT)
    fp = f.units[0].footprint
    assert fp is not None
    assert fp.tl == (slot.tl.x, slot.tl.y)
    assert fp.br == (slot.br.x, slot.br.y)


def test_non_building_has_no_footprint():
    f = world_frame(_state([_unit(1, "MARINE", 10, 10)]), CAT)
    assert f.units[0].footprint is None


# ---------------- 订单 / 在训 / 挂件 ----------------

def test_order_atom_mapped_and_raw_kept():
    u = _unit(1, "MARINE", 10, 10, orders=[Order(ability="ATTACK", target_pos=Point2(x=50, y=60))])
    o = world_frame(_state([u]), CAT).units[0].order
    assert o is not None
    assert o.atom == "attack_move_to"
    assert o.ability_raw == "ATTACK"
    assert o.target_pos == (50.0, 60.0)


def test_unknown_ability_keeps_raw_and_nulls_atom():
    """认不出的能力不猜原子名（不静默），但原始名要留着给调试面板。"""
    u = _unit(1, "MARINE", 10, 10, orders=[Order(ability="SOMETHING_WEIRD")])
    o = world_frame(_state([u]), CAT).units[0].order
    assert o is not None and o.atom is None and o.ability_raw == "SOMETHING_WEIRD"


def test_producing_listed_for_ready_building():
    rax = _unit(20, "BARRACKS", 40.5, 40.5, hp=1000, hp_max=1000,
                orders=[Order(ability="MARINE")])
    f = world_frame(_state([rax]), CAT)
    # progress 恒 None（rev 13）：SC2 订单不带进度，发假 0.0 等于把"未知"读成"刚开始"
    assert f.units[0].producing == [
        type(f.units[0].producing[0])(stable_id="terran/marine", progress=None)
    ]


def test_producing_none_while_under_construction():
    rax = _unit(20, "BARRACKS", 40.5, 40.5, build_progress=0.4, orders=[Order(ability="MARINE")])
    assert world_frame(_state([rax]), CAT).units[0].producing is None


def test_addon_detected():
    u = _unit(21, "BARRACKSREACTOR", 44.0, 40.0, hp=400, hp_max=400)
    assert world_frame(_state([u]), CAT).units[0].addon == "reactor"


# ---------------- 分组 / 资源 / 栅格 ----------------

def test_group_join_comes_from_caller_not_guessed():
    """分组归属由 Allocator 读模型 join（B1）；B0 不传就是 None，绝不用"离得近"猜。"""
    gs = _state([_unit(1, "MARINE", 10, 10), _unit(2, "MARINE", 11, 10)])
    assert [u.group_id for u in world_frame(gs, CAT).units] == [None, None]
    f = world_frame(gs, CAT, group_of={1: "G_INF"})
    assert [u.group_id for u in f.units] == ["G_INF", None]


def test_resource_state_counts_harvesters_by_target_tag():
    node = _unit(5000, "MINERALFIELD", 20, 20, owner=Owner.NEUTRAL)
    scv1 = _unit(1, "SCV", 21, 20, orders=[Order(ability="HARVEST_GATHER", target_tag=5000)])
    scv2 = _unit(2, "SCV", 22, 20, orders=[Order(ability="HARVEST_GATHER", target_tag=5000)])
    idle = _unit(3, "SCV", 60, 60)
    f = world_frame(_state([scv1, scv2, idle], resources=[node]), CAT)
    assert f.resource_state == [type(f.resource_state[0])(tag=5000, remaining=None, workers=2)]


def test_grids_omitted_unless_requested():
    gs = _state([_unit(1, "MARINE", 10, 10)])
    assert world_frame(gs, CAT).grids is None
    g = world_frame(gs, CAT, grids=grids_of(gs)).grids
    assert g is not None and g.visibility is not None
    assert list(base64.b64decode(g.visibility.data_b64))[:4] == [2, 2, 2, 2]


def test_enemy_clusters_none_because_backend_lacks_clustering():
    """聚类算法在 flow.vocab 的 forbidden.spatial_tools 里登记为未实现 → 发 None，前端降级。"""
    assert world_frame(_state([_unit(1, "MARINE", 1, 1)]), CAT).enemy_clusters is None


# ---------------- 投影 / 会话 ----------------

def _econ_state() -> GameState:
    """开局态。`game_time=0` 很关键：`Planner.project(..., until=)` 的 until 是**绝对**游戏时间。"""
    cc = _unit(1, "COMMANDCENTER", 30.5, 30.5, hp=1500, hp_max=1500)
    scvs = [_unit(100 + i, "SCV", 28 + i * 0.5, 33, hp=45, hp_max=45) for i in range(12)]
    nodes = [_unit(5000 + i, "MINERALFIELD", 26 + i, 35, owner=Owner.NEUTRAL) for i in range(8)]
    return _state([cc, *scvs], resources=nodes, game_time=0.0,
                  minerals=50, vespene=0, supply_used=12, supply_cap=15)


def test_projection_frame_from_real_planner():
    """用真 planner 跑一遍，帧只做重排：逐点与 curve 字段全等。"""
    gs = _econ_state()
    seq = [ProductionModuleInstance(instance_id="m0", module_ref="basic_opening", version=1, params={})]
    curve = Planner(CAT).project(gs, seq, until=120.0)
    f = projection_frame(curve, based_on_seq=gs.seq, based_on_game_time=gs.game_time,
                         horizon=120.0, queue_name="main")
    assert f.source == {"kind": "live_queue", "queue_name": "main"}
    assert len(f.points) == len(curve.points) > 0
    for p, q in zip(f.points, curve.points, strict=True):
        assert (p.t, p.minerals, p.gas) == (q.t, q.minerals, q.gas)
        assert p.in_flight_count == q.in_flight_count
    assert len(f.events) == len(curve.events)


def test_projection_frame_requires_exactly_one_source():
    curve = Planner(CAT).project(_econ_state(), [], until=1.0)
    with pytest.raises(ValueError):
        projection_frame(curve, based_on_seq=1, based_on_game_time=0, horizon=1)
    with pytest.raises(ValueError):
        projection_frame(curve, based_on_seq=1, based_on_game_time=0, horizon=1,
                         queue_name="main", plan_id="p1")


def test_session_frame_shape():
    s = session_frame("对局中", frame_source="replay", game_time=42.1234,
                      map_name="LadderMap", my_race="terran", enemy_race="protoss")
    assert (s.state, s.frame_source, s.game_time) == ("对局中", "replay", 42.123)
    assert to_json(s)["error"] is None
