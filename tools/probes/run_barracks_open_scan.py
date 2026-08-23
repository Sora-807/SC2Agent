"""LadderMap 兵营位扫描 V2（上方区域 + 周围空地评分，需 SC2；手动跑）。

改进版扫描：
1. 筛选 CC 上方区域（dy > 0，地图上方）的 BARRACKS 可建造点
2. 对每个候选位 + 挂件 2×2，检查周围 5×5 范围内有多少格可建造（空地评分）
3. 按空地评分排序输出——周围空地大 = 单位可自由通行不堵门

跑：uv run python run_barracks_open_scan.py [out_suffix]
输出：docs/barracks_open_scan<suffix>.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from driver.sc2_adapter import SC2DriverBot, SC2GamePort
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2 as SC2Point2

OUT_SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""
LOG = Path(__file__).resolve().parents[2] / "docs" / f"barracks_open_scan{OUT_SUFFIX}.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class BarracksOpenScanBot(SC2DriverBot):
    """扫描 CC 上方区域 BARRACKS 可建造点 + 挂件 + 周围空地评分。"""

    async def on_step(self, iteration: int) -> None:
        if iteration != 0:
            return
        cc = self.townhalls.first
        ccx, ccy = cc.position.x, cc.position.y
        log(f"=== barracks open scan start === spawn CC=({ccx:.1f},{ccy:.1f}) "
            f"map_size={self.game_info.map_size}")

        # 扫描范围：CC 周围 -16..+16，步长 1
        pts: list[tuple[float, float]] = []
        for dx in range(-16, 17):
            for dy in range(-16, 17):
                pts.append((ccx + dx, ccy + dy))

        positions = [SC2Point2((x, y)) for x, y in pts]
        ok = await self.can_place(UnitTypeId.BARRACKS, positions)
        buildable = [pts[i] for i, b in enumerate(ok) if b]
        log(f"[barracks 3×3] 可建造 {len(buildable)}/{len(pts)} 个")

        # 验挂件 2×2（右下偏移 +2,-1）
        addon_positions = [SC2Point2((x + 2, y - 1)) for x, y in buildable]
        addon_ok = await self.can_place(UnitTypeId.SUPPLYDEPOT, addon_positions) if addon_positions else []
        both = [(x, y) for (x, y), a in zip(buildable, addon_ok) if a]
        log(f"[barracks+addon] 兵营位+挂件 2×2 都空 {len(both)} 个")

        # 空地评分：对每个候选位，检查周围 ±3 格内有多少格可放 2×2（SUPPLYDEPOT）
        # 高分 = 周围空地大 = 单位可自由通行
        log("[空地评分] 检查每个候选位周围 ±3 格的可建造性（2×2 SUPPLYDEPOT）...")
        scored: list[tuple[int, float, float]] = []
        # 批量检查所有周围格点（一次性 can_place 调用提速）
        surround_pts: list[tuple[float, float, float, float]] = []  # (bx, by, sx, sy)
        for bx, by in both:
            for sdx in range(-3, 4):
                for sdy in range(-3, 4):
                    if sdx == 0 and sdy == 0:
                        continue  # 跳过兵营自身位置
                    surround_pts.append((bx, by, bx + sdx, by + sdy))

        # 去重周围格点（多个兵营可能共享同一周围格点）
        unique_surround = list(set((sx, sy) for _, _, sx, sy in surround_pts))
        surround_positions = [SC2Point2((x, y)) for x, y in unique_surround]
        surround_ok = await self.can_place(UnitTypeId.SUPPLYDEPOT, surround_positions) if surround_positions else []
        surround_map = {pos: ok for pos, ok in zip(unique_surround, surround_ok)}

        for bx, by in both:
            score = 0
            for sdx in range(-3, 4):
                for sdy in range(-3, 4):
                    if sdx == 0 and sdy == 0:
                        continue
                    if surround_map.get((bx + sdx, by + sdy), False):
                        score += 1
            scored.append((score, bx, by))

        # 按空地评分降序输出（上方区域 dy > 0 优先标注）
        scored.sort(reverse=True)
        upper = [(s, x, y) for s, x, y in scored if y > ccy]
        lower = [(s, x, y) for s, x, y in scored if y <= ccy]
        log(f"[上方区域 dy>0] {len(upper)} 个（推荐）：")
        for s, x, y in upper[:20]:
            log(f"  OPEN={s:2d} world=({x:.1f},{y:.1f}) dx={x - ccx:+.1f} dy={y - ccy:+.1f} "
                f"addon=({x + 2:.1f},{y - 1:.1f})")
        log(f"[下方区域 dy<=0] {len(lower)} 个：")
        for s, x, y in lower[:10]:
            log(f"  OPEN={s:2d} world=({x:.1f},{y:.1f}) dx={x - ccx:+.1f} dy={y - ccy:+.1f} "
                f"addon=({x + 2:.1f},{y - 1:.1f})")

        # 找楼梯对（+3,+2 偏移，两个都有挂件空间）
        both_set = {(round(x, 1), round(y, 1)) for x, y in both}
        scored_map = {(round(x, 1), round(y, 1)): s for s, x, y in scored}
        stairs = []
        for s, x, y in upper:
            partner = (round(x + 3, 1), round(y + 2, 1))
            if partner in both_set:
                partner_score = scored_map.get(partner, 0)
                total_score = s + partner_score
                stairs.append((total_score, x, y, x + 3, y + 2, s, partner_score))
        stairs.sort(reverse=True)
        log(f"[上方楼梯对] {len(stairs)} 对（按空地总分降序）：")
        for ts, x1, y1, x2, y2, s1, s2 in stairs[:10]:
            log(f"  STAIR_OPEN={ts:3d} rax1=({x1:.1f},{y1:.1f})[{s1}] rax2=({x2:.1f},{y2:.1f})[{s2}] "
                f"addon1=({x1 + 2:.1f},{y1 - 1:.1f}) addon2=({x2 + 2:.1f},{y2 - 1:.1f})")

        log("=== barracks open scan done ===（选 STAIR_OPEN 高分对回填 base_layout rax1/rax2）")


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=30, realtime=False, bot_cls=BarracksOpenScanBot,
    )
    port.start("barracks-open-scan-1")


if __name__ == "__main__":
    main()
