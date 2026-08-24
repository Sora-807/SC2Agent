"""planner.planner：生产投影仿真主循环（对齐需求文档 S5）。

Planner.project(gs, seq, until) → ProjectionCurve：
从 GameState 快照派生 SimState，逐秒仿真 production_sequence 消费（可行性门控），输出资源/单位曲线。
无状态、纯函数、不模拟战斗（R4）、不依赖 flow 运行期。

仿真模型（V1）：
- 收入：mineral_workers×rate + gas_workers×rate（线性；P5 校准）。
- 在途：每秒 progress += 1；到 build_time 落成（建筑→buildings+1+supply_cap、单位→units+1；builder 回矿）。
- 队列：顺序消费，队首经 feasible 门控（资源/前置/产槽/builder），可行则启动、不可行则卡。
- worker 实时分配：Build 拉 idle 或矿工（builder 回矿）；AssignWorkers 拉 idle 或另一采集池。
"""
from __future__ import annotations

from game import GameState
from game.catalog import Catalog, Role

from planner.build_order import AssignWorkers, Build, Op, Research, Train, expand
from planner.curve import ProjectionCurve, ProjectionEvent
from planner.economy import DEFAULT_ECON, EconomyParams
from constraint.semantics import ExecView, VerdictKind, classify
from planner.sim_state import InFlight, SimState, derive_from
from planner.slots import is_tech_unit, reactor_map, slot_capacity, techlab_map

#: SC2 人口上限（真实规则）——「缺供给」等待与死局判定的分界（D7 删供给守卫后：
#: cap<200 时缺供给算等待——人可以插 depot；顶满 200 才是死局）
SUPPLY_MAX = 200


#: until_complete 的封顶（秒）：越过 until 之后最多再跑这么久 —— 队列死局时
#: 「永远仿真不到」的部分在此停住（1 小时足够任何真开局跑完）
COMPLETION_CAP = 3600.0


