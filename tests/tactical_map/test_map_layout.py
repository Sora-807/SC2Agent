"""tactical_map MapLayout 测试：BuildSlot center + RegionMark + MapLayout 查询 + YAML 加载。"""
from game import GridPos, Point2
from tactical_map import BuildSlot, MapLayout, PosMark, RegionMark, load_map_layout


def _layout() -> MapLayout:
    return MapLayout(
        map_name="test",
        build_slots={
            "s1": BuildSlot("s1", GridPos(45, 46), 2),
            "s2": BuildSlot("s2", GridPos(47, 46), 2),
            "b1": BuildSlot("b1", GridPos(44, 43), 3),
        },
        pos_marks={
            "rally": PosMark("rally", Point2(50, 50)),
        },
        region_marks={
            "main_base": RegionMark("main_base", Point2(48, 28), ("s1", "s2", "b1")),
        },
    )


def test_build_slot_center_size2():
    s = BuildSlot("s1", GridPos(45, 46), 2)
    assert s.center == Point2(46.0, 47.0)  # 45 + 2/2 = 46, 46 + 2/2 = 47


def test_build_slot_center_size3():
    s = BuildSlot("b1", GridPos(44, 43), 3)
    assert s.center == Point2(45.5, 44.5)  # 44 + 3/2 = 45.5, 43 + 3/2 = 44.5


def test_region_center():
    assert _layout().region_center("main_base") == Point2(48, 28)


def test_region_center_fallback_to_pos_mark():
    assert _layout().region_center("rally") == Point2(50, 50)


def test_region_build_slots_ordered():
    slots = _layout().region_build_slots("main_base")
    assert [s.name for s in slots] == ["s1", "s2", "b1"]


def test_region_build_slots_filtered_by_size():
    slots = _layout().region_build_slots("main_base")
    assert len([s for s in slots if s.size == 2]) == 2
    assert len([s for s in slots if s.size == 3]) == 1


def test_pos_mark():
    layout = _layout()
    assert layout.pos_mark("rally").pos == Point2(50, 50)
    assert layout.pos_mark("missing") is None


def test_missing_region():
    assert _layout().region_build_slots("nope") == []
    assert _layout().region_center("nope") is None


def test_load_map_layout():
    yaml_str = """
map_name: test_map
build_slots:
  s1: {origin: [45, 46], size: 2}
  b1: {origin: [44, 43], size: 3}
pos_marks:
  rally: {pos: [50, 50], description_zh: 集结点}
region_marks:
  main_base:
    center: [48, 28]
    build_slots: [s1, b1]
    description_zh: 主基地区域
"""
    layout = load_map_layout(yaml_str)
    assert layout.map_name == "test_map"
    assert layout.build_slot("s1").origin == GridPos(45, 46)
    assert layout.build_slot("s1").size == 2
    assert layout.build_slot("b1").size == 3
    assert layout.pos_mark("rally").pos == Point2(50, 50)
    assert layout.pos_mark("rally").description_zh == "集结点"
    assert layout.region_marks["main_base"].build_slots == ("s1", "b1")
    assert layout.region_marks["main_base"].description_zh == "主基地区域"
    assert layout.region_center("main_base") == Point2(48, 28)
    assert layout.region_build_slots("main_base")[0].name == "s1"
