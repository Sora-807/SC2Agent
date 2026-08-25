"""tactical_map.base：主基建造模板（多出生点变体 + 平移实例化 + 与生产运行时联动）。"""
from game import GameState, Grid, GridPos, Owner, Point2, QueueItem, Unit
from game.catalog import load_all
from tests.factories import make_gs, make_unit
from game.production import PlacementInRegion
from production.runtime import ProductionRuntime
from tactical_map.base import (
    instantiate_spawn,
    load_base_template,
    spawn_layout_nearest,
)

TEMPLATE_YAML = """
map_name: TestMap
region_name: main_base
spawns:
  bl:
    origin: [10.5, 10.5]
    anchor: [10.5, 10.5]
    build_slots:
      depot1: {pos: [8.5, 12.5], size: 2, kind: supply}
      depot2: {pos: [10.5, 12.5], size: 2, kind: supply}
      barracks1: {pos: [8.5, 8.5], size: 3, kind: production}
  tr:
    origin: [90.5, 90.5]
    anchor: [90.5, 90.5]
    build_slots:
      depot1: {pos: [92.5, 88.5], size: 2, kind: supply}
"""


def _template(tmp_path):
    p = tmp_path / "base_layout.yaml"
    p.write_text(TEMPLATE_YAML, encoding="utf-8")
    return load_base_template(p)


def test_load_and_nearest_spawn(tmp_path):
    t = _template(tmp_path)
    assert t.map_name == "TestMap" and t.region_name == "main_base"
    assert spawn_layout_nearest(t, Point2(9, 9)).origin == Point2(10.5, 10.5)  # 选 bl
    assert spawn_layout_nearest(t, Point2(89, 91)).origin == Point2(90.5, 90.5)  # 选 tr


def test_instantiate_translates_slots_and_anchor(tmp_path):
    t = _template(tmp_path)
    layout = spawn_layout_nearest(t, Point2(9.5, 10.0))
    layer = instantiate_spawn(t, layout, Point2(9.5, 10.0), map_size=(176, 160))
    # 平移量 = (9.5-10.5, 10.0-10.5) = (-1.0, -0.5)
    d1 = layer.build_slots["depot1"]
    assert d1.pos == Point2(7.5, 12.0)  # 校准世界点平移
    assert d1.tl == GridPos(7, 12)  # tl = floor(pos)（V1 footprint 近似）
    assert layer.build_slots["barracks1"].pos == Point2(7.5, 8.0)
    assert layer.anchor("main_base") == Point2(9.5, 10.0)
    assert layer.regions["home"].build_slots == ("depot1", "depot2", "barracks1")  # 声明顺序


def _u(tag, tn, x=0.0, y=0.0):
    return make_unit(tag, tn, Owner.SELF, x, y, hp=400.0, hp_max=400.0)


def _gs(units, minerals=400):
    return make_gs(units, seq=0, game_time=0.0, minerals=minerals, vespene=0,
                   supply_used=8, supply_cap=15)


class _Port:
    def __init__(self):
        self.submitted = []

    def submit_operations(self, ops):
        self.submitted.extend(ops)


def test_fixed_order_placement_via_production_runtime(tmp_path):
    """固定位置顺序摆放：PlacementInRegion('home') 取声明顺序第一个未占位 → depot1 固定点。"""
    t = _template(tmp_path)
    layout = spawn_layout_nearest(t, Point2(10.5, 10.5))
    layer = instantiate_spawn(t, layout, Point2(10.5, 10.5), map_size=(176, 160))
    port = _Port()
    rt = ProductionRuntime(load_all(), port, region_layer=layer)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                    placement=PlacementInRegion("home"))])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")]))
    assert port.submitted[0].params["position"] == [8.5, 12.5]  # depot1 固定位
    # depot1 被占后 → depot2
    port2 = _Port()
    rt2 = ProductionRuntime(load_all(), port2, region_layer=layer)
    rt2.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                     placement=PlacementInRegion("home"))])
    rt2.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                           _u(3, "SUPPLYDEPOT", x=8.5, y=12.5)]))
    assert port2.submitted[0].params["position"] == [10.5, 12.5]  # depot2
