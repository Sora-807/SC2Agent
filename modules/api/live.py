"""api.live：LiveSession —— 通过**子进程**驱动的会话（B3）。

它和 `OfflineSession` 提供**同一套接口**（`info/statics/latest_at/between` + `seq`/`check_seq`
+ 命令方法），所以 WS 通道、命令端点、提案存储、观察包全都不用改 ——
前端把帧源切成 `live` 就在看它。

为什么分进程（S7 想要的三件事）：
1. `SC2GamePort.start()` 阻塞在 burnysc2 的 `run_game()` 里，它自带事件循环，
   塞进 api 进程会和 uvicorn 打架；
2. **游戏崩了不带走 api** —— 子进程死了这里变"崩溃"态，UI 还活着并能看到原因；
3. **没开游戏时 UI 照样活着**。

读子进程 stdout 用**线程**而不是 asyncio 子进程：Windows 上 asyncio 的子进程支持要
ProactorEventLoop，而 uvicorn 的循环策略不由我们定 —— 一个 daemon 线程 + 队列更省事也更稳。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from view.schema import REV, STATIC_TOPICS

from api.commands import QUEUE_OPS
from api.session import MAX_STALE_SEQ, StaleObservation
from api.frame_source import SourceInfo, between, info_of, latest_at, statics_only
from api.live_io import RecordingMixin, kill_tree

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_session.py"

#: 内存里保留的帧数（够时间线回看最近一段；完整历史落录制文件 —— 二十六轮起真落盘）
FRAME_BUFFER = 6000
#: 停止时等子进程自己退出的秒数，超时就 kill
STOP_GRACE = 5.0


class LiveSession(RecordingMixin):
    """子进程会话。`driver="sim"` 用假世界（能在没有 SC2 的环境里验进程分离），
    `driver="sc2"` 是真机。两者在子进程里走**完全同一条**产帧与命令路径。"""

    id = "live"

    def __init__(self, *, driver: str = "sim", map_name: str = "LadderMap",
                 seconds: float = 600.0, realtime: bool | None = None,
                 tick_seconds: float = 0.25,
                 label: str | None = None, python: str | None = None,
                 map_plan: str | None = None,
                 map_plans_dir: Path | str | None = None,
                 strategy_path: str | None = None,
                 spawn: str | None = None,
                 speed: float = 0.0,
                 record_dir: Path | None = None) -> None:
        self.driver = driver
        self.label = label or (f"真机会话（{map_name}）" if driver == "sc2"
                               else "子进程沙盒（假世界，验进程分离）")
        if map_plan:
            self.label = (self.label + " · " + Path(map_plan).stem)
        self.state = "启动中"
        self.error: str | None = None
        self.seq = 0
        self.game_time = 0.0
        self.frames: list[dict] = []
        self._statics: list[dict] = []
        self._lock = threading.Lock()
        self._acks = 0
        self._meta: dict[str, Any] = {}
        #: 投影往返：id → [Event, 结果]。双投影必须在**有 GameState 的**子进程侧算。
        self._pending: dict[int, list] = {}
        self._next_req = 0
        # 真机默认实时配速（二十六轮用户反馈「实时游戏连接不是正常流速」）：
        # realtime=False 时 burnysc2 的 step 不等墙钟，游戏时间飞跑。
        # sim 不受影响（它本来就按 tick_seconds 睡）。
        self._realtime = (driver == "sc2") if realtime is None else realtime
        # 对局录制 setup/收尾在 api.live_io（RecordingMixin，N3 抽出）；
        # record_dir=None（测试默认）= 不录
        self._rec_fh = None
        self._rec_meta_path: Path | None = None
        self._rec_count = 0
        self._init_recording(record_dir, driver, map_name, map_plan)
        #: 仿真模式倍速（「开启游戏」两模式，2026-08-23）：0=不限速；N>1=目标 N 倍。
        #: 只在非实时（仿真）会话有意义；set_speed 热改（快进倍数选择）。
        self.speed = float(speed)

        # 控制文件通道（B1/C）：sc2 的 stdin 是 DEVNULL（burnysc2 继承管道会挂起），
        # 命令改经文件 —— append 写、子进程帧边界 rename→读→删（无损）。sim 不用它
        #（stdin 管道本来就好好的），但通道两条腿都在：真机从此有命令面。
        self._ctl_lock = threading.Lock()
        self._ctl_path: Path | None = None
        if driver == "sc2":
            import tempfile

            ctl_dir = Path(tempfile.gettempdir())
            fd, name = tempfile.mkstemp(prefix="sc2agent-ctl-", suffix=".json",
                                        dir=str(ctl_dir))
            os.close(fd)
            self._ctl_path = Path(name)
            self._ctl_path.unlink(missing_ok=True)   # mkstemp 建的是占位，通道用"存在=有待办"

        cmd = [
            python or sys.executable, "-X", "utf8", str(RUNNER),
            "--driver", driver, "--map", map_name, "--seconds", str(seconds),
            "--tick-seconds", str(tick_seconds),
        ]
        if self._realtime:
            cmd.append("--realtime")
        if map_plan:
            cmd += ["--map-plan", str(map_plan)]
        if map_plans_dir:
            cmd += ["--map-plans-dir", str(map_plans_dir)]   # 批 2：合并图层要整个目录
        if strategy_path:
            cmd += ["--strategy-file", str(strategy_path)]   # 二十七轮：开放写策略
        if spawn:
            cmd += ["--spawn", str(spawn)]                   # B1：loadout 的出生点布局
        if driver == "sc2" and self.speed:
            cmd += ["--speed", str(self.speed)]              # 仿真模式起始倍速（sim 节拍不随之变）
        if self._ctl_path is not None:
            cmd += ["--control-file", str(self._ctl_path)]
        # 真机发现：`stdin=PIPE` 且保持打开会让 SC2 挂起（burnysc2 启动的 SC2 进程
        # 继承了打开的 stdin 管道句柄）。给 stdin 发 EOF（关闭写端）即可解除 ——
        # 但那样命令也写不进去了。所以：sim 用 PIPE（命令走 stdin），
        # sc2 用 DEVNULL（真机没有 stdin 命令通道，也不该有 —— 它的写面只有 op 队列）。
        stdin_mode = subprocess.DEVNULL if driver == "sc2" else subprocess.PIPE
        self.proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdin=stdin_mode, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        self._errs = threading.Thread(target=self._pump_stderr, daemon=True)
        self._errs.start()

    # ---- 子进程 I/O ----

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                # 子进程往 stdout 写了非 JSON（比如某个库的 print）→ 记到诊断尾部，
                # **不动 error**：一行日志不意味着会话崩了，帧流还在继续。
                self._meta.setdefault("stdout_noise", [])
                noise = self._meta["stdout_noise"]
                noise.append(line[:160])
                del noise[-5:]
                continue
            if "_" in obj:
                self._control(obj)
            else:
                self._frame(obj)
        # stdout 关了 = 子进程结束
        code = self.proc.wait()
        with self._lock:
            if self.state not in ("已结束", "崩溃"):
                self.state = "已结束" if code == 0 else "崩溃"
                if code != 0 and not self.error:
                    self.error = f"子进程退出码 {code}"
            self._close_recording("已结束" if code == 0 else "崩溃")

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            if line.strip():
                # stderr 不当错误处理（burnysc2 会往那儿写日志），但保留最后一段供诊断
                self._meta.setdefault("stderr_tail", [])
                tail = self._meta["stderr_tail"]
                tail.append(line.rstrip())
                del tail[:-20]

    def _control(self, obj: dict) -> None:
        kind = obj.get("_")
        need_frame = False
        terrain_frame: dict = {}
        with self._lock:
            if kind == "meta":
                self._meta.update({k: v for k, v in obj.items() if k != "_"})
                if obj.get("state"):
                    self.state = str(obj["state"])
            elif kind == "ack":
                self._acks += 1
            elif kind == "error":
                self._note_error(str(obj.get("detail") or "未知错误"), fatal=bool(obj.get("fatal")))
            elif kind == "terrain":
                # B4：地形是**事件式静态面** —— 控制行转成真正的 `static/terrain` 帧，
                # 这样前端订阅静态面时自然合并进 map.terrain（不搞特殊通道）。
                # 控制行里没有 seq/game_time（driver 发在 on_step 之外），用当前游标补齐。
                # 注意：此刻**已经在外层 with self._lock 里**，不能再用 self._lock
                # （普通 Lock 同线程重入 = 死锁，真机测地形时踩过）。
                self._meta["terrain"] = obj.get("terrain")
                if obj.get("expansions"):
                    # 基地/扩张位置（旁挂键，不进帧 payload）：采集与诊断用
                    self._meta["expansions"] = obj["expansions"]
                terrain_seq = self.seq
                terrain_time = self.game_time
                terrain_frame = {
                    "topic": "static/terrain",
                    "rev": REV,
                    "seq": terrain_seq,
                    "game_time": terrain_time,
                    # 诊断字段给真墙钟（此前恒 0，任何算延迟的诊断都是假的）
                    "wall_ms": int(time.time() * 1000),
                    "payload": obj.get("terrain"),
                }
                need_frame = True
            elif kind == "export-result":
                slot = self._pending.get(int(obj.get("id") or -1))
                if slot is not None:
                    slot[1] = obj
                    slot[0].set()
            elif kind == "projection":
                slot = self._pending.get(int(obj.get("id") or -1))
                if slot is not None:
                    slot[1] = obj
                    slot[0].set()
            elif kind == "bye":
                if self.state != "崩溃":
                    self.state = "已结束"
                self._meta["bye"] = obj.get("reason")
        if need_frame:
            self._frame(terrain_frame)

    def _frame(self, frame: dict) -> None:
        with self._lock:
            if frame.get("topic") in STATIC_TOPICS:
                # 同 topic 的静态帧**替换**而不是并列（热切换策略会重发 static/strategy；
                # latest_at 的 setdefault 是首帧优先，并列会让旧图永远遮住新图）
                self._statics = [f for f in self._statics if f["topic"] != frame["topic"]]
                self._statics.append(frame)
            self.frames.append(frame)
            if len(self.frames) > FRAME_BUFFER:
                keep = [f for f in self.frames[-FRAME_BUFFER:]
                        if f.get("topic") not in STATIC_TOPICS]
                self.frames = self._statics + keep
            self.seq = max(self.seq, int(frame.get("seq", 0)))
            self.game_time = max(self.game_time, float(frame.get("game_time", 0.0)))
            if self.state == "启动中":
                self.state = "对局中"
            # 录制：帧流同步落盘（buffering=1 行缓冲；写失败不拦帧流）
            if self._rec_fh is not None:
                try:
                    self._rec_fh.write(json.dumps(frame, ensure_ascii=False) + "\n")
                    self._rec_count += 1
                except OSError:
                    self._rec_fh = None

    def _note_error(self, detail: str, *, fatal: bool = False) -> None:
        self.error = detail
        if fatal:
            self.state = "崩溃"

    # ---- 帧源接口（与 JsonlSource / OfflineSession 同形；实现共享 api.frame_source，
    # 这里只包一层锁——读帧必须与写序一致）----

    def info(self) -> SourceInfo:
        with self._lock:
            return info_of(self.id, self.label, "live", self.frames)

    def statics(self) -> list[dict]:
        with self._lock:
            return statics_only(self._statics)

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        with self._lock:
            return latest_at(self.frames, self._statics, game_time, topics)

    def between(self, after: float, until: float,
                topics: set[str] | None = None) -> list[dict]:
        with self._lock:
            return between(self.frames, after, until, topics)

    # ---- 新鲜度门（R8），与 OfflineSession 同语义 ----

    def check_seq(self, based_on_seq: int | None) -> None:
        if based_on_seq is None:
            raise StaleObservation(-1, self.seq)
        if self.seq - int(based_on_seq) > MAX_STALE_SEQ:
            raise StaleObservation(int(based_on_seq), self.seq)

    # ---- 命令（写到子进程 stdin；在帧边界生效）----

    def _send(self, obj: dict) -> None:
        if self.proc.poll() is not None:
            raise RuntimeError(f"会话已结束（{self.state}）：{self.error or '子进程已退出'}")
        if self._ctl_path is not None:
            self._send_control(obj)     # sc2：stdin 是 DEVNULL，命令走控制文件
            return
        if self.proc.stdin is None:
            raise RuntimeError("该会话没有 stdin 命令通道（sc2 驱动；写面只有 op 队列）")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _send_control(self, obj: dict) -> None:
        """sc2 的命令通道：append 一行 JSON 到控制文件（子进程帧边界 rename→读→删）。"""
        assert self._ctl_path is not None
        with self._ctl_lock:
            with self._ctl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def queue_op(self, op: str, name: str, *, items: list | None = None,
                 before_uid: str | None = None, uid: str | None = None,
                 order: list[str] | None = None) -> dict:
        from view.proposals import item_to_json

        if op not in QUEUE_OPS:
            raise ValueError(f"未知队列 op {op!r}（{'|'.join(sorted(QUEUE_OPS))}）")
        self._send({
            "op": "queue", "kind": op, "name": name,
            "items": [item_to_json(i) for i in (items or [])],
            "before_uid": before_uid, "uid": uid, "order": order,
        })
        # 命令在**下一个帧边界**生效，所以这里回报的是"已送达"而不是"已生效"。
        # 前端据此显示 pending，等下一帧的 frame/production 确认。
        # B7 shape 统一：与 OfflineSession 同键 —— items = 这条命令携带的项数
        #（真身在子进程里，父进程只有帧；剩余长度等下一帧看 frame/production）。
        return {"queue": name, "items": len(items or []), "accepted_seq": self.seq}

    def set_worker_target(self, task: str, count: int) -> dict:
        self._send({"op": "workers", "task": task, "count": int(count)})
        return {"task": task, "quota": int(count), "accepted_seq": self.seq}

    def set_speed(self, multiplier: float) -> dict:
        """仿真模式变速（即时生效，不重启）：multiplier=0 → 不限速（最快）。

        正常模式（实时）没有变速通道 —— 游戏自己按真实流速走，客户端睡不着觉。
        """
        m = float(multiplier)
        if m != 0 and not (1 <= m <= 64):
            raise ValueError("multiplier 只能是 0（不限速）或 1..64 的倍数")
        if self._realtime:
            raise RuntimeError("正常模式按实时流速跑（玩家在场）；快进倍数属于仿真模式，"
                               "换模式要重开会话")
        if self.proc.poll() is not None:
            raise RuntimeError(f"会话已结束（{self.state}）：{self.error or '子进程已退出'}")
        self._send({"op": "speed", "multiplier": m})
        self.speed = m
        return {"speed": m, "accepted_seq": self.seq}

    def swap_strategy(self, strategy_file: str) -> dict:
        """热切 V1（批 C）：把 swap 命令发进子进程通道（stdin / sc2 走控制文件）。

        帧边界应用；约束校验在子进程侧跑（引擎 swap_strategy 先校验后变更），
        失败 → error 控制行 → `_note_error`，会话继续跑旧策略。
        """
        self._send({"op": "swap", "strategy": str(strategy_file)})
        return {"swap": "dispatched", "strategy": Path(strategy_file).stem,
                "accepted_seq": self.seq}

    def swap_map_plan(self, map_plan_id: str) -> dict:
        """默认地图热切（批 2）：换默认规划，帧边界重建合并图层并重发 static/map。

        命令走子进程通道；新默认不存在由子进程侧校验（它才看得见规划目录），
        失败 → error 控制行，会话继续跑旧默认。
        """
        if self.proc.poll() is not None:
            raise RuntimeError(f"会话已结束（{self.state}）：{self.error or '子进程已退出'}")
        self._send({"op": "map", "plan": str(map_plan_id)})
        return {"swap": "dispatched", "map_plan": str(map_plan_id),
                "accepted_seq": self.seq}

    def export_via_subprocess(self, name: str = "main", timeout: float = 5.0) -> dict | None:
        """批 6 清偿③：导出请求发进子进程（有 GameState 的一侧算最准），
        等结果（投影往返同一模式）。失败/超时 = None（调用方回退帧拼装）。"""
        with self._lock:
            self._next_req += 1
            req = self._next_req
            slot: list = [threading.Event(), None]
            self._pending[req] = slot
        try:
            self._send({"op": "export", "id": req, "name": name})
        except (RuntimeError, OSError):
            with self._lock:
                self._pending.pop(req, None)
            return None
        if not slot[0].wait(timeout):
            with self._lock:
                self._pending.pop(req, None)
            return None
        with self._lock:
            self._pending.pop(req, None)
        return slot[1]

    def tick(self) -> None:
        """手动步进一次 = 等一帧。子进程按墙钟自推，父进程的 `tick` 只是"等到新帧"。

        给 `/api/session/tick` 用：离线会话是推进，live 会话是等一帧 —— 对外语义一致
        （"给我一帧新观察"）。给负超时会一直等，所以加个上限。
        """
        import time

        target = self.seq + 1
        deadline = time.time() + 10.0
        while time.time() < deadline and self.seq < target:
            time.sleep(0.05)

    # ---- 提案需要的三件事（与 OfflineSession 同名同义）----

    def queue_items(self, name: str = "main") -> list:
        """从最近一帧 `frame/production` 反解队列项。

        队列的真身在子进程里，父进程**只有帧** —— 而帧就是唯一真相源（UI 看的也是它）。
        **字段名映射**：帧里是 `stable_id`，`parse_item` 吃的是 `type`。
        漏掉这一步的话 `type=None` 会一路流进投影（投影于是悄悄少算这段 —— 实测踩过：
        两条双投影曲线一模一样，cap 都是 15）。
        """
        from view.proposals import parse_item

        with self._lock:
            frames = [f for f in self.frames if f["topic"] == "frame/production"]
        if not frames:
            return []
        for q in frames[-1]["payload"]["queues"]:
            if q["name"] == name:
                out = []
                for it in q["items"]:
                    out.append(parse_item({
                        "op": it.get("op"),
                        "type": it.get("stable_id"),
                        "count": it.get("count"),
                        "placement": it.get("placement"),
                        "task": it.get("task"),
                        # 账本字段一并往返（ADR-0032）：丢 status 会让已完成项被重跑
                        "uid": it.get("uid"),
                        "status": it.get("status"),
                        "reason": it.get("reason"),
                    }))
                return out
        return []

    def apply_queue(self, name: str, items: list) -> dict:
        return self.queue_op("submit", name, items=items)

    def project(self, items: list, *, name: str = "main", horizon: float = 120.0,
                timeout: float = 5.0) -> dict | None:
        """让子进程按给定队列算投影（父进程没有 GameState，算不了）。"""
        from view.proposals import item_to_json

        with self._lock:
            self._next_req += 1
            req = self._next_req
            slot: list = [threading.Event(), None]
            self._pending[req] = slot
        try:
            self._send({"op": "project", "id": req, "name": name, "horizon": horizon,
                        "items": [item_to_json(i) for i in items]})
        except (RuntimeError, OSError):
            with self._lock:
                self._pending.pop(req, None)
            return None
        if not slot[0].wait(timeout):
            with self._lock:
                self._pending.pop(req, None)
            return None
        with self._lock:
            self._pending.pop(req, None)
        reply = slot[1] or {}
        if reply.get("error"):
            return None
        return reply.get("frame")

    # ---- 生命周期 ----

    def stop(self) -> None:
        try:
            self._send({"op": "stop"})
        except (RuntimeError, OSError, ValueError):
            pass
        # 控制文件通道收尾：unlink 掉（无论子进程有没有消费完，会话都停了）
        if self._ctl_path is not None:
            self._ctl_path.unlink(missing_ok=True)
            self._ctl_path.with_suffix(".pending").unlink(missing_ok=True)
        try:
            self.proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            pass
            # 注意：**不要在这里单独 kill 根**。taskkill /T 靠根进程枚举子树，
            # 根先死了 SC2 就成枚举不到的孤儿（真机实测：游戏停不掉就是这个顺序问题）。
        kill_tree(self.proc)
        # 兜底：树杀后根还活着（理论不该）再补刀
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        with self._lock:
            if self.state != "崩溃":
                self.state = "已结束"
            self._close_recording(self.state)

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id, "label": self.label, "kind": "live", "driver": self.driver,
                "state": self.state, "seq": self.seq, "game_time": round(self.game_time, 3),
                "max_stale_seq": MAX_STALE_SEQ, "error": self.error,
                "frames": len(self.frames), "acks": self._acks,
                "mode": "normal" if self._realtime else "fast",
                "speed": self.speed,
                "pid": self.proc.pid, "alive": self.proc.poll() is None,
                "meta": {k: v for k, v in self._meta.items() if k != "stderr_tail"},
                "queues": [],   # 队列由子进程持有；UI 从 frame/production 看（单一真相源）
                # 活跃警报（D 批）：子进程的 AlertService 我们够不着，从最近帧里捞
                "alerts": self._recent_alerts(),
            }

    def _recent_alerts(self, within: float = 15.0) -> list[dict]:
        """frame/alerts 是「新报出的」增量帧 —— 最近 `within` 游戏秒内的都算还在响。

        ⚠️ 只在 describe() 里调用（调用方**已持有** self._lock）—— 普通 Lock 同线程
        重入 = 死锁（_control 的 terrain 分支踩过同一个坑，见上方注释）。
        """
        out: list[dict] = []
        seen: set[str] = set()
        cutoff = self.game_time - within
        frames = self.frames
        for f in reversed(frames):
            if f.get("game_time", 0.0) < cutoff:
                break
            if f.get("topic") != "frame/alerts":
                continue
            for a in f.get("payload", {}).get("alerts", []):
                aid = str(a.get("id"))
                if aid in seen:
                    continue
                seen.add(aid)
                out.append({"id": aid, "kind": a.get("kind"), "severity": a.get("severity"),
                            "text_zh": a.get("text_zh"), "at": f.get("game_time")})
        return out
