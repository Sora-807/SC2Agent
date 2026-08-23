"""LadderMap 工厂位扫描（T6 authoring 校准工具，需 SC2；手动跑）。

用 burnysc2 的 can_place（不花矿）扫描 CC 周围候选格点对 FACTORY(3×3) 的可建造性，
输出出生点 + 可建造点（世界坐标 + 相对 CC 偏移），供 authoring
modules/tactical_map/data/ladder_map/base_layout.yaml 的 factory1 槽锁定坐标。

选点后另验：工厂右下 2×2（科技挂件吸附位）需空闲——SC2 在发 BUILD_TECHLAB 时才校验，
故建工厂后试挂件；若挂件订单静默消失，换一个 BUILDABLE factory 点。

跑：uv run python run_factory_slot_scan.py [out_suffix]
输出：docs/factory_slot_scan<suffix>.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from driver.sc2_adapter import SC2DriverBot, SC2GamePort
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2 as SC2Point2

OUT_SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""  # 输出文件名后缀（多次跑不互相覆盖）
LOG = Path(__file__).resolve().parents[2] / "docs" / f"factory_slot_scan{OUT_SUFFIX}.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class FactoryScanBot(SC2DriverBot):
    """迭代 0：定位 CC → can_place 扫描候选格点 → 输出 FACTORY 可建造点。"""

    async def on_step(self, iteration: int) -> None:
        if iteration != 0:
            return
        cc = self.townhalls.first
        log(f"=== factory scan start === spawn CC={cc.position} map_size={self.game_info.map_size}")
        # 宽范围双向扫描（步长 1：3×3 错位布局需奇数行偏移；任何出生点一次扫全）
        pts: list[tuple[float, float]] = []
        for dx in range(-16, 17):
            for dy in range(-16, 27):
                pts.append((cc.position.x + dx, cc.position.y + dy))
        positions = [SC2Point2((x, y)) for x, y in pts]
        ok = await self.can_place(UnitTypeId.FACTORY, positions)
        buildable = [pts[i] for i, b in enumerate(ok) if b]
        log(f"[factory 3×3] 可建造 {len(buildable)}/{len(pts)} 个")
        # 同时验挂件 2×2（工厂右下 +2,-1，同 rax 挂件偏移）：两者都空才是合格工厂位
        addon_positions = [SC2Point2((x + 2, y - 1)) for x, y in buildable]
        addon_ok = await self.can_place(UnitTypeId.SUPPLYDEPOT, addon_positions) if addon_positions else []
        both = [(b, a) for b, a in zip(buildable, addon_ok) if a]
        log(f"[factory+addon] 工厂位+挂件 2×2 都空 {len(both)} 个（推荐选这些）")
        for (x, y), _ in both:
            log(f"FACTORY_ADDON_OK world=({x:.1f},{y:.1f}) dx={x - cc.position.x:+.1f} dy={y - cc.position.y:+.1f} addon=({x + 2:.1f},{y - 1:.1f})")
        log("=== factory scan done ===（选 FACTORY_ADDON_OK 点回填 base_layout factory1）")


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=30, realtime=False, bot_cls=FactoryScanBot,
    )
    port.start("factory-slot-scan-1")


if __name__ == "__main__":
    main()
