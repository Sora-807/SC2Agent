"""生产运行时：队首 constraint 门控 + drain + 放置解析 + 队列工具操作（P0 生产模块安排）。"""
import pytest

from game import GameState, Grid, Owner, Point2, QueueItem, QueueOp, Unit, WorkerTask
from game.catalog import load_all
from tests.factories import FakePort, make_gs, make_unit
from game.production import PlacementExact, PlacementInRegion
from production.runtime import STALL_WARN_SECS, ProductionRuntime
from tactical_map import load_region_layer

CAT = load_all()

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


_Port = FakePort


def _u(tag, type_name, owner=Owner.SELF, x=0.0, y=0.0, progress=1.0):
    return make_unit(tag, type_name, owner=owner, x=x, y=y, progress=progress,
                     hp=400.0, hp_max=400.0)


def _gs(units=(), resources=(), minerals=200, vespene=0, supply_used=8, supply_cap=15,
        game_time=0.0):
    return make_gs(units, resources, game_time=game_time, minerals=minerals,
                   vespene=vespene, supply_used=supply_used, supply_cap=supply_cap)


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
    assert len(rt._build_flights.get("open", [])) == 1  # 在途确认中（flight 在列表里）
    # 实体出现（0.1 进度）→ flight 锁定实体等完工；项留账本标 in_progress（ADR-0032）
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"), _u(3, "SUPPLYDEPOT", x=1.5, y=1.5, progress=0.1)], minerals=100))
    assert rt.queue("open").items[0].status == "in_progress"


def test_build_placement_failure_retries_next_slot():
    """真机教训：SC2 对非法放置位静默丢弃命令（无实体、build order 消失）→ 换候选位重发。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home"))])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert port.submitted[0].params["position"] == [2.5, 2.5]  # s1
    # 之后 91 帧：SCV 无 build order、无实体 → 第 90 帧判失败、第 91 帧重发 s2
    for _ in range(91):
        rt.on_game_state(gs)  # 命令已消失（FakeUnit 无 orders）
    assert len(port.submitted) == 2
    assert port.submitted[1].params["position"] == [5.5, 2.5]  # s2
    assert len(rt._build_flights.get("open", [])) == 1  # 仍在途（flight 在列表里）


def test_build_confirm_claims_late_entity_at_attempted_slot():
    """I26：本 flight 发过的位置上晚到的同型实体要**收编**——命令其实落了，只是
    90 帧判失败时实体还没出现。旧语义只认**当前**预期位，把这种实体当"别人的"，
    换槽重发 → 真机 2 座的计划造出 9 座补给站（每次重发 = 又一座真建筑 + 再扣矿）。
    槽位有在途预留，别的 flight 不可能用本 flight 发过的槽位——收编安全。
    （full_flow「按位置匹配」教训的精确化：不认的是**没发过命令的位置**上的实体。）"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                      placement=PlacementInRegion("home"))])
    gs0 = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs0)
    assert port.submitted[0].params["position"] == [2.5, 2.5]  # s1
    for _ in range(91):
        rt.on_game_state(gs0)  # 无实体无 build order → 第 90 帧判失败 → 重发 s2
    assert port.submitted[1].params["position"] == [5.5, 2.5]  # s2（预期报告位 (6.0,3.0)）
    # s1 实体晚到（重试已发生）→ 发过的位置 → 收编锁定，不再等 s2 的实体
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.1)], minerals=400))
    assert rt._build_flights["q"][0]["entity_tag"] == 9
    assert rt.queue("q").items[0].status == "in_progress"
    # 实体完工 → started → 项完成；全程没有第三次发射
    for _ in range(3):
        rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                              _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=1.0)], minerals=400))
    assert len(port.submitted) == 2
    assert rt.queue("q").items[0].status == "completed"


def test_build_confirm_ignores_entity_at_never_emitted_position():
    """full_flow 教训仍然成立（I26 收编判据第 3 条）：从没发过命令的位置上的同型
    晚到实体不认领——那是别的 flight/玩家的事，认了就是替别的项假确认。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                      placement=PlacementInRegion("home"))])
    gs0 = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs0)  # emit s1
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=50.0, y=50.0, progress=0.1)], minerals=400))
    assert rt._build_flights["q"][0].get("entity_tag") is None  # 没被假确认
    # 无实体（无关位置的不算）+ 无 build order → 90 帧照常判失败重发（行为不变）
    for _ in range(90):
        rt.on_game_state(gs0)
    assert len(port.submitted) == 2  # 重试 s2 正常发生


def test_retry_claims_late_entity_without_reemitting():
    """I26 重试侧：假失败后的重试帧，发过的位置上实体已出现 → 收编，不重发
    （不再吃下一个槽位、不再扣一次矿）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home"))])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)               # emit s1
    for _ in range(90):
        rt.on_game_state(gs)           # 第 90 帧判失败（builder=None）
    assert len(port.submitted) == 1
    # 第 91 帧：s1 实体出现（命令其实落了）→ 重试路径先收编，不重发 s2
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.5)], minerals=400))
    assert len(port.submitted) == 1    # 没有第二次发射
    assert rt._build_flights["open"][0]["entity_tag"] == 9


