"""共享测试工厂：Unit / GameState / FakePort 的唯一构造点。

2026-08-25 测试审计发现：11 份本地 `_u`/`_unit` 签名互不兼容（第 3 位一会是 x
一会是 owner）、`Unit(...)` 全 kwarg 样板内联 50+ 处、`_Port` 假类 6 份。此后新测试
一律用这里的工厂；存量文件以薄包装迁移（本地默认值是语义，保留在包装里）。

约定：
- make_unit 位置参数序 = (tag, type_name, owner, x, y)——多数存量工厂的公共子序；
  其余全部 keyword-only，杜绝对位次的新猜测。
- hp 不传时按 type_name 查基线表（建筑厚、兵薄），再兜底 45。测试逻辑不应对
  精确 hp 敏感；个别敏感的测试显式传。
- make_gs 的 (units, resources) 位置序对应存量最常见形态；各文件的游戏时间派生、
  supply 口径等语义差异留在本地包装里，不在工厂里做魔法。
"""
from __future__ import annotations

from game import GameState, Grid, Order, Owner, Point2, Unit

# 常见单位 (hp, hp_max) 基线：只为"类型合理"，不是精确平衡数据
_HP_BASELINES: dict[str, tuple[float, float]] = {
    "MARINE": (45.0, 45.0),
    "MARAUDER": (125.0, 125.0),
    "SCV": (45.0, 45.0),
    "SIEGETANK": (160.0, 160.0),
    "MEDIVAC": (150.0, 150.0),
    "SUPPLYDEPOT": (400.0, 400.0),
    "BARRACKS": (1000.0, 1000.0),
    "FACTORY": (1250.0, 1250.0),
    "REFINERY": (400.0, 400.0),
    "EXTRACTOR": (400.0, 400.0),
    "ASSIMILATOR": (400.0, 400.0),
    "ENGINEERINGBAY": (850.0, 850.0),
    "COMMANDCENTER": (1500.0, 1500.0),
    "ORBITALCOMMAND": (1500.0, 1500.0),
}


def make_unit(
    tag: int,
    type_name: str = "MARINE",
    owner: Owner = Owner.SELF,
    x: float = 0.0,
    y: float = 0.0,
    *,
    hp: float | None = None,
    hp_max: float | None = None,
    shield: float = 0.0,
    energy: float = 0.0,
    progress: float = 1.0,
    orders: list[Order] | tuple[Order, ...] = (),
    carrying_minerals: bool = False,
    carrying_vespene: bool = False,
) -> Unit:
    base_hp, base_hp_max = _HP_BASELINES.get(type_name, (45.0, 45.0))
    return Unit(
        tag=tag,
        type_name=type_name,
        position=Point2(float(x), float(y)),
        owner=owner,
        hp=base_hp if hp is None else hp,
        hp_max=base_hp_max if hp_max is None else hp_max,
        shield=shield,
        energy=energy,
        build_progress=progress,
        orders=list(orders),
        is_carrying_minerals=carrying_minerals,
        is_carrying_vespene=carrying_vespene,
    )


def make_gs(
    units=(),
    resources=(),
    *,
    seq: int = 0,
    game_time: float = 0.0,
    minerals: int = 200,
    vespene: int = 0,
    supply_used: int = 8,
    supply_cap: int = 15,
    map_size: tuple[int, int] = (176, 160),
) -> GameState:
    g = Grid(1, 1, [[0]])
    return GameState(
        seq=seq,
        game_time=game_time,
        minerals=minerals,
        vespene=vespene,
        supply_used=supply_used,
        supply_cap=supply_cap,
        units=list(units),
        map_size=map_size,
        creep=g,
        visibility=g,
        resources=list(resources),
    )


class FakePort:
    """submit_operations 收集器——生产运行时 / 经济维持器的假端口。

    gathers() 是 economy 断言糖：[(unit_tag, target_tag)]，只看 gather op。
    """

    def __init__(self):
        self.submitted = []

    def submit_operations(self, ops):
        self.submitted.extend(ops)

    def gathers(self):
        return [(o.unit_tags[0], o.params["target_unit"]) for o in self.submitted if o.action == "gather"]
