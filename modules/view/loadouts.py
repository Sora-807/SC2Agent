"""view.loadouts：装配清单（loadout）文件存储（2026-08-23 执行轮批 B，WORKLOG §0.41）。

一份 loadout = 三件套引用：`runtime/loadouts/<id>.yaml`

```yaml
id: bio-tank-bl
title_zh: 步坦开局（蓝方）
map_plan: layout-bl      # → runtime/map-plans/<id>.yaml（缺省 = 出厂模板）
strategy: tank_push      # → runtime/strategies/<id>.yaml（缺省 = 内置默认）
plan: bio-tank-v4        # → runtime/plans/<id>.yaml（缺省 = 不自动入队）
spawn: bl                # 可选：地图规划里的出生点布局（缺省 = 排序第一个，即现状）
```

**只读、文件即真相**：没有 REST 写面 —— loadout 是人（或 agent 直接往磁盘写不了，
它不在工作区虚拟目录里）管的装配预设，与 plans/map-plans 的"锁定预设"同一姿态。
引用存在性在 `session/start?loadout=` 时校验（400 带清单），不在读取时 ——
store 启动时三个目标 store 还没到齐。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml

#: loadout 文件允许的键（拼错 = 读取报错，不静默忽略）
LOADOUT_KEYS = frozenset({"id", "title_zh", "map_plan", "strategy", "plan", "spawn"})
#: spawn 白名单：地图规划的出生点键（tpl.spawns）
SPAWN_KEYS = frozenset({"bl", "tr"})


class LoadoutStore:
    """`{dir}/{id}.yaml` 一份清单一个文件；dir=None = 纯内存（测试，恒空）。"""

    def __init__(self, dir: Path | None) -> None:  # noqa: A002
        self._dir = dir
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if dir is not None:
            dir.mkdir(parents=True, exist_ok=True)
            for p in sorted(dir.glob("*.yaml")):
                item = self._read(p)
                if item is not None:
                    self._items[item["id"]] = item

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(v) for _, v in sorted(self._items.items())]

    def get(self, lid: str) -> dict | None:
        with self._lock:
            item = self._items.get(lid)
            return dict(item) if item else None

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._items)

    def _read(self, path: Path) -> dict | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None    # 坏文件跳过（不拖垮整个 store）；id 以文件名为准
        if not isinstance(raw, dict):
            return None
        unknown = sorted(set(raw) - LOADOUT_KEYS)
        if unknown:
            raise ValueError(
                f"loadout {path.stem!r} 有未知键 {unknown}（只允许 {sorted(LOADOUT_KEYS)}）")
        spawn = raw.get("spawn")
        if spawn is not None and spawn not in SPAWN_KEYS:
            raise ValueError(f"loadout {path.stem!r}: spawn 只能是 {sorted(SPAWN_KEYS)}，当前 {spawn!r}")
        return {
            "id": path.stem,
            "title_zh": str(raw.get("title_zh") or path.stem),
            "map_plan": raw.get("map_plan") or None,
            "strategy": raw.get("strategy") or None,
            "plan": raw.get("plan") or None,
            "spawn": spawn,
            "updated_at": path.stat().st_mtime if path.exists() else time.time(),
        }
