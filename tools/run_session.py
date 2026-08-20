"""tools/run_session.py：**会话子进程**（B3 的进程分离）。

为什么必须分进程：`SC2GamePort.start()` 阻塞在 burnysc2 的 `run_game()` 里，
它自带 asyncio 事件循环 —— 塞进 api 进程会和 uvicorn 的循环打架。
分开之后还顺带拿到两件事（S7 想要的）：**游戏崩了不带走 api**、
**没开游戏时 UI 照样活着**。

协议刻意简单到不用任何依赖：**stdin 收命令、stdout 出帧**，都是一行一条 JSON。
- 出：ViewFrame 信封（与 WS/录制**同一批字节**）+ `{"_": "meta"|"error"|"bye"}` 控制行。
- 入：`{"op": "queue"|"workers"|"stop", ...}`。命令在**帧边界**应用 ——
  帧中间改状态会让"这一帧的观察对应哪个世界"说不清（R8 的前提就没了）。

`--driver sim` 用 `tools/worldsim.py` 驱动，与 `sc2` **走完全同一条产帧与命令路径**。
所以进程分离这套机械（spawn / 流式帧 / 命令通道 / 崩溃识别 / 停止）能在没有 SC2 的环境里
被测试覆盖，接真机时只换一个 flag。
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "modules", ROOT / "tools", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from game.catalog import load_terran  # noqa: E402
from game.geometry import Point2  # noqa: E402
from game.ports import ApplyResult  # noqa: E402
from game.production import QueueItem  # noqa: E402
from game.state import GameState  # noqa: E402
from flow.allocator import Allocator  # noqa: E402
from flow.engine import FlowEngine  # noqa: E402
from flow.manifest import parse_assembly, parse_strategy  # noqa: E402
from planner.build_order import ProductionModuleInstance  # noqa: E402
from planner.planner import Planner  # noqa: E402
from production.economy import EconomyKeeper, WorkerReservations  # noqa: E402
from production.runtime import ProductionRuntime  # noqa: E402
from tactical_map.base import instantiate_spawn, load_ladder_map  # noqa: E402
from view.port import OpRing, RecordingPort  # noqa: E402
from view.producer import FrameProducer  # noqa: E402
from view.proposals import parse_item  # noqa: E402
from world.adapter import adapt  # noqa: E402

from api.session import DEFAULT_ASSEMBLY, DEFAULT_STRATEGY  # noqa: E402


def _emit(obj: dict) -> None:
    """写一行 JSON 到 stdout 并 flush。

    必须 flush：父进程是按行读的，缓冲住会让"帧流"变成"游戏结束才一次性吐出来"。
    """
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


class _CommandReader(threading.Thread):
    """后台读 stdin。用线程是因为帧循环被 driver 占着（sc2 模式下我们不拥有循环）。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.inbox: queue.Queue[dict] = queue.Queue()
        self.closed = False

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                _emit({"_": "error", "detail": f"命令不是合法 JSON：{line[:120]}"})
        self.closed = True

    def drain(self) -> list[dict]:
        out: list[dict] = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out


class _Sink:
    """收集引擎发出的 op。sc2 模式下真 port 直接吃 op，这里只用于 sim 模式回灌世界。"""

    def __init__(self) -> None:
        self.ops: list = []

    def submit_operations(self, ops):
        self.ops.extend(ops)
        return ApplyResult(ok=True)

    def drain(self) -> list:
        out, self.ops = self.ops, []
        return out


