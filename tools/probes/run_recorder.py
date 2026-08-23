"""State recorder runner：开 SC2（默认 realtime 你手动玩），每秒存 state trace。

用法：
  uv run python run_recorder.py            # realtime=True, limit=120（你玩）
  uv run python run_recorder.py 5 false    # limit=5, realtime=False（快速 smoke）
输出：docs/state_trace.jsonl（每行一个 snapshot）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from driver.recorder import StateRecorder
from driver.sc2_adapter import SC2GamePort
from sc2.data import Difficulty, Race

TRACE = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "evidence" / "state_trace.jsonl"


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    realtime = (sys.argv[2].lower() != "false") if len(sys.argv) > 2 else True
    rec = StateRecorder(TRACE, interval_sec=1.0)
    logger.info(f"=== recorder start (realtime={realtime}, limit={limit}, trace={TRACE}) ===")
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=rec, game_time_limit=limit, realtime=realtime,
    )
    port.start("rec-1")
    logger.info(f"=== recorder done: {rec.count} snapshots → {TRACE} ===")


if __name__ == "__main__":
    main()
