"""game.production：生产队列 schema（docs/P0-影响边界.md「生产模块安排」）。

生产 = 运行时线性 build/train/research/assign_workers 队列，按序消费。
QueueItem 是队列项；placement 用抽象标记（PlacementExact/PlacementInRegion），
由生产运行时的 queue 处理层转成具体坐标。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PlacementExact:
    """精确标记：查 PosMark/BuildSlot 名 → 精确坐标。"""

    mark: str  # PosMark 或 BuildSlot 名


@dataclass(frozen=True, slots=True)
class PlacementInRegion:
    """区域内找位：查 RegionMark → 按 build_slots 顺序。
    index=None（默认）= 自动找第一个可用 slot（跳过被占的）。
    index=N = 指定用 slot[N]（不管占没占；被占就失败/等待）。"""

    region: str  # RegionMark 名
    index: int | None = None


PlacementSpec = PlacementExact | PlacementInRegion


@dataclass(slots=True)
class QueueItem:
    """生产队列项（生产运行时 drain 这个队列发 build/train/research/assign_workers op）。"""

    op: str  # 操作类型：build | train | research | cancel | assign_workers
    type: str | None = None  # 目标类型稳定 ID（build/train/research 用，如 "terran/barracks"）
    count: int = 1  # 数量（≥1；train 5 个则 count=5）
    placement: PlacementSpec | None = None  # 抽象标记（PlacementExact/InRegion/BySequence）；queue 处理层转坐标
    task: str | None = None  # assign_workers 用：mineral | gas | idle
    when: str | None = None  # 可选一次性触发条件（如 "supply>=16" 满足才执行此条）


@dataclass(slots=True)
class Queue:
    """命名生产队列（agent 工具操作：submit_queue/append/prepend/clear/remove/reorder）。"""

    name: str  # 队列唯一名（agent 引用）
    items: list[QueueItem] = field(default_factory=list)  # 有序队列项（队首先消费）
