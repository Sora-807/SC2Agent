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


def queue_to_ops(items: list[QueueItem], catalog: Catalog | None = None,
                 slot_pool=None) -> QueueOps:
    """`QueueItem` 列表 → planner op 序列。未知/不可投影的项进 `skipped`，不静默丢。

    `slot_pool`（放置近似模型，批 2 漏账补）：给了才做 exact 标记校验 ——
    标记不在图层 = 作者错误，摘除进 skipped（**仿真继续**，D6 分工）；
    命名空间引用（"规划id/名"）只对池来源同 id 的剥前缀，指向其他规划的
    引用近似不建模（该按自动找位处理，与 live 合并图层的语义差距如实存在）。
    """
    out = QueueOps()

    def _mark_of(it) -> str | None:
        """exact 引用 → 槽位名（池来源同 id 的命名空间引用剥前缀）。"""
        from game.production import PlacementExact
        p = it.placement
        if not isinstance(p, PlacementExact) or not isinstance(p.mark, str):
            return None
        mark = p.mark
        if "/" in mark and slot_pool is not None and slot_pool.source_id:
            prefix, _, bare = mark.partition("/")
            if prefix == slot_pool.source_id:
                return bare
            return None   # 指向别的规划：近似不建模（按自动找位）
        return mark

    for it in items:
        op = it.op if isinstance(it.op, QueueOp) else QueueOp(str(it.op))
        # ADR-0032 账本化回归修（2026-08-25 用户报「泳道图每帧整体后移、完全
        # 对不上」）：完成项永久留队后这里不过滤 = **整条历史每帧重仿真一遍**
        # ——已建成的 SCV/depot/兵营全变成从红线起新建的幻影条 + 幻影开销把
        # 后续项越推越晚（录像 rec-20260825-104557 实锤：q01-q07 全 completed，
        # 投影每帧仍画 9 条 started@T 的条，T 每帧 +3s 整体右移）。
        # - completed/skipped：终态历史不进仿真（skipped 是执行期失败，重建归
        #   agent 重提，不是投影的活）；
        # - in_progress 且 count<=0：全部已发射 —— 在途实体由 derive_from 按真实
        #   build_progress 建模（前端另有世界部分条），重仿真=双份。
        status = getattr(it, "status", "pending")
        if status in ("completed", "skipped"):
            continue
        count = max(0, int(it.count))
        if count <= 0:
            continue
        if op is QueueOp.BUILD:
            if not it.type:
                out.skipped.append(("build", "缺 type"))
                continue
            entry = catalog.by_stable_id(it.type) if catalog is not None else None
            if catalog is not None and entry is None:
                out.skipped.append(("build", f"catalog 没登记 {it.type}"))
                continue
            mark = _mark_of(it)
            if (mark is not None and slot_pool is not None and entry is not None
                    and slot_pool.handles(entry) and mark not in slot_pool.marks()):
                out.skipped.append(("build",
                                    f"placement 标记 {mark!r} 不在图层"
                                    f"（{slot_pool.source_label}）—— 改名或换图层来源"))
                continue
            out.ops.extend(Build(it.type, uid=it.uid, mark=mark) for _ in range(count))
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
            out.ops.append(AssignWorkers(str(task), count, uid=it.uid))
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
