"""production.ledger：队列项生命周期账本 + 阻塞记录 + 读模型（N3 从 runtime 回吐）。

**为什么是 Mixin 而不是独立对象**（D1 拍板 2026-08-25，与 flights 同一结论）：
这套账本与 runtime 的队列/征用/帧账本深度共享（_queues/_trainings/_build_flights/
_frame_*），抽协作对象要把共享状态设计成回调接口——行为最关键的文件上做这种手术，
风险大于收益。Mixin 搬运 = 代码原样、行为零变；纯化留未来（测试已就位）。

宿主契约（ProductionRuntime 提供）：
`_catalog` `_queues` `_trainings` `_build_flights` `_frame_min/_frame_gas/_frame_supply`
`_frame_ready_types` `_ever_built` `blocked` `stalls` `dropped` `_block_reason`。
"""
from __future__ import annotations

from game import GameState, Queue, QueueItem, QueueOp, WorkerTask
from game.production import PlacementExact, PlacementInRegion
from constraint.semantics import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_SKIPPED,
)

# 队首阻塞多久算失速 → 记一条 stalls 告警（游戏秒）。
# 纯诊断阈值：只影响是否报告，不影响任何执行决策（攒 150 矿约 15-20s，故取 30s 不误报）。
STALL_WARN_SECS = 30.0


def _placement_dict(p) -> dict | None:
    """PlacementSpec → 帧里的判别联合（前端按 kind 分支渲染）。"""
    if p is None:
        return None
    if isinstance(p, PlacementExact):
        return {"kind": "exact", "mark": p.mark}
    if isinstance(p, PlacementInRegion):
        return {"kind": "in_region", "region": p.region, "index": p.index}
    raise TypeError(f"未知 PlacementSpec {type(p).__name__}（不静默：新增放置形态要同步契约）")


