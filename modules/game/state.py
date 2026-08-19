"""game.state：处理后形态（D1 状态两面的"处理后"面，docs/P0-影响边界.md）。

world 把 game.raw.RawGameState 适配成 GameState：
- alliance + type → Owner 枚举（矿脉/气井/装饰物按 TYPE 判 neutral 并过滤）
- health/health_max → hp/hp_max、ability_name → ability
flow/constraint/planner 消费 GameState。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from game.geometry import Grid, Point2


class Owner(str, Enum):
    """单位归属。"""

    SELF = "self"  # 己方（alliance=1）
    ALLY = "ally"  # 盟友（alliance=2）
    ENEMY = "enemy"  # 敌方（alliance=3；注意：原始游戏数据将矿脉等中性资源也标 alliance=3，我们额外做了区分）
    NEUTRAL = "neutral"  # 中性（alliance=4 或 资源/装饰物）


@dataclass(slots=True)
class Order:
    """单位当前命令（SC2 原生命令精简镜像 + is_auto 标志）。"""

    ability: str  # 能力名（自带类型："SupplyDepot"=造补给站、"Marine"=训枪兵）
    target_tag: int | None = None  # 目标单位 tag（Attack/Gather/Follow/Load 的目标）
    target_pos: Point2 | None = None  # 目标坐标（Move/AttackMove/Build/Patrol 的目标点）
    is_auto: bool = False  # auto-order 标志（driver 按 auto-order 白名单设；用户接管识别用）


@dataclass(slots=True)
class Unit:
    """单个游戏单位。"""

    tag: int  # SC2 unit_tag（全局唯一；driver/Allocator 用它展开 group→unit）
    type_name: str  # 类型名（V1=burnysc2 UnitTypeId.name 如 "MARINE"；catalog 后映射稳定 ID 如 "terran/marine"）
    position: Point2  # 当前世界坐标（浮点）
    owner: Owner  # 归属（SELF/ALLY/ENEMY/NEUTRAL）
    hp: float  # 当前生命值
    hp_max: float  # 最大生命值
    shield: float  # 护盾值（Protoss 有；Terran=0）
    energy: float  # 能量值（如 Medivac heal 需能量；无能量单位=0）
    build_progress: float  # 建造/训练进度 0~1（1=完成；<1=在建/在训）
    orders: list[Order] = field(default_factory=list)  # 当前命令队列（第一项=正在执行）
    facing: float = 0.0  # 朝向角度（弧度）
    buffs: tuple[str, ...] = ()  # 当前 buff（如 stim）
    is_carrying_minerals: bool = False  # 是否正背着晶体矿（SCV 采完矿回交时 True）
    is_carrying_vespene: bool = False  # 是否正背着高能瓦斯


@dataclass(slots=True)
class GameState:
    """一帧游戏状态（world 适配后；flow/constraint/planner 消费的就是这个）。"""

    seq: int  # 单调步计数（driver 每 step +1；新鲜度/去重锚点）
    game_time: float  # 绝对游戏时间（秒；game_time >= T 常用谓词）
    minerals: int  # 当前晶体矿数量
    vespene: int  # 当前高能瓦斯数量
    supply_used: int  # 已用供给（人口）
    supply_cap: int  # 供给上限
    units: list[Unit]  # 可见单位（己/盟/可见敌方；中性矿脉/气井已由 world 过滤掉）
    map_size: tuple[int, int]  # 地图建筑格点尺寸 (width, height)
    creep: Grid  # 菌毯层（Zerg 相关；Terran 全 0）
    visibility: Grid  # 可见性层（0=Hidden/1=Fogged/2=Visible）
    # map_layers（power/addon_attachment 供能层）D11 后由 mechanics.LayerComputer 补
