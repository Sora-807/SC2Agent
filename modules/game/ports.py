"""game.ports：driver ↔ engine 边界契约（原顶层 ports 模块并入 game.ports，残留由 tests/architecture 锁死）。

- GamePort：driver 实现的端口（start/stop/submit_operations/events/read）。
- RuntimeSink：engine/runtime 实现的回调（driver 每 step 推 RawGameState）。
- WorkerPoolPort：工兵所有权查询/征用（flow.Allocator 实现；生产与经济维持器消费，ADR-0030 D3.4
  —— production 不许 import flow，所以走端口）。
- ApplyResult：submit_operations 的返回。
- GameEvent/SessionEvent：driver 推送的事件（目录 D7 待定）。
engine 侧可 duck-typing（不 import 本模块）；driver 显式实现这两个协议。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from game.operation import Operation
from game.raw import RawGameState


@dataclass(slots=True)
class OpApply:
    """单条 op 的应用结果（D6 落地）。`ok=None` = 尚未裁决（异步应用未回）。"""

    op_id: int
    ok: bool | None
    reason: str | None = None


@dataclass(slots=True)
class ApplyResult:
    """submit_operations 返回。

    `results` 是 D6 的正式逐 op 通道；`failed_op_ids` 是旧字段的兼容保留。
    应用是**异步**的（下一 step 生效），所以同步返回时结果可能还是 `ok=None`
    （"已受理，待裁决"）—— 这如实反映"Operation 下一 step 生效"的语义。
    """

    ok: bool
    failed_op_ids: list[int] = field(default_factory=list)
    results: list[OpApply] = field(default_factory=list)


#: GameEvent 目录（D7 落地）。**只增不改**：新增条目走这里登记，不在代码里散写字符串。
GAME_EVENTS: dict[str, str] = {
    "session_started": "游戏进程启动、bot 就绪",
    "session_ended": "游戏结束（正常到时 / 对局终止）",
    "op_dispatched": "op 已翻译并下发（unit 命令发出）",
    "op_apply_failed": "op 翻译/下发失败（含原因，进 ApplyResult.results）",
    "build_confirmed": "建造实体出现（driver 侧观察到 footprint 实体）",
    "build_timeout": "在途建造确认超时（SC2 静默拒单的一种表现）",
    "user_takeover": "疑似用户接管（auto-order 白名单之外的命令出现在单位上）",
    "unit_died": "己方单位死亡（tag + 类型）",
}


@dataclass(slots=True)
class GameEvent:
    """driver 推送的事件。`kind` 必须登记在 `GAME_EVENTS` 目录里。"""

    kind: str
    payload: dict
    game_time: float = 0.0


@dataclass(slots=True)
class SessionEvent:
    kind: str
    payload: dict


class RuntimeSink(Protocol):
    """engine/runtime 实现；driver 每 step 回调。"""

    def on_game_state(self, raw: RawGameState) -> None: ...
    def on_session_event(self, event: SessionEvent) -> None: ...


class WorkerPoolPort(Protocol):
    """工兵所有权端口（ADR-0030 D3）：一张 lease 表，三方（战斗组 / 生产建造 / 经济维持器）都从它取人。

    实现方 = flow.Allocator（它已经是 group lease 的权威）；消费方 = production（不许 import flow）。
    - unleased_workers：没被任何组租用、也没被用户接管的工兵 → 经济维持器的领地。
    - reserve/release：建造这类"短期独占"的征用（owner 如 "production/build#3"）。
    """

    def unleased_workers(self, gs) -> list[int]: ...
    def reserve(self, owner: str, tag: int) -> bool: ...
    def release(self, owner: str) -> None: ...
    def reserved_tags(self) -> frozenset[int]: ...


class GamePort(Protocol):
    """driver 实现的端口。"""

    def start(self, request_id: str) -> None: ...
    def stop(self) -> None: ...
    def submit_operations(self, ops: list[Operation]) -> ApplyResult: ...
    def events(self, cursor: int) -> list[GameEvent]: ...
    def read(self) -> RawGameState | None: ...
