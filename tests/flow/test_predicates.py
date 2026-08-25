"""flow 谓词全目录单测：eval_when 对 spec-003 §4.1 每个已实现谓词逐一断言 + 词表边界。"""
import pytest

from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_all
from tests.factories import make_gs, make_unit
from flow.predicates import EvalCtx, UNIMPLEMENTED_PREDICATE_OPS, eval_when
from tactical_map import load_region_layer

CAT = load_all()

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
    return make_unit(tag, type_name, owner, x, y, hp=hp, hp_max=hp_max, progress=progress)


def _gs(units, game_time=10.0):
    return make_gs(units, seq=0, game_time=game_time, minerals=50, vespene=0,
                   supply_used=0, supply_cap=20)


def _ctx(gs=None, alloc=None, bindings=None, params=None, variables=None, layer=None,
         start=0.0, entered=5.0, catalog=CAT, definitions=None, diagnostics=None, step_id=""):
    return EvalCtx(
        gs=gs or _gs([]), allocator=alloc or FakeAllocator(), bindings=bindings or {},
        params=params or {}, variables=variables or {}, strategy_start=start,
        step_entered=entered, region_layer=layer, catalog=catalog,
        definitions=definitions, diagnostics=diagnostics, step_id=step_id,
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
    alloc = FakeAllocator({"G1": {"terran/marine": {1, 2}, "terran/scv": {3}}})
    ctx = _ctx(alloc=alloc, bindings={"main": "G1"})
    assert eval_when({"op": "group_count", "group": "main"}, ctx) == 3
    assert eval_when({"op": "group_count", "group": "main", "type": "terran/marine"}, ctx) == 2
    assert eval_when({"op": "group_count", "group": "main", "type": "MEDIVAC"}, ctx) == 0
    assert eval_when({"op": "group_count", "group": "unbound"}, ctx) == 0


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
    alloc = FakeAllocator({"G1": {"terran/marine": {1, 2}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"main": "G1"})
    node = {"op": "arrived", "group": "main", "target": [5.0, 0.0], "radius": 5.0}  # 中心 (1,0) 距 (5,0)=4
    assert eval_when(node, ctx) is True
    assert eval_when({"op": "arrived", "group": "main", "target": [5.0, 0.0], "radius": 3.0}, ctx) is False
    empty = _ctx(gs=_gs([]), alloc=FakeAllocator({"G1": {}}), bindings={"main": "G1"})
    assert eval_when(node, empty) is False  # 空 group 恒 false（spec-003 §3.2）


def test_group_center_and_distance_between():
    gs = _gs([_u(1, 0, 0), _u(2, 2, 0), _u(3, 6, 0), _u(4, 10, 0)])
    alloc = FakeAllocator({"A": {"terran/marine": {1, 2}}, "B": {"terran/marine": {3, 4}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"a": "A", "b": "B"})
    assert eval_when({"op": "group_center", "group": "a"}, ctx) == Point2(1.0, 0.0)
    assert eval_when({"op": "group_center", "group": "b"}, ctx) == Point2(8.0, 0.0)
    assert eval_when({"op": "distance_between", "a": "a", "b": "b"}, ctx) == 7.0
    empty = _ctx(alloc=FakeAllocator({"A": {}}), bindings={"a": "A"})
    assert eval_when({"op": "distance_between", "a": "a", "b": "b"}, empty) == float("inf")


def test_enemy_count_near():
    gs = _gs([
        _u(10, 1, 0, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(11, 2, 0, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(12, 20, 20, owner=Owner.ENEMY, type_name="ZERGLING"),
        _u(1, 0, 0),
    ])
    ctx = _ctx(gs=gs)
    assert eval_when({"op": "enemy_count_near", "anchor": [0, 0], "radius": 5.0}, ctx) == 2
    assert eval_when({"op": "enemy_count_near", "anchor": [0, 0], "radius": 0.5}, ctx) == 0  # (1,0) 距 1.0 出圈
    # 锚点用区域名（走 resolver）
    ctx2 = _ctx(gs=gs, layer=_layer())
    assert eval_when({"op": "enemy_count_near", "anchor": "main_base", "radius": 2.0}, ctx2) == 2
    # 无 layer 时区域名解析失败 → 0
    assert eval_when({"op": "enemy_count_near", "anchor": "main_base", "radius": 2.0}, ctx) == 0


def test_group_hp_ratio():
    gs = _gs([_u(1, 0, 0, hp=45.0), _u(2, 1, 0, hp=15.0)])
    alloc = FakeAllocator({"G1": {"terran/marine": {1, 2}}})
    ctx = _ctx(gs=gs, alloc=alloc, bindings={"main": "G1"})
    assert eval_when({"op": "group_hp_ratio", "group": "main"}, ctx) == pytest.approx(2 / 3)
    empty = _ctx(alloc=FakeAllocator({"G1": {}}), bindings={"main": "G1"})
    assert eval_when({"op": "group_hp_ratio", "group": "main"}, empty) == 0.0


def test_region_center():
    ctx = _ctx(layer=_layer())
    assert eval_when({"op": "region_center", "name": "main_base"}, ctx) == Point2(1, 1)
    assert eval_when({"op": "region_center", "name": "nope"}, ctx) is None
    assert eval_when({"op": "region_center", "name": "main_base"}, _ctx()) is None


# ---- point_toward（空间值工具，T4）----


def test_point_toward_direction_ratio_boundary():
    """point_toward：from 朝 toward 延伸 dist（单位向量×dist + from）；dist=0/重合/负→from。"""
    def pt(frm, twd, d):
        return eval_when({"op": "point_toward", "origin": frm, "toward": twd, "dist": d}, _ctx())
    assert pt([0, 0], [10, 0], 3) == Point2(3.0, 0.0)    # 正东 3
    assert pt([0, 0], [0, 10], 5) == Point2(0.0, 5.0)    # 正北 5
    assert pt([1, 1], [4, 5], 5) == Point2(4.0, 5.0)     # (3,4) 归一×5=(3,4)+from=(4,5)
    assert pt([0, 0], [10, 0], 0) == Point2(0.0, 0.0)    # dist=0 → from
    assert pt([5, 5], [5, 5], 10) == Point2(5.0, 5.0)    # 重合 → from
    assert pt([0, 0], [10, 0], -3) == Point2(0.0, 0.0)   # 负 dist → from


def test_point_toward_none_and_point_name_via_layer():
    """None 输入 → None（arrived 判 false，不崩）；点位名经 layer 解析（T4 layer 尾参）。
    main_base anchor=[1,1]，from=[0,0] 朝它 dist=√2 → (1,1)；无 layer 时点位名→None。"""
    import math
    ctx = _ctx(layer=_layer())
    assert eval_when({"op": "point_toward", "origin": None, "toward": [10, 0], "dist": 3}, ctx) is None
    assert eval_when({"op": "point_toward", "origin": [0, 0], "toward": None, "dist": 3}, ctx) is None
    r = eval_when({"op": "point_toward", "origin": [0, 0], "toward": "main_base", "dist": math.sqrt(2)}, ctx)
    assert r is not None
    assert r.x == pytest.approx(1.0) and r.y == pytest.approx(1.0)
    assert eval_when({"op": "point_toward", "origin": [0, 0], "toward": "main_base", "dist": 2}, _ctx()) is None


def test_unit_count():
    gs = _gs([_u(1, 0, 0), _u(2, 0, 0), _u(3, 0, 0, owner=Owner.ENEMY), _u(4, 0, 0, type_name="SCV")])
    ctx = _ctx(gs=gs)
    assert eval_when({"op": "unit_count", "type": "terran/marine"}, ctx) == 2  # 只数 SELF
    assert eval_when({"op": "unit_count", "type": "terran/scv"}, ctx) == 1


def test_unit_count_normalizes_variants():
    """形态变体归一（T1/T3）：架起态实体（SIEGETANKSIEGED）计入 terran/siegetank；
    burnysc2 名不再是 authoring 词汇（传主名/变体名都算未登记 → 0）。"""
    gs = _gs([_u(1, 0, 0, type_name="SIEGETANK"), _u(2, 0, 0, type_name="SIEGETANKSIEGED")])
    assert eval_when({"op": "unit_count", "type": "terran/siegetank"}, _ctx(gs=gs)) == 2
    assert eval_when({"op": "unit_count", "type": "SIEGETANK"}, _ctx(gs=gs)) == 0
    assert eval_when({"op": "unit_count", "type": "SIEGETANKSIEGED"}, _ctx(gs=gs)) == 0


# ---- 区域归属谓词（ADR-0029 消费方）----


def test_group_center_in_region():
    layer = _layer()
    alloc = FakeAllocator({"G1": {"terran/marine": {1, 2}}})
    # 组中心在 main_base（左下两列）→ True；在 field → False；无 layer → False
    gs_in = _gs([_u(1, 0.5, 0.5), _u(2, 1.5, 0.5)])
    ctx = _ctx(gs=gs_in, alloc=alloc, bindings={"main": "G1"}, layer=layer)
    assert eval_when({"op": "group_center_in_region", "group": "main", "region": "main_base"}, ctx) is True
    gs_out = _gs([_u(1, 2.5, 0.5), _u(2, 3.5, 0.5)])
    ctx2 = _ctx(gs=gs_out, alloc=alloc, bindings={"main": "G1"}, layer=layer)
    assert eval_when({"op": "group_center_in_region", "group": "main", "region": "main_base"}, ctx2) is False
    assert eval_when({"op": "group_center_in_region", "group": "main", "region": "main_base"}, _ctx(gs=gs_in, alloc=alloc, bindings={"main": "G1"})) is False


def test_enemy_visible_in():
    layer = _layer()
    gs = _gs([_u(9, 1.5, 1.5, owner=Owner.ENEMY, type_name="ZERGLING")])
    ctx = _ctx(gs=gs, layer=layer)
    assert eval_when({"op": "enemy_visible_in", "region": "main_ramp"}, ctx) is True
    assert eval_when({"op": "enemy_visible_in", "region": "field"}, ctx) is False
    assert eval_when({"op": "enemy_visible_in", "region": "nope"}, ctx) is False
    assert eval_when({"op": "enemy_visible_in", "region": "main_ramp"}, _ctx(gs=gs)) is False


def test_has_building():
    layer = _layer()
    depot = _u(5, 0.5, 0.5, type_name="SUPPLYDEPOT", progress=0.5)
    ready_depot = _u(6, 1.5, 0.5, type_name="SUPPLYDEPOT", progress=1.0)
    outside = _u(7, 3.5, 3.5, type_name="SUPPLYDEPOT", progress=1.0)
    ctx = _ctx(gs=_gs([depot, ready_depot, outside]), layer=layer)
    assert eval_when({"op": "has_building", "type": "terran/supplydepot"}, ctx) is True
    assert eval_when({"op": "has_building", "type": "terran/supplydepot", "region": None, "ready": True}, ctx) is True
    assert eval_when({"op": "has_building", "type": "terran/supplydepot", "region": "main_base"}, ctx) is True
    assert eval_when({"op": "has_building", "type": "terran/supplydepot", "region": "field"}, ctx) is True
    assert eval_when({"op": "has_building", "type": "terran/barracks"}, ctx) is False
    only_partial = _ctx(gs=_gs([depot]))
    assert eval_when({"op": "has_building", "type": "terran/supplydepot", "region": None, "ready": True}, only_partial) is False


def test_has_building_normalizes_variants():
    """形态变体归一（T1/T3）：has_building 走同一条 unit_is_type 路径，架起态实体按 stable id 匹配。
    借 SIEGETANK 变体验证通路（建筑一般无变体，但逻辑一致）。"""
    sieged = _u(1, 0, 0, type_name="SIEGETANKSIEGED", progress=1.0)
    ctx = _ctx(gs=_gs([sieged]))
    assert eval_when({"op": "has_building", "type": "terran/siegetank"}, ctx) is True
    assert eval_when({"op": "has_building", "type": "terran/siegetank", "region": None, "ready": True}, ctx) is True
    # 未登记的类型名（burnysc2 名/拼错）→ False，不静默匹配
    assert eval_when({"op": "has_building", "type": "SIEGETANKSIEGED"}, ctx) is False


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



# ---- T2 求值语义：短路 / None 安全 / 诊断 / {ref} ----


def test_and_or_short_circuit():
    """D9：and/or 短路 —— 右支若会抛（未实现谓词）也不被求值。"""
    boom = {"op": "has_ready_base", "group": "main"}  # 未实现 → 求值即抛
    assert eval_when({"op": "and", "args": [{"const": False}, boom]}, _ctx()) is False
    assert eval_when({"op": "or", "args": [{"const": True}, boom]}, _ctx()) is True
    with pytest.raises(ValueError, match="未实现"):
        eval_when({"op": "and", "args": [{"const": True}, boom]}, _ctx())


def test_comparison_none_safe_and_recorded():
    """H6：空组 group_center=None 参与比较不崩（返回 False），但必须留痕（不静默）。"""
    diags = {}
    ctx = _ctx(alloc=FakeAllocator({"G1": {}}), bindings={"main": "G1"},
               gs=_gs([]), diagnostics=diags, step_id="s1")
    node = {"op": ">", "args": [{"op": "group_hp_ratio", "group": "main"}, None]}
    assert eval_when(node, ctx) is False
    assert any(kind == "compare_none" for _step, kind, _detail in diags)
    assert list(diags.values()) == [1]
    eval_when(node, ctx)  # 同一处再触发只累计次数
    assert list(diags.values()) == [2]


def test_eq_ne_keep_none_semantics():
    """==/!= 不做 None→False 降级（否则"不等"也不成立）：None == None 为 True。"""
    assert eval_when({"op": "==", "args": [None, None]}, _ctx()) is True
    assert eval_when({"op": "!=", "args": [None, 1]}, _ctx()) is True


def test_ref_expands_definitions():
    """T2b：{ref: name} 纯替换展开（编译期已保证存在且无环）。"""
    defs = {"two": {"const": 2}}
    ctx = _ctx(definitions=defs)
    assert eval_when({"op": ">=", "args": [{"ref": "two"}, 2]}, ctx) is True


def test_unknown_ref_returns_none_and_records():
    diags = {}
    ctx = _ctx(definitions={}, diagnostics=diags, step_id="s9")
    assert eval_when({"ref": "nope"}, ctx) is None
    assert any(kind == "unknown_ref" for _s, kind, _d in diags)
