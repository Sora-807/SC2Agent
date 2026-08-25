"""view.map_plans：离线地图规划的文件存储（P2 → PLAN-V2 批 2 双分支）。

用户原始设计（2026-08-24 重申）：**左出生点和右出生点是一份地图规划的两种
结构** —— 一份文件 = bl+tr 两套 build_slots/pos_marks（ADR-0033）。单分支旧格式
（`spawn:` + 平铺）继续可读（load_map_plan 兼容包装），但新建/预设全是双分支。

文件形态（双分支）：
`{id, title_zh, map_name, spawns: {bl: {origin, anchor, build_slots, pos_marks},
 tr: {…}}, updated_at}`

锁定预设（init 生成，复制是唯一改动路径；批 2 起取代单分支四件）：
- `default`：**空白地图**（双分支）—— 只有地形/资源/基地预设，无任何自建槽位；
- `layout`：出厂校准布局（双分支）—— 手写 base_layout 两侧的全部槽位。

编辑语义与 map_plan 提案同一套校验（apply_map_overrides：重名/重叠/尺寸/
指向不存在）；hunks 只作用于**当前编辑的分支**（payload/save 都带 spawn）。
离线直改文件（不走审批）。payload = static/map 形状 + 真机地形 + 全图资源点 +
预设名预留区。
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import yaml

from tactical_map.base import BaseTemplate, instantiate_spawn, load_base_template
from tactical_map.placement import is_valid_slot_name
from view.encode import to_json
from view.map_plan import apply_map_overrides, merge_map_state
from view.map_plan import MapHunkLike
from view.map_plan import footprint_of, footprints_overlap
from view.statics import ladder_resource_nodes, ladder_terrain_view, map_static

#: 手写模板源（预设从这里生成）
LADDER_SOURCE = (Path(__file__).resolve().parents[1] / "tactical_map"
                 / "data" / "ladder_map" / "base_layout.yaml")

#: 锁定的预设 id 前缀（default/layout：空白与出厂校准，复制再改；
#: 旧单分支预设 default-bl/tr、layout-bl/tr 是其子串 —— 同样锁）
LOCKED_PREFIXES = ("default", "layout")

#: 单分支时代的锁定预设（批 2 双分支取代；init 时退役 —— 锁定件不可改，删了安全）
LEGACY_LOCKED = ("default-bl", "default-tr", "layout-bl", "layout-tr")

SPAWNS = ("bl", "tr")


def _source() -> dict:
    return yaml.safe_load(LADDER_SOURCE.read_text(encoding="utf-8")) or {}


def _dual_preset(pid: str, title: str, *, empty: bool) -> dict:
    """双分支预设：base_layout 两侧各一份（空白 = 槽位/点位清空，origin/anchor 保留）。"""
    spawns: dict[str, dict] = {}
    for side, s in (_source().get("spawns") or {}).items():
        spawns[side] = {
            "origin": s.get("origin"), "anchor": s.get("anchor"),
            "build_slots": {} if empty else dict(s.get("build_slots") or {}),
            "pos_marks": {} if empty else dict(s.get("pos_marks") or {}),
        }
    return {"id": pid, "title_zh": title, "map_name": "LadderMap",
            "spawns": spawns, "updated_at": 0.0}


def _presets() -> list[dict]:
    return [
        _dual_preset("default", "默认空白地图（蓝红双分支）", empty=True),
        _dual_preset("layout", "出厂校准布局（蓝红双分支）", empty=False),
    ]


def _hunks_of(raw_hunks: list[dict]) -> list[MapHunkLike]:
    return [MapHunkLike(id=str(h.get("id") or f"h{i}"),
                        kind=str(h.get("kind") or ""),
                        payload=dict(h.get("payload") or {}))
            for i, h in enumerate(raw_hunks)]


def _validate_branch_slots(cur_slots: dict, new_slots: dict) -> list[dict]:
    """分支槽位校验（save_payload 的单/双分支共用）：简写约定 + 重叠 + 固定建造点。

    只查**本次改动**的槽位（新增，或 pos/size 变了）；重叠检查覆盖「改动 × 全部」。
    """
    changed = {n for n, e in new_slots.items()
               if n not in cur_slots
               or (cur_slots[n].get("pos") != e.get("pos")
                   or cur_slots[n].get("size") != e.get("size"))}
    errors: list[dict] = []
    reserved = _reserved_boxes(_default_catalog(), None)
    for a in sorted(changed):
        ea = new_slots[a]
        if not is_valid_slot_name(a):
            errors.append({"hunk_id": a,
                           "text_zh": f"槽位名 {a!r} 不符合简写约定"
                                      "（D/R/F/S+序号[+挂件]，如 D17、R5、R5+；"
                                      "中文别名写 alias_zh）"})
            continue
        pos_a = ea.get("pos")
        if not pos_a:
            continue
        fp_a = footprint_of([float(pos_a[0]), float(pos_a[1])], int(ea.get("size") or 0))
        for b, eb in sorted(new_slots.items()):
            if b == a:
                continue
            pos_b = eb.get("pos")
            if not pos_b:
                continue
            fp_b = footprint_of([float(pos_b[0]), float(pos_b[1])], int(eb.get("size") or 0))
            if footprints_overlap(fp_a, fp_b):
                errors.append({"hunk_id": a, "text_zh": f"槽位 {a!r} 与 {b!r} 重叠"})
                break
        else:
            if (err := _reserved_conflict(a, ea, reserved)) is not None:
                errors.append(err)
    return errors


def _reserved_conflict(name: str, entry: dict, reserved: list[dict]) -> dict | None:
    """槽位 × 固定建造点预留区的重叠检查（save 与 save_payload 共用一份；
    命中第一个就报——错误文案里带预留区中文名）。"""
    pos = entry.get("pos")
    if not pos:
        return None
    fp = footprint_of([float(pos[0]), float(pos[1])], int(entry.get("size") or 0))
    for rb in reserved:
        if footprints_overlap(fp, (rb["tl"][0], rb["tl"][1], rb["br"][0], rb["br"][1])):
            return {"hunk_id": name,
                    "text_zh": f"槽位 {name!r} 压住{rb['label_zh']}（固定建造点，不可占用）"}
    return None


def _reserved_boxes(catalog, mains_spec: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """固定建造点预留区 —— 计算已下沉 tactical_map.reserved（I8：会话装配也要用，
    view 不许被 world 反向依赖）。这里保留签名做委托，payload/save 两个调用点不动。"""
    from tactical_map.reserved import reserved_boxes

    return reserved_boxes(catalog, mains_spec)


def _default_catalog():
    from game.catalog import load_all

    return load_all()


def _plan_locked(pid: str) -> bool:
    return pid.startswith(LOCKED_PREFIXES)


def _template_from_dict(d: dict, spawn: str | None = None) -> BaseTemplate:
    """规划 dict → BaseTemplate（复用 load_base_template 的解析）。

    双分支（有 `spawns` 节）直接走；`spawn` 给了就只取该分支（保存/payload 的
    单分支视图）。单分支旧格式包装成 base_layout 形状再走同一条解析 ——
    校验/合并/会话装配共用同一份解析。
    """
    if d.get("spawns"):
        if spawn is not None:
            side = str(spawn)
            if side not in d["spawns"]:
                raise ValueError(f"规划没有 {side!r} 分支（现有：{sorted(d['spawns'])}）")
            src = d["spawns"][side]
            d = {"map_name": d.get("map_name"), "spawn": side,
                 "origin": src.get("origin"), "anchor": src.get("anchor"),
                 "build_slots": src.get("build_slots") or {},
                 "pos_marks": src.get("pos_marks") or {}}
        else:
            d = {"map_name": d.get("map_name"), "spawns": d["spawns"]}
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
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as f:
        yaml.safe_dump(wrapped, f, allow_unicode=True)
        path = f.name
    try:
        return load_base_template(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _source_mains() -> dict[str, tuple[float, float]]:
    from tactical_map.reserved import source_mains

    return source_mains()


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
        # 单分支时代锁定预设退役（批 2 双分支取代；锁定件不可改 → 直接删是安全的）
        for legacy in LEGACY_LOCKED:
            p = self._files.pop(legacy, None)
            if p is None:
                self._mem.pop(legacy, None)
            else:
                p.unlink(missing_ok=True)
        # 预设自愈：锁定两件（空白/出厂 × 双分支）总是存在
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
            dual = bool(d.get("spawns"))
            if dual:
                spawn, sides = "dual", sorted(d["spawns"])
                slots = sum(len((d["spawns"][s] or {}).get("build_slots") or {})
                            for s in d["spawns"])
            else:
                spawn = str(d.get("spawn") or "bl")
                sides, slots = [spawn], len(d.get("build_slots") or {})
            out.append({
                "id": pid,
                "title_zh": str(d.get("title_zh") or pid),
                "map_name": str(d.get("map_name") or "unknown"),
                "spawn": spawn,          # dual = 双分支（蓝红两页签）
                "spawns": sides,         # 实际有哪些分支（前端页签按这个画）
                "locked": _plan_locked(pid),
                "slots": slots,
                "updated_at": updated,
            })
        return out

    def payload(self, pid: str, spawn: str | None = None) -> dict:
        """该规划（指定分支）的 static/map 形状 payload（画布直接吃）。

        双分支必须给 spawn（缺省 bl）；单分支旧格式自带出生点、spawn 参数忽略。
        terrain/resource_nodes 附真机采集数据（全图、无战争迷雾）；reserved 附
        预设名预留区 —— 槽位摆放要看得见不可占用区。
        """
        with self._lock:
            d = self._read(pid)
        if not d:
            raise KeyError(pid)
        if d.get("spawns"):
            side = str(spawn or "bl")
            if side not in d["spawns"]:
                raise ValueError(f"规划 {pid!r} 没有 {side!r} 分支（现有：{sorted(d['spawns'])}）")
            src = d["spawns"][side]
            view = {"map_name": d.get("map_name"), "spawn": side,
                    "origin": src.get("origin"), "anchor": src.get("anchor"),
                    "build_slots": src.get("build_slots") or {},
                    "pos_marks": src.get("pos_marks") or {}}
        else:
            view = d
        t = _template_from_dict(view)
        side = str(view.get("spawn") or "bl")
        layout = t.spawns.get(side)
        assert layout is not None
        layer = instantiate_spawn(t, layout, layout.origin)   # cc=origin → 零平移
        out = to_json(map_static(layer, side, terrain=ladder_terrain_view(),
                                 resource_nodes=ladder_resource_nodes()))
        out["reserved"] = _reserved_boxes(self._catalog or _default_catalog(),
                                          _source_mains())
        return out

    def save(self, pid: str, hunks: list[dict], spawn: str | None = None) -> dict:
        """离线保存：hunks 应用到该规划的**指定分支**（与 map_plan 提案同一套校验）。"""
        with self._lock:
            if _plan_locked(pid):
                raise ValueError("预设已锁定（空白地图/出厂校准）：复制一份再改")
            d = self._read(pid)
            if not d:
                raise KeyError(pid)
            dual = bool(d.get("spawns"))
            if dual and spawn is None:
                raise ValueError("双分支规划保存要给 spawn（bl|tr —— 改的是哪一侧）")
            t = _template_from_dict(d, spawn=spawn)
            new_over, errors = apply_map_overrides({}, t, _hunks_of(hunks))
            if errors:
                return {"ok": False, "errors": errors}
            # 固定建造点预留校验：只查**本次改动**的槽位（预设存量不追溯）
            reserved = _reserved_boxes(self._catalog or _default_catalog(), None)
            for name, entry in (new_over.get("build_slots") or {}).items():
                if (err := _reserved_conflict(name, entry, reserved)) is not None:
                    errors.append(err)
            if errors:
                return {"ok": False, "errors": errors}
            state = merge_map_state(t, new_over)
            marks = {n: {k: v for k, v in e.items() if k != "name"}
                     for n, e in state["marks"].items()}
            side = str(d.get("spawn") or spawn or "bl")
            out = self._write_branch(d, side, state["slots"], marks)
            self._write(pid, out)
            return {"ok": True}

    @staticmethod
    def _write_branch(d: dict, side: str, slots: dict, marks: dict) -> dict:
        """把分支结果写回规划 dict（双分支只动该侧；单分支平铺）。"""
        if d.get("spawns"):
            src = dict(d["spawns"].get(side) or {})
            src["build_slots"], src["pos_marks"] = slots, marks
            return {**d, "spawns": {**d["spawns"], side: src},
                    "updated_at": time.time()}
        return {**d, "build_slots": slots, "pos_marks": marks,
                "updated_at": time.time()}

    def doc(self, pid: str) -> dict:
        """文档形状（agent 文件工作区读写用的就是这份）：不含画布要的 static/map 大负载。

        双分支 = `spawns: {bl: {build_slots, pos_marks}, tr: {…}}`；单分支旧格式
        照旧平铺（读写都兼容，不强迫迁移）。
        """
        with self._lock:
            d = self._read(pid)
        if not d:
            raise KeyError(pid)
        out: dict = {"id": pid, "title_zh": d.get("title_zh"),
                     "map_name": d.get("map_name"), "updated_at": d.get("updated_at")}
        if d.get("spawns"):
            out["spawns"] = {
                side: {"build_slots": dict((src or {}).get("build_slots") or {}),
                       "pos_marks": dict((src or {}).get("pos_marks") or {})}
                for side, src in d["spawns"].items()
            }
        else:
            for k in ("spawn", "build_slots", "pos_marks"):
                if d.get(k) is not None:
                    out[k] = d[k]
        return out

    def save_payload(self, pid: str, doc: dict) -> dict:
        """全量保存（agent 文件工作区的写钩子走这里）。

        双分支 doc：`spawns: {bl: {build_slots, pos_marks}, tr: {…}}`（缺的分支保留
        现状），校验对**每个提供的分支**各跑一遍。单分支旧 doc 只对单分支规划接受。

        校验口径与 save(hunks) 一致：**只查本次改动的槽位**（预设存量不追溯）。
        改动 = 新增的槽位，或 pos/size 变了的槽位；重叠检查覆盖「改动 × 全部」。
        """
        with self._lock:
            if _plan_locked(pid):
                raise ValueError("预设已锁定（空白地图/出厂校准）：复制一份再改")
            cur = self._read(pid)
            if not cur:
                raise KeyError(pid)
            dual_cur, dual_doc = bool(cur.get("spawns")), bool(doc.get("spawns"))
            if dual_doc and not dual_cur:
                return {"ok": False, "errors": [{
                    "hunk_id": None,
                    "text_zh": "该规划是单分支旧格式：doc 请用平铺形态（spawn/build_slots/pos_marks）"}]}
            if dual_cur and not dual_doc:
                return {"ok": False, "errors": [{
                    "hunk_id": None,
                    "text_zh": "双分支规划的 doc 要用 spawns: {bl: {…}, tr: {…}} 形态"}]}
            if not dual_cur:
                return self._save_flat(pid, cur, doc)
            # 双分支：逐分支校验（任一分支红 = 整体拒）
            errors: list[dict] = []
            new_spawns: dict[str, dict] = {}
            for side, src in cur["spawns"].items():
                br = (doc.get("spawns") or {}).get(side)
                if br is None:
                    new_spawns[side] = src        # 缺的分支保留现状
                    continue
                cur_slots = dict((src or {}).get("build_slots") or {})
                new_slots = {str(k): dict(v) for k, v in (br.get("build_slots") or {}).items()}
                errs = _validate_branch_slots(cur_slots, new_slots)
                for e in errs:
                    e["hunk_id"] = f"{side}/{e['hunk_id']}" if e.get("hunk_id") else e.get("hunk_id")
                errors.extend(errs)
                new_spawns[side] = {**src, "build_slots": new_slots,
                                    "pos_marks": {str(k): dict(v)
                                                  for k, v in (br.get("pos_marks") or {}).items()}}
            if errors:
                return {"ok": False, "errors": errors}
            merged = {**cur, "title_zh": str(doc.get("title_zh") or cur.get("title_zh") or pid),
                      "map_name": str(doc.get("map_name") or cur.get("map_name") or "unknown"),
                      "spawns": new_spawns, "updated_at": time.time()}
            self._write(pid, merged)
            return {"ok": True}

    def _save_flat(self, pid: str, cur: dict, doc: dict) -> dict:
        """单分支旧格式的全量保存（原 save_payload 逻辑，双分支走 spawns 形态）。"""
        cur_slots = dict(cur.get("build_slots") or {})
        new_slots = {str(k): dict(v) for k, v in (doc.get("build_slots") or {}).items()}
        errors = _validate_branch_slots(cur_slots, new_slots)
        if errors:
            return {"ok": False, "errors": errors}
        merged = {**cur, "title_zh": str(doc.get("title_zh") or cur.get("title_zh") or pid),
                  "map_name": str(doc.get("map_name") or cur.get("map_name") or "unknown"),
                  "spawn": str(doc.get("spawn") or cur.get("spawn") or "bl"),
                  "build_slots": new_slots,
                  "pos_marks": {str(k): dict(v)
                                for k, v in (doc.get("pos_marks") or {}).items()},
                  "updated_at": time.time()}
        self._write(pid, merged)
        return {"ok": True}

    def create(self, raw: dict) -> dict:
        """新建：复制既有规划（默认空白双分支），id 缺省自动生成。"""
        with self._lock:
            pid = str(raw.get("id") or f"map-{uuid.uuid4().hex[:6]}")
            if pid in self._files:
                raise ValueError(f"地图规划 id {pid!r} 已存在")
            src = str(raw.get("copy_from") or "default")
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

    @property
    def dir(self) -> Path | None:
        """规划目录（会话图层合并要读整个目录；内存态 = None）。"""
        return self._dir
