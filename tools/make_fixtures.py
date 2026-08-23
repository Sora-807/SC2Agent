"""tools/make_fixtures.py：用**真引擎**产出帧夹具。

链路：`WorldSim` → `GameState` → 真 `FlowEngine`/`ProductionRuntime`/`EconomyKeeper`
→ `snapshot()` → `view.producer.FrameProducer` → `view.recorder.ViewRecorder` → JSONL。

关键：产帧逻辑**不在这里**，在 `view.producer` —— live 推送、复盘录制、离线夹具共用同一条路径。
夹具和 live 各写一套产帧逻辑的话，两边迟早不一致，而表现是"复盘看着对、live 看着不对"。

用法：
    uv run python -X utf8 tools/make_fixtures.py [输出目录]
默认输出 `web/public/fixtures/`（前端 `pnpm gen:fixtures` 就调它）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "tools"))

from game.catalog import load_all  # noqa: E402
from game.geometry import Grid, Point2  # noqa: E402
from game.ports import ApplyResult  # noqa: E402
from game.production import PlacementInRegion, QueueItem, QueueOp, WorkerTask  # noqa: E402
from flow.allocator import Allocator  # noqa: E402
from flow.engine import FlowEngine  # noqa: E402
from flow.manifest import parse_assembly, parse_strategy  # noqa: E402
from planner.build_order import ProductionModuleInstance  # noqa: E402
from planner.planner import Planner  # noqa: E402
from production.economy import EconomyKeeper, WorkerReservations  # noqa: E402
from production.runtime import ProductionRuntime  # noqa: E402
from tactical_map.base import instantiate_spawn, load_ladder_map  # noqa: E402
from view.adapt import economy_frame  # noqa: E402
from view.port import OpRing, RecordingPort  # noqa: E402
from view.producer import FrameProducer  # noqa: E402
from view.recorder import ViewRecorder  # noqa: E402
from view.schema import HunkView, ProposalsFrame, ProposalView  # noqa: E402
from view.statics import ladder_terrain_view, terrain_static  # noqa: E402
from worldsim import WorldSim  # noqa: E402

CAT = load_all()
BASIC_PLAN = [ProductionModuleInstance(
    instance_id="m0", module_ref="basic_opening", version=1, params={})]


class _Collect:
    """收集本帧引擎发出的 op，交给 WorldSim 应用。真机由 driver 承担这一步。"""

    def __init__(self) -> None:
        self.ops: list = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)

    def drain(self) -> list:
        out, self.ops = self.ops, []
        return out


def _layer(cc: Point2):
    """LadderMap 主基模板实例化到给定 CC（真数据：区域 + 有序 build_slots）。"""
    tpl = load_ladder_map()
    _, layout = sorted(tpl.spawns.items())[0]
    return instantiate_spawn(tpl, layout, cc)


# ---------------- 合成地形（B16） ----------------

def _synth_terrain() -> dict:
    """确定性合成地形（固定形状，无随机）：以主基为中心的三级方形台地。

    离线夹具没有 SC2 game_info，但 F11 的台地-悬崖-斜坡渲染和 F14 的 placeable
    编辑背景都需要 static/terrain。夹具本来就是假世界（WorldSim），这里合成一份
    **结构可信**的地形，让离线规划页与真机看到的语言一致：
    - 高度：切比雪夫距离方形台地（0/10/20/30 四级，量化后是 4 个 level）；
    - 悬崖：台地交界环不可走（前端画硬描边 + 压暗）；
    - 斜坡：y∈[28,32] 的东西走廊切开悬崖墙（pathable 的交界格 → 前端暖色斜纹）；
    - placeable：3×3 邻域同 level 且可走（平坦可建）—— 悬崖边/斜坡上/远端高地不可建。
    """
    W, H = 176, 160
    cx, cy = 30, 30

    def level_at(x: int, y: int) -> int:
        d = max(abs(x - cx), abs(y - cy))
        if d <= 24:
            return 0
        if d <= 34:
            return 10
        if d <= 46:
            return 20
        return 30

    def pathable_at(x: int, y: int) -> int:
        if 28 <= y <= 32:
            return 1   # 斜坡走廊：贯穿东西，切开所有悬崖墙
        d = max(abs(x - cx), abs(y - cy))
        if d in (25, 35, 47):
            return 0   # 悬崖墙（台地交界环）
        if d > 90:
            return 0   # 远端高地：不可走（前端压暗去饱和）
        return 1

    def placeable_at(x: int, y: int) -> int:
        if x < 1 or y < 1 or x >= W - 1 or y >= H - 1:
            return 0
        if not pathable_at(x, y):
            return 0
        lv = level_at(x, y)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if level_at(x + dx, y + dy) != lv:
                    return 0
        return 1

    return {
        "height": Grid(W, H, [[level_at(x, y) for x in range(W)] for y in range(H)]),
        "pathable": Grid(W, H, [[pathable_at(x, y) for x in range(W)] for y in range(H)]),
        "placeable": Grid(W, H, [[placeable_at(x, y) for x in range(W)] for y in range(H)]),
    }


# ---------------- 策略 ----------------

FORMUP_STRATEGY = """
id: formup_probe
version: 1
display_name_zh: 集结推进
description_zh: 凑够 min_units 个单位后整队前往目标点，抵达即结束。
group_slots: [main]
params:
  min_units: {type: int, default: 2, description_zh: 出发所需的单位数}
  target: {type: point, default: [48.5, 48.5], description_zh: 推进目标点}
