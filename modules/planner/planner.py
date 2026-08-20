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


class Planner:
    """生产投影器：planner(GameState, production_sequence) → 投影曲线。"""

    def __init__(self, catalog: Catalog, econ: EconomyParams | None = None) -> None:
        self._catalog = catalog
        self._econ = econ or DEFAULT_ECON

    def project(self, gs: GameState, seq: list, until: float) -> ProjectionCurve:
        """从 gs 快照 + production_sequence 投影到 until 秒。"""
        st = derive_from(gs, self._catalog)
        queue = expand(seq) if seq else []
        curve = ProjectionCurve()
        stalled_recorded: set[int] = set()   # 已记卡点事件的 op id（每 op 只记首次卡）
        while st.t < until:
            # 1. 收入（气收入按精炼厂数量封顶：3 工/精炼厂；无精炼厂→气工空转 0 收入）
            st.minerals += st.mineral_workers * self._econ.mineral_per_scv_per_sec
            refineries = st.buildings.get("terran/refinery", 0)
            effective_gas = min(st.gas_workers, refineries * 3)
            st.gas += effective_gas * self._econ.gas_per_scv_per_sec
            # 2. 推进在途
            for f in st.in_flight:
                f.progress += 1
            # 3. 落成
            done = [f for f in st.in_flight if f.progress >= f.build_time]
            if done:
                self._apply_completed(st, done, curve)
                st.in_flight = [f for f in st.in_flight if f.progress < f.build_time]
            # 3b. 供给守卫：队首 Train 即将卡人口 → 自动插入补给站（尽可能晚）
            self._supply_guard(queue, st, curve)
            # 4. 消费队列（可行性门控）
            while queue:
                ok, reason = self._feasible(queue[0], st)
                if not ok:
                    if id(queue[0]) not in stalled_recorded:
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
        return curve

    # ---- 供给守卫：队首即将卡人口 / 缺 depot 前置 → 自动插补给站（尽可能晚插入）----
    def _supply_guard(self, queue: list, st: SimState, curve: ProjectionCurve) -> None:
        """两种触发条件（都只在无在途 depot 时插，避免重复）：

        1) Train 即将卡人口：supply_used + supply_cost > supply_cap → 插 depot（尽可能晚）
        2) Build 前置含 supplydepot 但未建：插 depot（barracks/factory 等需 depot 前置）
        depot 自身不消耗 supply（cost.supply=0），不会自激递归。
        """
        if not queue:
            return
        op = queue[0]
        need_inject = False
        if isinstance(op, Train):
            e = self._catalog.by_stable_id(op.type)
            if e is not None and e.cost.supply > 0:
                if st.supply_used + e.cost.supply > st.supply_cap and st.supply_cap < 200:
                    need_inject = True
        elif isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            if e is not None and "terran/supplydepot" in e.prerequisites:
                if st.buildings.get("terran/supplydepot", 0) < 1:
                    need_inject = True
        if not need_inject:
            return
        if any(f.type == "terran/supplydepot" for f in st.in_flight):
            return  # 已有在途 depot → 等
        depot_entry = self._catalog.by_stable_id("terran/supplydepot")
        if depot_entry is None or st.minerals < depot_entry.cost.minerals:
            return  # 攒矿后下帧再插
        queue.insert(0, Build("terran/supplydepot"))
        curve.events.append(ProjectionEvent("started", "terran/supplydepot", st.t, "auto-supply"))

    # ---- 可行性门控（资源/前置/产槽/builder）----
    def _feasible(self, op: Op, st: SimState) -> tuple[bool, str | None]:
        if isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}")
            if st.minerals < e.cost.minerals:
                return (False, "缺矿")
            if st.gas < e.cost.vespene:
                return (False, "缺气")
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    return (False, "前置没")
            if st.idle_workers < 1 and st.mineral_workers < 1:
                return (False, "无builder")
            return (True, None)
        if isinstance(op, Train):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}")
            if st.minerals < e.cost.minerals:
                return (False, "缺矿")
            if st.gas < e.cost.vespene:
                return (False, "缺气")
            if st.supply_used + e.cost.supply > st.supply_cap:
                return (False, "缺供给")
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    return (False, "前置没")
            if e.produced_by:
                producers = st.buildings.get(e.produced_by, 0)
                reactor_bonus = st.addons.get(e.produced_by, 0)
                busy = sum(1 for f in st.in_flight
                           if f.kind == "train" and f.producer == e.produced_by)
                if producers + reactor_bonus - busy <= 0:
                    return (False, "无产槽")
            return (True, None)
        if isinstance(op, Research):
            e = self._catalog.by_stable_id(op.type)
            if e is None:
                return (False, f"未知型 {op.type}")
            if st.minerals < e.cost.minerals:
                return (False, "缺矿")
            if st.gas < e.cost.vespene:
                return (False, "缺气")
            for p in e.prerequisites:
                if st.buildings.get(p, 0) < 1:
                    return (False, "前置没")
            if e.produced_by:
                producers = st.buildings.get(e.produced_by, 0)
                busy = sum(1 for f in st.in_flight
                           if f.kind == "research" and f.producer == e.produced_by)
                if producers - busy <= 0:
                    return (False, "无研究槽")
            return (True, None)
        if isinstance(op, AssignWorkers):
            # best-effort：有多少派多少，不卡（真机 steward 也是自适应不卡）
            return (True, None)
        return (False, "未知 op")

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
                    st.supply_cap += self._econ.supply_provided.get(f.type, 0)
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
