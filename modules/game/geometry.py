"""game.geometry：坐标与网格原语（全项目统一坐标约定，ADR-0002 §6）。

左下角为 (0,0)，x 向右增大，y 向上增大。
建筑/格点用整数 GridPos；单位位置用浮点 Point2，不做取整存储。
Grid 是通用二维网格（creep/visibility 等动态层共用，索引 data[y][x]）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class GridPos:
    """整数格点坐标（int(world_x), int(world_y)）。建筑 placement/creep 查格子用。"""

    x: int  # 格点列 = int(world_x)（正数 = floor）
    y: int  # 格点行 = int(world_y)


@dataclass(slots=True, frozen=True)
class Point2:
    """二维浮点坐标，左下原点。单位位置用浮点；建筑 placement 用 GridPos。"""

    x: float  # 横坐标，向右增大
    y: float  # 纵坐标，向上增大

    @property
    def grid_x(self) -> int:
        """格点列 = int(x)（正数 = floor）。"""
        return int(self.x)

    @property
    def grid_y(self) -> int:
        """格点行 = int(y)。"""
        return int(self.y)

    @property
    def grid_pos(self) -> GridPos:
        """格点坐标 GridPos(int(x), int(y))——访问时算，不存冗余。"""
        return GridPos(int(self.x), int(self.y))


@dataclass(slots=True, frozen=True)
class Grid:
    """通用二维网格数据类型，list[list[int]] 索引 data[y][x]。朝向已由 driver 对齐到 map grid。"""

    width: int  # 列数（= map_size[0]）
    height: int  # 行数（= map_size[1]）
    data: list[list[int]]  # data[y][x]；creep:0/1, visibility:0=Hidden/1=Fogged/2=Visible
