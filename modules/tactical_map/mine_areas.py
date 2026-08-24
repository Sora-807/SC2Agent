"""tactical_map.mine_areas：矿区划分（PLAN-V2 D4 —— 每张图一份基础数据）。

矿区不在 map-plan（规划是布局方案，矿区是地图事实）、不运行时推导 ——
observe v2（批 4）按这张表把世界分区渲染。坐标草案待真机校准，校准后只改
`data/ladder_map/mine_areas.yaml`。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True, slots=True)
class MineArea:
    """一个矿区：名字 + 归属方 + 世界坐标闭区间（左下 + 右上）。"""

    name: str
    side: str                     # bl | tr | neutral
    bbox: tuple[float, float, float, float]

    def contains(self, x: float, y: float) -> bool:
        x1, y1, x2, y2 = self.bbox
        return x1 <= x <= x2 and y1 <= y <= y2


def load_mine_areas(map_dir: str = "ladder_map") -> list[MineArea]:
    """矿区表（声明序 = 展示序）。没有该图的数据 = 空表（调用方如实降级）。

    `map_dir` 是数据目录键（data/ 下的目录名，如 ladder_map —— 同 base.py 的
    load_ladder_map 约定），不是显示用地图名。
    """
    p = DATA_DIR / map_dir / "mine_areas.yaml"
    if not p.is_file():
        return []
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: list[MineArea] = []
    for a in d.get("areas") or []:
        bbox = tuple(float(v) for v in a["bbox"])
        out.append(MineArea(name=str(a["name"]), side=str(a.get("side") or "neutral"),
                            bbox=bbox))  # type: ignore[arg-type]
    return out
