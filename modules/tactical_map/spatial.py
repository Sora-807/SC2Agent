"""tactical_map.spatial：空间查询原语（只依赖 game；ADR-0002：game 不做空间查询）。

纯函数（无 seq 缓存，V1 每次重算；M3 seq 缓存后补）。
flow 的空间谓词（arrived/enemy_count_near/group_center/...）组合这些原语。
"""
from __future__ import annotations

from game import Point2, Unit


def center_of_units(units: list[Unit]) -> Point2 | None:
    """单位质心；空则 None（空 group 中心谓词由调用方判 false）。"""
    if not units:
        return None
    n = len(units)
    return Point2(sum(u.position.x for u in units) / n, sum(u.position.y for u in units) / n)


def distance(a: Point2, b: Point2) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def units_within(target: Point2, units: list[Unit], r: float) -> list[Unit]:
    return [u for u in units if distance(target, u.position) <= r]


def nearest_index(points: list[tuple[float, float]], target: tuple[float, float]) -> int:
    """按平方距离找最近点的下标（「归属最近基地」这类坐标元组查询；
    Unit 域的最近查询走 units_within/自排 key，别混用）。"""
    return min(range(len(points)),
               key=lambda i: (points[i][0] - target[0]) ** 2 + (points[i][1] - target[1]) ** 2)

