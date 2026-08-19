"""tactical_map 模块：地图空间模型（只依赖 game；ADR-0029）。

- spatial：空间查询原语（distance/center/nearest/units_within）
- region：区域模型（大区全图分区 + leaf 稀疏小区，一层几何两层语义）+ 加载校验
- placement：BuildSlot（TL+BR+size，ADR-0027）+ PosMark 点位
- resolver：目标解析（map 名 → 坐标，ADR-0029 D1）
- pnggrid：调色板 PNG → 格点层（authoring 用）
"""
from tactical_map.base import (
    BaseTemplate,
    SpawnLayout,
    instantiate_spawn,
    load_base_template,
    load_ladder_map,
    spawn_layout_nearest,
)
from tactical_map.placement import BuildSlot, PosMark
from tactical_map.pnggrid import load_palette_png
from tactical_map.region import (
    BigRegion,
    Region,
    RegionLayer,
    load_region_layer,
    validate_layer,
)
from tactical_map.resolver import resolve_action_params, resolve_target
from tactical_map.spatial import center_of_units, distance, nearest, units_within

__all__ = [
    "BaseTemplate",
    "BigRegion",
    "BuildSlot",
    "PosMark",
    "Region",
    "RegionLayer",
    "SpawnLayout",
    "center_of_units",
    "distance",
    "instantiate_spawn",
    "load_base_template",
    "load_ladder_map",
    "load_palette_png",
    "load_region_layer",
    "nearest",
    "resolve_action_params",
    "resolve_target",
    "spawn_layout_nearest",
    "units_within",
    "validate_layer",
]
