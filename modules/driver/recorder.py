"""StateRecorder：RuntimeSink，每 game_time 整秒边界把 RawGameState 存成 JSONL snapshot。

用于测试/发现：realtime 下人手动操作 → recorder 存 state trace → 离线分析
（如建筑放置后 build_progress 演化、SCV orders）。grid 只存 dims 不存 data（省体积）。
"""
from __future__ import annotations

import json
from pathlib import Path

from game import RawGameState, RawUnit


def _unit_dict(u: RawUnit) -> dict:
    return {
        "tag": u.tag,
        "type_name": u.type_name,
        "position": [u.position.x, u.position.y],
        "alliance": u.alliance,
        "health": u.health,
        "health_max": u.health_max,
        "shield": u.shield,
        "energy": u.energy,
        "build_progress": u.build_progress,
        "orders": [
            {
                "ability": o.ability_name,
                "target_tag": o.target_tag,
                "target_pos": [o.target_pos.x, o.target_pos.y] if o.target_pos else None,
            }
            for o in u.orders
        ],
        "facing": u.facing,
        "buffs": list(u.buffs),
        "is_carrying_minerals": u.is_carrying_minerals,
        "is_carrying_vespene": u.is_carrying_vespene,
    }


class StateRecorder:
    """每 interval_sec 游戏秒存一份 snapshot（含 t=0 初始）。"""

    def __init__(self, trace_path, interval_sec: float = 1.0) -> None:
        self._path = Path(trace_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.unlink(missing_ok=True)
        self._interval = interval_sec
        self._next_save = 0.0
        self.count = 0

    def on_game_state(self, raw: RawGameState) -> None:
        while raw.game_time >= self._next_save:
            self._save(raw)
            self._next_save += self._interval

    def on_session_event(self, event) -> None:
        pass

    def _save(self, raw: RawGameState) -> None:
        snap = {
            "seq": raw.seq,
            "game_time": round(raw.game_time, 3),
            "minerals": raw.minerals,
            "vespene": raw.vespene,
            "supply_used": raw.supply_used,
            "supply_cap": raw.supply_cap,
            "map_size": list(raw.map_size),
            "creep_dims": [raw.creep.width, raw.creep.height],
            "visibility_dims": [raw.visibility.width, raw.visibility.height],
            "units": [_unit_dict(u) for u in raw.units],
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap, default=str) + "\n")
        self.count += 1
