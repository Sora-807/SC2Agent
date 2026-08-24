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

from game.catalog import load_all  # noqa: E402
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
from tactical_map.base import instantiate_spawn, load_ladder_map, load_map_plan  # noqa: E402
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
                 cc: Point2, map_name: str = "LadderMap",
                 map_plan: str | None = None,
                 strategy_file: str | None = None,
                 spawn: str | None = None,
                 control_path: Path | None = None) -> None:
        self.driver = driver
        self.reader = reader
        self.catalog = load_all()
        self.sink = _Sink()
        self.ring = OpRing(maxlen=400)
        self.seq = 0
        self.game_time = 0.0
        self.stopping = False
        #: 仿真模式倍速：None=未设（sim 按原 tick-seconds 节拍）；0=不限速；N>1=目标 N 倍。
        #: sim 与 sc2 共用这一个控制命令（speed op），sc2 落到 bot 配速、sim 缩放节拍睡眠。
        self._speed: float | None = None
        self._sc2_port = None   # sc2 模式挂 port 引用（speed 控制命令热改配速用）
        #: 控制文件通道（B1/C）：sc2 的 stdin 是 DEVNULL（burnysc2 继承管道会挂起），
        #: 命令改经文件 —— 父进程 append 写，帧边界 rename→读→删（无损）。
        self._control_path = Path(control_path) if control_path else None

        # 会话装配用**选定的地图规划文件**（进入游戏加载哪一份）；缺省 = 手写出厂模板
        tpl = load_map_plan(map_plan) if map_plan else load_ladder_map()
        self._map_tpl = tpl                    # 出生点检测要用（就近选 bl/tr 分支）
        if spawn and spawn in tpl.spawns:
            _, layout = spawn, tpl.spawns[spawn]      # loadout 显式选出生点布局
        else:
            _, layout = sorted(tpl.spawns.items())[0]  # 缺省 = 排序第一个（现状）
        # 真机的实际出生点首帧才知道：构造期这套 layer 是**临时假定**（排序第一 = bl），
        # 第一帧用实测 CC 重建（2026-08-24 事故修：此前锚点写死左下，出生右上时
        # 工人被派去左下采矿、槽位/地图层全错）。sim 世界就在 bl，无需检测。
        self._spawn_detected = driver != "sc2"
        self.layer = instantiate_spawn(tpl, layout, cc)
        # I8：预设固定建造点名进 layer（与 OfflineSession 同一语义，见 api/session.py）
        from tactical_map.reserved import reserved_marks
        self.layer.pos_marks.update(reserved_marks(self.catalog))

        clock = lambda: self.game_time  # noqa: E731
        self.reservations = WorkerReservations()
        self.allocator = Allocator(catalog=self.catalog, reservations=self.reservations)
        # 策略装配（二十七轮）：strategy_file 覆盖内置常量；坏文件带原因抛出
        if strategy_file:
            from view.strategies import load_strategy_file

            self.manifest, self.assembly = load_strategy_file(Path(strategy_file))
        else:
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
            # enemy_race 起手未知（对手是 Random）：首个可见敌方单位出现时推导覆盖
            #（写死 "protoss" 是假数据 —— 复盘清单的「人族 vs ?」就出不来）
            frame_source="live", enemy_race=None, my_race="terran",
            projection_plan=[ProductionModuleInstance(
                instance_id="m0", module_ref="basic_opening", version=1, params={})])
        self._statics_done = False
        #: 最近一帧的 GameState —— 投影要用它当起点（父进程那边只有帧）
        self._last_gs: GameState | None = None
        self._enemy_race: str | None = None

    # ---- 每帧 ----

    def on_state(self, gs: GameState) -> None:
        self._last_gs = gs
        self.seq = gs.seq
        self.game_time = gs.game_time
        if self._enemy_race is None:
            r = self._derive_enemy_race(gs)
            if r is not None:
                self._enemy_race = r
                self.producer.enemy_race = r
        if not self._statics_done:
            spawn_key = None
            if not self._spawn_detected:
                # 首帧实测出生点 → 就近分支 → 重建 layer（必须在 statics 发出**之前**，
                # 这样第一张 static/map 就是修正后的，不需要事后补帧）
                spawn_key = self._detect_spawn(gs)
                self._spawn_detected = True
            self._statics_done = True
            for frame in self.producer.statics(gs):
                _emit(frame)
            meta = {"_": "meta", "map_name": self.layer.map_name, "driver": self.driver}
            if spawn_key is not None:
                meta["spawn"] = spawn_key          # 实测出生点（诊断/前端可见）
            _emit(meta)

        # 命令在**帧边界**应用：帧中间改状态会让"这一帧的观察对应哪个世界"说不清
        for cmd in self._drain_control():
            self._apply(cmd)
        for cmd in self.reader.drain():
            self._apply(cmd)

        self.engine.on_game_state(gs)
        self.runtime.on_game_state(gs)
        self.keeper.on_game_state(gs)      # 征用先落表，维持器才知道谁不能动（ADR-0030 D3.3）
        for frame in self.producer.on_game_state(gs):
            _emit(frame)

    def _drain_control(self) -> list[dict]:
        """控制文件 → 命令列表（sc2 的命令通道；sim 走 stdin，两者都在帧边界应用）。

        rename → 读 → 删：rename 之后父进程再 append 会建新文件，读删之间无丢失窗口。
        坏行（非 JSON）记 error 控制行并跳过 —— 一行坏数据不该弄死会话。
        """
        import os

        if self._control_path is None or not self._control_path.exists():
            return []
        pending = self._control_path.with_suffix(".pending")
        try:
            os.replace(self._control_path, pending)
            lines = pending.read_text(encoding="utf-8").splitlines()
            pending.unlink(missing_ok=True)
        except OSError:
            return []
        out: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except ValueError:
                _emit({"_": "error", "detail": f"控制行不是合法 JSON：{line[:120]}"})
        return out

    # ---- 出生点检测（2026-08-24 事故修：真机随机 bl/tr，构造期不知道）----

    def _detect_spawn(self, gs: GameState) -> str | None:
        """实测我方 CC 位置 → 就近选模板分支 → 重建 layer 并同步给全部持有者。

        CC 找不到（异常局面）= 保持临时假定的 layer，meta 不带 spawn 键（如实）。
        """
        from game import Owner
        from tactical_map.base import instantiate_spawn, pick_spawn_layout
        from tactical_map.reserved import reserved_marks

        name = self.catalog.burnysc2_name_for("terran/commandcenter")
        cc = next((u.position for u in gs.units
                   if u.owner is Owner.SELF and u.type_name == name), None)
        if cc is None:
            return None
        key, layout = pick_spawn_layout(self._map_tpl, cc)
        self.layer = instantiate_spawn(self._map_tpl, layout, cc)
        self.layer.pos_marks.update(reserved_marks(self.catalog))
        # 持有 layer 的四方全部换到实测层（引擎动作解析/经济锚点/摆放/静态面）
        self.engine._region_layer = self.layer
        self.keeper._region_layer = self.layer
        self.runtime._region_layer = self.layer
        self.producer.region_layer = self.layer
        self.producer.spawn = key
        return key

    # ---- 命令 ----

    def _derive_enemy_race(self, gs: GameState) -> str | None:
        """首个可见敌方单位的 stable id 前缀（terran/protoss/zerg）。没见过敌人 = None。"""
        from game import Owner

        for u in gs.units:
            if u.owner is not Owner.ENEMY:
                continue
            entry = self.catalog.by_burnysc2_name(
                self.catalog.normalize_burnysc2_name(u.type_name.upper()))
            if entry is not None and "/" in entry.stable_id:
                return entry.stable_id.split("/", 1)[0]
        return None

    def _apply(self, cmd: dict) -> None:
        op = str(cmd.get("op") or "")
        try:
            if op == "stop":
                self.stopping = True
                _emit({"_": "meta", "state": "已结束"})
            elif op == "queue":
                self._queue_op(cmd)
            elif op == "swap":
                # 热切 V1（批 C）：整份策略文件切换，帧边界应用（本方法就在帧边界被调）
                self._swap_strategy(str(cmd["strategy"]))
                _emit({"_": "ack", "op": op, "seq": self.seq})
            elif op == "speed":
                # 仿真模式变速（倍数即时生效；sc2 落 bot 配速，sim 缩放节拍睡眠）
                m = float(cmd.get("multiplier", 0))
                if m != 0 and not (1 <= m <= 64):
                    raise ValueError("multiplier 只能是 0（不限速）或 1..64")
                self._speed = m
                if self._sc2_port is not None:
                    self._sc2_port.set_speed(m)
                _emit({"_": "ack", "op": "speed", "multiplier": m, "seq": self.seq})
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

    def _swap_strategy(self, strategy_file: str) -> None:
        """热切（批 C）：装载新 manifest → 引擎换脑（同装配约束 + 续位）→ 重发 static/strategy。

        失败（编译红/装配不兼容/地图名字校验）在这里抛 → _apply 的兜底把它变成
        error 控制行 —— 会话继续跑旧策略，不静默也不崩。
        """
        from view.strategies import load_strategy_file

        manifest, _assembly = load_strategy_file(Path(strategy_file))
        self.engine.swap_strategy(manifest)
        self.manifest = manifest
        self.producer.manifest = manifest
        if self._last_gs is not None:
            for frame in self.producer.statics(self._last_gs):
                if frame["topic"] == "static/strategy":
                    _emit(frame)     # 事件式静态面：swap 后补发新图

    def _queue_op(self, cmd: dict) -> None:
        from api.commands import QUEUE_OPS

        name = str(cmd.get("name") or "main")
        kind = str(cmd.get("kind") or "")
        items = [parse_item(i) for i in (cmd.get("items") or [])]
        q = self.runtime.queue(name)
        if kind not in QUEUE_OPS:
            raise ValueError(f"未知队列 kind {kind!r}（{'|'.join(sorted(QUEUE_OPS))}）")
        if kind == "submit":
            self.runtime.submit_queue(name, items)
        elif kind == "append":
            self.runtime.append(name, items)
        elif kind == "prepend":
            self.runtime.prepend(name, items)
        elif kind == "insert":
            index = int(cmd["index"])
            self.runtime.insert(name, index, items)
        elif kind == "replace_head":
            if not items:
                raise ValueError("replace_head：缺 items（要换上的新队首；清空请用 clear）")
            self.runtime.replace_head(name, items)
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
    # 沙盒没有 game_info —— 发真机采集的地形数据文件（ladder_terrain_view）。
    # 必须在**首个 on_state（statics）之后**发：早于 static/map 的话前端 store
    # 的 terrain→map 合并会丢帧（B16 的教训）。
    terrain_sent = False
    while world.t <= seconds and not session.stopping:
        started = time.monotonic()
        gs = world.game_state()
        session.on_state(gs)
        if not terrain_sent:
            terrain_sent = True
            from view.encode import to_json
            from view.statics import ladder_terrain_view

            terrain = ladder_terrain_view()
            if terrain is not None:
                _emit({"_": "terrain", "terrain": to_json(terrain)})
        world.apply(session.sink.drain())
        world.tick(1.0)
        if tick_seconds > 0:
            slack = tick_seconds - (time.monotonic() - started)
            if slack > 0:
                time.sleep(slack)
    _emit({"_": "bye", "reason": "已结束" if session.stopping else "到时长上限"})


