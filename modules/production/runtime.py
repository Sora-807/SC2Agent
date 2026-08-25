"""production.runtime：生产运行时 —— drain 命名队列 → 语义门控 → 发 Operation。

每帧 on_game_state(gs) 触发（与 flow engine 并列消费 GameState，都经同一个 port 出 op）。

**执行模型（ADR-0032，PLAN-V2 批 1 重构）**：队列 = 执行账本，已执行项**保留**在
`q.items` 里标 status（pending/in_progress/completed/skipped），不再摘除；
uid（q01… per-queue 递增、重排不变）是 before_uid 引用的稳定锚点。

1. 每项经 `constraint.semantics.classify`（单点权威，planner/健康检查共享）：
   - ready → 执行（emit）；count>1 逐帧补发，余量让给后续项；
   - pending（矿/气/人口未齐、前置/产出建筑在途或在队列、训练槽满）→ 队首等待，
     **不再越序执行后续项**（顺序语义保留）；
   - skip（prereq_missing｜placement_collision）→ 标记后**继续下一项**（队首冻结退役，
     不级联：被跳过的项不挡后面的路）。
2. 作者错误（catalog 不认 / placement 标记不存在 / research 等不支持 op）→ dropped
   摘除（D6 分工：语法错误摘除、执行失败留账本）。
3. 完成判定：BUILD 看 `_build_flights`（§0.53：实体 build_progress>=1 才算完工），
   TRAIN 看 `_trainings` 账本（G3）——count 归零且账本无记录 → completed。

阻塞不静默：pending 队首的原因+起始时间进 self.blocked[queue]；同一队首阻塞超
STALL_WARN_SECS 记 self.stalls 告警（供 UI/agent 看"卡在哪"）。
assign_workers 立即发（无资源门控）：WorkerAllocator 展开成 gather/stop。

**分文件（REFACTOR G2）**：在途建造确认/挂件/气矿在 `production/flights.py`（Mixin），
placement 解析在 `production/placement.py`（纯函数），执行语义在
`constraint/semantics.py`（纯函数，单点权威——planner/健康检查共享）；本文件留编排
（队列 CRUD / 帧账本 / 阻塞记录 / snapshot / drain / 选择器 / 输出与征用）。
"""
from __future__ import annotations

from game import (
    GameState,
    Operation,
    Owner,
    Point2,
    Queue,
    QueueItem,
    QueueOp,
    WorkerTask,
)
from game.catalog import Catalog
from game.production import PlacementExact, PlacementInRegion
from constraint.checks import check_build, check_train

from production.flights import BuildFlightsMixin
from production.placement import resolve_placement
from constraint.semantics import (
    SKIP_PLACEMENT_COLLISION,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_SKIPPED,
    ExecView,
    Verdict,
    VerdictKind,
    classify,
)
from production.worker import Emission, WorkerAllocator

# 队首阻塞多久算失速 → 记一条 stalls 告警（游戏秒）。
# 纯诊断阈值：只影响是否报告，不影响任何执行决策（攒 150 矿约 15-20s，故取 30s 不误报）。
STALL_WARN_SECS = 30.0

# V1 不支持的生产队列项 → 原因（drain 时出队并记入 dropped，R7 降级告警）
UNSUPPORTED_QUEUE_OPS: dict[QueueOp, str] = {
    QueueOp.RESEARCH: "upgrade 数据目录（cost/time/前置/科技挂件）待建",
    QueueOp.CANCEL: "取消需按上下文选 CANCEL_* 能力（同 driver UNIMPLEMENTED_ACTIONS）",
}


def _placement_dict(p) -> dict | None:
    """PlacementSpec → 帧里的判别联合（前端按 kind 分支渲染）。"""
    if p is None:
        return None
    if isinstance(p, PlacementExact):
        return {"kind": "exact", "mark": p.mark}
    if isinstance(p, PlacementInRegion):
        return {"kind": "in_region", "region": p.region, "index": p.index}
    raise TypeError(f"未知 PlacementSpec {type(p).__name__}（不静默：新增放置形态要同步契约）")