# ---- I26 类泛化（2026-08-25 审计批3c）：收编是「任意 build 项 × 任意发射位」的规则，
# 不是补给站@(3,3) 专属。期望实体位从实际提交的 op 读（ADR-0027：实体中心 = 发射位
# +0.5），换建筑类型 / 换出现进度（含 progress=0.0 的地基）各验一遍。 ----
@pytest.mark.parametrize("stable_id,sc2_name,progress", [
    pytest.param("terran/supplydepot", "SUPPLYDEPOT", 0.0, id="depot-progress0"),
    pytest.param("terran/barracks", "BARRACKS", 0.0, id="barracks-progress0"),
    pytest.param("terran/engineeringbay", "ENGINEERINGBAY", 0.4, id="engbay-progress04"),
])
def test_late_entity_claim_is_type_agnostic(stable_id, sc2_name, progress):
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type=stable_id,
                                       placement=PlacementInRegion("home"))])
    # 前置从 catalog 推（换类型不用改测试）：已满足的不摆；缺的补到远角，别压槽位
    base = [_u(1, "COMMANDCENTER"), _u(2, "SCV")]
    have = {"COMMANDCENTER"}
    n = 0
    for pre in CAT.by_stable_id(stable_id).prerequisites or ():
        name = CAT.burnysc2_name_for(pre)
        if name is None or name in have:
            continue
        base.append(_u(20 + n, name, x=7.0, y=7.0 + 2 * n))
        have.add(name)
        n += 1
    gs0 = _gs(base, minerals=2000)
    rt.on_game_state(gs0)
    ex, ey = port.submitted[0].params["position"]
    late = _u(9, sc2_name, x=ex + 0.5, y=ey + 0.5, progress=progress)
    rt.on_game_state(_gs(base + [late], minerals=2000))
    assert rt._build_flights["open"][0]["entity_tag"] == 9, "晚到同型实体要收编（不分建筑类型）"
    assert rt.queue("open").items[0].status == "in_progress"
    for _ in range(3):
        done = _u(9, sc2_name, x=ex + 0.5, y=ey + 0.5, progress=1.0)
        rt.on_game_state(_gs(base + [done], minerals=2000))
    assert len(port.submitted) == 1, "收编后不再换槽重发"
    assert rt.queue("open").items[0].status == "completed"


def test_build_dropped_when_candidates_exhausted():
    """唯一候选（PlacementExact）放置失败重试耗尽 → 留账本标 skipped(placement_collision)，
    不卡死整队、不进 dropped（ADR-0032：执行期失败 vs 作者错误的分工）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert len(port.submitted) == 2  # build + train（贪心并行：都发出了）
    for _ in range(92):
        rt.on_game_state(gs)  # 放置失败（第 90 帧判定，重试时候选耗尽）
    assert rt.dropped == []
    item = rt.queue("open").items[0]
    assert item.status == "skipped" and item.reason == "placement_collision"
    assert len(port.submitted) == 2 and port.submitted[1].action == "train"  # 后续项继续


def _roundtrip(item: QueueItem, **over) -> QueueItem:
    """账本往返项（agent 重提队列时从帧反解回灌）：带 uid/已扣 count/已执行状态。"""
    kw = dict(op=item.op, type=item.type, count=item.count, placement=item.placement,
              task=item.task, uid=item.uid, status=item.status, reason=item.reason,
              started_at=item.started_at, completed_at=item.completed_at)
    kw.update(over)
    return QueueItem(**kw)


def test_repropose_roundtrip_does_not_fake_complete_building_item():
    """I26 残留（真机 rec-20260825-093336）：重提队列（submit_queue）取消在途
    flight，往返项带 in_progress 回队 → 旧 sweep 假设「flight 移除 = 实体完工」
    直接标 completed —— 实际补给站还在建，自此对 ExecView 三处全无。现在：
    场上无完工同型实体就不完成，等实体真盖完。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home"))])
    gs0 = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs0)  # emit s1 → q01 in_progress
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.3)], minerals=400))
    # agent 重提：整队往返（uid 保留）→ submit_queue 取消 flight，但建筑还在建
    old = rt.queue("open").items[0]
    rt.submit_queue("open", [_roundtrip(old, count=0, status="in_progress")])
    assert rt._build_flights.get("open") == []  # flight 已被取消（复现链的 gt=115）
    # 实体仍 0.5 → 不假完成（旧代码这里就变 completed 了），也不重发
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.5)], minerals=400))
    assert rt.queue("open").items[0].status == "in_progress"
    assert len(port.submitted) == 1  # 没有为已扣 count 的项再发一次 build
    # 实体盖完 → 这才 completed
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=1.0)], minerals=400))
    assert rt.queue("open").items[0].status == "completed"


