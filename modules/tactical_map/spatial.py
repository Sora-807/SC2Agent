"""tactical_map：空间查询原语 + 点位名↔坐标登记。

只依赖 game（leaf）。纯函数（无 seq 缓存，V1 每次重算；M3 seq 缓存后补）。
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


def nearest(target: Point2, units: list[Unit], k: int = 1) -> list[Unit]:
    return sorted(units, key=lambda u: distance(target, u.position))[:k]


class PointRegistry:
    """点位名↔坐标 / 区域名↔(中心,半径) 登记。V1 手工登记。"""

    def __init__(self) -> None:
        self._points: dict[str, Point2] = {}
        self._regions: dict[str, tuple[Point2, float]] = {}

    def register_point(self, name: str, x: float, y: float) -> None:
        self._points[name] = Point2(float(x), float(y))

    def register_region(self, name: str, center: tuple[float, float], radius: float) -> None:
        self._regions[name] = (Point2(float(center[0]), float(center[1])), float(radius))

    def point(self, name: str) -> Point2 | None:
        return self._points.get(name)

    def region_center(self, name: str) -> Point2 | None:
        r = self._regions.get(name)
        if r is not None:
            return r[0]
        return self._points.get(name)  # 点名也当区域中心用
