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
                until_complete: bool = False, tail: float = 0.0) -> ProjectionCurve:
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
        """
        st = derive_from(gs, self._catalog)
        queue = expand(seq) if seq else []
        curve = ProjectionCurve()
        stalled_recorded: set[int] = set()   # 已记卡点事件的 op id（每 op 只记首次卡）
        hard_cap = until + COMPLETION_CAP
        done_at: float | None = None         # 队列+在途首次双双清空的时刻（tail 起点）
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
                self._apply_completed(st, done, curve)
                st.in_flight = [f for f in st.in_flight if f.progress < f.build_time]
            # 4. 消费队列（可行性门控）
            while queue:
                ok, reason, wait = self._feasible(queue[0], st)
                if not ok:
                    # 等待（攒钱/排队/等在途落成）是顺序执行的正常时序，不是警报；
                    # 只有死局（前置永远建不出 / 供给顶到上限 / 收入永远为 0）才记 stalled。
                    if not wait and id(queue[0]) not in stalled_recorded:
                        curve.events.append(ProjectionEvent(
                            "stalled", self._op_label(queue[0]), st.t, reason))
                        stalled_recorded.add(id(queue[0]))
                    break
                stalled_recorded.discard(id(queue[0]))
                self._start(queue[0], st)
                curve.events.append(ProjectionEvent("started", self._op_label(queue[0]), st.t, None))
                queue.pop(0)
            # 5. 记快照
            curve.points.append(st.to_point())
            st.t += 1
            # tail 的记账点：本帧结束后队列与在途都空了 → 这是「最后一个事件完成」时刻
            if until_complete and done_at is None and not queue and not st.in_flight:
                done_at = st.t
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

    def _feasible(self, op: Op, st: SimState) -> tuple[bool, str | None, bool]:
        if isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}", False)
            if st.minerals < e.cost.minerals:
                return (False, "缺矿", self._income_coming(st))
            if st.gas < e.cost.vespene:
                return (False, "缺气", self._gas_coming(st))
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    in_flight = any(f.type == p for f in st.in_flight)
                    return (False, "前置没", in_flight)
            if st.idle_workers < 1 and st.mineral_workers < 1:
                return (False, "无builder", self._income_coming(st))
            return (True, None, False)
        if isinstance(op, Train):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}", False)
            if st.minerals < e.cost.minerals:
                return (False, "缺矿", self._income_coming(st))
            if st.gas < e.cost.vespene:
                return (False, "缺气", self._gas_coming(st))
            if st.supply_used + e.cost.supply > st.supply_cap:
                # 供给守卫已删（D7）：cap<200 时缺供给算等待（人可以插 depot ——
                # 警报层 supply_capped 给建议）；顶满 200 才是死局
                return (False, "缺供给", st.supply_cap < 200)
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    in_flight = any(f.type == p for f in st.in_flight)
                    return (False, "前置没", in_flight)
            if e.produced_by:
                # B15 产槽模型：科技单位（前置含本产建筑的 techlab）只能用科技槽；
                # 普通单位优先普通槽（reactor 双倍），普通满后溢出到空闲科技槽。
                # 旧代码只数「母建筑数 − busy」——techlab 母建筑被当普通槽，
                # 坦克在普通工厂上也能"同时开工"。
                techlab_sid = self._techlab_of.get(e.produced_by)
                normal_cap, tech_cap = slot_capacity(
                    e.produced_by, st.buildings, st.addons, techlab_sid)
                # 在产分桶：普通单位溢出到科技槽时占的是科技槽 —— 不追踪具体槽位，
                # 用「溢出量 = max(0, bn − normal_cap)」推导科技槽占用（贪心：普通单位
                # 先占满普通槽才溢出，与用户拍板的优先序一致）。
                busy_tech = 0
                busy_normal = 0
                for f in st.in_flight:
                    if f.kind != "train" or f.producer != e.produced_by:
                        continue
                    if f.type in self._tech_units:
                        busy_tech += 1
                    else:
                        busy_normal += 1
                if e.stable_id in self._tech_units:
                    tech_taken = busy_tech + max(0, busy_normal - normal_cap)
                    if tech_cap - tech_taken <= 0:
                        # 槽全忙 = 排队等待；槽为 0 且没有 techlab 在建 = 死局
                        wait = tech_cap > 0 or (
                            techlab_sid is not None
                            and any(f.type == techlab_sid for f in st.in_flight))
                        return (False, "无科技槽", wait)
                elif busy_normal + busy_tech >= normal_cap + tech_cap:
                    # 普通单位任何空槽都能用（普通 + 溢出），总占用满才算无产槽
                    reactor_sid = self._reactor_of.get(e.produced_by)
                    wait = (normal_cap + tech_cap) > 0 or any(
                        f.type == e.produced_by
                        or (reactor_sid is not None and f.type == reactor_sid)
                        for f in st.in_flight)
                    return (False, "无产槽", wait)
            return (True, None, False)
        if isinstance(op, Research):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}", False)
            if st.minerals < e.cost.minerals:
                return (False, "缺矿", self._income_coming(st))
            if st.gas < e.cost.vespene:
                return (False, "缺气", self._gas_coming(st))
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    in_flight = any(f.type == p for f in st.in_flight)
                    return (False, "前置没", in_flight)
            if e.produced_by:
                producers = st.buildings.get(e.produced_by, 0)
                busy = sum(1 for f in st.in_flight
                           if f.kind == "research" and f.producer == e.produced_by)
                if producers - busy <= 0:
                    wait = producers > 0 or any(
                        f.type == e.produced_by for f in st.in_flight)
                    return (False, "无研究槽", wait)
            return (True, None, False)
        if isinstance(op, AssignWorkers):
            # best-effort：有多少派多少，不卡（真机 steward 也是自适应不卡）
            return (True, None, False)
        return (False, "未知 op", False)

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
            st.in_flight.append(InFlight(op.type, "build", e.build_time, 0.0, None))
        elif isinstance(op, Train):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
            st.supply_used += e.cost.supply
            st.in_flight.append(InFlight(op.type, "train", e.build_time, 0.0, e.produced_by))
        elif isinstance(op, Research):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
            st.in_flight.append(InFlight(op.type, "research", e.build_time, 0.0, e.produced_by))
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

    def _apply_completed(self, st: SimState, done: list[InFlight], curve: ProjectionCurve) -> None:
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

    @staticmethod
    def _op_label(op: Op) -> str | None:
        if isinstance(op, (Build, Train, Research)):
            return op.type
        if isinstance(op, AssignWorkers):
            return f"assign_{op.task}"
        return None
