"""view.recorder：ViewRecorder —— 把帧录成 JSONL（B5）。

与 `driver.StateRecorder` 的关系：**并存，不替换**。
- `StateRecorder` 录 `RawGameState`（driver 层的原始快照，字段是 SC2 原生形态）；
- `ViewRecorder` 录 **ViewFrame**（契约形态），所以录下来的东西前端可以直接播。

这就是"复盘照抄 live"能反过来变成"live 照抄复盘"的物理基础（决策 U2）：
live 推的帧和录下来的帧是**同一批字节**，复盘不是另做一套，而是换一个帧源。

ADR-0024 的完整事件溯源（flow_events + 版本树 + seek 重放）不在这里 ——
这只做"快照帧 + 时间线标记"的最小版；`snapshot_every` 标出的点就是时间线上可跳的锚。
"""
from __future__ import annotations

import json
from pathlib import Path

from game.state import GameState

from view.producer import FrameProducer

#: 多少游戏秒标一个"快照点"（ADR-0024 §6 的默认值）。时间线上显示为可跳锚点。
SNAPSHOT_EVERY = 5.0


class ViewRecorder:
    """RuntimeSink 形态：`on_game_state(gs)` 就录一帧。

    写盘是**追加**且每帧 flush，所以对局中途崩了，已录的部分仍然可播
    （真机崩溃是常态，S7 就是为它写的）。
    """

    def __init__(self, path, producer: FrameProducer, *,
                 snapshot_every: float = SNAPSHOT_EVERY) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("w", encoding="utf-8", newline="\n")
        self._producer = producer
        self._snapshot_every = snapshot_every
        self._next_snapshot = -1e18
        self.count = 0
        self.snapshots: list[float] = []
        self._statics_done = False

    # ---- RuntimeSink ----

    def on_game_state(self, gs: GameState) -> None:
        if not self._statics_done:
            self._statics_done = True
            self._write(self._producer.statics(gs))
        self._write(self._producer.on_game_state(gs))
        if gs.game_time >= self._next_snapshot:
            self._next_snapshot = gs.game_time + self._snapshot_every
            self.snapshots.append(round(gs.game_time, 3))

    def on_session_event(self, event) -> None:
        """会话事件目录（D7）还没定 → 不猜字段，什么都不录（不静默造数据）。"""

    # ---- 生命周期 ----

    def session(self, gs: GameState, state: str, *, error: str | None = None) -> None:
        self._write([self._producer.session(gs, state, error=error)])

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> ViewRecorder:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._path

    def _write(self, frames: list[dict]) -> None:
        for frame in frames:
            self._fh.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
            self._fh.write("\n")
            self.count += 1
        self._fh.flush()   # 崩了也要能播已录的部分
