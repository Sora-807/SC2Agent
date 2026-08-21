"""view.map_plans：离线地图规划的文件存储（P2）—— **规划 = 某地图 × 某方的一种布局**。

用户拍板的模型（2026-08-21 十三轮反馈）：规划不是"一张地图"，而是**针对
某个地图 + 红/蓝方的一个布局方案**——双下拉（先选地图和阵营，再在独立的
规划空间里切换布局），随时换布局对比。

文件形态（一规划一 YAML，单出生点分支）：
`{id, title_zh, map_name, spawn(bl|tr), origin, anchor, build_slots, pos_marks, updated_at}`

锁定预设（init 生成，复制是唯一改动路径）：
- `default-bl/tr`：**空白地图** —— 只有地形/资源/基地预设，无任何自建槽位
 （用户拍板"去掉所有自建槽位后才是默认地图"）；
- `layout-bl/tr`：出厂校准布局 —— 手写 base_layout 对应方的全部槽位
 （真机 can_place 扫描校准数据的引用副本）。

编辑语义与 map_plan 提案同一套校验（apply_map_overrides：重名/重叠/尺寸/
指向不存在）；离线直改文件（不走审批）。payload = static/map 形状 +
真机地形 + 全图资源点 + 预设名预留区。
"""
from __future__ import annotations

import tempfile
import threading
import time
import uuid
from pathlib import Path

import yaml

from tactical_map.base import BaseTemplate, instantiate_spawn, load_base_template
from view.encode import to_json
from view.map_plan import apply_map_overrides, merge_map_state
from view.map_plan import MapHunkLike
from view.map_plan import _footprint, _overlaps   # noqa: SLF001 —— 同包几何单点，不重写
from view.statics import ladder_resource_nodes, ladder_terrain_view, map_static

#: 手写模板源（预设从这里生成）
LADDER_SOURCE = (Path(__file__).resolve().parents[1] / "tactical_map"
                 / "data" / "ladder_map" / "base_layout.yaml")

#: 锁定的预设 id 前缀（default-*/layout-*：空白与出厂校准，复制再改）
LOCKED_PREFIXES = ("default-", "layout-")

SPAWNS = ("bl", "tr")


def _source() -> dict:
    return yaml.safe_load(LADDER_SOURCE.read_text(encoding="utf-8")) or {}


def _source_side(side: str) -> dict:
    s = (_source().get("spawns") or {}).get(side) or {}
    return {"origin": s.get("origin"), "anchor": s.get("anchor"),
            "build_slots": dict(s.get("build_slots") or {}),
            "pos_marks": dict(s.get("pos_marks") or {})}


def _preset(pid: str, title: str, side: str, *, empty: bool) -> dict:
    src = _source_side(side)
    return {"id": pid, "title_zh": title, "map_name": "LadderMap", "spawn": side,
            "origin": src["origin"], "anchor": src["anchor"],
            "build_slots": {} if empty else src["build_slots"],
            "pos_marks": {} if empty else src["pos_marks"],
            "updated_at": 0.0}


def _presets() -> list[dict]:
    return [
        _preset("default-bl", "默认空白地图（蓝方）", "bl", empty=True),
        _preset("default-tr", "默认空白地图（红方）", "tr", empty=True),
        _preset("layout-bl", "出厂校准布局（蓝方）", "bl", empty=False),
        _preset("layout-tr", "出厂校准布局（红方）", "tr", empty=False),
    ]


def _hunks_of(raw_hunks: list[dict]) -> list[MapHunkLike]:
    return [MapHunkLike(id=str(h.get("id") or f"h{i}"),
                        kind=str(h.get("kind") or ""),
                        payload=dict(h.get("payload") or {}))
            for i, h in enumerate(raw_hunks)]


