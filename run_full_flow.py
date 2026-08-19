"""完整 flow 真机验证（需 SC2；手动跑）：基础运营 → 50 枪兵 → 进攻敌方主矿。

双运行时联动（同 port）：production（固定位置顺序摆放 + 训练 + 派工）与 flow
（formup 等 50 枪兵 → advance attack_move 敌方主矿 → arrived → exit）每帧并行消费 GameState。

生产队列：assign mineral → 补给站×8 → 精炼厂×2 → 派气工 → 兵营×4 → 枪兵×8（自卫）
          → SCV×12；反应堆×4 = 4 条独立并行队列（母建筑自建）；枪兵由维持器补到 50（全部固定位：4×4 补给站网格 + 错位生产区，ADR-0027 锁定公式）
steward（演示粘合，真实"维持规则"post-V1）：每 ~2s 把空闲 SCV 派去矿/气。
证据：兵营/反应堆落位固定 slot、枪兵数达 50、flow 发出 attack_move_to（敌方主矿）、arrived→exit。
跑：uv run python run_full_flow.py
输出：docs/full_flow.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from game import QueueItem, Owner, Point2
from game.catalog import load_terran
from game.production import PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from production.runtime import ProductionRuntime
from sc2.data import Difficulty, Race
from tactical_map import instantiate_spawn, load_ladder_map, spawn_layout_nearest
from world.adapter import adapt

LOG = Path(__file__).parent / "docs" / "full_flow.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


STRATEGY = """
id: macro_push
version: 1
group_slots: [main]
params:
  min_marines: {type: int, default: 50}
  target: {type: point, default: [0, 0]}
