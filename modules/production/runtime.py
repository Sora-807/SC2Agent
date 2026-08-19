"""production.runtime：生产运行时 —— drain 命名队列 → constraint 门控 → 发 Operation。

每帧 on_game_state(gs) 触发（与 flow engine 并列消费 GameState，都经同一个 port 出 op）：
1. 队首 resolve（选建造 SCV/产出建筑/放置坐标）→ constraint 校验
2. 可行 → emit Operation；count 递减，归零出队，继续下一项（train count>1 逐帧排队）
3. 不可行 → 队首阻塞等待（如攒矿/等前置），本帧不再处理后续项（P0：队首按 constraint 门控）
assign_workers 立即发（无资源门控）：WorkerAllocator 展开成 gather/stop。
research/cancel：V1 不支持（缺 upgrade 数据目录）→ 出队记入 dropped（R7 降级，不静默）。
build 缺 placement 直接丢（ADR-0027 反例：placement 为 null 的 build 不通过）。
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

from production.worker import Emission, WorkerAllocator

# V1 不支持的生产队列项 → 原因（drain 时出队并记入 dropped，R7 降级告警）
UNSUPPORTED_QUEUE_OPS: dict[QueueOp, str] = {
    QueueOp.RESEARCH: "upgrade 数据目录（cost/time/前置/科技挂件）待建",
    QueueOp.CANCEL: "取消需按上下文选 CANCEL_* 能力（同 driver UNIMPLEMENTED_ACTIONS）",
}


class ProductionRuntime:
    """生产运行时：命名队列集合 + 队首门控 drain + 工具操作（P0 生产模块安排）。"""

    def __init__(self, catalog: Catalog, port, region_layer=None) -> None:
        self._catalog = catalog
        self._port = port  # duck-typed：submit_operations（同 flow engine）
        self._region_layer = region_layer  # placement 解析（BuildSlot/PosMark，ADR-0029）
        self._queues: dict[str, Queue] = {}
        self._workers = WorkerAllocator(catalog)
        self._op_seq = 0
        self.dropped: list[tuple[QueueItem, str]] = []  # 被丢弃项 + 原因（R7 审计）
        self._build_flight: dict[str, dict] = {}  # queue_name -> 在途建造确认状态

    # ---- agent 工具操作（P0：submit_queue/append/prepend/clear/remove/reorder）----

    def submit_queue(self, name: str, items: list[QueueItem]) -> None:
        self._queues[name] = Queue(name=name, items=list(items))

    def append(self, name: str, items: list[QueueItem]) -> None:
        self._queues.setdefault(name, Queue(name=name)).items.extend(items)

    def prepend(self, name: str, items: list[QueueItem]) -> None:
        q = self._queues.setdefault(name, Queue(name=name))
        q.items[0:0] = items  # 队首插入（紧急/LLM 临时决策）

    def clear(self, name: str) -> None:
        self._queues[name] = Queue(name=name)

    def remove(self, name: str, ref: QueueItem) -> None:
        q = self._queues.get(name)
        if q is not None:
            q.items = [i for i in q.items if i is not ref]

    def reorder(self, name: str, refs: list[QueueItem]) -> None:
        q = self._queues.get(name)
        if q is not None:
            q.items = list(refs)  # refs = 目标顺序的 QueueItem 对象列表

    def queue(self, name: str) -> Queue | None:
        return self._queues.get(name)

    # ---- 每帧 tick ----

    def on_game_state(self, gs: GameState) -> None:
        for q in self._queues.values():
            self._drain(q, gs)

    def _drain(self, q: Queue, gs: GameState) -> None:
        while q.items:
            head = q.items[0]
            # 在途建造确认（build 发出后不立即出队：等实体出现 / 放置失败换位重试）
            if q.name in self._build_flight:
                flight = self._build_flight[q.name]
                if flight["builder"] is not None:
                    outcome = self._confirm_build(q.name, head, gs)
                    if outcome == "started":
                        head.count -= 1
                        if head.count <= 0:
                            q.items.pop(0)
                    break  # 本帧完成一个建造确认（failed/waiting 也 break，下帧继续）
                else:
                    # 重试子状态：换候选位重发；资源不足则继续等
                    if not self._retry_build(head, q.name, gs):
                        q.items.pop(0)  # 候选位耗尽 → 丢弃
                        continue  # 丢弃项不占预算，继续 drain 后续项
                    break
            if head.op in UNSUPPORTED_QUEUE_OPS:
                self.dropped.append((head, UNSUPPORTED_QUEUE_OPS[head.op]))
                q.items.pop(0)
                continue
            if head.op is QueueOp.ASSIGN_WORKERS:
                self._do_assign_workers(head, gs)
                q.items.pop(0)  # 无门控，立即消费
                continue
            # 结果三态：emitted=已发（占本帧预算）；consumed=已丢弃（不占预算，继续）；blocked=等待
            if head.op is QueueOp.BUILD:
                outcome = self._try_build(head, q.name, gs)
                if outcome == "blocked":
                    break  # 队首阻塞（等资源/前置/工兵/放置位）
                if outcome == "consumed":
                    q.items.pop(0)
                    continue  # 丢弃项不占预算，继续 drain 后续项
                break  # emitted：转入在途确认，本帧结束此队列
            if head.op is QueueOp.TRAIN:
                outcome = self._try_train(head, gs)
                if outcome == "blocked":
                    break  # 队首阻塞
                head.count -= 1
                if head.count <= 0:
                    q.items.pop(0)
                if outcome == "emitted":
                    break  # 每帧一训（count>1 → 训练队列排队）
                continue
            break  # 未知 op（词表外）→ 阻塞保护

    # ---- 单项执行 ----

    def _do_assign_workers(self, head: QueueItem, gs: GameState) -> None:
        if not isinstance(head.task, WorkerTask):
            self._drop(head, "assign_workers 缺 task（mineral|gas|idle）")
            return
        emissions = self._workers.assign(gs, head.task, max(1, head.count))
        self._emit(emissions, gs.seq)

    def _try_train(self, head: QueueItem, gs: GameState) -> str:
        """返回 emitted / consumed（丢弃）/ blocked（等待）。"""
        if head.type is None:
            self._drop(head, "train 缺 type")
            return "consumed"
        res = check_train(gs, self._catalog, head.type)
        if not res.ok:
            return "blocked"
        producer = self._pick_producer(gs, head.type)
        if producer is None:
            return "blocked"  # 无就绪产出建筑 → 等
        self._emit([Emission("train", [producer.tag], {"type": head.type})], gs.seq)
        return "emitted"

    def _try_build(self, head: QueueItem, q_name: str, gs: GameState) -> str:
        """发出首个候选放置位并记入在途确认（不立即出队）。"""
        if head.type is None:
            self._drop(head, "build 缺 type")
            return "consumed"
        pos, slot_name, reason = self._resolve_placement(head, gs, attempted=frozenset())
        if reason is not None:
            self._drop(head, reason)  # 作者错误：丢弃并继续，不阻塞整队
            return "consumed"
        if pos is None:
            return "blocked"  # 放置位全被占：瞬时阻塞等待
        res = check_build(gs, self._catalog, head.type, pos)
        if not res.ok:
            return "blocked"  # 阻塞等待（资源/前置/占用）
        builder = self._pick_builder(gs)
        if builder is None:
            return "blocked"  # 无工兵 → 等
        self._emit(
            [Emission("build", [builder.tag], {"type": head.type, "position": [pos.x, pos.y]})],
            gs.seq,
        )
        self._build_flight[q_name] = {
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "attempted": {slot_name} if slot_name else set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
        }
        return "emitted"

    # ---- 在途建造确认（真机教训：SC2 放置失败时命令静默消失）----

    def _type_entity_tags(self, gs: GameState, stable_id: str) -> frozenset[int]:
        name = self._catalog.burnysc2_name_for(stable_id)
        return frozenset(u.tag for u in gs.units
                         if u.owner is Owner.SELF and u.type_name == name)

    def _confirm_build(self, q_name: str, head: QueueItem, gs: GameState) -> str:
        """在途建造：started（实体出现）/ failed（放置失败或超时 → 转重试）/ waiting。

        真机教训（ops check）：SC2 对非法放置位静默丢弃命令——实体不出现、
        builder 的 build order 消失。以此作为失败信号换候选位重试。
        """
        flight = self._build_flight[q_name]
        flight["frames"] += 1
        new_entities = self._type_entity_tags(gs, flight["type"]) - flight["seen_tags"]
        if new_entities:
            self._build_flight.pop(q_name)
            return "started"
        name = self._catalog.burnysc2_name_for(flight["type"])
        builder = next((u for u in gs.units if u.tag == flight["builder"]), None)
        if builder is None:
            flight["builder"] = None  # 建造者没了 → 转重试
            return "failed"
        has_build_order = any(
            o.ability and o.ability.lower() == (name or "").lower() for o in builder.orders
        )
        if flight["frames"] >= 5 and not has_build_order:
            flight["builder"] = None  # 命令已消失且无实体 → 放置失败 → 转重试
            return "failed"
        if flight["frames"] >= 120:
            flight["builder"] = None  # 超时（走过去太久/卡住）→ 转重试
            return "failed"
        return "waiting"

    def _retry_build(self, head: QueueItem, q_name: str, gs: GameState) -> bool:
        """重试子状态：换下一个候选位重发。

        返回 True = 项保留（已重发或资源暂时不够继续等）；False = 候选耗尽已丢弃。
        """
        flight = self._build_flight[q_name]
        pos, slot_name, reason = self._resolve_placement(
            head, gs, attempted=frozenset(flight["attempted"])
        )
        if pos is None or slot_name is None:
            self._drop(head, reason or f"放置失败：候选位耗尽（已试 {sorted(flight['attempted'])}）")
            self._build_flight.pop(q_name)
            return False
        res = check_build(gs, self._catalog, head.type, pos)
        builder = self._pick_builder(gs)
        if not res.ok or builder is None:
            return True  # 资源不够/没工兵：flight 保持 builder=None，下帧继续重试
        self._emit(
            [Emission("build", [builder.tag], {"type": head.type, "position": [pos.x, pos.y]})],
            gs.seq,
        )
        flight["builder"] = builder.tag
        flight["frames"] = 0
        flight["seen_tags"] = self._type_entity_tags(gs, head.type)
        flight["attempted"] = set(flight["attempted"]) | {slot_name}
        return True

    # ---- 选择器（全部经 catalog，不写死单位名）----

    def _pick_producer(self, gs: GameState, stable_id: str):
        entry = self._catalog.by_stable_id(stable_id)
        if entry is None or entry.produced_by is None:
            return None
        name = self._catalog.burnysc2_name_for(entry.produced_by)
        for u in gs.units:
            if u.owner is Owner.SELF and u.type_name == name and u.build_progress >= 1.0:
                return u
        return None

    def _pick_builder(self, gs: GameState):
        names = {e.burnysc2_name for e in self._catalog.where(role="worker")}
        candidates = [u for u in gs.units if u.owner is Owner.SELF and u.type_name in names]
        idle = [u for u in candidates if not u.orders]  # 优先空闲工兵
        return (idle or candidates or [None])[0]

    # ---- placement 解析（ADR-0029/0027；V1 近似）----

    def _resolve_placement(
        self, head: QueueItem, gs: GameState, attempted: frozenset[str] = frozenset()
    ) -> tuple[Point2 | None, str | None, str | None]:
        """placement 标记 → 放置点（跳过 attempted 里已失败的候选位）。

        返回 (point, slot_name, None) = 解析成功；
        (None, slot_name, reason) = 作者错误（丢弃并继续，不阻塞整队）；
        (None, None, None) = 瞬时无解（slot 全被占/全被尝试过）→ 阻塞等待或重试。
        """
        layer = self._region_layer
        if layer is None:
            return (None, None, "build 缺 region_layer（placement 解析需要区域层）")
        placement = head.placement
        if placement is None:
            return (None, None, "build 缺 placement（ADR-0027：placement 为 null 的 build 不通过）")
        if isinstance(placement, PlacementExact):
            if placement.mark in attempted:
                return (None, None, None)  # 唯一候选已失败
            pm = layer.pos_marks.get(placement.mark)
            if pm is not None:
                return (pm.pos, placement.mark, None)
            bs = layer.build_slots.get(placement.mark)
            if bs is not None:
                return (self._slot_point(bs), placement.mark, None)
            return (None, None, f"placement 标记 {placement.mark!r} 未登记")
        if isinstance(placement, PlacementInRegion):
            r = layer.regions.get(placement.region)
            if r is None:
                return (None, None, f"区域 {placement.region!r} 未登记")
            slots = [layer.build_slots[n] for n in r.build_slots if n in layer.build_slots]
            if not slots:
                return (None, None, f"区域 {placement.region!r} 无 build_slots")
            if placement.index is not None:
                if placement.index >= len(slots):
                    return (None, None, f"区域 {placement.region!r} slot index {placement.index} 越界")
                bs = slots[placement.index]
                return (self._slot_point(bs), bs.name, None)
            occupied = self._occupied_cells(gs)
            for bs in slots:  # index=None：按声明顺序找第一个未被占/未尝试过的 slot（P0）
                if bs.name in attempted:
                    continue
                if not (self._slot_cells(bs) & occupied):
                    return (self._slot_point(bs), bs.name, None)
            return (None, None, None)  # 全被占/全试过 → 阻塞等待或重试
        return (None, None, f"未知 placement 类型 {type(placement).__name__}")

    def _slot_cells(self, bs) -> set[tuple[int, int]]:
        return {(x, y) for x in range(bs.tl.x, bs.br.x + 1) for y in range(bs.tl.y, bs.br.y + 1)}

    def _slot_point(self, bs) -> Point2:
        # V1 近似：footprint 中心；偶数尺寸偏移待 ADR-0027 driver fixture 校准
        return Point2(bs.tl.x + bs.size / 2.0, bs.tl.y + bs.size / 2.0)

    def _occupied_cells(self, gs: GameState) -> set[tuple[int, int]]:
        """己方建筑占据的格点（V1 单格近似：单位位置格）。"""
        return {
            (int(u.position.x), int(u.position.y))
            for u in gs.units if u.owner is Owner.SELF
        }

    # ---- 输出 ----

    def _emit(self, emissions: list[Emission], seq: int) -> None:
        ops = []
        for e in emissions:
            self._op_seq += 1
            ops.append(Operation(
                op_id=self._op_seq, unit_tags=e.unit_tags, action=e.action,
                params=e.params, seq=seq,
            ))
        if ops:
            self._port.submit_operations(ops)

    def _drop(self, item: QueueItem, reason: str) -> None:
        self.dropped.append((item, reason))
