"""tactical_map MapLayout：地图标记布局（离线定义，运行时加载）。

BuildSlot（建造位，矩形：origin + size）+ PosMark（非建造点位）+ RegionMark（区域，含有序 BuildSlot 名）+ MapLayout（全图）。
flow 的 region_center(name) 谓词查 MapLayout；placement resolver（未来）查 MapLayout + GameState → 具体 Point2。
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from game import GridPos, Point2


@dataclass(frozen=True, slots=True)
class BuildSlot:
    """一个可建造位（矩形格点区域）。左下角 origin + size → 矩形 origin~origin+size。"""

    name: str  # "main_2x2_1"
    origin: GridPos  # 左下角格点
    size: int  # 2（补给站）/ 3（兵营/工厂/星港）/ 5（基地）

    @property
    def center(self) -> Point2:
        """矩形中心世界坐标（建筑放置点）。"""
        return Point2(self.origin.x + self.size / 2.0, self.origin.y + self.size / 2.0)


@dataclass(frozen=True, slots=True)
class PosMark:
    """非建造的点位标记（坦克驻扎、集结点等）。"""

    name: str  # "tank_siege_spot"
    pos: Point2
    description_zh: str = ""


@dataclass(frozen=True, slots=True)
class RegionMark:
    """区域标记（含有序 BuildSlot 列表 + 中心）。"""

    name: str  # "main_base" / "natural" / "ramp"
    center: Point2
    build_slots: tuple[str, ...]  # 有序 BuildSlot 名（建造顺序）
    description_zh: str = ""


@dataclass
class MapLayout:
    """一张地图的完整标记布局。"""

    map_name: str
    build_slots: dict[str, BuildSlot]
    pos_marks: dict[str, PosMark]
    region_marks: dict[str, RegionMark]

    def region_center(self, name: str) -> Point2 | None:
        """区域中心（兼容 PointRegistry 接口；flow 的 region_center 谓词用）。"""
        r = self.region_marks.get(name)
        if r is not None:
            return r.center
        pm = self.pos_marks.get(name)
        return pm.pos if pm is not None else None

    def build_slot(self, name: str) -> BuildSlot | None:
        return self.build_slots.get(name)

    def region_build_slots(self, region_name: str) -> list[BuildSlot]:
        """区域内的 BuildSlot 列表（按声明顺序）。"""
        r = self.region_marks.get(region_name)
        if r is None:
            return []
        return [self.build_slots[n] for n in r.build_slots if n in self.build_slots]

    def pos_mark(self, name: str) -> PosMark | None:
        return self.pos_marks.get(name)


def load_map_layout(yaml_str: str) -> MapLayout:
    """从 YAML 加载 MapLayout。"""
    d = yaml.safe_load(yaml_str)
    build_slots: dict[str, BuildSlot] = {}
    for name, data in (d.get("build_slots") or {}).items():
        build_slots[name] = BuildSlot(
            name=name,
            origin=GridPos(data["origin"][0], data["origin"][1]),
            size=data["size"],
        )
    pos_marks: dict[str, PosMark] = {}
    for name, data in (d.get("pos_marks") or {}).items():
        pos_marks[name] = PosMark(
            name=name,
            pos=Point2(data["pos"][0], data["pos"][1]),
            description_zh=data.get("description_zh", ""),
        )
    region_marks: dict[str, RegionMark] = {}
    for name, data in (d.get("region_marks") or {}).items():
        region_marks[name] = RegionMark(
            name=name,
            center=Point2(data["center"][0], data["center"][1]),
            build_slots=tuple(data.get("build_slots") or []),
            description_zh=data.get("description_zh", ""),
        )
    return MapLayout(
        map_name=d.get("map_name", "unknown"),
        build_slots=build_slots,
        pos_marks=pos_marks,
        region_marks=region_marks,
    )