class Planner:
    """生产投影器：planner(GameState, production_sequence) → 投影曲线。"""

    def __init__(self, catalog: Catalog, econ: EconomyParams | None = None) -> None:
        self._catalog = catalog
        self._econ = econ or DEFAULT_ECON
        # B15 产槽模型：母建筑 → techlab 挂件 stable ID（挂件是独立建筑，从 buildings 计数）；
        # 科技单位集合（前置含本产建筑的 techlab，如坦克）也一并缓存
        self._techlab_of = techlab_map(catalog)
        self._reactor_of = reactor_map(catalog)
        self._tech_units = {e.stable_id for e in catalog.where() if is_tech_unit(e, catalog)}

    def project(self, gs: GameState, seq: list, until: float, *,
                until_complete: bool = False, tail: float = 0.0,
                initial=None) -> ProjectionCurve:
        """从 gs 快照 + production_sequence 投影到 until 秒。

        until_complete（2026-08-22 二十三轮用户拍板）：跑到**队列完成**为止 ——
        队列/在途还有货就继续越过 until，曲线不再在生产中途截断；死局（前置永建
        不出）会推到 COMPLETION_CAP 封顶为止（「永远仿真不到」的部分不无限跑）。
        live 投影（窗口语义）不传这个标志，行为不变。

        tail（2026-08-23 二十七轮用户拍板）：队列跑空后再多跑 N 秒 —— 曲线末端
        留一小段尾巴（最后一个事件完成后还能看到经济的余势），仿真范围与
        「最后事件 + 30s」对齐，前端右缘钳制才有个自然的数据末端。

        供给守卫（auto_supply）已删（PLAN-V2 D7）：投影不替人补供给 —— 卡人口
        真实浮出，由警报层 supply_capped + audit_queue 给「插 depot before_uid」
        的建议，一切尽可能手动。

        initial（PLAN-V2 批 3）：给定 SimState 直接当起点（initial-state 文档/
        会话导出快照），跳过 derive_from(gs) —— gs 仍要给（seq 语义/签名兼容），
        但状态以 initial 为准。
        """
        st = initial if initial is not None else derive_from(gs, self._catalog)
        queue = expand(seq) if seq else []
        curve = ProjectionCurve()
        stalled_recorded: set[int] = set()   # 已记卡点事件的 op id（每 op 只记首次卡）
        hard_cap = until + COMPLETION_CAP
        done_at: float | None = None         # 队列+在途首次双双清空的时刻（tail 起点）
        # 执行账本（批 3）：uid → {status/started_at/completed_at/reason}。
        # count 展开的多个 Op 共享 uid —— 首个开工记 started，全部完工记 completed。
        ledger: dict[str, dict] = {}
        dead_head: dict[str, str] = {}       # uid → 死局原因（映射后的闭集 key+detail）
        for op in queue:
            if getattr(op, "uid", None) and op.uid not in ledger:
                ledger[op.uid] = {"uid": op.uid, "status": "pending",
                                  "started_at": None, "completed_at": None,
                                  "reason": None, "detail": None,
                                  "item": self._op_label(op)}
        while True:
            if not (st.t < until
                    or (until_complete and st.t < hard_cap
                        and (queue or st.in_flight
                             # tail：队列跑空后再留 N 秒经济的余势（二十七轮）
                             or (done_at is not None and st.t < done_at + tail)))):
                break
            # 1. 收入（气收入按精炼厂数量封顶：3 工/精炼厂；无精炼厂→气工空转 0 收入）
            st.minerals += st.mineral_workers * self._econ.mineral_per_scv_per_sec
            refineries = st.buildings.get("terran/refinery", 0)
            effective_gas = min(st.gas_workers, refineries * 3)
            st.gas += effective_gas * self._econ.gas_per_scv_per_sec
            # 2. 推进在途 —— train 按产槽排队（I10）：同产建筑每秒最多推进
            #    normal_cap+tech_cap 条（进度最高的先走 = 正在训的先完成、排队的原地等），
            #    与 B15 的队列侧槽模型 / WorldSim 的产槽语义三方一致。
            self._advance_in_flight(st)
            # 3. 落成
            done = [f for f in st.in_flight if f.progress >= f.build_time]
            if done:
                self._apply_completed(st, done, curve, ledger, queue)
                st.in_flight = [f for f in st.in_flight if f.progress < f.build_time]
            # 4. 消费队列（可行性门控）
            while queue:
                ok, reason, wait, skip_key = self._feasible(queue[0], st)
                if not ok:
                    # 等待（攒钱/排队/等在途落成）是顺序执行的正常时序，不是警报；
                    # 只有死局（前置永远建不出 / 供给顶到上限 / 收入永远为 0）才记 stalled。
                    if not wait and id(queue[0]) not in stalled_recorded:
                        curve.events.append(ProjectionEvent(
                            "stalled", self._op_label(queue[0]), st.t, reason))
                        stalled_recorded.add(id(queue[0]))
                        # 账本（批 6 清偿②）：skip_key 来自 classify 闭集直通 ——
                        # 规划器本地死局（无收入/顶满200）没有 skip_key → 项留 pending
                        uid = getattr(queue[0], "uid", None)
                        if skip_key is not None and uid is not None and uid not in dead_head:
                            dead_head[uid] = skip_key
                            ledger[uid]["detail"] = reason
                    break
                stalled_recorded.discard(id(queue[0]))
                uid = getattr(queue[0], "uid", None)
                if uid is not None and ledger[uid]["started_at"] is None:
                    ledger[uid]["started_at"] = st.t
                self._start(queue[0], st)
                curve.events.append(ProjectionEvent("started", self._op_label(queue[0]), st.t, None))
                # assign_workers 是瞬时动作（_start 直接改状态）：当场完成
                if isinstance(queue[0], AssignWorkers) and uid is not None:
                    ledger[uid]["completed_at"] = st.t
                queue.pop(0)
            # 5. 记快照 + 派生量（extras 与 points 等长；契约面 ProjectionPoint 不加字段）
            curve.points.append(st.to_point())
            curve.extras.append(self._point_extra(st))
            st.t += 1
            # tail 的记账点：本帧结束后队列与在途都空了 → 这是「最后一个事件完成」时刻
            if until_complete and done_at is None and not queue and not st.in_flight:
                done_at = st.t
        curve.final_state = st
        self._settle_ledger(ledger, queue, st, dead_head)
        curve.queue_status = list(ledger.values())
        return curve

    # ---- 可行性门控（资源/前置/产槽/builder）----
    #
    # 返回 (ok, reason, wait)：`wait=True` 表示**时序等待**（攒钱 / 产槽排队 /
    # 前置或收入在途）——顺序队列的常态，不构成警报；只有 `wait=False` 的**死局**
    # 才会被主循环记成 stalled 事件（真前瞻警报）。判据只看当前 SimState：
    # 顺序语义下，排在本项后面的 op 永远救不了卡住的队首。

    def _advance_in_flight(self, st: SimState) -> None:
        """在途推进（每仿真秒 progress+1），train 侧带产槽排队。

        旧版所有 in_flight 并行 +1 —— 快照里同产建筑的**排队中**单位会在同一秒
        一起"完成"（90 秒夹具的泳道因此堆出矿量撑不住的假并行，ISSUES I10）。
        排队判定与 _feasible 共用 B15 的 slot_capacity：同一产建筑每秒最多
        normal_cap+tech_cap 条在推进，取进度最高的先走。build 与无 producer
        的在途照旧并行（多建筑同时盖不需要 builder 槽语义）。
        """
        used: dict[str, int] = {}
        order = sorted(range(len(st.in_flight)), key=lambda i: -st.in_flight[i].progress)
        for i in order:
            f = st.in_flight[i]
            if f.kind != "train" or not f.producer:
                f.progress += 1
                continue
            techlab_sid = self._techlab_of.get(f.producer)
            normal_cap, tech_cap = slot_capacity(
                f.producer, st.buildings, st.addons, techlab_sid)
            cap = max(1, normal_cap + tech_cap)
            n = used.get(f.producer, 0)
            if n >= cap:
                continue
            used[f.producer] = n + 1
            f.progress += 1

    def _feasible(self, op: Op, st: SimState) -> tuple[bool, str | None, bool, str | None]:
        """可行性门控（批 6 清偿②）：**classify 单点** + 规划器时序精化。

        返回 (ok, reason, wait, skip_key)：
        - skip_key 非 None = classify 判 skip（闭集 prereq_missing/placement_collision）
          —— 账本直接记 skipped；
        - 规划器本地死局（矿/气无收入、供给顶满 200、无 builder）只发 stalled 事件
          （wait=False），**不**给 skip_key —— 项按 D8 留 pending（矿/气/人口缺 = 等待）。
        """
        from planner.build_order import AssignWorkers, Build, Research, Train
        from game.production import QueueOp as _QO

        if isinstance(op, Research):
            # classify 不覆盖 research（产出建筑=研究槽语义不同）—— 本地分支
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}", False, None)
            if st.minerals < e.cost.minerals:
                return (False, "缺矿", self._income_coming(st), None)
            if st.gas < e.cost.vespene:
                return (False, "缺气", self._gas_coming(st), None)
            for p_ in e.prerequisites:
                if st.buildings.get(p_, 0) < 1:
                    wait = any(f.type == p_ for f in st.in_flight)
                    return (False, "前置没", wait, None)
            if e.produced_by:
                producers = st.buildings.get(e.produced_by, 0)
                busy = sum(1 for f in st.in_flight
                           if f.kind == "research" and f.producer == e.produced_by)
                if producers - busy <= 0:
                    wait = producers > 0 or any(f.type == e.produced_by
                                                for f in st.in_flight)
                    return (False, "无研究槽", wait, None)
            return (True, None, False, None)

        class _Item:   # planner Op → classify 的 QueueItem 形态 shim
            pass

        it = _Item()
        it.op = (_QO.BUILD if isinstance(op, Build)
                 else _QO.TRAIN if isinstance(op, Train) else _QO.ASSIGN_WORKERS)
        it.type = getattr(op, "type", None)
        it.count = 1
        it.placement = None
        it.task = getattr(op, "task", None)
        v = classify(it, self._catalog, self._exec_view(op, st))
        if v.kind is VerdictKind.SKIP:
            # reason 带「前置没」前缀：alerts.from_curve 按这个词过滤 stalled 事件
            return (False, f"前置没（{v.detail or v.reason}）", False, v.reason)
        if v.kind is VerdictKind.PENDING:
            r = v.reason or ""
            if "晶体矿不足" in r:                      # 规划器精化：收入还会不会来
                return (False, "缺矿", self._income_coming(st), None)
            if "高能瓦斯不足" in r:
                return (False, "缺气", self._gas_coming(st), None)
            if "供给不足" in r:                        # 顶满 200：等待变死局事件
                return (False, "缺供给", st.supply_cap < 200, None)
            if "等前置" in r or "等产出建筑" in r:
                return (False, "前置没", True, None)
            if "production_capacity" in r or "训练槽" in r:
                return (False, "无产槽", True, None)
            return (False, r, True, None)
        # classify 判 ready —— 规划器本地还有两类检查（classify 看不见 builder 与
        # 科技槽特化：free_producers 适配层已算「该单位能用的槽」，builder 是 sim 特有）
        if isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            if e is not None and "addon" not in e.capabilities \
                    and st.idle_workers < 1 and st.mineral_workers < 1:
                return (False, "无builder", self._income_coming(st), None)
        return (True, None, False, None)

    def _exec_view(self, op: Op, st: SimState) -> ExecView:
        """SimState → classify 的 ExecView（批 6 清偿②的适配层）。

        - queued_types = ∅：仿真顺序消费，队首之前的 op 已解析（在场或在途）——
          与 runtime 的账本前序语义一致（分类器词表不降级）；
        - free_producers 只算当前 op 的产出建筑，且**按单位特化**：科技单位要
          空闲科技槽，普通单位普通槽满可溢到科技槽（B15 同款槽模型）。
        """
        producer = None
        op_type = getattr(op, "type", None)
        entry = self._catalog.by_stable_id(op_type) if op_type else None
        if isinstance(op, Train) and entry is not None:
            producer = entry.produced_by
        free: frozenset[str] = frozenset()
        if producer:
            techlab_sid = self._techlab_of.get(producer)
            normal_cap, tech_cap = slot_capacity(producer, st.buildings,
                                                 st.addons, techlab_sid)
            busy_n = busy_t = 0
            for f in st.in_flight:
                if f.kind != "train" or f.producer != producer:
                    continue
                if f.type in self._tech_units:
                    busy_t += 1
                else:
                    busy_n += 1
            # 槽占用语义与旧 _feasible/B15 三方一致：普通单位可用任意空槽
            # （总占用 < 总槽）；科技单位只认科技槽（普通溢出也占科技槽）
            is_tech = op_type in self._tech_units
            free_normal = busy_n + busy_t < normal_cap + tech_cap
            tech_taken = busy_t + max(0, busy_n - normal_cap)
            free_tech = tech_cap - tech_taken > 0
            if (free_tech if is_tech else free_normal):
                free = frozenset({producer})
        return ExecView(
            minerals=st.minerals, vespene=st.gas,
            supply_used=st.supply_used, supply_cap=st.supply_cap,
            ready_types=frozenset(st.buildings.keys()),
            inflight_types=frozenset(f.type for f in st.in_flight),
            queued_types=frozenset(),
            free_producers=free,
        )

    # ---- 等待/死局判据 ----

    def _income_coming(self, st: SimState) -> bool:
        """矿收入还会来吗：有矿工，或有 SCV 在训（训成自动进矿池）。"""
        if st.mineral_workers > 0:
            return True
        return any(
            f.kind == "train" and (e := self._catalog.by_stable_id(f.type)) is not None
            and e.role == Role.WORKER
            for f in st.in_flight)

    def _gas_coming(self, st: SimState) -> bool:
        """气收入还会来吗：有气工且（已有精炼厂或精炼厂在建）。

        只建精炼厂不派工、或只派工没有厂，气都永远为 0 —— 那是规划缺一步，算死局。
        """
        if st.gas_workers <= 0:
            return False
        return (st.buildings.get("terran/refinery", 0) > 0
                or any(f.type == "terran/refinery" for f in st.in_flight))

    # ---- 启动 op（扣资源/加在途/分配 worker）----
    def _start(self, op: Op, st: SimState) -> None:
        if isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
            if e is not None and "addon" not in e.capabilities:
                # 常规建筑：拉 SCV 建造（挂件由母建筑自建，不拉 SCV）
                if st.idle_workers >= 1:
                    st.idle_workers -= 1
                else:
                    st.mineral_workers -= 1
            st.in_flight.append(InFlight(op.type, "build", e.build_time, 0.0, None,
                                          uid=op.uid))
        elif isinstance(op, Train):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
            st.supply_used += e.cost.supply
            st.in_flight.append(InFlight(op.type, "train", e.build_time, 0.0, e.produced_by,
                                          uid=op.uid))
        elif isinstance(op, Research):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
            st.in_flight.append(InFlight(op.type, "research", e.build_time, 0.0, e.produced_by,
                                          uid=op.uid))
        elif isinstance(op, AssignWorkers):
            # **目标值语义**（ADR-0030 D2）：维持 count 个，不是"再派 count 个"。
            # 运行时的 assign_workers 已经是目标值（队列项 = 写目标，D2.2），
            # planner 若还用 delta 语义，投影就和真实行为不一致 —— 那种不一致会让投影不可信，
            # 而投影不可信等于警报也不可信（AlertService 的卡人口/缺前置都从它来）。
            cur = st.gas_workers if op.task == "gas" else st.mineral_workers
            other = st.mineral_workers if op.task == "gas" else st.gas_workers
            delta = op.count - cur                     # 差多少补多少；超了就退回去
            if delta > 0:
                move = min(delta, st.idle_workers + other)
                from_idle = min(st.idle_workers, move)
                st.idle_workers -= from_idle
                rest = move - from_idle                # 不够就从另一采集池拉
                if op.task == "gas":
                    st.mineral_workers -= rest
                    st.gas_workers += move
                else:
                    st.gas_workers -= rest
                    st.mineral_workers += move
            elif delta < 0:
                give_back = min(-delta, cur)           # 多出来的转到另一池（不凭空消失）
                if op.task == "gas":
                    st.gas_workers -= give_back
                    st.mineral_workers += give_back
                else:
                    st.mineral_workers -= give_back
                    st.gas_workers += give_back

    def _apply_completed(self, st: SimState, done: list[InFlight], curve: ProjectionCurve,
                         ledger: dict | None = None, queue: list | None = None) -> None:
        for f in done:
            if f.kind == "build":
                e = self._catalog.by_stable_id(f.type)
                is_addon = e is not None and "addon" in e.capabilities
                if is_addon and e is not None and e.produced_by:
                    # 挂件落成：反应堆给母建筑 +1 训练槽（techlab 不加槽但已计 buildings）
                    st.buildings[f.type] = st.buildings.get(f.type, 0) + 1
                    if "BUILD_REACTOR" in (e.build_ability or ""):
                        st.addons[e.produced_by] = st.addons.get(e.produced_by, 0) + 1
                else:
                    st.buildings[f.type] = st.buildings.get(f.type, 0) + 1
                    # SC2 人口上限 200（真实规则）：顶满后再建 depot/CC 也不涨 ——
                    # 这是「缺供给」从等待变死局的分界（用户拍板的真警报）
                    st.supply_cap = min(
                        SUPPLY_MAX, st.supply_cap + self._econ.supply_provided.get(f.type, 0))
                    st.mineral_workers += 1   # builder 回矿
            elif f.kind == "research":
                st.research_completed.add(f.type)
            else:  # train
                st.units[f.type] = st.units.get(f.type, 0) + 1
                # 新训练的 worker 自动去采矿（匹配真机 steward 行为）
                e = self._catalog.by_stable_id(f.type)
                if e is not None and e.role == Role.WORKER:
                    st.mineral_workers += 1
                    st.total_workers += 1
            curve.events.append(ProjectionEvent("completed", f.type, st.t, None))
        # 账本：uid 的全部在途都完工 → completed（count 展开的多 Op 归并）
        if ledger is None:
            return
        # 本秒完工的 flight 还留在 st.in_flight 里（过滤在调用侧）——按对象身份剔除；
        # 同 uid 多个在途（count 展开）只要还有活口就不算 completed
        done_ids = {id(f) for f in done}
        alive = {getattr(g, "uid", None) for g in st.in_flight
                 if id(g) not in done_ids}
        # count 展开的同 uid op 可能还有没开工的留在队列（逐个训练有间隙）——
        # 队列里还有同 uid = 没完事（保守至多晚 1 秒，完成时刻仍准确）
        remaining = {getattr(op, "uid", None) for op in (queue or [])}
        for f in done:
            fid = getattr(f, "uid", None)
            if fid is not None and fid not in alive and fid not in remaining:
                ledger[fid]["completed_at"] = st.t

    # ---- simulate v2（批 3）：账本终态 + 采样派生 ----

    def _settle_ledger(self, ledger: dict, queue: list, st, dead_head: dict) -> None:
        """仿真结束后的账本终态。

        剩余队列按序：completed（count 部分已完工）> in_progress（还在途）>
        队首判死（dead_head → skipped + 闭集 reason；资源类等待被截断 → pending）；
        其后全部 pending（D8：horizon 没轮到 = pending，无 not_reached）。
        不在剩余队列的项（已开工出队）：completed 或 in_progress（tail 段在途）。
        """
        inflight_uids = {getattr(f, "uid", None) for f in st.in_flight}
        head_settled = False
        for op in queue:
            uid = getattr(op, "uid", None)
            if uid is None:
                continue
            rec = ledger[uid]
            if rec["status"] != "pending":
                continue     # count 展开的同 uid op 只结算一次（别被后位覆写）
            if rec["completed_at"] is not None:
                rec["status"] = "completed"
            elif uid in inflight_uids:
                rec["status"] = "in_progress"
            elif not head_settled:
                head_settled = True
                if uid in dead_head:
                    rec["status"] = "skipped"
                    rec["reason"] = dead_head[uid]
            else:
                rec["status"] = "pending"
        for uid, rec in ledger.items():
            if rec["status"] != "pending" or rec["started_at"] is None:
                continue
            if rec["completed_at"] is not None:
                rec["status"] = "completed"
            elif uid in inflight_uids:
                rec["status"] = "in_progress"

    def _point_extra(self, st) -> dict:
        """采样派生量（extras 与 points 等长）：工人五分 + 产位 cap 明细。

        building = 在途建造数派生（SimState 无建造工单列账）；scouting 恒 0
        （D3：侦查是编组派生，批 5 显示面）；产位用 slot_capacity 同一套槽模型。
        """
        building_workers = sum(1 for f in st.in_flight if f.kind == "build")
        producers: dict[str, dict] = {}
        for sid, n in st.buildings.items():
            entry = self._catalog.by_stable_id(sid)
            if entry is None or "train" not in entry.capabilities or n <= 0:
                continue
            techlab_sid = self._techlab_of.get(sid)
            reactor_sid = self._reactor_of.get(sid)
            normal_cap, tech_cap = slot_capacity(sid, st.buildings, st.addons, techlab_sid)
            busy_n = busy_t = 0
            for f in st.in_flight:
                if f.kind != "train" or f.producer != sid:
                    continue
                if f.type in self._tech_units:
                    busy_t += 1
                else:
                    busy_n += 1
            tech = techlab_sid and st.buildings.get(techlab_sid, 0) or 0
            reac = reactor_sid and st.buildings.get(reactor_sid, 0) or 0
            producers[sid] = {
                "normal_cap": normal_cap, "tech_cap": tech_cap,
                "normal_busy": busy_n, "tech_busy": busy_t,
                "total": n, "bare": max(0, n - tech - reac),
                "with_reactor": reac, "with_techlab": tech,
                "building_addon": sum(
                    1 for f in st.in_flight
                    if f.kind == "build" and f.type in (techlab_sid, reactor_sid)),
            }
        return {
            "workers": {
                "mineral": st.mineral_workers, "gas": st.gas_workers,
                "building": building_workers, "scouting": 0, "idle": st.idle_workers,
            },
            "producers": producers,
        }

    @staticmethod
    def _op_label(op: Op) -> str | None:
        if isinstance(op, (Build, Train, Research)):
            return op.type
        if isinstance(op, AssignWorkers):
            return f"assign_{op.task}"
        return None
