"""api.commands：命令写入面的请求模型与解析（B6）。

三条边界：
1. **UI 与 agent 共用同一入口**（前端 U7/P4）：这些模型是 HTTP 层的，两边发的是同一份 JSON。
2. **必带 `based_on_seq`**（R8 / ADR-0009）：过期观察不得作为行动依据。字段设成必填，
   而不是"可选、缺了就跳过检查" —— 后者等于没有这道门。
3. **不静默**：未知 op / 不支持的 op / 非法参数一律 400 + 原因，原因文本取自后端闭集
   （`UNSUPPORTED_QUEUE_OPS` 等），不在这里另编一套。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from game.production import (
    PlacementExact,
    PlacementInRegion,
    QueueItem,
    QueueOp,
    WorkerTask,
)
from production.runtime import UNSUPPORTED_QUEUE_OPS

#: 队列工具 op 的全集（B2 扩到 8 个：insert/replace_head 入列）。
#: OfflineSession / LiveSession / run_session 三处的白名单共用这一份 ——
#: 各自手抄迟早漂移（LiveSession 曾漏过 prepend 的 allowlist 同款问题）。
QUEUE_OPS = frozenset({
    "submit", "append", "prepend", "clear", "remove", "reorder",
    "insert",          # B2：按剩余队列位置插入（0=队首前；只影响未来，越界 400）
    "replace_head",    # B2：原子换队首（remove 未执行队首 + prepend 新项一步完成）
})


class PlacementIn(BaseModel):
    """放置标记：精确槽位 或 区域内找位（ADR-0029）。"""

    kind: Literal["exact", "in_region"]
    mark: str | None = None
    region: str | None = None
    index: int | None = None

    def to_spec(self):
        if self.kind == "exact":
            if not self.mark:
                raise ValueError("placement.kind=exact 需要 mark（BuildSlot 或 PosMark 名）")
            return PlacementExact(mark=self.mark)
        if not self.region:
            raise ValueError("placement.kind=in_region 需要 region")
        return PlacementInRegion(region=self.region, index=self.index)


class QueueItemIn(BaseModel):
    """一条生产队列项。字段名与契约 `frame/production.items[]` 对齐，前端可原样回传。

    `uid`/`status`/`reason`（ADR-0032 账本）：从帧反解回灌时带上可以保住执行状态
    （否则已完成项会被重跑）；新建项不填。status 只认闭集四值。
    """

    op: str
    type: str | None = None
    count: int = Field(default=1, ge=1)
    placement: PlacementIn | None = None
    task: str | None = None
    uid: str | None = None
    status: str | None = None
    reason: str | None = None

    def to_item(self) -> QueueItem:
        try:
            op = QueueOp(self.op)
        except ValueError:
            raise ValueError(
                f"未知队列 op {self.op!r}（合法值 {[e.value for e in QueueOp]}）"
            ) from None
        if op in UNSUPPORTED_QUEUE_OPS:
            # 原因取自后端闭集，不另编文案（前端也是显示这一份）
            raise ValueError(f"{op.value} 暂不支持：{UNSUPPORTED_QUEUE_OPS[op]}")
        task = None
        if self.task is not None:
            try:
                task = WorkerTask(self.task)
            except ValueError:
                raise ValueError(
                    f"未知 worker task {self.task!r}（合法值 {[t.value for t in WorkerTask]}）"
                ) from None
        from constraint.semantics import QUEUE_STATUSES
        status = self.status or "pending"
        if status not in QUEUE_STATUSES:
            raise ValueError(f"未知队列项 status {self.status!r}（合法值 {list(QUEUE_STATUSES)}）")
        return QueueItem(
            op=op, type=self.type, count=self.count,
            placement=self.placement.to_spec() if self.placement else None,
            task=task, uid=self.uid, status=status, reason=self.reason,
        )


class QueueCommand(BaseModel):
    """队列工具 op（S11：轻量，不走 validate/compile；执行时按语义门控）。

    引用一律走 **uid**（ADR-0032）：已执行项保留在队列里，下标会随执行区增长漂移。
    `before_uid`：insert 插到哪项之前（None = 追加到末尾）；`uid`：remove 删哪项；
    `order`：reorder 的目标顺序（当前队列全部 uid 的一个排列）。
    """

    #: R8 的落点：UI 用当前帧 seq 填，agent 用 ObservationPacket 的 seq 填
    based_on_seq: int
    name: str = "main"
    items: list[QueueItemIn] = Field(default_factory=list)
    #: insert 用：插到该 uid 之前（None = 追加）
    before_uid: str | None = None
    #: remove 用：删该 uid
    uid: str | None = None
    #: reorder 用：当前队列全部 uid 的一个排列
    order: list[str] | None = None

    def to_items(self) -> list[QueueItem]:
        return [i.to_item() for i in self.items]


def apply_queue_op(runtime, op: str, name: str, *, items=None, before_uid=None,
                   uid: str | None = None, order=None, seq: int = 0) -> dict:
    """queue op 分发的**单点实现**（批 6 清偿①：OfflineSession 与 run_session 子进程
    共用；LiveSession 只转发 JSON 不落这层）。

    引用一律走 uid（ADR-0032）；校验中文错误就地抛 ValueError（REST 400 / 控制行 error）。
    """
    if op not in QUEUE_OPS:
        raise ValueError(f"未知队列 op {op!r}（{'|'.join(sorted(QUEUE_OPS))}）")
    q = runtime.queue(name)
    if op == "submit":
        runtime.submit_queue(name, items or [])
    elif op == "append":
        runtime.append(name, items or [])
    elif op == "prepend":
        runtime.prepend(name, items or [])
    elif op == "insert":
        runtime.insert(name, before_uid, items or [])
    elif op == "replace_head":
        if not items:
            raise ValueError("replace_head：缺 items（要换上的新队首；清空请用 clear）")
        runtime.replace_head(name, items)
    elif op == "clear":
        runtime.clear(name)
    elif op == "remove":
        if uid is None:
            raise ValueError("remove：缺 uid（要删的队列项）")
        target = runtime.item_by_uid(name, uid)
        if q is None or target is None:
            raise ValueError(f"remove：队列 {name!r} 没有 uid={uid!r} 的项")
        runtime.remove(name, target)
    elif op == "reorder":
        if q is None or order is None:
            raise ValueError(f"reorder：队列 {name!r} 不存在或缺 order")
        uids = [it.uid for it in q.items]
        if sorted(order) != sorted(uids) or len(set(order)) != len(order):
            raise ValueError(f"reorder：order 必须是队列 {name!r} 全部 uid（{uids}）的一个排列")
        by_uid = {it.uid: it for it in q.items}
        runtime.reorder(name, [by_uid[u] for u in order])
    else:  # pragma: no cover —— QUEUE_OPS 闭集守卫
        raise ValueError(f"未知队列 op {op!r}")
    after = runtime.queue(name)
    return {"queue": name, "items": len(after.items) if after else 0, "accepted_seq": seq}


class WorkerCommand(BaseModel):
    """采集配额 = 目标值（维持 N 个，幂等；ADR-0030 D2），不是"再派 N 个"。"""

    based_on_seq: int
    task: Literal["mineral", "gas", "idle"]
    count: int = Field(ge=0)


class CommandResult(BaseModel):
    ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)
    #: 命令被接受时的 seq。前端据此显示"下一 step 生效"的 pending 态
    accepted_seq: int
