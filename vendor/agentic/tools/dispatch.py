"""dispatch / status / wait 工具闭包:master agent 用它派子 agent + 感知 + 汇合。

create 时造,capture (engine, caller[, version])。agent 上下文无知(闭包 bake 进去)。
异步模型:dispatch 立即返(后台跑),status 非阻塞查,wait 挂起到 some/any 完成后返 workdir。
要并行派多个:LLM 在一条回复里发多个 dispatch tool_call 即可(不需要单独的 batch 工具)。
"""
from __future__ import annotations

from ..types import Tool


def _report_path(workdir: str | None) -> str | None:
    """子 agent 产出约定:<workdir>report.md。主 agent(workdir=None)无。"""
    return f"{workdir}report.md" if workdir else None


def _fmt(s) -> str:
    """TaskStatus → 一行文本(done 时优先返回调 result,否则给 report 路径)。"""
    parts = [f"{s.target}:{s.state}"]
    if s.state == "done":
        if s.result:
            parts.append(f"result: {s.result}")        # 子调了 done(result) → 回调
        else:
            rp = _report_path(s.workdir)
            if rp:
                parts.append(f"→ read {rp}")           # 否则读其 report 文件
    elif s.summary:
        parts.append(f"({s.summary})")
    return " ".join(parts)


def make_dispatch_tool(engine, caller, version: str) -> Tool:
    async def function(args: dict) -> str:
        contract = None
        contract_args = args.get("contract")
        if contract_args:
            from ..contract import WorkContract
            contract = WorkContract(
                task=contract_args.get("task", ""),
                working_directory=contract_args.get("working_directory", ""),
                output_directory=contract_args.get("output_directory", ""),
                input_paths=contract_args.get("input_paths", []),
                guidelines=contract_args.get("guidelines", ""),
            )
        r = await engine.dispatch(
            args["type"], args["target"], args.get("task", ""),
            caller=caller, version=version, grant=args.get("grant"), contract=contract,
        )
        wd = f", workdir {r.workdir}" if r.workdir else ""
        return f"dispatched {r.target} ({r.state}{wd})"

    return Tool(
        name="dispatch",
        description=(
            "Dispatch a sub-agent to run in the background (non-blocking). It works in its own "
            "writable region (agents/<target>/) of the shared workspace; when done, read "
            "<workdir>report.md. To run several in parallel, call dispatch multiple times in one "
            "reply. args: type (callee type_key), target (business key), task (instruction), "
            "grant (optional list of extra paths to grant the sub exclusive write access), "
            "contract (optional file contract: task/working_directory/output_directory/input_paths/guidelines)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "target": {"type": "string"},
                "task": {"type": "string"},
                "grant": {
                    "type": "array", "items": {"type": "string"},
                    "description": "extra workspace paths to grant the sub exclusive write access (beyond its region)",
                },
                "contract": {
                    "type": "object",
                    "description": "optional file contract for the sub-agent; omit to use the sub-agent spec's own contract",
                    "properties": {
                        "task": {"type": "string"},
                        "working_directory": {"type": "string"},
                        "output_directory": {"type": "string"},
                        "input_paths": {"type": "array", "items": {"type": "string"}},
                        "guidelines": {"type": "string"},
                    },
                },
            },
            "required": ["type", "target"],
        },
        function=function,
    )


def make_status_tool(engine, caller) -> Tool:
    async def function(args: dict) -> str:
        targets = args.get("targets")
        statuses = engine.status(caller, targets)
        if not statuses:
            return "(no dispatched agents)"
        return "status:\n" + "\n".join(f"- {_fmt(s)}" for s in statuses)

    return Tool(
        name="status",
        description=(
            "非阻塞查看你派出去的子 agent 当前状态(running/done/paused/error),done 时给出 report 路径(<workdir>report.md)。"
            "targets 不填或留空 = 返回所有你派出去的子 agent;填了只看指定的。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "targets": {"type": "array", "items": {"type": "string"}, "description": "可选;不填或留空 = 所有你派出去的子 agent"},
            },
        },
        function=function,
    )


def make_wait_tool(engine, caller) -> Tool:
    async def function(args: dict) -> str:
        targets = args.get("targets")
        mode = args.get("mode", "all")
        statuses = await engine.wait(caller, targets, mode=mode)
        if not statuses:
            return "(no agents to wait for)"
        return f"wait ({mode}): " + "; ".join(_fmt(s) for s in statuses)

    return Tool(
        name="wait",
        description=(
            "挂起自己,直到 some(mode='any') 或 all(默认) 子 agent 完成,唤醒一次返回各 agent 状态 + 回调/产出。"
            "targets 不填或留空 = 等待所有你派出去的子 agent;mode='all'(默认)/'any'。"
            "完成的子若调了 done(result) 会带回调结果;否则给出其 report 路径供 read。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "targets": {"type": "array", "items": {"type": "string"}, "description": "可选;不填或留空 = 所有你派出去的子 agent"},
                "mode": {"type": "string", "description": "'all' (默认) 或 'any'"},
            },
        },
        function=function,
    )


def make_done_tool(agent) -> Tool:
    """完成工具(普通声明工具,ADR-0001):agent 调它表示完成 + 提交 result。

    function 把 agent.state.status 设 'done'、state.result 设 args['result'];
    runner 每轮跑完看 state.status → before_finalize → 收尾(不再按名拦截 done)。
    子 agent 的 result 经 wait/status 回调给父;主 agent 的 result 经 start 返给调用方。
    """
    async def function(args: dict) -> str:
        agent.state.status = "done"
        agent.state.result = args.get("result")
        return ""  # 不会被看到——loop 即将结束

    return Tool(
        name="done",
        description=(
            "完成当前任务并提交结果。调用此工具即表示你已完成;result 是你的产出/回调,会发给父 agent(或调用方)。"
            "父 agent 在 wait 后可直接拿到你的 result,无需 read 文件。大输出也可先写文件,再在 result 里指路径。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "你的完成结果/回调报告(给父 agent 或调用方)"},
            },
        },
        function=function,
    )
