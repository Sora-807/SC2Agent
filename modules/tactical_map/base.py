"""tactical_map.base：主基建造模板 —— 固定有序建造位（authoring 数据 + 运行时实例化）。

数据文件 modules/tactical_map/data/<map>/base_layout.yaml：按出生点分变体
（出生点随机，几何随旋转不同），每个变体是相对该出生点的固定有序建造位
（真机 can_place 扫描校准，见 run_slot_scan.py + docs/evidence/slot_scan.log）。
运行时把模板平移到我方实际 CC → 确定性 RegionLayer：建筑永远按声明顺序
落在固定位置（"固定位置顺序摆放"），不做随机摆放、不依赖换位重试
（生产运行时的换位重试降级为安全网）。
"""
from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

import yaml

from game import Grid, GridPos, Point2
from tactical_map.placement import BuildSlot, PosMark
from tactical_map.region import BigRegion, Region, RegionLayer

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True, slots=True)
class SpawnLayout:
    """一个出生点的主基布局（固定有序建造位，坐标 = 该出生点的世界坐标）。"""

    origin: Point2  # 出生点 CC 参考位（实例化时平移到实际 CC）
    anchor: Point2  # 区域锚点（移动目标点/查询中心）
    build_slots: tuple[BuildSlot, ...]  # 有序建造位（声明顺序 = 建造顺序）
    pos_marks: tuple[PosMark, ...] = ()  # 非建造点位（集结点/驻扎点等；F14 起 authoring）
    # 来自 overrides（map_plan 已接受）的名字：这些条目存的是**最终世界坐标**，
    # 实例化时**不再平移**（base 条目才随出生点平移）。
    world_fixed: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class BaseTemplate:
    """一张地图一份：按出生点分变体的主基建造模板。"""

    map_name: str
    region_name: str  # 语义区域名（"main_base"；跨图统一槽位）
    spawns: dict[str, SpawnLayout]  # 出生点名（如 "bl"/"tr"）→ 布局


def _slot_from_entry(name: str, entry: dict) -> BuildSlot:
    pos = Point2(float(entry["pos"][0]), float(entry["pos"][1]))
    size = int(entry["size"])
    return BuildSlot(
        name=name,
        pos=pos,
        tl=BuildSlot.tl_from_pos(pos, size),
        size=size,
        kind=str(entry.get("kind", "production")),
        alias_zh=str(entry.get("alias_zh", "")),
    )


def _mark_from_entry(name: str, entry: dict) -> PosMark:
    return PosMark(
        name=name,
        pos=Point2(float(entry["pos"][0]), float(entry["pos"][1])),
        description_zh=str(entry.get("description_zh", "")),
    )


