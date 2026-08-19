"""world adapter：neutral 过滤 + owner 映射 + 字段对齐 + V1 no-op 透传。"""
from game import Grid, Owner, Point2, RawGameState, RawOrder, RawUnit
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
