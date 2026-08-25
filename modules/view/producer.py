"""view.producer：帧生产器 —— 引擎读模型 → 一组信封。

**这是唯一的产帧路径**。live 推送（B3）、复盘录制（B5 的 ViewRecorder）、离线夹具
（`tools/make_fixtures.py`）都走它。之所以要"唯一"：夹具和 live 各写一套产帧逻辑，
两边迟早不一致 —— 而不一致的表现是"复盘看着对、live 看着不对"，最难查的那种。

节拍对齐 `game_time`（ADR-0025 §6）：1s 观察 / 3s 投影 / 事件驱动警报。
不用墙钟定时器 —— 复盘要能按游戏时间重放。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from game.catalog import Catalog
from game.state import GameState

from view import adapt
from view.alerts import AlertService
from view.encode import envelope
from view.projection import project_queue
from view.schema import AlertsFrame, ProposalsFrame
from view.statics import (
    catalog_static,
    map_static,
    resource_nodes_from_state,
    schema_static,
    strategy_static,
)

#: 默认节拍（游戏秒）。与 ADR-0025 §6 的表一致。
PROJECTION_EVERY = 3.0
OPS_EVERY = 5.0
PROJECTION_HORIZON = 120.0


@dataclass
class FrameProducer:
    """把一帧 GameState + 各引擎的 `snapshot()` 变成信封列表。

    组件都是**可选注入**：只给 catalog 也能产 world 帧（离线看单帧用），
    给全了就产全部 topic。这样同一个生产器既能服务"只有 driver 的裸录制"，
    也能服务"完整会话"。

    刻意**不自己驱动引擎**：谁先 tick（flow → production → economy 的顺序有语义，
    见 ADR-0030 D3.3 的征用先后）是会话装配的事，不是产帧的事。
    """

    catalog: Catalog
    engine: Any = None            # FlowEngine（要 snapshot()）
    runtime: Any = None           # ProductionRuntime
    keeper: Any = None            # EconomyKeeper
    ring: Any = None              # view.port.OpRing
    planner: Any = None           # planner.Planner
    region_layer: Any = None
    manifest: Any = None          # StrategyManifest（static/strategy 用）
    assembly: Any = None
    spawn: str = "bl"
    frame_source: str = "live"
    my_race: str = "terran"
    enemy_race: str | None = None
    #: 兜底的"参考计划"（没有 live 队列时用）。有 runtime 且队列非空时**优先投影真队列**。
    projection_plan: list | None = None
    #: 投影哪条队列（多队列时取这条；它们互相独立，拼起来投影是错的）
    projection_queue: str = "main"
    horizon: float = PROJECTION_HORIZON
    projection_every: float = PROJECTION_EVERY
    ops_every: float = OPS_EVERY
    alerts: AlertService | None = None
    #: 提案存储（B7）。给了就在内容变化时发 `proposals` 帧 —— 只在变化时发，
    #: 否则每帧一条会把 WS 刷满（提案是事件驱动的，不是周期性的）。
    proposals: Any = None
    include_grids: bool = False
    #: 墙钟（秒）—— `wall_ms` 诊断字段用。真机/live 装配传 `time.time`（默认）；
    #: 测试传固定 lambda 拿确定性输出。game_time 仍是唯一语义时间基准，见 Envelope。
    clock: Callable[[], float] = time.time

    _proj_at: float = field(default=-1e18)
    _ops_at: float = field(default=-1e18)
    _grids_fp: tuple[str | None, str | None] | None = None
    _proposals_fingerprint: str = ""
    #: 本会话是否见过非空 live 队列（true 后永不回退参考计划，见 `_project`）
    _live_seen: bool = False

    def __post_init__(self) -> None:
        if self.alerts is None:
            self.alerts = AlertService(catalog=self.catalog)

    # ---- 静态面（每局一次）----

    def statics(self, gs: GameState, *, session_state: str = "对局中") -> list[dict]:
        out: list[dict] = []
        if self.region_layer is not None:
            out.append(self._env("static/map", gs, map_static(
                self.region_layer, self.spawn,
                resource_nodes=resource_nodes_from_state(gs.resources))))
        out.append(self._env("static/catalog", gs, catalog_static(self.catalog)))
        out.append(self._env("static/schema", gs, schema_static()))
        if self.manifest is not None and self.assembly is not None:
            out.append(self._env("static/strategy", gs,
                                 strategy_static(self.manifest, self.assembly)))
        out.append(self._env("frame/session", gs, adapt.session_frame(
            session_state, frame_source=self.frame_source, game_time=gs.game_time,
            map_name=getattr(self.region_layer, "map_name", None),
            my_race=self.my_race, enemy_race=self.enemy_race)))
        return out

    # ---- 每帧 ----

    def on_game_state(self, gs: GameState) -> list[dict]:
        """产出本帧的信封。**只读**各引擎的 snapshot，不驱动它们。"""
        out: list[dict] = []
        flow_snap = self.engine.snapshot() if self.engine is not None else None
        group_of = adapt.group_of_from_flow(flow_snap) if flow_snap else None

        # 栅格「仅变化时下发」（契约里 grids 字段承诺的语义）：按内容指纹比对，
        # 变了才带在本帧里，没变带 None —— 前端 store 对 world 帧保留上一份 grids。
        # 旧实现是"只第一帧发"：菌毯蔓延/视野移动整局都读第一帧的陈旧值。
        grids = None
        if self.include_grids:
            candidate = adapt.grids_of(gs)
            fp = (candidate.creep.data_b64 if candidate.creep is not None else None,
                  candidate.visibility.data_b64 if candidate.visibility is not None else None)
            if fp != self._grids_fp:
                self._grids_fp = fp
                grids = candidate
        out.append(self._env("frame/world", gs, adapt.world_frame(
            gs, self.catalog, group_of=group_of, grids=grids)))

        if flow_snap is not None:
            out.append(self._env("frame/flow", gs, adapt.flow_frame(flow_snap, gs)))

        prod_snap = None
        if self.runtime is not None:
            prod_snap = self.runtime.snapshot()
            out.append(self._env("frame/production", gs, adapt.with_waited(
                adapt.production_frame(prod_snap, self.catalog), gs.game_time)))

        if self.keeper is not None:
            out.append(self._env("frame/economy", gs,
                                 adapt.economy_frame(self.keeper.snapshot(gs))))

        curve = None
        if self.planner is not None and gs.game_time - self._proj_at >= self.projection_every:
            proj = self._project(gs)
            if proj is not None:
                self._proj_at = gs.game_time
                curve, frame = proj
                out.append(self._env("frame/projection", gs, frame))

        assert self.alerts is not None
        fired = self.alerts.evaluate(gs, production=prod_snap, curve=curve)
        if fired:
            out.append(self._env("frame/alerts", gs, AlertsFrame(alerts=fired)))

        if self.proposals is not None:
            rows = self.proposals.list()
            # 指纹 = id+状态：提案的展示内容只在这两者变化时需要重推
            fp = "|".join(f"{r['id']}:{r['status']}" for r in rows)
            if fp != self._proposals_fingerprint:
                self._proposals_fingerprint = fp
                out.append(self._env("proposals", gs, ProposalsFrame(proposals=rows)))

        if self.ring is not None and gs.game_time - self._ops_at >= self.ops_every:
            self._ops_at = gs.game_time
            out.append(self._env("frame/ops", gs, adapt.ops_frame(self.ring)))

        return out

    def _project(self, gs: GameState):
        """投影当前生产队列；参考计划只在**本会话还没见过 live 队列**时用（开局展示）。

        中途回退参考计划 = 把一整份没提过的开局顺序画成投影（§0.51 事故：只提了
        1 个农民，队列为空的瞬间泳道图炸出 20+ 农民条目）。见过 live 队列后队列
        空了就投**空队列**：纯收入外推 + 世界里在建/在训的项（`derive_from` 从
        build_progress 派生）照常落成 —— 在途可见性由世界帧给，不靠参考计划。
        """
        queue = None
        if self.runtime is not None:
            queue = self.runtime.queue(self.projection_queue)
        if queue is not None and queue.items:
            self._live_seen = True
        if queue is not None and (queue.items or self._live_seen):
            curve, translated = project_queue(
                self.planner, gs, list(queue.items),
                until=gs.game_time + self.horizon, catalog=self.catalog)
            return curve, adapt.projection_frame(
                curve, based_on_seq=gs.seq, based_on_game_time=gs.game_time,
                horizon=self.horizon, queue_name=queue.name,
                skipped=translated.skipped)
        if self.projection_plan is None:
            return None
        curve = self.planner.project(gs, self.projection_plan, until=gs.game_time + self.horizon)
        return curve, adapt.projection_frame(
            curve, based_on_seq=gs.seq, based_on_game_time=gs.game_time,
            horizon=self.horizon, plan_id=_plan_id(self.projection_plan))

    def session(self, gs: GameState, state: str, *, error: str | None = None) -> dict:
        """单独产一条会话帧（状态变化时用）。"""
        return self._env("frame/session", gs, adapt.session_frame(
            state, frame_source=self.frame_source, game_time=gs.game_time,
            map_name=getattr(self.region_layer, "map_name", None),
            my_race=self.my_race, enemy_race=self.enemy_race, error=error))

    # ---- 内部 ----

    def _env(self, topic: str, gs: GameState, payload) -> dict:
        """信封的 `seq` = **`GameState.seq`**（契约 §2.1 原文），不是自增计数器。

        这不是风格问题：`seq` 是"世界的版本号"，命令的新鲜度门（R8）就靠
        `session.seq - based_on_seq` 判断。用自增计数器的话，观察包给出的 seq 会远大于
        世界 seq，差值恒为负 —— **R8 的门对 agent 就永久失效了**（真机上表现为
        "agent 拿几分钟前的观察下命令也照样被接受"）。

        代价是同一 tick 里多个 topic 共享同一个 seq。这没问题：帧内顺序由**流的顺序**给
        （JSONL 的行序 / WS 的发送序），不靠 seq 排。
        """
        return envelope(topic, seq=gs.seq, game_time=gs.game_time, payload=payload,
                        wall_ms=int(self.clock() * 1000))


def _plan_id(plan: list) -> str:
    refs = [getattr(p, "module_ref", None) for p in plan]
    return "+".join(r for r in refs if r) or "draft"