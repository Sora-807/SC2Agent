"""game.production：生产队列 schema（docs/contract/P0-影响边界.md「生产模块安排」）。

生产 = 运行时线性 build/train/research/assign_workers 队列，按序消费。
QueueItem 是队列项；placement 用抽象标记（PlacementExact/PlacementInRegion），
由生产运行时的 queue 处理层转成具体 TL+BR 坐标（ADR-0027）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class QueueOp(str, Enum):
    """生产队列项的操作类型（闭集：生产运行时 drain 时按值分派）。"""

    BUILD = "build"                    # 建造建筑（如 兵营；需 placement）
    TRAIN = "train"                    # 训练单位（如 枪兵）
    RESEARCH = "research"              # 研究科技（如 兴奋剂）
    CANCEL = "cancel"                  # 取消建造/训练
    ASSIGN_WORKERS = "assign_workers"  # SCV 矿/气/空闲自动分配（饱和上限 + 溢出转 idle）


class WorkerTask(str, Enum):
    """assign_workers 的任务类型（闭集 3 值；WorkerAllocator 按它分派 SCV）。"""

    MINERAL = "mineral"   # 派去采晶体矿
    GAS = "gas"           # 派去采高能瓦斯
    IDLE = "idle"         # 从矿/气解放（给建造/修理用）


@dataclass(frozen=True, slots=True)
class PlacementExact:
    """精确标记：查 PosMark/BuildSlot 名 → 精确坐标。"""

    mark: str  # PosMark 或 BuildSlot 名（tactical_map.RegionLayer 中登记）


@dataclass(frozen=True, slots=True)
class PlacementInRegion:
    """区域内找位：查区域名 → 按区域 build_slots 顺序。
    index=None（默认）= 自动找第一个可用 slot（跳过被占的）。
    index=N = 指定用 slot[N]（不管占没占；被占就失败/等待）。"""

    region: str  # 区域名（tactical_map.RegionLayer 中登记，ADR-0029）
    index: int | None = None  # None=自动找第一个可用 slot；N=指定 slot[N]（被占即失败/等待）


PlacementSpec = PlacementExact | PlacementInRegion


@dataclass(slots=True)
class QueueItem:
    """生产队列项（生产运行时 drain 这个队列发 build/train/research/assign_workers op）。

    执行账本字段（ADR-0032）：uid 由 runtime 入队时分配（per-queue 递增 q01…，
    重排/插入不变 —— before_uid 引用的稳定锚点）；status 四值闭集
    （production.semantics.QUEUE_STATUSES），已执行项**保留在队列里**不再摘除；
    reason 只在 skipped 时有值（SKIP_REASONS 闭集 key）。规划文件/草稿里的项
    这些字段全是默认值（pending/None）。
    """

    op: QueueOp  # 操作类型（闭集枚举；构造时可传字符串，__post_init__ 归一化）
    type: str | None = None  # 目标类型稳定 ID（build/train/research 用，如 "terran/barracks"）
    count: int = 1  # 数量（≥1；train 5 个则 count=5）
    placement: PlacementSpec | None = None  # 建造位置抽象标记；queue 处理层转 TL+BR 坐标（ADR-0027）
    task: WorkerTask | None = None  # assign_workers 用：mineral/gas/idle（闭集枚举）
    uid: str | None = None  # runtime 分配的稳定 ID（未入队 = None）
    status: str = "pending"  # pending / in_progress / completed / skipped
    reason: str | None = None  # skipped 时的闭集原因 key（prereq_missing/placement_collision）
    started_at: float | None = None  # 首次发出命令的游戏时刻（runtime 记）
    completed_at: float | None = None  # 全部执行完成的游戏时刻（runtime 记）
    # 注：条件项（原 when: "supply>=16"）无消费方，已删（T3/D5：死字段是虚假承诺）。
    # 生产 authoring 统一轮再以结构化 AST 条件回归（与 flow 的 when 同一套词表/求值器）。

    def __post_init__(self) -> None:
        """归一化：允许 YAML/JSON/测试以字符串构造，统一收成枚举成员。"""
        if isinstance(self.op, str) and not isinstance(self.op, QueueOp):
            self.op = QueueOp(self.op)
        if isinstance(self.task, str) and not isinstance(self.task, WorkerTask):
            self.task = WorkerTask(self.task)


@dataclass(slots=True)
class Queue:
    """命名生产队列（agent 工具操作：submit_queue/append/prepend/clear/remove/reorder）。"""

    name: str  # 队列唯一名（agent 引用）
    items: list[QueueItem] = field(default_factory=list)  # 有序队列项（队首先消费）
