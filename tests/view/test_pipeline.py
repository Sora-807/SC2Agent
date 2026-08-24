"""端到端管线冒烟：真引擎 → snapshot() → adapt → 信封。

这条测试守的是**整条链的连通性**：任何一环改坏（schema 字段改名、adapt 漏字段、
读模型少键、encode 编不了新类型）都会在这里失败，而不是等到前端渲染出 undefined。

它复用 `tools/worldsim.py`（夹具生成器同一个世界），所以夹具与测试不会各自漂移。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from game.catalog import load_all  # noqa: E402
from game.geometry import Point2  # noqa: E402
from game.ports import ApplyResult  # noqa: E402
from game.production import PlacementInRegion, QueueItem, QueueOp, WorkerTask  # noqa: E402
from flow.engine import FlowEngine  # noqa: E402
from flow.manifest import parse_assembly, parse_strategy  # noqa: E402
from production.runtime import ProductionRuntime  # noqa: E402
from tactical_map.base import instantiate_spawn, load_ladder_map  # noqa: E402
from view import adapt  # noqa: E402
from view.alerts import AlertService  # noqa: E402
from view.encode import envelope  # noqa: E402
from view.port import OpRing, RecordingPort  # noqa: E402
from view.schema import REV, TOPICS  # noqa: E402
from view.statics import catalog_static, map_static, resource_nodes_from_state, schema_static  # noqa: E402
from worldsim import WorldSim  # noqa: E402

CAT = load_all()

STRATEGY = """
id: pipe_probe
version: 1
group_slots: [main]
params:
  min_units: {type: int, default: 1}
variables: {}
initial_step: hold
steps:
  - step_id: hold
    branches:
      - branch_id: b_wait
        do: []
edges: []
loop_limits: {max_step_transitions: 10}
"""

ASSEMBLY = """
id: pipe_assembly
groups:
  - group_id: G1
    composition:
      terran/scv: {min: 1, target: 2, max: 3}
strategy_instances:
  - instance_id: s1
    strategy_ref: pipe_probe
    bindings: {main: G1}
    params: {}
