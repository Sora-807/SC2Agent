"""坦克生产链真机验证（T6，需 SC2；手动跑）：1 工厂 + 1 科技挂件 + 4 坦克。

队列（plan-step-tank T6）：assign 矿 → 补给站×2 → SCV×12 → 补给站×6 → 精炼厂×2 →
派气工 → 兵营（factory 前置，落 rax1）→ 工厂（PlacementExact factory1）→ 科技挂件
（addon，吸附工厂）→ 坦克×4。

关键：工厂用 PlacementExact("factory1") 显式指名（PlacementInRegion 会按声明顺序落到 rax2，
非 factory1）；挂件/坦克走现有 _try_build_addon / _try_train 路径（T2 catalog 已就绪，无新代码）。

证据：工厂落 factory1（reported_position 0.75 内）、FACTORYTECHLAB 实体出现、4 SIEGETANK 训练完成。
顺带实测校正 T2 数值（factory build_time 43 / techlab 18 / siegetank 32；range 5/13）——
从 tick 日志的订单常驻时长 / 实体 build_progress 推算，回填 terran.json + test_catalog 注释。

跑：uv run python run_tank_production_check.py
输出：docs/tank_production_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from game import QueueItem, Owner, Point2
from game.catalog import load_terran
from game.production import PlacementExact, PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from production.runtime import ProductionRuntime
from sc2.data import Difficulty, Race
from tactical_map import instantiate_spawn, load_ladder_map, spawn_layout_nearest
from world.adapter import adapt

LOG = Path(__file__).parent / "docs" / "tank_production_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class TankProdSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._slots: dict[str, Point2] = {}
        self._placed: set[str] = set()
        self._bot = None

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            self._setup(gs)
        if self._runtime is not None and raw.seq != getattr(self, "_last_seq", -1):
            self._last_seq = raw.seq
            self._runtime.on_game_state(gs)
            self._verify(raw, gs)

    def on_session_event(self, event) -> None:
        pass

    def _setup(self, gs) -> None:
        log("=== tank production check start（工厂+挂件+4坦克）===")
        cc = next(u.position for u in gs.units
                  if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER")
        template = load_ladder_map()
        layout = spawn_layout_nearest(template, cc)
        layer = instantiate_spawn(template, layout, cc)
        for name in ("rax1", "factory1"):  # rax1（兵营落位）+ factory1（工厂落位）校验
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
        self._runtime.submit_queue("tank", [
            QueueItem(op="assign_workers", task="mineral", count=16),
            *[QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home"))
              for _ in range(2)],
            QueueItem(op="train", type="terran/scv", count=12),
            *[QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home"))
              for _ in range(6)],
            *[QueueItem(op="build", type="terran/refinery") for _ in range(2)],
            QueueItem(op="assign_workers", task="gas", count=6),
            # 兵营：factory 前置（PlacementInRegion 落 rax1，首个空闲 production 槽）
            QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
            # 工厂：PlacementExact 显式指 factory1（否则 PlacementInRegion 落 rax2）
            QueueItem(op="build", type="terran/factory", placement=PlacementExact("factory1")),
            # 科技挂件：addon，无 placement，吸附就绪空闲工厂（_try_build_addon）
            QueueItem(op="build", type="terran/factorytechlab"),
            QueueItem(op="train", type="terran/siegetank", count=4),
        ])
        self._bot = self._port._bot
        self._bot._apply_failures = []
        log(f"[setup] CC={cc} 模板出生点={layout.origin} 工厂预期落位={self._slots.get('factory1')}")
        log(f"[setup] tank 队列 = 矿工→补给×2→SCV×12→补给×6→精炼厂×2→气工→兵营→工厂(factory1)→科技挂件→坦克×4")

    def _verify(self, raw, gs) -> None:
        # 工厂落 factory1 校验
        for slot_name, type_name in (("rax1", "BARRACKS"), ("factory1", "FACTORY")):
            if slot_name in self._placed or slot_name not in self._slots:
                continue
            for u in gs.units:
                if u.owner is Owner.SELF and u.type_name == type_name:
                    ex = self._slots[slot_name]
                    dist = ((u.position.x - ex.x) ** 2 + (u.position.y - ex.y) ** 2) ** 0.5
                    if dist <= 0.75:
                        self._placed.add(slot_name)
                        log(f"[{slot_name}] OK 落位 {u.position}（预期 {ex}）")
                        break
        if raw.seq % 50 == 0:
            factory = self._count(gs, "FACTORY")
            techlab = self._count(gs, "FACTORYTECHLAB")
            tanks = self._count(gs, "SIEGETANK")
            depots = self._count(gs, "SUPPLYDEPOT")
            rax = self._count(gs, "BARRACKS")
            scvs = self._count(gs, "SCV")
            fails = getattr(self._bot, "_apply_failures", []) if self._bot else []
            flight = {k: [(f["type"], f["frames"]) for f in v] for k, v in
                      self._runtime._build_flights.items()}  # 并行建造后是 list（原 _build_flight 已改名）
            dropped = [(i.type, r) for i, r in self._runtime.dropped][-3:]
            log(f"[tick] seq={raw.seq} t={raw.game_time:.0f} 矿={gs.minerals} 气={gs.vespene} "
                f"supply={gs.supply_used}/{gs.supply_cap} depot={depots} rax={rax} factory={factory} "
                f"techlab={techlab} tank={tanks} scv={scvs} 落位={sorted(self._placed)} "
                f"apply_failures={fails[-3:]} flight={flight} dropped={dropped}")
            if tanks >= 4:
                log(f"[done] 4 坦克训练完成（t={raw.game_time:.0f}）；工厂/挂件/坦克实体计数见上")

    def _count(self, gs, type_name) -> int:
        return sum(1 for u in gs.units if u.owner is Owner.SELF and u.type_name == type_name)


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=700, realtime=False,
    )
    sink = TankProdSink(port)
    port.set_sink(sink)
    port.start("tank-prod-check-1")
    log(f"=== tank production check done === 落位 {sorted(sink._placed)} "
        f"dropped={len(sink._runtime.dropped) if sink._runtime else 0}")


if __name__ == "__main__":
    main()
