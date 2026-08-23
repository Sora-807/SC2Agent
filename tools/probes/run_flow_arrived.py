"""真 SC2 端到端：arrived 空间谓词的战斗 flow。

formup（group_count>=4）→ advance（arrived(main,[50,50],8)→exit；否则 move_to[50,50]）。
用起始 SCV（无需生产），验 flow→SC2 管道 + 空间谓词端到端。
跑：uv run python run_flow_arrived.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from driver.sc2_adapter import SC2GamePort
from flow.engine import FlowEngine
from game.catalog import load_all
from flow.manifest import parse_assembly, parse_strategy
from game import Owner
from sc2.data import Difficulty, Race
from world.adapter import adapt

LOG = Path(__file__).resolve().parents[2] / "docs" / "flow_arrived.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


STRATEGY = """
id: scv_arrived
group_slots: [main]
params: {min_units: {type: int, default: 4}}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: arrived, group: main, target: [50.0, 50.0], radius: 8.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - do:
          - {op: group_action, group_slot: main, type: terran/scv, action_atom: move_to, params: {position: [50.0, 50.0]}}
edges: [{from: formup, to: advance, kind: done, reason: FORMED}]
"""

ASSEMBLY = """
id: arrive_assembly
groups:
  - group_id: G1
    composition:
      terran/scv: {min: 4, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: scv_arrived
    bindings: {main: G1}
    params: {}
"""


class LoopSink:
    def __init__(self, engine: FlowEngine) -> None:
        self._engine = engine
        self._track: int | None = None

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if raw.seq < 5 or raw.seq % 20 == 0:
            own_scvs = [u for u in gs.units if u.type_name == "SCV" and u.owner == Owner.SELF]
            movers = [u for u in own_scvs if any("Move" in (o.ability or "") for o in u.orders)]
            if self._track is None and movers:
                self._track = movers[0].tag
            t = next((u for u in gs.units if u.tag == self._track), None) if self._track else None
            if t:
                d = ((t.position.x - 50.0) ** 2 + (t.position.y - 50.0) ** 2) ** 0.5
                log(f"step {raw.seq} (t={raw.game_time:.2f}): movers={len(movers)} track_pos={t.position} dist={d:.1f} done={self._engine._done}")
            else:
                log(f"step {raw.seq} (t={raw.game_time:.2f}): movers={len(movers)} track=None")
        self._engine.on_game_state(gs)

    def on_session_event(self, event) -> None:
        pass


def main() -> None:
    LOG.unlink(missing_ok=True)
    log("=== arrived flow slice start (driver→world→flow→driver, 真 SC2) ===")
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=120, realtime=False,
    )
    engine = FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY), port,
                        catalog=load_all())
    port.set_sink(LoopSink(engine))
    port.start("arrived-1")
    log(f"=== done: engine._done={engine._done} ===")


if __name__ == "__main__":
    main()
