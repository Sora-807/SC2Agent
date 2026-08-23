"""production.flights：在途建造确认 + 挂件/气矿路径（REFACTOR G2 从 runtime 抽出）。

**为什么是 Mixin 而不是独立对象**：这套状态机与 runtime 的账本/征用/阻塞记录
深度共享（_charge/_emit/_reserve_for_flight/_block…），抽成协作对象要把这套
共享状态设计成回调接口 —— 在行为最关键的文件上做这种手术，回归风险大于收益。
Mixin 搬运 = 代码原样、行为零变，文件导航性拿到手；未来要再纯化，测试已经就位。

宿主契约（ProductionRuntime 提供）：
`_catalog` `_region_layer` `_build_flights` `_frame_busy` `_reservations` +
方法 `_emit/_charge/_drop/_block/_why/_pick_builder/_base_anchor/
_reserve_for_flight/_release_flight`。
"""
from __future__ import annotations

from game import GameState, Owner, Point2, QueueItem
from constraint.checks import check_addon, check_build, check_gas, occupied_cells
from tactical_map.placement import BuildSlot

from production.worker import Emission, NODE_RADIUS


class BuildFlightsMixin:
    """在途建造（confirm/retry 状态机）+ 挂件（addon）+ 气矿（gas）建造路径。"""

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
        if entry is not None and "gas" in entry.capabilities:
            return self._retry_build_gas(flight, q_name, gs)
        pos, slot_name, reason = self._resolve_placement(
            head, gs, attempted=frozenset(flight["attempted"])
        )
        if pos is None or slot_name is None:
            self._drop(head, reason or f"放置失败：候选位耗尽（已试 {sorted(flight['attempted'])}）")
            self._release_flight(flight)
            return False
        res = check_build(gs, self._catalog, head.type, pos)
        builder = self._pick_builder(gs)
        if not res.ok or builder is None:
            return True  # 资源不够/没工兵：flight 保持 builder=None，下帧继续重试
        self._emit(
            [Emission("build", [builder.tag], {"type": head.type, "position": [pos.x, pos.y]})],
            gs.seq,
        )
        self._charge(head.type)  # P3：重试重发也要入账
        flight["builder"] = builder.tag
        self._reserve_for_flight(flight, builder.tag)  # 换人重发 → 征用换到新工兵
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
        self._charge(head.type)  # P3：挂件重发入账
        flight["retries"] = flight.get("retries", 0) + 1
        flight["builder"] = parent.tag
        flight["frames"] = 0
        flight["seen_tags"] = self._type_entity_tags(gs, head.type)
        flight["expect_pos"] = self._expected_addon_reported(parent)
        flight["radius"] = 1.5
        return True

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
        """母建筑 → 挂件实体报告位置（真机锁定裸实验 docs/evidence/bare_addon.log）：
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
            return self._block(self._why(res, f"挂件 {head.type} 门控不通过（母建筑前置）"))
        parent = self._pick_parent_for_addon(gs, head.type)
        if parent is None:
            return self._block("无空闲母建筑可挂（在训练/已带挂件）")  # 等（母建筑被毁的僵局由上层维持规则
            # 自适应处理：reactors >= 现存 rax 即开训，不卡 4 台——真机教训）
        # 挂件是无目标能力：position=None，SC2 吸附到母建筑右下 2×2（真机教训：传点会被静默拒绝）
        self._emit(
            [Emission("build", [parent.tag], {"type": head.type, "position": None})],
            gs.seq,
        )
        self._charge(head.type)  # P3
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

    def _retry_build_gas(self, flight: dict, q_name: str, gs: GameState) -> bool:
        """气矿重试（P4）：换一个没试过的气井重发；候选耗尽才 drop。

        原来气矿会走 _resolve_placement —— 而气矿队列项没有 placement，于是第一次放置失败后
        直接以"build 缺 placement"丢弃精炼厂订单，即使还有空闲气井（真机上会连锁卡死整条 gas 链）。
        与挂件重试对称：候选集是"未试过且未被占的气井"，用 flight["attempted_geysers"] 排除。
        """
        head = flight["item"]
        tried = frozenset(flight.get("attempted_geysers") or ())
        res = check_gas(gs, self._catalog, head.type)
        geyser = self._pick_free_geyser(gs, exclude=tried) if res.ok else None
        if geyser is None and res.ok:
            self._drop(head, f"气矿放置失败：气井候选耗尽（已试 {sorted(tried)}）")
            self._release_flight(flight)
            return False
        builder = self._pick_builder(gs)
        if not res.ok or builder is None:
            # 资源不够 / 没工兵：保持 builder=None，下帧继续重试（原因进 flight，便于观测）
            flight["last_wait"] = self._why(res, "无空闲 SCV 可派去建气矿")
            return True
        self._emit(
            [Emission("build_gas", [builder.tag],
                      {"type": head.type, "target_unit": geyser.tag})],
            gs.seq,
        )
        self._charge(head.type)  # P3
        flight["builder"] = builder.tag
        self._reserve_for_flight(flight, builder.tag)
        flight["frames"] = 0
        flight["seen_tags"] = self._type_entity_tags(gs, head.type)
        flight["attempted_geysers"] = set(tried) | {geyser.tag}
        flight["expect_pos"] = geyser.position
        flight["radius"] = 3.0
        return True

    def _pick_free_geyser(self, gs: GameState, exclude: frozenset[int] = frozenset()):
        """找一个未被精炼厂占据的气井（仅主基锚点附近；真机教训：别把精炼厂建敌方气井上）。

        exclude = 本 flight 已经试过并失败的气井（P4：重试必须换井，否则永远撞同一个）。
        """
        buildings = []
        for u in gs.units:
            if u.owner is not Owner.SELF:
                continue
            e = self._catalog.by_burnysc2_name(u.type_name)
            if e is not None and e.size is not None:
                buildings.append(u)
        anchor = self._base_anchor()

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

        nodes = [u for u in gs.resources
                 if "GEYSER" in u.type_name and u.tag not in exclude]
        if anchor is not None:
            nodes = [u for u in nodes
                     if (u.position.x - anchor.x) ** 2 + (u.position.y - anchor.y) ** 2
                     <= NODE_RADIUS ** 2]
        return next((u for u in nodes if not _taken(u)), None)

    def _try_build_gas(self, head: QueueItem, q_name: str, gs: GameState) -> str:
        """气矿：SCV 把精炼厂建在空闲气井上（build_gas 动作，target = 气井 Unit）。"""
        res = check_gas(gs, self._catalog, head.type)
        if not res.ok:
            return self._block(self._why(res, f"气矿 {head.type} 门控不通过"))
        geyser = self._pick_free_geyser(gs)
        builder = self._pick_builder(gs)
        if geyser is None or builder is None:
            return self._block(
                "无空闲气井" if geyser is None else "无空闲 SCV 可派去建气矿"
            )
        self._emit(
            [Emission("build_gas", [builder.tag],
                      {"type": head.type, "target_unit": geyser.tag})],
            gs.seq,
        )
        self._charge(head.type)  # P3
        flight = {
            "item": head,
            "type": head.type,
            "builder": builder.tag,
            "frames": 0,
            "attempted": set(),
            "attempted_geysers": {geyser.tag},  # P4：重试要换井，不能重撞同一口
            "seen_tags": self._type_entity_tags(gs, head.type),
            "expect_pos": geyser.position,  # 精炼厂实体应出现在气井位置
            "radius": 3.0,
        }
        self._reserve_for_flight(flight, builder.tag)  # 气矿也是 SCV 在建，一样要征用
        self._build_flights.setdefault(q_name, []).append(flight)
        return "emitted"