def test_repropose_roundtrip_downstream_waits_not_skips():
    """同链下半段：depot 往返 in_progress 且实体在建 → 兵营走 pending（等前置，
    queued_types 认 in_progress），不再被假完成连锁成终态 skip；depot 盖完后
    兵营照常发射（真机 q04 兵营级联 skip 整局不出兵的回归锁）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
        QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=500))
    # depot 发射（inflight）；兵营 pending 等前置
    items = rt.queue("open").items
    assert items[0].status == "in_progress" and items[1].status == "pending"
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.3)], minerals=500))
    # 重提（depot 往返 in_progress + 兵营 pending）→ flight 取消
    rt.submit_queue("open", [_roundtrip(items[0], count=0),
                             _roundtrip(items[1])])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.5)], minerals=500))
    items = rt.queue("open").items
    assert items[0].status == "in_progress"
    assert items[1].status == "pending"  # 不是 skipped（旧代码假完成后这里级联 skip）
    assert "等前置" in rt.blocked["open"]["reason"]
    # depot 盖完 → 兵营当帧放行发射
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=1.0)], minerals=500))
    items = rt.queue("open").items
    assert items[0].status == "completed"
    assert items[1].status == "in_progress"
    assert any(op.action == "build" and op.params["type"] == "terran/barracks"
               for op in port.submitted)


def test_sweep_no_entity_keeps_in_progress_and_queue_alive():
    """flight 缺席且场上同型实体全无（命令真丢/建筑被拆）→ 也不假完成不假跳：
    保留 in_progress（诚实账本），后续项照常执行 —— 真死由 stall 警报 + agent
    重提收口，skip 是终态不拿猜测冒险。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementInRegion("home")),
                             QueueItem(op="train", type="terran/scv")])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    items = rt.queue("open").items
    # 重提：只往返 depot（in_progress/count=0）+ train —— flight 取消，实体从未出现
    rt.submit_queue("open", [_roundtrip(items[0], count=0), _roundtrip(items[1])])
    for _ in range(5):
        rt.on_game_state(gs)
    items = rt.queue("open").items
    assert items[0].status == "in_progress"   # 不假完成也不终态化
    assert items[1].status in ("in_progress", "completed")  # 队列不被冻住
    assert any(op.action == "train" for op in port.submitted)


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
    it = rt.queue("open").items[0]
    assert it.count == 0 and it.status == "in_progress"  # 发完在训（账本淘汰后转 completed）


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
    """队首门控（D7）：队首 build 缺矿阻塞 → 后续 train 本帧不执行（保 build order 顺序）。

    旧行为是贪心跳过（train 先出），会让后置项抢走队首要攒的矿、破坏 build order，
    且跨帧累计超支（模块 docstring 第 3 条一直写的是队首门控，代码此前与文档相反）。
    """
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=50))
    assert port.submitted == []  # build 阻塞(50<100) → train(50>=50) 也不越队
    assert len(rt.queue("open").items) == 2  # 两项都还在
    rec = rt.blocked["open"]  # H1：阻塞不静默，能看见卡在哪
    assert rec["item"] is rt.queue("open").items[0]
    assert "晶体矿不足" in rec["reason"]