class Session:
    """一套完整装配（与 `api.session.OfflineSession` 同构）+ 命令应用 + 产帧。"""

    def __init__(self, *, driver: str, reader: _CommandReader,
                 cc: Point2, map_name: str = "LadderMap") -> None:
        self.driver = driver
        self.reader = reader
        self.catalog = load_terran()
        self.sink = _Sink()
        self.ring = OpRing(maxlen=400)
        self.seq = 0
        self.game_time = 0.0
        self.stopping = False

        tpl = load_ladder_map()
        _, layout = sorted(tpl.spawns.items())[0]
        self.layer = instantiate_spawn(tpl, layout, cc)

        clock = lambda: self.game_time  # noqa: E731
        self.reservations = WorkerReservations()
        self.allocator = Allocator(catalog=self.catalog, reservations=self.reservations)
        self.manifest = parse_strategy(DEFAULT_STRATEGY)
        self.assembly = parse_assembly(DEFAULT_ASSEMBLY)
        self.engine = FlowEngine(
            self.manifest, self.assembly,
            RecordingPort(self.sink, "flow", self.ring, clock=clock),
            region_layer=self.layer, catalog=self.catalog, allocator=self.allocator)
        self.keeper = EconomyKeeper(
            self.catalog, RecordingPort(self.sink, "worker", self.ring, clock=clock),
            region_layer=self.layer, pool=self.allocator, reservations=self.reservations)
        self.runtime = ProductionRuntime(
            self.catalog, RecordingPort(self.sink, "production", self.ring, clock=clock),
            region_layer=self.layer, reservations=self.reservations, economy=self.keeper)
        self.producer = FrameProducer(
            catalog=self.catalog, engine=self.engine, runtime=self.runtime, keeper=self.keeper,
            ring=self.ring, planner=Planner(self.catalog), region_layer=self.layer,
            manifest=self.manifest, assembly=self.assembly, spawn="bl",
            frame_source="live", enemy_race="protoss", my_race="terran",
            projection_plan=[ProductionModuleInstance(
                instance_id="m0", module_ref="basic_opening", version=1, params={})])
        self._statics_done = False
        #: 最近一帧的 GameState —— 投影要用它当起点（父进程那边只有帧）
        self._last_gs: GameState | None = None

    # ---- 每帧 ----

    def on_state(self, gs: GameState) -> None:
        self._last_gs = gs
        self.seq = gs.seq
        self.game_time = gs.game_time
        if not self._statics_done:
            self._statics_done = True
            for frame in self.producer.statics(gs):
                _emit(frame)
            _emit({"_": "meta", "map_name": self.layer.map_name, "driver": self.driver})

        # 命令在**帧边界**应用：帧中间改状态会让"这一帧的观察对应哪个世界"说不清
        for cmd in self.reader.drain():
            self._apply(cmd)

        self.engine.on_game_state(gs)
        self.runtime.on_game_state(gs)
        self.keeper.on_game_state(gs)      # 征用先落表，维持器才知道谁不能动（ADR-0030 D3.3）
        for frame in self.producer.on_game_state(gs):
            _emit(frame)

    # ---- 命令 ----

    def _apply(self, cmd: dict) -> None:
        op = str(cmd.get("op") or "")
        try:
            if op == "stop":
                self.stopping = True
                _emit({"_": "meta", "state": "已结束"})
            elif op == "queue":
                self._queue_op(cmd)
            elif op == "workers":
                self.keeper.set_target(str(cmd["task"]), int(cmd["count"]))
                _emit({"_": "ack", "op": op, "seq": self.seq})
            elif op == "project":
                # 提案的双投影要在**有 GameState 的这一侧**算 —— 父进程只有帧，没有 GameState，
                # 而从 WorldFrame 反推 GameState 又错又脆。
                self._project(cmd)
            else:
                _emit({"_": "error", "detail": f"未知命令 op {op!r}"})
                return
            if op == "queue":
                _emit({"_": "ack", "op": op, "seq": self.seq})
        except Exception as exc:                      # noqa: BLE001
            # 一条坏命令不该弄死会话：报错继续（R7 降级但不静默）
            _emit({"_": "error", "detail": f"命令 {op} 失败：{type(exc).__name__}: {exc}"})

    def _project(self, cmd: dict) -> None:
        """按给定队列算一条投影并回给父进程（`id` 用于配对请求与回复）。"""
        from view.adapt import projection_frame
        from view.encode import to_json
        from view.projection import project_queue

        gs = self._last_gs
        if gs is None:
            _emit({"_": "projection", "id": cmd.get("id"), "error": "还没有帧"})
            return
        items = [parse_item(i) for i in (cmd.get("items") or [])]
        horizon = float(cmd.get("horizon") or 120.0)
        curve, tr = project_queue(self.producer.planner, gs, items,
                                  until=gs.game_time + horizon, catalog=self.catalog)
        frame = projection_frame(curve, based_on_seq=gs.seq, based_on_game_time=gs.game_time,
                                 horizon=horizon, queue_name=str(cmd.get("name") or "main"),
                                 skipped=tr.skipped)
        _emit({"_": "projection", "id": cmd.get("id"), "frame": to_json(frame)})

    def _queue_op(self, cmd: dict) -> None:
        name = str(cmd.get("name") or "main")
        kind = str(cmd.get("kind") or "")
        items = [parse_item(i) for i in (cmd.get("items") or [])]
        q = self.runtime.queue(name)
        if kind == "submit":
            self.runtime.submit_queue(name, items)
        elif kind == "append":
            self.runtime.append(name, items)
        elif kind == "prepend":
            self.runtime.prepend(name, items)
        elif kind == "clear":
            self.runtime.clear(name)
        elif kind == "remove":
            index = int(cmd["index"])
            if q is None or not (0 <= index < len(q.items)):
                raise ValueError(f"remove：队列 {name!r} 没有下标 {index}")
            self.runtime.remove(name, q.items[index])
        elif kind == "reorder":
            order = [int(x) for x in cmd["order"]]
            if q is None or sorted(order) != list(range(len(q.items))):
                raise ValueError(f"reorder：order 必须是 0..{len(q.items) if q else 0}-1 的排列")
            self.runtime.reorder(name, [q.items[i] for i in order])
        else:
            raise ValueError(f"未知队列 kind {kind!r}")


