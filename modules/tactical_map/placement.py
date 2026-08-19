"""tactical_map.placement：建筑位 BuildSlot（TL+BR+size）与非建造点位 PosMark。

footprint 换算（ADR-0027 §3 的唯一实现点；**已用真机观测锁定**，勿在别处复制奇偶分支）：

SC2 建筑放置在半格网格上，命令点 P = footprint 矩形中心（按半格取）：
- 奇数尺寸（3×3/5×5）：中心落在格心 → P = TL + size/2（如兵营 TL(43,27) → P=(44.5,28.5)）
- 偶数尺寸（2×2）：中心落在格角 → P = TL + (size-1)/2（如补给站 TL(40,32) → P=(40.5,32.5)）
统一表达（真机 fixture 验证）：
- 命令点 → footprint 格点：TL = ceil(P - size/2)，BR = TL + size - 1
- SC2 报告的实体位置 = footprint 矩形中心 = TL + size/2（两种尺寸统一：
  2×2 报告 (132,108)、3×3 报告 (44.5,28.5) 均与真机一致）
- 报告位置 → TL：int(R - size/2)（R - N/2 恒为整数）
教训：旧的 tl+size/2 反推对偶数尺寸错 0.5 格（SC2 把 2×2 建筑锚在格角而非格心）。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

from game import GridPos, Point2


def _corner_offset(size: int) -> float:
    """TL → 命令点 的偏移（奇偶分支集中在此，ADR-0027 §3）。"""
    return size / 2.0 if size % 2 else (size - 1) / 2.0


@dataclass(frozen=True, slots=True)
class BuildSlot:
    """一个可建造位（矩形格点区域）：tl + br + size（ADR-0027 双角点表示）。

    pos 可选：真机校准过的世界建造点（can_place/实测）；None 时用 build_point 公式。
    """

    name: str  # "depot1"（声明顺序 = 建造顺序）
    tl: GridPos  # footprint 左下角格点（min corner；与 pos 的关系见模块 docstring）
    size: int  # 2（补给站）/ 3（兵营/工厂/星港）/ 5（基地）
    kind: str = "production"  # 槽位类别：supply（补给）/ production（生产建筑）/ addon（挂件预留，不参与放置）
    pos: Point2 | None = None  # 校准过的世界建造点（权威；driver build 用它）

    @property
    def br(self) -> GridPos:
        """对顶角格点：br = tl + size - 1。"""
        return GridPos(self.tl.x + self.size - 1, self.tl.y + self.size - 1)

    @property
    def build_point(self) -> Point2:
        """实际下发给 driver 的世界建造点：校准值优先，否则按锁定公式从 TL 推。"""
        if self.pos is not None:
            return self.pos
        off = _corner_offset(self.size)
        return Point2(self.tl.x + off, self.tl.y + off)

    @property
    def reported_position(self) -> Point2:
        """SC2 报告该建筑实体的位置 = footprint 矩形中心 = TL + size/2（真机锁定）。"""
        return Point2(self.tl.x + self.size / 2.0, self.tl.y + self.size / 2.0)

    @staticmethod
    def tl_from_pos(pos: Point2, size: int) -> GridPos:
        """世界命令点 → footprint min 格点：TL = ceil(P - size/2)（2/3/5 统一；真机验证）。"""
        return GridPos(int(ceil(pos.x - size / 2.0)), int(ceil(pos.y - size / 2.0)))

    @staticmethod
    def tl_from_reported(pos: Point2, size: int) -> GridPos:
        """SC2 报告位置 → footprint min 格点：TL = floor(R - size/2)（R - N/2 恒为整数；floor 对负边角也正确）。"""
        return GridPos(int(floor(pos.x - size / 2.0)), int(floor(pos.y - size / 2.0)))

    def contains(self, cell: GridPos) -> bool:
        """格点是否落在 footprint 闭区间 [tl.x, br.x] × [tl.y, br.y] 内。"""
        return (self.tl.x <= cell.x <= self.br.x) and (self.tl.y <= cell.y <= self.br.y)


@dataclass(frozen=True, slots=True)
class PosMark:
    """非建造的点位标记（集结点、坦克驻扎点等）。"""

    name: str  # "rally"
    pos: Point2
    description_zh: str = ""
