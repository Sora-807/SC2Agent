"""api.frame_source：帧源查询的共享实现（D2 拍板：api 内独立模块）。

一个「帧源」= 一段可按游戏时间检索的信封序列，四查询：info / statics /
latest_at / between。三个实现（JsonlSource、OfflineSession、LiveSession）
**同形不同壳**——存储形态（录制流=排序后的全量列表；会话=动态缓冲+静态缓冲）
与并发（live 要持锁）各不相同，所以这里给**纯函数 + 各自薄壳**，不给基类：
三处没有共享状态，基类只会把「同形」做成「同壳」，还得跟锁语义搏斗。

FrameSource Protocol 只当类型标注用（「任何帧源」的结构契约）。前端同款接口
叫 FrameSource（plan-frontend.md §4），这里与它对齐命名。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from view.schema import STATIC_TOPICS

#: 浮点游标容差：`<= game_time` 与 `(after, until]` 的边界都用它兜浮点噪声
EPS = 1e-9


@dataclass(slots=True)
class SourceInfo:
    id: str
    label: str
    kind: str            # replay | live
    envelopes: int
    from_time: float
    to_time: float
    topics: list[str]
    #: 快照锚点（ADR-0024 §6）：时间线上可跳的点。来自录制时的 index.json
    snapshots: list[float]


class FrameSource(Protocol):
    """帧源的结构契约（JsonlSource / OfflineSession / LiveSession 都满足）。"""

    id: str
    label: str

    def info(self) -> SourceInfo: ...
    def statics(self) -> list[dict]: ...
    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]: ...
    def between(self, after: float, until: float, topics: set[str] | None = None) -> list[dict]: ...


def info_of(source_id: str, label: str, kind: str, frames: list[dict],
            snapshots: list[float] | None = None) -> SourceInfo:
    """帧序列 → 清单条目（空流给 0.0 边界，别在 min() 上炸）。"""
    times = [f["game_time"] for f in frames] or [0.0]
    return SourceInfo(
        id=source_id, label=label, kind=kind,
        envelopes=len(frames), from_time=min(times), to_time=max(times),
        topics=sorted({f["topic"] for f in frames}), snapshots=list(snapshots or []),
    )


def first_statics(frames: list[dict]) -> list[dict]:
    """每个静态面取第一条（录制流没有独立静态缓冲——从全量帧里翻）。"""
    out: list[dict] = []
    for topic in STATIC_TOPICS:
        first = next((f for f in frames if f["topic"] == topic), None)
        if first is not None:
            out.append(first)
    return out


def statics_only(buf: list[dict]) -> list[dict]:
    """会话静态缓冲里只挑静态面（缓冲还夹着初始 frame/session——它是动态的，
    状态会变，走 latest_at 的兜底路径下发，不混进静态面）。"""
    return [f for f in buf if f["topic"] in STATIC_TOPICS]


def latest_at(frames: list[dict], backfill: list[dict], game_time: float,
              topics: set[str] | None = None) -> list[dict]:
    """每个 topic 取 `<= game_time` 的最后一帧；backfill 兜底（静态面即使晚于
    游标也给——它任何游标下都有效）。

    保持**流的顺序**（dict 记插入序）：同一 tick 里多个 topic 共享 GameState.seq，
    按 seq 排会得到任意顺序，而静态面必须先到（前端要先拿到地图与目录）。
    backfill 由调用方给：录制流传 first_statics(frames)，会话传静态缓冲。
    """
    chosen: dict[str, dict] = {}
    for f in frames:
        if topics is not None and f["topic"] not in topics:
            continue
        if f["game_time"] <= game_time + EPS:
            chosen[f["topic"]] = f
    for f in backfill:
        if topics is not None and f["topic"] not in topics:
            continue
        chosen.setdefault(f["topic"], f)
    return list(chosen.values())


def between(frames: list[dict], after: float, until: float,
            topics: set[str] | None = None) -> list[dict]:
    """`(after, until]` 区间内的帧（WS 按节拍推送用）。"""
    return [
        f for f in frames
        if after + EPS < f["game_time"] <= until + EPS
        and (topics is None or f["topic"] in topics)
    ]
