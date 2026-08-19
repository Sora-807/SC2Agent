"""生产运行时：队首 constraint 门控 + drain + 放置解析 + 队列工具操作（P0 生产模块安排）。"""
from game import GameState, Grid, Owner, Point2, QueueItem, QueueOp, Unit, WorkerTask
from game.catalog import load_terran
from game.production import PlacementExact, PlacementInRegion
from production.runtime import ProductionRuntime
from tactical_map import load_region_layer

CAT = load_terran()

LAYER_YAML = """
map_name: p
size: [8, 8]
big_palette: {1: main_base}
big_grid:
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
  - "1 1 1 1 1 1 1 1"
leaf_palette: {1: home}
leaf_grid:
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 1 0 0 0"
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 0 0 0 0"
  - "0 0 0 0 0 0 0 0"
big_regions:
  main_base: {anchor: [4, 4]}
regions:
  home: {parent: main_base, anchor: [4, 4], build_slots: [s1, s2, b1]}
pos_marks:
  spot: {pos: [1.5, 1.5]}
build_slots:
  s1: {tl: [2, 2], size: 2}
  s2: {tl: [5, 2], size: 2}
  b1: {tl: [2, 5], size: 3}
"""


def _layer():
    return load_region_layer(LAYER_YAML)


class _Port:
    def __init__(self):
        self.submitted = []

    def submit_operations(self, ops):
        self.submitted.extend(ops)


def _u(tag, type_name, owner=Owner.SELF, x=0.0, y=0.0, progress=1.0):
    return Unit(tag=tag, type_name=type_name, position=Point2(x, y), owner=owner,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=progress)


def _gs(units=(), resources=(), minerals=200, vespene=0, supply_used=8, supply_cap=15):
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=minerals, vespene=vespene,
                     supply_used=supply_used, supply_cap=supply_cap, units=list(units),
                     map_size=(176, 160), creep=g, visibility=g, resources=list(resources))


def _runtime(port, queue_name="open"):
    rt = ProductionRuntime(CAT, port, region_layer=_layer())
    return rt


def test_build_head_blocked_by_minerals_then_emits():
    """队首门控：矿不够 → 阻塞不发；矿够 → 发 build（类型 = stable ID，放置 = pos_mark）。

    发出后进入在途确认（不出队）；实体出现 → 出队。
    """
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementExact("spot"))])
    gs_poor = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=50)
    rt.on_game_state(gs_poor)
    assert port.submitted == []  # 阻塞
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=200))
    assert len(port.submitted) == 1
    op = port.submitted[0]
    assert op.action == "build"
    assert op.unit_tags == [2]  # 派空闲 SCV
    assert op.params["type"] == "terran/supplydepot"  # stable ID 直达 driver（catalog 解析）
    assert op.params["position"] == [1.5, 1.5]  # pos_mark spot
    assert len(rt.queue("open").items) == 1  # 在途确认中，未出队
    # 实体出现 → 确认建造开始 → 出队
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"), _u(3, "SUPPLYDEPOT", x=1.5, y=1.5, progress=0.1)], minerals=100))
    assert rt.queue("open").items == []


def test_build_placement_failure_retries_next_slot():
    """真机教训：SC2 对非法放置位静默丢弃命令（无实体、build order 消失）→ 换候选位重发。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home"))])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert port.submitted[0].params["position"] == [3.0, 3.0]  # s1
    # 之后 6 帧：SCV 无 build order、无实体 → 第 5 帧判失败、第 6 帧重发 s2
    for _ in range(6):
        rt.on_game_state(gs)  # 命令已消失（FakeUnit 无 orders）
    assert len(port.submitted) == 2
    assert port.submitted[1].params["position"] == [6.0, 3.0]  # s2
    assert len(rt.queue("open").items) == 1  # 仍在途


def test_build_dropped_when_candidates_exhausted():
    """唯一候选（PlacementExact）放置失败 → 出队记入 dropped，不卡死整队。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1  # 发出 spot
    for _ in range(6):
        rt.on_game_state(gs)  # 放置失败（第 5 帧判定，第 6 帧重试时耗尽）
    assert any("耗尽" in r for _, r in rt.dropped)
    assert len(port.submitted) == 2 and port.submitted[1].action == "train"  # 后续项继续


