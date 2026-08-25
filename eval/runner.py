"""eval.runner：轻管线两个 runner（SingleRound / FakeFollow 假 live）。

**SingleRoundRunner 偏离说明**：PLAN 写的是 `Engine.start`——但那走 vendor 的
`build_system_prompt`（带 done 行），与 AgentTalk 实际用的 `_system_prompt` 组装
不同。评测的第一原则是**测 agent 真看到的提示词**（D16），所以直接复用
`AgentTalk.say`：与用户对话同回路、同提示词组装、同 trace 落盘。

**FakeFollowRunner（⑦，D4）**：假 live——后台线程用 `/api/session/tick` 推进
离线世界（1 tick ≈ 1 游戏秒）、到 horizon 后 `/api/session/stop` 结束对局，
AgentTalk 的跟随循环看到「游戏已结束」自然收轮（终局总结走它自己的路径）。
**同一回路不同驱动**：talk.py 零改动，游戏推进从外面注入 —— 接口与真机
LiveFollowRunner（⑧）一致，届时只换驱动源。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from agent.talk import AgentTalk

from eval.result import RunResult, extract_result

TARGET = "advisor#1"


def _talk(world: dict, llm_factory: Callable[[], object], run_dir: Path,
          max_turns: int, mount_readonly: bool) -> AgentTalk:
    return AgentTalk(
        world["api"], llm_factory=llm_factory,
        trace_root=run_dir / "traces",
        workspace_root=world["workspace"],
        target=TARGET,
        max_turns=max_turns,
        history_path=run_dir / "history.json",
        **(_readonly_areas() if mount_readonly else {}),
    )


def _readonly_areas() -> dict:
    from api.app import DEFAULT_MAP_PLANS_DIR
    root = Path(__file__).resolve().parent.parent / "runtime"
    return {
        "recordings_dir": root / "recordings",
        "map_plans_dir": DEFAULT_MAP_PLANS_DIR,
    }


def _finish(world: dict, prompt_text: str, outcome, run_dir: Path,
            duration_s: float) -> RunResult:
    """共同收尾：提示词快照（D16）+ trace 提取 + 提案/会话终态。"""
    proposals = world["client"].get("/api/proposals").json()
    session = _session_end(world)
    return extract_result(
        run_dir / "traces", TARGET,
        talk_outcome=outcome if isinstance(outcome, dict) else {},
        run_no=0,
        workspace_root=world["workspace"],
        seed_hash=world.get("seed_hash", ""),
        proposals=list(proposals),
        session=session,
        duration_s=duration_s,
        prompt_text=prompt_text or None,
    )


def _session_end(world: dict) -> dict | None:
    # 假 live：stop 端点会把 app.state.session 置 None（之后 GET 只剩「未连接」），
    # 终态由驱动线程在 stop 前落进 extras —— 文件/快照优先于事后查询
    preset = (world.get("extras") or {}).get("session_end")
    if preset is not None:
        return preset
    info = world["client"].get("/api/session").json()
    if info.get("state") == "未连接":
        return None
    return {"state": info.get("state"), "game_time": info.get("game_time"),
            "alive": info.get("state") not in ("已结束", "未连接")}


class SingleRoundRunner:
    """一轮对话（say）跑完即收 —— 轻管线的执行单元。"""

    name = "single_round"

    def __init__(self, max_turns: int = 24, mount_readonly: bool = False) -> None:
        self.max_turns = max_turns
        self.mount_readonly = mount_readonly

    async def run(self, world: dict, task, llm_factory: Callable[[], object],
                  run_dir: Path) -> RunResult:
        talk = _talk(world, llm_factory, run_dir,
                     task.max_turns or self.max_turns, self.mount_readonly)
        t0 = time.perf_counter()
        outcome = await talk.say(task.text)
        prompt = await talk.prompt_snapshot()
        return _finish(world, prompt, outcome, run_dir, time.perf_counter() - t0)


class FakeFollowRunner:
    """假 live（⑦）：离线世界后台推进 + AgentTalk 全跟随回路（D4：同回路换驱动）。

    horizon_game/tick_interval：推到多少游戏秒停局、墙钟多久一 tick。
    min_wall：**到点后至少等这么多墙钟秒才停**——真 LLM 一轮要几十秒，游戏若在
    agent 开口前就结束，停局会把 proposals.session 置 None，之后的提案校验
    「不在队列里」全崩（冒烟实测的竞态）。给 agent 留出至少一轮的活局窗口。
    会话在 fixture 里以 autotick=false 起（推进完全由本 runner 驱动，可控可复现）。
    """

    name = "fake_live"

    def __init__(self, max_turns: int = 200, horizon_game: float = 120.0,
                 tick_interval: float = 0.1, min_wall: float = 90.0,
                 mount_readonly: bool = False) -> None:
        self.max_turns = max_turns
        self.horizon_game = horizon_game
        self.tick_interval = tick_interval
        self.min_wall = min_wall
        self.mount_readonly = mount_readonly

    async def run(self, world: dict, task, llm_factory: Callable[[], object],
                  run_dir: Path) -> RunResult:
        client = world["client"]
        stop = threading.Event()
        t0 = time.perf_counter()

        def drive() -> None:
            while not stop.is_set():
                info = client.get("/api/session").json()
                t = float(info.get("game_time") or 0.0)
                wall = time.perf_counter() - t0
                if ((t >= self.horizon_game and wall >= self.min_wall)
                        or info.get("state") in ("已结束", "未连接")):
                    # stop 端点会把 session 置 None —— 终态先落 extras（_session_end 消费）
                    world.setdefault("extras", {})["session_end"] = {
                        "state": "已结束", "game_time": t, "alive": False}
                    client.post("/api/session/stop")
                    return
                client.post("/api/session/tick", params={"count": 1})
                time.sleep(self.tick_interval)

        ticker = threading.Thread(target=drive, daemon=True, name="eval-fake-live")
        ticker.start()
        talk = _talk(world, llm_factory, run_dir,
                     task.max_turns or self.max_turns, self.mount_readonly)
        t0 = time.perf_counter()
        try:
            outcome = await talk.say(task.text)
            prompt = await talk.prompt_snapshot()
        finally:
            stop.set()
        return _finish(world, prompt, outcome, run_dir, time.perf_counter() - t0)
