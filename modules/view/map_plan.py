"""view.map_plan：map_plan 提案的覆盖层投影与落盘（B14）。

map_plan 提案接受后**写回机器生成的覆盖层**，而不是改写手写 base_layout.yaml：
手写文件带真机校准注释、人工维护，机器改写会吞注释；覆盖层是机器产物、
无注释需求，safe_dump 往返安全。加载侧合并逻辑在 tactical_map.base。

语义约定（V1，文档写明）：
- 覆盖层坐标是**世界坐标**，条目被标为 world_fixed，实例化时不再随出生点平移；
- 两个 spawn 变体（bl/tr）的同名条目共享名字空间：bl 优先、tr 补缺，
  用户编辑的是 bl 视图（离线夹具 spawn=bl），tr 变体按世界坐标不镜像。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from game import Point2
from tactical_map.base import DATA_DIR, BaseTemplate, load_base_overrides
from tactical_map.placement import BuildSlot, is_valid_slot_name

MAP_OVERRIDES_PATH = DATA_DIR / "ladder_map" / "base_layout.overrides.yaml"

SLOT_SIZES = {2, 3, 5}
SLOT_KINDS = {"supply", "production", "addon"}


@dataclass
class MapHunkLike:
    """apply 只吃提案 hunk 的最小接口（view.proposals.Hunk 天然满足，不必 import 它）。"""

    id: str
    kind: str
    payload: dict


def merge_map_state(template: BaseTemplate, overrides: dict) -> dict:
    """base + overrides → 合并态（名字空间统一，坐标 = 世界坐标）。"""
    slots: dict[str, dict] = {}
    marks: dict[str, dict] = {}
    for _name, layout in template.spawns.items():
        for s in layout.build_slots:
            if s.name not in slots:
                slots[s.name] = {"pos": [float(s.pos.x), float(s.pos.y)] if s.pos else None,
                                 "size": int(s.size), "kind": s.kind}
        for m in layout.pos_marks:
            if m.name not in marks:
                marks[m.name] = {"pos": [float(m.pos.x), float(m.pos.y)],
                                 "description_zh": m.description_zh}
    for name in overrides.get("build_slots_removed") or []:
        slots.pop(str(name), None)
    for name, entry in (overrides.get("build_slots") or {}).items():
        slots[str(name)] = entry
    for name in overrides.get("pos_marks_removed") or []:
        marks.pop(str(name), None)
    for name, entry in (overrides.get("pos_marks") or {}).items():
        marks[str(name)] = entry
    return {"slots": slots, "marks": marks}


def _footprint(pos: list[float], size: int) -> tuple[int, int, int, int]:
    """锚点（格心）→ footprint 格点闭区间。公式来自 tactical_map.placement（单一真相源）。"""
    tl = BuildSlot.tl_from_pos(Point2(float(pos[0]), float(pos[1])), size)
    return int(tl.x), int(tl.y), int(tl.x + size - 1), int(tl.y + size - 1)


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def apply_map_overrides(
    current: dict,
    template: BaseTemplate,
    hunks: list[MapHunkLike],
) -> tuple[dict, list[dict]]:
    """map_plan hunks → 新覆盖层（不写盘，纯投影）。返回 (new_overrides, errors)。

    校验（拒绝 = errors 带 hunk_id + 结构化中文理由，不静默）：
    - 名字唯一：add 撞合并态已有名 → 拒；rename 目标是已有名 → 拒；
    - 槽位 size ∈ {2,3,5}、kind ∈ {supply,production,addon}；
    - 重叠：add 槽位与合并态其它槽位 footprint 相交 → 拒
      （placeable 地形校验在前端编辑器即时做，后端下次真机运行时自然生效）；
    - del 不存在 → 拒（指向不存在的对象是请求错误，不是可忽略的幂等）；
    - move/rename 指向不存在 → 拒。
    """
    errors: list[dict] = []
    state = merge_map_state(template, current)
    slots: dict[str, dict] = state["slots"]
    marks: dict[str, dict] = state["marks"]

    def err(h: MapHunkLike, text: str) -> None:
        errors.append({"hunk_id": h.id, "text_zh": text})

    def is_pos(p: object) -> bool:
        return isinstance(p, (list, tuple)) and len(p) == 2 and all(
            isinstance(v, (int, float)) for v in p)

    for h in hunks:
        p = h.payload
        if h.kind == "add_mark":
            name = str(p.get("name") or "").strip()
            pos = p.get("pos")
            if not name:
                err(h, "add_mark 缺 name")
            elif not is_pos(pos):
                err(h, "add_mark 缺合法 pos（[x, y] 两个数）")
            elif name in marks:
                err(h, f"点位 {name!r} 已存在（要用新位置请 move_mark）")
            else:
                marks[name] = {"pos": [float(pos[0]), float(pos[1])], "description_zh": ""}
        elif h.kind == "move_mark":
            name = str(p.get("name") or "")
            pos = p.get("pos")
            if name not in marks:
                err(h, f"move_mark 指向不存在的点位 {name!r}")
            elif not is_pos(pos):
                err(h, f"move_mark {name!r} 缺合法 pos")
            else:
                marks[name]["pos"] = [float(pos[0]), float(pos[1])]
        elif h.kind == "rename_mark":
            src = str(p.get("from") or "").strip()
            dst = str(p.get("to") or "").strip()
            if src not in marks:
                err(h, f"rename_mark 指向不存在的点位 {src!r}")
            elif not dst:
                err(h, "rename_mark 缺 to")
            elif dst in marks and dst != src:
                err(h, f"点位 {dst!r} 已存在，不能重名")
            else:
                entry = marks.pop(src)
                entry["name"] = dst
                marks[dst] = entry
        elif h.kind == "del_mark":
            name = str(p.get("name") or "")
            if name not in marks:
                err(h, f"del_mark 指向不存在的点位 {name!r}")
            else:
                marks.pop(name, None)
        elif h.kind == "add_slot":
            name = str(p.get("name") or "").strip()
            pos = p.get("pos")
            size = int(p.get("size") or 0)
            kind = str(p.get("kind") or "")
            if not name:
                err(h, "add_slot 缺 name")
            elif not is_valid_slot_name(name):
                err(h, f"槽位名 {name!r} 不符合简写约定（D/R/F/S+序号[+挂件]，"
                        "如 D17、R5、F2、R5+；中文别名写 alias_zh）")
            elif name in slots:
                err(h, f"槽位 {name!r} 已存在")
            elif size not in SLOT_SIZES:
                err(h, f"add_slot {name!r} 的 size {size} 非法（合法 {sorted(SLOT_SIZES)}）")
            elif kind not in SLOT_KINDS:
                err(h, f"add_slot {name!r} 的 kind {kind!r} 非法（合法 {sorted(SLOT_KINDS)}）")
            elif not is_pos(pos):
                err(h, f"add_slot {name!r} 缺合法 pos")
            else:
                fp = _footprint([float(pos[0]), float(pos[1])], size)
                for other, entry in slots.items():
                    if entry.get("pos") is None:
                        continue
                    ofp = _footprint([float(entry["pos"][0]), float(entry["pos"][1])],
                                     int(entry["size"]))
                    if _overlaps(fp, ofp):
                        err(h, f"add_slot {name!r} 与既有槽位 {other!r} 重叠")
                        break
                else:
                    slots[name] = {"pos": [float(pos[0]), float(pos[1])],
                                   "size": size, "kind": kind,
                                   "alias_zh": str(p.get("alias_zh") or "")}
        elif h.kind == "del_slot":
            name = str(p.get("name") or "")
            if name not in slots:
                err(h, f"del_slot 指向不存在的槽位 {name!r}")
            else:
                slots.pop(name, None)
        else:
            err(h, f"map_plan 不认识的 hunk kind {h.kind!r}")

    # 合并态 → 覆盖层（diff：与 base 不同的进覆盖，base 有而合并态没有的进 removed）
    base = merge_map_state(template, {})
    out: dict = {}
    added_slots = {n: e for n, e in slots.items()
                   if n not in base["slots"] or base["slots"][n] != e}
    added_marks = {n: e for n, e in marks.items()
                   if n not in base["marks"] or base["marks"][n] != e}
    removed_slots = [n for n in base["slots"] if n not in slots]
    removed_marks = [n for n in base["marks"] if n not in marks]
    if added_slots:
        out["build_slots"] = added_slots
    if added_marks:
        out["pos_marks"] = added_marks
    if removed_slots:
        out["build_slots_removed"] = removed_slots
    if removed_marks:
        out["pos_marks_removed"] = removed_marks
    return out, errors


def load_map_overrides(path: Path | None = None) -> dict:
    """读当前覆盖层（不存在 = 空）。"""
    p = path or MAP_OVERRIDES_PATH
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def save_map_overrides(overrides: dict, path: Path | None = None) -> None:
    """写回覆盖层（机器生成、无注释需求）。"""
    p = path or MAP_OVERRIDES_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(overrides, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def changed_names(base: BaseTemplate, overrides: dict) -> dict:
    """map_overlay preview 的摘要：变更了哪些名字。"""
    state = merge_map_state(base, overrides)
    merged = merge_map_state(base, {})
    base_slots = set(merged["slots"])
    base_marks = set(merged["marks"])
    now_slots = set(state["slots"])
    now_marks = set(state["marks"])
    return {
        "changed_slots": sorted((base_slots | now_slots) - (base_slots & now_slots)),
        "changed_marks": sorted((base_marks | now_marks) - (base_marks & now_marks)),
    }
