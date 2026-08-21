"""map_plan 覆盖层投影（B14）—— 纯函数语义钉死。

对应 web/tests/map-draft.test.ts 的前端草稿模型：两侧行为必须同构
（add 同名拒绝 / rename 撞名拒绝 / del 不存在拒绝 / 重叠拒绝）。
"""
import pytest

from game import Point2
from tactical_map.base import BaseTemplate, SpawnLayout, load_ladder_map
from tactical_map.placement import BuildSlot, PosMark
from view.map_plan import (
    MapHunkLike,
    apply_map_overrides,
    changed_names,
    load_map_overrides,
    merge_map_state,
    save_map_overrides,
)


def _h(hid: str, hkind: str, **payload) -> MapHunkLike:
    # 参数叫 hkind：add_slot 的 payload 里也有个 kind 字段，不能撞名
    return MapHunkLike(id=hid, kind=hkind, payload=payload)


def _tmpl() -> BaseTemplate:
    """最小模板：一个 spawn、两个槽位（2×2 与 3×3）、一个点位。"""
    s2 = BuildSlot(name="depot1", pos=Point2(40.5, 32.5), tl=BuildSlot.tl_from_pos(Point2(40.5, 32.5), 2), size=2, kind="supply")
    s3 = BuildSlot(name="rax1", pos=Point2(55.5, 33.5), tl=BuildSlot.tl_from_pos(Point2(55.5, 33.5), 3), size=3, kind="production")
    mark = PosMark(name="rally", pos=Point2(48.5, 30.5))
    return BaseTemplate(
        map_name="T", region_name="main_base",
        spawns={"bl": SpawnLayout(origin=Point2(0, 0), anchor=Point2(0, 0),
                                  build_slots=(s2, s3), pos_marks=(mark,))},
    )


# ---------------- merge_map_state ----------------

def test_merge_base_only():
    st = merge_map_state(_tmpl(), {})
    assert set(st["slots"]) == {"depot1", "rax1"}
    assert set(st["marks"]) == {"rally"}
    assert st["slots"]["rax1"]["size"] == 3


def test_merge_removed_then_added():
    st = merge_map_state(_tmpl(), {
        "build_slots_removed": ["depot1"],
        "build_slots": {"new1": {"pos": [10.5, 20.5], "size": 2, "kind": "supply"}},
        "pos_marks_removed": ["rally"],
        "pos_marks": {"m1": {"pos": [1.5, 2.5], "description_zh": "x"}},
    })
    assert "depot1" not in st["slots"] and "new1" in st["slots"]
    assert "rally" not in st["marks"] and st["marks"]["m1"]["description_zh"] == "x"


# ---------------- apply_map_overrides ----------------

def test_add_mark_projects_into_overrides():
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "add_mark", name="m1", pos=[1.5, 2.5])])
    assert errs == []
    assert out["pos_marks"]["m1"]["pos"] == [1.5, 2.5]


def test_add_mark_duplicate_rejected():
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "add_mark", name="rally", pos=[1.5, 2.5])])
    assert errs and "已存在" in errs[0]["text_zh"]
    assert errs[0]["hunk_id"] == "h1"
    assert out == {}


def test_rename_collision_rejected_and_valid_rename_ok():
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "rename_mark", **{"from": "rally", "to": "m2"}),
        _h("h2", "rename_mark", **{"from": "m2", "to": "m2"}),
    ])
    assert errs == []
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "rename_mark", **{"from": "rally", "to": "rally"})])
    assert errs == []  # 改成自己 = 无害
    # 撞已有其它名 → 拒
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "rename_mark", **{"from": "rally", "to": "depot1"})])
    assert errs == []  # 点位与槽位名字空间不同，允许同名
    out, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_mark", name="x", pos=[0.5, 0.5]),
        _h("h2", "rename_mark", **{"from": "x", "to": "rally"}),
    ])
    assert errs and "已存在" in errs[0]["text_zh"]