def _run_sc2(session: Session, *, map_name: str, seconds: int, realtime: bool,
             speed: float = 0.0) -> None:
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
                       speed=speed, catalog=session.catalog)
    session._sc2_port = port   # speed 控制命令热改配速的落点
    # B4：game_info 就绪后把静态地形推出去（父进程合并进 static/terrain）。
    # expansions（基地/扩张位置）走同一控制行的**旁挂键**：不进 terrain 帧 payload
    #（契约干净），父进程存 meta 供采集/诊断。
    from view.encode import to_json
    from view.statics import terrain_static

    port.on_map_info(lambda info: _emit({
        "_": "terrain", "terrain": to_json(terrain_static(info)),
        "expansions": info.get("expansions") or []}))
    # 引擎发的 op 要真的下发给 SC2：把三个 RecordingPort 的内层换成真 port
    for holder in (session.engine, session.runtime, session.keeper):
        inner = getattr(holder, "_port", None)
        if inner is not None and hasattr(inner, "_inner"):
            inner._inner = port          # noqa: SLF001（RecordingPort 的内层就是要换的东西）
    port.start("live")
    _emit({"_": "bye", "reason": "run_game 返回"})


def _redirect_loguru_to_stderr() -> None:
    """burnysc2 的 `sc2.main` 在导入时执行 `logger.add(sys.stdout, level="INFO")` ——
    那会把日志写进**帧协议管道**（stdout 是父进程读帧的通道，混入日志会污染协议）。

    真机实测：子进程 stdout 的第一行是带颜色的 loguru 日志，父进程把它当成"非 JSON"记录。
    修法：把 loguru 重新指到 stderr（stderr 本来就是诊断通道，父进程只保留尾巴）。
    """
    try:
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="INFO")
    except ImportError:
        pass  # 没装 loguru 就无所谓


