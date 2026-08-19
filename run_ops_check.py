"""Driver 操作集真机验证（需 SC2 环境，手动跑；不进 pytest）。

逐 op 在真实对局里发命令并回读 RawGameState 验证生效（命令出现在 orders / 状态反映）：
- 零资源：move_to / attack_move_to / hold_position / stop / patrol / follow（用起始 SCV）
- 需资源：train（SCV，50 矿）/ build（补给站，100 矿）——等自然采够后发
- 本脚本不验（V1 缺口或条件不可控）：load/unload/use_ability/cancel/morph/assign_workers
  （原因见 driver.sc2_adapter.UNIMPLEMENTED_ACTIONS）；focus_fire 需可见敌方目标，真机条件
  不可控，单测覆盖（tests/driver/test_translate.py）

跑：uv run python run_ops_check.py
输出：docs/ops_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from loguru import logger

from game import Operation
from driver.sc2_adapter import SC2GamePort, UNIMPLEMENTED_ACTIONS
from sc2.data import Difficulty, Race

LOG = Path(__file__).parent / "docs" / "ops_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def _own_units(raw, type_name):
    return [u for u in raw.units if u.alliance == 1 and u.type_name == type_name]


class OpsCheckSink:
    """脚本化逐轮验证：每轮 = 在触发 seq 发 op，随后若干帧回读 orders 验证生效。"""

    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._op_id = 0
        self._cc_pos = None
        self._scv = None
        self._scv2 = None
        self._track = {}  # op_name -> {tag, expect, submitted_seq, done}

    def on_game_state(self, raw) -> None:
        if raw.seq == 0:
            self._capture(raw)
        elif raw.seq == 1:
            self._submit("move_to", [self._scv], {"position": [self._cc_pos.x + 4, self._cc_pos.y]}, "Move", raw.seq)
        elif raw.seq == 8:
            self._submit("attack_move_to", [self._scv], {"position": [self._cc_pos.x - 4, self._cc_pos.y]}, "Attack", raw.seq)
        elif raw.seq == 15:
            self._submit("hold_position", [self._scv], {}, "HoldPosition", raw.seq)
        elif raw.seq == 19:
            self._submit("stop", [self._scv], {}, "Stop", raw.seq)
        elif raw.seq == 23:
            self._submit("patrol", [self._scv], {"positions": [[self._cc_pos.x + 4, self._cc_pos.y + 2]]}, "Patrol", raw.seq)
        elif raw.seq == 30:
            self._submit("follow", [self._scv], {"target_unit": self._scv2}, "Move", raw.seq)
        elif raw.seq >= 40:
            self._resource_ops(raw)
        self._verify(raw)

    def on_session_event(self, event) -> None:
        pass

    # ---- 轮次实现 ----

    def _capture(self, raw) -> None:
        log("=== ops check start ===")
        log(f"本脚本不验（V1 缺口/条件不可控）：{sorted(UNIMPLEMENTED_ACTIONS)} + focus_fire")
        ccs = _own_units(raw, "COMMANDCENTER")
        scvs = _own_units(raw, "SCV")
        if not ccs or len(scvs) < 2:
            log("[capture] 缺 CC/SCV，无法验证（需要的初始单位不存在）")
            return
        self._cc_pos = ccs[0].position
        self._scv = scvs[0].tag
        self._scv2 = scvs[1].tag
        log(f"[capture] CC={ccs[0].tag} pos={self._cc_pos} SCV={self._scv} SCV2={self._scv2}")

    def _submit(self, name, tags, params, expect, seq) -> None:
        if self._scv is None:
            log(f"[{name}] 初始单位缺失，跳过")
            return
        self._op_id += 1
        self._port.submit_operations([Operation(
            op_id=self._op_id, unit_tags=list(tags), action=name, params=params, seq=seq,
        )])
        self._track[name] = {"tag": tags[0], "expect": expect.lower(), "seen": False, "at": seq}
        log(f"[{name}] submit（tag={tags[0]} params={params}，期望 orders 含 {expect}）")

    def _verify(self, raw) -> None:
        for name, t in list(self._track.items()):
            if t["seen"]:
                continue
            u = next((x for x in raw.units if x.tag == t["tag"]), None)
            if u is None:
                continue
            orders = [o.ability_name or "" for o in u.orders]
            if any(t["expect"] in o.lower() for o in orders):
                t["seen"] = True
                log(f"[{name}] ✓ 生效（seq {raw.seq}，orders={orders}）")

    def _resource_ops(self, raw) -> None:
        """矿够就发 train/build（自然采矿）；验证订单出现 + 资源扣减。"""
        if "train" not in self._track and raw.minerals >= 50:
            cc = next((u for u in _own_units(raw, "COMMANDCENTER")), None)
            if cc is not None:
                self._op_id += 1
                self._port.submit_operations([Operation(
                    op_id=self._op_id, unit_tags=[cc.tag], action="train",
                    params={"type": "SCV"}, seq=raw.seq,
                )])
                self._track["train"] = {"tag": cc.tag, "expect": "scv", "seen": False, "at": raw.seq}
                log(f"[train] submit（CC={cc.tag} minerals={raw.minerals}）")
        if "build" not in self._track and raw.minerals >= 100 and self._scv:
            self._op_id += 1
            self._port.submit_operations([Operation(
                op_id=self._op_id, unit_tags=[self._scv], action="build",
                params={"type": "SUPPLYDEPOT", "position": [self._cc_pos.x + 6, self._cc_pos.y]},
                seq=raw.seq,
            )])
            self._track["build"] = {"tag": self._scv, "expect": "supplydepot", "seen": False, "at": raw.seq}
            log(f"[build] submit（SCV={self._scv} minerals={raw.minerals}）")
        # build 的额外证据：补给站实体出现在状态里（在建 build_progress < 1）
        if "build" in self._track and not self._track["build"]["seen"]:
            depots = _own_units(raw, "SUPPLYDEPOT")
            if depots:
                self._track["build"]["seen"] = True
                d = depots[0]
                log(f"[build] ✓ 补给站实体出现（seq {raw.seq}，tag={d.tag} build_progress={d.build_progress:.2f}）")

def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.Easy,
        sink=None, game_time_limit=90, realtime=False,
    )
    sink = OpsCheckSink(port)
    port.set_sink(sink)
    port.start("ops-check-1")
    seen = [k for k, v in sink._track.items() if v["seen"]]
    missing = [k for k, v in sink._track.items() if not v["seen"]]
    log(f"=== ops check done === 生效: {seen} / 未观察到: {missing}")


if __name__ == "__main__":
    main()