def test_head_unblocks_then_later_items_run_same_frame():
    """队首可行后立刻恢复：同帧发 build + 其后 train（资源够则并行不被削弱），阻塞记录清除。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="train", type="terran/scv"),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=50))
    assert "open" in rt.blocked
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=300))
    assert [op.action for op in port.submitted] == ["build", "train"]  # 队首先发，后续项跟上
    assert "open" not in rt.blocked  # 通畅 → 记录清除


def test_persistent_block_raises_stall_warning_once():
    """H1：同一队首持续阻塞超 STALL_WARN_SECS → 记一条 stalls 告警（只报一次，不自动丢弃）。

    队首门控会冻结整条队列，永久阻塞（区域满/前置永不满足）必须能被看见 ——
    否则就是静默失速（违背需求文档 R7 与"不静默"红线）。
    """
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/barracks",
                                      placement=PlacementExact("spot"))])
    units = [_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")]
    for t in (0.0, 10.0, STALL_WARN_SECS, STALL_WARN_SECS + 10.0):
        rt.on_game_state(_gs(units, minerals=50, game_time=t))
    assert port.submitted == []
    assert len(rt.stalls) == 1  # 只报一次，不刷屏
    item, msg = rt.stalls[0]
    assert item is rt.queue("open").items[0]
    assert "队首阻塞" in msg and "晶体矿不足" in msg
    assert rt.dropped == []  # 不自动丢弃队首（那会破坏 build order）


def test_assign_workers_does_not_jump_blocked_build():
    """顺序语义（反例守卫）：assign_workers(gas) 排在 refinery 之后是有意的 ——

    refinery 还没建好就把 SCV 派去采气，WorkerAllocator 找不到气矿槽，意图会静默蒸发。
    所以 assign_workers 也不能越过阻塞的队首（真机脚本 run_tank_marine_push 的 macro 队列形状）。
    """
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("macro", [
        QueueItem(op="build", type="terran/refinery"),
        QueueItem(op="assign_workers", task="gas", count=3),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=25))  # 精炼厂 75
    assert port.submitted == []
    assert len(rt.queue("macro").items) == 2


def test_unknown_queue_op_dropped_not_silently_skipped():
    """D7：未知 op 出队并记 dropped（旧行为是 i+=1 静默跳过 → 永久占住队首）。

    QueueOp 在 QueueItem 构造期已校验，这里绕过构造直接改字段模拟脏数据（R7 兜底路径）。
    """
    port = _Port()
    rt = _runtime(port)
    bad = QueueItem(op="train", type="terran/scv")
    bad.op = "fly"  # 绕过 __post_init__ 的枚举归一
    rt.submit_queue("open", [bad, QueueItem(op="train", type="terran/scv")])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER")], minerals=200))
    assert len(rt.dropped) == 1 and "未知 QueueOp" in rt.dropped[0][1]
    assert len(port.submitted) == 1 and port.submitted[0].action == "train"  # 后续项继续
    assert rt.queue("open").items[0].status == "in_progress"  # 好项留账本在训（ADR-0032）


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
    assert rt.queue("open").items[0].status == "completed"  # 当帧消费，留账本（ADR-0032）


def test_research_dropped_with_reason():
    """V1 不支持 research（缺 upgrade 目录）→ 出队并记入 dropped（R7 降级，不阻塞）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="research", type="terran/stimpack")])
    rt.on_game_state(_gs([]))
    assert port.submitted == []
    assert rt.queue("open").items == []
    assert len(rt.dropped) == 1 and "upgrade" in rt.dropped[0][1]


