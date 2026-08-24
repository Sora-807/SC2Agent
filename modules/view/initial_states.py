"""view.initial_states：initial-state 快照文件存储（PLAN-V2 批 3，I2）。

`{dir}/{id}.yaml`，与 production-plans/map-plans/strategies 平级（I2：独立资源，
同一快照可被多个规划引用、也可被 export 落盘复用）。校验走
`planner.initial_state.validate_state_doc`（catalog/工人分项/supply_cap 对账）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml

#: 出厂示例种子（只在缺失时播种 —— 与 agent/seeds 同一条「只补缺失」纪律）
EXAMPLE_SEED = """\
id: example-midgame
title_zh: 示例：中期双兵营+科技挂件（可复制改用）
minerals: 400
gas: 100
supply_used: 21
supply_cap: 21
workers:
  mineral: 14
  gas: 3
  building: 0
  scouting: 0
  idle: 0
buildings:
  terran/commandcenter: 1
  terran/supplydepot: 1
  terran/barracks: 2
  terran/techlab: 1
units:
  terran/marine: 4
upgrades: []
"""

LOCKED_PREFIXES = ("example-",)


def _locked(pid: str) -> bool:
    return pid.startswith(LOCKED_PREFIXES)


class InitialStateStore:
    """initial-state 文件存储：`{dir}/{id}.yaml`；dir=None = 纯内存（测试）。"""

    def __init__(self, dir: Path | None) -> None:  # noqa: A002
        self._dir = dir
        self._lock = threading.Lock()
        self._files: dict[str, Path | None] = {}
        self._mem: dict[str, dict] = {}
        if dir is not None:
            dir.mkdir(parents=True, exist_ok=True)
            for p in sorted(dir.glob("*.yaml")):
                self._files[p.stem] = p
        if "example-midgame" not in self._files:
            self._write("example-midgame", yaml.safe_load(EXAMPLE_SEED))

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
            out = []
            for pid in sorted(self._files):
                d = self._read(pid)
                if not d:
                    continue
                p = self._files[pid]
                out.append({
                    "id": pid,
                    "title_zh": str(d.get("title_zh") or pid),
                    "locked": _locked(pid),
                    "workers": sum(int(v) for v in (d.get("workers") or {}).values()),
                    "buildings": len(d.get("buildings") or {}),
                    "updated_at": (p.stat().st_mtime if p is not None
                                   else float(d.get("updated_at") or 0.0)),
                })
            return out

    def get(self, pid: str) -> dict:
        with self._lock:
            d = self._read(pid)
        if not d:
            raise KeyError(pid)
        return {**d, "id": pid}

    def save(self, pid: str, doc: dict, catalog) -> dict:
        """全量保存（工作区写/REST PUT 同一条路）：先校验再落盘。"""
        from planner.initial_state import validate_state_doc

        with self._lock:
            if _locked(pid):
                raise ValueError("示例种子已锁定：复制一份再改（id 别用 example- 前缀）")
            errs = validate_state_doc({**doc, "id": pid}, catalog)
            if errs:
                return {"ok": False, "errors": [{"hunk_id": None, "text_zh": e} for e in errs]}
            cur = self._read(pid)
            self._write(pid, {**cur, **doc, "id": pid, "updated_at": time.time()})
            return {"ok": True}

    def remove(self, pid: str) -> None:
        with self._lock:
            if _locked(pid):
                raise ValueError("示例种子锁定，不能删除（复制一份改你自己的）")
            p = self._files.pop(pid, None)
            if p is None and self._mem.pop(pid, None) is None:
                raise KeyError(pid)
            if p is not None:
                p.unlink(missing_ok=True)

    @property
    def dir(self) -> Path | None:
        return self._dir
