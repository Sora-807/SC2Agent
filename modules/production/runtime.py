"""production.runtime：生产运行时 —— drain 命名队列 → constraint 门控 → 发 Operation。

每帧 on_game_state(gs) 触发（与 flow engine 并列消费 GameState，都经同一个 port 出 op）：
1. 队首 resolve（选建造 SCV/产出建筑/放置坐标）→ constraint 校验
2. 可行 → emit Operation；count 递减，归零出队，继续下一项（资源够就同帧并行发多条）
3. 不可行 → 队首阻塞等待（如攒矿/等前置），本帧不再处理后续项（P0/S11：队首按 constraint 门控）
   阻塞不静默：原因+起始时间进 self.blocked[queue]；同一队首阻塞超 STALL_WARN_SECS 记一条
   self.stalls 告警（R7 降级告警，供 UI/agent 看"卡在哪"；不自动丢弃队首——那会破坏 build order）
assign_workers 立即发（无资源门控）：WorkerAllocator 展开成 gather/stop。
research/cancel：V1 不支持（缺 upgrade 数据目录）→ 出队记入 dropped（R7 降级，不静默）。
build 缺 placement 直接丢（ADR-0027 反例：placement 为 null 的 build 不通过）。

**分文件（REFACTOR G2）**：在途建造确认/挂件/气矿在 `production/flights.py`（Mixin，
原样搬运），placement 解析在 `production/placement.py`（纯函数）；本文件留编排
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
    """生产运行时：命名队列集合 + 队首门控 drain + 工具操作（P0 生产模块安排）。"""

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
        self.dropped: list[tuple[QueueItem, str]] = []  # 被丢弃项 + 原因（R7 审计）
        # 队首阻塞可观测性（H1）：queue_name -> {item, reason, since, frames, warned}
        # 队首门控会冻结整条队列，必须能看见"卡在哪、卡多久"，否则就是静默失速。
        self.blocked: dict[str, dict] = {}
        # 曾建成过的建筑类型 stable_id（E 批 2026-08-24）：TRAIN 阻塞在「缺产出建筑」时，
        # 用它区分「被摧毁」（曾就绪、现在没了）和「还没建」—— 文案给 agent 的整改方向不同
        self._ever_ready: set[str] = set()
        # 在训记账（G3，rev 17）：emit 训练单时记开始时刻 —— SC2 订单不带进度，
        # 复盘截断线左侧的"训练中部分条"全靠这本账 + catalog build_time
        self._trainings: list[dict] = []
        self.stalls: list[tuple[QueueItem, str]] = []  # 失速告警（同一队首只报一次）
        self._block_reason: str = ""  # 最近一次门控失败原因（_block 写入，_note_block 读取）
        self._build_flights: dict[str, list[dict]] = {}  # queue_name -> [在途建造确认状态]（多并行）
        # 本帧已被下过令的单位 tag（跨队列去重：burnysc2 同帧同单位命令被去重丢单——真机踩坑）
        self._frame_busy: set[int] = set()
        # 本帧资源账本（P3）：**跨队列共享**，且 BUILD/TRAIN/挂件/气矿/重试全部入账。
        # 原来它是 _drain 的局部变量（每条队列各自一份）且 TRAIN 完全不记账 ——
        # 60 矿 + 两台兵营能同帧发两条 50 矿 train，第二条靠 SC2 静默拒单兜底（队列项却已消费）。
        self._frame_min = 0
        self._frame_gas = 0
        self._frame_supply = 0

    # ---- agent 工具操作（P0：submit_queue/append/prepend/clear/remove/reorder）----

    @staticmethod
    def _check_items(items: list[QueueItem]) -> None:
        """工具操作的输入不变量（P12）：agent/LLM 写面最容易塞进 count=0/负数。

        R7 不崩游戏，但也不能静默改队列 —— 这里直接拒绝（调用方是工具操作，拒绝即返回错误）。
        """
        for it in items:
            if not isinstance(it, QueueItem):
                raise ValueError(f"队列项必须是 QueueItem，当前 {it!r}")
            if not isinstance(it.count, int) or isinstance(it.count, bool) or it.count < 1:
                raise ValueError(f"队列项 count 必须是 ≥1 的整数，当前 {it.count!r}")

    def submit_queue(self, name: str, items: list[QueueItem]) -> None:
        """创建/替换命名队列。替换 = 旧队列作废，所以在途建造也要一起取消（P2）。"""
        self._check_items(items)
        self._cancel_flights(name, "队列被 submit_queue 替换")
        self._queues[name] = Queue(name=name, items=list(items))

    def append(self, name: str, items: list[QueueItem]) -> None:
        self._check_items(items)
        self._queues.setdefault(name, Queue(name=name)).items.extend(items)

    def prepend(self, name: str, items: list[QueueItem]) -> None:
        self._check_items(items)
        q = self._queues.setdefault(name, Queue(name=name))
        q.items[0:0] = items  # 队首插入（紧急/LLM 临时决策）

    def insert(self, name: str, index: int, items: list[QueueItem]) -> None:
        """按**剩余队列位置**插入（B2：0=队首前；越界 ValueError → REST 400）。

        生产队列 = 未来清单：已执行项出队（BUILD 进在途、TRAIN 直接走），队列里没有
        "过去区" —— 插入天然只具后效性。index 是调用方在同一帧看到的下标，
        配合 based_on_seq 的新鲜度门（R8）就够安全。帧边界语义由会话层保证
        （命令在帧边界应用，一帧内世界不变）。
        """
        self._check_items(items)
        q = self._queues.get(name)
        if q is None:
            if index != 0:
                raise ValueError(f"insert：队列 {name!r} 不存在，index 只能是 0（当前 {index}）")
            q = Queue(name=name)
            self._queues[name] = q
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(q.items):
            raise ValueError(
                f"insert：index 必须在 0..{len(q.items)}（剩余队列位置，0=队首前），当前 {index}")
        q.items[index:index] = items

    def replace_head(self, name: str, items: list[QueueItem]) -> None:
        """原子换队首（B2）= remove 未执行队首 + prepend 新项一步完成，不留 409 窗口。

        在途项不受影响（已下令、确认中）。空队列 = 纯 prepend。
        """
        self._check_items(items)
        q = self._queues.setdefault(name, Queue(name=name))
        if q.items:
            q.items.pop(0)
        q.items[0:0] = items

    def clear(self, name: str) -> None:
        """清空队列 **并取消在途建造**（P2）。

        原来只删排队项，_build_flights 里的在途建造照常 retry 并重新发令 ——
        对 agent 来说"取消"是假的（实测清队后 91 帧还在重试）。
        已经发出去的 build 命令无法真正撤回，能做的是停止重试并记审计。
        """
        self._cancel_flights(name, "队列被 clear 取消")
        self._queues[name] = Queue(name=name)

    def remove(self, name: str, ref: QueueItem) -> None:
        q = self._queues.get(name)
        if q is not None:
            q.items = [i for i in q.items if i is not ref]
        self._cancel_flights(name, "队列项被 remove 取消", only_item=ref)

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
        q = self._queues.get(name)
        if q is not None:
            q.items = list(refs)  # refs = 目标顺序的 QueueItem 对象列表

    def queue(self, name: str) -> Queue | None:
        return self._queues.get(name)

    # ---- 帧资源账本（P3：跨队列共享，所有 emit 点入账）----

    def _shortage(self, gs: GameState, entry, with_supply: bool = False) -> str | None:
        """本帧余量是否够（None = 够）。entry=None（未知类型）交给下游 drop 路径处理。"""
        if entry is None:
            return None
        if gs.minerals - self._frame_min < entry.cost.minerals:
            return (f"晶体矿不足（本帧余 {gs.minerals - self._frame_min}"
                    f" < {entry.cost.minerals}）")
        if gs.vespene - self._frame_gas < entry.cost.vespene:
            return (f"高能瓦斯不足（本帧余 {gs.vespene - self._frame_gas}"
                    f" < {entry.cost.vespene}）")
        if with_supply and entry.cost.supply:
            left = gs.supply_cap - gs.supply_used - self._frame_supply
            if left < entry.cost.supply:
                return f"供给不足（本帧余 {left} < {entry.cost.supply})"
        return None

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
                f"队列 {q_name!r} 队首阻塞 {waited:.0f}s（{rec['frames']} 帧）：{rec['reason']}",
            ))

    # ---- 读模型（B1）----

    def _producer_ever_ready(self, rec: dict) -> bool | None:
        """阻塞项的产出建筑「曾建成过吗」。只对 train 类阻塞有意义（E 批）。"""
        if "产出建筑" not in (rec.get("reason") or ""):
            return None
        item = rec.get("item")
        entry = self._catalog.by_stable_id(item.type) if item is not None else None
        if entry is None or not entry.produced_by:
            return None
        return entry.produced_by in self._ever_ready

    def snapshot(self) -> dict:
        """生产运行时的显式只读快照（供 view / agent / 复盘录制）。

        **纯派生，不改 drain**：队首门控的语义已经决定了队列里每一项的状态 ——
        还留在 `q.items` 里的，要么是被卡住的队首（`blocked[q]` 指向它），要么是本帧没轮到；
        已发出的项要么已出队（train/assign_workers），要么进了 `_build_flights`（build 在途）。
        所以不需要在 drain 里埋状态位，也就不会和 drain 的任何重构打架。

        返回**普通 dict**（production 不认识 view，架构测试锁死方向）；
        键名与 `docs/contract/plan-frontend.md` §2 的 ProductionFrame 对齐，由 view.adapt 显式映射。
        """
        queues: list[dict] = []
        for q in self._queues.values():
            rec = self.blocked.get(q.name)
            blocked_item = rec["item"] if rec else None
            items: list[dict] = []
            for index, it in enumerate(q.items):
                is_blocked = it is blocked_item
                items.append({
                    "index": index,
                    "op": it.op.value if isinstance(it.op, QueueOp) else str(it.op),
                    "stable_id": it.type,
                    "count": it.count,
                    "placement": _placement_dict(it.placement),
                    "task": it.task.value if isinstance(it.task, WorkerTask) else it.task,
                    "status": "队首阻塞" if is_blocked else "未处理",
                    "block_reason": rec["reason"] if is_blocked else None,
                })
            if not q.items:
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
                    # 来源队列序号（B3）：emit 时该项在剩余队列里的下标 ——
                    # observe 答"队列执行到第几项"靠它，None = 旧 flight/未知
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
                          "started_at": t["started_at"]} for t in self._trainings],
            # dropped 目前不带时间戳（QueueItem 被丢时没有记 game_time）→ 帧里 at=None，
            # UI 显示"未知"而不是编一个时间（不静默）。要时间戳得在 _drop 处补记。
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
        # E 批：记曾建成过的建筑类型（区分「缺产出建筑」= 被摧毁 还是 还没建）
        for u in gs.units:
            if u.owner is Owner.SELF and u.build_progress >= 1.0:
                entry = self._catalog.by_burnysc2_name(
                    self._catalog.normalize_burnysc2_name(u.type_name.upper()))
                if entry is not None:
                    self._ever_ready.add(entry.stable_id)
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

    def _drain(self, q: Queue, gs: GameState) -> None:
        # Phase 1: 确认所有在途建造（多并行：遍历 flights 列表）
        flights = self._build_flights.get(q.name)
        if flights:
            still_pending = []
            for flight in flights:
                if flight.get("builder") is not None:
                    outcome = self._confirm_build(flight, gs)
                    if outcome == "waiting":
                        still_pending.append(flight)
                    elif outcome == "started":
                        self._release_flight(flight)  # 建筑**完工**（§0.53：实体出现不算，
                        # 要等 build_progress>=1）→ 建造工回去采矿
                    elif outcome == "failed":
                        self._release_flight(flight)  # 命令没了/实体消失 → 先放回，重试时再征用
                        # _confirm_build 已设 builder=None；下帧再重试
                        # （不在本帧重试——避免同帧 failed→retry→failed 循环）
                        still_pending.append(flight)
                    # started: 不保留（确认完成）
                else:
                    # builder=None → 重试（上帧 failed 后本帧重试）
                    if self._retry_build(flight, q.name, gs):
                        still_pending.append(flight)
                    # else: _retry_build 已 drop，不保留
            self._build_flights[q.name] = still_pending

        # Phase 2: 队首门控（P0/S11 + 模块 docstring 第 3 条）——
        # 队首不可行 → 本帧不再处理后续项（乱序执行会破坏 build order，且跨帧累计超支）；
        # 队首可行 → 继续下一项（资源够时同帧并行发多条建造）。
        i = 0
        blocked_head: QueueItem | None = None
        while i < len(q.items):
            head = q.items[i]
            if head.op in UNSUPPORTED_QUEUE_OPS:
                self.dropped.append((head, UNSUPPORTED_QUEUE_OPS[head.op]))
                q.items.pop(i)
                continue
            if head.op is QueueOp.ASSIGN_WORKERS:
                self._do_assign_workers(head, gs)
                q.items.pop(i)
                continue
            if head.op is QueueOp.BUILD:
                # 帧账本预检（gs 是帧快照，不反映同帧已扣的矿；P3 起跨队列共享）
                entry = self._catalog.by_stable_id(head.type) if head.type else None
                shortage = self._shortage(gs, entry)
                if shortage is not None:
                    self._block(shortage)
                    blocked_head = head
                    break
                outcome = self._try_build(head, q.name, gs, q_index=i)
                if outcome == "blocked":
                    blocked_head = head
                    break
                if outcome == "consumed":
                    q.items.pop(i)  # 作者错误（未知 type/缺 placement）：丢弃继续，不冻结整队
                    continue
                # emitted: 入账（_charge 在 emit 点统一记），count--，出队或留队
                head.count -= 1
                if head.count <= 0:
                    q.items.pop(i)
                else:
                    i += 1  # count>1 逐帧排队（既有设计）：本帧余量让给后续项
                continue
            if head.op is QueueOp.TRAIN:
                # P3：TRAIN 此前完全不记账 —— 60 矿 + 两台兵营能同帧发两条 50 矿 train，
                # 第二条靠 SC2 静默拒单兜底而队列项已消费。供给同理（同帧连训会超 cap）。
                entry = self._catalog.by_stable_id(head.type) if head.type else None
                shortage = self._shortage(gs, entry, with_supply=True)
                if shortage is not None:
                    self._block(shortage)
                    blocked_head = head
                    break
                outcome = self._try_train(head, gs)
                if outcome == "blocked":
                    blocked_head = head
                    break
                if outcome == "consumed":
                    q.items.pop(i)  # 作者错误（train 缺 type）：丢弃继续（否则 count>1 会反复记 dropped）
                    continue
                head.count -= 1
                if head.count <= 0:
                    q.items.pop(i)
                else:
                    i += 1
                continue
            # 未知 op：不可执行又不能静默留在队里（会永久占住队首）→ 出队记 dropped
            self.dropped.append((head, f"未知 QueueOp {head.op!r}"))
            q.items.pop(i)

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
        """返回 emitted / consumed（丢弃）/ blocked（等待）。"""
        if head.type is None:
            self._drop(head, "train 缺 type")
            return "consumed"
        res = check_train(gs, self._catalog, head.type)
        if not res.ok:
            return self._block(self._why(res, f"train {head.type} 门控不通过"))
        producer = self._pick_producer(gs, head.type)
        if producer is None:
            return self._block(f"无就绪产出建筑（{head.type} 的产出建筑未完工/训练槽满）")
        self._emit([Emission("train", [producer.tag], {"type": head.type})], gs.seq)
        self._trainings.append({"type": head.type, "producer_tag": producer.tag,
                                "started_at": gs.game_time})   # G3：进度自己记账
        self._charge(head.type)  # P3
        return "emitted"

    def _try_build(self, head: QueueItem, q_name: str, gs: GameState,
                   q_index: int | None = None) -> str:
        """发出首个候选放置位并记入在途确认（不立即出队）。

        按目标类型分派：addon 挂件（母建筑自建）→ gas 气矿（SCV 建在气井）→ 常规。
        挂件/气矿的分派实现在 `production/flights.py`（Mixin）。
        `q_index`（B3）：emit 时该项在剩余队列里的下标，进 flight["from_index"]。
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
            self._drop(head, reason)  # 作者错误：丢弃并继续，不阻塞整队
            return "consumed"
        if pos is None:
            return self._block("候选放置位全被占用（等空位；若区域已满会一直卡在这里）")
        res = check_build(gs, self._catalog, head.type, pos)
        if not res.ok:
            return self._block(self._why(res, f"build {head.type} 门控不通过"))
        builder = self._pick_builder(gs, near=pos)
        if builder is None:
            return self._block(self._no_builder_reason())
        self._emit(
            [Emission("build", [builder.tag], {"type": head.type, "position": [pos.x, pos.y]})],
            gs.seq,
        )
        flight = {
            "item": head,
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "from_index": q_index,
            "attempted": {slot_name} if slot_name else set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": self._expected_reported(entry, pos),  # 实体应出现的报告位置（位置匹配确认）
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
