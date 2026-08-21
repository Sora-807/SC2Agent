"""api.session：离线沙盒会话（B6 的命令目标；B3 的 live 会话与它同构）。

为什么先做离线会话：命令写入面（B6）需要一个**能被命令改变**的会话，提案审批（B7）
需要一个能真正 apply 的目标 —— 但真 live 要等 B3（`SC2GamePort.start()` 阻塞在 `run_game()`，
要先做进程分离）。所以先用 `tools/worldsim.py` 驱动一套完整装配：
flow + production + economy + 帧生产，全是真引擎。B3 落地时只把 WorldSim 换成真 driver。

它刻意**长得像 `JsonlSource`**（`info/statics/latest_at/between`），所以 WS 那条通道不用改：
前端换成 `source=live` 就在看这个会话，页面代码零改动。

新鲜度（R8 / ADR-0009）：所有命令必带 `based_on_seq`，落后超过 `MAX_STALE_SEQ` 直接拒并回报当前 seq。
这一条是 UI 与 agent 共用的 —— agent 用它的 ObservationPacket 的 seq 填。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from game.catalog import Catalog
from game.geometry import Point2
from game.ports import ApplyResult
from game.production import QueueItem

from flow.allocator import Allocator
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from planner.build_order import ProductionModuleInstance
from planner.planner import Planner
from production.economy import EconomyKeeper, WorkerReservations
from production.runtime import ProductionRuntime
from tactical_map.base import instantiate_spawn, load_ladder_map, load_map_plan
from view.port import OpRing, RecordingPort
from view.producer import FrameProducer
from view.schema import STATIC_TOPICS

from api.sources import SourceInfo

#: 命令允许的最大观察滞后（游戏秒/seq）。R8："旧观察不得作为当前行动依据"。
#: 5 秒是个手感值：UI 点一下的往返远小于它，而"拖回历史后再下命令"会被拒。
MAX_STALE_SEQ = 5

#: 会话内存里保留的帧数（够时间线回看最近一段；完整历史靠 ViewRecorder 落盘）
FRAME_BUFFER = 4000


class StaleObservation(Exception):
    """命令基于过期观察（R8）。带上当前 seq，调用方可以重取再试。"""

    def __init__(self, based_on_seq: int, current_seq: int) -> None:
        super().__init__(
            f"命令基于过期观察：based_on_seq={based_on_seq}，当前 seq={current_seq}"
            f"（允许滞后 {MAX_STALE_SEQ}）。请用最新帧的 seq 重新提交（R8）"
        )
        self.based_on_seq = based_on_seq
        self.current_seq = current_seq


DEFAULT_STRATEGY = """
id: sandbox_hold
version: 1
group_slots: [main]
params:
  min_units: {type: int, default: 6}
  target: {type: point, default: [60.5, 60.5]}
