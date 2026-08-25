"""world adapter：neutral 过滤 + owner 映射 + 字段对齐 + V1 no-op 透传。"""
import pytest

from game import GameState, Grid, Owner, Point2, RawGameState, RawOrder, RawUnit
from game.catalog import _NEUTRAL_KIND_PATTERNS, neutral_kind
from world.adapter import _EXTRA_NEUTRAL_TYPES, adapt, is_neutral_resource


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


# ---- I25：neutral_kind 模式表全覆盖（2026-08-25 审计补缺）----
# 现状：catalog._NEUTRAL_KIND_PATTERNS 有 12 条关键词模式，旧测试
# test_is_neutral_resource_patterns_cover_unlisted_rock_subtypes 只覆盖约 5 条，
# 其余零覆盖（2026-08-25 审计发现的类缺口）。这里逐条参数化 + 集合完整性守卫焊死。

#: 每条 _NEUTRAL_KIND_PATTERNS 关键词 → 一个能命中它（且不命中更早关键词）的代表性 type_name。
#: 名字自造（SC2 风格大写），只要能命中对应关键词模式；选词避开更早模式的关键词，
#: 确保 neutral_kind 的 first-match-wins 下命中的就是本条（而非更早的 destructible 等）。
_NEUTRAL_KIND_REPS: dict[str, str] = {
    "mineral": "MINERALFIELD",
    "geyser": "VESPENEGEYSER",
    "xelnagatower": "XELNAGATOWER",
    "destructible": "DESTRUCTIBLEROCK6X6",
    "debris": "DEBRIS6X6",
    "ramp": "RAMP6X6",
    "collapsible": "COLLAPSIBLE6X6",
    "blocker": "BLOCKER6X6",
    "barrier": "BARRIERROCKTOWER",
    "unbuildable": "UNBUILDABLEBRUSH",
    "sandbag": "SANDBAG6X6",
    "rockcover": "ROCKCOVER6X6",
}


def test_neutral_kind_representatives_cover_every_pattern_keyword():
    """完整性守卫：代表性名字典的键集合 == _NEUTRAL_KIND_PATTERNS 的关键词集合。

    将来给 _NEUTRAL_KIND_PATTERNS 加新模式关键词而不补代表名 → 这里立刻红
    （防类缺口再长回来，2026-08-25 审计同款根因）。
    """
    pattern_keywords = {kw for kw, _kind in _NEUTRAL_KIND_PATTERNS}
    assert set(_NEUTRAL_KIND_REPS) == pattern_keywords


@pytest.mark.parametrize("keyword", list(_NEUTRAL_KIND_REPS),
                         ids=list(_NEUTRAL_KIND_REPS))
def test_neutral_kind_returns_expected_kind_for_each_pattern(keyword):
    """I25：对 _NEUTRAL_KIND_PATTERNS 每一条模式，代表性 type_name 命中后返回该模式声明的类别。"""
    expected_kind = dict(_NEUTRAL_KIND_PATTERNS)[keyword]
    assert neutral_kind(_NEUTRAL_KIND_REPS[keyword]) == expected_kind


def test_neutral_kind_negative_and_forcefield_escape_hatch():
    """反例 + 逃逸舱：两层分类——模式表（单一事实源 catalog.neutral_kind）+
    world.adapter._EXTRA_NEUTRAL_TYPES（模式判不了的非资源中立物，如法术效果 FORCEFIELD）。"""
    # 不含任何模式关键词 → 既非模式中性也非资源中性
    assert neutral_kind("WEIRDOBJECT42") is None
    assert is_neutral_resource("WEIRDOBJECT42") is False
    # FORCEFIELD 命不中任何模式关键词（neutral_kind 返 None），
    # 但被 _EXTRA_NEUTRAL_TYPES 逃逸舱判为中性（is_neutral_resource 第二层）
    assert neutral_kind("FORCEFIELD") is None
    assert "FORCEFIELD" in _EXTRA_NEUTRAL_TYPES
    assert is_neutral_resource("FORCEFIELD") is True
