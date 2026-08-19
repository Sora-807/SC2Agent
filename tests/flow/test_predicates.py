"""flow 谓词全目录单测：eval_when 对 spec-003 §4.1 每个已实现谓词逐一断言 + 词表边界。"""
import pytest

from game import GameState, Grid, Owner, Point2, Unit
from flow.predicates import EvalCtx, UNIMPLEMENTED_PREDICATE_OPS, eval_when
from tactical_map import load_region_layer

LAYER_YAML = """
map_name: p
size: [4, 4]
big_palette: {1: main_base, 2: field}
big_grid:
  - "1 1 2 2"
  - "1 1 2 2"
  - "1 1 2 2"
  - "1 1 2 2"
leaf_palette: {1: main_ramp}
leaf_grid:
  - "0 1 0 0"
  - "0 1 0 0"
  - "0 0 0 0"
  - "0 0 0 0"
big_regions:
  main_base: {anchor: [1, 1]}
  field: {anchor: [3, 3]}
regions:
  main_ramp: {parent: main_base, anchor: [1, 0]}
"""


class FakeAllocator:
    def __init__(self, groups=None):
        self._groups = groups or {}

    def count(self, gid, type_name=None):
        g = self._groups.get(gid, {})
        if type_name is None:
            return sum(len(s) for s in g.values())
        return len(g.get(type_name, set()))

    def expand_all(self, gid):
        g = self._groups.get(gid, {})
        return sorted({t for s in g.values() for t in s})


def _u(tag, x, y, owner=Owner.SELF, type_name="MARINE", hp=45.0, hp_max=45.0, progress=1.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=hp, hp_max=hp_max, shield=0.0, energy=0.0, build_progress=progress)


def _gs(units, game_time=10.0):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=game_time, minerals=50, vespene=0, supply_used=0,
                     supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g)


def _ctx(gs=None, alloc=None, bindings=None, params=None, variables=None, layer=None,
         start=0.0, entered=5.0):
    return EvalCtx(
        gs=gs or _gs([]), allocator=alloc or FakeAllocator(), bindings=bindings or {},
        params=params or {}, variables=variables or {}, strategy_start=start,
        step_entered=entered, region_layer=layer,
    )


def _layer():
    return load_region_layer(LAYER_YAML)


# ---- 字面量 / 引用节点 ----


def test_literal_passthrough():
    assert eval_when(5, _ctx()) == 5
    assert eval_when([1, 2], _ctx()) == [1, 2]
    assert eval_when("main", _ctx()) == "main"


def test_const_param_var_nodes():
    ctx = _ctx(params={"p1": 7}, variables={"v1": 9})
    assert eval_when({"const": 3}, ctx) == 3
    assert eval_when({"param": "p1"}, ctx) == 7
    assert eval_when({"var": "v1"}, ctx) == 9
    assert eval_when({"param": "missing"}, ctx) is None
    assert eval_when({"var": "missing"}, ctx) is None


# ---- 计数 / 时间谓词 ----


def test_group_count_with_and_without_type():
    alloc = FakeAllocator({"G1": {"MARINE": {1, 2}, "SCV": {3}}})
    ctx = _ctx(alloc=alloc, bindings={"main": "G1"})
    assert eval_when({"op": "group_count", "args": ["main"]}, ctx) == 3
    assert eval_when({"op": "group_count", "args": ["main", "MARINE"]}, ctx) == 2
    assert eval_when({"op": "group_count", "args": ["main", "MEDIVAC"]}, ctx) == 0
    assert eval_when({"op": "group_count", "args": ["unbound"]}, ctx) == 0


def test_time_predicates():
    ctx = _ctx(gs=_gs([], game_time=42.0), start=10.0, entered=40.0)
    assert eval_when({"op": "game_time"}, ctx) == 42.0
    assert eval_when({"op": "strategy_elapsed"}, ctx) == 32.0
    assert eval_when({"op": "step_elapsed"}, ctx) == 2.0


# ---- 比较 / 逻辑 ----


