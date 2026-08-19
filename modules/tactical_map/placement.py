"""tactical_map.placement：建筑位 BuildSlot（TL+BR+size）与非建造点位 PosMark。

ADR-0027：内部一律用 TL+BR+size 双角点表示建筑位，不允许只存中心点。
偶数尺寸（2×2）建筑的世界坐标换算由 driver 统一实现（ADR-0027 §3），
这里只存格点，不做奇偶特判。
"""
from __future__ import annotations

from dataclasses import dataclass

from game import GridPos, Point2


@dataclass(frozen=True, slots=True)
class BuildSlot:
    """一个可建造位（矩形格点区域）：tl + br + size（ADR-0027 双角点表示）。"""

    name: str  # "main_ramp_slot_1"
    tl: GridPos  # 左下角格点（min corner）
    size: int  # 2（补给站）/ 3（兵营/工厂/星港）/ 5（基地）

    @property
    def br(self) -> GridPos:
        """对顶角格点：br = tl + size - 1。"""
        return GridPos(self.tl.x + self.size - 1, self.tl.y + self.size - 1)

    def contains(self, cell: GridPos) -> bool:
        """格点是否落在 footprint 闭区间 [tl.x, br.x] × [tl.y, br.y] 内。"""
        return (self.tl.x <= cell.x <= self.br.x) and (self.tl.y <= cell.y <= self.br.y)


@dataclass(frozen=True, slots=True)
class PosMark:
    """非建造的点位标记（集结点、坦克驻扎点等）。"""

    name: str  # "rally"
    pos: Point2
    description_zh: str = ""
