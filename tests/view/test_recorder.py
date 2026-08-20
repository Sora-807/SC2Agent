"""view.producer / view.recorder（B5）：唯一的产帧路径 + JSONL 录制。

守的核心是"**只有一条产帧路径**"：夹具、复盘录制、live 推送都走 `FrameProducer`。
两套产帧逻辑最难查的表现是"复盘看着对、live 看着不对"。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from game.catalog import load_terran  # noqa: E402
from game.geometry import Point2  # noqa: E402
from game.ports import ApplyResult  # noqa: E402
from game.production import PlacementInRegion, QueueItem, QueueOp  # noqa: E402
from flow.allocator import Allocator  # noqa: E402
from flow.engine import FlowEngine  # noqa: E402
from flow.manifest import parse_assembly, parse_strategy  # noqa: E402
from planner.build_order import ProductionModuleInstance  # noqa: E402
from planner.planner import Planner  # noqa: E402
from production.economy import EconomyKeeper, WorkerReservations  # noqa: E402
from production.runtime import ProductionRuntime  # noqa: E402
from tactical_map.base import instantiate_spawn, load_ladder_map  # noqa: E402
from view.port import OpRing, RecordingPort  # noqa: E402
from view.producer import FrameProducer  # noqa: E402
from view.recorder import ViewRecorder  # noqa: E402
from view.schema import REV, STATIC_TOPICS, TOPICS  # noqa: E402
from worldsim import WorldSim  # noqa: E402

CAT = load_terran()

STRATEGY = """
id: rec_probe
version: 1
group_slots: [main]
params: {min_units: {type: int, default: 1}}
variables: {}
initial_step: hold
steps:
  - step_id: hold
    branches:
      - branch_id: b_wait
        do: []
edges: []
loop_limits: {max_step_transitions: 5}
"""

ASSEMBLY = """
id: rec_assembly
groups:
  - group_id: G1
    composition:
      terran/scv: {min: 1, target: 2, max: 3}
strategy_instances:
  - instance_id: s1
    strategy_ref: rec_probe
    bindings: {main: G1}
    params: {}
