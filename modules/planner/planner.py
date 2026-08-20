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
from game.catalog import Catalog

from planner.build_order import AssignWorkers, Build, Op, Train, expand
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
            # 1. 收入
            st.minerals += st.mineral_workers * self._econ.mineral_per_scv_per_sec
            st.gas += st.gas_workers * self._econ.gas_per_scv_per_sec
            # 2. 推进在途
            for f in st.in_flight:
                f.progress += 1
            # 3. 落成
            done = [f for f in st.in_flight if f.progress >= f.build_time]
            if done:
                self._apply_completed(st, done, curve)
                st.in_flight = [f for f in st.in_flight if f.progress < f.build_time]
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
                busy = sum(1 for f in st.in_flight
                           if f.kind == "train" and f.producer == e.produced_by)
                if producers - busy <= 0:
                    return (False, "无产槽")
            return (True, None)
        if isinstance(op, AssignWorkers):
            other = st.mineral_workers if op.task == "gas" else st.gas_workers
            if st.idle_workers + other < op.count:
                return (False, "无idle")
            return (True, None)
        return (False, "未知 op")

    # ---- 启动 op（扣资源/加在途/分配 worker）----
    def _start(self, op: Op, st: SimState) -> None:
        if isinstance(op, Build):
            e = self._catalog.by_stable_id(op.type)
            st.minerals -= e.cost.minerals
            st.gas -= e.cost.vespene
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
        elif isinstance(op, AssignWorkers):
            need = op.count
            from_idle = min(st.idle_workers, need)
            st.idle_workers -= from_idle
            need -= from_idle
            if op.task == "gas":
                st.mineral_workers -= need
                st.gas_workers += op.count
            else:
                st.gas_workers -= need
                st.mineral_workers += op.count

    def _apply_completed(self, st: SimState, done: list[InFlight], curve: ProjectionCurve) -> None:
        for f in done:
            if f.kind == "build":
                st.buildings[f.type] = st.buildings.get(f.type, 0) + 1
                st.supply_cap += self._econ.supply_provided.get(f.type, 0)
                st.mineral_workers += 1   # builder 回矿
            else:  # train
                st.units[f.type] = st.units.get(f.type, 0) + 1
            curve.events.append(ProjectionEvent("completed", f.type, st.t, None))

    @staticmethod
    def _op_label(op: Op) -> str | None:
        if isinstance(op, (Build, Train)):
            return op.type
        if isinstance(op, AssignWorkers):
            return f"assign_{op.task}"
        return None
