"""view.producer：帧生产器 —— 引擎读模型 → 一组信封。

**这是唯一的产帧路径**。live 推送（B3）、复盘录制（B5 的 ViewRecorder）、离线夹具
（`tools/make_fixtures.py`）都走它。之所以要"唯一"：夹具和 live 各写一套产帧逻辑，
两边迟早不一致 —— 而不一致的表现是"复盘看着对、live 看着不对"，最难查的那种。

节拍对齐 `game_time`（ADR-0025 §6）：1s 观察 / 3s 投影 / 事件驱动警报。
不用墙钟定时器 —— 复盘要能按游戏时间重放。
"""
from __future__ import annotations

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

    _seq: int = 0
    _proj_at: float = field(default=-1e18)
    _ops_at: float = field(default=-1e18)
    _grids_sent: bool = False
    _proposals_fingerprint: str = ""

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

        # 栅格只在第一帧发一次（仅变化时下发；真实现要 diff，这里先保守）
        grids = self.include_grids and not self._grids_sent
        if grids:
            self._grids_sent = True
        out.append(self._env("frame/world", gs, adapt.world_frame(
            gs, self.catalog, group_of=group_of, include_grids=grids)))

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
        """投影当前生产队列；队列为空才退回"参考计划"。

        以前只能投"参考计划"（planner 与运行时的 authoring 面没有互转）。
        `view.projection` 把 `QueueItem` 翻成 planner 的 op 后，
        `source.kind="live_queue"` 才有真值 —— 概览页的"实际 vs 预测"从此不是骗人的。
        """
        queue = None
        if self.runtime is not None:
            queue = self.runtime.queue(self.projection_queue)
        if queue is not None and queue.items:
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
        self._seq += 1
        return envelope(topic, seq=self._seq, game_time=gs.game_time, payload=payload,
                        wall_ms=1_700_000_000_000 + int(gs.game_time * 1000))


def _plan_id(plan: list) -> str:
    refs = [getattr(p, "module_ref", None) for p in plan]
    return "+".join(r for r in refs if r) or "draft"