"""


class _Sink:
    def __init__(self):
        self.ops: list = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)

    def drain(self):
        out, self.ops = self.ops, []
        return out


def _session(with_queue: bool = True):
    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5), minerals=500.0)
    world.bootstrap(workers=12)
    tpl = load_ladder_map()
    _, layout = sorted(tpl.spawns.items())[0]
    layer = instantiate_spawn(tpl, layout, world.cc_pos)
    sink = _Sink()
    ring = OpRing()
    clock = lambda: world.t  # noqa: E731
    res = WorkerReservations()
    alloc = Allocator(catalog=CAT, reservations=res)
    manifest, assembly = parse_strategy(STRATEGY), parse_assembly(ASSEMBLY)
    engine = FlowEngine(manifest, assembly, RecordingPort(sink, "flow", ring, clock=clock),
                        region_layer=layer, catalog=CAT, allocator=alloc)
    keeper = EconomyKeeper(CAT, RecordingPort(sink, "worker", ring, clock=clock),
                           region_layer=layer, pool=alloc, reservations=res)
    runtime = ProductionRuntime(CAT, RecordingPort(sink, "production", ring, clock=clock),
                                region_layer=layer, reservations=res, economy=keeper)
    if with_queue:
        runtime.submit_queue("main", [QueueItem(
            op=QueueOp.BUILD, type="terran/supplydepot",
            placement=PlacementInRegion(region="home"))])
    producer = FrameProducer(
        catalog=CAT, engine=engine, runtime=runtime, keeper=keeper, ring=ring,
        planner=Planner(CAT), region_layer=layer, manifest=manifest, assembly=assembly,
        frame_source="replay",
        projection_plan=[ProductionModuleInstance(
            instance_id="m0", module_ref="basic_opening", version=1, params={})],
    )
    return world, engine, runtime, keeper, sink, producer


def _run(recorder: ViewRecorder, world, engine, runtime, keeper, sink, seconds: int) -> None:
    for _ in range(seconds):
        gs = world.game_state()
        engine.on_game_state(gs)
        runtime.on_game_state(gs)
        keeper.on_game_state(gs)
        world.apply(sink.drain())
        recorder.on_game_state(gs)
        world.tick(1.0)


# ---------------- FrameProducer ----------------

def test_producer_emits_all_static_faces_once():
    world, *_rest, producer = _session()
    gs = world.game_state()
    statics = producer.statics(gs)
    topics = [f["topic"] for f in statics]
    for t in STATIC_TOPICS:
        assert t in topics, f"缺静态面 {t}"
    assert "frame/session" in topics
    assert all(f["rev"] == REV for f in statics)


def test_producer_only_reads_snapshots_never_drives_engines():
    """产帧不驱动引擎 —— 谁先 tick 有语义（ADR-0030 D3.3 的征用先后），那是会话装配的事。"""
    world, engine, runtime, keeper, _sink, producer = _session()
    gs = world.game_state()
    before = engine.snapshot()["active_step"]
    producer.on_game_state(gs)
    producer.on_game_state(gs)
    assert engine.snapshot()["active_step"] == before
    assert world.t == 0.0, "产帧不该推进世界"


def test_producer_respects_cadence():
    """投影 3s 一次、ops 5s 一次（ADR-0025 §6），而不是每帧都发。"""
    world, engine, runtime, keeper, sink, producer = _session()
    proj = ops = 0
    for _ in range(10):
        gs = world.game_state()
        engine.on_game_state(gs)
        runtime.on_game_state(gs)
        keeper.on_game_state(gs)
        world.apply(sink.drain())
        for f in producer.on_game_state(gs):
            if f["topic"] == "frame/projection":
                proj += 1
            if f["topic"] == "frame/ops":
                ops += 1
        world.tick(1.0)
    assert 3 <= proj <= 5, f"10 秒里应有 3~4 次投影，实际 {proj}"
    assert 2 <= ops <= 3, f"10 秒里应有 2~3 次 ops，实际 {ops}"


def test_producer_works_with_only_catalog():
    """只给 catalog 也能产帧 —— 服务"只有 driver 的裸录制"场景。

    引擎相关的 topic（flow/production/economy/projection/ops）一个都不该出现：
    没有读模型就没有内容，宁可没有也不编（红线 C7 的后端一侧）。
    警报是例外 —— 它只需要 GameState（如"产线空闲"）。
    """
    world = WorldSim(catalog=CAT)
    world.bootstrap(workers=4)
    p = FrameProducer(catalog=CAT)
    topics = {f["topic"] for f in p.on_game_state(world.game_state())}
    assert "frame/world" in topics
    assert topics <= {"frame/world", "frame/alerts"}, f"多出了 {topics}"


# ---------------- ViewRecorder ----------------

def test_recorder_writes_valid_jsonl_with_statics_first(tmp_path: Path):
    world, engine, runtime, keeper, sink, producer = _session()
    rec = ViewRecorder(tmp_path / "rec.jsonl", producer)
    _run(rec, world, engine, runtime, keeper, sink, 8)
    rec.close()

    rows = [json.loads(x) for x in rec.path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == rec.count > 0
    assert rows[0]["topic"] in STATIC_TOPICS, "静态面必须在最前（前端要先拿到地图与目录）"
    for r in rows:
        assert r["rev"] == REV
        assert r["topic"] in TOPICS
    assert {r["topic"] for r in rows} >= {"frame/world", "frame/flow", "frame/production",
                                          "frame/economy"}


def test_recorder_marks_snapshot_anchors(tmp_path: Path):
    """ADR-0024 §6：5 秒一个快照点 —— 时间线上可跳的锚。"""
    world, engine, runtime, keeper, sink, producer = _session()
    rec = ViewRecorder(tmp_path / "rec.jsonl", producer, snapshot_every=5.0)
    _run(rec, world, engine, runtime, keeper, sink, 21)
    rec.close()
    assert rec.snapshots == [0.0, 5.0, 10.0, 15.0, 20.0]


def test_recorder_flushes_so_a_crash_still_leaves_playable_frames(tmp_path: Path):
    """真机崩溃是常态（S7）：录到一半也必须可播。"""
    world, engine, runtime, keeper, sink, producer = _session()
    rec = ViewRecorder(tmp_path / "rec.jsonl", producer)
    _run(rec, world, engine, runtime, keeper, sink, 3)
    # **不 close**，直接读文件
    rows = [json.loads(x) for x in rec.path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) > 0
    assert rows[-1]["topic"] in TOPICS
    rec.close()


def test_recorder_session_event_records_nothing(tmp_path: Path):
    """`GameEvent` 目录（D7）还没定 → 不猜字段、不造数据。"""
    world, *_r, producer = _session()
    rec = ViewRecorder(tmp_path / "rec.jsonl", producer)
    rec.on_session_event({"kind": "whatever"})
    rec.close()
    assert rec.count == 0


def test_recorder_output_is_playable_by_api(tmp_path: Path):
    """录出来的东西 api 能当帧源直接播 —— 这就是"live 与复盘同一批字节"。"""
    from fastapi.testclient import TestClient

    from api.app import create_app

    world, engine, runtime, keeper, sink, producer = _session()
    rec = ViewRecorder(tmp_path / "myrun.jsonl", producer)
    _run(rec, world, engine, runtime, keeper, sink, 12)
    rec.close()

    client = TestClient(create_app(tmp_path))
    rows = client.get("/api/sources").json()
    assert [r["id"] for r in rows] == ["myrun"]
    statics = client.get("/api/sources/myrun/statics").json()
    assert [s["topic"] for s in statics] == list(STATIC_TOPICS)
    at = client.get("/api/sources/myrun/frames", params={"game_time": 6.0}).json()
    assert any(f["topic"] == "frame/world" for f in at)