# ---------------- 两种驱动 ----------------

def _run_sim(session: Session, *, seconds: float, workers: int, minerals: float,
             tick_seconds: float) -> None:
    """假世界驱动：我们自己拥有循环。与 sc2 走同一条产帧/命令路径。

    `tick_seconds` 是**墙钟节拍**（每游戏秒睡多久）。必须有它：不限速的话 600 游戏秒
    能在两秒内跑完 —— 那就不是"live"了，命令还没送到会话就结束了（实测踩过）。
    `0` = 不限速（跑批/测试用）。
    """
    import time

    from worldsim import WorldSim

    world = WorldSim(catalog=session.catalog, cc_pos=Point2(30.5, 30.5), minerals=minerals)
    world.bootstrap(workers=workers)
    while world.t <= seconds and not session.stopping:
        started = time.monotonic()
        gs = world.game_state()
        session.on_state(gs)
        world.apply(session.sink.drain())
        world.tick(1.0)
        if tick_seconds > 0:
            slack = tick_seconds - (time.monotonic() - started)
            if slack > 0:
                time.sleep(slack)
    _emit({"_": "bye", "reason": "已结束" if session.stopping else "到时长上限"})


def _run_sc2(session: Session, *, map_name: str, seconds: int, realtime: bool) -> None:
    """真 SC2：driver 拥有循环，我们只当 RuntimeSink 被回调。"""
    from sc2.data import Difficulty, Race

    from driver.sc2_adapter import SC2GamePort

    class _RawSink:
        """driver 推 RawGameState；world 转 GameState 后交给同一条产帧路径。"""

        def on_game_state(self, raw) -> None:
            session.on_state(adapt(raw))

        def on_session_event(self, event) -> None:
            _emit({"_": "meta", "session_event": getattr(event, "kind", str(event))})

    port = SC2GamePort(map_name=map_name, race=Race.Terran, difficulty=Difficulty.Easy,
                       sink=_RawSink(), game_time_limit=seconds, realtime=realtime,
                       catalog=session.catalog)
    # B4：game_info 就绪后把静态地形推出去（父进程合并进 static/terrain）
    from view.encode import to_json
    from view.statics import terrain_static

    port.on_map_info(lambda info: _emit({
        "_": "terrain", "terrain": to_json(terrain_static(info))}))
    # 引擎发的 op 要真的下发给 SC2：把三个 RecordingPort 的内层换成真 port
    for holder in (session.engine, session.runtime, session.keeper):
        inner = getattr(holder, "_port", None)
        if inner is not None and hasattr(inner, "_inner"):
            inner._inner = port          # noqa: SLF001（RecordingPort 的内层就是要换的东西）
    port.start("live")
    _emit({"_": "bye", "reason": "run_game 返回"})


def main() -> int:
    ap = argparse.ArgumentParser(description="会话子进程（帧出 stdout、命令入 stdin）")
    ap.add_argument("--driver", choices=("sim", "sc2"), default="sim")
    ap.add_argument("--map", default="LadderMap")
    ap.add_argument("--seconds", type=float, default=600.0, help="时长上限（游戏秒）")
    ap.add_argument("--workers", type=int, default=12, help="sim：开局工兵数")
    ap.add_argument("--minerals", type=float, default=400.0, help="sim：开局矿")
    ap.add_argument("--realtime", action="store_true", help="sc2：实时模式")
    ap.add_argument("--tick-seconds", type=float, default=0.25,
                    help="sim：每游戏秒睡多久（0=不限速）")
    args = ap.parse_args()

    reader = _CommandReader()
    reader.start()
    try:
        session = Session(driver=args.driver, reader=reader, cc=Point2(30.5, 30.5),
                          map_name=args.map)
        if args.driver == "sim":
            _run_sim(session, seconds=args.seconds, workers=args.workers,
                     minerals=args.minerals, tick_seconds=args.tick_seconds)
        else:
            _run_sc2(session, map_name=args.map, seconds=int(args.seconds),
                     realtime=args.realtime)
    except Exception:                                  # noqa: BLE001
        # 崩溃要**结构化**报给父进程：否则父进程只能看到 exit code，说不出崩在哪
        _emit({"_": "error", "fatal": True, "detail": traceback.format_exc(limit=6)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())