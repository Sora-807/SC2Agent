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
from constraint.checks import (
    check_addon,
    check_build,
    check_gas,
    check_train,
    occupied_cells,
)
from tactical_map.placement import BuildSlot

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
        self._build_flights: dict[str, list[dict]] = {}  # queue_name -> [在途建造确认状态]（多并行）
        # 本帧已被下过令的单位 tag（跨队列去重：burnysc2 同帧同单位命令被去重丢单——真机踩坑）
        self._frame_busy: set[int] = set()

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
        self._frame_busy = set()  # 每帧重置：同帧跨队列不重复命令同一单位
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
                    elif outcome == "failed":
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

        # Phase 2: 贪心处理队列项（emit 后不 break → 并行建造）
        pending_min = 0  # 本帧已扣矿（防同帧多并行超支：SC2 对矿不足命令静默拒绝）
        pending_gas = 0
        i = 0
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
                # 本地资源预检（gs 是帧快照，不反映同帧已扣的矿）
                entry = self._catalog.by_stable_id(head.type) if head.type else None
                if entry is not None:
                    if gs.minerals - pending_min < entry.cost.minerals:
                        i += 1
                        continue
                    if gs.vespene - pending_gas < entry.cost.vespene:
                        i += 1
                        continue
                outcome = self._try_build(head, q.name, gs)
                if outcome == "blocked":
                    i += 1  # 资源/前置/工兵/放置位不足 → 跳过试下一项
                    continue
                if outcome == "consumed":
                    q.items.pop(i)
                    continue
                # emitted: 扣本地资源，count--，出队或留队
                if entry is not None:
                    pending_min += entry.cost.minerals
                    pending_gas += entry.cost.vespene
                head.count -= 1
                if head.count <= 0:
                    q.items.pop(i)
                else:
                    i += 1
                continue  # 不 break → 继续下一项（并行！）
            if head.op is QueueOp.TRAIN:
                outcome = self._try_train(head, gs)
                if outcome == "blocked":
                    i += 1
                    continue
                head.count -= 1
                if head.count <= 0:
                    q.items.pop(i)
                else:
                    i += 1
                continue
            i += 1  # 未知 op → 跳过

    # ---- 单项执行 ----

    def _do_assign_workers(self, head: QueueItem, gs: GameState) -> None:
        if not isinstance(head.task, WorkerTask):
            self._drop(head, "assign_workers 缺 task（mineral|gas|idle）")
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
            return "blocked"
        producer = self._pick_producer(gs, head.type)
        if producer is None:
            return "blocked"  # 无就绪产出建筑 → 等
        self._emit([Emission("train", [producer.tag], {"type": head.type})], gs.seq)
        return "emitted"

    def _try_build(self, head: QueueItem, q_name: str, gs: GameState) -> str:
        """发出首个候选放置位并记入在途确认（不立即出队）。

        按目标类型分派：addon 挂件（母建筑自建）→ gas 气矿（SCV 建在气井）→ 常规。
        """
        if head.type is None:
            self._drop(head, "build 缺 type")
            return "consumed"
        entry = self._catalog.by_stable_id(head.type)
        if entry is None:
            self._drop(head, f"build 未知类型 {head.type!r}")
            return "consumed"
        if "addon" in entry.capabilities:
            return self._try_build_addon(head, q_name, gs)
        if "gas" in entry.capabilities:
            return self._try_build_gas(head, q_name, gs)
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
        self._build_flights.setdefault(q_name, []).append({
            "item": head,
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "attempted": {slot_name} if slot_name else set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": self._expected_reported(entry, pos),  # 实体应出现的报告位置（位置匹配确认）
            "radius": 1.5,
        })
        return "emitted"

    # ---- 在途建造确认（真机教训：SC2 放置失败时命令静默消失）----

    @staticmethod
    def _expected_reported(entry, build_point: Point2) -> Point2:
        """建造点 → 实体报告位置（锁定公式 ADR-0027）：奇数尺寸 R=P；偶数尺寸 R=P+0.5。"""
        size = entry.size if entry is not None else 2
        if size % 2 == 1:
            return build_point
        return Point2(build_point.x + 0.5, build_point.y + 0.5)

    def _type_entity_tags(self, gs: GameState, stable_id: str) -> frozenset[int]:
        name = self._catalog.burnysc2_name_for(stable_id)
        return frozenset(u.tag for u in gs.units
                         if u.owner is Owner.SELF and u.type_name == name)

    def _confirm_build(self, flight: dict, gs: GameState) -> str:
        """在途建造：started（实体出现）/ failed（放置失败或超时 → 转重试）/ waiting。

        真机教训（ops check）：SC2 对非法放置位静默丢弃命令——实体不出现、
        builder 的 build order 消失。以此作为失败信号换候选位重试。
        caller 管理 flights 列表，本方法只返回状态（不 pop）。
        """
        flight["frames"] += 1
        new_entities = self._type_entity_tags(gs, flight["type"]) - flight["seen_tags"]
        if new_entities:
            expect = flight.get("expect_pos")
            if expect is None:
                # 挂件：无放置位，按类型计数确认
                return "started"
            radius2 = flight.get("radius", 1.5) ** 2
            matched = any(
                u.tag in new_entities
                and (u.position.x - expect.x) ** 2 + (u.position.y - expect.y) ** 2 <= radius2
                for u in gs.units
            )
            if matched:
                return "started"
            # 类型计数误报（同类型其他在途实体晚到）→ 不算，继续等本放置位实体
        name = self._catalog.burnysc2_name_for(flight["type"])
        builder = next((u for u in gs.units if u.tag == flight["builder"]), None)
        if builder is None:
            flight["builder"] = None  # 建造者没了 → 转重试
            return "failed"
        entry = self._catalog.by_stable_id(flight["type"])
        if entry is not None and entry.build_order_name:
            # 挂件：订单按钮名是通用名（如 Reactor），与实体类型名（BARRACKSREACTOR）不同
            has_build_order = any(
                o.ability and entry.build_order_name.lower() in (o.ability or "").lower()
                for o in builder.orders
            )
        else:
            has_build_order = any(
                o.ability and (name or "").lower() in o.ability.lower() for o in builder.orders
            )
        build_time = entry.build_time if entry is not None else 21
        # 超时按 build_time 换算（~5.6 帧/游戏秒 ×2 余量；挂件反应堆 36s ≈ 202 帧，
        # 旧固定 120 帧在完工前误判失败——真机踩坑）
        timeout = max(120, int(build_time * 5.6 * 2))
        # SCV 走到建造位需要时间（远位 11+ 格 ≈ 3-5s）；30 帧(5s)太短 → 误判失败 → 换位重试 → 循环
        # 改为 90 帧(16s)：足够走到任何建造位；真正失败的命令（位置非法被 SC2 静默拒绝）也不会持续 16s
        if flight["frames"] >= 90 and not has_build_order:
            flight["builder"] = None  # 命令已消失且无实体 → 放置失败 → 转重试
            return "failed"
        if flight["frames"] >= timeout:
            flight["builder"] = None  # 超时（卡死）→ 转重试
            return "failed"
        return "waiting"

    def _retry_build(self, flight: dict, q_name: str, gs: GameState) -> bool:
        """重试子状态：换下一个候选位重发。

        返回 True = 项保留（已重发或资源暂时不够继续等）；False = 候选耗尽已丢弃。
        挂件走独立分支（无放置位：重选母建筑重发；候选 = 空闲母建筑数，重试上限防永久阻塞）。
        """
        head = flight["item"]
        entry = self._catalog.by_stable_id(head.type)
        if entry is not None and "addon" in entry.capabilities:
            return self._retry_build_addon(flight, q_name, gs)
        pos, slot_name, reason = self._resolve_placement(
            head, gs, attempted=frozenset(flight["attempted"])
        )
        if pos is None or slot_name is None:
            self._drop(head, reason or f"放置失败：候选位耗尽（已试 {sorted(flight['attempted'])}）")
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
        flight["expect_pos"] = self._expected_reported(entry, pos)  # 换位重发 → 换预期位置
        flight["radius"] = 1.5
        return True

    def _retry_build_addon(self, flight: dict, q_name: str, gs: GameState) -> bool:
        """挂件重试：重选空闲母建筑重发（无放置位概念）。

        只在实际重发时计一次重试（资源/母建筑不足的等待帧不计——真机踩坑：等待帧
        也会烧重试次数导致 3 帧内误丢弃）；超过 6 次实际重发丢弃防永久阻塞。
        返回 True = 项保留（已重发或暂缺继续等）；False = 已丢弃。
        """
        head = flight["item"]
        if flight.get("retries", 0) >= 6:
            self._drop(head, "挂件重发超过 6 次（母建筑候选耗尽）")
            return False
        res = check_addon(gs, self._catalog, head.type)
        parent = self._pick_parent_for_addon(gs, head.type) if res.ok else None
        if not res.ok or parent is None:
            return True  # 资源不够/无空闲母建筑：保持 builder=None，下帧继续
        self._emit(
            [Emission("build", [parent.tag], {"type": head.type, "position": None})],
            gs.seq,
        )
        flight["retries"] = flight.get("retries", 0) + 1
        flight["builder"] = parent.tag
        flight["frames"] = 0
        flight["seen_tags"] = self._type_entity_tags(gs, head.type)
        flight["expect_pos"] = self._expected_addon_reported(parent)
        flight["radius"] = 1.5
        return True

    # ---- 选择器（全部经 catalog，不写死单位名）----

    def _pick_producer(self, gs: GameState, stable_id: str):
        """就绪产出建筑（跳过在建挂件/训练槽已满的）。

        真机教训（docs/full_flow.log）：SC2 训练队列满时静默拒绝新订单——无反馈、不报错；
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

    def _pick_builder(self, gs: GameState):
        names = {e.burnysc2_name for e in self._catalog.where(role="worker")}
        candidates = [u for u in gs.units
                      if u.owner is Owner.SELF and u.type_name in names
                      and u.tag not in self._frame_busy]  # 本帧已被命令的工兵不重复用
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
            # 按槽位类别 + footprint 尺寸过滤：supply→补给位、production→生产位、
            # addon 位不参与放置（挂件由母建筑吸附，几何预留）；兵营(3×3)不放补给站(2×2)位（真机踩过）
            entry = self._catalog.by_stable_id(head.type)
            if entry is not None and "supply" in entry.capabilities:
                slots = [bs for bs in slots if bs.kind == "supply"]
            else:
                slots = [bs for bs in slots if bs.kind == "production"]
            if not slots:
                return (None, None, f"区域 {placement.region!r} 无匹配类别的 build_slots")
            target_size = entry.size if entry is not None else None
            if target_size is not None:
                slots = [bs for bs in slots if bs.size == target_size]
                if not slots:
                    return (None, None, f"区域 {placement.region!r} 无 size={target_size} 的 build_slots")
            occupied = occupied_cells(gs, self._catalog)
            # 在途建造预留：命令已发但实体未出现的放置位不能被别的项重选
            # （真机教训 full_flow.log：未预留 → 重选已下单位 → SC2 静默拒绝 → 丢补给站）
            for f in (f for flights in self._build_flights.values() for f in flights):
                ep = f.get("expect_pos")
                if ep is None or f.get("builder") is None:
                    continue
                fe = self._catalog.by_stable_id(f.get("type"))
                fs = fe.size if fe is not None else 2
                ftl = BuildSlot.tl_from_reported(ep, fs)
                occupied |= {(x, y) for x in range(ftl.x, ftl.x + fs) for y in range(ftl.y, ftl.y + fs)}
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
        """下发 driver 的世界建造点：校准值优先（BuildSlot.build_point）。"""
        return bs.build_point

    # ---- 挂件（addon）与气矿（gas）建造路径 ----

    def _addon_order_names(self) -> frozenset[str]:
        """在建挂件订单按钮名词表（catalog build_order_name，如 reactor/techlab）。

        实体未出现但订单持续时母建筑不能再下单/训练（真机踩坑：重复下单无产出）。
        """
        return frozenset(
            e.build_order_name.lower()
            for e in self._catalog.where(capability="addon")
            if e.build_order_name
        )

    def _addon_cells(self, building, gs: GameState) -> set[tuple[int, int]]:
        """building 的右下 2×2 挂件预留格点（锁定公式：TL = floor(R - size/2)）。"""
        entry = self._catalog.by_burnysc2_name(building.type_name)
        size = entry.size if entry is not None else 3
        tl = BuildSlot.tl_from_reported(building.position, size)
        return {(tl.x + size + dx, tl.y + dy) for dx in range(2) for dy in range(2)}

    def _expected_addon_reported(self, building) -> Point2:
        """母建筑 → 挂件实体报告位置（真机锁定裸实验 docs/bare_addon.log）：
        贴附右下 2×2 → 报告 R = 母建筑 TL + (size, 0) + (1, 1)。
        例：兵营 (117.5,110.5) → TL(116,109) → 反应堆报告 (120.0,110.0) ✓
        """
        entry = self._catalog.by_burnysc2_name(building.type_name)
        size = entry.size if entry is not None else 3
        tl = BuildSlot.tl_from_reported(building.position, size)
        return Point2(tl.x + size + 1, tl.y + 1)

    def _has_addon(self, building, gs: GameState) -> bool:
        return bool(occupied_cells(gs, self._catalog) & self._addon_cells(building, gs))

    def _pick_parent_for_addon(self, gs: GameState, stable_id: str):
        """找一个就绪、完全空闲（无任何订单）且无挂件的母建筑（produced_by）。"""
        entry = self._catalog.by_stable_id(stable_id)
        if entry is None or entry.produced_by is None:
            return None
        name = self._catalog.burnysc2_name_for(entry.produced_by)
        for u in gs.units:
            if (u.owner is Owner.SELF and u.type_name == name and u.build_progress >= 1.0
                    and u.tag not in self._frame_busy):
                # 只选完全空闲的母建筑（真机教训 full_flow.log：带训练订单的兵营挂件命令
                # 会被拒绝或与同帧训练命令冲突，订单静默消失）
                if u.orders or self._has_addon(u, gs):
                    continue
                return u
        return None

    def _try_build_addon(self, head: QueueItem, q_name: str, gs: GameState) -> str:
        """挂件：母建筑自建（builder = 母建筑，非 SCV）；SC2 把挂件吸附到右下 2×2。"""
        res = check_addon(gs, self._catalog, head.type)
        if not res.ok:
            return "blocked"  # 母建筑还没建好（前置）→ 等 macro 队列
        parent = self._pick_parent_for_addon(gs, head.type)
        if parent is None:
            return "blocked"  # 无空闲母建筑 → 等（母建筑被毁的僵局由上层维持规则
            # 自适应处理：reactors >= 现存 rax 即开训，不卡 4 台——真机教训）
        # 挂件是无目标能力：position=None，SC2 吸附到母建筑右下 2×2（真机教训：传点会被静默拒绝）
        self._emit(
            [Emission("build", [parent.tag], {"type": head.type, "position": None})],
            gs.seq,
        )
        self._build_flights.setdefault(q_name, []).append({
            "item": head,
            "type": head.type,
            "builder": parent.tag,
            "frames": 0,
            "attempted": set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": self._expected_addon_reported(parent),  # 并行挂件也按位置确认（防互认）
            "radius": 1.5,
        })
        return "emitted"

    def _pick_free_geyser(self, gs: GameState):
        """找一个未被精炼厂占据的气井（仅主基锚点附近；真机教训：别把精炼厂建敌方气井上）。"""
        buildings = []
        for u in gs.units:
            if u.owner is not Owner.SELF:
                continue
            e = self._catalog.by_burnysc2_name(u.type_name)
            if e is not None and e.size is not None:
                buildings.append(u)
        anchor = self._base_anchor()
        from production.worker import NODE_RADIUS

        # 在途精炼厂预留的气井（命令已发、实体未出现）不能重选（SC2 会静默拒绝第二个）
        reserved = [f["expect_pos"] for flights in self._build_flights.values()
                    for f in flights
                    if f.get("expect_pos") is not None and f.get("builder") is not None]

        def _taken(geyser) -> bool:
            return any(
                (b.position.x - geyser.position.x) ** 2 + (b.position.y - geyser.position.y) ** 2 < 6.25
                for b in buildings
            ) or any(
                (r.x - geyser.position.x) ** 2 + (r.y - geyser.position.y) ** 2 < 6.25
                for r in reserved
            )

        nodes = [u for u in gs.resources if "GEYSER" in u.type_name]
        if anchor is not None:
            nodes = [u for u in nodes
                     if (u.position.x - anchor.x) ** 2 + (u.position.y - anchor.y) ** 2
                     <= NODE_RADIUS ** 2]
        return next((u for u in nodes if not _taken(u)), None)

    def _try_build_gas(self, head: QueueItem, q_name: str, gs: GameState) -> str:
        """气矿：SCV 把精炼厂建在空闲气井上（build_gas 动作，target = 气井 Unit）。"""
        res = check_gas(gs, self._catalog, head.type)
        if not res.ok:
            return "blocked"
        geyser = self._pick_free_geyser(gs)
        builder = self._pick_builder(gs)
        if geyser is None or builder is None:
            return "blocked"
        self._emit(
            [Emission("build_gas", [builder.tag],
                      {"type": head.type, "target_unit": geyser.tag})],
            gs.seq,
        )
        self._build_flights.setdefault(q_name, []).append({
            "item": head,
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "attempted": set(),
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": geyser.position,  # 精炼厂实体应出现在气井位置
            "radius": 3.0,
        })
        return "emitted"

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

    def _drop(self, item: QueueItem, reason: str) -> None:
        self.dropped.append((item, reason))