class ProductionRuntime(BuildFlightsMixin):
    """生产运行时：命名队列集合 + 语义门控 drain + 工具操作（P0 生产模块安排）。"""

    def __init__(self, catalog: Catalog, port, region_layer=None,
                 reservations=None, economy=None) -> None:
        self._catalog = catalog
        self._port = port  # duck-typed：submit_operations（同 flow engine）
        # 工兵征用登记（ADR-0030 D3.3）：与 flow.Allocator / EconomyKeeper 共用同一个实例，
        # 建造期间那个 SCV 既不会被战斗组 lease、也不会被维持器改派（issues P14 的结构性修法）。
        self._reservations = reservations
        # 经济维持器（ADR-0030 D2.2）：有它时 assign_workers 队列项 = 写目标（幂等、意图不蒸发）；
        # 没有它时退回旧的一次性 WorkerAllocator 展开（脚本迁移完再删）。
        self._economy = economy
        self._flight_seq = 0  # 征用 owner 编号（production/build#N）
        self._region_layer = region_layer  # placement 解析（BuildSlot/PosMark，ADR-0029）
        self._queues: dict[str, Queue] = {}
        self._workers = WorkerAllocator(catalog)
        self._op_seq = 0
        self.dropped: list[tuple[QueueItem, str]] = []  # 作者错误摘除 + 原因（R7 审计，D6）
        # 队首阻塞可观测性（H1）：queue_name -> {item, reason, since, frames, warned}
        self.blocked: dict[str, dict] = {}
        # uid 分配账本（ADR-0032）：queue_name -> 下一个编号。clear 不重置 —— uid
        # 一旦发出去（帧/提案/agent 上下文里）就永远不复用
        self._uid_next: dict[str, int] = {}
        # 曾建成过的建筑类型 stable_id（E 批 2026-08-24）：判定「曾被摧毁 vs 从没建过」
        self._ever_built: set[str] = set()  # 曾建成的 stable_id（累计只增不减；I27 改名自 _ever_ready——语义是「曾建成」非「现在就绪」，当前在场由警报层对账）
        # 在训记账（G3，rev 17）：emit 训练单时记开始时刻 —— SC2 订单不带进度，
        # 复盘截断线左侧的"训练中部分条"全靠这本账 + catalog build_time
        self._trainings: list[dict] = []
        self.stalls: list[tuple[QueueItem, str]] = []  # 失速告警（同一队首只报一次）
        self._block_reason: str = ""  # 最近一次门控失败原因（_block 写入，_note_block 读取）
        self._build_flights: dict[str, list[dict]] = {}  # queue_name -> [在途建造确认状态]（多并行）
        # 本帧已被下过令的单位 tag（跨队列去重：burnysc2 同帧同单位命令被去重丢单——真机踩坑）
        self._frame_busy: set[int] = set()
        # 本帧就绪建筑类型缓存（classify 的 ExecView 用；on_game_state 每帧重算）
        self._frame_ready_types: frozenset[str] = frozenset()
        # 本帧资源账本（P3）：**跨队列共享**，且 BUILD/TRAIN/挂件/气矿/重试全部入账。
        self._frame_min = 0
        self._frame_gas = 0
        self._frame_supply = 0

    # ---- agent 工具操作（P0：submit_queue/append/prepend/clear/remove/reorder）----

    @staticmethod
    def _check_items(items: list[QueueItem]) -> None:
        """工具操作的输入不变量（P12）：agent/LLM 写面最容易塞进 count=0/负数。

        只对**无 uid 的新项**强制 count≥1 —— 带 uid 的项是账本往返（帧反解回灌），
        已执行项 count=0 是合法账本状态（ADR-0032），不能拒。
        R7 不崩游戏，但也不能静默改队列 —— 新项违规直接拒绝（拒绝即返回错误）。
        """
        for it in items:
            if not isinstance(it, QueueItem):
                raise ValueError(f"队列项必须是 QueueItem，当前 {it!r}")
            if it.uid is not None:
                continue  # 账本往返项：状态以账本为准
            if not isinstance(it.count, int) or isinstance(it.count, bool) or it.count < 1:
                raise ValueError(f"队列项 count 必须是 ≥1 的整数，当前 {it.count!r}")

    def _assign_uids(self, name: str, items: list[QueueItem]) -> None:
        """给无 uid 的项分配 per-queue 递增编号（q01…）；带 uid 进来的（帧反解回灌）
        原样保留 —— 重提队列时引用不漂移。"""
        n = self._uid_next.get(name, 1)
        for it in items:
            if it.uid is None:
                it.uid = f"q{n:02d}"
                n += 1
        self._uid_next[name] = n

    def submit_queue(self, name: str, items: list[QueueItem]) -> None:
        """创建/替换命名队列。替换 = 旧队列作废，所以在途建造也要一起取消（P2）。"""
        self._check_items(items)
        self._cancel_flights(name, "队列被 submit_queue 替换")
        self._assign_uids(name, items)
        self._queues[name] = Queue(name=name, items=list(items))

    def append(self, name: str, items: list[QueueItem]) -> None:
        self._check_items(items)
        self._assign_uids(name, items)
        self._queues.setdefault(name, Queue(name=name)).items.extend(items)

    def prepend(self, name: str, items: list[QueueItem]) -> None:
        self._check_items(items)
        self._assign_uids(name, items)
        q = self._queues.setdefault(name, Queue(name=name))
        q.items[0:0] = items  # 队首插入（紧急/LLM 临时决策）

    def insert(self, name: str, before_uid: str | None, items: list[QueueItem]) -> None:
        """在 uid=before_uid 的项**之前**插入（B2 在账本化后的演进）；before_uid=None 追加到末尾。

        uid 是稳定锚点：已执行项保留在队列里，下标会随执行区增长漂移，引用必须走 uid。
        帧边界语义由会话层保证（命令在帧边界应用，一帧内世界不变）。
        """
        self._check_items(items)
        q = self._queues.get(name)
        if q is None:
            if before_uid is not None:
                raise ValueError(f"insert：队列 {name!r} 不存在，before_uid 只能省略（追加）")
            q = Queue(name=name)
            self._queues[name] = q
        self._assign_uids(name, items)
        if before_uid is None:
            q.items.extend(items)
            return
        for idx, it in enumerate(q.items):
            if it.uid == before_uid:
                q.items[idx:idx] = items
                return
        raise ValueError(f"insert：队列 {name!r} 没有 uid={before_uid!r} 的项")

    def replace_head(self, name: str, items: list[QueueItem]) -> None:
        """原子换**待执行队首** = 定位第一个 pending 项，替换（取消其在途）一步完成。

        账本化后队列头可能是已完成的历史项 —— 换的永远是第一个还没执行的项；
        没有 pending 项 = 纯追加。空 items 无意义（清空请用 clear）。
        """
        self._check_items(items)
        if not items:
            raise ValueError("replace_head：缺 items（要换上的新项；清空请用 clear）")
        q = self._queues.setdefault(name, Queue(name=name))
        idx = next((i for i, it in enumerate(q.items)
                    if it.status == STATUS_PENDING), None)
        self._assign_uids(name, items)
        if idx is None:
            q.items.extend(items)
            return
        old = q.items[idx]
        q.items[idx:idx + 1] = items
        self._cancel_flights(name, "队列项被 replace_head 替换", only_item=old)

    def clear(self, name: str) -> None:
        """清空队列 **并取消在途建造**（P2）。uid 计数不重置（发出去的 uid 永不复用）。"""
        self._cancel_flights(name, "队列被 clear 取消")
        self._queues[name] = Queue(name=name)

    def remove(self, name: str, ref: QueueItem) -> None:
        q = self._queues.get(name)
        if q is not None:
            q.items = [i for i in q.items if i is not ref]
        self._cancel_flights(name, "队列项被 remove 取消", only_item=ref)

    def item_by_uid(self, name: str, uid: str) -> QueueItem | None:
        q = self._queues.get(name)
        if q is None:
            return None
        return next((it for it in q.items if it.uid == uid), None)

    def _cancel_flights(self, name: str, reason: str, only_item: QueueItem | None = None) -> None:
        """取消某队列的在途建造：停止重试 + 释放建造工 + 记 dropped（不静默）。"""
        flights = self._build_flights.get(name)
        if not flights:
            return
        keep = []
        for flight in flights:
            if only_item is not None and flight.get("item") is not only_item:
                keep.append(flight)
                continue
            self._release_flight(flight)
            self.dropped.append((flight.get("item"), reason))
        self._build_flights[name] = keep

    def reorder(self, name: str, refs: list[QueueItem]) -> None:
        """按目标顺序重排（refs = 目标顺序的 QueueItem 对象列表；uid 随对象走，不变）。"""
        q = self._queues.get(name)
        if q is not None:
            q.items = list(refs)

    def queue(self, name: str) -> Queue | None:
        return self._queues.get(name)

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

    # ---- 每帧 tick ----

    def on_game_state(self, gs: GameState) -> None:
        self._frame_busy = set()  # 每帧重置：同帧跨队列不重复命令同一单位
        self._frame_min = 0  # 帧账本（P3）：跨队列共享，drain 前重置
        self._frame_gas = 0
        self._frame_supply = 0
        # E 批 + 批 1：就绪建筑类型缓存（classify 的 ExecView 用）+ 曾建成记忆
        ready: set[str] = set()
        for u in gs.units:
            if u.owner is Owner.SELF and u.build_progress >= 1.0:
                entry = self._catalog.by_burnysc2_name(
                    self._catalog.normalize_burnysc2_name(u.type_name.upper()))
                if entry is not None:
                    self._ever_built.add(entry.stable_id)
                    ready.add(entry.stable_id)
        self._frame_ready_types = frozenset(ready)
        # G3：在训记账的淘汰 —— 产出建筑没了 = 完成/被取消；订单空**且已超训练时长**
        # 才算完（emit 当帧到订单落地之间有间隙，离线世界也不保证 orders 非空）
        by_tag = {u.tag: u for u in gs.units}
        kept: list[dict] = []
        for t in self._trainings:
            u = by_tag.get(t["producer_tag"])
            if u is None:
                continue
            if not u.orders:
                entry = self._catalog.by_stable_id(t["type"])
                dur = float(entry.build_time) if entry is not None else 30.0
                if gs.game_time - t["started_at"] > dur:
                    continue
            kept.append(t)
        self._trainings = kept
        for q in self._queues.values():
            self._drain(q, gs)

    def _exec_view(self, q: Queue, i: int, gs: GameState, entry) -> ExecView:
        """装配 classify 的世界视图：帧账本余量 + 就绪/在途/队列前序 + 空闲产位。"""
        return ExecView(
            minerals=gs.minerals - self._frame_min,
            vespene=gs.vespene - self._frame_gas,
            supply_used=gs.supply_used + self._frame_supply,
            supply_cap=gs.supply_cap,
            ready_types=self._frame_ready_types,
            inflight_types=frozenset(
                f["type"] for flights in self._build_flights.values() for f in flights),
            queued_types=self._queued_build_types(q, i),
            free_producers=self._free_producer_types(gs, entry.produced_by),
        )

    def _queued_build_types(self, q: Queue, i: int) -> frozenset[str]:
        """未执行建造类型：本队列排在当前项之前 + 其他队列的全部（跨队列无次序，
        等它是安全默认 —— 对方 skip 后自动从集合退出，本项重新判 skip，不会死等）。"""
        out: set[str] = set()
        for qq in self._queues.values():
            for j, it in enumerate(qq.items):
                if (it.op is QueueOp.BUILD and it.type
                        and it.status in (STATUS_PENDING, STATUS_IN_PROGRESS)):
                    if qq is q and j >= i:
                        continue
                    out.add(it.type)
        return frozenset(out)

    def _free_producer_types(self, gs: GameState, produced_by: str | None) -> frozenset[str]:
        """该产出建筑类型有没有「就绪 + 有空训练槽 + 本帧未被令」的一座。"""
        if produced_by is None:
            return frozenset()
        name = self._catalog.burnysc2_name_for(produced_by)
        if name is None:
            return frozenset()
        addon_orders = self._addon_order_names()
        for u in gs.units:
            if (u.owner is Owner.SELF and u.type_name == name and u.build_progress >= 1.0
                    and u.tag not in self._frame_busy):
                if any((o.ability or "").lower() in addon_orders for o in u.orders):
                    continue  # 在建挂件：不能训练
                if len(u.orders) >= 2:
                    continue  # 双槽（反应堆）已满；SC2 队满静默拒单
                return frozenset({produced_by})
        return frozenset()

    def _drain(self, q: Queue, gs: GameState) -> None:
        # Phase 1: 确认所有在途建造（多并行：遍历 flights 列表）
        flights = self._build_flights.get(q.name)
        if flights:
            still_pending = []
            for flight in flights:
                if flight.get("builder") is not None or flight.get("entity_tag") is not None:
                    # builder 在 → 正常确认；entity_tag 在（I26 收编）→ 走锁实体的进度
                    # 判定路径，别进重试（重发 = 又一座真建筑）
                    outcome = self._confirm_build(flight, gs)
                    if outcome == "waiting":
                        still_pending.append(flight)
                    elif outcome == "started":
                        self._release_flight(flight)  # 建筑**完工**（§0.53）→ 建造工回去采矿
                    elif outcome == "failed":
                        self._release_flight(flight)  # 命令没了/实体消失 → 先放回，重试时再征用
                        still_pending.append(flight)
                    # started: 不保留（确认完成）；完成扫账在 Phase 1 之后统一判
                else:
                    # builder=None → 重试（上帧 failed 后本帧重试）
                    if self._retry_build(flight, q.name, gs):
                        still_pending.append(flight)
                    # else: _retry_build 已 skip/drop，不保留
            self._build_flights[q.name] = still_pending
        self._sweep_completions(q, gs)

        # Phase 2: 顺序门控（skip-and-continue，ADR-0032）——
        # skip 的项标记后继续下一项（队首冻结退役）；pending 的项等待时**不越序**
        # （乱序执行会破坏 build order，且跨帧累计超支）。
        i = 0
        blocked_head: QueueItem | None = None
        while i < len(q.items):
            head = q.items[i]
            if head.status in (STATUS_COMPLETED, STATUS_SKIPPED):
                i += 1          # 历史区：不消费
                continue
            if head.status == STATUS_IN_PROGRESS and head.count <= 0:
                i += 1          # 在途等账本（flight/训练）判完成
                continue
            if head.op in UNSUPPORTED_QUEUE_OPS:
                self.dropped.append((head, UNSUPPORTED_QUEUE_OPS[head.op]))
                q.items.pop(i)
                continue
            if head.op not in (QueueOp.BUILD, QueueOp.TRAIN, QueueOp.ASSIGN_WORKERS):
                # 防御：未知 op 不可执行又不能静默留在队里（会永久占位）→ 摘除记 dropped
                self.dropped.append((head, f"未知 QueueOp {head.op!r}"))
                q.items.pop(i)
                continue
            if head.op is QueueOp.ASSIGN_WORKERS:
                self._do_assign_workers(head, gs)
                self._finish_item(head, gs)
                i += 1
                continue
            # 作者错误（缺 type / catalog 不认）→ dropped 摘除（D6：语法错误不留账本）
            entry = self._catalog.by_stable_id(head.type) if head.type else None
            if entry is None:
                self._drop(head, f"{head.op.value} 未知类型 {head.type!r}")
                q.items.pop(i)
                continue
            verdict = classify(head, self._catalog, self._exec_view(q, i, gs, entry))
            if verdict.kind is VerdictKind.PENDING:
                self._block(verdict.reason)
                blocked_head = head
                break
            if verdict.kind is VerdictKind.SKIP:
                self._mark_skip(head, verdict, gs)
                i += 1
                continue
            # ready → 执行路径（emit 里还会碰到瞬态失败：无 builder=blocked，
            # 放置位耗尽=skipped，标记不存在=作者错误 dropped）
            if head.op is QueueOp.BUILD:
                outcome = self._try_build(head, q.name, gs, q_index=i)
            else:
                outcome = self._try_train(head, gs)
            if outcome == "blocked":
                blocked_head = head
                break
            if outcome in ("skipped", "consumed"):
                if outcome == "consumed":
                    q.items.pop(i)
                else:
                    i += 1
                continue
            # emitted：count--（count>0 留在原地，本帧余量让给后续项）
            head.count -= 1
            self._mark_started(head, gs)
            if head.count <= 0:
                i += 1
            else:
                i += 1
            continue

        if blocked_head is not None:
            self._note_block(q.name, blocked_head, gs)
        else:
            self.blocked.pop(q.name, None)  # 队列通畅或已空 → 清阻塞记录

    # ---- 单项执行 ----

    def _do_assign_workers(self, head: QueueItem, gs: GameState) -> None:
        if not isinstance(head.task, WorkerTask):
            self._drop(head, "assign_workers 缺 task（mineral|gas|idle）")
            return
        if self._economy is not None:
            # ADR-0030 D2.2：队列项 = **写目标**（绝对值，幂等）。目标是持久的，所以
            # "精炼厂还没建好就设了 gas 目标"不再蒸发（issues P9）—— 建好后维持器自动补满。
            self._economy.set_target(head.task.value, max(0, head.count))
            return
        emissions = self._workers.assign(gs, head.task, max(1, head.count),
                                        base_pos=self._base_anchor(),
                                        skip=frozenset(self._frame_busy))
        self._emit(emissions, gs.seq)

    def _base_anchor(self):
        """主基锚点（资源节点过滤中心）；无区域层 → None（不过滤）。"""
        if self._region_layer is None:
            return None
        b = self._region_layer.big_regions.get(self._region_layer.big_index.get(
            self._region_layer.big_grid.data[0][0]
        ))
        return b.anchor if b is not None else None

    def _try_train(self, head: QueueItem, gs: GameState) -> str:
        """返回 emitted / consumed（作者错误摘除）/ blocked（等待）/ skipped。"""
        if head.type is None:
            self._drop(head, "train 缺 type")
            return "consumed"
        res = check_train(gs, self._catalog, head.type)
        if not res.ok:
            return self._block(self._why(res, f"train {head.type} 门控不通过"))
        producer = self._pick_producer(gs, head.type)
        if producer is None:
            # classify 已判 ready（有空闲产位）却选不出 —— 同帧被别的队列抢了：
            # 等下一帧（瞬态），不算失败
            return self._block(f"训练槽同帧被占（{head.type} 稍后重试）")
        self._emit([Emission("train", [producer.tag], {"type": head.type})], gs.seq)
        self._trainings.append({"type": head.type, "producer_tag": producer.tag,
                                "started_at": gs.game_time,
                                "uid": head.uid})   # G3：进度自己记账；uid 关联到项（批 1）
        self._charge(head.type)  # P3
        return "emitted"

    def _try_build(self, head: QueueItem, q_name: str, gs: GameState,
                   q_index: int | None = None) -> str:
        """发出首个候选放置位并记入在途确认（不出队；完成由 flight 账本判）。

        按目标类型分派：addon 挂件（母建筑自建）→ gas 气矿（SCV 建在气井）→ 常规。
        挂件/气矿的分派实现在 `production/flights.py`（Mixin）。
        `q_index`（B3）：emit 时该项在队列里的下标，进 flight["from_index"]。
        """
        if head.type is None:
            self._drop(head, "build 缺 type")
            return "consumed"
        entry = self._catalog.by_stable_id(head.type)
        if entry is None:
            self._drop(head, f"build 未知类型 {head.type!r}")
            return "consumed"
        if "addon" in entry.capabilities:
            return self._try_build_addon(head, q_name, gs, q_index=q_index)
        if "gas" in entry.capabilities:
            return self._try_build_gas(head, q_name, gs, q_index=q_index)
        pos, slot_name, reason = self._resolve_placement(head, gs, attempted=frozenset())
        if reason is not None:
            self._drop(head, reason)  # 作者错误（标记不存在）：摘除继续，不阻塞整队
            return "consumed"
        if pos is None:
            # 执行期失败：候选位全被占 —— 留账本标 skipped，继续下一项（ADR-0032）
            self._mark_skip(head, Verdict(
                VerdictKind.SKIP, SKIP_PLACEMENT_COLLISION,
                f"{head.type} 候选放置位全被占用"), gs)
            return "skipped"
        res = check_build(gs, self._catalog, head.type, pos)
        if not res.ok:
            # classify 已判 ready 后这里只剩放置类失败（footprint 重叠等）
            self._mark_skip(head, Verdict(
                VerdictKind.SKIP, SKIP_PLACEMENT_COLLISION,
                self._why(res, f"build {head.type} 放置校验不通过")), gs)
            return "skipped"
        builder = self._pick_builder(gs, near=pos)
        if builder is None:
            return self._block(self._no_builder_reason())
        self._emit(
            [Emission("build", [builder.tag], {"type": head.type, "position": [pos.x, pos.y]})],
            gs.seq,
        )
        flight = {
            "item": head,
            "uid": head.uid,
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "from_index": q_index,
            "attempted": {slot_name} if slot_name else set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": self._expected_reported(entry, pos),  # 实体应出现的报告位置（位置匹配确认）
            "emitted_pos": [self._expected_reported(entry, pos)],  # I26：历次发射位（晚到实体收编判据）
            "radius": 1.5,
        }
        self._charge(head.type)  # P3：入账（同帧后续项/其他队列都看得见）
        self._reserve_for_flight(flight, builder.tag)  # 建造期间这个 SCV 谁都不许动
        self._build_flights.setdefault(q_name, []).append(flight)
        return "emitted"

    # ---- 选择器（全部经 catalog，不写死单位名）----

    def _pick_producer(self, gs: GameState, stable_id: str):
        """就绪产出建筑（跳过在建挂件/训练槽已满的）。

        真机教训（docs/evidence/full_flow.log）：SC2 训练队列满时静默拒绝新订单——无反馈、不报错；
        挂件双槽 ≈ 2 条订单为满，在建挂件（Reactor 订单）时不可训练。
        """
        entry = self._catalog.by_stable_id(stable_id)
        if entry is None or entry.produced_by is None:
            return None
        name = self._catalog.burnysc2_name_for(entry.produced_by)
        addon_orders = self._addon_order_names()
        for u in gs.units:
            if (u.owner is Owner.SELF and u.type_name == name and u.build_progress >= 1.0
                    and u.tag not in self._frame_busy):
                if any((o.ability or "").lower() in addon_orders for o in u.orders):
                    continue  # 在建挂件：不能训练
                if len(u.orders) >= 2:
                    continue  # 双槽（反应堆）已满；SC2 队满静默拒单
                return u
        return None

    def _pick_builder(self, gs: GameState, near: Point2 | None = None):
        """选建造工兵（2026-08-24 用户拍板：**就近抽采矿 SCV**）。

        - `near`（建造点）给了 → 选离它最近的候选 —— 采矿中的 SCV 就是被抽的对象，
          建造期间征用保护 + 维持器的外来订单规则都不动它；
          建完（flight 确认/取消 → 征用释放 → 订单清空）维持器自动派回采矿。
        - 没给 near（无坐标的路径）→ 退回旧规则：优先真空闲，否则任一候选。
        - 诊断：None 时 caller 的阻塞原因带上计数（场上几个/忙几个/征用几个），
          「缺少建造者」不再是一句猜不出原因的话。
        """
        names = {e.burnysc2_name for e in self._catalog.where(role="worker")}
        reserved = self._reservations.tags() if self._reservations is not None else frozenset()
        pool = [u for u in gs.units
                if u.owner is Owner.SELF and u.type_name in names]
        candidates = [u for u in pool
                      if u.tag not in self._frame_busy  # 本帧已被命令的工兵不重复用
                      and u.tag not in reserved]  # 已在给别的 flight 盖房子的不抢（ADR-0030 D3.3）
        # 诊断三元组（§0.52 D 批）：总数 / 建造征用 / 本帧已令 ——「无可用 SCV」的
        # 阻塞与告警带上它，用户/agent 才看得出是谁占着（截图事故里裸喊了整场）
        self._builder_diag = (
            len(pool),
            sum(1 for u in pool if u.tag in reserved),
            sum(1 for u in pool if u.tag in self._frame_busy),
        )
        if not candidates:
            return None
        if near is not None:
            return min(candidates,
                       key=lambda u: (u.position.x - near.x) ** 2 + (u.position.y - near.y) ** 2)
        idle = [u for u in candidates if not u.orders]  # 优先空闲工兵
        return (idle or candidates)[0]

    def _no_builder_reason(self) -> str:
        """「缺少建造者」的如实版本：带上分类计数，让 agent/用户看得出是谁占着。"""
        total, held, busy = getattr(self, "_builder_diag", (0, 0, 0))
        if not total:
            return "无可用 SCV 去建造（场上工兵 0 —— 一个都没有，先造/保工兵）"
        return (f"无可用 SCV 去建造（场上工兵 {total}：建造征用 {held}、本帧已令 {busy}"
                "—— 等在途建造完工/下一帧再试）")

    # ---- placement 解析（薄包装；实现在 production/placement.py 纯函数）----

    def _resolve_placement(
        self, head: QueueItem, gs: GameState, attempted: frozenset[str] = frozenset()
    ) -> tuple[Point2 | None, str | None, str | None]:
        return resolve_placement(self._region_layer, self._catalog,
                                 self._build_flights, head, gs, attempted)

    # ---- 输出 ----

    def _emit(self, emissions: list[Emission], seq: int) -> None:
        for e in emissions:
            self._frame_busy.update(e.unit_tags)  # 本帧已命令的单位（跨队列去重）
        ops = []
        for e in emissions:
            self._op_seq += 1
            ops.append(Operation(
                op_id=self._op_seq, unit_tags=e.unit_tags, action=e.action,
                params=e.params, seq=seq,
            ))
        if ops:
            self._port.submit_operations(ops)

    # ---- 建造工征用（ADR-0030 D3.3）----

    def _reserve_for_flight(self, flight: dict, tag: int) -> None:
        """给 flight 征用建造工；同一 flight 换人时先释放旧的（owner 不变）。"""
        if self._reservations is None:
            return
        owner = flight.get("owner")
        if owner is None:
            self._flight_seq += 1
            owner = f"production/build#{self._flight_seq}"
            flight["owner"] = owner
        self._reservations.release(owner)
        self._reservations.reserve(owner, tag)

    def _release_flight(self, flight: dict) -> None:
        """flight 结束（实体已出现 / 被丢弃 / 转重试）→ 释放建造工，让它回去采矿。"""
        owner = flight.get("owner")
        if owner is not None and self._reservations is not None:
            self._reservations.release(owner)

    def _drop(self, item: QueueItem, reason: str) -> None:
        self.dropped.append((item, reason))
