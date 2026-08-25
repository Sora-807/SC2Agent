"""api.sources：帧源注册表。

一个"帧源"= 一段可按游戏时间检索的信封序列。现在只有 JSONL（夹具与录制），
B3 接上 live 之后再加一个 live 源 —— **前端不需要改**，因为它看到的接口是同一个。

刻意不做的事：不做 seek 索引优化（几百到几万条信封全量载入内存足够；
真要长对局再说，那时该做的是 ADR-0024 的快照 + 事件重放，不是在这里加索引）。
"""
from __future__ import annotations

from pathlib import Path

from api.frame_source import (
    SourceInfo,
    between,
    first_statics,
    info_of,
    latest_at,
)
from view.jsonl import read_frames

__all__ = ["JsonlSource", "SourceRegistry", "SourceInfo"]


class JsonlSource:
    """一份 JSONL 帧序列（查询实现共享自 api.frame_source）。"""

    def __init__(self, source_id: str, path: Path, label: str | None = None,
                 snapshots: list[float] | None = None) -> None:
        self.id = source_id
        self.path = path
        self.label = label or source_id
        self.snapshots = list(snapshots or [])
        self.frames: list[dict] = list(read_frames(path))
        if not self.frames:
            raise ValueError(f"{path} 里没有帧")
        self.frames.sort(key=lambda f: (f["game_time"], f["seq"]))

    # ---- 查询 ----

    def info(self) -> SourceInfo:
        return info_of(self.id, self.label, "replay", self.frames, self.snapshots)

    def statics(self) -> list[dict]:
        """三个静态面各取第一条（每局只发一次）。"""
        return first_statics(self.frames)

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        """每个 topic 取 `<= game_time` 的最后一帧。

        这与前端 `JsonlFrameSource` 的语义**逐字一致**（所有 payload 都是该时刻的完整快照），
        所以"服务端 seek"和"客户端 seek"看到的东西必然相同。
        静态面即使晚于游标也给 —— 它任何游标下都有效。
        """
        return latest_at(self.frames, first_statics(self.frames), game_time, topics)

    def between(self, after: float, until: float, topics: set[str] | None = None) -> list[dict]:
        """`(after, until]` 区间内的帧（WS 按节拍推送用）。"""
        return between(self.frames, after, until, topics)


class SourceRegistry:
    """扫一个目录里的 `*.jsonl` 作为帧源。懒加载：只有被访问过的才读进内存。"""

    def __init__(self, root: Path, labels: dict[str, str] | None = None) -> None:
        self.root = Path(root)
        self._labels = labels or {}
        self._snapshots: dict[str, list[float]] = {}
        self._cache: dict[str, JsonlSource] = {}

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.jsonl"))

    def get(self, source_id: str) -> JsonlSource | None:
        if source_id in self._cache:
            return self._cache[source_id]
        path = self.root / f"{source_id}.jsonl"
        if not path.is_file():
            return None
        src = JsonlSource(source_id, path, label=self._labels.get(source_id),
                          snapshots=self._snapshots.get(source_id))
        self._cache[source_id] = src
        return src

    def list(self) -> list[SourceInfo]:
        out: list[SourceInfo] = []
        for sid in self.ids():
            src = self.get(sid)
            if src is not None:
                out.append(src.info())
        return out

    def load_labels_from_index(self) -> None:
        """夹具目录里有 `index.json` 时用它的中文标签（后端产的标签，前端不另起名字）。"""
        index = self.root / "index.json"
        if not index.is_file():
            return
        import json

        try:
            rows = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in rows if isinstance(rows, list) else []:
            key, label = row.get("key"), row.get("label")
            if key and label:
                self._labels[key] = label
            if key and isinstance(row.get("snapshots"), list):
                self._snapshots[key] = [float(x) for x in row["snapshots"]]