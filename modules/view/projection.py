"""view.projection：把 live 生产队列翻成 planner 的 op 序列。

**这解决的是一个真缺口**：`Planner.project` 吃 `ProductionModuleInstance`（authoring 面），
而 `ProductionRuntime` 执行 `QueueItem`（运行面），两者没有互转 —— 所以在这之前
"当前队列的实时投影"根本产不出来，契约里 `source.kind="live_queue"` 是填不出真值的，
夹具只能诚实地标 `draft`（"参考计划"）。

翻译规则一对一，且**不猜**：
- `build` / `train` → `Build` / `Train`，`count>1` 展开成多条（planner 一条一件）；
- `assign_workers` → `AssignWorkers`（**目标值**语义，与 ADR-0030 D2 一致 ——
  planner 侧已同步改成目标值，否则投影与真实行为不一致）；
- `research` → `Research`（planner 支持；运行时还不支持，这是投影**领先于**运行时的一处，
  会在预览里显示"投影里能跑、真机会被 dropped"）；
- `cancel` → planner 没有对应 op → **跳过并留原因**（不静默）。

放置（placement）不进投影：planner 只数建筑数不放置（position 归 live runtime）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game.catalog import Catalog
from game.production import QueueItem, QueueOp
from game.state import GameState

from planner.build_order import AssignWorkers, Build, Op, Research, Train


@dataclass(slots=True)
class QueueOps:
    """翻译结果 + 被跳过的项（带原因）。"""

    ops: list[Op] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (op 名, 原因)


def queue_to_ops(items: list[QueueItem], catalog: Catalog | None = None) -> QueueOps:
    """`QueueItem` 列表 → planner op 序列。未知/不可投影的项进 `skipped`，不静默丢。"""
    out = QueueOps()
    for it in items:
        op = it.op if isinstance(it.op, QueueOp) else QueueOp(str(it.op))
        count = max(1, int(it.count))
        if op is QueueOp.BUILD:
            if not it.type:
                out.skipped.append(("build", "缺 type"))
                continue
            if catalog is not None and catalog.by_stable_id(it.type) is None:
                out.skipped.append(("build", f"catalog 没登记 {it.type}"))
                continue
            out.ops.extend(Build(it.type, uid=it.uid) for _ in range(count))
        elif op is QueueOp.TRAIN:
            if not it.type:
                out.skipped.append(("train", "缺 type"))
                continue
            if catalog is not None and catalog.by_stable_id(it.type) is None:
                out.skipped.append(("train", f"catalog 没登记 {it.type}"))
                continue
            out.ops.extend(Train(it.type, uid=it.uid) for _ in range(count))
        elif op is QueueOp.RESEARCH:
            if not it.type:
                out.skipped.append(("research", "缺 type"))
                continue
            out.ops.append(Research(it.type, uid=it.uid))
        elif op is QueueOp.ASSIGN_WORKERS:
            task = it.task.value if hasattr(it.task, "value") else it.task
            if task is None:
                out.skipped.append(("assign_workers", "缺 task"))
                continue
            # 目标值语义（ADR-0030 D2）：count = 维持几个
            out.ops.append(AssignWorkers(str(task), count))
        elif op is QueueOp.CANCEL:
            out.skipped.append(("cancel", "planner 没有对应 op（取消不进投影）"))
        else:
            out.skipped.append((str(op), "未知 QueueOp"))
    return out


def project_queue(planner, gs: GameState, items: list[QueueItem], *, until: float,
                  catalog: Catalog | None = None):
    """直接投影一条 live 队列。返回 `(curve, QueueOps)` —— 跳过项要能传给 UI。"""
    translated = queue_to_ops(items, catalog)
    curve = planner.project(gs, list(translated.ops), until)
    return curve, translated