def _reserved_boxes(catalog, mains_spec: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """固定建造点预留区（真机采集数据 → 矩形 + 预设名清单，几何单点）。

    - 基地（扩张点）：CC footprint（catalog 的 commandcenter.size，实测 12 个），
      命名 蓝方主矿/蓝方二矿…红方主矿…（主基按出生点匹配，分矿按距离编号）；
    - 气井：3×3（refinery footprint），命名 归属基地 + 气井N；
    - 矿脉：2×2，不命名（98 块）。

    背景：game_info 的 placeable/pathable **不含资源占用**（矿/井位置两格全 1，
    2026-08-21 实测）—— 预留必须靠这份显式数据。
    """
    from view.statics import _ladder_map_data

    raw = _ladder_map_data() or {}
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
    mains: dict[int, str] = {}
    if mains_spec:
        for spawn, origin in mains_spec.items():
            side = "蓝方" if spawn == "bl" else "红方"
            best = min(range(len(bases)),
                       key=lambda i: (bases[i][0] - origin[0]) ** 2 + (bases[i][1] - origin[1]) ** 2)
            bx, by = bases[best]
            if abs(bx - origin[0]) < 3 and abs(by - origin[1]) < 3:
                mains[best] = side
    for i, side in mains.items():
        names[i] = side + "主矿"
    zh = ["二", "三", "四", "五", "六", "七", "八", "九", "十"]
    for i in mains:
        side = mains[i]
        exps = [j for j in range(len(bases)) if j not in mains]
        exps.sort(key=lambda j: (bases[j][0] - bases[i][0]) ** 2 + (bases[j][1] - bases[i][1]) ** 2)
        others = [k for k in mains if k != i]
        for n, j in enumerate(exps[:len(zh)]):
            if others and any(
                (bases[j][0] - bases[k][0]) ** 2 + (bases[j][1] - bases[k][1]) ** 2
                < (bases[j][0] - bases[i][0]) ** 2 + (bases[j][1] - bases[i][1]) ** 2
                for k in others):
                continue
            names[j] = side + zh[n] + "矿"
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


def _default_catalog():
    from game.catalog import load_terran

    return load_terran()


def _plan_locked(pid: str) -> bool:
    return pid.startswith(LOCKED_PREFIXES)


def _template_from_dict(d: dict) -> BaseTemplate:
    """规划 dict（单出生点）→ BaseTemplate（复用 load_base_template 的解析）。

    包装成 base_layout 形状（spawns 只含本方）再走同一条解析路径 ——
    校验/合并/会话装配共用同一份解析。
    """
    side = str(d.get("spawn") or "bl")
    wrapped = {
        "map_name": d.get("map_name") or "LadderMap",
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
        path = f.name
    try:
        return load_base_template(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _source_mains() -> dict[str, tuple[float, float]]:
    sp = _source().get("spawns") or {}
    out: dict[str, tuple[float, float]] = {}
    for side in SPAWNS:
        if side in sp and sp[side].get("origin"):
            o = sp[side]["origin"]
            out[side] = (float(o[0]), float(o[1]))
    return out


class MapPlanStore:
    """地图规划文件存储：`{dir}/{id}.yaml`；dir=None = 纯内存（测试）。"""

    def __init__(self, dir: Path | None, catalog=None) -> None:  # noqa: A002
        self._catalog = catalog
        self._dir = dir
        self._lock = threading.Lock()
        self._files: dict[str, Path | None] = {}
        self._mem: dict[str, dict] = {}
        if dir is not None:
            dir.mkdir(parents=True, exist_ok=True)
            for p in sorted(dir.glob("*.yaml")):
                self._files[p.stem] = p
        # 预设自愈：锁定四件（空白/出厂 × 蓝/红）总是存在
        for preset in _presets():
            if preset["id"] not in self._files:
                self._write(preset["id"], preset)

    # ---- 基础读写 ----

    def _read(self, pid: str) -> dict:
        p = self._files.get(pid)
        if p is not None:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return self._mem.get(pid) or {}

    def _write(self, pid: str, d: dict) -> None:
        if self._dir is None:
            self._mem[pid] = d
            self._files[pid] = None
            return
        path = self._dir / f"{pid}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
        tmp.replace(path)
        self._files[pid] = path

    # ---- 对外 ----

    def list(self) -> list[dict]:
        with self._lock:
            return self._list_unlocked()

    def _list_unlocked(self) -> list[dict]:
        out = []
        for pid in sorted(self._files, key=lambda k: (not _plan_locked(k), k)):
            d = self._read(pid)
            if not d:
                continue
            p = self._files[pid]
            updated = (p.stat().st_mtime if p is not None
                       else float(d.get("updated_at") or 0.0))
            out.append({
                "id": pid,
                "title_zh": str(d.get("title_zh") or pid),
                "map_name": str(d.get("map_name") or "unknown"),
                "spawn": str(d.get("spawn") or "bl"),
                "locked": _plan_locked(pid),
                "slots": len(d.get("build_slots") or {}),
                "updated_at": updated,
            })
        return out

    def payload(self, pid: str) -> dict:
        """该规划的 static/map 形状 payload（画布直接吃）。

        terrain/resource_nodes 附真机采集数据（全图、无战争迷雾）；reserved 附
        预设名预留区 —— 槽位摆放要看得见不可占用区。
        """
        with self._lock:
            d = self._read(pid)
        if not d:
            raise KeyError(pid)
        t = _template_from_dict(d)
        side = str(d.get("spawn") or "bl")
        layout = t.spawns.get(side)
        assert layout is not None
        layer = instantiate_spawn(t, layout, layout.origin)   # cc=origin → 零平移
        out = to_json(map_static(layer, side, terrain=ladder_terrain_view(),
                                 resource_nodes=ladder_resource_nodes()))
        out["reserved"] = _reserved_boxes(self._catalog or _default_catalog(),
                                          _source_mains())
        return out

    def save(self, pid: str, hunks: list[dict]) -> dict:
        """离线保存：hunks 应用到该规划（与 map_plan 提案同一套校验）。"""
        with self._lock:
            if _plan_locked(pid):
                raise ValueError("预设已锁定（空白地图/出厂校准）：复制一份再改")
            d = self._read(pid)
            if not d:
                raise KeyError(pid)
            t = _template_from_dict(d)
            new_over, errors = apply_map_overrides({}, t, _hunks_of(hunks))
            if errors:
                return {"ok": False, "errors": errors}
            # 固定建造点预留校验：只查**本次改动**的槽位（预设存量不追溯）
            reserved = _reserved_boxes(self._catalog or _default_catalog(), None)
            for name, entry in (new_over.get("build_slots") or {}).items():
                pos = entry.get("pos")
                if not pos:
                    continue
                fp = _footprint([float(pos[0]), float(pos[1])], int(entry.get("size") or 0))
                for rb in reserved:
                    if _overlaps(fp, (rb["tl"][0], rb["tl"][1], rb["br"][0], rb["br"][1])):
                        errors.append({
                            "hunk_id": name,
                            "text_zh": f"槽位 {name!r} 压住{rb['label_zh']}（固定建造点，不可占用）",
                        })
                        break
            if errors:
                return {"ok": False, "errors": errors}
            state = merge_map_state(t, new_over)
            marks = {n: {k: v for k, v in e.items() if k != "name"}
                     for n, e in state["marks"].items()}
            out = {**d, "build_slots": state["slots"], "pos_marks": marks,
                   "updated_at": time.time()}
            self._write(pid, out)
            return {"ok": True}

    def create(self, raw: dict) -> dict:
        """新建：复制既有规划（默认空白），id 缺省自动生成。"""
        with self._lock:
            pid = str(raw.get("id") or f"map-{uuid.uuid4().hex[:6]}")
            if pid in self._files:
                raise ValueError(f"地图规划 id {pid!r} 已存在")
            src = str(raw.get("copy_from") or "default-bl")
            if src not in self._files:
                raise ValueError(f"要复制的地图规划 {src!r} 不存在")
            d = self._read(src)
            out = {**d, "id": pid,
                   "title_zh": str(raw.get("title_zh") or f"{d.get('title_zh', '布局')} 副本"),
                   "updated_at": time.time()}
            self._write(pid, out)
            rows = [r for r in self._list_unlocked() if r["id"] == pid]
            assert rows, "刚写的规划不可能不在列表里"
            return rows[0]

    def remove(self, pid: str) -> None:
        with self._lock:
            if _plan_locked(pid):
                raise ValueError("预设已锁定，不能删除（空白地图/出厂校准）")
            p = self._files.pop(pid, None)
            if p is None and self._mem.pop(pid, None) is None:
                raise KeyError(pid)
            if p is not None:
                p.unlink(missing_ok=True)

    def file_path(self, pid: str) -> Path | None:
        """规划文件路径（内存态/不存在 = None —— 子进程会话需要真文件）。"""
        with self._lock:
            return self._files.get(pid)
