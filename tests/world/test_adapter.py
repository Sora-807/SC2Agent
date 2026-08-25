"""world adapter：neutral 过滤 + owner 映射 + 字段对齐 + V1 no-op 透传。"""
from game import GameState, Grid, Owner, Point2, RawGameState, RawOrder, RawUnit
from world.adapter import adapt, is_neutral_resource


def _raw_unit(tag, alliance, type_name="SCV", hp=45.0, pos=(0.0, 0.0)):
    return RawUnit(
        tag=tag, type_name=type_name, position=Point2(*pos), alliance=alliance,
        health=hp, health_max=hp, shield=0.0, energy=0.0, build_progress=1.0,
        orders=[RawOrder(ability_name="Gather")],
    )


def _raw(units, seq=0):
    g = Grid(1, 1, [[0]])
    return RawGameState(
        seq=seq, game_time=0.0, minerals=50, vespene=0, supply_used=8, supply_cap=13,
        units=units, map_size=(2, 2), creep=g, visibility=g,
    )


def test_neutral_resource_filtered():
    units = [_raw_unit(1, 1, "SCV"), _raw_unit(2, 3, "MINERALFIELD"), _raw_unit(3, 3, "ZERGLING")]
    gs = adapt(_raw(units))
    tags = {u.tag for u in gs.units}
    assert tags == {1, 3}  # mineral (tag 2) filtered out
    assert all(u.type_name != "MINERALFIELD" for u in gs.units)


def test_resources_extracted():
    """中性资源节点拆到 GameState.resources（WorkerAllocator/生产约束用），不进 units。"""
    units = [_raw_unit(1, 1, "SCV"), _raw_unit(2, 3, "MINERALFIELD", pos=(5.0, 5.0)),
             _raw_unit(3, 3, "VESPENEGEYSER", pos=(6.0, 6.0)), _raw_unit(4, 3, "ZERGLING")]
    gs = adapt(_raw(units))
    assert {u.tag for u in gs.units} == {1, 4}
    res = {u.tag: u for u in gs.resources}
    assert set(res) == {2, 3}
    assert res[2].type_name == "MINERALFIELD" and res[2].position == Point2(5.0, 5.0)
    assert res[3].type_name == "VESPENEGEYSER"
    # 默认值：老构造方式不传 resources 也不炸
    assert GameState(seq=0, game_time=0.0, minerals=0, vespene=0, supply_used=0, supply_cap=0,
                     units=[], map_size=(0, 0), creep=Grid(1, 1, [[0]]), visibility=Grid(1, 1, [[0]])).resources == []


def test_owner_mapping():
    units = [_raw_unit(1, 1, "SCV"), _raw_unit(2, 2, "MARINE"), _raw_unit(3, 3, "ZERGLING")]
    gs = adapt(_raw(units))
    by_tag = {u.tag: u for u in gs.units}
    assert by_tag[1].owner is Owner.SELF
    assert by_tag[2].owner is Owner.ALLY
    assert by_tag[3].owner is Owner.ENEMY


def test_field_alignment():
    u = _raw_unit(1, 1, "SCV", hp=40.0, pos=(5.0, 6.0))
    gs = adapt(_raw([u]))
    gu = gs.units[0]
    assert gu.hp == 40.0 and gu.hp_max == 40.0  # health → hp
    assert gu.position == Point2(5.0, 6.0)  # pass-through
    assert gu.orders[0].ability == "Gather"  # ability_name → ability
    assert gu.owner is Owner.SELF


def test_passthrough_noop():
    g = Grid(2, 2, [[0, 1], [1, 0]])
    raw = RawGameState(
        seq=7, game_time=12.5, minerals=80, vespene=10, supply_used=10, supply_cap=15,
        units=[_raw_unit(1, 1, "SCV")], map_size=(2, 2), creep=g, visibility=g,
    )
    gs = adapt(raw)
    assert gs.seq == 7 and gs.game_time == 12.5 and gs.minerals == 80
    assert gs.map_size == (2, 2)
    assert gs.creep is g and gs.visibility is g  # V1 no-op：同对象透传
    assert gs.creep.data == [[0, 1], [1, 0]]


def test_is_neutral_resource():
    assert is_neutral_resource("MINERALFIELD")
    assert is_neutral_resource("VESPENEGEYSER")
    assert not is_neutral_resource("SCV")
    assert not is_neutral_resource("ZERGLING")


def test_is_neutral_resource_patterns_cover_unlisted_rock_subtypes():
    """I25：按名称模式判（game.catalog.neutral_kind 单一事实源），不再枚举白名单——
    各图自带的岩石/残骸/斜坡子类型几十种，白名单外的岩石曾被当 Owner.ENEMY
    （假敌方警报根因）。"""
    assert is_neutral_resource("DESTRUCTIBLEROCK6X6")
    assert is_neutral_resource("DESTRUCTIBLEROCKTALL4X4")       # 白名单从未列过
    assert is_neutral_resource("DESTRUCTIBLEDEBRIS6X6")
    assert is_neutral_resource("COLLAPSIBLEROCKTALLDIAGONAL")   # 斜坡崩塌塔
    assert is_neutral_resource("RICHMINERALFIELD")              # 富矿变体
    assert is_neutral_resource("FORCEFIELD")                    # 法术效果（显式补）
    # 己方/敌方单位不许误伤
    for t in ("MARINE", "SIEGETANKSIEGED", "BANELING", "MOTHERSHIP", "SUPPLYDEPOT"):
        assert not is_neutral_resource(t), t


def test_unlisted_rocks_routed_to_resources_not_enemy():
    """I25 根治验收：白名单外的岩石 alliance=3 也进 resources，不再以 ENEMY 进 units。"""
    units = [_raw_unit(1, 1, "SCV"),
             _raw_unit(2, 3, "DESTRUCTIBLEROCKTALL4X4", pos=(57.0, 62.0)),
             _raw_unit(3, 3, "ZERGLING")]
    gs = adapt(_raw(units))
    assert {u.tag for u in gs.units} == {1, 3}          # 岩石不在 units
    assert {u.tag for u in gs.resources} == {2}         # 岩石归资源/中立物
    assert gs.units[0].owner is Owner.SELF
    assert gs.units[1].owner is Owner.ENEMY             # 真敌兵照常
