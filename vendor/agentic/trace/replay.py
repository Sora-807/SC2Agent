"""根据 trace 回放工作区变动。

思路:初始快照 + 全部 agent 的 tool_call 事件(按时间排序) → 依次重放文件修改工具,
即可复原任意时刻的共享工作区。只重放结果不以 error: 开头的工具调用。

限制:
- 只覆盖内置文件工具(write/append/edit/insert/delete);read/ls/glob/grep/stat 不改变内容。
- 自定义工具若在工作区内产生副作用,需要工具自身记录,本模块无法推断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..workspace import MemoryWorkspace, WorkspaceConfig
from . import events

MUTATING_TOOLS = {"write", "append", "edit", "insert", "delete"}


@dataclass
class ReplayResult:
    """回放结果。"""
    files: dict[str, str]
    applied_mutations: list[dict] = field(default_factory=list)
    failed_mutations: list[dict] = field(default_factory=list)
    workspace_steps: list[dict] = field(default_factory=list)

    def compare(self, final_files: dict[str, str] | None) -> dict:
        """与最终快照对比,返回差异说明。"""
        if final_files is None:
            return {"final_provided": False}
        keys = sorted(set(self.files) | set(final_files))
        differences = []
        for path in keys:
            replayed = self.files.get(path)
            final = final_files.get(path)
            if replayed != final:
                differences.append({
                    "path": path,
                    "replayed": replayed,
                    "final": final,
                })
        return {"final_provided": True, "matches": not differences, "differences": differences}


def _ordered_tool_calls(agents_events: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    """把所有 agent 的工具调用事件按时间排序。"""
    calls: list[tuple[str, dict]] = []
    for agent, event_list in agents_events.items():
        for event in event_list:
            if event.get("type") != events.TOOL_CALL:
                continue
            if event.get("tool") not in MUTATING_TOOLS:
                continue
            calls.append((agent, event))
    calls.sort(key=lambda item: item[1].get("started_at") or item[1].get("ts") or "")
    return calls


def replay_workspace(
    initial_files: dict[str, str],
    agents_events: dict[str, list[dict]],
) -> ReplayResult:
    """从初始快照 + 事件流回放工作区。

    agents_events 形如 {agent_id: [event, ...]},可由 Tracer.read_events 得到。
    """
    workspace = MemoryWorkspace(WorkspaceConfig(require_read_before_write=False))
    for path, content in initial_files.items():
        workspace.add_document(path, content)

    result = ReplayResult(files={})
    for agent, event in _ordered_tool_calls(agents_events):
        tool_name = event.get("tool")
        arguments = event.get("args") or {}
        result_preview = event.get("result_preview") or ""
        if result_preview.startswith("error:"):
            continue  # 原执行已失败,不重放
        path = arguments.get("path")
        before = workspace.snapshot().get(path) if path else None
        try:
            if tool_name == "write":
                workspace.write_text(arguments["path"], arguments["content"])
            elif tool_name == "append":
                workspace.append_text(arguments["path"], arguments["content"])
            elif tool_name == "edit":
                workspace.edit_text(
                    arguments["path"],
                    arguments["old_string"],
                    arguments["new_string"],
                    bool(arguments.get("replace_all", False)),
                )
            elif tool_name == "insert":
                workspace.insert_text(
                    arguments["path"],
                    int(arguments["line_number"]),
                    arguments["content"],
                )
            elif tool_name == "delete":
                workspace.delete_lines(
                    arguments["path"],
                    int(arguments["start_line"]),
                    int(arguments["end_line"]),
                )
            else:
                continue
        except Exception as error:  # noqa: BLE001
            result.failed_mutations.append({
                "agent": agent,
                "tool": tool_name,
                "args": arguments,
                "error": str(error),
            })
            continue
        after = workspace.snapshot().get(path) if path else None
        step = {
            "index": len(result.workspace_steps),
            "agent": agent,
            "tool": tool_name,
            "args": arguments,
            "path": path,
            "before": before,
            "after": after,
            "started_at": event.get("started_at") or event.get("ts"),
            "event_sequence": event.get("seq"),
        }
        result.workspace_steps.append(step)
        result.applied_mutations.append({
            "agent": agent,
            "tool": tool_name,
            "args": arguments,
        })

    result.files = workspace.snapshot()
    return result


def load_initial_snapshot(trace_dir: str | Path) -> dict[str, str]:
    """读取 trace 目录中的初始工作区快照。"""
    import json
    path = Path(trace_dir) / "workspace_snapshots" / "initial.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_final_snapshot(trace_dir: str | Path) -> dict[str, str]:
    """读取 trace 目录中的最终工作区快照。"""
    import json
    path = Path(trace_dir) / "workspace_snapshots" / "final.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def replay_trace(trace_dir: str | Path) -> ReplayResult:
    """便捷入口:读 trace 目录,回放并返回结果。"""
    from .render import load_trace
    data = load_trace(trace_dir)
    agents_events = {agent: info["events"] for agent, info in data["agents"].items()}
    initial = load_initial_snapshot(trace_dir)
    return replay_workspace(initial, agents_events)
