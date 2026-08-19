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
  s1: {tl: [2, 2], size: 2, kind: supply}
  s2: {tl: [5, 2], size: 2, kind: supply}
  b1: {tl: [2, 5], size: 3, kind: production}
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
    gs_poor = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=50)
    rt.on_game_state(gs_poor)
    assert port.submitted == []  # 阻塞
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=200))
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
    assert port.submitted[0].params["position"] == [2.5, 2.5]  # s1
    # 之后 31 帧：SCV 无 build order、无实体 → 第 30 帧判失败、第 31 帧重发 s2
    for _ in range(31):
        rt.on_game_state(gs)  # 命令已消失（FakeUnit 无 orders）
    assert len(port.submitted) == 2
    assert port.submitted[1].params["position"] == [5.5, 2.5]  # s2
    assert len(rt.queue("open").items) == 1  # 仍在途


def test_build_confirm_matches_position_not_type_count():
    """真机教训（full_flow.log）：同类型建筑连续建时，晚到实体不能替别的项确认——
    按放置位置匹配（奇数尺寸 R=P、偶数尺寸 R=P+0.5 锁定公式）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                      placement=PlacementInRegion("home"))])
    gs0 = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs0)
    assert port.submitted[0].params["position"] == [2.5, 2.5]  # s1
    for _ in range(31):
        rt.on_game_state(gs0)  # 无实体无 build order → 第 30 帧判失败 → 重发 s2
    assert port.submitted[1].params["position"] == [5.5, 2.5]  # s2（预期报告位 (6.0,3.0)）
    # s1 位置出现 depot 实体（类型相同、位置不匹配）→ 不能确认
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=2.5, y=2.5, progress=0.1)], minerals=400))
    assert len(rt.queue("q").items) == 1  # 仍在途
    # s2 位置实体出现 → 位置匹配 → 确认出队
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(10, "SUPPLYDEPOT", x=6.0, y=3.0, progress=0.1)], minerals=400))
    assert rt.queue("q").items == []


def test_build_dropped_when_candidates_exhausted():
    """唯一候选（PlacementExact）放置失败 → 出队记入 dropped，不卡死整队。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1  # 发出 spot
    for _ in range(32):
        rt.on_game_state(gs)  # 放置失败（第 30 帧判定，重试时候选耗尽）
    assert any("耗尽" in r for _, r in rt.dropped)
    assert len(port.submitted) == 2 and port.submitted[1].action == "train"  # 后续项继续


def test_same_frame_no_duplicate_unit_commands():
    """同帧跨队列不对同一单位重复发令（burnysc2 同帧同单位命令去重丢单——真机踩坑：
    建造工兵被 steward 的 gather 抢走导致 build 静默丢失）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q1", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementExact("spot"))])
    rt.submit_queue("q2", [QueueItem(op="assign_workers", task="mineral", count=1)])
    resources = [_u(10, "MINERALFIELD", owner=Owner.NEUTRAL, x=12.0, y=12.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")],
                         resources=resources, minerals=400))
    build_ops = [o for o in port.submitted if o.action == "build"]
    gather_ops = [o for o in port.submitted if o.action == "gather"]
    assert len(build_ops) == 1
    assert not gather_ops  # 唯一空闲 SCV 已被 build 占用 → 分配器跳过（不重复命令）


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


def test_train_skips_full_producer():
    """真机教训（full_flow.log）：SC2 训练队列满静默拒单——只选有空槽的兵营（挂件双槽 ≈ 2 条订单为满）。"""
    from game import Order
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine")])
    full = Unit(tag=3, type_name="BARRACKS", position=Point2(8.0, 8.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0,
                orders=[Order(ability="Marine"), Order(ability="Marine")])
    free = Unit(tag=4, type_name="BARRACKS", position=Point2(20.0, 20.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), full, free], minerals=200))
    assert len(port.submitted) == 1
    assert port.submitted[0].unit_tags == [4]  # 跳过满槽兵营(3)


def test_train_skips_barracks_building_addon():
    """兵营在建挂件（Reactor 订单）→ 不能训练 → 选另一台空闲兵营。"""
    from game import Order
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine")])
    busy = Unit(tag=3, type_name="BARRACKS", position=Point2(8.0, 8.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0,
                orders=[Order(ability="Reactor")])
    free = Unit(tag=4, type_name="BARRACKS", position=Point2(20.0, 20.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), busy, free], minerals=200))
    assert len(port.submitted) == 1
    assert port.submitted[0].unit_tags == [4]  # 跳过在建挂件的兵营(3)


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
    assert port.submitted[0].params["position"] == [2.5, 2.5]  # s1（tl 2,2 size 2 → 格角偏移 0.5）
    # s1 被占（新补给站出现在 3,3）→ 下一次 build 用 s2
    port2 = _Port()
    rt2 = _runtime(port2)
    rt2.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                        placement=PlacementInRegion("home"))])
    rt2.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"), _u(3, "SUPPLYDEPOT", x=3.0, y=3.0)], minerals=400))
    assert port2.submitted[0].params["position"] == [5.5, 2.5]  # s2（tl 5,2 size 2）
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

# ---- 挂件（addon）与气矿（gas）建造路径 ----


def test_addon_built_by_parent_not_scv():
    """挂件：builder = 母建筑（兵营），非 SCV；SC2 吸附到右下 2×2。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "BARRACKS", x=8.0, y=8.0)],
             minerals=200, vespene=200)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1
    op = port.submitted[0]
    assert op.action == "build"
    assert op.unit_tags == [3]  # 兵营自建，不是 SCV(2)
    assert op.params["type"] == "terran/reactor"
    assert op.params["position"] is None  # 挂件无目标能力：SC2 吸附母建筑右下 2×2（真机教训）
    assert len(rt.queue("q").items) == 1  # 在途确认
    # BARRACKSREACTOR 实体出现 → 确认 → 出队
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
                          _u(3, "BARRACKS", x=8.0, y=8.0), _u(4, "BARRACKSREACTOR", x=10.0, y=7.0, progress=0.1)],
                         minerals=150, vespene=150))
    assert rt.queue("q").items == []