@pytest.mark.parametrize("op,a,b,want", [
    (">=", 1, 1, True), (">=", 0, 1, False),
    (">", 2, 1, True), (">", 1, 1, False),
    ("<=", 1, 1, True), ("<=", 2, 1, False),
    ("<", 1, 2, True), ("<", 2, 1, False),
    ("==", 2, 2, True), ("==", 2, 3, False),
    ("!=", 2, 3, True), ("!=", 2, 2, False),
])
def test_comparison_ops(op, a, b, want):
    assert eval_when({"op": op, "args": [a, b]}, _ctx()) is want


def test_logic_ops():
    ctx = _ctx()
    t = {"const": True}
    f = {"const": False}
    assert eval_when({"op": "and", "args": [t, t]}, ctx) is True
    assert eval_when({"op": "and", "args": [t, f]}, ctx) is False
    assert eval_when({"op": "or", "args": [f, t]}, ctx) is True
    assert eval_when({"op": "or", "args": [f, f]}, ctx) is False
    assert eval_when({"op": "not", "args": [f]}, ctx) is True
    # 组合：not(x and y)
    assert eval_when({"op": "not", "args": [{"op": "and", "args": [t, f]}]}, ctx) is True


# ---- 空间谓词 ----


def test_arrived_true_false_empty():
    gs = _gs([_u(1, 0, 0), _u(2, 2, 0)])
    alloc = FakeAllocator({"G1": {"MARINE": {1, 2}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"main": "G1"})
    node = {"op": "arrived", "args": ["main", [5.0, 0.0], 5.0]}  # 中心 (1,0) 距 (5,0)=4
    assert eval_when(node, ctx) is True
    assert eval_when({"op": "arrived", "args": ["main", [5.0, 0.0], 3.0]}, ctx) is False
    empty = _ctx(gs=_gs([]), alloc=FakeAllocator({"G1": {}}), bindings={"main": "G1"})
    assert eval_when(node, empty) is False  # 空 group 恒 false（spec-003 §3.2）


def test_group_center_and_distance_between():
    gs = _gs([_u(1, 0, 0), _u(2, 2, 0), _u(3, 6, 0), _u(4, 10, 0)])
    alloc = FakeAllocator({"A": {"MARINE": {1, 2}}, "B": {"MARINE": {3, 4}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"a": "A", "b": "B"})
    assert eval_when({"op": "group_center", "args": ["a"]}, ctx) == Point2(1.0, 0.0)
    assert eval_when({"op": "group_center", "args": ["b"]}, ctx) == Point2(8.0, 0.0)
    assert eval_when({"op": "distance_between", "args": ["a", "b"]}, ctx) == 7.0
    empty = _ctx(alloc=FakeAllocator({"A": {}}), bindings={"a": "A"})
    assert eval_when({"op": "distance_between", "args": ["a", "b"]}, empty) == float("inf")


def test_enemy_count_near():
    gs = _gs([
        _u(10, 1, 0, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(11, 2, 0, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(12, 20, 20, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(1, 0, 0),
    ])
    ctx = _ctx(gs=gs)
    assert eval_when({"op": "enemy_count_near", "args": [[0, 0], 5.0]}, ctx) == 2
    assert eval_when({"op": "enemy_count_near", "args": [[0, 0], 0.5]}, ctx) == 0  # (1,0) 距 1.0 出圈
    # 锚点用区域名（走 resolver）
    ctx2 = _ctx(gs=gs, layer=_layer())
    assert eval_when({"op": "enemy_count_near", "args": ["main_base", 2.0]}, ctx2) == 2
    # 无 layer 时区域名解析失败 → 0
    assert eval_when({"op": "enemy_count_near", "args": ["main_base", 2.0]}, ctx) == 0


def test_group_hp_ratio():
    gs = _gs([_u(1, 0, 0, hp=45.0), _u(2, 1, 0, hp=15.0)])
    alloc = FakeAllocator({"G1": {"MARINE": {1, 2}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"main": "G1"})
    assert eval_when({"op": "group_hp_ratio", "args": ["main"]}, ctx) == pytest.approx(2 / 3)
    empty = _ctx(alloc=FakeAllocator({"G1": {}}), bindings={"main": "G1"})
    assert eval_when({"op": "group_hp_ratio", "args": ["main"]}, empty) == 0.0


def test_region_center():
    ctx = _ctx(layer=_layer())
    assert eval_when({"op": "region_center", "args": ["main_base"]}, ctx) == Point2(1, 1)
    assert eval_when({"op": "region_center", "args": ["nope"]}, ctx) is None
    assert eval_when({"op": "region_center", "args": ["main_base"]}, _ctx()) is None


def test_unit_count():
    gs = _gs([_u(1, 0, 0), _u(2, 0, 0), _u(3, 0, 0, owner=Owner.ENEMY), _u(4, 0, 0, type_name="SCV")])
    ctx = _ctx(gs=gs)
    assert eval_when({"op": "unit_count", "args": ["MARINE"]}, ctx) == 2  # 只数 SELF
    assert eval_when({"op": "unit_count", "args": ["SCV"]}, ctx) == 1


# ---- 区域归属谓词（ADR-0029 消费方）----


def test_group_center_in_region():
    layer = _layer()
    alloc = FakeAllocator({"G1": {"MARINE": {1, 2}}})
    # 组中心在 main_base（左下两列）→ True；在 field → False；无 layer → False
    gs_in = _gs([_u(1, 0.5, 0.5), _u(2, 1.5, 0.5)])
    ctx = _ctx(gs=gs_in, alloc=alloc, bindings={"main": "G1"}, layer=layer)
    assert eval_when({"op": "group_center_in_region", "args": ["main", "main_base"]}, ctx) is True
    gs_out = _gs([_u(1, 2.5, 0.5), _u(2, 3.5, 0.5)])
    ctx2 = _ctx(gs=gs_out, alloc=alloc, bindings={"main": "G1"}, layer=layer)
    assert eval_when({"op": "group_center_in_region", "args": ["main", "main_base"]}, ctx2) is False
    assert eval_when({"op": "group_center_in_region", "args": ["main", "main_base"]}, _ctx(gs=gs_in, alloc=alloc, bindings={"main": "G1"})) is False


def test_enemy_visible_in():
    layer = _layer()
    gs = _gs([_u(9, 1.5, 1.5, owner=Owner.ENEMY, type_name="ZERGLING")])
    ctx = _ctx(gs=gs, layer=layer)
    assert eval_when({"op": "enemy_visible_in", "args": ["main_ramp"]}, ctx) is True
    assert eval_when({"op": "enemy_visible_in", "args": ["field"]}, ctx) is False
    assert eval_when({"op": "enemy_visible_in", "args": ["nope"]}, ctx) is False
    assert eval_when({"op": "enemy_visible_in", "args": ["main_ramp"]}, _ctx(gs=gs)) is False


def test_has_building():
    layer = _layer()
    depot = _u(5, 0.5, 0.5, type_name="SUPPLYDEPOT", progress=0.5)
    ready_depot = _u(6, 1.5, 0.5, type_name="SUPPLYDEPOT", progress=1.0)
    outside = _u(7, 3.5, 3.5, type_name="SUPPLYDEPOT", progress=1.0)
    ctx = _ctx(gs=_gs([depot, ready_depot, outside]), layer=layer)
    assert eval_when({"op": "has_building", "args": ["SUPPLYDEPOT"]}, ctx) is True
    assert eval_when({"op": "has_building", "args": ["SUPPLYDEPOT", None, True]}, ctx) is True
    assert eval_when({"op": "has_building", "args": ["SUPPLYDEPOT", "main_base"]}, ctx) is True
    assert eval_when({"op": "has_building", "args": ["SUPPLYDEPOT", "field"]}, ctx) is True
    assert eval_when({"op": "has_building", "args": ["BARRACKS"]}, ctx) is False
    only_partial = _ctx(gs=_gs([depot]))
    assert eval_when({"op": "has_building", "args": ["SUPPLYDEPOT", None, True]}, only_partial) is False


# ---- 词表边界 ----


def test_unknown_op_raises():
    with pytest.raises(ValueError, match="unknown op"):
        eval_when({"op": "fly_pred", "args": []}, _ctx())


@pytest.mark.parametrize("op", sorted(UNIMPLEMENTED_PREDICATE_OPS))
def test_unimplemented_predicates_raise_with_reason(op):
    with pytest.raises(ValueError, match="未实现"):
        eval_when({"op": op, "args": []}, _ctx())


def test_unimplemented_spatial_tools_raise():
    with pytest.raises(ValueError, match="未实现"):
        eval_when({"op": "nearest_units", "args": []}, _ctx())

