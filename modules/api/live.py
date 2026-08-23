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
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from view.schema import REV, STATIC_TOPICS

from api.commands import QUEUE_OPS
from api.session import MAX_STALE_SEQ, StaleObservation
from api.sources import SourceInfo

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_session.py"

#: 内存里保留的帧数（够时间线回看最近一段；完整历史落录制文件 —— 二十六轮起真落盘）
FRAME_BUFFER = 6000
#: 停止时等子进程自己退出的秒数，超时就 kill
STOP_GRACE = 5.0


class LiveSession:
    """子进程会话。`driver="sim"` 用假世界（能在没有 SC2 的环境里验进程分离），
    `driver="sc2"` 是真机。两者在子进程里走**完全同一条**产帧与命令路径。"""

    id = "live"

    def __init__(self, *, driver: str = "sim", map_name: str = "LadderMap",
                 seconds: float = 600.0, realtime: bool | None = None,
                 tick_seconds: float = 0.25,
                 label: str | None = None, python: str | None = None,
                 map_plan: str | None = None,
                 strategy_path: str | None = None,
                 spawn: str | None = None,
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
        # 对局记录（二十六轮用户反馈「对局记录没保存」）：帧流同步落 JSONL，
        # 结束后复盘模式的下拉里出现「📹 录像」。内存 FRAME_BUFFER 会截老帧，
        # 文件才是完整历史。record_dir=None（测试默认）= 不录。
        self._rec_fh = None
        self._rec_meta_path: Path | None = None
        self._rec_count = 0
        if record_dir is not None:
            try:
                record_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                rid = f"rec-{stamp}-{driver}"
                rec_path = record_dir / f"{rid}.jsonl"
                self._rec_fh = rec_path.open("w", encoding="utf-8", buffering=1)
                self._rec_meta_path = record_dir / f"{rid}.meta.json"
                self._rec_meta_path.write_text(json.dumps({
                    "id": rid, "driver": driver, "map": map_name,
                    "label": self.label, "map_plan": Path(map_plan).stem if map_plan else None,
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "state": "recording",
                }, ensure_ascii=False), encoding="utf-8")
                self._meta["recording"] = rid
            except OSError:
                self._rec_fh = None   # 录不了不拦对局：帧流照跑，只是没文件

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
        if strategy_path:
            cmd += ["--strategy-file", str(strategy_path)]   # 二十七轮：开放写策略
        if spawn:
            cmd += ["--spawn", str(spawn)]                   # B1：loadout 的出生点布局
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

    def _close_recording(self, final_state: str) -> None:
        """收尾录制：关文件 + 把 meta 标成终态（时长/帧数写进去，清单端点就不用扫文件）。
        调用方需持有 self._lock（与 _frame 同一把，写序一致）。"""
        fh, self._rec_fh = self._rec_fh, None
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except OSError:
                pass
        meta_path, self._rec_meta_path = self._rec_meta_path, None
        if meta_path is None:
            return
        # 命名是 <rid>.meta.json —— 不能用 .stem（只剥最后一个后缀，得到 "<rid>.meta"）
        rid = meta_path.name.replace(".meta.json", "")
        my_zh, enemy_zh = _races_from_frames(self.frames)
        try:
            old: dict = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {"id": rid, "driver": self.driver, "label": self.label}
        old.update({
            "state": final_state,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "envelopes": self._rec_count,
            "to": round(self.game_time, 3),       # 清单端点统一读 to（前端时长显示）
            "to_time": round(self.game_time, 3),
            "my_race_zh": my_zh,                   # 复盘清单的「人族 vs 神族」（二十七轮）
            "enemy_race_zh": enemy_zh,
        })
        try:
            meta_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        # 衍生摘要（I20 文件契约闭环）：原始帧流几 MB，人/agent 都翻不动 ——
        # 落盘原则 = 原始数据 + 可读视图一起保存。渲染失败不拦收尾（jsonl 仍在）。
        try:
            from view.recap import render_recording_summary

            summary = render_recording_summary(self._statics + self.frames)
            meta_path.with_name(f"{rid}.md").write_text(summary, encoding="utf-8")
        except Exception:              # noqa: BLE001
            pass
        self._meta.pop("recording", None)
        self._meta["recorded"] = {"id": rid, "envelopes": self._rec_count}

    def _note_error(self, detail: str, *, fatal: bool = False) -> None:
        self.error = detail
        if fatal:
            self.state = "崩溃"

    # ---- 帧源接口（与 JsonlSource / OfflineSession 同形）----

    def info(self) -> SourceInfo:
        with self._lock:
            times = [f["game_time"] for f in self.frames] or [0.0]
            return SourceInfo(
                id=self.id, label=self.label, kind="live",
                envelopes=len(self.frames), from_time=min(times), to_time=max(times),
                topics=sorted({f["topic"] for f in self.frames}), snapshots=[])

    def statics(self) -> list[dict]:
        with self._lock:
            return [f for f in self._statics if f["topic"] in STATIC_TOPICS]

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        with self._lock:
            chosen: dict[str, dict] = {}
            for f in self.frames:
                if topics is not None and f["topic"] not in topics:
                    continue
                if f["game_time"] <= game_time + 1e-9:
                    chosen[f["topic"]] = f
            for f in self._statics:
                if topics is not None and f["topic"] not in topics:
                    continue
                chosen.setdefault(f["topic"], f)
            return list(chosen.values())

    def between(self, after: float, until: float,
                topics: set[str] | None = None) -> list[dict]:
        with self._lock:
            return [f for f in self.frames
                    if after + 1e-9 < f["game_time"] <= until + 1e-9
                    and (topics is None or f["topic"] in topics)]

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
                 index: int | None = None, order: list[int] | None = None) -> dict:
        from view.proposals import item_to_json

        if op not in QUEUE_OPS:
            raise ValueError(f"未知队列 op {op!r}（{'|'.join(sorted(QUEUE_OPS))}）")
        self._send({
            "op": "queue", "kind": op, "name": name,
            "items": [item_to_json(i) for i in (items or [])],
            "index": index, "order": order,
        })
        # 命令在**下一个帧边界**生效，所以这里回报的是"已送达"而不是"已生效"。
        # 前端据此显示 pending，等下一帧的 frame/production 确认。
        # B7 shape 统一：与 OfflineSession 同键 —— items = 这条命令携带的项数
        #（真身在子进程里，父进程只有帧；剩余长度等下一帧看 frame/production）。
        return {"queue": name, "items": len(items or []), "accepted_seq": self.seq}

    def set_worker_target(self, task: str, count: int) -> dict:
        self._send({"op": "workers", "task": task, "count": int(count)})
        return {"task": task, "quota": int(count), "accepted_seq": self.seq}

    def swap_strategy(self, strategy_file: str) -> dict:
        """热切 V1（批 C）：把 swap 命令发进子进程通道（stdin / sc2 走控制文件）。

        帧边界应用；约束校验在子进程侧跑（引擎 swap_strategy 先校验后变更），
        失败 → error 控制行 → `_note_error`，会话继续跑旧策略。
        """
        self._send({"op": "swap", "strategy": str(strategy_file)})
        return {"swap": "dispatched", "strategy": Path(strategy_file).stem,
                "accepted_seq": self.seq}

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

    def _kill_tree(self) -> None:
        """杀掉**整棵进程树**（run_session + 它启的 SC2）。

        真机欠账 §10.3 的根修：`proc.kill()` 只杀直接子进程，SC2 是孙进程 ——
        子进程死了它变孤儿，留在桌面上的就是那些黑屏窗口。
        - Windows：`taskkill /T /F`（/T = 整棵树）
        - POSIX：进程组 SIGKILL（Popen 里没设 start_new_session，退化为只杀子进程 ——
          本项目主要跑 Windows，POSIX 路径是尽力而为）
        进程已遇时 taskkill 会报错，静默即可（幂等）。
        """
        if self.proc.poll() is not None and not _pid_has_children(self.proc.pid):
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                    capture_output=True, timeout=10, check=False,
                )
            else:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self.proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass

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
        self._kill_tree()
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
                "pid": self.proc.pid, "alive": self.proc.poll() is None,
                "meta": {k: v for k, v in self._meta.items() if k != "stderr_tail"},
                "queues": [],   # 队列由子进程持有；UI 从 frame/production 看（单一真相源）
            }


