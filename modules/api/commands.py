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
    """一条生产队列项。字段名与契约 `frame/production.items[]` 对齐，前端可原样回传。"""

    op: str
    type: str | None = None
    count: int = Field(default=1, ge=1)
    placement: PlacementIn | None = None
    task: str | None = None

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
        return QueueItem(
            op=op, type=self.type, count=self.count,
            placement=self.placement.to_spec() if self.placement else None,
            task=task,
        )


class QueueCommand(BaseModel):
    """队列工具 op（S11：轻量，不走 validate/compile；执行时按 constraint 门控）。"""

    #: R8 的落点：UI 用当前帧 seq 填，agent 用 ObservationPacket 的 seq 填
    based_on_seq: int
    name: str = "main"
    items: list[QueueItemIn] = Field(default_factory=list)
    #: remove 用；下标是前端在同一帧看到的位置
    index: int | None = None
    #: reorder 用；必须是 0..n-1 的一个排列
    order: list[int] | None = None

    def to_items(self) -> list[QueueItem]:
        return [i.to_item() for i in self.items]


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
