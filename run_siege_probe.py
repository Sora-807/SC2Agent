"""攻城坦克架起/收起决定性实验（裸 burnysc2；手动跑）：验证 siege/unsiege 能力路径 + type_id 形态变体。

前置（全部校准位）：补给站 → 精炼厂（最近气井）+ 3 气工 → 兵营（factory 前置）→ 工厂 →
工厂科技挂件（BUILD_TECHLAB 通用能力，挂件右下 2×2 吸附）→ 训练攻城坦克。
然后发通用 AbilityId.SIEGEMODE_SIEGEMODE（同 driver._t_siege 的 __call__ 路径），读 type_id
变化（预期 SIEGETANK → SIEGETANKSIEGED）；检测到架起态后发 UNSIEGE_UNSIEGE 观测回变。
证据喂给 T3 形态变体归一化（catalog variants 反查）。
输出：docs/siege_probe.log

注：工厂/挂件坐标为探针级近似（T6 才真机 can_place 扫描锁定正式 slot）；blocked 时 log 会显示无实体。
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

LOG = Path(__file__).parent / "docs" / "siege_probe.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


BL = {"depot": Point2((40.5, 32.5)), "rax": Point2((42.5, 42.5)), "factory": Point2((44.5, 52.5))}
TR = {"depot": Point2((131.5, 107.5)), "rax": Point2((117.5, 110.5)), "factory": Point2((115.5, 120.5))}


class SiegeProbeBot(BotAI):
    _did_depot = False
    _did_depot2 = False
    _refinery_iter = -99
    _did_rax = False
    _did_factory = False
    _did_techlab = False
    _tank_trained = False
    _did_siege = False
    _did_unsiege = False

    async def on_start(self) -> None:
        self.unit_command_uses_self_do = True

    async def on_step(self, iteration: int) -> None:
        cc = self.townhalls.first.position
        pos = TR if cc.x > 90 else BL
        depot_ready = self.structures(UnitTypeId.SUPPLYDEPOT).ready
        refinery_ready = self.gas_buildings.ready
        rax_ready = self.structures(UnitTypeId.BARRACKS).ready
        factory_ready = self.structures(UnitTypeId.FACTORY).ready
        techlab_ready = (self.structures(UnitTypeId.FACTORYTECHLAB).ready
                         | self.units(UnitTypeId.FACTORYTECHLAB).ready)
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
        # 第二个补给站（坦克 3 供应 + 后续维持）
        if not self._did_depot2 and depot_ready and self.supply_left <= 4 \
                and self.minerals >= 100:
            self._did_depot2 = True
            self.do(self.workers.random.build(UnitTypeId.SUPPLYDEPOT, pos["depot"]))
            log(f"[{iteration}] 发第二补给站（supply_left={self.supply_left}）")
        # 兵营：factory 前置（精炼厂命令 2 帧后、换工兵避免同帧同 SCV 去重）
        if (not self._did_rax and depot_ready and iteration - self._refinery_iter >= 2
                and self.minerals >= 150):
            self._did_rax = True
            builder = self.workers.random
            self.do(builder.build(UnitTypeId.BARRACKS, pos["rax"]))
            log(f"[{iteration}] 发兵营 @ {pos['rax']}（factory 前置，SCV={builder.tag}）")
        # 工厂
        if (not self._did_factory and rax_ready and self.minerals >= 150 and self.vespene >= 100):
            self._did_factory = True
            builder = self.workers.random
            self.do(builder.build(UnitTypeId.FACTORY, pos["factory"]))
            log(f"[{iteration}] 发工厂 @ {pos['factory']}（SCV={builder.tag}）")
        # 科技挂件：通用 BUILD_TECHLAB 能力（挂件右下 2×2 吸附，同挂件 BUILD_REACTOR 路径）
        if factory_ready and self.vespene >= 25 and not self._did_techlab:
            self._did_techlab = True
            fac = self.structures(UnitTypeId.FACTORY).ready.first
            self.do(fac(AbilityId.BUILD_TECHLAB))
            log(f"[{iteration}] 对工厂 {fac.tag} @ {fac.position} 发 BUILD_TECHLAB")
        # 训练攻城坦克（factory + techlab 就绪）
        if (not self._tank_trained and techlab_ready and self.minerals >= 150
                and self.vespene >= 125 and self.supply_left >= 3):
            self._tank_trained = True
            fac = self.structures(UnitTypeId.FACTORY).ready.first
            self.do(fac.train(UnitTypeId.SIEGETANK))
            log(f"[{iteration}] 训练攻城坦克（factory={fac.tag}）")
        # 架起：检测到 SIEGETANK 实体后发 SIEGEMODE（driver._t_siege 的 __call__ 路径）
        if not self._did_siege and self.units(UnitTypeId.SIEGETANK):
            self._did_siege = True
            tank = self.units(UnitTypeId.SIEGETANK).first
            self.do(tank(AbilityId.SIEGEMODE_SIEGEMODE))
            log(f"[{iteration}] 对坦克 {tank.tag} 发 SIEGEMODE → 观测 type_id 变 SIEGETANKSIEGED")
        # 收起：检测到架起态后发 UNSIEGE → 观测回变
        if not self._did_unsiege and self.units(UnitTypeId.SIEGETANKSIEGED):
            self._did_unsiege = True
            tank = self.units(UnitTypeId.SIEGETANKSIEGED).first
            self.do(tank(AbilityId.UNSIEGE_UNSIEGE))
            log(f"[{iteration}] 检测到 SIEGETANKSIEGED → 发 UNSIEGE → 观测回变 SIEGETANK")
        if iteration % 10 == 0:
            tanks = self.units(UnitTypeId.SIEGETANK) | self.units(UnitTypeId.SIEGETANKSIEGED)
            tank_info = [(u.tag, u.type_id.name, [o.ability.button_name for o in u.orders])
                         for u in tanks]
            log(f"[{iteration}] t={self.time:.1f} 矿={self.minerals} 气={self.vespene} "
                f"sup={self.supply_used}/{self.supply_cap} tanks={tank_info}")


def main() -> None:
    LOG.unlink(missing_ok=True)
    run_game(
        maps.get("LadderMap"),
        [Bot(Race.Terran, SiegeProbeBot()), Computer(Race.Random, Difficulty.Easy)],
        realtime=False, game_time_limit=480,
    )


if __name__ == "__main__":
    main()
