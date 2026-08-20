"""端到端切片 capstone：driver→world→flow→driver 跑真 SC2。

手写 scv_move flow：formup（等 group_count 达 4）→ advance（move SCV 到 (50,50)）→ exit。
验证整条链路：SC2GamePort 抽 RawGameState → world.adapt → GameState → FlowEngine.eval
→ submit move Operation → driver 应用 → 下一 step state 反映（SCV 出现 Move order + 位移）。
跑：uv run python run_flow_slice.py
输出：docs/flow_slice.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from driver.sc2_adapter import SC2GamePort
from flow.engine import FlowEngine
from game.catalog import load_terran
from flow.manifest import parse_assembly, parse_strategy
from game import Owner
from sc2.data import Difficulty, Race
from world.adapter import adapt

LOG = Path(__file__).parent / "docs" / "flow_slice.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


STRATEGY = """
id: scv_move
version: 1
group_slots: [main]
params:
  min_units: {type: int, default: 4}
variables: {}
initial_step: formup
steps:
  - step_id: formup
    branches:
      - when: {op: ">=", args: [{op: group_count, args: [main]}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - do: []
  - step_id: advance
    branches:
      - when: {op: ">=", args: [{op: strategy_elapsed}, {const: 3.0}]}
        do: [{op: exit_strategy, kind: done, reason: SAFE}]
      - do:
          - {op: group_action, group_slot: main, type: terran/scv, action_atom: move_to, params: {position: [50.0, 50.0]}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
on_exit: release
loop_limits: {max_step_transitions: 100}
"""

ASSEMBLY = """
id: slice_assembly
groups:
  - group_id: G1
    composition:
      terran/scv: {min: 4, target: 4, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: scv_move
    bindings: {main: G1}
    params: {}
"""


class LoopSink:
    """driver 推 RawGameState → world.adapt → GameState → FlowEngine。"""

    def __init__(self, engine: FlowEngine) -> None:
        self._engine = engine
        self._track_tag: int | None = None

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if raw.seq in (0, 1, 2, 5, 10, 15, 20, 25):
            own_scvs = [u for u in gs.units if u.type_name == "SCV" and u.owner == Owner.SELF]
            movers = [u for u in own_scvs if any("Move" in (o.ability or "") for o in u.orders)]
            if self._track_tag is None and movers:
                self._track_tag = movers[0].tag
            track = next((u for u in gs.units if u.tag == self._track_tag), None) if self._track_tag else None
            tgt = track.orders[0].target_pos if (track and track.orders) else None
            dist = (((track.position.x - 50.0) ** 2 + (track.position.y - 50.0) ** 2) ** 0.5) if track else None
            log(
                f"step {raw.seq} (t={raw.game_time:.2f}): own_scvs={len(own_scvs)} movers={len(movers)} "
                f"track={self._track_tag} pos={track.position if track else None} "
                f"orders={[o.ability for o in (track.orders if track else [])]} "
                f"move_target={tgt} dist_to_(50,50)={dist:.1f}" if track else
                f"step {raw.seq} (t={raw.game_time:.2f}): own_scvs={len(own_scvs)} movers={len(movers)} track=None"
            )
        self._engine.on_game_state(gs)

    def on_session_event(self, event) -> None:
        pass


def main() -> None:
    LOG.unlink(missing_ok=True)
    log("=== flow slice start (driver→world→flow→driver, 真 SC2) ===")
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=25,
    )
    engine = FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY), port,
                        catalog=load_terran())
    port.set_sink(LoopSink(engine))
    port.start("slice-1")
    log("=== flow slice done ===")


if __name__ == "__main__":
    main()
