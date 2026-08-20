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
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from view.schema import STATIC_TOPICS

from api.session import MAX_STALE_SEQ, StaleObservation
from api.sources import SourceInfo

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_session.py"

#: 内存里保留的帧数（够时间线回看最近一段；完整历史靠 ViewRecorder 落盘）
FRAME_BUFFER = 6000
#: 停止时等子进程自己退出的秒数，超时就 kill
STOP_GRACE = 5.0


class LiveSession:
    """子进程会话。`driver="sim"` 用假世界（能在没有 SC2 的环境里验进程分离），
    `driver="sc2"` 是真机。两者在子进程里走**完全同一条**产帧与命令路径。"""

    id = "live"

    def __init__(self, *, driver: str = "sim", map_name: str = "LadderMap",
                 seconds: float = 600.0, realtime: bool = False,
                 tick_seconds: float = 0.25,
                 label: str | None = None, python: str | None = None) -> None:
        self.driver = driver
        self.label = label or (f"真机会话（{map_name}）" if driver == "sc2"
                               else "子进程沙盒（假世界，验进程分离）")
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

        cmd = [
            python or sys.executable, "-X", "utf8", str(RUNNER),
            "--driver", driver, "--map", map_name, "--seconds", str(seconds),
            "--tick-seconds", str(tick_seconds),
        ]
        if realtime:
            cmd.append("--realtime")
        self.proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
                # 子进程往 stdout 写了非 JSON（比如某个库的 print）→ 记下来，不当帧
                self._note_error(f"子进程 stdout 出现非 JSON：{line[:160]}")
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
                self._meta["terrain"] = obj.get("terrain")
            elif kind == "projection":
                slot = self._pending.get(int(obj.get("id") or -1))
                if slot is not None:
                    slot[1] = obj
                    slot[0].set()
            elif kind == "bye":
                if self.state != "崩溃":
                    self.state = "已结束"
                self._meta["bye"] = obj.get("reason")

    def _frame(self, frame: dict) -> None:
        with self._lock:
            if frame.get("topic") in STATIC_TOPICS:
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
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def queue_op(self, op: str, name: str, *, items: list | None = None,
                 index: int | None = None, order: list[int] | None = None) -> dict:
        from view.proposals import item_to_json

        if op not in ("submit", "append", "prepend", "clear", "remove", "reorder"):
            raise ValueError(f"未知队列 op {op!r}（submit|append|prepend|clear|remove|reorder）")
        self._send({
            "op": "queue", "kind": op, "name": name,
            "items": [item_to_json(i) for i in (items or [])],
            "index": index, "order": order,
        })
        # 命令在**下一个帧边界**生效，所以这里回报的是"已送达"而不是"已生效"。
        # 前端据此显示 pending，等下一帧的 frame/production 确认。
        return {"queue": name, "dispatched": True, "accepted_seq": self.seq}

    def set_worker_target(self, task: str, count: int) -> dict:
        self._send({"op": "workers", "task": task, "count": int(count)})
        return {"task": task, "quota": int(count), "dispatched": True, "accepted_seq": self.seq}

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

    def stop(self) -> None:
        try:
            self._send({"op": "stop"})
        except (RuntimeError, OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            # 真机上 run_game 不一定听我们的 —— 到点就 kill，别把 api 拖住
            self.proc.kill()
        with self._lock:
            if self.state != "崩溃":
                self.state = "已结束"

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