variables: {}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, args: [main, MARINE]}, {param: min_marines}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: arrived, args: [main, {param: target}, 8.0]}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - do:
          - {op: group_action, group_slot: main, type: MARINE, action_atom: attack_move_to,
             params: {position: {param: target}}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
on_exit: release
loop_limits: {max_step_transitions: 100}
"""


def _assembly(target: list) -> str:
    return f"""
id: macro_assembly
groups:
  - group_id: G1
    composition:
      MARINE: {{min: 50, target: 50, max: 50}}
strategy_instances:
  - instance_id: s1
    strategy_ref: macro_push
    bindings: {{main: G1}}
    params: {{min_marines: 50, target: {target}}}
"""


class FullFlowSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._engine: FlowEngine | None = None
        self._enemy: Point2 | None = None
        self._slots: dict[str, Point2] = {}
        self._placed: set[str] = set()
        self._reported: dict[str, str] = {}
        self._steward_seq = 0
        self._bot = None

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            self._setup(gs)
        if self._runtime is not None:
            self._runtime.on_game_state(gs)
            self._engine.on_game_state(gs)
            self._steward(raw, gs)
            self._verify(raw, gs)

    def on_session_event(self, event) -> None:
        pass

    def _setup(self, gs) -> None:
        log("=== full flow start（生产半边 + 战斗半边联动）===")
        cc = next(u.position for u in gs.units
                  if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER")
        self._cc = cc
        template = load_ladder_map()
        layout = spawn_layout_nearest(template, cc)
        layer = instantiate_spawn(template, layout, cc)
        other = next((s for name, s in template.spawns.items() if s.origin != layout.origin), layout)
        self._enemy = other.origin  # 敌方主矿 = 另一个出生点
        for name in ("depot1", "rax1", "rax2", "rax3", "rax4"):
            bs = layer.build_slots.get(name)
            if bs is not None:
                self._slots[name] = bs.reported_position
        orig_submit = self._port.submit_operations

        def logging_submit(ops):
            for o in ops:
                log(f"[op] {o.action} tags={o.unit_tags} params={o.params} seq={o.seq}")
            return orig_submit(ops)

        self._port.submit_operations = logging_submit  # type: ignore[method-assign]
        self._runtime = ProductionRuntime(load_terran(), self._port, region_layer=layer)
        self._runtime.submit_queue("macro", [
            QueueItem(op="assign_workers", task="mineral", count=16),
            *[QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home"))
              for _ in range(2)],
            QueueItem(op="train", type="terran/scv", count=12),  # 单矿满 SCV 优先（先补农民）
            *[QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home"))
              for _ in range(6)],
            *[QueueItem(op="build", type="terran/refinery") for _ in range(2)],
            QueueItem(op="assign_workers", task="gas", count=6),
            *[QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home"))
              for _ in range(4)],
            QueueItem(op="train", type="terran/marine", count=8),  # 早期自卫小队：守家等反应堆
        ])
        # 反应堆×4 用独立队列并行（真机教训：单队列队首门控串行 → 4×36s=144s 拖垮节奏；
        # 同帧 4 令 → 不同母建筑，靠 _frame_busy 去重 + 按位置确认不互认）
        for i in range(4):
            self._runtime.submit_queue(f"addon{i}", [QueueItem(op="build", type="terran/reactor")])
        self._engine = FlowEngine(
            parse_strategy(STRATEGY),
            parse_assembly(_assembly([self._enemy.x, self._enemy.y])),
            self._port,
            region_layer=layer,
        )
        log(f"[setup] CC={cc} 模板出生点={layout.origin} 敌方主矿={self._enemy}")
        log(f"[setup] macro 队列 = 补给站×8 精炼厂×2 兵营×4 枪兵×8（自卫）SCV×12；反应堆×4 独立并行队列；枪兵维持补到 50（固定位顺序摆放）")
        self._bot = self._port._bot
        self._bot._apply_failures = []

    def _steward(self, raw, gs) -> None:
        """演示粘合：空闲 SCV → 矿/气（真实维持规则 post-V1 再进运行时）。"""
        if raw.seq - self._steward_seq < 10:
            return
        self._steward_seq = raw.seq
        idle = [u for u in gs.units
                if u.owner is Owner.SELF and u.type_name == "SCV" and not u.orders]
        if not idle:
            return
        refineries = [u for u in gs.units
                      if u.owner is Owner.SELF and u.type_name == "REFINERY" and u.build_progress >= 1.0]
        geyser_tags = {u.tag for u in gs.resources if "GEYSER" in u.type_name}
        gas_workers = sum(
            1 for u in gs.units
            if u.owner is Owner.SELF and u.type_name == "SCV"
            and any(o.target_tag in geyser_tags for o in u.orders)
        )
        task = "gas" if (refineries and gas_workers < 6) else "mineral"
        self._runtime.submit_queue("steward", [
            QueueItem(op="assign_workers", task=task, count=len(idle)),
        ])
        # 补给维持：macro 排空后（自己的 8 个建完），被敌军打掉的补给站才补建
        # （真机教训 full_flow.log：丢 1 个补给站 = 供给 69 卡死第 50 枪兵；
        # 且必须等 macro 排空——否则和 macro 自己的补给站抢位，多建到 12 个）。
        macro_q0 = self._runtime.queue("macro")
        depots = self._count(gs, "SUPPLYDEPOT")
        supply_q = self._runtime.queue("supply")
        depot_pending = bool(supply_q and supply_q.items) or any(
            f.get("type") == "terran/supplydepot" for f in self._runtime._build_flight.values()
        )
        if (macro_q0 is not None and not macro_q0.items
                and not self._runtime._build_flight
                and depots < 8 and not depot_pending):
            self._runtime.submit_queue("supply", [
                QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            ])
        # 枪兵维持：macro 队列排空（反应堆×4 + 50 枪兵训完）后、少于 50 且有空训练槽 → 补训。
        # 真机教训：只训 50 次会因骚扰损耗永远等不到 50 存活；SC2 队列满静默拒单，必须按空槽补；
        # 且补训必须等挂件全部落地——训练订单会挤掉/拒绝同帧挂件命令（full_flow.log 第二次跑）。
        macro_q = self._runtime.queue("macro")
        rax = self._count(gs, "BARRACKS")
        # 枪兵维持条件：每台现存兵营都挂上反应堆（自适应：兵营被敌军摧毁时
        # 不再死等 4 台——真机教训 full_flow.log：死等 → 无兵守家被推平）
        if (macro_q is not None and not macro_q.items
                and not self._runtime._build_flight
                and self._count(gs, "BARRACKSREACTOR") >= max(1, rax)
                and self._engine._active_step == "formup"):
            # 只在 formup 补训：advance 后新兵蛋子从家里出发会拖拽 group 中心，
            # 让 arrived(组心距目标 5) 永远不成立（真机踩坑）
            marines = self._count(gs, "MARINE")
            if marines < 50:
                open_slots = 0
                for u in gs.units:
                    if u.owner is Owner.SELF and u.type_name == "BARRACKS" and u.build_progress >= 1.0:
                        queued = sum(1 for o in u.orders if "marine" in (o.ability or "").lower())
                        open_slots += max(0, 2 - queued)
                need = min(open_slots, 50 - marines)  # 防超训（空槽可能 > 缺口）
                if need > 0:
                    self._runtime.submit_queue("marine", [
                        QueueItem(op="train", type="terran/marine", count=need),
                    ])

    def _mineral_tags(self, gs) -> set[int]:
        return {u.tag for u in gs.resources if u.type_name.startswith("MINERALFIELD")}

    def _verify(self, raw, gs) -> None:
        checks = {"depot1": "SUPPLYDEPOT", "rax1": "BARRACKS", "rax2": "BARRACKS",
                  "rax3": "BARRACKS", "rax4": "BARRACKS"}
        for slot_name, type_name in checks.items():
            if slot_name in self._placed or slot_name not in self._slots:
                continue
            for u in gs.units:
                if u.owner is Owner.SELF and u.type_name == type_name:
                    dist = ((u.position.x - self._slots[slot_name].x) ** 2
                            + (u.position.y - self._slots[slot_name].y) ** 2) ** 0.5
                    if dist <= 0.75:
                        self._placed.add(slot_name)
                        log(f"[{slot_name}] OK 落位 {u.position}（预期 {self._slots[slot_name]}）")
                        break
        if raw.seq % 50 == 0:
            marines = self._count(gs, "MARINE")
            self._max_marines = max(getattr(self, "_max_marines", 0), marines)
            rax = self._count(gs, "BARRACKS")
            reactors = self._count(gs, "BARRACKSREACTOR")  # 挂件实体 = 父建筑专属类型（真机锁定）
            depots = self._count(gs, "SUPPLYDEPOT")
            scvs = self._count(gs, "SCV")
            cc = getattr(self, "_cc", Point2(0, 0))
            enemy_near = sum(
                1 for u in gs.units
                if u.owner is Owner.ENEMY
                and ((u.position.x - cc.x) ** 2 + (u.position.y - cc.y) ** 2) ** 0.5 < 30
            )
            step = self._engine._active_step
            rax_orders = sorted({o.ability or "" for u in gs.units
                                 if u.owner is Owner.SELF and u.type_name == "BARRACKS"
                                 for o in u.orders})
            fails = getattr(self._bot, "_apply_failures", []) if self._bot else []
            flight = {k: (v["type"], v["frames"]) for k, v in
                      self._runtime._build_flight.items()}
            dropped = [(i.type, r) for i, r in self._runtime.dropped][-3:]
            group_dist = ""
            if step == "advance" and self._enemy is not None:
                ms = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "MARINE"]
                if ms:
                    cx = sum(u.position.x for u in ms) / len(ms)
                    cy = sum(u.position.y for u in ms) / len(ms)
                    group_dist = f"组心距敌方主矿={((cx - self._enemy.x) ** 2 + (cy - self._enemy.y) ** 2) ** 0.5:.1f} "
            log(f"[tick] seq={raw.seq} t={raw.game_time:.0f} 矿={gs.minerals} 气={gs.vespene} "
                f"supply={gs.supply_used}/{gs.supply_cap} depot={depots} rax={rax} reactor={reactors} "
                f"scv={scvs} marine={marines}(峰值{self._max_marines}) 敌方近={enemy_near} "
                f"{group_dist}step={step} done={self._engine._done} rax_orders={rax_orders} "
                f"apply_failures={fails[-3:]} flight={flight} dropped={dropped}")
            if step == "advance":
                attacks = [o for o in self._port._op_queue if o.action == "attack_move_to"]
                if attacks:
                    log(f"[flow] attack_move_to 已入队 → 目标 {attacks[0].params['position']}（敌方主矿 {self._enemy}）")

    def _count(self, gs, type_name) -> int:
        return sum(1 for u in gs.units if u.owner is Owner.SELF and u.type_name == type_name)


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=700, realtime=False,
    )
    sink = FullFlowSink(port)
    port.set_sink(sink)
    port.start("full-flow-1")
    log(f"=== full flow done === 固定位 {sorted(sink._placed)} engine_done={sink._engine._done if sink._engine else False} "
        f"dropped={len(sink._runtime.dropped) if sink._runtime else 0} "
        f"marine峰值={getattr(sink, '_max_marines', 0)}")


if __name__ == "__main__":
    main()