def test_build_missing_placement_now_auto_places():
    """ADR-0027 修订（批 2）：缺 placement 的 build = 自动放置（默认图层按序空位），
    不再是作者错误丢弃；图层无位才是 skip(placement_collision)。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot"),
        QueueItem(op="train", type="terran/scv"),
    ])
    # CC 是 5×5：放 (7.5,7.5)（tl=5..9），别盖住 s1(2..3)/s2(5..6,y2..3) 补给槽
    gs = _gs([_u(1, "COMMANDCENTER", x=7.5, y=7.5), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert rt.dropped == [], "null placement 不再 dropped（自动放置）"
    assert any(o.action == "build" for o in port.submitted), "按 home 区槽位自动发出建造"
    assert rt.queue("open").items[0].status == "in_progress"


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
    assert len(rt._build_flights.get("q", [])) == 1  # 在途确认（flight 在列表里）
    # BARRACKSREACTOR 实体出现（0.1）→ flight 锁定等完工；项留账本 in_progress
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
                          _u(3, "BARRACKS", x=8.0, y=8.0), _u(4, "BARRACKSREACTOR", x=10.0, y=7.0, progress=0.1)],
                         minerals=150, vespene=150))
    assert rt.queue("q").items[0].status == "in_progress"


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
    for _ in range(90):
        rt.on_game_state(gs)  # 无订单无实体 → 第 90 帧判失败 → 转重试
    assert rt._build_flights["q"][0]["builder"] is None
    poor = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "BARRACKS", x=8.0, y=8.0)],
               minerals=200, vespene=0)
    for _ in range(60):
        rt.on_game_state(poor)  # 缺气等待 60 帧：不重发、不计次数、不丢弃
    assert rt._build_flights.get("q") is not None
    assert rt._build_flights["q"][0].get("retries", 0) == 0
    rt.on_game_state(gs)  # 气够了 → 实际重发一次（retries=1）
    assert len(port.submitted) == 2
    assert rt._build_flights["q"][0]["retries"] == 1


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


# ---- ADR-0030 第 3/4 步：建造工征用 + assign_workers 写目标 ----


def test_build_reserves_builder_until_confirmed():
    """发出 build 时征用建造工（谁都不许动），实体出现确认后释放回矿池（ADR-0030 D3.3 / issues P14）。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), reservations=res)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementExact("spot"))])
    units = [_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")]
    rt.on_game_state(_gs(units, minerals=200))
    assert len(port.submitted) == 1 and port.submitted[0].action == "build"
    assert res.tags() == frozenset({2}), "建造工必须在征用中"
    # 实体出现 → 确认完成 → 释放
    depot = _u(9, "SUPPLYDEPOT", x=2.0, y=2.0)
    for _ in range(2):
        rt.on_game_state(_gs(units + [depot], minerals=200))
    assert res.tags() == frozenset(), "确认完成后应释放建造工"


def test_build_does_not_steal_a_reserved_worker():
    """已被别的 flight 征用的 SCV 不会被再次派去建造（_pick_builder 排除征用中）。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    res.reserve("other/build#1", 2)
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), reservations=res)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementExact("spot"))])
    units = [_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"), _u(3, "SCV")]
    rt.on_game_state(_gs(units, minerals=200))
    assert port.submitted and port.submitted[0].unit_tags == [3]


def test_assign_workers_queue_item_writes_target_when_keeper_present():
    """ADR-0030 D2.2：有维持器时 assign_workers 队列项 = 写目标（幂等、意图不蒸发，修 P9）。"""
    from production.economy import EconomyKeeper

    keeper = EconomyKeeper(CAT, _Port())
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), economy=keeper)
    rt.submit_queue("q", [QueueItem(op="assign_workers", task="gas", count=3)])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")]))
    assert keeper.policy.gas_workers == 3, "队列项应把目标写进维持器"
    assert port.submitted == [], "写目标不该立刻发命令（扇出是维持器的事）"
    assert rt.queue("q").items[0].status == "completed", "写完即完成（幂等，不需要门控）"
    assert rt.dropped == [], "更不该被当成失败丢弃"



# ---- P2/P3/P4：帧账本 / 取消在途 / 气矿重试 ----


def test_frame_ledger_is_shared_across_queues():
    """P3：帧账本跨队列共享 —— 60 矿两条 50 矿 train（两台兵营）只应发一条。

    原来 pending 是 _drain 的局部变量且 TRAIN 完全不记账，第二条靠 SC2 静默拒单兜底，
    而队列项已经被消费掉了。
    """
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q1", [QueueItem(op="train", type="terran/marine")])
    rt.submit_queue("q2", [QueueItem(op="train", type="terran/marine")])
    rax = [_u(2, "BARRACKS", x=6.0, y=6.0), _u(3, "BARRACKS", x=10.0, y=10.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0)] + rax,
                         minerals=60, supply_used=5, supply_cap=20))
    trains = [o for o in port.submitted if o.action == "train"]
    assert len(trains) == 1, f"60 矿只够一个机枪，实际发了 {len(trains)} 条"
    assert any("晶体矿不足" in rec["reason"] for rec in rt.blocked.values())


def test_frame_ledger_counts_supply():
    """P3：同帧连训会超供给上限 —— 供给也要进帧账本。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="train", type="terran/marine", count=3)])
    rax = [_u(2, "BARRACKS", x=6.0, y=6.0)]
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0)] + rax,
                         minerals=500, supply_used=19, supply_cap=20))
    trains = [o for o in port.submitted if o.action == "train"]
    assert len(trains) == 1, "只剩 1 供给 → 只能训 1 个（机枪占 1）"


