"""footprint 换算公式的 fixture 测试（真机观测值锁定，ADR-0027 §3）。

真机观测来源：
- run_ops_check / run_production_check：2×2 补给站命令点 (131.5,107.5) → SC2 报告 (132,108)；
  兵营命令点 (44.5,28.5) → 报告 (44.5,28.5)（dist=0.00）
- 手动 trace：补给站报告 (45,46)、兵营报告 (44.5,43.5)
- driver_spike：CC 报告 (48.5,28.5)，5×5
"""
from game import GridPos, Point2
from tactical_map.placement import BuildSlot


def test_build_point_from_tl_even_size():
    """2×2：命令点 = 格角（TL + 0.5），不是 tl+size/2（那会错 0.5 格）。"""
    s = BuildSlot(name="depot1", tl=GridPos(131, 107), size=2)
    assert s.build_point == Point2(131.5, 107.5)  # 真机扫描值
    assert s.br == GridPos(132, 108)


def test_build_point_from_tl_odd_size():
    """3×3/5×5：命令点 = 格心（TL + size/2）。"""
    assert BuildSlot("b", GridPos(43, 27), 3).build_point == Point2(44.5, 28.5)  # 真机
    assert BuildSlot("c", GridPos(46, 26), 5).build_point == Point2(48.5, 28.5)  # driver_spike CC


def test_tl_from_pos_all_sizes():
    """命令点 → footprint min 格点：TL = ceil(P - size/2)。"""
    assert BuildSlot.tl_from_pos(Point2(131.5, 107.5), 2) == GridPos(131, 107)
    assert BuildSlot.tl_from_pos(Point2(44.5, 28.5), 3) == GridPos(43, 27)
    assert BuildSlot.tl_from_pos(Point2(48.5, 28.5), 5) == GridPos(46, 26)


def test_tl_from_reported_all_sizes():
    """SC2 报告位置 → footprint min 格点：TL = floor(R - size/2)（真机观测）。"""
    assert BuildSlot.tl_from_reported(Point2(132.0, 108.0), 2) == GridPos(131, 107)
    assert BuildSlot.tl_from_reported(Point2(45.0, 46.0), 2) == GridPos(44, 45)  # 手动 trace
    assert BuildSlot.tl_from_reported(Point2(44.5, 28.5), 3) == GridPos(43, 27)


def test_reported_position_all_sizes():
    """报告位置 = footprint 矩形中心 = TL + size/2（两种尺寸统一）。"""
    assert BuildSlot("d", GridPos(131, 107), 2).reported_position == Point2(132.0, 108.0)
    assert BuildSlot("b", GridPos(43, 27), 3).reported_position == Point2(44.5, 28.5)


def test_roundtrip_tl_pos():
    """TL → 命令点 → TL 往返一致（2/3/5）。"""
    for tl, size in ((GridPos(131, 107), 2), (GridPos(43, 27), 3), (GridPos(46, 26), 5)):
        s = BuildSlot(name="x", tl=tl, size=size)
        assert BuildSlot.tl_from_pos(s.build_point, size) == tl


def test_reported_roundtrip():
    """报告位置 → TL → 报告位置 往返一致。"""
    for r, size in ((Point2(132.0, 108.0), 2), (Point2(44.5, 28.5), 3)):
        tl = BuildSlot.tl_from_reported(r, size)
        assert BuildSlot(name="x", tl=tl, size=size).reported_position == r
