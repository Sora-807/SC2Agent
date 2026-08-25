"""tactical_map 空间原语：center/distance/units_within/nearest。"""
from game import Owner, Point2, Unit
from tactical_map import center_of_units, distance, nearest, units_within
from tests.factories import make_unit


def _u(tag, x, y, hp=45.0, owner=Owner.SELF, type_name="MARINE"):
    return make_unit(tag, type_name, owner, x, y, hp=hp, hp_max=45.0)


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
