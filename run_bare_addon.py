"""挂件建造裸实验（隔离 burnysc2/SC2 层；手动跑）：不经过我们的任何模块。

裸 bot 直接：建补给站 → 建兵营 → 对兵营发 BUILD_REACTOR_BARRACKS（三个变体轮换：
a) rax(build_ability) 旧式直发  b) self.do(rax(...))  c) rax.build(REACTOR)）。
每 10 步记录：兵营 orders、REACTOR 实体、矿气。
输出：docs/bare_addon.log
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger
from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

LOG = Path(__file__).parent / "docs" / "bare_addon.log"
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "a"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class BareBot(BotAI):
    _did_reactor = False

    async def on_step(self, iteration: int) -> None:
        cc = self.townhalls.first
        rax = self.structures(UnitTypeId.BARRACKS).ready.first             if self.structures(UnitTypeId.BARRACKS).ready else None
        # 建补给站（CC 左上，裸 bot 固定位置）
        if self.minerals >= 100 and not self.structures(UnitTypeId.SUPPLYDEPOT):
            scv = self.workers.first
            scv.build(UnitTypeId.SUPPLYDEPOT, Point2((cc.position.x - 8, cc.position.y + 4)))
            log(f"[{iteration}] 发补给站建造 @ {cc.position.x - 8},{cc.position.y + 4}")
        # 建兵营
        if (self.minerals >= 150 and self.structures(UnitTypeId.SUPPLYDEPOT).ready
                and not self.structures(UnitTypeId.BARRACKS)):
            scv = self.workers.first
            scv.build(UnitTypeId.BARRACKS, Point2((cc.position.x - 7, cc.position.y + 12)))
            log(f"[{iteration}] 发兵营建造 @ {cc.position.x - 7},{cc.position.y + 12}")
        # 挂件三变体
        if rax is not None and self.vespene >= 50 and not self._did_reactor:
            self._did_reactor = True
            if VARIANT == "a":
                rax(AbilityId.BUILD_REACTOR_BARRACKS)  # 旧式直发
            elif VARIANT == "b":
                self.do(rax(AbilityId.BUILD_REACTOR_BARRACKS))
            else:
                rax.build(UnitTypeId.REACTOR)
            log(f"[{iteration}] VARIANT={VARIANT} 对兵营 {rax.tag} @ {rax.position} 发挂件命令")
        if iteration % 10 == 0:
            reactors = [(u.tag, u.position, round(u.build_progress, 2)) for u in
                        self.structures(UnitTypeId.REACTOR) | self.units(UnitTypeId.REACTOR)]
            rax_orders = [(u.tag, [o.ability.button_name for o in u.orders]) for u in
                          self.structures(UnitTypeId.BARRACKS)]
            log(f"[{iteration}] t={self.time:.1f} 矿={self.minerals} 气={self.vespene} "
                f"rax={rax_orders} reactors={reactors}")


def main() -> None:
    LOG.unlink(missing_ok=True)
    run_game(
        maps.get("LadderMap"),
        [Bot(Race.Terran, BareBot()), Computer(Race.Random, Difficulty.Easy)],
        realtime=False, game_time_limit=200,
    )


if __name__ == "__main__":
    main()
