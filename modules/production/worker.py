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
NODE_RADIUS = 20.0  # 资源节点归属半径：只取距主基锚点此距离内的矿脉/气井（真机教训：全图选节点会把农民派到敌方基地送死）


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

    def _nodes(self, gs: GameState, gas: bool, base_pos) -> list:
        """资源节点：gas=True 取精炼厂（SELF REFINERY），否则取矿脉（gs.resources MINERALFIELD）。

        gas gather 目标 = 精炼厂 building tag（不是气井）：精炼厂建筑 3 SCV 能采；
        gather(气井) 只 1 SCV 挤得进（气井点被精炼厂占）——真机踩坑（1/精炼厂、气≈0）。
        order 目标 = 精炼厂 tag（gather(refinery)→order.target=refinery）→ _saturation/检测一致。
        只取主基锚点 NODE_RADIUS 内的；base_pos=None 不过滤。
        """
        if gas:
            ref = [u for u in gs.units
                   if u.owner is Owner.SELF and u.type_name == "REFINERY"
                   and u.build_progress >= 1.0]
        else:
            ref = [u for u in gs.resources if u.type_name.startswith("MINERALFIELD")]
        if base_pos is not None:
            ref = [u for u in ref
                   if (u.position.x - base_pos.x) ** 2 + (u.position.y - base_pos.y) ** 2
                   <= NODE_RADIUS ** 2]
        return ref

    def _saturation(self, workers: list, nodes: list) -> dict[int, int]:
        """节点 tag -> 正在采集它的 SCV 数（从 orders 的 target_tag 派生，无内部状态）。"""
        node_tags = {n.tag for n in nodes}
        sat: dict[int, int] = {n.tag: 0 for n in nodes}
        for w in workers:
            for o in w.orders:
                if o.target_tag in node_tags:
                    sat[o.target_tag] += 1
        return sat

    def _pick_worker(self, workers: list, exclude: set[int] | None = None) -> object | None:
        """优先空闲工兵（orders 空），否则取第一个；排除已选（同帧不重复派同一 SCV——
        真机踩坑：无 idle 时旧实现每次都返回 workers[0]，N 条 gather 打同一 SCV 被去重→只 1 个真采）。"""
        ex = exclude or set()
        idle = [w for w in workers if w.tag not in ex and not w.orders]
        if idle:
            return idle[0]
        rest = [w for w in workers if w.tag not in ex]
        return rest[0] if rest else None

    def assign(self, gs: GameState, task: WorkerTask, count: int, base_pos=None,
               skip: frozenset[int] = frozenset()) -> list[Emission]:
        """assign_workers(task, count) → gather/stop 级 Emission 列表。base_pos = 主基锚点（节点过滤）；
        skip = 本帧已被其他队列下过令的单位 tag（同帧同单位命令会被 burnysc2 去重丢单——真机踩坑）。"""
        if task is WorkerTask.IDLE:
            return self._idle(gs, count)
        workers = [u for u in self._workers(gs) if u.tag not in skip]
        nodes = self._nodes(gs, gas=(task is WorkerTask.GAS), base_pos=base_pos)
        if not workers or not nodes:
            return []
        sat = self._saturation(workers, nodes)
        cap = GAS_SATURATION if task is WorkerTask.GAS else MINERAL_SATURATION
        out: list[Emission] = []
        remaining = count
        picked: set[int] = set()  # 本帧已派令的 SCV（不重复派同一单位——见 _pick_worker）
        # 轮询补位：每轮给未饱和节点各派一个（SC2 采集习惯：矿脉间分摊）
        while remaining > 0:
            progressed = False
            for node in nodes:
                if remaining <= 0:
                    break
                if sat.get(node.tag, 0) >= cap:
                    continue
                w = self._pick_worker(workers, exclude=picked)
                if w is None:
                    return out
                picked.add(w.tag)
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
        nodes = self._nodes(gs, gas=False, base_pos=None) + self._nodes(gs, gas=True, base_pos=None)
        node_tags = {n.tag for n in nodes}
        out: list[Emission] = []
        for w in workers:
            if len(out) >= count:
                break
            if any(o.target_tag in node_tags for o in w.orders):
                out.append(Emission(action="stop", unit_tags=[w.tag]))
        return out
