"""tactical_map.region_view：格点区域 → Markdown 网格（I18 用户规格的回归锁）。

锁的是用户 2026-08-23 拍板的规格：一格一词 ≤3 字符；`·/✗/D1/R1/F1/R+/gas/CC/M`；
行头 Y 从高到低、列头 X 从左到右；多格建筑整个 footprint 同标签；超上限报错
而不是硬渲染；空白规划的"没槽位"要显形（不静默）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from game.catalog import load_all
from tactical_map.region_view import MAX_COLS, slot_label

CAT = load_all()


def test_slot_label_vocabulary():
    assert slot_label("depot1", "supply") == "D1"
    assert slot_label("depot16", "supply") == "D16"      # 3 字符上限刚好
    assert slot_label("rax3", "production") == "R3"
    assert slot_label("factory1", "production") == "F1"
    assert slot_label("starport2", "production") == "S2"
    assert slot_label("rax1_addon", "addon") == "R+"
    assert slot_label("factory1_addon", "addon") == "F+"
    assert slot_label("weird", "production") == "?"       # 认不出不猜


def _slots():
    return {
        "depot1": {"pos": [40.5, 32.5], "size": 2, "kind": "supply"},
        "rax1": {"pos": [55.5, 33.5], "size": 3, "kind": "production"},
        "rax1_addon": {"pos": [57.5, 32.5], "size": 2, "kind": "addon"},
    }


def test_render_region_grid_shape_and_labels():
    from tactical_map.region_view import render_region

    text = render_region((39, 31, 41, 34), _slots(), CAT, step=1, title="probe")
    lines = text.splitlines()
    header = next(ln for ln in lines if ln.startswith("| y\\x"))
    # 列头 X 从左到右；行头 Y 从高到低（俯视）
    assert [int(v) for v in header.split("|")[2:-1]] == [39, 40, 41]
    y_head = [ln.split("|")[1] for ln in lines if ln.startswith("| **")]
    assert y_head == [" **34** ", " **33** ", " **32** ", " **31** "]
    # 补给站 2×2：footprint 四格同标签 D1
    row32 = next(ln for ln in lines if ln.startswith("| **32**"))
    assert row32.count("D1") == 2
    row33 = next(ln for ln in lines if ln.startswith("| **33**"))
    assert row33.count("D1") == 2
    # 兵营 3×3 + 挂件 2×2 在框外（x55+）不该出现；词表脚注在
    assert "R1" not in text and "R+" not in text
    assert "词表" in text


def test_render_region_footprint_fills_every_cell():
    from tactical_map.region_view import render_region

    text = render_region((54, 32, 58, 36), _slots(), CAT)
    # rax1 3×3 → 9 格全是 R1（分三行断言，每行 3 个）
    for y in (34, 33, 32):
        row = next(ln for ln in text.splitlines() if ln.startswith(f"| **{y}**"))
        assert row.count("R1") == 3, f"y={y}"


def test_render_region_oversized_bbox_rejected_with_hint():
    from tactical_map.region_view import render_region

    with pytest.raises(ValueError, match="step"):
        render_region((0, 0, 40, 40), _slots(), CAT, step=1)


def test_render_region_empty_slots_say_so():
    from tactical_map.region_view import render_region

    text = render_region((39, 31, 41, 34), {}, CAT)
    assert "没有槽位" in text and "layout-bl" in text


def test_render_region_clamps_and_notes():
    from tactical_map.region_view import render_region

    text = render_region((-5, -5, 3, 3), {}, CAT)
    assert "钳到地图范围" in text