def test_clear_cancels_in_flight_build():
    """P2：clear 必须取消在途建造 —— 原来清了队列，flight 照常 retry 并重新发令（假取消）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementExact("spot"))])
    units = [_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")]
    rt.on_game_state(_gs(units, minerals=200))
    assert len(port.submitted) == 1
    assert rt._build_flights["q"], "应有在途 flight"
    rt.clear("q")
    assert not rt._build_flights["q"], "clear 应取消在途 flight"
    assert any("clear" in reason for _item, reason in rt.dropped), "取消要留审计，不静默"
    for _ in range(120):  # 跑够重试窗口：不应再发第二条 build
        rt.on_game_state(_gs(units, minerals=200))
    assert len([o for o in port.submitted if o.action == "build"]) == 1


def test_queue_tools_reject_bad_count():
    """P12：agent/LLM 写面最容易塞 count=0/负数 —— 直接拒绝，不静默改队列。"""
    rt = _runtime(_Port())
    import pytest

    with pytest.raises(ValueError, match="count"):
        rt.submit_queue("q", [QueueItem(op="train", type="terran/marine", count=0)])
    with pytest.raises(ValueError, match="count"):
        rt.append("q", [QueueItem(op="train", type="terran/marine", count=-2)])


def test_gas_retry_switches_geyser_instead_of_dropping():
    """P4：气矿第一次放置失败后应换井重试，而不是以"缺 placement"直接丢弃订单。"""
    port = _Port()
    rt = _runtime(port)
    geysers = [_u(20, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=3.0, y=3.0),
               _u(21, "VESPENEGEYSER", owner=Owner.NEUTRAL, x=5.0, y=5.0)]
    rt.submit_queue("q", [QueueItem(op="build", type="terran/refinery")])
    # CC 要离气井够远：_pick_free_geyser 把 2.5 格内有己方建筑的气井算作已占
    units = [_u(1, "COMMANDCENTER", x=12.0, y=12.0), _u(2, "SCV")]
    rt.on_game_state(_gs(units, resources=geysers, minerals=200, vespene=0))
    first = [o for o in port.submitted if o.action == "build_gas"]
    assert len(first) == 1
    tried_first = first[0].params["target_unit"]
    # 命令静默消失（实体不出现、builder 无订单）→ 91 帧后判失败 → 应换另一口井重试
    for _ in range(95):
        rt.on_game_state(_gs(units, resources=geysers, minerals=200, vespene=0))
    gas_ops = [o for o in port.submitted if o.action == "build_gas"]
    assert len(gas_ops) >= 2, f"应换井重试，实际只有 {len(gas_ops)} 条"
    assert gas_ops[-1].params["target_unit"] != tried_first, "重试必须换一口没试过的气井"
    assert not any("缺 placement" in reason for _i, reason in rt.dropped)


# ---- 2026-08-24 用户拍板：建造默认抽**最近的采矿 SCV**，完工自动归队 ----


def test_pick_builder_prefers_nearest_to_site():
    """采矿中的 SCV 是被抽对象；离建造点最近的优先（少走路 = 少误工）。"""
    from game import Order, Point2, Unit

    def scv(tag, x, y):
        return Unit(tag=tag, type_name="SCV", position=Point2(x, y), owner=Owner.SELF,
                    hp=45.0, hp_max=45.0, shield=0.0, energy=0.0, build_progress=1.0,
                    orders=[Order(ability="Gather", target_tag=900)])   # 都在采矿

    port = _Port() if "Port" in dir() else None
    rt = _runtime(_SilentPort())
    gs = _gs(units=[scv(1, 0.0, 0.0), scv(2, 50.0, 50.0)])
    assert rt._pick_builder(gs, near=Point2(48.0, 48.0)).tag == 2
    assert rt._pick_builder(gs, near=Point2(1.0, 1.0)).tag == 1


class _SilentPort:
    def submit_operations(self, ops):
        return None


# ---- §0.52 D 批：无 builder 诊断 + 丢弃必释放征用 ----

def test_flight_drop_releases_builder_reservation():
    """flight 候选耗尽被丢弃 → 征用必须释放：否则候选被旧 flight 占死，
    「无可用 SCV」阻塞永不解除（截图事故的候选根因之二，回归锁）。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), reservations=res)
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementExact("spot"))])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=200)
    rt.on_game_state(gs)
    assert res.tags() == frozenset({2}), "发出 build 即征用"
    for _ in range(92):
        rt.on_game_state(gs)   # 放置失败 → 重试 → 候选耗尽 skip（ADR-0032：不再 dropped）
    item = rt.queue("q").items[0]
    assert item.status == "skipped" and item.reason == "placement_collision"
    assert res.tags() == frozenset(), "skip 后征用必须释放（工兵回候选池）"
    # 释放后下一项 build 能立刻抽到同一个 SCV（候选没被占死）
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementExact("spot"))])
    rt.on_game_state(gs)
    assert any(o.action == "build" and 2 in o.unit_tags for o in port.submitted[1:]), \
        "丢弃释放后同一 SCV 可再次被抽中"


