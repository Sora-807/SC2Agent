"""api.live_io：LiveSession 的进程侧辅助（N3 从 live.py 抽出）。

围绕子进程与它的产物转的三件事：
- **对局录制**（RecordingMixin）：JSONL 帧流落盘的 setup 与收尾（meta 终态 +
  种族推断 + 衍生摘要）。Mixin 而不是独立对象：状态（_rec_fh/_rec_meta_path/
  _rec_count/_meta/frames/_statics）与宿主深度共享，独立对象要做回调接口，
  收益配不上手术（与 production.flights/ledger 同一拍板）。
- **进程树清理**（kill_tree）：Windows taskkill /T / POSIX 进程组。
- **帧流种族推断**（_races_from_frames）：复盘清单「人族 vs 神族」用。

宿主契约（LiveSession 提供）：`label` `driver` `frames` `_statics` `_meta`
`_rec_fh` `_rec_meta_path` `_rec_count` `game_time`。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path


class RecordingMixin:
    """对局录制：setup（__init__ 末尾调）+ 收尾（stop 时在锁内调）。"""

    def _init_recording(self, record_dir: Path | None, driver: str,
                        map_name: str, map_plan: str | None) -> None:
        """开录制（record_dir=None = 不录）。录不了不拦对局：帧流照跑，只是没文件。

        （二十六轮用户反馈「对局记录没保存」）：帧流同步落 JSONL，结束后复盘模式
        的下拉里出现「📹 录像」。内存 FRAME_BUFFER 会截老帧，文件才是完整历史。
        """
        if record_dir is None:
            return
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

    def _close_recording(self, final_state: str) -> None:
        """收尾录制：关文件 + 把 meta 标成终态（时长/帧数写进去，清单端点就不用扫文件）。
        调用方需持有宿主的 _lock（与 _frame 同一把，写序一致）。"""
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


def kill_tree(proc: subprocess.Popen) -> None:
    """杀掉**整棵进程树**（run_session + 它启的 SC2）。

    真机欠账 §10.3 的根修：`proc.kill()` 只杀直接子进程，SC2 是孙进程 ——
    子进程死了它变孤儿，留在桌面上的就是那些黑屏窗口。
    - Windows：`taskkill /T /F`（/T = 整棵树）
    - POSIX：进程组 SIGKILL（Popen 里没设 start_new_session，退化为只杀子进程 ——
      本项目主要跑 Windows，POSIX 路径是尽力而为）
    进程已遇时 taskkill 会报错，静默即可（幂等）。
    """
    if proc.poll() is not None and not _pid_has_children(proc.pid):
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=10, check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass


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
    """该 pid 是否还有活着的子进程（决定 kill_tree 是否还有活可干）。

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
