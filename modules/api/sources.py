"""api.sources：帧源注册表。

一个"帧源"= 一段可按游戏时间检索的信封序列。现在只有 JSONL（夹具与录制），
B3 接上 live 之后再加一个 live 源 —— **前端不需要改**，因为它看到的接口是同一个。

刻意不做的事：不做 seek 索引优化（几百到几万条信封全量载入内存足够；
真要长对局再说，那时该做的是 ADR-0024 的快照 + 事件重放，不是在这里加索引）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from view.jsonl import read_frames
from view.schema import STATIC_TOPICS


@dataclass(slots=True)
class SourceInfo:
    id: str
    label: str
    kind: str            # replay | live
    envelopes: int
    from_time: float
    to_time: float
    topics: list[str]


class JsonlSource:
    """一份 JSONL 帧序列。"""

    def __init__(self, source_id: str, path: Path, label: str | None = None) -> None:
        self.id = source_id
        self.path = path
        self.label = label or source_id
        self.frames: list[dict] = list(read_frames(path))
        if not self.frames:
            raise ValueError(f"{path} 里没有帧")
        self.frames.sort(key=lambda f: (f["game_time"], f["seq"]))

    # ---- 查询 ----

    def info(self) -> SourceInfo:
        times = [f["game_time"] for f in self.frames]
        return SourceInfo(
            id=self.id, label=self.label, kind="replay",
            envelopes=len(self.frames), from_time=min(times), to_time=max(times),
            topics=sorted({f["topic"] for f in self.frames}),
        )

    def statics(self) -> list[dict]:
        """三个静态面各取第一条（每局只发一次）。"""
        out: list[dict] = []
        for topic in STATIC_TOPICS:
            first = next((f for f in self.frames if f["topic"] == topic), None)
            if first is not None:
                out.append(first)
        return out

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        """每个 topic 取 `<= game_time` 的最后一帧。

        这与前端 `JsonlFrameSource` 的语义**逐字一致**（所有 payload 都是该时刻的完整快照），
        所以"服务端 seek"和"客户端 seek"看到的东西必然相同。
        静态面即使晚于游标也给 —— 它任何游标下都有效。
        """
        chosen: dict[str, dict] = {}
        for f in self.frames:
            if topics is not None and f["topic"] not in topics:
                continue
            if f["game_time"] <= game_time + 1e-9:
                chosen[f["topic"]] = f
        for topic in STATIC_TOPICS:
            if topic in chosen:
                continue
            if topics is not None and topic not in topics:
                continue
            first = next((f for f in self.frames if f["topic"] == topic), None)
            if first is not None:
                chosen[topic] = first
        return sorted(chosen.values(), key=lambda f: f["seq"])

    def between(self, after: float, until: float, topics: set[str] | None = None) -> list[dict]:
        """`(after, until]` 区间内的帧（WS 按节拍推送用）。"""
        return [
            f for f in self.frames
            if after + 1e-9 < f["game_time"] <= until + 1e-9
            and (topics is None or f["topic"] in topics)
        ]


class SourceRegistry:
    """扫一个目录里的 `*.jsonl` 作为帧源。懒加载：只有被访问过的才读进内存。"""

    def __init__(self, root: Path, labels: dict[str, str] | None = None) -> None:
        self.root = Path(root)
        self._labels = labels or {}
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
        src = JsonlSource(source_id, path, label=self._labels.get(source_id))
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
