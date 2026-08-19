"""production.worker：WorkerAllocator —— assign_workers(task,count) 展开成 gather/stop 级 Emission。

- 饱和上限：矿脉 2、气井 3（P0 生产模块安排）；饱和度从 gs 派生：
  正在采集该节点的 SCV 数（orders 里 target_tag == 节点 tag），无内部状态 → 重启/换帧一致。
- mineral：矿脉轮流补到 2；gas：气井轮流补到 3；idle：正在采的 SCV → stop 解放。
- 选 SCV：catalog role=worker 的类型（不写死 "SCV"）；优先无命令的空闲工兵。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import GameState, Owner, WorkerTask
from game.catalog import Catalog

MINERAL_SATURATION = 2  # 每个矿脉的采集上限（P0）
GAS_SATURATION = 3  # 每个气井的采集上限（P0）


@dataclass(frozen=True, slots=True)
class Emission:
    """一次待发的操作意图（生产运行时负责配 op_id 转成 Operation）。"""

    action: str  # "gather" / "stop"
    unit_tags: list[int]
    params: dict = field(default_factory=dict)


class WorkerAllocator:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def _worker_names(self) -> frozenset[str]:
        return frozenset(e.burnysc2_name for e in self._catalog.where(role="worker"))

    def _workers(self, gs: GameState) -> list:
        names = self._worker_names()
        return [u for u in gs.units if u.owner is Owner.SELF and u.type_name in names]

    def _nodes(self, gs: GameState, gas: bool) -> list:
        """资源节点：gas=True 取气井，否则取矿脉（来自 world 拆出的 gs.resources）。

        气井只取已建精炼厂的（裸气井不能采）；判定 = 气井 2.5 距离内有己方建筑。
        """
        if not gas:
            return [u for u in gs.resources if u.type_name.startswith("MINERALFIELD")]
        buildings = []
        for u in gs.units:
            if u.owner is not Owner.SELF:
                continue
            e = self._catalog.by_burnysc2_name(u.type_name)
            if e is not None and e.size is not None:
                buildings.append(u)

        def _has_refinery(geyser) -> bool:
            return any(
                (b.position.x - geyser.position.x) ** 2 + (b.position.y - geyser.position.y) ** 2 < 6.25
                for b in buildings
            )

        return [u for u in gs.resources if "GEYSER" in u.type_name and _has_refinery(u)]

    def _saturation(self, workers: list, nodes: list) -> dict[int, int]:
        """节点 tag -> 正在采集它的 SCV 数（从 orders 的 target_tag 派生，无内部状态）。"""
        node_tags = {n.tag for n in nodes}
        sat: dict[int, int] = {n.tag: 0 for n in nodes}
        for w in workers:
            for o in w.orders:
                if o.target_tag in node_tags:
                    sat[o.target_tag] += 1
        return sat

    def _pick_worker(self, workers: list) -> object | None:
        """优先空闲工兵（orders 空），否则取第一个。"""
        idle = [w for w in workers if not w.orders]
        return (idle or workers or [None])[0]

    def assign(self, gs: GameState, task: WorkerTask, count: int) -> list[Emission]:
        """assign_workers(task, count) → gather/stop 级 Emission 列表。"""
        if task is WorkerTask.IDLE:
            return self._idle(gs, count)
        workers = self._workers(gs)
        nodes = self._nodes(gs, gas=(task is WorkerTask.GAS))
        if not workers or not nodes:
            return []
        sat = self._saturation(workers, nodes)
        cap = GAS_SATURATION if task is WorkerTask.GAS else MINERAL_SATURATION
        out: list[Emission] = []
        remaining = count
        # 轮询补位：每轮给未饱和节点各派一个（SC2 采集习惯：矿脉间分摊）
        while remaining > 0:
            progressed = False
            for node in nodes:
                if remaining <= 0:
                    break
                if sat.get(node.tag, 0) >= cap:
                    continue
                w = self._pick_worker(workers)
                if w is None:
                    return out
                out.append(Emission(
                    action="gather",
                    unit_tags=[w.tag],
                    params={"target_unit": node.tag},
                ))
                sat[node.tag] = sat.get(node.tag, 0) + 1
                remaining -= 1
                progressed = True
            if not progressed:
                break  # 全部饱和或没工兵
        return out

    def _idle(self, gs: GameState, count: int) -> list[Emission]:
        """idle：把正在采集的 SCV 从矿/气解放（stop）——给建造/修理用。"""
        workers = self._workers(gs)
        nodes = self._nodes(gs, gas=False) + self._nodes(gs, gas=True)
        node_tags = {n.tag for n in nodes}
        out: list[Emission] = []
        for w in workers:
            if len(out) >= count:
                break
            if any(o.target_tag in node_tags for o in w.orders):
                out.append(Emission(action="stop", unit_tags=[w.tag]))
        return out