def main() -> int:
    _redirect_loguru_to_stderr()
    ap = argparse.ArgumentParser(description="会话子进程（帧出 stdout、命令入 stdin）")
    ap.add_argument("--driver", choices=("sim", "sc2"), default="sim")
    ap.add_argument("--map", default="LadderMap")
    ap.add_argument("--strategy-file", default=None,
                    help="策略文件（strategy+assembly 两段 YAML）；缺省 = 内置常量")
    ap.add_argument("--map-plan", default=None,
                    help="地图规划文件路径（会话装配用它；缺省 = 手写出厂模板）")
    ap.add_argument("--spawn", default=None, choices=("bl", "tr"),
                    help="出生点布局（地图规划 spawns 的键；缺省 = 排序第一个）")
    ap.add_argument("--control-file", default=None,
                    help="控制文件路径（sc2 的命令通道：父进程 append 写、帧边界应用）")
    ap.add_argument("--seconds", type=float, default=600.0, help="时长上限（游戏秒）")
    ap.add_argument("--workers", type=int, default=12, help="sim：开局工兵数")
    ap.add_argument("--minerals", type=float, default=400.0, help="sim：开局矿")
    ap.add_argument("--realtime", action="store_true", help="sc2：正常模式（实时流速）")
    ap.add_argument("--speed", type=float, default=None,
                    help="仿真模式倍速：0=不限速（最快）；N>1=目标 N 倍。sc2 落 bot 配速，sim 缩放节拍")
    ap.add_argument("--tick-seconds", type=float, default=0.25,
                    help="sim：每游戏秒睡多久（0=不限速）")
    args = ap.parse_args()

    reader = _CommandReader()
    reader.start()
    try:
        session = Session(driver=args.driver, reader=reader, cc=Point2(30.5, 30.5),
                          map_name=args.map, map_plan=args.map_plan,
                          strategy_file=args.strategy_file,
                          spawn=args.spawn,
                          control_path=Path(args.control_file) if args.control_file else None)
        if args.speed is not None:
            session._speed = args.speed
        if args.driver == "sim":
            _run_sim(session, seconds=args.seconds, workers=args.workers,
                     minerals=args.minerals, tick_seconds=args.tick_seconds)
        else:
            _run_sc2(session, map_name=args.map, seconds=int(args.seconds),
                     realtime=args.realtime, speed=args.speed or 0.0)
    except Exception:                                  # noqa: BLE001
        # 崩溃要**结构化**报给父进程：否则父进程只能看到 exit code，说不出崩在哪
        _emit({"_": "error", "fatal": True, "detail": traceback.format_exc(limit=6)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())