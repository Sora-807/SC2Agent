"""tactical_map.region：区域模型（ADR-0029 D2：一层几何，两层语义）。

几何只有一层 leaf 分区：每个格点至多属于一个 leaf；大区是 leaf 的分组（父指针）。
- 大区层 = 全图强制分区（每格恰属一个大区）
- leaf 小区稀疏（斜坡/矿区等战术要点），未覆盖格点属大区默认域
区域形状 = 格点集；anchor = 作者标注的语义中心（不用质心）。
加载即校验：无洞（4-连通）、leaf ⊆ parent、每层 cell 唯一、大区全覆盖、名字唯一。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from game import Grid, GridPos, Point2

from tactical_map.placement import BuildSlot, PosMark
from tactical_map.pnggrid import load_palette_png


@dataclass(frozen=True, slots=True)
class Region:
    """leaf 小区：一块 4-连通、无洞的格点集，隶属于某个大区。"""

    stable_id: str  # 语义槽名（跨图统一，如 "main_ramp"）
    parent: str  # 所属大区 stable_id
    cells: frozenset[tuple[int, int]]  # (x, y) 格点集
    anchor: Point2  # 作者标注的语义中心/移动目标点
    display_name_zh: str = ""
    aliases_zh: tuple[str, ...] = ()
    build_slots: tuple[str, ...] = ()  # 本区 BuildSlot 名（有序）


@dataclass(frozen=True, slots=True)
class BigRegion:
    """大区：全图分区的一块；leaf 的语义分组（children）。"""

    stable_id: str  # 语义槽名（跨图统一，如 "main_base"）
    anchor: Point2  # 作者标注的语义中心/移动目标点
    display_name_zh: str = ""
    aliases_zh: tuple[str, ...] = ()
    children: frozenset[str] = frozenset()  # 隶属的 leaf stable_id 集合


@dataclass(slots=True)
class RegionLayer:
    """一张地图的完整区域层：大区标签层 + leaf 标签层 + 点位/建筑位。"""

    map_name: str
    size: tuple[int, int]  # (width, height)
    big_grid: Grid  # 每格存大区 key（全图覆盖）
    big_index: dict[int, str]  # key -> 大区 stable_id
    big_regions: dict[str, BigRegion]
    leaf_grid: Grid | None = None  # 每格存 leaf key（0 = 无；稀疏）
    leaf_index: dict[int, str] = field(default_factory=dict)  # key -> leaf stable_id
    regions: dict[str, Region] = field(default_factory=dict)
    pos_marks: dict[str, PosMark] = field(default_factory=dict)
    build_slots: dict[str, BuildSlot] = field(default_factory=dict)

    # ---- 查询 ----

    def region_at(self, pos: Point2) -> tuple[str | None, str | None]:
        """单位所在区域：(大区 stable_id, leaf stable_id | None)。越界 → (None, None)。"""
        x, y = int(pos.x), int(pos.y)
        w, h = self.size
        if not (0 <= x < w and 0 <= y < h):
            return (None, None)
        big = self.big_index.get(self.big_grid.data[y][x])
        leaf: str | None = None
        if self.leaf_grid is not None:
            leaf = self.leaf_index.get(self.leaf_grid.data[y][x])
        return (big, leaf)

    def anchor(self, name: str) -> Point2 | None:
        """名字 → 语义中心：leaf 区域 / 大区 / 点位标记（名字全局唯一）。"""
        r = self.regions.get(name)
        if r is not None:
            return r.anchor
        b = self.big_regions.get(name)
        if b is not None:
            return b.anchor
        pm = self.pos_marks.get(name)
        return pm.pos if pm is not None else None

    def contains(self, name: str, pos: Point2) -> bool:
        """点位是否属于该区域（leaf 格点集 / 大区格点集）；未知名或越界 → False。

        谓词 enemy_visible_in / group_center_in_region / has_building(region=...) 用它。
        """
        x, y = int(pos.x), int(pos.y)
        w, h = self.size
        if not (0 <= x < w and 0 <= y < h):
            return False
        r = self.regions.get(name)
        if r is not None:
            return (x, y) in r.cells
        b = self.big_regions.get(name)
        if b is not None:
            return self.big_index.get(self.big_grid.data[y][x]) == name
        return False

    def cells_of_big(self, big_id: str) -> frozenset[tuple[int, int]]:
        """大区全部格点（从 big_grid 扫描；校验/规划用，非热路径）。"""
        w, h = self.size
        return frozenset(
            (x, y) for y in range(h) for x in range(w)
            if self.big_index.get(self.big_grid.data[y][x]) == big_id
        )


# ---------- 校验 ----------


def _connected(cells: frozenset[tuple[int, int]]) -> bool:
    """4-连通 flood fill：从任一格可达全部格 = 无洞。"""
    if not cells:
        return True
    start = next(iter(cells))
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n in cells and n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen) == len(cells)


def validate_layer(layer: RegionLayer) -> list[str]:
    """区域层校验（ADR-0029 D2 全部规则）。返回错误清单（空 = 合法）。"""
    errs: list[str] = []
    w, h = layer.size
    if layer.big_grid.width != w or layer.big_grid.height != h:
        errs.append(f"big_grid 尺寸 {layer.big_grid.width}x{layer.big_grid.height} != size {w}x{h}")
    if layer.leaf_grid is not None and (layer.leaf_grid.width != w or layer.leaf_grid.height != h):
        errs.append("leaf_grid 尺寸与 size 不符")

    # 名字全局唯一（leaf/big/pos_mark/build_slot）
    names: dict[str, str] = {}
    def _add(name: str, kind: str) -> None:
        if name in names:
            errs.append(f"重名 {name!r}（{kind} vs {names[name]}）")
        else:
            names[name] = kind
    for n in layer.big_regions:
        _add(n, "big_region")
    for n in layer.regions:
        _add(n, "region")
    for n in layer.pos_marks:
        _add(n, "pos_mark")
    for n in layer.build_slots:
        _add(n, "build_slot")

    # 大区：全覆盖 + 每格 value 在 index 中
    big_cells: dict[str, set[tuple[int, int]]] = {}
    for y in range(h):
        for x in range(w):
            v = layer.big_grid.data[y][x]
            bid = layer.big_index.get(v)
            if bid is None:
                errs.append(f"格点 ({x},{y}) 大区 key {v} 不在 big_palette")
                continue
            big_cells.setdefault(bid, set()).add((x, y))
    if set(layer.big_index.values()) != set(layer.big_regions):
        errs.append("big_palette 与 big_regions 条目不一致")

    # leaf：cell 唯一（结构保证）+ 非空 + 连通 + ⊆ parent + anchor 在内
    leaf_cells: dict[str, set[tuple[int, int]]] = {}
    if layer.leaf_grid is not None:
        for y in range(h):
            for x in range(w):
                v = layer.leaf_grid.data[y][x]
                if v == 0:
                    continue
                lid = layer.leaf_index.get(v)
                if lid is None:
                    errs.append(f"格点 ({x},{y}) leaf key {v} 不在 leaf_palette")
                    continue
                leaf_cells.setdefault(lid, set()).add((x, y))
        if set(layer.leaf_index.values()) != set(layer.regions):
            errs.append("leaf_palette 与 regions 条目不一致")
    elif layer.regions:
        errs.append("声明了 regions 但没有 leaf_grid")

    for lid, r in layer.regions.items():
        cells = leaf_cells.get(lid, set())
        if not cells:
            errs.append(f"region {lid!r} 无格点")
            continue
        if r.parent not in layer.big_regions:
            errs.append(f"region {lid!r} 的 parent {r.parent!r} 不存在")
        elif not cells <= big_cells.get(r.parent, set()):
            errs.append(f"region {lid!r} 越出其 parent {r.parent!r}")
        if not _connected(frozenset(cells)):
            errs.append(f"region {lid!r} 不连通/有洞")
        if (int(r.anchor.x), int(r.anchor.y)) not in cells:
            errs.append(f"region {lid!r} 的 anchor {r.anchor} 不在区域内")

    for bid, b in layer.big_regions.items():
        if (int(b.anchor.x), int(b.anchor.y)) not in big_cells.get(bid, set()):
            errs.append(f"big_region {bid!r} 的 anchor {b.anchor} 不在区域内")

    for lid, r in layer.regions.items():
        for sn in r.build_slots:
            if sn not in layer.build_slots:
                errs.append(f"region {lid!r} 引用不存在的 build_slot {sn!r}")

    for name, pm in layer.pos_marks.items():
        if not (0 <= int(pm.pos.x) < w and 0 <= int(pm.pos.y) < h):
            errs.append(f"pos_mark {name!r} 的 pos {pm.pos} 在图外")

    return errs


# ---------- 加载（YAML 元数据 + 行式格点或调色板 PNG）----------


def _point2(data) -> Point2:
    return Point2(float(data[0]), float(data[1]))


def _parse_grid_rows(rows, size: tuple[int, int], what: str) -> list[list[int]]:
    w, h = size
    if not isinstance(rows, (list, tuple)) or len(rows) != h:
        raise ValueError(f"{what}: 行数 {len(rows) if isinstance(rows, (list, tuple)) else '?'} != size[1] {h}")
    out: list[list[int]] = []
    for i, row in enumerate(rows):
        try:
            parts = [int(t) for t in str(row).split()]
        except ValueError:
            raise ValueError(f"{what} 第 {i} 行不是整数序列: {row!r}")
        if len(parts) != w:
            raise ValueError(f"{what} 第 {i} 行宽 {len(parts)} != size[0] {w}")
        out.append(parts)
    return out


def load_region_layer(yaml_str: str, base_dir: str | Path | None = None) -> RegionLayer:
    """从 YAML 加载区域层（ADR-0029 D3）。校验失败抛 ValueError（R7：上层降级告警）。

    YAML 形态：
      map_name / size: [w, h]
      big_palette: {1: main_base, 2: natural}          # key = 格点值 → 大区 stable_id
      big_grid: ["1 1 2 2", ...]       或 big_grid_png: big.png（调色板索引 = key）
      leaf_palette: {1: main_ramp}     （可缺省 = 无 leaf）
      leaf_grid / leaf_grid_png        （可缺省）
      big_regions: {main_base: {anchor: [x, y], display_name_zh: 主矿}}
      regions: {main_ramp: {parent: main_base, anchor: [x, y], build_slots: [...]}}
      pos_marks: {rally: {pos: [x, y]}}
      build_slots: {r1: {tl: [x, y], size: 2}}
    """
    d = yaml.safe_load(yaml_str)
    if not isinstance(d, dict):
        raise ValueError("区域布局 YAML 顶层必须是 mapping")
    size = (int(d["size"][0]), int(d["size"][1]))
    w, h = size
    base = Path(base_dir) if base_dir is not None else Path(".")
    big_palette = {int(k): str(v) for k, v in (d.get("big_palette") or {}).items()}
    leaf_palette = {int(k): str(v) for k, v in (d.get("leaf_palette") or {}).items()}

    if d.get("big_grid") is not None:
        big_rows = _parse_grid_rows(d["big_grid"], size, "big_grid")
    elif d.get("big_grid_png"):
        big_rows = load_palette_png(base / d["big_grid_png"])
        if len(big_rows) != h or any(len(r) != w for r in big_rows):
            raise ValueError("big_grid_png 尺寸与 size 不符")
    else:
        raise ValueError("缺 big_grid 或 big_grid_png")

    leaf_rows: list[list[int]] | None = None
    if d.get("leaf_grid") is not None:
        leaf_rows = _parse_grid_rows(d["leaf_grid"], size, "leaf_grid")
    elif d.get("leaf_grid_png"):
        leaf_rows = load_palette_png(base / d["leaf_grid_png"])
        if len(leaf_rows) != h or any(len(r) != w for r in leaf_rows):
            raise ValueError("leaf_grid_png 尺寸与 size 不符")

    # 扫描 leaf 格点 → 各 leaf 的 cell 集
    leaf_cells: dict[str, set[tuple[int, int]]] = {}
    if leaf_rows is not None:
        for y in range(h):
            for x in range(w):
                v = leaf_rows[y][x]
                if v == 0:
                    continue
                lid = leaf_palette.get(v)
                if lid is not None:
                    leaf_cells.setdefault(lid, set()).add((x, y))

    regions: dict[str, Region] = {}
    for name, data in (d.get("regions") or {}).items():
        regions[name] = Region(
            stable_id=name,
            parent=data["parent"],
            cells=frozenset(leaf_cells.get(name, set())),
            anchor=_point2(data["anchor"]),
            display_name_zh=data.get("display_name_zh", name),
            aliases_zh=tuple(data.get("aliases_zh") or ()),
            build_slots=tuple(data.get("build_slots") or ()),
        )
    # children 反向填充（大区 → leaf 集合）
    children_of: dict[str, frozenset[str]] = {}
    for lid, r in regions.items():
        children_of.setdefault(r.parent, frozenset())
        children_of[r.parent] = children_of[r.parent] | {lid}
    big_regions: dict[str, BigRegion] = {}
    for name, data in (d.get("big_regions") or {}).items():
        big_regions[name] = BigRegion(
            stable_id=name,
            anchor=_point2(data["anchor"]),
            display_name_zh=data.get("display_name_zh", name),
            aliases_zh=tuple(data.get("aliases_zh") or ()),
            children=children_of.get(name, frozenset()),
        )

    pos_marks: dict[str, PosMark] = {}
    for name, data in (d.get("pos_marks") or {}).items():
        pos_marks[name] = PosMark(
            name=name, pos=_point2(data["pos"]), description_zh=data.get("description_zh", ""),
        )
    build_slots: dict[str, BuildSlot] = {}
    for name, data in (d.get("build_slots") or {}).items():
        build_slots[name] = BuildSlot(
            name=name,
            tl=GridPos(int(data["tl"][0]), int(data["tl"][1])),
            size=int(data["size"]),
            kind=data.get("kind", "production"),
            pos=_point2(data["pos"]) if "pos" in data else None,
            alias_zh=str(data.get("alias_zh", "")),
        )

    layer = RegionLayer(
        map_name=d.get("map_name", "unknown"),
        size=size,
        big_grid=Grid(w, h, big_rows),
        big_index=big_palette,
        big_regions=big_regions,
        leaf_grid=Grid(w, h, leaf_rows) if leaf_rows is not None else None,
        leaf_index=leaf_palette,
        regions=regions,
        pos_marks=pos_marks,
        build_slots=build_slots,
    )
    errs = validate_layer(layer)
    if errs:
        raise ValueError("区域布局校验失败:\n- " + "\n- ".join(errs))
    return layer
