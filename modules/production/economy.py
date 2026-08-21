"""production.economy：经济维持器（EconomyKeeper）—— 采矿/采气的常驻收敛器（ADR-0030）。

为什么不是队列项：采矿是**常驻维持任务**，不是一次性命令。`WorkerAllocator.assign` 只算"当下差额"，
所以新造 SCV 不会自动进矿、矿采空后工兵永久闲置 —— run 脚本里的 `steward` 队列就是手写的维持循环。

形态（与 flow 引擎同构）：每帧算 目标态 - 实际态，**只发差量**；实际态从 orders.target_tag 派生，
维持器自身无"谁在采哪个矿"的持久状态（重启/换帧一致，与 worker.py 同一原则）。
因此"矿采空自动回收""气矿建好自动补满"都是收敛的副作用，不需要专门代码。

语义（ADR-0030 D2）：配额是**目标值**（维持 N 个），不是"再派 N 个" ——
delta 语义在每帧重放里必然累加。目标是持久的，所以"精炼厂还没建好就设了 gas 目标"不会蒸发（issues P9）。

边界（ADR-0030 D3）：领地 = WorkerPoolPort.unleased_workers（没被战斗组租用/用户接管的）减去征用中的；
战术要专用工兵就声明一个组，Allocator 一租，这里自动不碰。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import GameState, Operation, Owner
from game.catalog import Catalog

# 每矿脉/每气矿的采集上限（真机常数，与 worker.py 同源）
MINERAL_SATURATION = 2
GAS_SATURATION = 3
# 资源节点归属半径：只管主基锚点附近（真机教训：全图选节点会把农民派到敌方基地送死）
NODE_RADIUS = 20.0
# 防抖：同一工兵在这么多帧内最多改派一次（初值 ≈4 游戏秒，**待真机校准**，ADR-0030 D4.4）
RETASK_COOLDOWN_FRAMES = 22
# 矿脉的实体名前缀：资源节点词表还没进 catalog（issues P11），这里集中一处
MINERAL_FIELD_PREFIX = "MINERALFIELD"


@dataclass
class EconomyPolicy:
    """配额（ADR-0030 D2.4）。饱和度是关于**矿点**的，不是关于人数的 —— 不满采就调 per_patch。"""

    mineral_per_patch: int = MINERAL_SATURATION  # 0..2
    gas_per_refinery: int = GAS_SATURATION       # 0..3
    mineral_workers: int | None = None  # 绝对目标（非 None 时优先于 per_patch）
    gas_workers: int | None = None      # 绝对目标（非 None 时优先于 per_refinery）
    reserve_idle: int = 0               # 保留的空闲工兵数（给建造/修理/应急）
    node_radius: float = NODE_RADIUS


class WorkerReservations:
    """建造这类"短期独占"的征用登记（ADR-0030 D3.3）。

    生产运行时发 build 前征用、flight 确认/丢弃时释放；维持器绝不改派征用中的工兵，
    于是"抢走正在建造的 SCV"（issues P14）结构上不可能。
    一个 owner 一次只持一个 tag（build flight 一对一）。
    """

    def __init__(self) -> None:
        self._by_owner: dict[str, int] = {}

    def reserve(self, owner: str, tag: int) -> bool:
        if tag in self.tags():
            return False  # 已被别人征用
        self._by_owner[owner] = tag
        return True

    def release(self, owner: str) -> None:
        self._by_owner.pop(owner, None)

    def tags(self) -> frozenset[int]:
        return frozenset(self._by_owner.values())

    def prune(self, alive: frozenset[int]) -> None:
        """死亡/消失的工兵自动解除征用（不然 flight 挂了会永久占住一个 tag）。"""
        for owner, tag in list(self._by_owner.items()):
            if tag not in alive:
                del self._by_owner[owner]

    def snapshot(self) -> dict[str, int]:
        return dict(self._by_owner)


@dataclass(slots=True)
class _Node:
    """一个资源节点（矿脉或已完工的气矿建筑）。"""

    tag: int
    is_gas: bool
    cap: int
    base: int | None = None  # 归属基地（最近的己方 dropoff 建筑 tag；无基地 → None，B12）


class EconomyKeeper:
    """每帧幂等收敛的采矿维持器。无内部"谁采哪个矿"状态；只记防抖用的上次改派帧。"""

    def __init__(self, catalog: Catalog, port, region_layer=None, pool=None,
                 reservations: WorkerReservations | None = None,
                 policy: EconomyPolicy | None = None) -> None:
        self._catalog = catalog
        self._port = port  # duck-typed：submit_operations
        self._region_layer = region_layer  # 主基锚点（节点过滤）；None = 不过滤
        self._pool = pool  # WorkerPoolPort；None = 领地是全部己方工兵（无战斗组竞争的场景）
        self.reservations = reservations or WorkerReservations()
        self.policy = policy or EconomyPolicy()
        self._last_retask: dict[int, int] = {}  # tag -> 上次改派的 gs.seq（防抖）
        self._op_seq = 0
        self._last_plan: dict[int, int | None] = {}  # 上一帧的目标分配（仅用于 snapshot 展示）
        self._last_emitted_count = 0

    # ---- 配额（flow 的 assign_workers 意图 / 生产队列项都落到这里）----

    def set_target(self, task: str, count: int) -> None:
        """设定目标值（幂等）。task: mineral | gas | idle。"""
        if count < 0:
            raise ValueError(f"assign_workers 目标数必须 ≥ 0，当前 {count}")
        if task == "mineral":
            self.policy.mineral_workers = count
        elif task == "gas":
            self.policy.gas_workers = count
        elif task == "idle":
            self.policy.reserve_idle = count
        else:
            raise ValueError(f"未知 task {task!r}（mineral|gas|idle）")

    # ---- 每帧收敛 ----

    def on_game_state(self, gs: GameState) -> None:
        workers = self._domain_workers(gs)
        self.reservations.prune(frozenset(u.tag for u in gs.units))
        nodes = self._nodes(gs)
        current = self._current_assignment(workers, nodes)
        plan = self._plan(gs, workers, nodes, current)
        self._last_plan = plan
        ops: list[Operation] = []
        for tag, node_tag in plan.items():
            if current.get(tag) == node_tag:
                continue  # 已经在目标上：不发命令（稳定态零命令）
            if not self._may_retask(tag, gs.seq):
                continue
            self._last_retask[tag] = gs.seq
            self._op_seq += 1
            if node_tag is None:
                ops.append(Operation(op_id=self._op_seq, unit_tags=[tag], action="stop",
                                     params={}, seq=gs.seq))
            else:
                ops.append(Operation(op_id=self._op_seq, unit_tags=[tag], action="gather",
                                     params={"target_unit": node_tag}, seq=gs.seq))
        self._last_emitted_count = len(ops)
        if ops:
            self._port.submit_operations(ops)

    # ---- 领地与节点 ----

    def _worker_names(self) -> frozenset[str]:
        return frozenset(e.burnysc2_name for e in self._catalog.where(role="worker"))

    def _domain_workers(self, gs: GameState) -> list:
        """领地 = 无主工兵 - 征用中（ADR-0030 D3.2）。"""
        names = self._worker_names()
        mine = [u for u in gs.units if u.owner is Owner.SELF and u.type_name in names]
        if self._pool is not None:
            allowed = set(self._pool.unleased_workers(gs))
            mine = [u for u in mine if u.tag in allowed]
        reserved = self.reservations.tags()
        return [u for u in mine if u.tag not in reserved]

    def _gas_names(self) -> frozenset[str]:
        """气矿建筑实体名（走 catalog capability，不写死 REFINERY —— issues P11 的一半）。"""
        return frozenset(e.burnysc2_name for e in self._catalog.where(capability="gas"))

    def _bases(self, gs: GameState) -> list:
        """己方基地（dropoff 建筑，如指挥中心；已建成）。节点的 base_tag 从这里取最近者（B12）。"""
        dropoff_names = frozenset(
            e.burnysc2_name for e in self._catalog.where(capability="dropoff")
        )
        return [
            u for u in gs.units
            if u.owner is Owner.SELF and u.type_name in dropoff_names and u.build_progress >= 1.0
        ]

    @staticmethod
    def _nearest_base(node_pos, bases) -> int | None:
        if not bases:
            return None
        best = min(
            bases,
            key=lambda b: (b.position.x - node_pos.x) ** 2 + (b.position.y - node_pos.y) ** 2,
        )
        return best.tag

    def _nodes(self, gs: GameState) -> list[_Node]:
        anchor = self._base_anchor()
        gas_names = self._gas_names()
        bases = self._bases(gs)
        out: list[_Node] = []
        for u in gs.units:
            if (u.owner is Owner.SELF and u.type_name in gas_names
                    and u.build_progress >= 1.0 and self._near(u, anchor)):
                out.append(_Node(u.tag, True, self.policy.gas_per_refinery,
                                 base=self._nearest_base(u.position, bases)))
        for r in gs.resources:
            if r.type_name.startswith(MINERAL_FIELD_PREFIX) and self._near(r, anchor):
                out.append(_Node(r.tag, False, self.policy.mineral_per_patch,
                                 base=self._nearest_base(r.position, bases)))
        return out

    def _near(self, unit, anchor) -> bool:
        if anchor is None:
            return True
        dx, dy = unit.position.x - anchor.x, unit.position.y - anchor.y
        return dx * dx + dy * dy <= self.policy.node_radius ** 2

    def _base_anchor(self):
        layer = self._region_layer
        if layer is None:
            return None
        big_id = layer.big_index.get(layer.big_grid.data[0][0])
        big = layer.big_regions.get(big_id)
        return big.anchor if big is not None else None

    # ---- 实际态（从 orders 派生，无内部状态）----

    @staticmethod
    def _current_assignment(workers: list, nodes: list[_Node]) -> dict[int, int | None]:
        node_tags = {n.tag for n in nodes}
        cur: dict[int, int | None] = {}
        for w in workers:
            target = next((o.target_tag for o in w.orders if o.target_tag in node_tags), None)
            cur[w.tag] = target  # None = 空闲或在做别的事（建造/移动）
        return cur

    def _may_retask(self, tag: int, seq: int) -> bool:
        last = self._last_retask.get(tag)
        return last is None or seq - last >= RETASK_COOLDOWN_FRAMES

    # ---- 目标态 ----

    def _targets(self, nodes: list[_Node], worker_count: int) -> tuple[int, int]:
        """(矿目标人数, 气目标人数)。

        绝对目标优先，否则按节点容量之和；都不超过容量。
        **气优先于矿**（ADR-0030 D4.3 的取人优先级同理）：气通常是显式配额且更稀缺，
        所以先扣气与 reserve_idle，剩下的人数才是矿的上限 —— 否则人不够时矿会先把人占满。
        """
        m_cap = sum(n.cap for n in nodes if not n.is_gas)
        g_cap = sum(n.cap for n in nodes if n.is_gas)
        m_want = self.policy.mineral_workers
        g_want = self.policy.gas_workers
        g_target = g_cap if g_want is None else min(g_want, g_cap)
        m_room = max(0, worker_count - g_target - self.policy.reserve_idle)
        m_target = min(m_cap if m_want is None else m_want, m_cap, m_room)
        return m_target, g_target

    def _plan(self, gs: GameState, workers: list, nodes: list[_Node],
              current: dict[int, int | None]) -> dict[int, int | None]:
        """目标分配：tag -> node_tag（None = 保持空闲）。

        原则：先留住已在目标上的（sticky，不发命令）；缺口用空闲工兵补；
        还不够才从超额任务里抽人；气优先于矿（气更稀缺，且 gas 目标通常是显式配额）。
        """
        by_tag = {w.tag: w for w in workers}
        m_target, g_target = self._targets(nodes, len(workers))
        node_by_tag = {n.tag: n for n in nodes}
        room = {n.tag: n.cap for n in nodes}
        plan: dict[int, int | None] = {}

        def is_gas(node_tag) -> bool:
            node = node_by_tag.get(node_tag)
            return bool(node and node.is_gas)

        # 1) 留住已在有效节点上的（在容量与目标之内）
        kept_gas = kept_mineral = 0
        for tag in sorted(by_tag):
            node_tag = current.get(tag)
            if node_tag is None or node_tag not in room or room[node_tag] <= 0:
                continue
            if is_gas(node_tag):
                if kept_gas >= g_target:
                    continue
                kept_gas += 1
            else:
                if kept_mineral >= m_target:
                    continue
                kept_mineral += 1
            plan[tag] = node_tag
            room[node_tag] -= 1

        # 2) 缺口按 气 → 矿 补；候选顺序：真空闲 > 采矿中（超额的） > 其他
        def candidates(want_gas: bool) -> list[int]:
            free, mining, other = [], [], []
            for tag in sorted(by_tag):
                if tag in plan:
                    continue
                node_tag = current.get(tag)
                if node_tag is None:
                    (free if not by_tag[tag].orders else other).append(tag)
                elif is_gas(node_tag) != want_gas:
                    mining.append(tag)  # 在另一种任务上且是超额的（没被 keep 住）
                else:
                    mining.append(tag)
            return free + mining + other

        for want_gas, target, kept in ((True, g_target, kept_gas), (False, m_target, kept_mineral)):
            need = target - kept
            if need <= 0:
                continue
            pool = candidates(want_gas)
            for node in [n for n in nodes if n.is_gas == want_gas]:
                while room[node.tag] > 0 and need > 0 and pool:
                    plan[pool.pop(0)] = node.tag
                    room[node.tag] -= 1
                    need -= 1

        # 3) 其余保持现状（不主动 stop）；reserve_idle 只保证"不再往上派"，不强行拉人下矿
        for tag in by_tag:
            plan.setdefault(tag, current.get(tag))
        return plan

    # ---- 观测（喂 B1 读模型）----

    def snapshot(self, gs: GameState | None = None) -> dict:
        nodes = self._nodes(gs) if gs is not None else []
        worker_count = len(self._domain_workers(gs)) if gs is not None else 0
        m_target, g_target = self._targets(nodes, worker_count)
        return {
            "policy": {
                "mineral_per_patch": self.policy.mineral_per_patch,
                "gas_per_refinery": self.policy.gas_per_refinery,
                "mineral_workers": self.policy.mineral_workers,
                "gas_workers": self.policy.gas_workers,
                "reserve_idle": self.policy.reserve_idle,
            },
            "targets": {"mineral": m_target, "gas": g_target},
            "nodes": [{"tag": n.tag, "gas": n.is_gas, "cap": n.cap, "base": n.base} for n in nodes],
            "reserved": self.reservations.snapshot(),
            "last_ops": self._last_emitted_count,
            "assigned": {str(k): v for k, v in self._last_plan.items()},
        }
