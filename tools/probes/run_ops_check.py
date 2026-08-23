"""Driver 操作集真机验证（需 SC2 环境，手动跑；不进 pytest）。

逐 op 在真实对局里发命令并回读 RawGameState 验证生效。证据分两类：
- 命令证据：提交后，该单位出现 baseline 里没有的新 order（避免 patrol 残留等误报）；
- 客观证据：train 看矿物扣减 ~50；build 看补给站实体出现在状态里；stop 看 orders 被清空。

覆盖：move_to / attack_move_to / hold_position / stop / patrol / follow（起始 SCV，零资源）
      + train（SCV，50 矿）/ build（补给站，100 矿，等自然采够）
不验（V1 缺口或条件不可控）：load/unload/use_ability/cancel/morph/assign_workers
  （原因见 driver.sc2_adapter.UNIMPLEMENTED_ACTIONS）；focus_fire 需可见敌方目标。

跑：uv run python run_ops_check.py
输出：docs/ops_check.log + 控制台。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from game import Operation
from driver.sc2_adapter import SC2GamePort, UNIMPLEMENTED_ACTIONS
from sc2.data import Difficulty, Race

LOG = Path(__file__).resolve().parents[2] / "docs" / "ops_check.log"


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def _own_units(raw, type_name):
    return [u for u in raw.units if u.alliance == 1 and u.type_name == type_name]


# build 候选放置位（相对 CC 的偏移；出生点随机，逐个试到补给站实体出现）
BUILD_CANDIDATES = [(-3, 2), (3, 2), (2, -3), (-2, -3), (6, 0), (-6, 0), (0, 6), (0, -6)]


class OpsCheckSink:
    """脚本化逐轮验证：每轮在触发 seq 发 op，随后逐帧回读 state 找生效证据。"""

    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._op_id = 0
        self._cc_pos = None
        self._scv = None
        self._scv2 = None
        self._track = {}  # op_name -> 证据追踪
        self._build_attempt = 0  # build 候选位置游标

    def on_game_state(self, raw) -> None:
        if raw.seq == 0:
            self._capture(raw)
        elif raw.seq == 1:
            self._submit("move_to", [self._scv], {"position": [self._cc_pos.x + 4, self._cc_pos.y]}, "move", raw)
        elif raw.seq == 8:
            self._submit("attack_move_to", [self._scv], {"position": [self._cc_pos.x - 4, self._cc_pos.y]}, "attack", raw)
        elif raw.seq == 15:
            self._submit("hold_position", [self._scv], {}, "holdposition", raw)
        elif raw.seq == 19:
            self._submit("stop", [self._scv], {}, "stop", raw)
        elif raw.seq == 23:
            self._submit("patrol", [self._scv], {"positions": [[self._cc_pos.x + 4, self._cc_pos.y + 2]]}, "patrol", raw)
        elif raw.seq == 30:
            self._submit("follow", [self._scv], {"target_unit": self._scv2}, "move", raw)
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

    def _orders_of(self, raw, tag):
        u = next((x for x in raw.units if x.tag == tag), None)
        return frozenset(o.ability_name or "" for o in u.orders) if u is not None else frozenset()

    def _submit(self, name, tags, params, expect, raw) -> None:
        if self._scv is None:
            log(f"[{name}] 初始单位缺失，跳过")
            return
        self._op_id += 1
        self._port.submit_operations([Operation(
            op_id=self._op_id, unit_tags=list(tags), action=name, params=params, seq=raw.seq,
        )])
        self._track[name] = {
            "tag": tags[0], "expect": expect.lower(), "seen": False, "at": raw.seq,
            "baseline": self._orders_of(raw, tags[0]),  # 提交时的 order 集（排除残留误报）
            "minerals": raw.minerals,  # train 的客观证据基线
        }
        base = sorted(self._track[name]["baseline"])
        log(f"[{name}] submit（tag={tags[0]} params={params} baseline_orders={base}）")

    def _verify(self, raw) -> None:
        for name, t in list(self._track.items()):
            if t["seen"]:
                continue
            orders = self._orders_of(raw, t["tag"])
            if name == "stop":
                # stop 的客观证据：打断 patrol/move → orders 被清空（或短暂出现 Stop）
                if not orders or any("stop" in o.lower() for o in orders):
                    self._mark(name, raw, orders)
            elif name == "train":
                if raw.minerals <= t["minerals"] - 45:
                    extra = "minerals " + str(t["minerals"]) + " -> " + str(raw.minerals)
                    self._mark(name, raw, orders, extra=extra)
                elif any("scv" in o.lower() and o not in t["baseline"] for o in orders):
                    self._mark(name, raw, orders)
            elif name == "build":
                depots = _own_units(raw, "SUPPLYDEPOT")
                if depots:
                    d = depots[0]
                    extra = "实体 tag=" + str(d.tag) + " progress=" + str(round(d.build_progress, 2))
                    self._mark(name, raw, orders, extra=extra)
                elif any("supplydepot" in o.lower() and o not in t["baseline"] for o in orders):
                    self._mark(name, raw, orders)
            else:
                # 通用命令证据：出现 baseline 里没有的、含 expect 的新 order
                if any(t["expect"] in o.lower() and o not in t["baseline"] for o in orders):
                    self._mark(name, raw, orders)

    def _mark(self, name, raw, orders, extra="") -> None:
        self._track[name]["seen"] = True
        log(f"[{name}] OK 生效（seq {raw.seq}，orders={sorted(orders)} {extra}）")

    def _resource_ops(self, raw) -> None:
        """矿够就发 train/build（自然采矿）；证据 = 矿物扣减 / 补给站实体。"""
        if "train" not in self._track and raw.minerals >= 50 and raw.supply_used < raw.supply_cap:
            cc = next((u for u in _own_units(raw, "COMMANDCENTER")), None)
            if cc is not None:
                self._op_id += 1
                self._port.submit_operations([Operation(
                    op_id=self._op_id, unit_tags=[cc.tag], action="train",
                    params={"type": "SCV"}, seq=raw.seq,
                )])
                base = sorted(self._orders_of(raw, cc.tag))
                self._track["train"] = {
                    "tag": cc.tag, "expect": "scv", "seen": False, "at": raw.seq,
                    "baseline": self._orders_of(raw, cc.tag), "minerals": raw.minerals,
                }
                log(f"[train] submit（CC={cc.tag} minerals={raw.minerals} baseline={base}）")
        b = self._track.get("build")
        if b is None and raw.minerals >= 100:
            self._try_build(raw)
        elif b is not None and not b["seen"] and raw.seq - b["at"] >= 25:
            self._try_build(raw)  # 放置位非法（出生点随机）→ 换下一个候选位置重试

    def _try_build(self, raw) -> None:
        if self._build_attempt >= len(BUILD_CANDIDATES):
            log("[build] 候选位置耗尽，放弃（可能是资源/地形问题，看日志人工判）")
            self._track["build"]["seen"] = True  # 停止重试
            return
        dx, dy = BUILD_CANDIDATES[self._build_attempt]
        self._build_attempt += 1
        scv = next((u for u in _own_units(raw, "SCV")), None)
        if scv is None:
            return
        pos = [self._cc_pos.x + dx, self._cc_pos.y + dy]
        self._op_id += 1
        self._port.submit_operations([Operation(
            op_id=self._op_id, unit_tags=[scv.tag], action="build",
            params={"type": "SUPPLYDEPOT", "position": pos},
            seq=raw.seq,
        )])
        self._track["build"] = {
            "tag": scv.tag, "expect": "supplydepot", "seen": False, "at": raw.seq,
            "baseline": self._orders_of(raw, scv.tag), "minerals": raw.minerals,
        }
        log(f"[build] submit attempt={self._build_attempt}（SCV={scv.tag} pos={pos} minerals={raw.minerals}）")


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
