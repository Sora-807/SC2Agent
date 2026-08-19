"""tactical_map 区域模型：加载 + 查询 + 校验（ADR-0029 D2）。"""
import pytest

from game import GridPos, Point2
from tactical_map import load_region_layer, validate_layer

LAYER_YAML = """
map_name: test_map
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
  main_base: {anchor: [1, 1], display_name_zh: 主矿}
  field: {anchor: [3, 3], display_name_zh: 野外}
regions:
  main_ramp: {parent: main_base, anchor: [1, 1], display_name_zh: 主矿斜坡, build_slots: [s1]}
pos_marks:
  rally: {pos: [2.5, 2.5], description_zh: 集结点}
build_slots:
  s1: {tl: [1, 1], size: 2}
"""


def _layer():
    return load_region_layer(LAYER_YAML)


def test_load_and_region_at():
    layer = _layer()
    # leaf 内 → (big, leaf)；大区默认域 → (big, None)；越界 → (None, None)
    assert layer.region_at(Point2(1.5, 1.5)) == ("main_base", "main_ramp")
    assert layer.region_at(Point2(0.5, 0.5)) == ("main_base", None)
    assert layer.region_at(Point2(3.5, 3.5)) == ("field", None)
    assert layer.region_at(Point2(10, 10)) == (None, None)


def test_anchor_resolution():
    layer = _layer()
    assert layer.anchor("main_base") == Point2(1, 1)   # 大区锚点
    assert layer.anchor("main_ramp") == Point2(1, 1)   # leaf 锚点
    assert layer.anchor("rally") == Point2(2.5, 2.5)   # 点位标记
    assert layer.anchor("missing") is None


def test_hierarchy_children():
    layer = _layer()
    assert layer.big_regions["main_base"].children == frozenset({"main_ramp"})
    assert layer.big_regions["field"].children == frozenset()


def test_region_build_slots_reference():
    layer = _layer()
    assert layer.regions["main_ramp"].build_slots == ("s1",)
    assert layer.build_slots["s1"].br == GridPos(2, 2)


def test_leafless_layer():
    yaml_str = """
map_name: no_leaf
size: [2, 2]
big_palette: {1: main_base}
big_grid:
  - "1 1"
  - "1 1"
big_regions:
  main_base: {anchor: [0, 0]}
"""
    layer = load_region_layer(yaml_str)
    assert layer.leaf_grid is None
    assert layer.region_at(Point2(0.5, 0.5)) == ("main_base", None)


def test_validate_reports_uncovered_big_cell():
    bad = LAYER_YAML.replace('"1 1 1 2"', '"1 9 1 2"')
    with pytest.raises(ValueError, match="big_palette"):
        load_region_layer(bad)


def test_validate_reports_leaf_outside_parent():
    # main_ramp 多画一格到 field（(3,2)，x=3 列），但 parent 仍是 main_base
    bad = LAYER_YAML.replace('  - "0 0 1 0"', '  - "0 0 1 1"')
    with pytest.raises(ValueError, match="越出其 parent"):
        load_region_layer(bad)


def test_validate_reports_disconnected_leaf():
    # 对角两格（(1,1) 与 (2,2)，4-邻接不相连）→ 不连通
    bad = LAYER_YAML.replace('  - "0 1 1 0"', '  - "0 1 0 0"')
    with pytest.raises(ValueError, match="不连通"):
        load_region_layer(bad)


def test_validate_reports_anchor_outside_region():
    bad = LAYER_YAML.replace(
        "main_ramp: {parent: main_base, anchor: [1, 1], display_name_zh: 主矿斜坡, build_slots: [s1]}",
        "main_ramp: {parent: main_base, anchor: [0, 0], display_name_zh: 主矿斜坡}")
    with pytest.raises(ValueError, match="anchor"):
        load_region_layer(bad)


def test_validate_reports_duplicate_name():
    # pos_mark 与 build_slot 同名 → 名字全局唯一校验
    bad = LAYER_YAML.replace("rally: {pos: [2.5, 2.5], description_zh: 集结点}", "s1: {pos: [2.5, 2.5]}")
    with pytest.raises(ValueError, match="重名"):
        load_region_layer(bad)


def test_validate_reports_palette_mismatch():
    bad = LAYER_YAML.replace("leaf_palette: {1: main_ramp}", "leaf_palette: {1: main_ramp, 2: ghost}")
    with pytest.raises(ValueError, match="不一致"):
        load_region_layer(bad)


def test_validate_layer_returns_errors_not_raises():
    layer = _layer()
    assert validate_layer(layer) == []

