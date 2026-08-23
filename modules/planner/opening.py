"""planner.opening：离线规划的标准开局态（规划仿真的种子）。

规划文件是 authoring 数据：仿真它**不需要任何会话** —— 种子就是
「一个基地 + 12 SCV」的标准开局（与 worldsim.bootstrap / 夹具同口径）。
放在 planner（而不是 tools/worldsim）是因为 api/view 都要调它，
而架构红线禁止下层依赖 tools；hp/坐标这类 planner 不读的字段给占位值。
"""
from __future__ import annotations

from game import GameState, Order, Owner, Unit
from game.catalog import Catalog
from game.geometry import Grid, Point2

from planner.economy import DEFAULT_ECON

#: 标准开局常量（SC2 人族：50 矿起步、12 农民）。指挥中心供给不再单独写死 ——
#: 与 worldsim/planner 同用 planner.economy 的单一真相源（本机 dump+录像校准 = 13；
#: 此前这里写 15，与 economy 的 13 自相矛盾）。
START_MINERALS = 50
START_WORKERS = 12
CC_SUPPLY = DEFAULT_ECON.supply_provided["terran/commandcenter"]


def opening_game_state(catalog: Catalog, *, minerals: int = START_MINERALS,
                       workers: int = START_WORKERS) -> GameState:
    """标准开局 GameState：CC + N SCV（挂采矿）+ 8 矿脉，矿 minerals、气 0。

    `derive_from` 按 orders.target_tag 把工人分类成矿工 —— 所以 SCV 必须带
    HARVEST_GATHER 指向矿脉 tag，否则开局 12 工全算 idle，收入为 0。
    """
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
    cc = _unit("terran/commandcenter")
    scvs: list[Unit] = []
    for i in range(workers):
        scv = _unit("terran/scv")
        scv.orders = [Order(ability="HARVEST_GATHER",
                            target_tag=patches[i % len(patches)].tag, is_auto=True)]
        scvs.append(scv)
    zeros = Grid(4, 4, [[0] * 4 for _ in range(4)])
    return GameState(
        seq=0, game_time=0.0,
        minerals=int(minerals), vespene=0,
        supply_used=workers, supply_cap=CC_SUPPLY,
        units=[cc, *scvs], map_size=(4, 4), creep=zeros,
        visibility=zeros, resources=patches,
    )
