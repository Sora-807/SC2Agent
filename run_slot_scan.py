"""LadderMap 主基建造位扫描（tactical_map authoring 校准工具，需 SC2；手动跑）。

用 burnysc2 的 can_place（不花矿）扫描 CC 周围候选格点对 SUPPLYDEPOT(2×2) 与
BARRACKS(3×3) 的可建造性，输出出生点 + 可建造点（世界坐标 + 相对 CC 偏移），
供 authoring modules/tactical_map/data/ladder_map 的固定有序建造位。

跑：uv run python run_slot_scan.py
输出：docs/slot_scan.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from driver.sc2_adapter import SC2DriverBot, SC2GamePort
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2 as SC2Point2

OUT_SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""  # 输出文件名后缀（多次跑不互相覆盖）
LOG = Path(__file__).parent / "docs" / f"slot_scan{OUT_SUFFIX}.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class ScanBot(SC2DriverBot):
    """迭代 0：定位 CC → can_place 扫描候选格点 → 输出可建造点。"""

    async def on_step(self, iteration: int) -> None:
        if iteration != 0:
            return
        cc = self.townhalls.first
        log(f"=== scan start === spawn CC={cc.position} map_size={self.game_info.map_size}")
        # 宽范围双向扫描（步长 1：错位布局需要奇数行偏移；任何出生点一次扫全）
        pts: list[tuple[float, float]] = []
        for dx in range(-16, 17):
            for dy in range(-16, 27):
                pts.append((cc.position.x + dx, cc.position.y + dy))
        positions = [SC2Point2((x, y)) for x, y in pts]
        for unit_type, label in ((UnitTypeId.SUPPLYDEPOT, "depot"), (UnitTypeId.BARRACKS, "rax")):
            ok = await self.can_place(unit_type, positions)
            buildable = [pts[i] for i, b in enumerate(ok) if b]
            log(f"[{label}] 可建造 {len(buildable)}/{len(pts)} 个")
            for x, y in buildable:
                log(f"BUILDABLE {label} world=({x:.1f},{y:.1f}) dx={x - cc.position.x:+.1f} dy={y - cc.position.y:+.1f}")
        log("=== scan done ===")


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=30, realtime=False, bot_cls=ScanBot,
    )
    port.start("slot-scan-1")


if __name__ == "__main__":
    main()