def test_no_builder_reason_carries_split_counts():
    """诊断三元组进阻塞文案（总数/建造征用/本帧已令）——告警里看得出谁占着。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    res.reserve("other/build#9", 2)   # 一个别的 flight 占着 2 号
    rt = ProductionRuntime(CAT, _Port(), region_layer=_layer(), reservations=res)
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0),
              _u(2, "SCV"), _u(3, "SCV"), _u(4, "SCV")], minerals=200)
    rt._frame_busy = {3, 4}           # 本帧已令另外两个
    rt._pick_builder(gs)              # 候选耗尽（3 工兵全被排除）→ 记诊断
    reason = rt._no_builder_reason()
    assert "场上工兵 3" in reason and "建造征用 1" in reason and "本帧已令 2" in reason


# ---- §0.53 根因②：实体出现 ≠ 完工，征用握到 build_progress>=1 ----

def _emit_one_build(rt, port):
    rt.submit_queue("q", [QueueItem(op="build", type="terran/supplydepot",
                                   placement=PlacementInRegion("home"))])
    gs = _gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    assert port.submitted and port.submitted[0].action == "build"
    return gs


def test_flight_holds_builder_until_building_completes():
    """0% 实体出现时征用**不释放**（SC2 放置即出实体，SCV 还要盖 ~21s）——提前放人
    会被维持器立刻派回采矿、gather 顶掉建造单（真机两次 0% 补给站被弃的根因②）。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), reservations=res)
    gs = _emit_one_build(rt, port)
    assert res.tags() == frozenset({2})
    # 放置位出现 0% 实体（emit 位置 [2.5,2.5] → 偶数尺寸报告位 (3.0,3.0)）
    building = _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.0)
    for _ in range(3):
        rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                              building], minerals=400))
        assert res.tags() == frozenset({2}), "建筑没盖完，征用不许释放"
        assert len(rt._build_flights["q"]) == 1, "flight 等完工，不出列表"
    # 盖到 100% → started → 释放 + 出列表
    done = _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=1.0)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                          done], minerals=400))
    assert res.tags() == frozenset()
    assert not rt._build_flights.get("q")


def test_flight_retries_when_partial_building_dies():
    """实体半途消失（被拆/弃建 Decay 死）→ failed → 转重试（builder=None，下帧重发）。"""
    from production.economy import WorkerReservations

    res = WorkerReservations()
    port = _Port()
    rt = ProductionRuntime(CAT, port, region_layer=_layer(), reservations=res)
    _emit_one_build(rt, port)
    building = _u(9, "SUPPLYDEPOT", x=3.0, y=3.0, progress=0.3)
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV"),
                          building], minerals=400))
    assert res.tags() == frozenset({2})
    # 实体消失 + 无 build order → 重试路径（重发换位）
    for _ in range(2):
        rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV"),
                              _u(3, "SCV")], minerals=400))
    assert len([o for o in port.submitted if o.action == "build"]) >= 2, "实体死了要重发"


# ---------------- G3（2026-08-24，rev 17）：在训记账 ----------------

def test_training_ledger_records_start_and_expires():
    """emit 训练单 → snapshot.training 带开始时刻（SC2 订单无进度，进度自己记账）；
    产出建筑消失、或无订单且超训练时长 → 淘汰（复盘截断线的左延伸数据源）。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine", count=1)])
    # 场上要有就绪兵营（marine 的产出建筑）
    from tests.production.test_runtime import _u as _uu  # noqa: PLC0415 —— 同文件helper
    gs = _gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")], minerals=200, game_time=50.0)
    rt.on_game_state(gs)
    assert len(port.submitted) == 1
    snap = rt.snapshot()
    assert snap["training"] == [{"stable_id": "terran/marine",
                                 "producer_tag": 7, "started_at": 50.0, "uid": "q01"}]
    # 订单还没落地（orders 空）但没超时长 → 保留（emit→订单落地的间隙）
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")],
                         minerals=200, game_time=51.0))
    assert len(rt.snapshot()["training"]) == 1
    # 无订单且远超 marine 训练时长 → 完成淘汰
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")],
                         minerals=200, game_time=200.0))
    assert rt.snapshot()["training"] == []


# ---------------- ADR-0032（PLAN-V2 批 1）：执行账本 + skip-and-continue ----------------

def _uid_states(rt, name="open"):
    return [(it.uid, it.status) for it in rt.queue(name).items]


def test_uid_assigned_on_submit_and_stable_across_reorder():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="train", type="terran/marine"),
        QueueItem(op="train", type="terran/marine"),
    ])
    a, b = rt.queue("open").items
    assert (a.uid, b.uid) == ("q01", "q02")
    rt.reorder("open", [b, a])  # 重排不变 uid
    assert [it.uid for it in rt.queue("open").items] == ["q02", "q01"]
    rt.append("open", [QueueItem(op="train", type="terran/marine")])
    assert rt.queue("open").items[-1].uid == "q03"


def test_insert_before_uid_and_remove_by_uid():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="train", type="terran/marine"),
        QueueItem(op="train", type="terran/marine"),
    ])
    rt.insert("open", "q02", [QueueItem(op="train", type="terran/scv")])
    assert [it.uid for it in rt.queue("open").items] == ["q01", "q03", "q02"]
    rt.remove("open", rt.item_by_uid("open", "q01"))
    assert [it.uid for it in rt.queue("open").items] == ["q03", "q02"]
    try:
        rt.insert("open", "q99", [QueueItem(op="train", type="terran/scv")])
        raise AssertionError("未知 uid 必须报错")
    except ValueError:
        pass


def test_uid_not_reused_after_clear():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine")])
    rt.clear("open")
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine")])
    assert rt.queue("open").items[0].uid == "q02"


def test_executed_train_item_retained_with_status():
    """账本核心行为：train 发单 → in_progress；训练账本淘汰 → completed；项不出队。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="train", type="terran/marine")])
    gs0 = _gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")], minerals=200, game_time=10.0)
    rt.on_game_state(gs0)
    item = rt.queue("open").items[0]
    assert item.status == "in_progress" and item.count == 0 and item.started_at == 10.0
    # 订单间隙帧：仍 in_progress
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")],
                         minerals=200, game_time=11.0))
    assert item.status == "in_progress"
    # 训练时长过了 → completed，且项还在队列里（不再摘除）
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(7, "BARRACKS")],
                         minerals=200, game_time=200.0))
    assert item.status == "completed" and item.completed_at == 200.0
    assert len(rt.queue("open").items) == 1


