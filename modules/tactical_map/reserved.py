"""tactical_map.reserved：固定建造点预留区 —— 预设名单的单一事实源（I8）。

真机采集数据（data/ladder_map/terrain.json 的 bases/resources）→ 矩形 + 预设名：
- 基地（扩张点）：CC footprint，命名 蓝方主矿/蓝方二矿…红方主矿…（主基按出生点
  匹配，分矿归属最近主基按距离编号，只收离本方更近的）；
- 气井：3×3（refinery footprint），命名 归属基地 + 气井N；
- 矿脉：2×2，不命名（98 块）。

背景：game_info 的 placeable/pathable **不含资源占用**（矿/井位置两格全 1，
2026-08-21 实测）—— 预留必须靠这份显式数据。

原实现长在 view/map_plans.py（P2 时代只有规划编辑要吃）；I8 收口后**会话装配
也要用**（预设名进 RegionLayer.pos_marks，placement 的 exact.mark 才能引用），
而 world/session 不许 import view —— 计算下沉到 tactical_map（数据本来就在这层）。
"""
from __future__ import annotations

import json
from pathlib import Path

from game.geometry import Point2
from tactical_map.placement import PosMark

_LADDER_JSON = Path(__file__).resolve().parent / "data" / "ladder_map" / "terrain.json"
_LADDER_SOURCE = Path(__file__).resolve().parent / "data" / "ladder_map" / "base_layout.yaml"

_ZH_EXPANSIONS = ["二", "三", "四", "五", "六", "七", "八", "九", "十"]


def ladder_map_data() -> dict | None:
    try:
        return json.loads(_LADDER_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def source_mains() -> dict[str, tuple[float, float]]:
    """出厂模板（base_layout.yaml）的双方主基 origin —— 预设命名的锚点。

    命名锚点固定用出厂源，不跟所选规划走：名字是全地图命名空间（I8 引用目标
    要全局稳定），规划副本的 origin 理论上可被改，不能让名字跟着漂。
    """
    import yaml

    raw = yaml.safe_load(_LADDER_SOURCE.read_text(encoding="utf-8")) or {}
    out: dict[str, tuple[float, float]] = {}
    for side in ("bl", "tr"):
        sp = (raw.get("spawns") or {}).get(side) or {}
        if sp.get("origin"):
            o = sp["origin"]
            out[side] = (float(o[0]), float(o[1]))
    return out


def reserved_boxes(catalog, mains: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """预留矩形清单（134 块；命名的只有 12 基地 + 24 气井）。

    mains = 双方主基 origin；None 时用出厂模板推（命名不依赖所选规划 —— 名字是
    全地图命名空间，I8 的引用目标因此全局稳定）。
    """
    raw = ladder_map_data() or {}
    boxes: list[dict] = []
    cc = catalog.by_stable_id("terran/commandcenter")
    cc_size = int(cc.size) if cc is not None and cc.size else 5
    refinery = catalog.by_stable_id("terran/refinery")
    geyser_size = int(refinery.size) if refinery is not None and refinery.size else 3

    def box(x: float, y: float, size: int, kind: str, name: str | None = None) -> dict:
        tlx, tly = int(x) - size // 2, int(y) - size // 2
        return {"tl": [tlx, tly], "br": [tlx + size - 1, tly + size - 1],
                "kind": kind, "name": name,
                "label_zh": {"base": "基地", "geyser": "气井", "mineral": "矿脉"}[kind]}

    bases = [(float(b[0]), float(b[1])) for b in raw.get("bases") or []]
    names: dict[int, str] = {}
    mains_idx: dict[int, str] = {}
    if mains is None:
        mains = source_mains()
    for spawn, origin in mains.items():
        side = "蓝方" if spawn == "bl" else "红方"
        best = min(range(len(bases)),
                   key=lambda i: (bases[i][0] - origin[0]) ** 2 + (bases[i][1] - origin[1]) ** 2)
        bx, by = bases[best]
        if abs(bx - origin[0]) < 3 and abs(by - origin[1]) < 3:
            mains_idx[best] = side
    for i, side in mains_idx.items():
        names[i] = side + "主矿"
    for i in mains_idx:
        side = mains_idx[i]
        exps = [j for j in range(len(bases)) if j not in mains_idx]
        exps.sort(key=lambda j: (bases[j][0] - bases[i][0]) ** 2 + (bases[j][1] - bases[i][1]) ** 2)
        others = [k for k in mains_idx if k != i]
        for n, j in enumerate(exps[:len(_ZH_EXPANSIONS)]):
            if others and any(
                (bases[j][0] - bases[k][0]) ** 2 + (bases[j][1] - bases[k][1]) ** 2
                < (bases[j][0] - bases[i][0]) ** 2 + (bases[j][1] - bases[i][1]) ** 2
                for k in others):
                continue
            names[j] = side + _ZH_EXPANSIONS[n] + "矿"
    for i, (bx, by) in enumerate(bases):
        boxes.append(box(bx, by, cc_size, "base", names.get(i)))

    geyser_no: dict[int, int] = {}
    for r in raw.get("resources") or []:
        if r.get("kind") != "geyser":
            continue
        gx, gy = float(r["pos"][0]), float(r["pos"][1])
        nearest = min(range(len(bases)),
                      key=lambda i: (bases[i][0] - gx) ** 2 + (bases[i][1] - gy) ** 2)
        geyser_no[nearest] = geyser_no.get(nearest, 0) + 1
        gname = (names.get(nearest) or "矿区") + f"气井{geyser_no[nearest]}"
        boxes.append(box(gx, gy, geyser_size, "geyser", gname))

    for r in raw.get("resources") or []:
        if r.get("kind") == "geyser":
            continue
        boxes.append(box(float(r["pos"][0]), float(r["pos"][1]), 2, "mineral"))
    return boxes


def reserved_marks(catalog) -> dict[str, PosMark]:
    """命名预留区 → PosMark（I8：placement 点名引用「预设固定建造点」的落点）。

    两侧 36 名一起给：名字全局唯一（蓝方/红方前缀），layer 是单出生点实例但
    引用目标是全图命名空间 —— 缺了哪边名字，哪边就引用不了。
    """
    out: dict[str, PosMark] = {}
    for b in reserved_boxes(catalog):
        name = b.get("name")
        if not name:
            continue
        cx = (b["tl"][0] + b["br"][0]) / 2
        cy = (b["tl"][1] + b["br"][1]) / 2
        out[name] = PosMark(name=name, pos=Point2(cx, cy),
                            description_zh=f"预设固定建造点（{b['label_zh']}）")
    return out