#: 种族中文名（复盘清单「人族 vs 神族」用；C4：zh 文案来自后端）
_RACE_ZH = {"terran": "人族", "protoss": "神族", "zerg": "虫族"}


def _races_from_frames(frames: list[dict]) -> tuple[str | None, str | None]:
    """从帧流推 (我方, 敌方) 族中文名。

    优先读最后一帧 frame/session（run_session 从首个可见敌方单位推导后写进去）；
    会话帧没有（比如一局没见过敌人）再退 frame/world 的敌方单位 stable id 前缀。
    都推不出 = None（前端清单该段显示「—」）。
    """
    for f in reversed(frames):
        if f.get("topic") == "frame/session":
            p = f.get("payload") or {}
            my = _RACE_ZH.get(p.get("my_race") or "")
            enemy = _RACE_ZH.get(p.get("enemy_race") or "")
            if my or enemy:
                return (my or None, enemy or None)
            break
    enemy = None
    for f in reversed(frames):
        if f.get("topic") != "frame/world":
            continue
        for u in (f.get("payload") or {}).get("units") or []:
            sid = str(u.get("stable_id") or "")
            if sid.startswith(("terran/", "protoss/", "zerg/")) and u.get("owner") == "enemy":
                enemy = _RACE_ZH.get(sid.split("/", 1)[0])
                if enemy:
                    return ("人族", enemy)
        break
    return ("人族" if any(f.get("topic") == "frame/world" for f in frames) else None, enemy)


def _pid_has_children(pid: int) -> bool:
    """该 pid 是否还有活着的子进程（决定 _kill_tree 是否还有活可干）。

    Windows 上枚举父子关系要 WMI，太重；简化为：只要目标进程还活着就交给 taskkill /T
    （它对无子进程的 pid 是无害的 no-op），已死则直接跳过。POSIX 同理只看进程本身。
    """
    if os.name == "nt":
        # taskkill /T 对已退出的 pid 只是报错破锁；直接返回 True 走 taskkill 兜底
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False