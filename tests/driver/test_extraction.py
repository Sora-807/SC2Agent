"""extraction 纯函数：用 duck-typed 假 burnysc2 对象验证 RawGameState/RawUnit/RawOrder 抽取。"""
from types import SimpleNamespace

from driver.sc2_adapter import extract_raw_order, extract_raw_state, extract_raw_unit
from game import Point2


def _fake_unit(tag=1, alliance=1, type_name="SCV", hp=45.0, orders=(), pos=(48.5, 25.5)):
    return SimpleNamespace(
        tag=tag,
        type_id=SimpleNamespace(name=type_name),
        position=SimpleNamespace(x=pos[0], y=pos[1]),
        alliance=alliance,
        health=hp,
        health_max=hp,
        shield=0.0,
        energy=0.0,
        build_progress=1.0,
        orders=orders,
        facing=0.81,
        buffs=(),
        is_carrying_minerals=False,
        is_carrying_vespene=False,
    )


def _fake_order(name="Gather", tpos=None, ttag=None):
    """**按 burnysc2 UnitOrder 真实形态**造订单（§0.53 事故教训）：`target` 字段是
    int=单位 tag | Point2=目标点 | None —— 协议的 target_unit_tag/target_world_space_pos
    已被 from_proto 归一。旧夹具按不存在的字段造（target_unit_tag），把驱动的字段
    名 bug 也一起"测绿"了（离线全绿、真机 target_tag 恒 None）。"""
    if tpos is not None:
        target = tpos
    elif ttag is not None:
        target = ttag
    else:
        target = None
    return SimpleNamespace(
        ability=SimpleNamespace(name=name),
        target=target,
    )


def _fake_pm(w=2, h=2, data=b"\x00\x01\x01\x00"):
    return SimpleNamespace(width=w, height=h, data=data)


def _fake_bot(units, time=0.0, minerals=50, pm=None):
    pm = pm or _fake_pm()
    return SimpleNamespace(
        all_units=units,
        state=SimpleNamespace(creep=pm, visibility=pm),
        game_info=SimpleNamespace(map_size=(2, 2)),
        time=time,
        minerals=minerals,
        vespene=0,
        supply_used=8,
        supply_cap=13,
    )


def test_extract_raw_unit_basic():
    u = _fake_unit(tag=42, alliance=1, type_name="SCV", hp=45.0)
    r = extract_raw_unit(u)
    assert r.tag == 42
    assert r.type_name == "SCV"
    assert r.position == Point2(48.5, 25.5)
    assert r.alliance == 1
    assert r.health == 45.0 and r.health_max == 45.0
    assert r.facing == 0.81
    assert r.orders == []


def test_extract_raw_order_with_target():
    # 真实形态：一个 target 字段（Move 带点、Gather 带 tag）—— 两者分别见
    # test_extract_raw_order_target_field_shapes
    o = _fake_order(name="Move", tpos=SimpleNamespace(x=10.0, y=20.0))
    r = extract_raw_order(o)
    assert r.ability_name == "Move"
    assert r.target_tag is None
    assert r.target_pos == Point2(10.0, 20.0)


def test_extract_raw_order_no_target():
    r = extract_raw_order(_fake_order(name="Gather"))
    assert r.ability_name == "Gather"
    assert r.target_tag is None
    assert r.target_pos is None


def test_extract_raw_order_button_name():
    # burnysc2 AbilityData 的干净名是 .button_name（非 .name）；取干净名，不存 repr
    o = SimpleNamespace(ability=SimpleNamespace(button_name="SupplyDepot"), target=None)
    r = extract_raw_order(o)
    assert r.ability_name == "SupplyDepot"


def test_extract_raw_order_target_field_shapes():
    """§0.53 根因①回归锁：burnysc2 UnitOrder 只有 `target`（int|Point2|None）——
    采矿单必须能翻出 target_tag，否则维持器看不见"已在采"（真机全场重派）。"""
    r = extract_raw_order(SimpleNamespace(
        ability=SimpleNamespace(name="Gather"), target=4295229441))
    assert r.target_tag == 4295229441 and r.target_pos is None
    r2 = extract_raw_order(SimpleNamespace(
        ability=SimpleNamespace(name="Move"), target=SimpleNamespace(x=30.0, y=44.0)))
    assert r2.target_tag is None and r2.target_pos == Point2(30.0, 44.0)
    r3 = extract_raw_order(SimpleNamespace(
        ability=SimpleNamespace(name="SupplyDepot"), target=None))
    assert r3.target_tag is None and r3.target_pos is None


def test_extract_raw_state_aggregates():
    units = [_fake_unit(tag=1, alliance=1), _fake_unit(tag=2, alliance=3, type_name="ZERGLING", hp=35.0, pos=(5, 5))]
    bot = _fake_bot(units, time=12.5, minerals=80)
    s = extract_raw_state(bot, seq=7)
    assert s.seq == 7
    assert s.game_time == 12.5
    assert s.minerals == 80
    assert s.supply_used == 8 and s.supply_cap == 13
    assert len(s.units) == 2
    assert s.units[0].alliance == 1 and s.units[0].type_name == "SCV"
    assert s.units[1].alliance == 3 and s.units[1].type_name == "ZERGLING"
    assert s.map_size == (2, 2)
    assert s.creep.width == 2 and s.creep.data[0][1] == 1
    assert s.visibility.data == [[0, 1], [1, 0]]
