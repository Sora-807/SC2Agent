"""tactical_map.base：主基建造模板 —— 固定有序建造位（authoring 数据 + 运行时实例化）。

数据文件 modules/tactical_map/data/<map>/base_layout.yaml：按出生点分变体
（出生点随机，几何随旋转不同），每个变体是相对该出生点的固定有序建造位
（真机 can_place 扫描校准，见 run_slot_scan.py + docs/slot_scan.log）。
运行时把模板平移到我方实际 CC → 确定性 RegionLayer：建筑永远按声明顺序
落在固定位置（"固定位置顺序摆放"），不做随机摆放、不依赖换位重试
（生产运行时的换位重试降级为安全网）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from game import Grid, GridPos, Point2
from tactical_map.placement import BuildSlot
from tactical_map.region import BigRegion, Region, RegionLayer

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True, slots=True)
class SpawnLayout:
    """一个出生点的主基布局（固定有序建造位，坐标 = 该出生点的世界坐标）。"""

    origin: Point2  # 出生点 CC 参考位（实例化时平移到实际 CC）
    anchor: Point2  # 区域锚点（移动目标点/查询中心）
    build_slots: tuple[BuildSlot, ...]  # 有序建造位（声明顺序 = 建造顺序）


@dataclass(frozen=True, slots=True)
class BaseTemplate:
    """一张地图一份：按出生点分变体的主基建造模板。"""

    map_name: str
    region_name: str  # 语义区域名（"main_base"；跨图统一槽位）
    spawns: dict[str, SpawnLayout]  # 出生点名（如 "bl"/"tr"）→ 布局


def load_base_template(path: str | Path) -> BaseTemplate:
    """从 YAML 加载主基建造模板（pos = 校准过的世界建造点，权威；tl 由 floor(pos) 近似）。"""
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    spawns: dict[str, SpawnLayout] = {}
    for name, data in (d.get("spawns") or {}).items():
        slots = tuple(
            BuildSlot(
                name=sname,
                pos=Point2(float(s["pos"][0]), float(s["pos"][1])),
                tl=BuildSlot.tl_from_pos(
                    Point2(float(s["pos"][0]), float(s["pos"][1])), int(s["size"])
                ),
                size=int(s["size"]),
                kind=s.get("kind", "production"),
            )
            for sname, s in (data.get("build_slots") or {}).items()
        )
        spawns[name] = SpawnLayout(
            origin=Point2(float(data["origin"][0]), float(data["origin"][1])),
            anchor=Point2(float(data["anchor"][0]), float(data["anchor"][1])),
            build_slots=slots,
        )
    return BaseTemplate(
        map_name=d.get("map_name", "unknown"),
        region_name=d.get("region_name", "main_base"),
        spawns=spawns,
    )


def load_ladder_map() -> BaseTemplate:
    """LadderMap（破晓黎明）主基模板（固定建造位数据，按出生点分变体）。"""
    return load_base_template(DATA_DIR / "ladder_map" / "base_layout.yaml")


def spawn_layout_nearest(template: BaseTemplate, cc: Point2) -> SpawnLayout | None:
    """按我方实际 CC 选最近的出生点变体（出生点随机）。"""
    if not template.spawns:
        return None
    return min(
        template.spawns.values(),
        key=lambda s: (s.origin.x - cc.x) ** 2 + (s.origin.y - cc.y) ** 2,
    )


def instantiate_spawn(
    template: BaseTemplate,
    layout: SpawnLayout,
    cc: Point2,
    map_size: tuple[int, int] = (176, 160),
) -> RegionLayer:
    """把出生点布局平移到实际 CC → 确定性 RegionLayer。

    大区 main_base 全覆盖 + leaf "home"（挂有序 build_slots）；
    生产运行时的 PlacementInRegion("home") 即按声明顺序固定摆放。
    """
    dx = cc.x - layout.origin.x
    dy = cc.y - layout.origin.y
    w, h = map_size
    anchor = Point2(layout.anchor.x + dx, layout.anchor.y + dy)
    slots = {
        s.name: BuildSlot(
            name=s.name,
            tl=GridPos(s.tl.x + int(round(dx)), s.tl.y + int(round(dy))),
            size=s.size,
            kind=s.kind,
            pos=Point2(s.pos.x + dx, s.pos.y + dy) if s.pos is not None else None,
        )
        for s in layout.build_slots
    }
    cc_cell = (int(cc.x), int(cc.y))
    return RegionLayer(
        map_name=template.map_name,
        size=map_size,
        big_grid=Grid(w, h, [[1] * w for _ in range(h)]),
        big_index={1: template.region_name},
        big_regions={template.region_name: BigRegion(stable_id=template.region_name, anchor=anchor)},
        leaf_grid=None,
        leaf_index={},
        regions={
            "home": Region(
                stable_id="home",
                parent=template.region_name,
                cells=frozenset({cc_cell}),
                anchor=anchor,
                build_slots=tuple(slots.keys()),
            )
        },
        pos_marks={},
        build_slots=slots,
    )
