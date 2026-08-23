"""tactical_map.region_view：格点区域 → Markdown 网格（I18 落地，2026-08-23 用户拍板）。

规格（用户原话）：**一格一词，不超过 3 字符** ——
- `·`  空地，可建造
- `✗`  地形障碍，不可建造
- `D1 D2…` 补给站槽（Depot+序）｜`R1 R2…` 兵营槽｜`F1` 工厂槽｜`S1` 星港槽
- `R+ F+ S+` 挂件预留槽（反应堆/科技实验室）
- `gas` 气井｜`CC` 主基地｜`M` 矿脉（预设固定建造点）

行头 Y 从高到低、列头 X 从左到右；多格建筑（补给站 2×2、兵营 3×3）的整个
footprint 都填同一标签——尺寸一眼可见。**网格看布局结构，不看建造状态**（已建/
在建去 observe 查，用户拍板不进网格）；序号 = 地图规划里的槽位名（depot3→D3），
坐标与校准建造点去读规划文件本身。

地形来自 checked-in 真机采集数据（data/ladder_map/terrain.json，driver 无关）；
注意 game_info 的 placeable **不含资源占用**（矿/井位置也是 1），所以预设点
（gas/CC/M）由 reserved_boxes 叠加标出，优先级 槽位 > 预设点 > 地形。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from game.geometry import Point2
from tactical_map.placement import BuildSlot, is_valid_slot_name
from tactical_map.reserved import reserved_boxes

_TERRAIN_JSON = Path(__file__).resolve().parent / "data" / "ladder_map" / "terrain.json"

#: 网格上限（列/行）——再大表格不可读，报错并建议缩框或 step=2（I18：≤10×10 偏好）
MAX_COLS = 14
MAX_ROWS = 14

#: 槽位名前缀 → 标签字母（挂件 = 母建筑前缀 + "+"）
_RESERVED_LABEL = {"base": "CC", "geyser": "gas", "mineral": "M"}

#: 词表脚注（每次渲染附在网格末尾 —— 读文件的人不一定先读过 maps/index.md）
LEGEND = ("词表：`·` 空可建 · `✗` 地形障碍 · `gas/CC/M` 预设固定点；"
           "**其余标签就是槽位正式名**（D=补给站 R=兵营 F=工厂 S=星港，序号=序，`+`=挂件位；"
           "引用/编辑用它，中文别名看规划文件 alias_zh）")

_placeable_cache: tuple[tuple[int, int], bytes] | None = None


def slot_label(name: str, kind: str = "") -> str:
    """槽位名 → 格子标签。**名字就是标签**（2026-08-23 起简写即正式标记：D1/R4+/F1）。

    规范外的名字不猜：给 `?`（读规划文件核对）—— 命名约定由保存/编辑校验强制，
    这里只是防线。
    """
    return name if is_valid_slot_name(name) else "?"


def load_placeable() -> tuple[tuple[int, int], bytes]:
    """全图可建造栅格（checked-in 真机采集；行主序 uint8，1=可建造）。模块级缓存。"""
    global _placeable_cache
    if _placeable_cache is None:
        raw = json.loads(_TERRAIN_JSON.read_text(encoding="utf-8"))
        size = (int(raw["size"][0]), int(raw["size"][1]))
        _placeable_cache = (size, base64.b64decode(raw["placeable_b64"]))
    return _placeable_cache


def _reserved_overlay(catalog) -> dict[tuple[int, int], str]:
    """预设固定点（12 基地 + 24 气井 + 矿脉）→ {格点: 标签}（矩形整块）。"""
    out: dict[tuple[int, int], str] = {}
    for b in reserved_boxes(catalog):
        label = _RESERVED_LABEL[b["kind"]]
        for x in range(b["tl"][0], b["br"][0] + 1):
            for y in range(b["tl"][1], b["br"][1] + 1):
                out[(x, y)] = label
    return out


def _slot_cells(slot: dict) -> list[tuple[int, int]]:
    """槽位 dict（地图规划 YAML 形态 {pos,size}）→ footprint 格点（TL 用锁定公式反解）。"""
    pos = slot.get("pos")
    size = int(slot.get("size") or 2)
    if not pos:
        return []
    tl = BuildSlot.tl_from_pos(Point2(float(pos[0]), float(pos[1])), size)
    return [(x, y) for x in range(tl.x, tl.x + size) for y in range(tl.y, tl.y + size)]


def render_region(bbox: tuple[int, int, int, int], slots: dict[str, dict],
                  catalog, step: int = 1, *, title: str = "") -> str:
    """框选区域 → Markdown 网格。

    bbox = (x1, y1, x2, y2)（左下 + 右上，闭区间）；slots = 地图规划的
    build_slots（{name: {pos, size, kind}}）；catalog 用于预设点目录。
    """
    (w, h), grid = load_placeable()
    x1, y1, x2, y2 = bbox
    step = max(1, int(step))
    clamped = []
    if x1 < 0 or y1 < 0 or x2 >= w or y2 >= h:
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        clamped.append(f"bbox 已钳到地图范围 {w}×{h}")
    cols = list(range(x1, x2 + 1, step))
    rows = list(range(y2, y1 - 1, -step))      # Y 从高到低（俯视）
    if len(cols) > MAX_COLS or len(rows) > MAX_ROWS:
        raise ValueError(
            f"网格 {len(cols)}×{len(rows)} 超上限 {MAX_COLS}×{MAX_ROWS} —— "
            f"缩小 bbox 或加 step（当前 {step}，试 step={step * 2}）")

    overlay: dict[tuple[int, int], str] = {}
    for name, slot in (slots or {}).items():
        label = slot_label(name, slot.get("kind") or "")
        for cell in _slot_cells(slot):
            overlay[cell] = label              # 槽位 > 预设点 > 地形
    for cell, label in _reserved_overlay(catalog).items():
        overlay.setdefault(cell, label)

    def _cell(x: int, y: int) -> str:
        hit = overlay.get((x, y))
        if hit:
            return hit
        return "·" if grid[y * w + x] else "✗"

    out = [f"# 区域 {title or '网格'}（x{x1}–{x2}，y{y1}–{y2}，step={step}）", ""]
    if not slots:
        out.append("> 该规划没有槽位（空白预设）—— 看出厂布局用 `maps/layout-bl/<bbox>.md`。")
        out.append("")
    out.append("| y\\x | " + " | ".join(str(x) for x in cols) + " |")
    out.append("|---|" + "|".join([":---:"] * len(cols)) + "|")
    for y in rows:
        out.append(f"| **{y}** | " + " | ".join(_cell(x, y) for x in cols) + " |")
    out.append("")
    out.append(LEGEND)
    if clamped:
        out.append("")
        out.extend(f"> ⚠️ {c}" for c in clamped)
    return "\n".join(out) + "\n"