"""


class _Sink:
    def __init__(self):
        self.ops = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)

    def drain(self):
        out, self.ops = self.ops, []
        return out


def _run(seconds: int = 30):
    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5), minerals=400.0)
    world.bootstrap(workers=12)
    tpl = load_ladder_map()
    _, layout = sorted(tpl.spawns.items())[0]
    layer = instantiate_spawn(tpl, layout, world.cc_pos)

    sink = _Sink()
    ring = OpRing()
    engine = FlowEngine(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY),
                        RecordingPort(sink, "flow", ring, clock=lambda: world.t),
                        region_layer=layer, catalog=CAT)
    runtime = ProductionRuntime(CAT, RecordingPort(sink, "production", ring, clock=lambda: world.t),
                               region_layer=layer)
    # 让几个工兵闲着，assign_workers(mineral) 才有活干（否则 delta=0，一条命令都不该发）
    for u in list(world.units.values())[:4]:
        if u.type_name == "SCV":
            u.orders = []
    runtime.submit_queue("main", [
        QueueItem(op=QueueOp.BUILD, type="terran/supplydepot",
                  placement=PlacementInRegion(region="home")),
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.MINERAL, count=12),
    ])
    alerts = AlertService(catalog=CAT)

    frames = []
    seq = 0

    def emit(topic, payload):
        nonlocal seq
        seq += 1
        frames.append(envelope(topic, seq=seq, game_time=world.t, payload=payload, wall_ms=0))

    gs = world.game_state()
    emit("static/map", map_static(layer, "bl",
                                 resource_nodes=resource_nodes_from_state(gs.resources)))
    emit("static/catalog", catalog_static(CAT))
    emit("static/schema", schema_static())
    emit("frame/session", adapt.session_frame("对局中", frame_source="fixture",
                                              game_time=world.t, map_name=layer.map_name))
    for _ in range(seconds):
        gs = world.game_state()
        engine.on_game_state(gs)
        runtime.on_game_state(gs)
        world.apply(sink.drain())
        fsnap, psnap = engine.snapshot(), runtime.snapshot()
        emit("frame/world", adapt.world_frame(gs, CAT, group_of=adapt.group_of_from_flow(fsnap)))
        emit("frame/flow", adapt.flow_frame(fsnap, gs))
        emit("frame/production", adapt.with_waited(adapt.production_frame(psnap, CAT), gs.game_time))
        emit("frame/ops", adapt.ops_frame(ring))
        got = alerts.evaluate(gs, production=psnap)
        if got:
            from view.schema import AlertsFrame
            emit("frame/alerts", AlertsFrame(alerts=got))
        world.tick(1.0)
    return world, engine, runtime, ring, frames


def test_pipeline_produces_valid_envelopes():
    _, _, _, _, frames = _run()
    assert frames
    for f in frames:
        assert f["rev"] == REV
        assert f["topic"] in TOPICS
        assert isinstance(f["seq"], int) and isinstance(f["game_time"], (int, float))
        json.dumps(f, ensure_ascii=False)   # 必须可序列化（encode 编不了会在这里炸）


def test_pipeline_engine_actually_reacted_to_the_world():
    """真引擎对真反馈起了作用：补给站被建出来、工兵被分去采气。"""
    world, _, runtime, ring, _ = _run(40)
    built = [u for u in world.units.values() if u.type_name == "SUPPLYDEPOT"]
    assert built, "生产运行时应该真的建出了补给站"
    actions = {r["action"] for r in ring.all()}
    assert "build" in actions
    assert "gather" in actions, "assign_workers 应扇出成 gather"
    assert not runtime.dropped, f"不该有掉项：{runtime.dropped}"


def test_pipeline_group_join_reaches_world_frame():
    """flow 的分组归属 join 进了 world 帧（前端的分组图层靠它）。"""
    _, engine, _, _, frames = _run(10)
    world_frames = [f for f in frames if f["topic"] == "frame/world"]
    grouped = [u for u in world_frames[-1]["payload"]["units"] if u["group_id"]]
    assert grouped, "composition 里有 terran/scv，应该有单位被 lease 进 G1"
    assert all(u["group_id"] == "G1" for u in grouped)
    assert engine.snapshot()["groups"][0]["leased_tags"]


def test_pipeline_ops_carry_origin():
    _, _, _, ring, _ = _run(20)
    origins = {r["origin"] for r in ring.all()}
    assert origins <= {"flow", "production", "worker", "user", "agent"}
    assert "production" in origins, "生产运行时发的 op 应打上 production"


def test_assign_workers_writes_a_persistent_quota_p9_fixed():
    """**已翻转的表征测试**：issues P9 修好了（ADR-0030 D2.2）。

    原来的行为：精炼厂没建好时 `assign_workers(gas,3)` 分配到 0 人后直接出队，
    `dropped`/`blocked` 里什么都没有 —— 意图静默蒸发。
    现在：队列项 = **写目标**（绝对值、幂等），配额持久挂着，气矿建好后维持器自动补满。

    这条测试的价值在于把"修没修"钉死：`quota` 必须留着，`target` 允许为 0。
    """
    from production.economy import EconomyKeeper

    from view.adapt import economy_frame

    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5), minerals=400.0)
    world.bootstrap(workers=12)
    sink = _Sink()
    ring = OpRing()
    port = RecordingPort(sink, "production", ring, clock=lambda: world.t)
    keeper = EconomyKeeper(CAT, port)
    runtime = ProductionRuntime(CAT, port, economy=keeper)
    runtime.submit_queue("main", [QueueItem(op=QueueOp.ASSIGN_WORKERS,
                                            task=WorkerTask.GAS, count=3)])
    gs = world.game_state()
    runtime.on_game_state(gs)

    snap = runtime.snapshot()
    assert snap["queues"][0]["items"][0]["status"] == "completed", "写完目标即完成（账本保留，ADR-0032）"
    assert snap["dropped"] == [], "不是掉项"

    econ = economy_frame(keeper.snapshot(gs))
    gas = {t.task: t for t in econ.tasks}["gas"]
    assert gas.quota == 3, "配额持久挂着 —— 意图不再蒸发"
    assert gas.target == 0, "但现在没有气矿，可达目标是 0"


def test_economy_keeper_puts_idle_workers_back_to_minerals():
    """ADR-0030 验收 1：新造/闲置的 SCV 不需要任何人下令就会进矿。"""
    from production.economy import EconomyKeeper

    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5))
    world.bootstrap(workers=8)
    for u in list(world.units.values()):
        if u.type_name == "SCV":
            u.orders = []                      # 全部闲置
    sink = _Sink()
    ring = OpRing()
    keeper = EconomyKeeper(CAT, RecordingPort(sink, "production", ring, clock=lambda: world.t))
    keeper.on_game_state(world.game_state())
    assert [op.action for op in sink.ops].count("gather") > 0, "维持器应主动把闲人派进矿"


def test_pipeline_no_unhandled_actions_in_world():
    """世界没实现的动作要为空 —— 否则引擎发的命令有一部分没有后果，夹具就失真了。"""
    world, _, _, _, _ = _run(30)
    assert not world.unhandled, f"WorldSim 未实现：{sorted(set(world.unhandled))}"
