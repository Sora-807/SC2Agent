"""Tracer:agent 运行的结构化记录 sink。

目录结构(按启动时间):
  <root>/<run_id>/
    run.meta.json
    summary.json
    tree.json
    trace.md                  # 人类可读时间线(finalize 时生成)
    trace.html                # 自包含可视化(finalize 时自动生成)
    agents/<id>.jsonl         # per-agent 事件流
    agents/<id>.messages.jsonl # per-agent 全量对话
    prompts/<id>.<name>.json
    checkpoints/<id>.<seq>.ckpt.json
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events
from .timeline import render_timeline

BEIJING_TZ = timezone(timedelta(hours=8))


def timestamp() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="milliseconds")


def _merge_tool_counts(maps) -> dict[str, int]:
    """合并多个 {tool: count} 字典。"""
    merged: dict[str, int] = {}
    for mapping in maps:
        for tool_name, count in (mapping or {}).items():
            merged[tool_name] = merged.get(tool_name, 0) + count
    return merged


class Tracer:
    def __init__(self, root: str | Path = "traces", *, run_id: str | None = None) -> None:
        self.root = Path(root)
        if run_id is None:
            stamp = datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H%M%S")
            run_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        self.run_id = run_id
        self.trace_dir = self.root / self.run_id
        (self.trace_dir / "agents").mkdir(parents=True, exist_ok=True)
        (self.trace_dir / "prompts").mkdir(exist_ok=True)
        (self.trace_dir / "checkpoints").mkdir(exist_ok=True)
        (self.trace_dir / "workspace_snapshots").mkdir(exist_ok=True)
        self._started_at = timestamp()
        self._event_sequences: dict[str, int] = {}
        self._message_sequences: dict[str, int] = {}
        self._checkpoint_sequences: dict[str, int] = {}
        self._edges: list[dict] = []
        self._write_meta()

    def _trace_path(self, agent_id: str) -> Path:
        return self.trace_dir / "agents" / f"{agent_id}.jsonl"

    def _messages_path(self, agent_id: str) -> Path:
        return self.trace_dir / "agents" / f"{agent_id}.messages.jsonl"

    def _write_meta(self, *, ended: bool = False) -> None:
        meta = {"run_id": self.run_id, "started_at": self._started_at}
        if ended:
            meta["ended_at"] = timestamp()
        (self.trace_dir / "run.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def now(self) -> str:
        return timestamp()

    # ---- 事件与消息 ----
    def event(self, agent_id: str, type: str, **payload) -> None:
        """追加一条事件到 agents/<agent_id>.jsonl;type 必须在词汇表中。"""
        if type not in events.ALL_TYPES:
            raise ValueError(f"unknown trace event type {type!r}; see agentic/trace/events.py")
        self._event_sequences[agent_id] = self._event_sequences.get(agent_id, 0) + 1
        record = {
            events.TIMESTAMP: self.now(),
            events.AGENT_ID: agent_id,
            events.SEQUENCE: self._event_sequences[agent_id],
            "type": type,
        }
        record.update(payload)
        with open(self._trace_path(agent_id), "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def dispatch(self, caller_target: str, callee_target: str, callee_type: str) -> None:
        """记录父子派发边 + 发 dispatch 事件。"""
        self._edges.append({"parent": caller_target, "child": callee_target, "callee_type": callee_type})
        self.event(caller_target, events.DISPATCH,
                   caller_target=caller_target, target=callee_target, callee_type=callee_type)

    def log_message(self, agent_id: str, message: dict) -> int:
        """追加一条消息到全量对话日志,返回其 idx。"""
        idx = self._message_sequences.get(agent_id, 0)
        self._message_sequences[agent_id] = idx + 1
        record = {"idx": idx, "ts": self.now()}
        record.update(message)
        with open(self._messages_path(agent_id), "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return idx

    def message_count(self, agent_id: str) -> int:
        return self._message_sequences.get(agent_id, 0)

    def save_blob(self, agent_id: str, name: str, content: str) -> str:
        """大字段全文落 prompts/;返回相对 path 作为引用。"""
        path = self.trace_dir / "prompts" / f"{agent_id}.{name}.json"
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.trace_dir)).replace("\\", "/")

    def save_workspace_snapshot(self, name: str, files: dict[str, str]) -> str:
        """保存工作区快照 {path: content} 到 workspace_snapshots/<name>.json。"""
        path = self.trace_dir / "workspace_snapshots" / f"{name}.json"
        path.write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.relative_to(self.trace_dir)).replace("\\", "/")

    # ---- checkpoint ----
    def checkpoint(self, agent_id: str, state: dict) -> str:
        self._checkpoint_sequences[agent_id] = self._checkpoint_sequences.get(agent_id, 0) + 1
        sequence = self._checkpoint_sequences[agent_id]
        path = self.trace_dir / "checkpoints" / f"{agent_id}.{sequence}.ckpt.json"
        path.write_text(json.dumps(state, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        return str(path.relative_to(self.trace_dir)).replace("\\", "/")

    def load_checkpoint(self, reference: str) -> dict:
        return json.loads((self.trace_dir / reference).read_text(encoding="utf-8"))

    # ---- 读取 ----
    def read_events(self, agent_id: str) -> list[dict]:
        path = self._trace_path(agent_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def read_messages(self, agent_id: str) -> list[dict]:
        path = self._messages_path(agent_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    # ---- 聚合 ----
    def finalize_summary(self) -> dict:
        """聚合 trace → summary.json + tree.json + trace.md + trace.html,并更新 run.meta。"""
        agents: dict[str, dict] = {}
        agent_meta: dict[str, dict] = {}
        for file in (self.trace_dir / "agents").glob("*.jsonl"):
            if file.name.endswith(".messages.jsonl"):
                continue
            agent_id = file.stem
            summary = {"input_tokens": 0, "output_tokens": 0, "cached_input": 0,
                       "uncached_input": 0, "turns": 0, "tool_calls": 0, "tools": {},
                       "tool_failures": 0, "failed_tools": {}}
            for event in self.read_events(agent_id):
                event_type = event.get("type")
                if event_type == events.RUN_START:
                    agent_meta[agent_id] = {
                        "type_key": event.get(events.TYPE_KEY),
                        "caller_target": event.get(events.CALLER_TARGET),
                    }
                elif event_type == events.TURN_END:
                    summary["turns"] += 1
                elif event_type == events.LLM_CALL:
                    input_tokens = int(event.get(events.INPUT_TOKENS, 0) or 0)
                    cached = int(event.get(events.CACHED_TOKENS, 0) or 0)
                    summary["input_tokens"] += input_tokens
                    summary["output_tokens"] += int(event.get(events.OUTPUT_TOKENS, 0) or 0)
                    summary["cached_input"] += cached
                    summary["uncached_input"] += max(0, input_tokens - cached)
                elif event_type == events.TOOL_CALL:
                    summary["tool_calls"] += 1
                    tool_name = event.get(events.TOOL, "?")
                    summary["tools"][tool_name] = summary["tools"].get(tool_name, 0) + 1
                    if (event.get(events.RESULT_PREVIEW) or "").startswith("error:"):
                        summary["tool_failures"] += 1
                        summary["failed_tools"][tool_name] = summary["failed_tools"].get(tool_name, 0) + 1
            agents[agent_id] = summary

        run_total = {
            "input_tokens": sum(agent["input_tokens"] for agent in agents.values()),
            "output_tokens": sum(agent["output_tokens"] for agent in agents.values()),
            "cached_input": sum(agent["cached_input"] for agent in agents.values()),
            "uncached_input": sum(agent["uncached_input"] for agent in agents.values()),
            "turns": sum(agent["turns"] for agent in agents.values()),
            "tool_calls": sum(agent["tool_calls"] for agent in agents.values()),
            "tool_failures": sum(agent["tool_failures"] for agent in agents.values()),
            "failed_tools": _merge_tool_counts(agent["failed_tools"] for agent in agents.values()),
        }
        agent_list = [{"target": agent_id, **meta} for agent_id, meta in agent_meta.items()]
        summary = {"run_id": self.run_id, "run": run_total, "agents": agents, "agent_list": agent_list}
        (self.trace_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        parents = {edge["parent"] for edge in self._edges}
        children = {edge["child"] for edge in self._edges}
        roots = sorted(parents - children)
        tree = {"roots": roots, "edges": self._edges}
        (self.trace_dir / "tree.json").write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        (self.trace_dir / "trace.md").write_text(render_timeline(self.trace_dir), encoding="utf-8")
        self._write_meta(ended=True)

        # 自动生成 HTML(render 在函数内导入,避免循环依赖)
        from .render import render_trace_html
        render_trace_html(self.trace_dir)
        return summary
