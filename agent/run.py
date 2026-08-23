"""agent.run：跑一个顾问回合。

用法：
    uv run python -m agent.run                              # 连默认 api，用 .env 里的 LLM
    uv run python -m agent.run --dry                        # 不打网络（FakeLLM：observe→propose）
    uv run python -m agent.run --dry --demo planning        # P3 验收链：读规划→改→试算→读警报
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

from agentic import Engine, Tracer  # noqa: E402
from agentic.llm import FakeLLMClient, OpenAIClient  # noqa: E402
from agentic.types import LLMResponse, Message, ToolCall  # noqa: E402

from agent.client import ApiClient  # noqa: E402
from agent.spec import AdvisorSpec  # noqa: E402
from agent.workspace import ApiWorkspace, ChangeLog  # noqa: E402

DEFAULT_TASK = "看一眼当前局面，找出最值得修的一件生产问题，提一条草稿提案。"
PLANNING_TASK = "读默认生产规划，复制一份把精炼厂提前，试算并报告曲线与前瞻警报。"


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


def planning_llm() -> FakeLLMClient:
    """文件工作流验收链（不打网络）：ls → read → write(新建规划文件) → simulate → done。

    规划域是 codeagent 语义：文件契约直改 plans/ 虚拟目录 + 干跑读警报。
    """
    return FakeLLMClient([
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("p1", "ls", {})]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("p2", "read", {"path": "plans/default.yaml"})]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("p3", "write", {"path": "plans/agent-demo.yaml", "content": (
                "id: agent-demo\n"
                "title_zh: agent 试算副本（精炼厂提前）\n"
                "map: LadderMap\n"
                "spawn: bl\n"
                "queue:\n"
                "- {op: build, type: terran/refinery, count: 1}\n"
                "- {op: assign_workers, task: gas, count: 3}\n"
                "- {op: build, type: terran/barracks, count: 2}\n"
                "- {op: build, type: terran/factory, count: 1}\n"
                "- {op: train, type: terran/marine, count: 6}\n"
            )})]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("p4", "simulate_plan", {"plan_id": "agent-demo",
                                             "horizon": 240})]), 0, 0, "fake"),
        LLMResponse(Message(role="assistant", content=None, tool_calls=[
            ToolCall("p5", "done", {
                "result": "新建 plans/agent-demo.yaml（精炼厂提前到队首）；干跑 240s "
                          "曲线与前瞻警报已读，缺气卡点消除情况见试算输出。"})]), 0, 0, "fake"),
    ])


async def run_round(*, base: str, task: str, dry: bool, source: str,
                    trace_root: Path, workspace_root: Path, max_turns: int = 8,
                    demo: str = "live", client: ApiClient | None = None) -> dict:
    if client is None:
        client = ApiClient(base=base)
    llm = (planning_llm() if demo == "planning" else fake_llm()) if dry \
        else OpenAIClient.from_env()
    tracer = Tracer(trace_root)
    changes = ChangeLog()
    engine = Engine(
        llm, tracer,
        # 虚拟文件工作区：plans/ + map-plans/ 走规划 API（写时校验），其余是 scratch。
        # R5「live 中不能创建/编辑模块与 Strategy」仍然是机制保证 —— 工作区外的东西碰不到。
        workspace=ApiWorkspace(client, workspace_root, changes),
        max_turns=max_turns,
    )
    engine.register("advisor", AdvisorSpec(client, source=source, changes=changes), entry=True)
    status = await engine.start("advisor#1", task)
    # BaseAgent 的自包含 HTML 可视化（trace.html）：Engine.start **不代收尾**，
    # finalize_summary 必须显式调 —— 不调就只有 jsonl 没有 html（2026-08-22 用户要求开启）。
    tracer.finalize_summary()
    return {
        "state": getattr(status, "status", None),
        "result": getattr(status, "result", None),
        "trace_html": str(tracer.trace_dir / "trace.html"),
        "changes": [c.to_json() for c in changes.drain()],
        "proposals": [
            {"id": p["id"], "status": p["status"], "title": p["title_zh"],
             "valid": (p.get("validation") or {}).get("ok")}
            for p in client.proposals()
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="跑一个 SC2 生产顾问回合")
    ap.add_argument("--base", default="http://127.0.0.1:8770")
    ap.add_argument("--task", default=None, help="任务描述（缺省按 demo 选内置任务）")
    ap.add_argument("--source", default="live", help="观察哪个帧源（live 或某个复盘 id）")
    ap.add_argument("--dry", action="store_true", help="不打网络，用 FakeLLM 走一遍链路")
    ap.add_argument("--demo", default="live", choices=["live", "planning"],
                    help="--dry 的演示链：live=observe→propose；planning=读规划→改→试算（P3）")
    ap.add_argument("--traces", default=str(ROOT / "traces"))
    ap.add_argument("--workspace", default=str(ROOT / "runtime" / "agent-workspace"))
    args = ap.parse_args()
    task = args.task or (PLANNING_TASK if args.demo == "planning" else DEFAULT_TASK)

    out = asyncio.run(run_round(
        base=args.base, task=task, dry=args.dry, source=args.source,
        trace_root=Path(args.traces), workspace_root=Path(args.workspace),
        demo=args.demo))
    print(f"回合结束：{out['state']}")
    if out["result"]:
        print(f"结论：{out['result']}")
    print(f"可视化：{out['trace_html']}")
    print("提案：")
    for p in out["proposals"]:
        print(f"  {p['id']} [{p['status']}] {'✓' if p['valid'] else '✗校验未通过'} {p['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())