"""planner.opening：离线规划的标准开局态（规划仿真的种子）。

规划文件是 authoring 数据：仿真它**不需要任何会话** —— 种子就是
「一个基地 + 12 农民」的标准开局（与 worldsim.bootstrap / 夹具同口径）。
放在 planner（而不是 tools/worldsim）是因为 api/view 都要调它，
而架构红线禁止下层依赖 tools；hp/坐标这类 planner 不读的字段给占位值。

三族（N1c / REFACTOR B6）：种子按 race 参数化（Zerg 开局多 1 只 overlord）；
供给值取 catalog.supply_map() 单源（CC/Nexus=13、Hatchery=4、Overlord=8），
不在本地写第二份。
"""
from __future__ import annotations

from game import GameState, Order, Owner, Unit
from game.catalog import Catalog
from game.geometry import Grid, Point2

#: 标准开局常量（三族通用：50 矿起步、12 农民）
START_MINERALS = 50
START_WORKERS = 12

#: 三族开局种子（基地, 农民, 额外单位）。Zerg 开局自带 1 只 overlord
#: （hatchery 4 + overlord 8 = 12 供给，正好 12/12 与另两族的 12/13 对齐口径）。
_RACE_SEED: dict[str, tuple[str, str, str | None]] = {
    "terran": ("terran/commandcenter", "terran/scv", None),
    "protoss": ("protoss/nexus", "protoss/probe", None),
    "zerg": ("zerg/hatchery", "zerg/drone", "zerg/overlord"),
}


def base_supply(catalog: Catalog, race: str = "terran") -> int:
    """开局供给（catalog 单源；terran=13 —— 历史 CC_SUPPLY 常量的接替者）。"""
    base, _, extra = _RACE_SEED[race]
    supply = catalog.supply_map()
    return supply.get(base, 0) + (supply.get(extra, 0) if extra else 0)


def opening_game_state(catalog: Catalog, *, race: str = "terran",
                       minerals: int = START_MINERALS,
                       workers: int = START_WORKERS) -> GameState:
    """标准开局 GameState：基地 + N 农民（挂采矿）+ 8 矿脉，Zerg 另带 1 只 overlord。

    `derive_from` 按 orders.target_tag 把工人分类成矿工 —— 所以农民必须带
    HARVEST_GATHER 指向矿脉 tag，否则开局 12 工全算 idle，收入为 0。
    """
    base, worker, extra = _RACE_SEED[race]
    tag = 0

    def _next() -> int:
        nonlocal tag
        tag += 1
        return tag

    def _unit(stable_id: str) -> Unit:
        entry = catalog.by_stable_id(stable_id)
        assert entry is not None, f"catalog 没有 {stable_id}"
        return Unit(tag=_next(), type_name=entry.burnysc2_name, position=Point2(0, 0),
                    owner=Owner.SELF, hp=100.0, hp_max=100.0, shield=0.0, energy=0.0,
                    build_progress=1.0, orders=[])

    patches: list[Unit] = [
        Unit(tag=_next(), type_name="MINERALFIELD", position=Point2(i, 5), owner=Owner.NEUTRAL,
             hp=1.0, hp_max=1.0, shield=0.0, energy=0.0, build_progress=1.0, orders=[])
        for i in range(8)
    ]
    units: list[Unit] = [_unit(base)]
    for i in range(workers):
        w = _unit(worker)
        w.orders = [Order(ability="HARVEST_GATHER",
                          target_tag=patches[i % len(patches)].tag, is_auto=True)]
        units.append(w)
    if extra is not None:
        units.append(_unit(extra))
    zeros = Grid(4, 4, [[0] * 4 for _ in range(4)])
    return GameState(
        seq=0, game_time=0.0,
        minerals=int(minerals), vespene=0,
        supply_used=workers, supply_cap=base_supply(catalog, race),
        units=units, map_size=(4, 4), creep=zeros,
        visibility=zeros, resources=patches,
    )
