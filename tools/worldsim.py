"""tools/worldsim：极简确定性世界模拟器（**只服务于夹具与离线验证**）。

为什么需要它：夹具里的 `frame/flow` / `frame/production` 应该是**真引擎对真反馈的反应**，
而不是我手写的期望值。手写夹具会把"我以为引擎会这样"固化进前端；一旦真引擎行为不同，
UI 就是照着错的形状做的。所以这里给引擎一个会回应的世界：发 build 就真出建筑、发 train 就真出兵、
发 gather 就真采矿 —— 引擎的 flight 确认 / 队首门控 / 补兵滞回于是全部走真实路径。

它**不是** SC2 模拟器，刻意不做：战斗、寻路、碰撞、放置合法性、SC2 的静默拒单。
ADR-0025 §7 要求的"FakeGamePort + 脚本化 GameState 序列"就是这个位置。
不放进 `modules/`：它是工具，不该进分层表。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "modules") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules"))

from game.catalog import Catalog  # noqa: E402
from game.geometry import Grid, Point2  # noqa: E402
from game.operation import Operation  # noqa: E402
from game.state import GameState, Order, Owner, Unit  # noqa: E402
from planner.economy import DEFAULT_ECON  # noqa: E402
from tactical_map.placement import BuildSlot  # noqa: E402

#: 每秒每工兵的采集速率（真机量级的粗略值；夹具只需要曲线形状可信）
MINERAL_RATE = 0.7
GAS_RATE = 0.55


@dataclass
class _Pending:
    """在建/在训：到点把 build_progress 推到 1。"""

    finish_t: float
    tag: int
    kind: str


@dataclass
class WorldSim:
    catalog: Catalog
    cc_pos: Point2 = field(default_factory=lambda: Point2(30.5, 30.5))
    minerals: float = 50.0
    gas: float = 0.0
    t: float = 0.0
    seq: int = 0
    map_size: tuple[int, int] = (176, 160)

    units: dict[int, Unit] = field(default_factory=dict)
    resources: list[Unit] = field(default_factory=list)
    _pending: list[_Pending] = field(default_factory=list)
    _next_tag: int = 1000
    #: 训练产槽（I10 修复）：producer tag → 产槽空闲时刻。真机 SC2 的训练是**排队**的 ——
    #: 命令立刻接受，但进度条要等该建筑前一个训完才起算。之前没有占用模型，
    #: count=6 的 TRAIN 会让一个兵营同时爬 6 条机枪兵进度条，90 秒夹具的投影
    #: 泳道因此堆出矿量根本撑不住的"并行行"（I10 的根因就在这，不在 planner 经济参数）。
    _train_busy: dict[int, float] = field(default_factory=dict)
    #: 世界没实现的动作（诊断：夹具生成时能看出哪些 op 没有后果）
    unhandled: list[str] = field(default_factory=list)

    # ---- 构造 ----

    def bootstrap(self, workers: int = 12, mineral_patches: int = 8, geysers: int = 2) -> None:
        """开局：指挥中心 + N 个 SCV + 矿区；SCV 自动挂采矿 order（真机开局也是）。"""
        self._spawn("terran/commandcenter", self.cc_pos, ready=True)
        for i in range(mineral_patches):
            self.resources.append(
                self._neutral("MINERALFIELD", Point2(self.cc_pos.x - 6 + i * 1.5, self.cc_pos.y + 5))
            )
        for i in range(geysers):
            self.resources.append(
                self._neutral("VESPENEGEYSER", Point2(self.cc_pos.x - 7 + i * 14, self.cc_pos.y + 2))
            )
        for i in range(workers):
            scv = self._spawn("terran/scv",
                              Point2(self.cc_pos.x - 4 + i * 0.6, self.cc_pos.y + 3), ready=True)
            node = self.resources[i % max(1, mineral_patches)]
            scv.orders = [Order(ability="HARVEST_GATHER", target_tag=node.tag, is_auto=True)]

    # ---- 每帧 ----

    def game_state(self) -> GameState:
        g = Grid(4, 4, [[0] * 4 for _ in range(4)])
        vis = Grid(4, 4, [[2] * 4 for _ in range(4)])
        return GameState(
            seq=self.seq, game_time=round(self.t, 3),
            minerals=int(self.minerals), vespene=int(self.gas),
            supply_used=self._supply_used(), supply_cap=self._supply_cap(),
            units=[self._clone(u) for u in self.units.values()],
            map_size=self.map_size, creep=g, visibility=vis,
            resources=[self._clone(u) for u in self.resources],
        )

    def tick(self, dt: float = 1.0) -> None:
        """推进时间：收资源、推进在建/在训、到点交付、移动中的单位前进。"""
        self.t += dt
        self.seq += 1
        self.minerals += MINERAL_RATE * self.mineral_workers() * dt
        self.gas += GAS_RATE * self.gas_workers() * dt

        done: list[_Pending] = []
        for p in self._pending:
            u = self.units.get(p.tag)
            if u is None:
                done.append(p)
                continue
            entry = self.catalog.by_stable_id(self._stable_of(u))
            build_time = float(entry.build_time) if entry else 1.0
            if self.t >= p.finish_t:
                u.build_progress = 1.0
                u.orders = []
                done.append(p)
            else:
                remain = p.finish_t - self.t
                u.build_progress = max(0.05, min(0.99, 1.0 - remain / max(1e-6, build_time)))
        for p in done:
            self._pending.remove(p)
        self._advance_marching(dt)

    # ---- 应用引擎发出的命令 ----

    def apply(self, ops: list[Operation]) -> None:
        for op in ops:
            handler = getattr(self, "_op_" + op.action, None)
            if handler is None:
                self.unhandled.append(op.action)
                continue
            handler(op)

    def _op_build(self, op: Operation) -> None:
        stable_id = op.params.get("type")
        pos = op.params.get("position")
        entry = self.catalog.by_stable_id(stable_id or "")
        if entry is None or pos is None:
            self.unhandled.append(f"build {stable_id}")
            return
        if not self._pay(entry):
            return  # 矿不够：真机 SC2 静默拒单，这里也什么都不发生（引擎 flight 会超时重试）
        # SC2 的建筑**立刻出现**且 build_progress 从 0 爬 —— 引擎的 flight 确认依赖这一点
        world_pos = self._reported_of(entry, Point2(float(pos[0]), float(pos[1])))
        u = self._spawn(stable_id, world_pos, ready=False)
        self._pending.append(_Pending(self.t + float(entry.build_time), u.tag, "build"))
        for tag in op.unit_tags:      # 建造者进入建造状态（引擎靠 build order 判断命令被接受）
            b = self.units.get(tag)
            if b is not None:
                b.orders = [Order(ability=entry.burnysc2_name, target_pos=world_pos)]

    def _op_build_gas(self, op: Operation) -> None:
        entry = self.catalog.by_stable_id(op.params.get("type") or "")
        geyser = next((g for g in self.resources if g.tag == op.params.get("target_unit")), None)
        if entry is None or geyser is None or not self._pay(entry):
            return
        u = self._spawn(entry.stable_id, geyser.position, ready=False)
        self._pending.append(_Pending(self.t + float(entry.build_time), u.tag, "build"))

    def _op_train(self, op: Operation) -> None:
        entry = self.catalog.by_stable_id(op.params.get("type") or "")
        if entry is None or not self._pay(entry):
            return
        producer = next((self.units.get(t) for t in op.unit_tags if t in self.units), None)
        at = producer.position if producer else self.cc_pos
        # 产槽排队（I10）：命令现在就接受并扣矿（真机行为），但完成时刻从产槽空闲起算。
        # 排队中的单位 build_progress 停在 0.05 下限 —— 投影读到的 in_flight 也就如实
        # 呈现"这些是排着队的，不是并行开工"。
        start = self.t
        if producer is not None:
            start = max(self.t, self._train_busy.get(producer.tag, 0.0))
            self._train_busy[producer.tag] = start + float(entry.build_time)
        u = self._spawn(entry.stable_id, Point2(at.x + 1.5, at.y - 1.5), ready=False)
        self._pending.append(_Pending(start + float(entry.build_time), u.tag, "train"))
        if producer is not None:
            producer.orders = [Order(ability=entry.burnysc2_name)]

    def _op_gather(self, op: Operation) -> None:
        target = op.params.get("target_unit")
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None:
                u.orders = [Order(ability="HARVEST_GATHER", target_tag=target)]

    def _op_stop(self, op: Operation) -> None:
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None:
                u.orders = []

    def _op_move_to(self, op: Operation) -> None:
        self._march(op, "MOVE")

    def _op_attack_move_to(self, op: Operation) -> None:
        self._march(op, "ATTACK")

    def _op_hold_position(self, op: Operation) -> None:
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None:
                u.orders = [Order(ability="HOLDPOSITION")]

    def _op_siege(self, op: Operation) -> None:
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None and u.type_name == "SIEGETANK":
                u.type_name = "SIEGETANKSIEGED"   # 形态变体：正好验 adapt 的归一化路径
                u.orders = [Order(ability="SIEGEMODE_SIEGEMODE")]

    def _op_unsiege(self, op: Operation) -> None:
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None and u.type_name == "SIEGETANKSIEGED":
                u.type_name = "SIEGETANK"
                u.orders = []

    def _march(self, op: Operation, ability: str) -> None:
        pos = op.params.get("position")
        if pos is None:
            return
        target = Point2(float(pos[0]), float(pos[1]))
        for tag in op.unit_tags:
            u = self.units.get(tag)
            if u is not None:
                u.orders = [Order(ability=ability, target_pos=target)]

    def _advance_marching(self, dt: float) -> None:
        """带移动 order 的单位朝目标直线前进（无寻路：夹具不需要）。"""
        for u in self.units.values():
            if not u.orders:
                continue
            o = u.orders[0]
            if o.ability not in ("MOVE", "ATTACK") or o.target_pos is None:
                continue
            dx, dy = o.target_pos.x - u.position.x, o.target_pos.y - u.position.y
            dist = (dx * dx + dy * dy) ** 0.5
            step = 2.0 * dt
            if dist <= step:
                u.position = Point2(o.target_pos.x, o.target_pos.y)
            else:
                u.position = Point2(u.position.x + dx / dist * step,
                                    u.position.y + dy / dist * step)

    # ---- 派生量 ----

    def mineral_workers(self) -> int:
        tags = {r.tag for r in self.resources if "MINERAL" in r.type_name}
        return sum(1 for u in self.units.values() if self._gathering(u) in tags)

    def gas_workers(self) -> int:
        geysers = {r.tag for r in self.resources if "GEYSER" in r.type_name}
        refineries = {u.tag for u in self.units.values()
                      if self._stable_of(u) == "terran/refinery" and u.build_progress >= 1.0}
        return sum(1 for u in self.units.values() if self._gathering(u) in (geysers | refineries))

    @staticmethod
    def _gathering(u: Unit) -> int | None:
        if not u.orders:
            return None
        o = u.orders[0]
        return o.target_tag if o.ability.upper().startswith("HARVEST") else None

    def _supply_used(self) -> int:
        total = 0
        for u in self.units.values():
            if u.build_progress < 1.0:
                continue
            entry = self.catalog.by_stable_id(self._stable_of(u))
            if entry is not None:
                total += entry.cost.supply
        return total

    def _supply_cap(self) -> int:
        # 供给增量的单一真相源在 planner.economy.supply_provided
        # （本机 game_data_dump + 真机录像校准：CC=13、Depot=8）。
        # 此前这里写死 bases*15，与 economy 的 13 自相矛盾 —— 同一个 sim 里
        # 建第二个 CC 加的供给和 planner 投影算的不一样。
        provided = DEFAULT_ECON.supply_provided
        total = sum(provided.get(self._stable_of(u), 0)
                    for u in self.units.values() if u.build_progress >= 1.0)
        return min(200, total)

    # ---- 内部 ----

    def _pay(self, entry) -> bool:
        if self.minerals < entry.cost.minerals or self.gas < entry.cost.vespene:
            return False
        self.minerals -= entry.cost.minerals
        self.gas -= entry.cost.vespene
        return True

    def _spawn(self, stable_id: str, pos: Point2, *, ready: bool) -> Unit:
        entry = self.catalog.by_stable_id(stable_id)
        assert entry is not None, f"catalog 没有 {stable_id}"
        self._next_tag += 1
        hp = 100.0 if entry.size is None else 400.0 * entry.size / 2
        u = Unit(tag=self._next_tag, type_name=entry.burnysc2_name, position=pos, owner=Owner.SELF,
                 hp=hp, hp_max=hp, shield=0.0, energy=0.0,
                 build_progress=1.0 if ready else 0.05, orders=[])
        self.units[u.tag] = u
        return u

    def _neutral(self, type_name: str, pos: Point2) -> Unit:
        self._next_tag += 1
        return Unit(tag=self._next_tag, type_name=type_name, position=pos, owner=Owner.NEUTRAL,
                    hp=1.0, hp_max=1.0, shield=0.0, energy=0.0, build_progress=1.0, orders=[])

    def spawn_enemy(self, burnysc2_name: str, pos: Point2, count: int = 1) -> list[Unit]:
        """敌方单位（不参与战斗，只为让敌情图层有内容）。"""
        out: list[Unit] = []
        for i in range(count):
            self._next_tag += 1
            u = Unit(tag=self._next_tag, type_name=burnysc2_name,
                     position=Point2(pos.x + i * 1.1, pos.y), owner=Owner.ENEMY,
                     hp=100.0, hp_max=100.0, shield=50.0, energy=0.0,
                     build_progress=1.0, orders=[])
            self.units[u.tag] = u
            out.append(u)
        return out

    def _stable_of(self, u: Unit) -> str:
        entry = self.catalog.by_burnysc2_name(self.catalog.normalize_burnysc2_name(u.type_name))
        return entry.stable_id if entry else "unknown/" + u.type_name.lower()

    @staticmethod
    def _reported_of(entry, build_point: Point2) -> Point2:
        """建造点 → 报告位置。走 BuildSlot 的权威换算（ADR-0027），不自己写公式。"""
        size = entry.size or 2
        tl = BuildSlot.tl_from_pos(build_point, size)
        return BuildSlot(name="_tmp", tl=tl, size=size).reported_position

    @staticmethod
    def _clone(u: Unit) -> Unit:
        """给引擎的是**副本**：引擎不该能改世界（真机也是快照语义）。"""
        return Unit(
            tag=u.tag, type_name=u.type_name, position=Point2(u.position.x, u.position.y),
            owner=u.owner, hp=u.hp, hp_max=u.hp_max, shield=u.shield, energy=u.energy,
            build_progress=u.build_progress,
            orders=[Order(ability=o.ability, target_tag=o.target_tag,
                          target_pos=None if o.target_pos is None
                          else Point2(o.target_pos.x, o.target_pos.y),
                          is_auto=o.is_auto) for o in u.orders],
            facing=u.facing, buffs=tuple(u.buffs),
            is_carrying_minerals=u.is_carrying_minerals, is_carrying_vespene=u.is_carrying_vespene,
        )