class LedgerMixin:
    """ADR-0032 账本（状态迁移/完成扫账）+ 阻塞记录（不静默）+ snapshot 读模型。"""

    # ---- 帧资源账本（P3：跨队列共享，所有 emit 点入账）----

    def _charge(self, stable_id: str | None) -> None:
        """在每个真正发出命令的点入账（build / train / 挂件 / 气矿 / 重试重发）。"""
        entry = self._catalog.by_stable_id(stable_id) if stable_id else None
        if entry is None:
            return
        self._frame_min += entry.cost.minerals
        self._frame_gas += entry.cost.vespene
        self._frame_supply += entry.cost.supply

    # ---- 阻塞记录（不静默：门控失败必须留下原因）----

    def _block(self, reason: str) -> str:
        """记下本次门控失败原因并返回 "blocked"（原因由 _note_block 落到 self.blocked）。"""
        self._block_reason = reason
        return "blocked"

    @staticmethod
    def _why(res, fallback: str) -> str:
        """ConstraintResult.reasons → 单行原因文本。"""
        return "；".join(res.reasons) if getattr(res, "reasons", ()) else fallback

    def _note_block(self, q_name: str, head: QueueItem, gs: GameState) -> None:
        """记录/更新队首阻塞；同一队首持续阻塞超 STALL_WARN_SECS 记一条 stalls 告警。"""
        rec = self.blocked.get(q_name)
        if rec is None or rec["item"] is not head:
            self.blocked[q_name] = {
                "item": head, "reason": self._block_reason,
                "since": gs.game_time, "frames": 1, "warned": False,
            }
            return
        rec["reason"] = self._block_reason  # 原因会变（先缺矿、后缺工兵）：留最新
        rec["frames"] += 1
        waited = gs.game_time - rec["since"]
        if not rec["warned"] and waited >= STALL_WARN_SECS:
            rec["warned"] = True
            self.stalls.append((
                head,
                f"队列 {q_name!r} uid={head.uid} 队首阻塞 {waited:.0f}s"
                f"（{rec['frames']} 帧）：{rec['reason']}",
            ))

    # ---- 状态迁移（ADR-0032 账本）----

    def _mark_skip(self, item: QueueItem, verdict: Verdict, gs: GameState) -> None:
        """执行期失败：留在队列里标 skipped + 闭集 reason（继续下一项，不级联）。"""
        item.status = STATUS_SKIPPED
        item.reason = verdict.reason
        if item.completed_at is None:
            item.completed_at = gs.game_time

    def _finish_item(self, item: QueueItem, gs: GameState) -> None:
        item.status = STATUS_COMPLETED
        if item.completed_at is None:
            item.completed_at = gs.game_time

    def _mark_started(self, item: QueueItem, gs: GameState) -> None:
        if item.status == STATUS_PENDING:
            item.status = STATUS_IN_PROGRESS
            item.started_at = gs.game_time

    def _sweep_completions(self, q: Queue, gs: GameState) -> None:
        """完成扫账：count 归零的 in_progress 项，flight/训练账本里都没它的份 → completed。

        BUILD 看 `_build_flights` 的 **uid**（与 TRAIN 同形态；对象 id 在队列重提
        往返时会换对象）、TRAIN 看 `_trainings`（uid）——两个账本的淘汰逻辑
        （实体完工 / 订单结束超时）就是完成信号。

        I26 残留（真机 rec-20260825-093336）：flight 缺席 ≠ 实体完工 —— 重提队列
        （submit_queue）会取消在途 flight，而往返项带着 in_progress 回队；假完成
        把在建前置从 ExecView 三处（ready/inflight/queued）抹掉，下游级联 skip
        （q02 depot → q04 兵营连锁整局不出兵）。BUILD 项收尾前与场上实体对账：
        有完工同型 → completed；没有 → 保留 in_progress（在建中 = 等盖完，期间
        项仍在 queued_types、下游走 pending 不误 skip；命令真丢 = stall 警报 +
        agent 重提收口 —— skip 是终态，不拿猜测冒险）。
        """
        live_train_uids = {t.get("uid") for t in self._trainings}
        flight_uids = {f.get("uid") for f in self._build_flights.get(q.name, ())}
        for it in q.items:
            if it.status != STATUS_IN_PROGRESS or it.count > 0:
                continue
            if it.op is QueueOp.TRAIN:
                if it.uid is None or it.uid in live_train_uids:
                    continue
                self._finish_item(it, gs)
            elif it.op is QueueOp.BUILD and it.uid not in flight_uids:
                if it.type in self._frame_ready_types:
                    self._finish_item(it, gs)
                # else：场上无完工同型实体（本 type 级对账，flight 已不在无法按
                # 位置归属）—— 不假完成，见 docstring

    # ---- 读模型（B1）----

    def _producer_ever_ready(self, rec: dict) -> bool | None:
        """阻塞项的产出建筑「曾建成过吗」。只对 train 类阻塞有意义（E 批）。"""
        return self._item_ever_ready(rec.get("item"))

    def _item_ever_ready(self, item: QueueItem | None) -> bool | None:
        """skipped/阻塞项相关建筑「曾建成过吗」（累计语义，**不含**当前是否在场——
        I27：当前在场由警报层与帧对账，这里只回答历史）。

        train 看产出建筑；build 看前置里有没有曾建成的（科技建筑被打掉的情形③）。
        无关类型（两者都没有）→ None。payload 键名 `producer_ever_ready` 是契约，不改。
        """
        if item is None or item.type is None:
            return None
        entry = self._catalog.by_stable_id(item.type)
        if entry is None:
            return None
        ever: set[str] = set()
        if item.op is QueueOp.TRAIN and entry.produced_by:
            ever.add(entry.produced_by)
        elif item.op is QueueOp.BUILD:
            ever.update(entry.prerequisites)
        if not ever:
            return None
        return any(sid in self._ever_built for sid in ever)

    def snapshot(self) -> dict:
        """生产运行时的显式只读快照（供 view / agent / 复盘录制）。

        账本语义（ADR-0032）：items 含全部历史（pending/in_progress/completed/skipped），
        uid 随项走；blocked 指向本帧 pending 等待的那一项。返回**普通 dict**
        （production 不认识 view，架构测试锁死方向）；键名由 view.adapt 显式映射。
        """
        queues: list[dict] = []
        for q in self._queues.values():
            rec = self.blocked.get(q.name)
            blocked_item = rec["item"] if rec else None
            items: list[dict] = []
            n_pending = 0
            for index, it in enumerate(q.items):
                is_blocked = it is blocked_item
                if it.status == STATUS_PENDING:
                    n_pending += 1
                items.append({
                    "uid": it.uid,
                    "index": index,
                    "op": it.op.value if isinstance(it.op, QueueOp) else str(it.op),
                    "stable_id": it.type,
                    "count": it.count,
                    "placement": _placement_dict(it.placement),
                    "task": it.task.value if isinstance(it.task, WorkerTask) else it.task,
                    "status": it.status,
                    "reason": it.reason,
                    # blocked 项额外给等待原因（前端/警报高亮"卡在哪"）
                    "block_reason": rec["reason"] if is_blocked else None,
                    "producer_ever_ready": (self._item_ever_ready(it)
                                            if it.status == STATUS_SKIPPED else None),
                })
            if n_pending == 0:
                head_status = "空"
            elif blocked_item is not None:
                head_status = "阻塞"
            else:
                head_status = "可执行"
            queues.append({
                "name": q.name,
                "head_status": head_status,
                "blocked": None if rec is None else {
                    "reason": rec["reason"],
                    "since": rec["since"],
                    "frames": rec["frames"],
                    "warned": rec["warned"],
                    "uid": blocked_item.uid if blocked_item is not None else None,
                    # E 批：仅对「缺产出建筑」类阻塞有意义 —— True=曾建成（被摧毁），
                    # False=从没建过（建造被卡/掉单）；None=其他原因
                    "producer_ever_ready": self._producer_ever_ready(rec),
                },
                "items": items,
            })

        in_flight: list[dict] = []
        for q_name, flights in self._build_flights.items():
            for f in flights:
                in_flight.append({
                    "queue": q_name,
                    "stable_id": f["type"],
                    "uid": f.get("uid"),
                    # 来源队列序号（B3）：emit 时该项在队列里的下标 —— 账本化后下标稳定
                    "from_index": f.get("from_index"),
                    "builder_tag": f.get("builder"),
                    "expect_pos": f.get("expect_pos"),
                    "radius": f.get("radius", 0.0),
                    "frames_waited": f.get("frames", 0),
                    "retries": f.get("retries", 0),
                    # 已尝试过的槽位名：摆放调试叠加层要画"试过哪几个位置"（F5）
                    "attempted_slots": sorted(f.get("attempted") or ()),
                })

        return {
            "queues": queues,
            "in_flight": in_flight,
            # 在训条目（G3，rev 17）：开始时刻我们自己记（SC2 订单不带进度）
            "training": [{"stable_id": t["type"], "producer_tag": t["producer_tag"],
                          "started_at": t["started_at"], "uid": t.get("uid")}
                         for t in self._trainings],
            # dropped 目前不带时间戳（QueueItem 被丢时没有记 game_time）→ 帧里 at=None，
            # UI 显示"未知"而不是编一个时间（不静默）。
            "dropped": [
                {"op": it.op.value if isinstance(it.op, QueueOp) else str(it.op),
                 "stable_id": it.type, "reason": reason, "at": None}
                for it, reason in self.dropped
            ],
            "stalls": [
                {"stable_id": it.type, "text": text} for it, text in self.stalls
            ],
        }
