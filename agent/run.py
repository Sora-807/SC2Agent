"""agent.run：跑一个顾问回合。

用法：
    uv run python -m agent.run                 # 连默认 api，用 .env 里的 LLM
    uv run python -m agent.run --dry           # 不打网络（FakeLLM 走一遍链路）
    uv run python -m agent.run --source opening --task "复盘这段开局"

它是**一个回合**而不是常驻循环：常驻应由会话节拍驱动（B3 之后），
而"每隔几秒问一次 LLM"在真机上既贵又没必要 —— 局面变化是事件驱动的。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT / "vendor", ROOT / "modules", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from agentic import DiskWorkspace, Engine, Tracer  # noqa: E402
from agentic.llm import FakeLLMClient, OpenAIClient  # noqa: E402
from agentic.types import LLMResponse, Message, ToolCall  # noqa: E402

from agent.client import ApiClient  # noqa: E402
from agent.spec import AdvisorSpec  # noqa: E402

DEFAULT_TASK = "看一眼当前局面，找出最值得修的一件生产问题，提一条草稿提案。"


def fake_llm() -> FakeLLMClient:
    """不打网络的最短路径：observe → propose → done。

    它验的是**装配**（工具能不能被调用、提案能不能落地），不是 LLM 的判断力。
    """
    return FakeLLMClient([
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("c1", "observe", {})]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("c2", "propose", {
                "kind": "production_queue",
                "title_zh": "补一个精炼厂解开缺气的队首",
                "rationale_zh": "队首在等气，而队列里没有气的来源；先插精炼厂，否则整队一直冻结。",
                "target": {"queue": "main"},
                "hunks": [{"id": "h1", "kind": "insert", "text_zh": "在队首插入精炼厂",
                           "payload": {"index": 0,
                                       "item": {"op": "build", "type": "terran/refinery",
                                                "count": 1}}}],
            })]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("c3", "done", {
                "result": "提了一条：队首插精炼厂，解开缺气阻塞。"})]), 0, 0, "fake"),
    ])


async def run_round(*, base: str, task: str, dry: bool, source: str,
                    trace_root: Path, workspace_root: Path, max_turns: int = 8) -> dict:
    client = ApiClient(base=base)
    llm = fake_llm() if dry else OpenAIClient.from_env()
    engine = Engine(
        llm, Tracer(trace_root),
        # 物理隔离的工作区：agent 只能在这个目录里写笔记，碰不到仓库其它东西。
        # R5「live 中不能创建/编辑模块与 Strategy」因此是机制保证，不是提示词保证。
        workspace=DiskWorkspace(workspace_root),
        max_turns=max_turns,
    )
    engine.register("advisor", AdvisorSpec(client, source=source), entry=True)
    status = await engine.start("advisor#1", task)
    return {
        "state": getattr(status, "status", None),
        "result": getattr(status, "result", None),
        "proposals": [
            {"id": p["id"], "status": p["status"], "title": p["title_zh"],
             "valid": (p.get("validation") or {}).get("ok")}
            for p in client.proposals()
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="跑一个 SC2 生产顾问回合")
    ap.add_argument("--base", default="http://127.0.0.1:8770")
    ap.add_argument("--task", default=DEFAULT_TASK)
    ap.add_argument("--source", default="live", help="观察哪个帧源（live 或某个复盘 id）")
    ap.add_argument("--dry", action="store_true", help="不打网络，用 FakeLLM 走一遍链路")
    ap.add_argument("--traces", default=str(ROOT / "traces"))
    ap.add_argument("--workspace", default=str(ROOT / "runtime" / "agent-workspace"))
    args = ap.parse_args()

    out = asyncio.run(run_round(
        base=args.base, task=args.task, dry=args.dry, source=args.source,
        trace_root=Path(args.traces), workspace_root=Path(args.workspace)))
    print(f"回合结束：{out['state']}")
    if out["result"]:
        print(f"结论：{out['result']}")
    print("提案：")
    for p in out["proposals"]:
        print(f"  {p['id']} [{p['status']}] {'✓' if p['valid'] else '✗校验未通过'} {p['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())