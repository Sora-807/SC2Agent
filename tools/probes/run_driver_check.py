"""Driver 集成 check：真跑 SC2GamePort。
Round 1（step 0）：抽取 RawGameState，验形态对不对。
Round 2（step 1 发 move，step 2-20 跟踪）：op 能否执行 + state 能否反映生效。
跑：uv run python run_driver_check.py
输出：docs/driver_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from game import Operation
from driver.sc2_adapter import SC2GamePort
from sc2.data import Difficulty, Race

LOG = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "driver_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


class CheckSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._move_tag: int | None = None
        self._move_target = None
        self._cleared: int | None = None

    def on_game_state(self, raw) -> None:
        if raw.seq == 0:
            self._round1(raw)
        if raw.seq == 1:
            self._round2_submit(raw)
        if 2 <= raw.seq <= 20:
            self._round2_track(raw)

    def on_session_event(self, event) -> None:
        pass

    def _round1(self, raw) -> None:
        log("=== ROUND 1: RawGameState 形态（step 0）===")
        own = [u for u in raw.units if u.alliance == 1]
        enemy = [u for u in raw.units if u.alliance == 3]
        neutral = [u for u in raw.units if u.alliance == 4]
        log(f"counts: all={len(raw.units)} own={len(own)} enemy={len(enemy)} neutral={len(neutral)}")
        sample = next((u for u in raw.units if u.alliance != 1), None)
        if sample:
            log(f"sample non-own: type={sample.type_name} alliance={sample.alliance} pos={sample.position}")
        cc = next((u for u in raw.units if u.type_name == "COMMANDCENTER"), None)
        scv = next((u for u in raw.units if u.type_name == "SCV" and u.alliance == 1), None)
        if cc:
            log(f"CC: tag={cc.tag} pos={cc.position} health={cc.health}/{cc.health_max} build_progress={cc.build_progress} orders={[o.ability_name for o in cc.orders]}")
        if scv:
            log(f"SCV: tag={scv.tag} pos={scv.position} health={scv.health}/{scv.health_max} alliance={scv.alliance} orders={[(o.ability_name, o.target_pos) for o in scv.orders]} facing={scv.facing} buffs={scv.buffs}")
        log(f"resources: minerals={raw.minerals} vespene={raw.vespene} supply={raw.supply_used}/{raw.supply_cap}")
        log(f"map_size={raw.map_size} creep={raw.creep.width}x{raw.creep.height} visibility={raw.visibility.width}x{raw.visibility.height}")
        log(f"creep.data len={len(raw.creep.data)} visibility.data len={len(raw.visibility.data)}")
        if cc:
            log(f"origin sanity: CC pos {cc.position} on map {raw.map_size}（左下原点→小坐标=左下）")

    def _round2_submit(self, raw) -> None:
        scv = next((u for u in raw.units if u.type_name == "SCV" and u.alliance == 1), None)
        cc = next((u for u in raw.units if u.type_name == "COMMANDCENTER"), None)
        if not scv or not cc:
            log("[round2] no scv/cc to move")
            return
        self._move_tag = scv.tag
        self._move_target = cc.position
        self._port.submit_operations([Operation(
            op_id=1, unit_tags=[scv.tag], action="move_to",
            params={"position": cc.position}, seq=raw.seq,
        )])
        log(f"=== ROUND 2 submit (step {raw.seq}): move SCV {scv.tag} from {scv.position} -> {cc.position} ===")

    def _round2_track(self, raw) -> None:
        u = next((u for u in raw.units if u.tag == self._move_tag), None) if self._move_tag else None
        if u is None:
            if self._cleared is None:
                self._cleared = raw.seq
                log(f"[round2 track] step {raw.seq} (t={raw.game_time:.2f}): SCV {self._move_tag} not found")
            return
        abilities = [o.ability_name for o in u.orders]
        if abilities:
            log(f"[round2 track] step {raw.seq} (t={raw.game_time:.2f}): orders PERSIST {abilities} pos={u.position} (toward {self._move_target})")
        else:
            if self._cleared is None:
                self._cleared = raw.seq
                log(f"[round2 track] step {raw.seq} (t={raw.game_time:.2f}): orders CLEARED pos={u.position} target={self._move_target} → op 生效且 state 反映 ✓")


def main() -> None:
    LOG.unlink(missing_ok=True)
    log("=== driver check start ===")
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=30,
    )
    sink = CheckSink(port)
    port.set_sink(sink)
    port.start("check-1")
    log("=== driver check done ===")


if __name__ == "__main__":
    main()
