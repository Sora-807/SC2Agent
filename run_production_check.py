"""Driver 生产半边真机验证（需 SC2 环境，手动跑；不进 pytest）。

真实对局里跑生产运行时整链：build 补给站 → train SCV×3 → assign_workers 往返。
证据（客观）：
- build：补给站实体出现（build_progress 增长）+ 矿物扣减 ~100
- train：SCV 数量 8→11（每训 +1）
- assign_workers：idle 让 2 个 SCV 停止采集（Gather 数下降）→ mineral 再派回（回升）
跑：uv run python run_production_check.py
输出：docs/production_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from game import Grid, GridPos, Operation, QueueItem, Owner, Point2
from game.catalog import load_terran
from game.production import PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from sc2.data import Difficulty, Race
from production.runtime import ProductionRuntime
from tactical_map import BigRegion, BuildSlot, Region, RegionLayer
from world.adapter import adapt

LOG = Path(__file__).parent / "docs" / "production_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def _make_layer(cc: Point2) -> RegionLayer:
    """对局内动态构造区域层：围绕 CC 的候选 build_slots（出生点随机，逐个试）。"""
    offs = [(-3, 2), (3, 2), (2, -3), (-2, -3), (6, 0), (-6, 0), (0, 6), (0, -6)]
    slots: dict[str, BuildSlot] = {}
    names: list[str] = []
    for i, (dx, dy) in enumerate(offs):
        n = f"s{i}"
        names.append(n)
        slots[n] = BuildSlot(name=n, tl=GridPos(int(cc.x) + dx, int(cc.y) + dy), size=2)
    cell = (int(cc.x), int(cc.y))
    return RegionLayer(
        map_name="ladder", size=(176, 160),
        big_grid=Grid(176, 160, [[1] * 176 for _ in range(160)]),
        big_index={1: "main_base"},
        big_regions={"main_base": BigRegion(stable_id="main_base", anchor=cc)},
        leaf_grid=None, leaf_index={},
        regions={"home": Region(stable_id="home", parent="main_base",
                                cells=frozenset({cell}), anchor=cc,
                                build_slots=tuple(names))},
        pos_marks={}, build_slots=slots,
    )


class ProdCheckSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._initial_scvs: int | None = None
        self._depot_seen = False
        self._train_seen = False
        self._gatherers_at_idle: int | None = None
        self._gatherers_at_mine: int | None = None
        self._idle_done = False
        self._mine_done = False

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            self._setup(raw, gs)
        if self._runtime is not None:
            self._runtime.on_game_state(gs)
            self._phase_control(raw, gs)
            self._verify(raw, gs)

    def on_session_event(self, event) -> None:
        pass

    def _setup(self, raw, gs) -> None:
        log("=== production check start ===")
        ccs = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER"]
        if not ccs:
            log("[setup] 找不到 CC，无法验证")
            return
        cc = ccs[0].position
        self._initial_scvs = len([u for u in gs.units if u.owner is Owner.SELF and u.type_name == "SCV"])
        layer = _make_layer(cc)
        self._runtime = ProductionRuntime(load_terran(), self._port, region_layer=layer)
        self._runtime.submit_queue("opening", [
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="train", type="terran/scv", count=3),
        ])
        log(f"[setup] CC={cc} 初始 SCV={self._initial_scvs}；opening 队列 = build 补给站 + train SCV×3")

    def _phase_control(self, raw, gs) -> None:
        if raw.seq == 60 and not self._idle_done:
            self._gatherers_at_idle = self._gatherers(gs)
            self._runtime.submit_queue("idle_q", [QueueItem(op="assign_workers", task="idle", count=2)])
            log(f"[phase] seq {raw.seq}：assign_workers(idle,2)，当前采集中 SCV={self._gatherers_at_idle}")
            self._idle_done = True  # 提交标记（效果待 _verify 观察）
        if raw.seq == 90 and self._idle_done and not self._mine_done:
            self._gatherers_at_mine = self._gatherers(gs)
            self._runtime.submit_queue("mine_q", [QueueItem(op="assign_workers", task="mineral", count=2)])
            log(f"[phase] seq {raw.seq}：assign_workers(mineral,2)，当前采集中 SCV={self._gatherers_at_mine}")
            self._mine_done = True

    def _verify(self, raw, gs) -> None:
        if self._runtime is None:
            return
        own_scvs = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "SCV"]
        if not self._depot_seen:
            depots = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "SUPPLYDEPOT"]
            if depots:
                self._depot_seen = True
                log(f"[build] OK 补给站实体出现（seq {raw.seq}，progress={depots[0].build_progress:.2f}，minerals={gs.minerals}）")
        if not self._train_seen and self._initial_scvs and len(own_scvs) > self._initial_scvs:
            self._train_seen = True
            log(f"[train] OK SCV 数量 {self._initial_scvs}→{len(own_scvs)}（seq {raw.seq}）")
        if self._idle_done and self._gatherers_at_idle is not None and self._gatherers(gs) <= self._gatherers_at_idle - 2:
            self._gatherers_at_idle = None  # 只报一次
            log(f"[idle] OK 采集中 SCV 降到 {self._gatherers(gs)}（seq {raw.seq}，应 -2）")
        if self._mine_done and self._gatherers_at_mine is not None and self._gatherers(gs) >= self._gatherers_at_mine + 2:
            self._gatherers_at_mine = None
            log(f"[mineral] OK 采集中 SCV 回到 {self._gatherers(gs)}（seq {raw.seq}，应 +2）")
        if raw.seq % 25 == 0:
            log(f"[tick] seq={raw.seq} t={raw.game_time:.1f} minerals={gs.minerals} scvs={len(own_scvs)} gatherers={self._gatherers(gs)}")

    def _gatherers(self, gs) -> int:
        return sum(
            1 for u in gs.units
            if u.owner is Owner.SELF and u.type_name == "SCV"
            and any("gather" in (o.ability or "").lower() for o in u.orders)
        )


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=120, realtime=False,
    )
    sink = ProdCheckSink(port)
    port.set_sink(sink)
    port.start("prod-check-1")
    log(f"=== production check done === depot={sink._depot_seen} train={sink._train_seen} dropped={len(sink._runtime.dropped) if sink._runtime else 0}")


if __name__ == "__main__":
    main()