variables: {}
initial_step: gather
steps:
  - step_id: gather
    branches:
      - branch_id: b_ready
        when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - branch_id: b_wait
        do: []
  - step_id: push
    branches:
      - branch_id: b_arrived
        when: {op: arrived, group: main, target: {param: target}, radius: 4.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - branch_id: b_move
        do:
          - {op: group_action, group_slot: main, type: terran/marine,
             action_atom: attack_move_to, params: {position: {param: target}}}
edges:
  - {from: gather, to: push, kind: done, reason: FORMED}
loop_limits: {max_step_transitions: 50}
"""

DEFAULT_ASSEMBLY = """
id: sandbox_assembly
groups:
  - group_id: G_INF
    composition:
      terran/marine: {min: 4, target: 10, max: 12}
strategy_instances:
  - instance_id: s1
    strategy_ref: sandbox_hold
    bindings: {main: G_INF}
    params: {}
"""


class _Collect:
    """收集引擎发出的 op 交给 WorldSim。真机由 driver 承担这一步。"""

    def __init__(self) -> None:
        self.ops: list = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)

    def drain(self) -> list:
        out, self.ops = self.ops, []
        return out


class OfflineSession:
    """离线沙盒：真引擎 + 假世界。`tick()` 推进一个游戏秒并返回本帧信封。"""

    id = "live"

    def __init__(self, catalog: Catalog, *, workers: int = 12, minerals: float = 400.0,
                 label: str = "离线沙盒（真引擎 + 假世界）",
                 map_plan: str | None = None) -> None:
        from worldsim import WorldSim

        self.catalog = catalog
        self.label = label
        self.state = "对局中"
        self.error: str | None = None
        # 会话协议要求 `game_time` 一直可读（提案的 anchor/失效判断读它）；
        # 没初始化的话，第一帧之前访问就会 AttributeError（实测踩过）。
        self.game_time = 0.0
        self.world = WorldSim(catalog=catalog, cc_pos=Point2(30.5, 30.5), minerals=minerals)
        self.world.bootstrap(workers=workers)
        # 会话装配用**选定的地图规划文件**（进入游戏加载哪一份）；缺省 = 手写出厂模板
        tpl = load_map_plan(map_plan) if map_plan else load_ladder_map()
        _, layout = sorted(tpl.spawns.items())[0]
        self.layer = instantiate_spawn(tpl, layout, self.world.cc_pos)

        self.sink = _Collect()
        self.ring = OpRing(maxlen=400)
        clock = self._clock
        self.reservations = WorkerReservations()
        self.allocator = Allocator(catalog=catalog, reservations=self.reservations)
        self.manifest = parse_strategy(DEFAULT_STRATEGY)
        self.assembly = parse_assembly(DEFAULT_ASSEMBLY)
        self.engine = FlowEngine(
            self.manifest, self.assembly,
            RecordingPort(self.sink, "flow", self.ring, clock=clock),
            region_layer=self.layer, catalog=catalog, allocator=self.allocator,
        )
        self.keeper = EconomyKeeper(
            catalog, RecordingPort(self.sink, "worker", self.ring, clock=clock),
            region_layer=self.layer, pool=self.allocator, reservations=self.reservations,
        )
        self.runtime = ProductionRuntime(
            catalog, RecordingPort(self.sink, "production", self.ring, clock=clock),
            region_layer=self.layer, reservations=self.reservations, economy=self.keeper,
        )
        self.producer = FrameProducer(
            catalog=catalog, engine=self.engine, runtime=self.runtime, keeper=self.keeper,
            ring=self.ring, planner=Planner(catalog), region_layer=self.layer,
            manifest=self.manifest, assembly=self.assembly, spawn="bl",
            frame_source="live", enemy_race="protoss",
            projection_plan=[ProductionModuleInstance(
                instance_id="m0", module_ref="basic_opening", version=1, params={})],
        )
        self.frames: list[dict] = list(self.producer.statics(self.world.game_state()))
        self._statics = list(self.frames)

    def _clock(self) -> float:
        return self.world.t

    # ---- 推进 ----

    def tick(self) -> list[dict]:
        """一个游戏秒：flow → production → economy → op 作用回世界 → 产帧。

        顺序有语义：建造征用要先落到 lease 表，维持器才知道哪些工兵不能动（ADR-0030 D3.3）。
        """
        gs = self.world.game_state()
        # 会话协议要求 `game_time` 一直反映「最新一帧」：提案的 anchor/失效判断读它。
        # 必须在这里更新，而不是依赖 producer 的某个调用路径（proposals 可能没接上）。
        self.game_time = gs.game_time
        self.engine.on_game_state(gs)
        self.runtime.on_game_state(gs)
        self.keeper.on_game_state(gs)
        self.world.apply(self.sink.drain())
        produced = self.producer.on_game_state(gs)
        self.frames.extend(produced)
        if len(self.frames) > FRAME_BUFFER:
            # 静态面永不淘汰（任何游标下都有效）
            keep = [f for f in self.frames[-FRAME_BUFFER:] if f["topic"] not in STATIC_TOPICS]
            self.frames = self._statics + keep
        self.world.tick(1.0)
        return produced

    # ---- 新鲜度门（R8）----

    @property
    def seq(self) -> int:
        return self.world.seq

    def check_seq(self, based_on_seq: int | None) -> None:
        if based_on_seq is None:
            raise StaleObservation(-1, self.seq)
        if self.seq - int(based_on_seq) > MAX_STALE_SEQ:
            raise StaleObservation(int(based_on_seq), self.seq)

    # ---- 帧源接口（与 JsonlSource 同形，WS 通道不用改）----

    def info(self) -> SourceInfo:
        times = [f["game_time"] for f in self.frames] or [0.0]
        return SourceInfo(
            id=self.id, label=self.label, kind="live",
            envelopes=len(self.frames), from_time=min(times), to_time=max(times),
            topics=sorted({f["topic"] for f in self.frames}), snapshots=[],
        )

    def statics(self) -> list[dict]:
        """只返回**静态面**，与 `JsonlSource.statics()` 一致。

        `_statics` 里还有一条初始 `frame/session`（它是动态的：状态会变），
        那条走 `latest_at` 的兜底路径下发，不混进静态面。
        """
        return [f for f in self._statics if f["topic"] in STATIC_TOPICS]

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        chosen: dict[str, dict] = {}
        for f in self.frames:
            if topics is not None and f["topic"] not in topics:
                continue
            if f["game_time"] <= game_time + 1e-9:
                chosen[f["topic"]] = f
        for f in self._statics:
            if topics is not None and f["topic"] not in topics:
                continue
            chosen.setdefault(f["topic"], f)
        # 同 `JsonlSource`：保持流的顺序，不按 seq 排（同 tick 的 seq 相同）
        return list(chosen.values())

    def between(self, after: float, until: float,
                topics: set[str] | None = None) -> list[dict]:
        return [
            f for f in self.frames
            if after + 1e-9 < f["game_time"] <= until + 1e-9
            and (topics is None or f["topic"] in topics)
        ]

    # ---- 命令（与 agent 同一入口）----

    def queue_op(self, op: str, name: str, *, items: list[QueueItem] | None = None,
                 index: int | None = None, order: list[int] | None = None) -> dict:
        """生产队列工具 op（S11：轻量，不走 validate/compile，执行时按 constraint 门控）。

        `remove`/`reorder` 用**下标**而不是对象引用：HTTP 上传不了对象引用，
        而下标是前端在同一帧看到的东西 —— 配合 `based_on_seq` 的新鲜度门就够安全。
        """
        q = self.runtime.queue(name)
        if op == "submit":
            self.runtime.submit_queue(name, items or [])
        elif op == "append":
            self.runtime.append(name, items or [])
        elif op == "prepend":
            self.runtime.prepend(name, items or [])
        elif op == "clear":
            self.runtime.clear(name)
        elif op == "remove":
            if q is None or index is None or not (0 <= index < len(q.items)):
                raise ValueError(f"remove：队列 {name!r} 没有下标 {index}")
            self.runtime.remove(name, q.items[index])
        elif op == "reorder":
            if q is None or order is None:
                raise ValueError(f"reorder：队列 {name!r} 不存在或缺 order")
            if sorted(order) != list(range(len(q.items))):
                raise ValueError(
                    f"reorder：order 必须是 0..{len(q.items) - 1} 的一个排列，收到 {order}")
            self.runtime.reorder(name, [q.items[i] for i in order])
        else:
            raise ValueError(f"未知队列 op {op!r}（submit|append|prepend|clear|remove|reorder）")
        after = self.runtime.queue(name)
        return {"queue": name, "items": len(after.items) if after else 0, "accepted_seq": self.seq}

    def set_worker_target(self, task: str, count: int) -> dict:
        """采集配额 = **目标值**（维持 N 个，幂等；ADR-0030 D2），不是"再派 N 个"。"""
        self.keeper.set_target(task, int(count))
        return {"task": task, "quota": int(count), "accepted_seq": self.seq}

    # ---- 提案需要的三件事（与 LiveSession 同名同义）----

    def queue_items(self, name: str = "main") -> list:
        q = self.runtime.queue(name)
        return list(q.items) if q else []

    def apply_queue(self, name: str, items: list) -> dict:
        return self.queue_op("submit", name, items=items)

    def project(self, items: list, *, name: str = "main", horizon: float = 120.0,
                timeout: float = 5.0) -> dict | None:
        """本地算（我们就有 GameState）。签名与 `LiveSession.project` 一致。"""
        from view.adapt import projection_frame
        from view.encode import to_json
        from view.projection import project_queue

        gs = self.world.game_state()
        curve, tr = project_queue(self.producer.planner, gs, items,
                                  until=gs.game_time + horizon, catalog=self.catalog)
        return to_json(projection_frame(
            curve, based_on_seq=gs.seq, based_on_game_time=gs.game_time,
            horizon=horizon, queue_name=name, skipped=tr.skipped))

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "state": self.state,
            "seq": self.seq, "game_time": round(self.world.t, 3),
            "max_stale_seq": MAX_STALE_SEQ,
            "queues": [q["name"] for q in self.runtime.snapshot()["queues"]],
            "error": self.error,
        }