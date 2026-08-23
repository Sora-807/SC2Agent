"""Driver 生产半边真机验证（需 SC2 环境，手动跑；不进 pytest）。

用 tactical_map 的 LadderMap 主基建造模板（固定有序建造位）跑生产运行时整链：
build 补给站×2 → train SCV×3 → build 兵营 → assign_workers 往返。
证据（客观）：
- 固定位置：补给站实体落位 ≈ depot1/depot2 校准点；兵营实体落位 ≈ barracks1 校准点
- train：SCV 数量 8→11（每训 +1）
- assign_workers：idle 让 2 个 SCV 停止采集 → mineral 再派回
跑：uv run python run_production_check.py
输出：docs/production_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from game import QueueItem, Owner, Point2
from game.catalog import load_all
from game.production import PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from sc2.data import Difficulty, Race
from production.runtime import ProductionRuntime
from tactical_map import instantiate_spawn, load_ladder_map, spawn_layout_nearest
from world.adapter import adapt

LOG = Path(__file__).resolve().parents[2] / "docs" / "production_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class ProdCheckSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._initial_scvs: int | None = None
        self._expected: dict[str, Point2] = {}  # slot 名 → 预期世界落点（模板平移后）
        self._placed: set[str] = set()
        self._train_seen = False
        self._gatherers_at_idle: int | None = None
        self._gatherers_at_mine: int | None = None
        self._idle_submitted = False
        self._mine_submitted = False

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            self._setup(gs)
        if self._runtime is not None:
            self._runtime.on_game_state(gs)
            self._phase_control(raw, gs)
            self._verify(raw, gs)

    def on_session_event(self, event) -> None:
        pass

    def _setup(self, gs) -> None:
        log("=== production check start（LadderMap 主基建造模板）===")
        ccs = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER"]
        if not ccs:
            log("[setup] 找不到 CC，无法验证")
            return
        cc = ccs[0].position
        self._initial_scvs = len([u for u in gs.units if u.owner is Owner.SELF and u.type_name == "SCV"])
        template = load_ladder_map()
        layout = spawn_layout_nearest(template, cc)
        layer = instantiate_spawn(template, layout, cc)
        for name in ("depot1", "depot2", "barracks1"):
            bs = layer.build_slots.get(name)
            if bs is not None:
                self._expected[name] = bs.reported_position  # SC2 报告位置（真机锁定公式）
        orig_submit = self._port.submit_operations

        def logging_submit(ops):
            for o in ops:
                log(f"[op] {o.action} tags={o.unit_tags} params={o.params} seq={o.seq}")
            return orig_submit(ops)

        self._port.submit_operations = logging_submit  # type: ignore[method-assign]
        self._runtime = ProductionRuntime(load_all(), self._port, region_layer=layer)
        self._runtime.submit_queue("opening", [
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="train", type="terran/scv", count=3),
            QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
        ])
        log(f"[setup] CC={cc} 初始 SCV={self._initial_scvs} 模板出生点={layout.origin}")
        log(f"[setup] 预期固定位：{ {k: str(v) for k, v in self._expected.items()} }")
        log("[setup] opening 队列 = depot×2 → train SCV×3 → barracks（全部固定位置顺序摆放）")

    def _phase_control(self, raw, gs) -> None:
        if raw.seq == 110 and not self._idle_submitted:
            self._gatherers_at_idle = self._gatherers(gs)
            self._runtime.submit_queue("idle_q", [QueueItem(op="assign_workers", task="idle", count=2)])
            log(f"[phase] seq {raw.seq}：assign_workers(idle,2)，当前采集中 SCV={self._gatherers_at_idle}")
            self._idle_submitted = True
        if raw.seq == 140 and self._idle_submitted and not self._mine_submitted:
            self._gatherers_at_mine = self._gatherers(gs)
            self._runtime.submit_queue("mine_q", [QueueItem(op="assign_workers", task="mineral", count=2)])
            log(f"[phase] seq {raw.seq}：assign_workers(mineral,2)，当前采集中 SCV={self._gatherers_at_mine}")
            self._mine_submitted = True

    def _verify(self, raw, gs) -> None:
        if self._runtime is None:
            return
        own_scvs = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "SCV"]
        # 固定位置验证：实体落点 ≈ 校准点（容差 0.75）
        checks = {
            "depot1": "SUPPLYDEPOT", "depot2": "SUPPLYDEPOT", "barracks1": "BARRACKS",
        }
        for slot_name, type_name in checks.items():
            if slot_name in self._placed or slot_name not in self._expected:
                continue
            for u in gs.units:
                if u.owner is Owner.SELF and u.type_name == type_name:
                    dist = ((u.position.x - self._expected[slot_name].x) ** 2
                            + (u.position.y - self._expected[slot_name].y) ** 2) ** 0.5
                    if dist <= 0.75:
                        self._placed.add(slot_name)
                        log(f"[{slot_name}] OK 实体落位 {u.position}（预期 {self._expected[slot_name]}，dist={dist:.2f}，progress={u.build_progress:.2f}）")
                        break
        if not self._train_seen and self._initial_scvs and len(own_scvs) > self._initial_scvs:
            self._train_seen = True
            log(f"[train] OK SCV 数量 {self._initial_scvs}→{len(own_scvs)}（seq {raw.seq}）")
        if self._idle_submitted and self._gatherers_at_idle is not None and self._gatherers(gs) <= self._gatherers_at_idle - 2:
            self._gatherers_at_idle = None
            log(f"[idle] OK 采集中 SCV 降到 {self._gatherers(gs)}（seq {raw.seq}，应 -2）")
        if self._mine_submitted and self._gatherers_at_mine is not None and self._gatherers(gs) >= self._gatherers_at_mine + 2:
            self._gatherers_at_mine = None
            log(f"[mineral] OK 采集中 SCV 回到 {self._gatherers(gs)}（seq {raw.seq}，应 +2）")
        if raw.seq % 30 == 0:
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
        sink=None, game_time_limit=150, realtime=False,
    )
    sink = ProdCheckSink(port)
    port.set_sink(sink)
    port.start("prod-check-2")
    log(f"=== production check done === 固定位 {sorted(sink._placed)} / train={sink._train_seen} "
        f"dropped={len(sink._runtime.dropped) if sink._runtime else 0}")


if __name__ == "__main__":
    main()
