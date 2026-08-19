"""目标解析：map 名 → 坐标（ADR-0029 D1）。"""
from game import Point2
from tactical_map import load_region_layer, resolve_action_params, resolve_target

LAYER_YAML = """
map_name: res_map
size: [4, 4]
big_palette: {1: main_base, 2: field}
big_grid:
  - "1 1 1 2"
  - "1 1 1 2"
  - "1 1 1 2"
  - "2 2 2 2"
leaf_palette: {1: main_ramp}
leaf_grid:
  - "0 0 0 0"
  - "0 1 1 0"
  - "0 0 1 0"
  - "0 0 0 0"
big_regions:
  main_base: {anchor: [1, 1]}
  field: {anchor: [3, 3]}
regions:
  main_ramp: {parent: main_base, anchor: [1, 1]}
pos_marks:
  rally: {pos: [2.5, 2.5]}
"""


def _layer():
    return load_region_layer(LAYER_YAML)


def test_resolve_target_literals_and_names():
    layer = _layer()
    assert resolve_target(Point2(1, 2), layer) == Point2(1, 2)
    assert resolve_target([3, 4], layer) == Point2(3, 4)
    assert resolve_target((5.5, 6.5), layer) == Point2(5.5, 6.5)
    assert resolve_target("main_base", layer) == Point2(1, 1)
    assert resolve_target("main_ramp", layer) == Point2(1, 1)
    assert resolve_target("rally", layer) == Point2(2.5, 2.5)
    assert resolve_target("nope", layer) is None
    assert resolve_target(None, layer) is None


def test_resolve_action_params_point():
    layer = _layer()
    out = resolve_action_params("move_to", {"position": "main_base"}, layer)
    assert out == {"position": [1.0, 1.0]}


def test_resolve_action_params_build_type_untouched():
    # stable_id 型参数（type）不做 map 名解析——只有 point/points 走 resolver
    layer = _layer()
    out = resolve_action_params("build", {"type": "terran/barracks", "position": "main_ramp"}, layer)
    assert out["type"] == "terran/barracks"
    assert out["position"] == [1.0, 1.0]


def test_resolve_action_params_points_list():
    layer = _layer()
    out = resolve_action_params("patrol", {"positions": ["main_base", [3, 3]]}, layer)
    assert out == {"positions": [[1.0, 1.0], [3.0, 3.0]]}


def test_unresolvable_name_passthrough():
    # 未知名原样保留 → driver 应用时静默失败（D6/V1 降级路径；编译期校验后补）
    layer = _layer()
    out = resolve_action_params("move_to", {"position": "unknown_region"}, layer)
    assert out == {"position": "unknown_region"}


def test_no_layer_literal_still_resolves():
    out = resolve_action_params("move_to", {"position": [7, 8]}, None)
    assert out == {"position": [7.0, 8.0]}