def load_base_overrides(path: str | Path) -> dict:
    """机器生成的覆盖层（map_plan 提案接受后写回）。不存在 = 空覆盖。

    与手写 base_layout.yaml 分离的理由：手写文件带真机校准注释、人工维护，
    机器改写会吞注释；覆盖层是机器产物、无注释需求，safe_dump 往返安全。
    """
    p = Path(path)
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_base_template(path: str | Path) -> BaseTemplate:
    """从 YAML 加载主基建造模板（pos = 校准过的世界建造点，权威；tl 由 tl_from_pos 反推）。

    同时合并 <同名>.overrides.yaml（若存在）：机器生成的覆盖层**先删后增**。
    覆盖层坐标是**世界坐标**（用户在画布上"这里就是这里"，不随 spawn 变体镜像），
    且名字全局生效（del 一个名字 = 两个 spawn 里的同名条目都删）。
    """
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    # base_layout.yaml → base_layout.overrides.yaml（with_name+拼接会拼出 .yaml.overrides.yaml）
    overrides = load_base_overrides(str(Path(path).with_suffix(".overrides.yaml")))
    removed_slots = set(str(n) for n in (overrides.get("build_slots_removed") or []))
    added_slots = {str(n): e for n, e in (overrides.get("build_slots") or {}).items()}
    removed_marks = set(str(n) for n in (overrides.get("pos_marks_removed") or []))
    added_marks = {str(n): e for n, e in (overrides.get("pos_marks") or {}).items()}
    spawns: dict[str, SpawnLayout] = {}
    for name, data in (d.get("spawns") or {}).items():
        slots: dict[str, dict] = {sname: s for sname, s in (data.get("build_slots") or {}).items()}
        for rm in removed_slots:
            slots.pop(rm, None)
        for sname, entry in added_slots.items():
            slots[sname] = entry
        marks: dict[str, dict] = {mname: m for mname, m in (data.get("pos_marks") or {}).items()}
        for rm in removed_marks:
            marks.pop(rm, None)
        for mname, entry in added_marks.items():
            marks[mname] = entry
        fixed = frozenset(set(added_slots) | set(added_marks))
        spawns[name] = SpawnLayout(
            origin=Point2(float(data["origin"][0]), float(data["origin"][1])),
            anchor=Point2(float(data["anchor"][0]), float(data["anchor"][1])),
            build_slots=tuple(_slot_from_entry(sname, s) for sname, s in slots.items()),
            pos_marks=tuple(_mark_from_entry(mname, m) for mname, m in marks.items()),
            world_fixed=fixed,
        )
    return BaseTemplate(
        map_name=d.get("map_name", "unknown"),
        region_name=d.get("region_name", "main_base"),
        spawns=spawns,
    )


def load_map_plan(path: str | Path) -> BaseTemplate:
    """地图规划文件 → BaseTemplate；兼容 base_layout 原形态（spawns 双分支）。

    单出生点形态（view.map_plans 的规划文件）没有 spawns 节 —— 包一层再走
    同一条解析，校验/合并/会话装配共用解析逻辑（不另写一份字段解读）。
    """
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if d.get("spawns"):
        return load_base_template(path)
    side = str(d.get("spawn") or "bl")
    wrapped = {
        "map_name": d.get("map_name") or "unknown",
        "region_name": "main_base",
        "spawns": {side: {
            "origin": d.get("origin") or [0, 0],
            "anchor": d.get("anchor") or [0, 0],
            "build_slots": d.get("build_slots") or {},
            "pos_marks": d.get("pos_marks") or {},
        }},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as f:
        yaml.safe_dump(wrapped, f, allow_unicode=True)
        tmp = f.name
    try:
        return load_base_template(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


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


def pick_spawn_layout(template: BaseTemplate, cc: Point2) -> tuple[str, "SpawnLayout"]:
    """实际 CC 坐标 → **就近**的出生点分支（真机随机出生点检测，2026-08-24 事故修）。

    SC2 会把我们随机放在 bl 或 tr；构造期不知道实际位置（此前写死 bl 导致出生 tr 时
    工人被派去左下采矿）。分支选择 = 离实际 CC 最近的 layout.origin。
    """
    best_key, best_layout, best_d = None, None, None
    for key, layout in template.spawns.items():
        d = (layout.origin.x - cc.x) ** 2 + (layout.origin.y - cc.y) ** 2
        if best_d is None or d < best_d:
            best_key, best_layout, best_d = key, layout, d
    return best_key, best_layout


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
        s.name: (
            s if s.name in layout.world_fixed else BuildSlot(
                name=s.name,
                tl=GridPos(s.tl.x + int(round(dx)), s.tl.y + int(round(dy))),
                size=s.size,
                kind=s.kind,
                pos=Point2(s.pos.x + dx, s.pos.y + dy) if s.pos is not None else None,
                alias_zh=s.alias_zh,
            )
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
        pos_marks={
            m.name: (
                m if m.name in layout.world_fixed else PosMark(
                    name=m.name,
                    pos=Point2(m.pos.x + dx, m.pos.y + dy),
                    description_zh=m.description_zh,
                )
            )
            for m in layout.pos_marks
        },
        build_slots=slots,
    )