variables: {}
reasons:
  FORMED: 集结完成
  ARRIVED: 已抵达目标
initial_step: formup
steps:
  - step_id: formup
    display_name_zh: 集结
    description_zh: 等待部队成型（组内数量 ≥ min_units）
    branches:
      - branch_id: b_ready
        when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - branch_id: b_wait
        do: []
  - step_id: advance
    display_name_zh: 推进
    description_zh: 全组攻击移动到目标点
    branches:
      - branch_id: b_arrived
        when: {op: arrived, group: main, target: {param: target}, radius: 3.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - branch_id: b_move
        do:
          - {op: group_action, group_slot: main, type: terran/marine,
             action_atom: attack_move_to, params: {position: {param: target}}}
edges:
  - {from: formup, to: advance, kind: done, reason: FORMED}
loop_limits: {max_step_transitions: 50}
"""

FORMUP_ASSEMBLY = """
id: formup_assembly
groups:
  - group_id: G_INF
    display_name_zh: 步兵组
    composition:
      terran/marine: {min: 2, target: 6, max: 8}
strategy_instances:
  - instance_id: s1
    strategy_ref: formup_probe
    bindings: {main: G_INF}
    params: {}
"""

# 蛙跳：装甲推到步兵前方 hop 距离 → 步兵跟上 → 循环（转移历史因此会累积）
LEAPFROG_STRATEGY = """
id: leapfrog_probe
version: 1
display_name_zh: 装甲蛙跳推进
description_zh: 坦克先推到步兵前方 hop 距离，步兵跟上，循环往复直到目标。
group_slots: [inf, armor]
params:
  min_inf: {type: int, default: 6, description_zh: 出发所需枪兵数}
  min_armor: {type: int, default: 2, description_zh: 出发所需坦克数}
  target: {type: point, default: [80.5, 80.5], description_zh: 推进目标点}
  hop: {type: float, default: 7.0, description_zh: 每轮蛙跳的前压距离}
variables: {}
reasons:
  READY: 集结就绪
  ARMOR_UP: 坦克已前压
  INF_UP: 步兵已跟进
  ARRIVED: 已抵达目标
definitions:
  front:
    op: point_toward
    origin: {op: group_center, group: inf}
    toward: {param: target}
    dist: {param: hop}
initial_step: garrison
steps:
  - step_id: garrison
    display_name_zh: 驻守集结
    description_zh: 等步兵与坦克都凑够数量
    branches:
      - branch_id: b_ready
        when:
          op: and
          args:
            - {op: ">=", args: [{op: group_count, group: inf}, {param: min_inf}]}
            - {op: ">=", args: [{op: group_count, group: armor}, {param: min_armor}]}
        do: [{op: exit_step, kind: done, reason: READY}]
      - branch_id: b_wait
        do: []
  - step_id: armor_hop
    display_name_zh: 坦克前压
    description_zh: 装甲推到步兵前方 hop 距离处
    branches:
      - branch_id: b_armor_up
        when: {op: arrived, group: armor, target: {ref: front}, radius: 3.5}
        do: [{op: exit_step, kind: done, reason: ARMOR_UP}]
      - branch_id: b_armor_move
        do:
          - {op: group_action, group_slot: armor, type: terran/siegetank,
             action_atom: attack_move_to, params: {position: {ref: front}}}
  - step_id: inf_hop
    display_name_zh: 步兵跟进
    description_zh: 步兵跟上坦克新位置；整队抵达目标则结束
    branches:
      # 环的出口（ADR-0021 §4：每个环必须有出口，loop_limits 只是兜底）
      - branch_id: b_done
        when: {op: arrived, group: inf, target: {param: target}, radius: 5.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - branch_id: b_inf_up
        when: {op: arrived, group: inf, target: {ref: front}, radius: 3.5}
        do: [{op: exit_step, kind: done, reason: INF_UP}]
      - branch_id: b_inf_move
        do:
          - {op: group_action, group_slot: inf, type: terran/marine,
             action_atom: attack_move_to, params: {position: {ref: front}}}
edges:
  - {from: garrison, to: armor_hop, kind: done, reason: READY}
  - {from: armor_hop, to: inf_hop, kind: done, reason: ARMOR_UP}
  - {from: inf_hop, to: armor_hop, kind: done, reason: INF_UP}
loop_limits: {max_step_transitions: 100}
"""

LEAPFROG_ASSEMBLY = """
id: leapfrog_assembly
groups:
  - group_id: G_INF
    display_name_zh: 步兵组
    composition:
      terran/marine: {min: 6, target: 10, max: 12}
  - group_id: G_TANK
    display_name_zh: 装甲组
    composition:
      terran/siegetank: {min: 2, target: 3, max: 4}
strategy_instances:
  - instance_id: s1
    strategy_ref: leapfrog_probe
    bindings: {inf: G_INF, armor: G_TANK}
    params: {}
"""


class SceneRunner:
    """离线会话装配：一张 lease 表 + flow/production/economy 三方 + 帧生产器。

    这套装配与 B3 的 live 会话**同构** —— 到时候只是把 WorldSim 换成真 driver。
    """

    def __init__(self, name: str, world: WorldSim, layer, *, strategy: str, assembly: str,
                 queue: list[QueueItem] | None = None, race_enemy: str = "protoss",
                 out_dir: Path | None = None) -> None:
        self.name = name
        self.world = world
        self.layer = layer
        self.sink = _Collect()
        self.ring = OpRing(maxlen=300)
        clock = lambda: self.world.t  # noqa: E731
        # ADR-0030 D3：**一张 lease 表**（Allocator + 共享 WorkerReservations），三方都从它取人。
        # 会话装配在这里构造并注入（D3.5），所以夹具跑的就是真的所有权模型：
        # 战斗组租走的、建造征用中的工兵，维持器都自动不碰。
        self.reservations = WorkerReservations()
        self.allocator = Allocator(catalog=CAT, reservations=self.reservations)
        self.manifest = parse_strategy(strategy)
        self.assembly = parse_assembly(assembly)
        self.engine = FlowEngine(
            self.manifest, self.assembly,
            RecordingPort(self.sink, "flow", self.ring, clock=clock),
            region_layer=layer, catalog=CAT, allocator=self.allocator,
        )
        self.keeper = EconomyKeeper(
            CAT, RecordingPort(self.sink, "worker", self.ring, clock=clock),
            region_layer=layer, pool=self.allocator, reservations=self.reservations,
        )
        self.runtime = ProductionRuntime(
            CAT, RecordingPort(self.sink, "production", self.ring, clock=clock),
            region_layer=layer, reservations=self.reservations, economy=self.keeper,
        )
        if queue:
            self.runtime.submit_queue("main", queue)
        self.producer = FrameProducer(
            catalog=CAT, engine=self.engine, runtime=self.runtime, keeper=self.keeper,
            ring=self.ring, planner=Planner(CAT), region_layer=layer,
            manifest=self.manifest, assembly=self.assembly,
            spawn="bl", frame_source="fixture", enemy_race=race_enemy,
            projection_plan=BASIC_PLAN,
        )
        out = out_dir or (ROOT / "web" / "public" / "fixtures")
        self.recorder = ViewRecorder(out / f"{name}.jsonl", self.producer)

    def tick(self) -> None:
        """一个游戏秒：flow → production → economy → 把 op 作用回世界 → 录帧。

        顺序有语义：建造征用要先落到 lease 表里，维持器才知道哪些工兵不能动（ADR-0030 D3.3）。
        """
        gs = self.world.game_state()
        self.engine.on_game_state(gs)
        self.runtime.on_game_state(gs)
        self.keeper.on_game_state(gs)
        self.world.apply(self.sink.drain())
        self.recorder.on_game_state(gs)
        self.world.tick(1.0)

    def run(self, seconds: int) -> None:
        for i in range(seconds + 1):
            self.tick()
            if i == 0:
                # B16：首帧（statics 已落盘）之后补发地形。必须在 static/map **之后**：
                # 前端 store 的合并逻辑是「map 到了才把 terrain 并进去」，早到会被丢。
                # 优先用**真机采集**的地形数据文件（2026-08-21 落盘，离线即真实地图）；
                # 文件缺失才退回 B16 的合成台地（确定性兜底）。
                terrain = ladder_terrain_view() or terrain_static(_synth_terrain())
                self.emit_extra("static/terrain", terrain)

    def emit_extra(self, topic: str, payload) -> None:
        """补发一条非周期性的帧（如提案样本）。"""
        gs = self.world.game_state()
        self.recorder._write([self.producer._env(topic, gs, payload)])  # noqa: SLF001

    def close(self) -> None:
        self.recorder.close()


# ---------------- 三个场景 ----------------

def scene_opening(out: Path) -> SceneRunner:
    """开局 90 秒：真生产队列跑补给站→兵营→精炼厂→维持 3 采气→出兵；flow 等兵成型后推进。"""
    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5))
    world.bootstrap(workers=12)
    layer = _layer(world.cc_pos)
    queue = [
        QueueItem(op=QueueOp.BUILD, type="terran/supplydepot",
                  placement=PlacementInRegion(region="home")),
        QueueItem(op=QueueOp.BUILD, type="terran/barracks",
                  placement=PlacementInRegion(region="home")),
        QueueItem(op=QueueOp.BUILD, type="terran/refinery"),
        # 目标值语义（ADR-0030 D2）：维持 3 个采气。精炼厂还在建也没关系，配额挂着，
        # 建好后维持器自动补满 —— 这就是 issues P9 的修复。
        QueueItem(op=QueueOp.ASSIGN_WORKERS, task=WorkerTask.GAS, count=3),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=6),
    ]
    r = SceneRunner("opening", world, layer, strategy=FORMUP_STRATEGY,
                    assembly=FORMUP_ASSEMBLY, queue=queue, out_dir=out)
    r.run(90)
    return r


def scene_blocked(out: Path) -> SceneRunner:
    """生产阻塞 70 秒：队首重工厂缺气 → 队首门控冻结整队、矿越堆越多、超阈值后警报升级。"""
    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5), minerals=700.0, gas=0.0)
    world.bootstrap(workers=16)
    world._spawn("terran/supplydepot", Point2(24.0, 36.0), ready=True)  # noqa: SLF001
    world._spawn("terran/barracks", Point2(36.5, 36.5), ready=True)  # noqa: SLF001
    layer = _layer(world.cc_pos)
    queue = [
        # 不支持项放队首：队首门控下它必须先出队（否则会永久占住队首）→ 演示"掉项带原因"
        QueueItem(op=QueueOp.RESEARCH, type="terran/marine"),
        QueueItem(op=QueueOp.BUILD, type="terran/factory",
                  placement=PlacementInRegion(region="home")),
        QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=8),
    ]
    r = SceneRunner("blocked", world, layer, strategy=FORMUP_STRATEGY,
                    assembly=FORMUP_ASSEMBLY, queue=queue, out_dir=out)
    r.run(70)
    # 一条待审批提案（B7 之前由夹具提供样本：提案内容天然是 agent 产出的）
    r.emit_extra("proposals", ProposalsFrame(proposals=[ProposalView(
        id="p-001", author="agent", created_at=r.world.t,
        anchor={"seq": r.world.seq, "game_time": r.world.t},
        kind="production_queue",
        title_zh="把重工厂后移，先出 8 个机枪兵",
        rationale_zh=("队首重工厂缺气已阻塞很久，浮矿持续上涨。先把无气需求的机枪兵提前，"
                      "气攒够再回到重工厂，预计少浪费一段产能。"),
        target={"queue": "main"},
        hunks=[HunkView(id="h1", kind="reorder",
                        text_zh="把 #0 重工厂移到 #1 机枪兵×8 之后",
                        payload={"from": 0, "to": 1})],
        validation={"ok": True, "errors": []},
        preview={"kind": "projection_pair", "current_ref": "proj:live",
                 "proposed_ref": "proj:draft:p-001"},
        status="待审批", decision=None,
    )]))
    return r


def scene_leapfrog(out: Path) -> SceneRunner:
    """蛙跳推进 90 秒：步坦交替前压，策略图在两个 step 间成环 → 转移历史累积。"""
    world = WorldSim(catalog=CAT, cc_pos=Point2(30.5, 30.5), minerals=900.0, gas=400.0)
    world.bootstrap(workers=14)
    world._spawn("terran/barracks", Point2(36.5, 36.5), ready=True)  # noqa: SLF001
    for i in range(10):
        world._spawn("terran/marine", Point2(34.0 + i * 0.7, 42.0), ready=True)  # noqa: SLF001
    for i in range(3):
        world._spawn("terran/siegetank", Point2(33.0 + i * 1.6, 40.0), ready=True)  # noqa: SLF001
    world.spawn_enemy("ZEALOT", Point2(70.0, 70.0), count=6)
    layer = _layer(world.cc_pos)
    queue = [QueueItem(op=QueueOp.TRAIN, type="terran/marine", count=10)]
    r = SceneRunner("leapfrog", world, layer, strategy=LEAPFROG_STRATEGY,
                    assembly=LEAPFROG_ASSEMBLY, queue=queue, out_dir=out)
    r.run(90)
    return r


LABELS = {
    "opening": "① 开局 90 秒（真生产链）",
    "blocked": "② 生产阻塞（队首缺气 + 警报升级）",
    "leapfrog": "③ 蛙跳推进（策略图成环）",
}


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web" / "public" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for build in (scene_opening, scene_blocked, scene_leapfrog):
        runner = build(out_dir)
        runner.close()
        frames = [json.loads(line) for line in
                  runner.recorder.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        times = [f["game_time"] for f in frames]
        topics = sorted({f["topic"] for f in frames})
        index.append({
            "key": runner.name, "label": LABELS.get(runner.name, runner.name),
            "file": runner.recorder.path.name, "envelopes": len(frames),
            "from": min(times), "to": max(times),
            "snapshots": runner.recorder.snapshots,
        })
        econ = economy_frame(runner.keeper.snapshot(runner.world.game_state()))
        unhandled = sorted(set(runner.world.unhandled))
        print(f"{runner.recorder.path.name}: 信封 {len(frames)} / topic {len(topics)}"
              f" / 时间 {min(times)}→{max(times)} / 快照点 {len(runner.recorder.snapshots)}"
              + (f" / 世界未实现 {unhandled}" if unhandled else ""))
        print(f"    队列剩余 {[len(q['items']) for q in runner.runtime.snapshot()['queues']]}"
              f" / 掉项 {len(runner.runtime.dropped)}"
              f" / 转移 {runner.engine.snapshot()['transition_count']}"
              f" / op {len(runner.ring.all())}")
        print("    经济 " + " ".join(
            f"{t.task}={t.actual}/{t.target}" + (f"(配额{t.quota})" if t.quota is not None else "")
            for t in econ.tasks) + f" / 征用 {len(econ.reserved)}")
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n已写出 {out_dir}（帧由 view.producer 产出 —— 与 live 推送同一条路径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
