"""planner.curve：投影曲线数据模型（planner 输出）。

ProjectionPoint = 逐秒快照（矿/气/supply/工人/建筑/单位/在途数）；
ProjectionEvent = 完成/启动/卡 事件；ProjectionCurve = 点序列 + 事件 + 查询辅助。
对齐需求文档 S5：planner(GameState, production_sequence)→投影曲线。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ProjectionPoint:
    """逐秒快照（投影曲线的一个采样点）。"""

    t: float
    minerals: float
    gas: float
    supply_used: int
    supply_cap: int
    mineral_workers: int
    gas_workers: int
    buildings: dict[str, int]   # type -> 已建成数
    units: dict[str, int]       # type -> 已训成数（非 worker/建筑）
    in_flight_count: int        # 在建/在训项数


@dataclass(slots=True)
class ProjectionEvent:
    """完成/启动/卡 事件。"""

    kind: str                   # "completed" / "started" / "stalled"
    type: str | None            # 完成/启动的型；stalled 时为队首 op 的型/None
    t: float
    reason: str | None = None   # stalled 原因（缺矿/缺气/无槽/前置没）


@dataclass
class ProjectionCurve:
    """投影曲线：points（逐秒快照）+ events（完成/启动/卡）+ 查询辅助。

    simulate v2（PLAN-V2 批 3）附加面（**不进帧契约**，REST 端点/渲染层消费）：
    - `queue_status`：队列项执行账本（uid/status 四值/started_at/completed_at/reason，
      D8：horizon 没轮到 = pending，无 not_reached）；
    - `extras`：与 points 等长的派生量（workers 五分 + 产位 cap 明细）——
      ProjectionPoint 是契约面不加字段，派生量走这里；
    - `final_state`：结束时的 SimState（终值快照/产线明细/已完成升级用）。
    """

    points: list[ProjectionPoint] = field(default_factory=list)
    events: list[ProjectionEvent] = field(default_factory=list)
    queue_status: list[dict] = field(default_factory=list)
    extras: list[dict] = field(default_factory=list)
    final_state: object | None = None

    def time_to(self, predicate) -> float | None:
        """首个 predicate(point)==True 的 t；None=全程未满足。predicate: ProjectionPoint→bool。"""
        for p in self.points:
            if predicate(p):
                return p.t
        return None

    def peak_minerals(self) -> float:
        """峰值余矿（堆矿诊断）。"""
        return max((p.minerals for p in self.points), default=0.0)

    def stalls(self) -> list[ProjectionEvent]:
        """所有卡点事件。"""
        return [e for e in self.events if e.kind == "stalled"]