def test_train_count_multiple_frames():
    """train count=3 → 逐帧各发一条（训练排队），发完出队。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/scv", count=3)])
    gs = _gs([_u(1, "COMMANDCENTER")], minerals=200)
    for _ in range(3):
        rt.on_game_state(gs)
    assert len(port.submitted) == 3
    assert all(op.action == "train" and op.unit_tags == [1] for op in port.submitted)
    assert all(op.params["type"] == "terran/scv" for op in port.submitted)
    assert rt.queue("open").items == []


def test_train_blocked_by_supply_then_resumes():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/scv")])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER")], minerals=200, supply_used=15, supply_cap=15))
    assert port.submitted == []  # 供给满 → 阻塞
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER")], minerals=200, supply_used=14, supply_cap=15))
    assert len(port.submitted) == 1


def test_head_blocking_holds_back_later_items():
    """队首阻塞 → 后面的项不越队（P0：队首按 constraint 门控）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=50))
    assert port.submitted == []  # build 阻塞 → train 也被按住
    assert len(rt.queue("open").items) == 2


def test_assign_workers_immediate_and_expanded():
    """assign_workers 无门控立即消费，展开成 gather 级 op（矿脉分摊）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="assign_workers", task="mineral", count=2)])
    minerals = [_u(10, "MINERALFIELD", owner=Owner.NEUTRAL), _u(11, "MINERALFIELD", owner=Owner.NEUTRAL)]
    rt.on_game_state(_gs([_u(1, "SCV"), _u(2, "SCV")], resources=minerals))
    assert len(port.submitted) == 2
    assert all(op.action == "gather" for op in port.submitted)
    assert {op.params["target_unit"] for op in port.submitted} == {10, 11}
    assert rt.queue("open").items == []  # 当帧消费


def test_research_dropped_with_reason():
    """V1 不支持 research（缺 upgrade 目录）→ 出队并记入 dropped（R7 降级，不阻塞）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="research", type="terran/stimpack")])
    rt.on_game_state(_gs([]))
    assert port.submitted == []
    assert rt.queue("open").items == []
    assert len(rt.dropped) == 1 and "upgrade" in rt.dropped[0][1]


def test_build_missing_placement_dropped_not_blocking():
    """ADR-0027 反例：缺 placement 的 build 丢弃并继续，不卡死整队。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot"),
        QueueItem(op="train", type="terran/scv"),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER")], minerals=200))
    assert any("placement" in r for _, r in rt.dropped)
    assert len(port.submitted) == 1 and port.submitted[0].action == "train"  # 后续项继续


def test_build_region_slot_auto_pick_and_blocked_when_full():
    """PlacementInRegion(index=None)：按声明顺序找第一个未被占的 slot；全占 → 阻塞。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home"))])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert port.submitted[0].params["position"] == [3.0, 3.0]  # s1（tl 2,2 size 2 → 中心 3,3）
    # s1 被占（新补给站出现在 3,3）→ 下一次 build 用 s2
    port2 = _Port()
    rt2 = _runtime(port2)
    rt2.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                        placement=PlacementInRegion("home"))])
    rt2.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"), _u(3, "SUPPLYDEPOT", x=3.0, y=3.0)], minerals=400))
    assert port2.submitted[0].params["position"] == [6.0, 3.0]  # s2（tl 5,2 → 中心 6,3）
    # 两 slot 全占 → 阻塞
    port3 = _Port()
    rt3 = _runtime(port3)
    rt3.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                        placement=PlacementInRegion("home"))])
    rt3.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                           _u(3, "SUPPLYDEPOT", x=3.0, y=3.0), _u(4, "SUPPLYDEPOT", x=6.0, y=3.0)], minerals=400))
    assert port3.submitted == []


def test_build_slot_filtered_by_footprint_size():
    """兵营(3×3)不落补给站(2×2)位：slot 按 catalog size 过滤（真机踩过）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/barracks",
                                       placement=PlacementInRegion("home"))])
    # 前置 = 就绪补给站（放在 6,6，不占 b1）；s1/s2 空闲但 size 2 不匹配 → 选 b1(3×3)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"), _u(3, "SUPPLYDEPOT", x=6.0, y=6.0)], minerals=400))
    assert port.submitted[0].params["position"] == [3.5, 6.5]  # b1（tl 2,5 size 3 → 中心 3.5,6.5）


def test_placement_unknown_mark_dropped():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementExact("nope"))])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400))
    assert any("未登记" in r for _, r in rt.dropped)


def test_queue_tool_operations():
    """agent 工具操作：submit/append/prepend/clear/remove/reorder。"""
    port = _Port()
    rt = _runtime(port)
    a = QueueItem(op="train", type="terran/scv")
    b = QueueItem(op="train", type="terran/scv")
    c = QueueItem(op="train", type="terran/scv")
    rt.submit_queue("q", [a])
    rt.append("q", [b])
    assert rt.queue("q").items == [a, b]
    rt.prepend("q", [c])
    assert rt.queue("q").items == [c, a, b]
    rt.remove("q", a)
    assert rt.queue("q").items == [c, b]
    rt.reorder("q", [b, c])
    assert rt.queue("q").items == [b, c]
    rt.clear("q")
    assert rt.queue("q").items == []
