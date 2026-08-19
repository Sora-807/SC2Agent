"""挂件建造探针（真机调试；手动跑）：只建 1 兵营 + 1 反应堆，记录实际下发的 UnitCommand。

输出：docs/addon_probe.log
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from game import QueueItem, Owner
from game.catalog import load_terran
from game.production import PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from production.runtime import ProductionRuntime
from sc2.data import Difficulty, Race
from tactical_map import instantiate_spawn, load_ladder_map, spawn_layout_nearest
from world.adapter import adapt

LOG = Path(__file__).parent / "docs" / "addon_probe.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class ProbeSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._last_seq = -1

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            cc = next(u.position for u in gs.units
                      if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER")
            template = load_ladder_map()
            layer = instantiate_spawn(template, spawn_layout_nearest(template, cc), cc)
            self._runtime = ProductionRuntime(load_terran(), self._port, region_layer=layer)
            self._runtime.submit_queue("probe", [
                QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
                QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
                QueueItem(op="build", type="terran/reactor"),
            ])
            # driver 侧翻译追踪（比包裹 bot.do 可靠）
            bot = self._port._bot
            bot._apply_trace = []
            bot._apply_failures = []
            log(f"[setup] CC={cc} 队列 = depot → barracks → reactor")
        if self._runtime is not None and raw.seq != self._last_seq:
            self._last_seq = raw.seq
            self._runtime.on_game_state(gs)
            if raw.seq % 10 == 0:
                rax_orders = [(u.tag, [o.ability for o in u.orders]) for u in gs.units
                              if u.owner is Owner.SELF and u.type_name == "BARRACKS"]
                reactors = [(u.tag, u.position, u.build_progress) for u in gs.units
                            if u.owner is Owner.SELF and u.type_name == "REACTOR"]
                trace = getattr(self._port._bot, "_apply_trace", []) or []
                fails = getattr(self._port._bot, "_apply_failures", []) or []
                log(f"[tick] seq={raw.seq} 矿={gs.minerals} 气={gs.vespene} rax_orders={rax_orders} "
                    f"reactors={reactors} trace={trace[-4:]} fails={fails[-2:]} "
                    f"dropped={[(i.type, r[:40]) for i, r in self._runtime.dropped][-2:]}")

    def on_session_event(self, event) -> None:
        pass


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=200, realtime=False,
    )
    sink = ProbeSink(port)
    port.set_sink(sink)
    port.start("addon-probe-1")


if __name__ == "__main__":
    main()
