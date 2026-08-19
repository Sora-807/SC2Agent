"""tactical_map placement：BuildSlot（TL+BR+size）+ PosMark。"""
from game import GridPos, Point2
from tactical_map import BuildSlot, PosMark


def test_build_slot_tl_br_size2():
    s = BuildSlot("s1", GridPos(45, 46), 2)
    assert s.tl == GridPos(45, 46)
    assert s.br == GridPos(46, 47)  # br = tl + size - 1
    assert s.contains(GridPos(45, 46)) and s.contains(GridPos(46, 47))
    assert not s.contains(GridPos(47, 46))


def test_build_slot_tl_br_size3():
    s = BuildSlot("b1", GridPos(44, 43), 3)
    assert s.br == GridPos(46, 45)


def test_pos_mark():
    pm = PosMark("rally", Point2(50, 50), "集结点")
    assert pm.pos == Point2(50, 50)
    assert pm.description_zh == "集结点"
