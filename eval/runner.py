"""eval.runner：SingleRoundRunner（轻管线，PLAN §3.4 的有意偏离版）。

**偏离说明**：PLAN 写的是 `Engine.start`（agent/run.py 同款）——但那走 vendor 的
`build_system_prompt`（带 done 行），与 AgentTalk 实际用的 `_system_prompt` 组装
不同。评测的第一原则是**测 agent 真看到的提示词**（D16），所以这里直接复用
`AgentTalk.say`：与用户对话同回路、同提示词组装、同 trace 落盘。D4 的约束
（「接口跟真的一样」）在同族的最轻 runner 上同样成立。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from agent.talk import AgentTalk

from eval.result import RunResult, extract_result

TARGET = "advisor#1"


class SingleRoundRunner:
    """一轮对话（say）跑完即收 —— 轻管线的执行单元。"""

    name = "single_round"

    def __init__(self, max_turns: int = 24, mount_readonly: bool = False) -> None:
        #: max_turns：轻管线一轮的刹车（对局跟随是重管线的事）
        self.max_turns = max_turns
        #: 挂真实只读区（runtime/recordings、默认 map-plans）——planning/map 场景
        #: 开（agent 的文件树面与真机一致）；live 提案场景默认关（隔离）。
        self.mount_readonly = mount_readonly

    async def run(self, world: dict, task, llm_factory: Callable[[], object],
                  run_dir: Path) -> RunResult:
        talk = AgentTalk(
            world["api"], llm_factory=llm_factory,
            trace_root=run_dir / "traces",
            workspace_root=world["workspace"],
            target=TARGET,
            max_turns=task.max_turns or self.max_turns,
            history_path=run_dir / "history.json",
            **(self._readonly_areas() if self.mount_readonly else {}),
        )
        t0 = time.perf_counter()
        outcome = await talk.say(task.text)
        duration = time.perf_counter() - t0
        # D16：trace 的 messages.jsonl 在 AgentTalk 路径不含 system 消息 ——
        # 提示词快照从 talk 本体取（同一 agent 实例重新组装，与轮内预种同源）
        prompt_text = await talk.prompt_snapshot()

        proposals = world["client"].get("/api/proposals").json()
        session = self._session_end(world)
        return extract_result(
            run_dir / "traces", TARGET,
            talk_outcome=outcome if isinstance(outcome, dict) else {},
            run_no=0,
            workspace_root=world["workspace"],
            seed_hash=world.get("seed_hash", ""),
            proposals=list(proposals),
            session=session,
            duration_s=duration,
            prompt_text=prompt_text or None,
        )

    def _readonly_areas(self) -> dict:
        from api.app import DEFAULT_MAP_PLANS_DIR
        root = Path(__file__).resolve().parent.parent / "runtime"
        return {
            "recordings_dir": root / "recordings",
            "map_plans_dir": DEFAULT_MAP_PLANS_DIR,
        }

    @staticmethod
    def _session_end(world: dict) -> dict | None:
        sess = world.get("app").state.session if world.get("app") else None
        if sess is None:
            return None
        return {"game_time": sess.game_time, "alive": True}
