"""game.ports：driver ↔ engine 边界契约（原顶层 ports 模块并入 game，见 docs/模块审查.md）。

- GamePort：driver 实现的端口（start/stop/submit_operations/events/read）。
- RuntimeSink：engine/runtime 实现的回调（driver 每 step 推 RawGameState）。
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
class ApplyResult:
    """submit_operations 返回。具体字段 D6 待定。"""

    ok: bool
    failed_op_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class GameEvent:
    """driver 推送的事件（目录 D7 待定）。"""

    kind: str
    payload: dict


@dataclass(slots=True)
class SessionEvent:
    kind: str
    payload: dict


class RuntimeSink(Protocol):
    """engine/runtime 实现；driver 每 step 回调。"""

    def on_game_state(self, raw: RawGameState) -> None: ...
    def on_session_event(self, event: SessionEvent) -> None: ...


class GamePort(Protocol):
    """driver 实现的端口。"""

    def start(self, request_id: str) -> None: ...
    def stop(self) -> None: ...
    def submit_operations(self, ops: list[Operation]) -> ApplyResult: ...
    def events(self, cursor: int) -> list[GameEvent]: ...
    def read(self) -> RawGameState | None: ...
