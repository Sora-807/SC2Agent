"""constraint 可行性校验：build/train 的门控项逐一断言（资源/供给/前置/放置）。"""
from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_terran
from constraint.checks import check_assign_workers, check_build, check_resources, check_train

CAT = load_terran()


def _u(tag, type_name, owner=Owner.SELF, x=0.0, y=0.0, progress=1.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=progress)


def _gs(units=(), minerals=200, vespene=0, supply_used=8, supply_cap=15):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=minerals, vespene=vespene,
                     supply_used=supply_used, supply_cap=supply_cap, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g)


def test_check_build_ok():
    gs = _gs([_u(1, "COMMANDCENTER")])  # 前置 = CC（depot 的 prerequisites）
    assert check_build(gs, CAT, "terran/supplydepot", Point2(50, 50)).ok


def test_check_build_blocked_by_minerals():
    r = check_build(_gs([_u(1, "COMMANDCENTER")], minerals=50), CAT, "terran/supplydepot", Point2(50, 50))
    assert not r.ok and any("晶体矿" in x for x in r.reasons)


def test_check_build_blocked_by_prereq():
    r = check_build(_gs([]), CAT, "terran/supplydepot", Point2(50, 50))
    assert not r.ok and any("前置" in x for x in r.reasons)


def test_check_build_blocked_by_occupied_cell():
    r = check_build(_gs([_u(1, "COMMANDCENTER"), _u(2, "SUPPLYDEPOT", x=50.0, y=50.0)]),
                    CAT, "terran/supplydepot", Point2(50, 50))
    assert not r.ok and any("占据" in x for x in r.reasons)


def test_check_build_unknown_type():
    r = check_build(_gs([]), CAT, "terran/ghost", Point2(50, 50))
    assert not r.ok and any("未知类型" in x for x in r.reasons)


def test_check_train_ok():
    gs = _gs([_u(1, "COMMANDCENTER")], minerals=50)
    assert check_train(gs, CAT, "terran/scv").ok


def test_check_train_blocked_by_supply():
    r = check_train(_gs([_u(1, "COMMANDCENTER")], minerals=50, supply_used=15, supply_cap=15),
                    CAT, "terran/scv")
    assert not r.ok and any("供给" in x for x in r.reasons)


def test_check_train_blocked_by_missing_producer():
    r = check_train(_gs([]), CAT, "terran/marine")  # 无兵营
    assert not r.ok and any("产出建筑" in x for x in r.reasons)


def test_check_train_unknown_type():
    r = check_train(_gs([]), CAT, "terran/ghost")
    assert not r.ok and any("未知类型" in x for x in r.reasons)


def test_check_resources_vespene():
    from game import Cost
    reasons = check_resources(_gs(minerals=200, vespene=50), Cost(minerals=0, vespene=100, supply=0))
    assert any("瓦斯" in x for x in reasons)


def test_check_assign_workers_always_ok():
    assert check_assign_workers().ok  # P0：无资源门控，立即发