def test_skip_does_not_freeze_queue():
    """skip-and-continue：缺产出建筑的 train 被标 skipped，后续 build 照常执行。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="train", type="terran/marine"),          # 没兵营：skip
        QueueItem(op="build", type="terran/supplydepot",
                  placement=PlacementExact("spot")),           # 照常执行
    ])
    gs = _gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    items = rt.queue("open").items
    assert items[0].status == "skipped" and items[0].reason == "prereq_missing"
    assert items[1].status == "in_progress"
    assert len(port.submitted) == 1 and port.submitted[0].action == "build"


def test_pending_head_still_gates_later_items():
    """pending 等待仍保序：队首缺矿时后续项不越序执行。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=50))
    assert port.submitted == []
    items = rt.queue("open").items
    assert items[0].status == "pending" and items[1].status == "pending"
    assert rt.blocked["open"]["item"] is items[0]


def test_build_placement_exhaustion_marks_skip_not_drop():
    """槽位全被占 → skip(placement_collision) 留账本，不是 dropped。"""
    port = _Port()
    rt = _runtime(port)
    # spot 被 CC 占住（同位）：放置校验不通过 → skip
    rt.submit_queue("open", [QueueItem(op="build", type="terran/supplydepot",
                                       placement=PlacementExact("spot"))])
    gs = _gs([_u(1, "COMMANDCENTER", x=1.5, y=1.5), _u(2, "SCV")], minerals=400)
    rt.on_game_state(gs)
    item = rt.queue("open").items[0]
    assert item.status == "skipped" and item.reason == "placement_collision"
    assert rt.dropped == []


def test_unknown_type_is_dropped_not_retained():
    """D6 分工：作者错误（catalog 不认）→ dropped 摘除，不进账本。"""
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [QueueItem(op="build", type="terran/nonsense",
                                       placement=PlacementExact("spot"))])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER"), _u(2, "SCV")], minerals=400))
    assert rt.queue("open").items == []
    assert rt.dropped and "未知类型" in rt.dropped[0][1]


def test_snapshot_items_carry_uid_status_reason():
    port = _Port()
    rt = _runtime(port)
    rt.submit_queue("open", [
        QueueItem(op="train", type="terran/marine"),
        QueueItem(op="build", type="terran/supplydepot", placement=PlacementExact("spot")),
    ])
    rt.on_game_state(_gs([_u(1, "COMMANDCENTER", x=6.0, y=6.0), _u(2, "SCV")], minerals=400))
    snap = rt.snapshot()
    q = snap["queues"][0]
    its = q["items"]
    assert its[0]["uid"] == "q01" and its[0]["status"] == "skipped"
    assert its[0]["reason"] == "prereq_missing"
    assert its[1]["status"] in ("in_progress",) and its[1]["reason"] is None
    assert q["head_status"] in ("可执行", "空")
    # 在途带 uid（observe/警报的引用锚点）
    assert snap["in_flight"] and snap["in_flight"][0]["uid"] == "q02"
