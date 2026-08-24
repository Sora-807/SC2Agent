"""constraint.semantics：队列项执行语义的**单点权威**（PLAN-V2 批 1，ADR-0032）。

一套执行语义，三个消费方：live runtime（_drain 门控）、planner 仿真（批 3 接入）、
simulate 健康检查（批 3 接入）。语义改一处，三处同步 —— 不再各写一套判据。

classify(item, catalog, view) → ready | pending(中文原因) | skip(闭集 reason key)：

- pending = 等待（矿/气/人口未齐、前置在途或在队列、产出建筑在途/在队列、
  训练槽满 production_capacity）—— 顺序执行的常态，队首等待时后续项不动（保序）。
  production_capacity 归 pending 而非 skip（槽满是瞬态，"等一等就满足"）；
  超 60s 由警报层升级 warn（alerts.py 的 capacity 规则）。
- skip = 执行期失败（前置不在场、不在途、也不在队列 = prereq_missing；
  放置无可用位 = placement_collision）—— 标记后**继续下一项**，不冻结整队。
- 语法/作者错误（catalog 不认、标记不存在、YAML 坏）**不经 classify**：
  runtime 直接 dropped 摘除（D6 分工）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from game.catalog import Catalog
from game.production import QueueItem, QueueOp, WorkerTask


class VerdictKind(str, Enum):
    READY = "ready"
    PENDING = "pending"
    SKIP = "skip"


#: skip 原因闭集（SKIP_REASON_ZH 覆盖层；同概念必须同形态）
SKIP_PREREQ_MISSING = "prereq_missing"
SKIP_PLACEMENT_COLLISION = "placement_collision"
SKIP_REASONS = (SKIP_PREREQ_MISSING, SKIP_PLACEMENT_COLLISION)

SKIP_REASON_ZH = {
    SKIP_PREREQ_MISSING: "前置缺失（不在场、不在途、也不在队列）",
    SKIP_PLACEMENT_COLLISION: "放置失败（无可用槽位/候选耗尽）",
}

#: 队列项状态四值闭集（D8：live 与仿真同枚举；STATUS_ZH 覆盖层）
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
QUEUE_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_SKIPPED)

STATUS_ZH = {
    STATUS_PENDING: "等待中",
    STATUS_IN_PROGRESS: "执行中",
    STATUS_COMPLETED: "已完成",
    STATUS_SKIPPED: "已跳过",
}

#: capacity 等待的警报升级阈值（游戏秒）：pending 里只有这一类会升级成 warn
CAPACITY_WARN_SECS = 60.0


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: VerdictKind
    reason: str | None = None  # pending：中文等待原因；skip：SKIP_REASONS 之一
    detail: str | None = None  # skip 时缺的具体前置/放置上下文（警报建议用）

    @property
    def is_skip(self) -> bool:
        return self.kind is VerdictKind.SKIP


@dataclass(frozen=True, slots=True)
class ExecView:
    """classify 的世界视图 —— live runtime 与 planner 仿真各自装配（批 3 接入仿真）。

    `free_producers`：产出建筑类型集合中「至少有一座就绪且有空闲训练槽」的类型。
    `queued_types`：同会话所有队列里排在当前项之前、尚未执行的**建造**类型
    （跨队列也认 —— B 队列的训练可以等 A 队列正在排的建造）。
    """

    minerals: float
    vespene: float
    supply_used: float
    supply_cap: float
    ready_types: frozenset[str]      # 场上就绪建筑类型（stable_id）
    inflight_types: frozenset[str]   # 在途建造/挂件类型
    queued_types: frozenset[str]     # 队列中未执行的建造类型
    free_producers: frozenset[str]   # 有空闲训练槽的产出建筑类型


def _zh(catalog: Catalog, stable_id: str | None) -> str:
    if not stable_id:
        return "?"
    e = catalog.by_stable_id(stable_id)
    return e.display_name_zh if e is not None else stable_id


def _prereq_verdict(catalog: Catalog, entry, view: ExecView) -> Verdict | None:
    """前置三态：在场=过 / 在途或队列=pending / 哪都没有=skip。返回 None = 全过。"""
    missing: list[str] = []
    for p in entry.prerequisites:
        if p in view.ready_types:
            continue
        if p in view.inflight_types or p in view.queued_types:
            return Verdict(VerdictKind.PENDING,
                           f"等前置 {_zh(catalog, p)}（在途/在队列更早处）")
        missing.append(p)
    if missing:
        names = "、".join(_zh(catalog, p) for p in missing)
        return Verdict(VerdictKind.SKIP, SKIP_PREREQ_MISSING, f"缺 {names}")
    return None


def classify(item: QueueItem, catalog: Catalog, view: ExecView) -> Verdict:
    """单个队列项在当前世界视图下的三态判定（纯函数，表驱动测试锁语义）。"""
    if item.op is QueueOp.ASSIGN_WORKERS:
        return Verdict(VerdictKind.READY)  # 无资源门控（P0：立即发）
    if item.op not in (QueueOp.BUILD, QueueOp.TRAIN):
        # 防御：未知 op 由 runtime 在 classify 之前 dropped（D6）；真到这里只能失败
        return Verdict(VerdictKind.SKIP, SKIP_PREREQ_MISSING, f"未知 op {item.op!r}")
    if item.type is None:
        # 作者错误由 caller 在 classify 之前 dropped（D6）；这里是防御性兜底
        return Verdict(VerdictKind.SKIP, SKIP_PREREQ_MISSING, "缺 type")
    entry = catalog.by_stable_id(item.type)
    if entry is None:
        return Verdict(VerdictKind.SKIP, SKIP_PREREQ_MISSING,
                       f"catalog 不认 {item.type!r}")
    # 矿/气/人口三要素：缺 = 等待（攒）——不是失败
    if view.minerals < entry.cost.minerals:
        return Verdict(VerdictKind.PENDING,
                       f"晶体矿不足（余 {view.minerals:.0f} < {entry.cost.minerals}）")
    if view.vespene < entry.cost.vespene:
        return Verdict(VerdictKind.PENDING,
                       f"高能瓦斯不足（余 {view.vespene:.0f} < {entry.cost.vespene}）")
    if item.op is QueueOp.TRAIN and entry.cost.supply:
        left = view.supply_cap - view.supply_used
        if entry.cost.supply > left:
            return Verdict(VerdictKind.PENDING,
                           f"供给不足（余 {left:.0f} < {entry.cost.supply}）")
    v = _prereq_verdict(catalog, entry, view)
    if v is not None:
        return v
    if item.op is QueueOp.TRAIN and entry.produced_by:
        pb = entry.produced_by
        if pb in view.free_producers:
            return Verdict(VerdictKind.READY)
        if pb in view.ready_types:
            # 产出建筑就绪但训练槽全忙 —— 瞬态，等待（超 60s 警报层升级 warn）
            return Verdict(VerdictKind.PENDING,
                           f"训练槽满（production_capacity：{_zh(catalog, pb)} 就绪但订单已满）")
        if pb in view.inflight_types or pb in view.queued_types:
            return Verdict(VerdictKind.PENDING,
                           f"等产出建筑 {_zh(catalog, pb)}（在途/在队列更早处）")
        return Verdict(VerdictKind.SKIP, SKIP_PREREQ_MISSING,
                       f"缺产出建筑 {_zh(catalog, pb)}")
    return Verdict(VerdictKind.READY)


def is_capacity_wait(reason: str | None) -> bool:
    """blocked 原因是否属于 production_capacity 等待（警报层 60s 升级用的判据）。"""
    return bool(reason) and "production_capacity" in reason
