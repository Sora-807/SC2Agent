"""tactical_map：空间查询原语 + 点位登记 + MapLayout（只依赖 game）。"""
from tactical_map.map_layout import (
    BuildSlot,
    MapLayout,
    PosMark,
    RegionMark,
    load_map_layout,
)
from tactical_map.spatial import (
    PointRegistry,
    center_of_units,
    distance,
    nearest,
    units_within,
)

__all__ = [
    "BuildSlot",
    "MapLayout",
    "PointRegistry",
    "PosMark",
    "RegionMark",
    "center_of_units",
    "distance",
    "load_map_layout",
    "nearest",
    "units_within",
]
