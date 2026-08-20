"""flow Allocator V1：FCFS + sticky lease 语义（spec-006 接口预留的简实现）。"""
from game import GameState, Grid, Owner, Point2, Unit
from flow.allocator import Allocator


def _u(tag, type_name="MARINE", owner=Owner.SELF, x=0.0, y=0.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(units):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=0,
                     supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g)


def test_fill_to_target_fcfs():
    alloc = Allocator()
    alloc.create_group("G1", {"MARINE": {"min": 1, "target": 2, "max": 4}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.count("G1") == 2  # 只补到 target，第三个留 free 池
    assert alloc.expand("G1", "MARINE") == [1, 2]  # FCFS：按 gs.units 顺序取前 2


def test_sticky_lease_not_stolen():
    alloc = Allocator()
    alloc.create_group("G1", {"MARINE": {"target": 1}})
    alloc.create_group("G2", {"MARINE": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "MARINE") == [1]  # 先注册先拿（FCFS）
    assert alloc.expand("G2", "MARINE") == [2, 3]  # 后注册只从 free 拿，不抢 G1
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "MARINE") == [1]  # sticky：重复 refresh 不重分配


def test_death_pruning_and_refill():
    alloc = Allocator()
    alloc.create_group("G1", {"MARINE": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2)]))
    assert alloc.count("G1") == 2
    alloc.refresh(_gs([_u(2)]))  # 1 死亡 → lease 清除
    assert alloc.expand("G1", "MARINE") == [2]
    alloc.refresh(_gs([_u(2), _u(3)]))  # 新兵进 free 池 → 补到 target
    assert alloc.expand("G1", "MARINE") == [2, 3]


def test_only_self_units_leased():
    alloc = Allocator()
    alloc.create_group("G1", {"MARINE": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2, owner=Owner.ENEMY)]))
    assert alloc.expand("G1", "MARINE") == [1]  # 敌方单位不租


def test_count_and_expand_unknown_group():
    alloc = Allocator()
    assert alloc.count("nope") == 0
    assert alloc.expand("nope", "MARINE") == []
    assert alloc.expand_all("nope") == []


def test_empty_group():
    alloc = Allocator()
    alloc.create_group("G2", {"MARINE": {"target": 1}})
    assert alloc.count("G2") == 0
    assert alloc.expand("G2", "MARINE") == []  # 空 group：动作展开为空 → no-op
    assert alloc.expand_all("G2") == []


def test_expand_all_across_types():
    alloc = Allocator()
    alloc.create_group("G1", {"MARINE": {"target": 1}, "SCV": {"target": 1}})
    alloc.refresh(_gs([_u(1, "MARINE"), _u(2, "SCV")]))
    assert alloc.expand_all("G1") == [1, 2]
    assert alloc.count("G1") == 2
    assert alloc.count("G1", "MARINE") == 1
    assert alloc.count("G1", "SCV") == 1


def test_sieged_tank_still_leased_and_counted():
    """形态变体归一（T3）：坦克架起后 type_name 变 SIEGETANKSIEGED，传 catalog 时
    仍被 lease/计数为 SIEGETANK 组成员；不传 catalog 时漏 lease（回归对照）。"""
    from game.catalog import load_terran
    cat = load_terran()
    # 4 坦克：2 未架起 + 2 已架起（type_name=SIEGETANKSIEGED）
    units = [_u(1, "SIEGETANK"), _u(2, "SIEGETANK"),
             _u(3, "SIEGETANKSIEGED"), _u(4, "SIEGETANKSIEGED")]
    alloc = Allocator(catalog=cat)
    alloc.create_group("G1", {"SIEGETANK": {"target": 4}})
    alloc.refresh(_gs(units))
    assert alloc.count("G1", "SIEGETANK") == 4              # 4 辆全 lease（架起态归一到主名）
    assert sorted(alloc.expand("G1", "SIEGETANK")) == [1, 2, 3, 4]
    assert sorted(alloc.expand("G1", "SIEGETANKSIEGED")) == [1, 2, 3, 4]  # 变体名查询也归一
    # 回归对照：不传 catalog → 架起态漏 lease（只 lease 2 辆未架起的）
    alloc2 = Allocator()
    alloc2.create_group("G1", {"SIEGETANK": {"target": 4}})
    alloc2.refresh(_gs(units))
    assert alloc2.count("G1", "SIEGETANK") == 2

