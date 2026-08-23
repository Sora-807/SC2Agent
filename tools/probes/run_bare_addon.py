"""挂件能力决定性实验 v5（裸 burnysc2；手动跑）：通用 BUILD_REACTOR + 专属实体类型。

前置（全部校准位）：补给站 → 精炼厂（最近气井）+ 3 气工 → 兵营（孤立）。
然后发通用 AbilityId.BUILD_REACTOR（真机锁定：build() 静默失败、BUILD_REACTOR_BARRACKS 无实体产出），
读 BARRACKSREACTOR 实体报告位置 → 反推真实贴附格点（验证右下 2×2 预留模型）。
输出：docs/evidence/bare_addon.log
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger
from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

LOG = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "bare_addon.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


BL = {"depot": Point2((40.5, 32.5)), "rax": Point2((42.5, 42.5))}
TR = {"depot": Point2((131.5, 107.5)), "rax": Point2((117.5, 110.5))}


class BareBot(BotAI):
    _did_depot = False
    _refinery_iter = -99
    _did_rax = False
    _did_reactor = False

    async def on_start(self) -> None:
        self.unit_command_uses_self_do = True

    async def on_step(self, iteration: int) -> None:
        cc = self.townhalls.first.position
        pos = TR if cc.x > 90 else BL
        depot_ready = self.structures(UnitTypeId.SUPPLYDEPOT).ready
        refinery_ready = self.gas_buildings.ready
        rax = self.structures(UnitTypeId.BARRACKS).ready.first \
            if self.structures(UnitTypeId.BARRACKS).ready else None
        if not self._did_depot and self.minerals >= 100 and not self.structures(UnitTypeId.SUPPLYDEPOT):
            self._did_depot = True
            self.do(self.workers.first.build(UnitTypeId.SUPPLYDEPOT, pos["depot"]))
            log(f"[{iteration}] 发补给站 @ {pos['depot']}")
        if self._refinery_iter < 0 and depot_ready and self.minerals >= 75 \
                and not self.gas_buildings and self.all_units.vespene_geyser:
            self._refinery_iter = iteration
            geyser = self.all_units.vespene_geyser.closest_to(cc)
            self.do(self.workers.first.build(UnitTypeId.REFINERY, geyser))
            log(f"[{iteration}] 发精炼厂 @ 气井 {geyser.tag} {geyser.position}")
        # 派 3 气工（只一次）
        if refinery_ready and not hasattr(self, "_gas_done"):
            self._gas_done = True
            refinery = self.gas_buildings.ready.first
            for w in self.workers.idle.take(3):
                self.do(w.gather(refinery))
            log(f"[{iteration}] 派 3 气工")
        # 兵营：精炼厂命令 2 帧后、换工兵（避免同帧同 SCV 去重）
        if (not self._did_rax and depot_ready and iteration - self._refinery_iter >= 2
                and self.minerals >= 150):
            self._did_rax = True
            builder = self.workers.random
            self.do(builder.build(UnitTypeId.BARRACKS, pos["rax"]))
            log(f"[{iteration}] 发兵营 @ {pos['rax']}（孤立，SCV={builder.tag}）")
        if rax is not None and self.vespene >= 50 and not self._did_reactor:
            self._did_reactor = True
            self.do(rax(AbilityId.BUILD_REACTOR))  # 通用能力（CREATION_ABILITY_FIX 的命名规律）
            log(f"[{iteration}] 对兵营 {rax.tag} @ {rax.position} 发 BUILD_REACTOR 通用能力")
        if iteration % 10 == 0:
            # 挂件实体 = 父建筑专属类型（BARRACKSREACTOR=38）；通用 REACTOR=6 在游戏里不产出实体
            reactors = [(u.tag, u.position) for u in
                        self.structures(UnitTypeId.BARRACKSREACTOR) | self.units(UnitTypeId.BARRACKSREACTOR)]
            rax_info = [(u.tag, u.position, [o.ability.button_name for o in u.orders])
                        for u in self.structures(UnitTypeId.BARRACKS)]
            log(f"[{iteration}] t={self.time:.1f} 矿={self.minerals} 气={self.vespene} "
                f"rax={rax_info} reactors={reactors}")


def main() -> None:
    LOG.unlink(missing_ok=True)
    run_game(
        maps.get("LadderMap"),
        [Bot(Race.Terran, BareBot()), Computer(Race.Random, Difficulty.Easy)],
        realtime=False, game_time_limit=320,
    )


if __name__ == "__main__":
    main()
