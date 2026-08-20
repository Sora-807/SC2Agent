"""flow Allocator V1：FCFS + sticky lease 语义（spec-006 接口预留的简实现）。"""
from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_terran
from flow.allocator import Allocator

CAT = load_terran()


def _u(tag, type_name="MARINE", owner=Owner.SELF, x=0.0, y=0.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)


def _gs(units):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=0,
                     supply_cap=20, units=units, map_size=(176, 160), creep=g, visibility=g)


def test_fill_to_target_fcfs():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 1, "target": 2, "max": 4}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.count("G1") == 2  # 只补到 target，第三个留 free 池
    assert alloc.expand("G1", "terran/marine") == [1, 2]  # FCFS：按 gs.units 顺序取前 2


def test_sticky_lease_not_stolen():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 1}})
    alloc.create_group("G2", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # 先注册先拿（FCFS）
    assert alloc.expand("G2", "terran/marine") == [2, 3]  # 后注册只从 free 拿，不抢 G1
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # sticky：重复 refresh 不重分配


def test_death_pruning_and_refill():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2)]))
    assert alloc.count("G1") == 2
    alloc.refresh(_gs([_u(2)]))  # 1 死亡 → lease 清除
    assert alloc.expand("G1", "terran/marine") == [2]
    alloc.refresh(_gs([_u(2), _u(3)]))  # 新兵进 free 池 → 补到 target
    assert alloc.expand("G1", "terran/marine") == [2, 3]


def test_only_self_units_leased():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2, owner=Owner.ENEMY)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # 敌方单位不租


def test_count_and_expand_unknown_group():
    alloc = Allocator(CAT)
    assert alloc.count("nope") == 0
    assert alloc.expand("nope", "terran/marine") == []
    assert alloc.expand_all("nope") == []


def test_empty_group():
    alloc = Allocator(CAT)
    alloc.create_group("G2", {"terran/marine": {"target": 1}})
    assert alloc.count("G2") == 0
    assert alloc.expand("G2", "terran/marine") == []  # 空 group：动作展开为空 → no-op
    assert alloc.expand_all("G2") == []


def test_expand_all_across_types():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 1}, "terran/scv": {"target": 1}})
    alloc.refresh(_gs([_u(1, "MARINE"), _u(2, "SCV")]))
    assert alloc.expand_all("G1") == [1, 2]
    assert alloc.count("G1") == 2
    assert alloc.count("G1", "terran/marine") == 1
    assert alloc.count("G1", "terran/scv") == 1


def test_sieged_tank_still_leased_and_counted():
    """形态变体归一（T3）：坦克架起后实体名变 SIEGETANKSIEGED，仍被 lease/计数为
    terran/siegetank 组成员（单侧归一：stable id → 主名，单位名归一到主名后比较）。"""
    units = [_u(1, "SIEGETANK"), _u(2, "SIEGETANK"),
             _u(3, "SIEGETANKSIEGED"), _u(4, "SIEGETANKSIEGED")]
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/siegetank": {"target": 4}})
    alloc.refresh(_gs(units))
    assert alloc.count("G1", "terran/siegetank") == 4              # 4 辆全 lease（架起态归一到主名）
    assert sorted(alloc.expand("G1", "terran/siegetank")) == [1, 2, 3, 4]


def test_burnysc2_name_is_not_authoring_vocabulary():
    """T1/D1：authoring 侧只认 stable id —— 变体名/burnysc2 名查询不再被归一（词汇只剩一套）。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/siegetank": {"target": 2}})
    alloc.refresh(_gs([_u(1, "SIEGETANK"), _u(2, "SIEGETANKSIEGED")]))
    assert alloc.count("G1", "terran/siegetank") == 2
    assert alloc.expand("G1", "SIEGETANK") == []          # 主名也不是 authoring 词汇
    assert alloc.expand("G1", "SIEGETANKSIEGED") == []    # 变体名同理
    assert alloc.count("G1", "SIEGETANK") == 0


def test_catalog_required():
    """catalog 必传（D1）：composition 是 stable id，没 catalog 无法匹配 gs 实体名 → 构造期报错。"""
    import pytest
    with pytest.raises(ValueError, match="catalog"):
        Allocator(None)


def test_unknown_stable_id_in_composition_rejected():
    """未登记的 stable id（或误写 burnysc2 名）在 create_group 就报错，不静默漏 lease。"""
    import pytest
    alloc = Allocator(CAT)
    with pytest.raises(ValueError, match="stable id"):
        alloc.create_group("G1", {"MARINE": {"target": 1}})