def test_addon_blocked_when_parent_has_addon():
    """兵营右下 2×2 已有建筑（挂件）→ 该兵营不可再挂；无其他母建筑 → 阻塞。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    # 兵营 at (8,8) 3×3 → TL(6,6)；右下挂件 cells (9..10, 6..7)；BARRACKSREACTOR 报告 (10.0,7.0) → TL(9,6) ✓ 完全占据（裸实验锁定位置）
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
              _u(3, "BARRACKS", x=8.0, y=8.0), _u(4, "BARRACKSREACTOR", x=10.0, y=7.0)],
             minerals=200, vespene=200)
    rt.on_game_state(gs)
    assert port.submitted == []  # 阻塞等待


def test_addon_second_goes_to_other_barracks():
    """两台兵营：第一台已挂 → 第二台自建。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
              _u(3, "BARRACKS", x=8.0, y=8.0), _u(4, "BARRACKSREACTOR", x=10.0, y=7.0),
              _u(5, "BARRACKS", x=20.0, y=20.0)],
             minerals=200, vespene=200)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1
    assert port.submitted[0].unit_tags == [5]  # 第二台兵营


def test_addon_skips_barracks_with_orders():
    """兵营有训练订单（忙碌）→ 不选它挂件（真机教训 full_flow.log：忙碌兵营的挂件命令
    会被拒绝或与同帧训练命令冲突，订单静默消失）。"""
    from game import Order
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    busy = Unit(tag=3, type_name="BARRACKS", position=Point2(8.0, 8.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0,
                orders=[Order(ability="Marine")])
    free = Unit(tag=5, type_name="BARRACKS", position=Point2(20.0, 20.0), owner=Owner.SELF,
                hp=400.0, hp_max=400.0, shield=0.0, energy=0.0, build_progress=1.0)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), busy, free],
                         minerals=200, vespene=200))
    assert len(port.submitted) == 1
    assert port.submitted[0].unit_tags == [5]  # 跳过忙碌兵营(3)


def test_addon_waits_when_parent_not_built_yet():
    """母建筑还没建（macro 队列在建）→ 挂件项阻塞等待，不误判为被摧毁而丢弃
    （真机教训：开局即判死 → 反应堆×4 全丢，后续枪兵全被按住）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")],
                         minerals=200, vespene=200))
    assert rt.dropped == []
    assert port.submitted == []  # 前置未满足 → 等兵营
    assert len(rt.queue("q").items) == 1


def test_addon_retry_wait_does_not_burn_retries():
    """重试等待（资源/母建筑不足）不计重试次数——只在实际重发时计（真机踩坑：
    等待帧也烧次数会在 3 帧内误丢弃挂件）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/reactor")])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "BARRACKS", x=8.0, y=8.0)],
             minerals=200, vespene=200)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1  # 发出挂件
    for _ in range(30):
        rt.on_game_state(gs)  # 无订单无实体 → 第 30 帧判失败 → 转重试
    assert rt._build_flight["q"]["builder"] is None
    poor = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "BARRACKS", x=8.0, y=8.0)],
               minerals=200, vespene=0)
    for _ in range(60):
        rt.on_game_state(poor)  # 缺气等待 60 帧：不重发、不计次数、不丢弃
    assert rt._build_flight.get("q") is not None
    assert rt._build_flight["q"].get("retries", 0) == 0
    rt.on_game_state(gs)  # 气够了 → 实际重发一次（retries=1）
    assert len(port.submitted) == 2
    assert rt._build_flight["q"]["retries"] == 1


def test_gas_build_targets_free_geyser():
    """气矿：SCV 把精炼厂建在空闲气井上（build_gas，target = 气井 Unit）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/refinery")])
    geysers = [_u(10, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=15.0, y=15.0),
               _u(11, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=25.0, y=25.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")],
                         resources=geysers, minerals=200))
    assert len(port.submitted) == 1
    op = port.submitted[0]
    assert op.action == "build_gas"
    assert op.unit_tags == [2]  # SCV 建造
    assert op.params["type"] == "terran/refinery"
    assert op.params["target_unit"] == 10  # 第一个空闲气井


def test_gas_skips_occupied_geyser():
    """气井旁已有精炼厂 → 选另一个空闲气井。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/refinery")])
    geysers = [_u(10, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=15.0, y=15.0),
               _u(11, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=10.0, y=10.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
                          _u(3, "REFINERY", x=15.0, y=15.0)],
                         resources=geysers, minerals=200))
    assert port.submitted[0].params["target_unit"] == 11  # 10 号气井被占 → 选 11（两井都在主基半径内）


def test_gas_skips_inflight_reserved_geyser():
    """在途精炼厂预留的气井（命令已发、实体未出现）不能被第二项重选（SC2 静默拒绝第二个）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/refinery")])
    geysers = [_u(10, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=12.0, y=12.0),
               _u(11, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=18.0, y=10.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")],
                         resources=geysers, minerals=200))
    assert port.submitted[0].params["target_unit"] == 10
    rt.submit_queue("q2", [QueueItem(op="build", type="terran/refinery")])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "SCV")],
                         resources=geysers, minerals=200))
    build_ops = [o for o in port.submitted if o.action == "build_gas"]
    assert len(build_ops) == 2
    assert build_ops[-1].params["target_unit"] == 11  # 避开在途预留的 10 号气井