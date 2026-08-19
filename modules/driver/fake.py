"""FakeGamePort：给 engine/world/flow 测试用，不连 SC2。

喂脚本化 RawGameState 序列；start() 逐个回调 sink.on_game_state；
submit_operations 捕获到 self.submitted 供测试断言。
"""
from __future__ import annotations

from game import ApplyResult, GameEvent, GamePort, Operation, RawGameState, RuntimeSink


class FakeGamePort:
    """结构式实现 GamePort，不连 SC2。"""

    def __init__(self, script: list[RawGameState], sink: RuntimeSink | None = None) -> None:
        self._script = list(script)
        self._sink = sink
        self.submitted: list[Operation] = []
        self._events: list[GameEvent] = []
        self._stopped = False

    def set_sink(self, sink: RuntimeSink) -> None:
        self._sink = sink

    def start(self, request_id: str) -> None:
        self._stopped = False
        for raw in self._script:
            if self._stopped:
                break
            if self._sink is not None:
                self._sink.on_game_state(raw)

    def stop(self) -> None:
        self._stopped = True

    def submit_operations(self, ops: list[Operation]) -> ApplyResult:
        self.submitted.extend(ops)
        return ApplyResult(ok=True)

    def events(self, cursor: int) -> list[GameEvent]:
        return list(self._events)

    def read(self) -> RawGameState:
        return self._script[-1]
