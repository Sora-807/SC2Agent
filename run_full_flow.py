"""完整 flow 真机验证（需 SC2；手动跑）：基础运营 → 50 枪兵 → 进攻敌方主矿。

双运行时联动（同 port）：production（固定位置顺序摆放 + 训练 + 派工）与 flow
（formup 等 50 枪兵 → advance attack_move 敌方主矿 → arrived → exit）每帧并行消费 GameState。

生产队列：assign mineral → 补给站×8 → 精炼厂×2 → 派气工 → 兵营×4 → 反应堆×4
          → SCV×12 → 枪兵×50（全部固定位：4×4 补给站网格 + 错位生产区，ADR-0027 锁定公式）
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
      - when: {op: arrived, args: [main, {param: target}, 5.0]}
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
        template = load_ladder_map()
        layout = spawn_layout_nearest(template, cc)
        layer = instantiate_spawn(template, layout, cc)
        other = next((s for name, s in template.spawns.items() if s.origin != layout.origin), layout)
        self._enemy = other.origin  # 敌方主矿 = 另一个出生点
        for name in ("depot1", "rax1", "rax2", "rax3", "rax4"):
            bs = layer.build_slots.get(name)
            if bs is not None:
                self._slots[name] = bs.reported_position
        self._runtime = ProductionRuntime(load_terran(), self._port, region_layer=layer)
        self._runtime.submit_queue("macro", [
            QueueItem(op="assign_workers", task="mineral", count=16),
            *[QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home"))
              for _ in range(8)],
            *[QueueItem(op="build", type="terran/refinery") for _ in range(2)],
            QueueItem(op="assign_workers", task="gas", count=6),
            *[QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home"))
              for _ in range(4)],
            *[QueueItem(op="build", type="terran/reactor") for _ in range(4)],
            QueueItem(op="train", type="terran/scv", count=12),
            QueueItem(op="train", type="terran/marine", count=50),
        ])
        self._engine = FlowEngine(
            parse_strategy(STRATEGY),
            parse_assembly(_assembly([self._enemy.x, self._enemy.y])),
            self._port,
            region_layer=layer,
        )
        log(f"[setup] CC={cc} 模板出生点={layout.origin} 敌方主矿={self._enemy}")
        log(f"[setup] macro 队列 = 补给站×8 精炼厂×2 兵营×4 反应堆×4 SCV×12 枪兵×50（固定位顺序摆放）")

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
            rax = self._count(gs, "BARRACKS")
            reactors = self._count(gs, "REACTOR")
            depots = self._count(gs, "SUPPLYDEPOT")
            scvs = self._count(gs, "SCV")
            step = self._engine._active_step
            log(f"[tick] seq={raw.seq} t={raw.game_time:.0f} 矿={gs.minerals} 气={gs.vespene} "
                f"depot={depots} rax={rax} reactor={reactors} scv={scvs} marine={marines} "
                f"step={step} done={self._engine._done}")
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
        sink=None, game_time_limit=420, realtime=False,
    )
    sink = FullFlowSink(port)
    port.set_sink(sink)
    port.start("full-flow-1")
    log(f"=== full flow done === 固定位 {sorted(sink._placed)} engine_done={sink._engine._done if sink._engine else False} "
        f"dropped={len(sink._runtime.dropped) if sink._runtime else 0}")


if __name__ == "__main__":
    main()
