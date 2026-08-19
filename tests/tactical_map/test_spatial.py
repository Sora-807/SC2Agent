"""tactical_map 空间原语 + PointRegistry。"""
from game import Owner, Point2, Unit
from tactical_map import PointRegistry, center_of_units, distance, nearest, units_within


def _u(tag, x, y, hp=45.0, owner=Owner.SELF, type_name="MARINE"):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=hp, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def test_center_of_units():
    assert center_of_units([_u(1, 0, 0), _u(2, 2, 4)]) == Point2(1.0, 2.0)
    assert center_of_units([]) is None


def test_distance():
    assert distance(Point2(0, 0), Point2(3, 4)) == 5.0


def test_units_within():
    us = [_u(1, 0, 0), _u(2, 1, 0), _u(3, 5, 5)]
    near = units_within(Point2(0, 0), us, 1.5)
    assert [u.tag for u in near] == [1, 2]


def test_nearest():
    us = [_u(1, 5, 5), _u(2, 1, 1), _u(3, 2, 2)]
    n = nearest(Point2(0, 0), us, k=2)
    assert [u.tag for u in n] == [2, 3]


def test_point_registry():
    r = PointRegistry()
    r.register_point("main_base", 50, 50)
    r.register_region("enemy_natural", (100, 100), 20)
    assert r.point("main_base") == Point2(50, 50)
    assert r.region_center("enemy_natural") == Point2(100, 100)
    assert r.region_center("main_base") == Point2(50, 50)  # 点名当区域中心
    assert r.point("missing") is None