def test_add_slot_overlap_rejected():
    # depot1 是 2×2 @ (40.5,32.5)，footprint 40..41 × 32..33；放一个相邻但不重叠的
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_slot", name="s1", pos=[43.5, 32.5], size=2, kind="supply"),
    ])
    assert errs == []
    # 压到 depot1 上 → 拒
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_slot", name="s2", pos=[41.5, 33.5], size=2, kind="supply"),
    ])
    assert errs and "重叠" in errs[0]["text_zh"]
    # 偶数尺寸半格：同一格心连放两个 2×2 必然重叠
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_slot", name="a", pos=[20.5, 20.5], size=2, kind="supply"),
        _h("h2", "add_slot", name="b", pos=[20.5, 20.5], size=2, kind="supply"),
    ])
    assert errs and errs[0]["hunk_id"] == "h2"


def test_add_slot_bad_size_kind_rejected():
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_slot", name="s1", pos=[20.5, 20.5], size=4, kind="supply"),
    ])
    assert errs and "size" in errs[0]["text_zh"]
    _, errs = apply_map_overrides({}, _tmpl(), [
        _h("h1", "add_slot", name="s1", pos=[20.5, 20.5], size=2, kind="turret"),
    ])
    assert errs and "kind" in errs[0]["text_zh"]


def test_del_missing_rejected():
    _, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "del_mark", name="ghost")])
    assert errs and "不存在" in errs[0]["text_zh"]


def test_del_projects_to_removed_list():
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "del_slot", name="depot1")])
    assert errs == []
    assert out["build_slots_removed"] == ["depot1"]


def test_move_mark_projects_override():
    out, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "move_mark", name="rally", pos=[9.5, 9.5])])
    assert errs == []
    assert out["pos_marks"]["rally"]["pos"] == [9.5, 9.5]


def test_unknown_kind_rejected():
    _, errs = apply_map_overrides({}, _tmpl(), [_h("h1", "paint_region", name="x")])
    assert errs and "不认识" in errs[0]["text_zh"]


# ---------------- 覆盖层往返 + world_fixed ----------------

def test_override_roundtrip_and_world_fixed(tmp_path):
    """覆盖层往返 + world_fixed：覆盖条目存最终世界坐标，实例化不再平移。"""
    from tactical_map.base import load_base_template, instantiate_spawn
    base_yaml = """\
map_name: T
region_name: main_base
spawns:
  bl:
    origin: [10.0, 10.0]
    anchor: [10.0, 10.0]
    build_slots:
      depot1: {pos: [40.5, 32.5], size: 2, kind: supply}
    pos_marks:
      rally: {pos: [48.5, 30.5]}
"""
    (tmp_path / "base_layout.yaml").write_text(base_yaml, encoding="utf-8")
    ov_path = tmp_path / "base_layout.overrides.yaml"
    save_map_overrides(
        {"build_slots": {"new1": {"pos": [7.5, 8.5], "size": 2, "kind": "supply"}},
         "pos_marks": {"m1": {"pos": [11.5, 12.5], "description_zh": "z"}}},
        ov_path,
    )
    assert load_map_overrides(ov_path)["pos_marks"]["m1"]["pos"] == [11.5, 12.5]

    tpl = load_base_template(tmp_path / "base_layout.yaml")
    layout = tpl.spawns["bl"]
    assert layout.world_fixed == {"new1", "m1"}

    # cc 偏移 (3, -2)：base 条目跟着平移，override 条目**不动**（它们已是最终世界坐标）
    layer = instantiate_spawn(tpl, layout, Point2(13.0, 8.0), map_size=(176, 160))
    assert layer.build_slots["depot1"].pos == Point2(43.5, 30.5)      # 40.5+3, 32.5-2
    assert layer.build_slots["new1"].pos == Point2(7.5, 8.5)          # 不平移
    assert layer.pos_marks["rally"].pos == Point2(51.5, 28.5)         # 平移
    assert layer.pos_marks["m1"].pos == Point2(11.5, 12.5)            # 不平移


def test_changed_names_summary():
    ov = {"pos_marks": {"m1": {"pos": [1.5, 2.5]}}, "pos_marks_removed": ["rally"]}
    c = changed_names(_tmpl(), ov)
    assert set(c["changed_marks"]) == {"rally", "m1"}
    assert c["changed_slots"] == []