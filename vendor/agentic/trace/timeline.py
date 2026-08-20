"""trace.md 人类可读时间线生成。"""
from __future__ import annotations

import json
from pathlib import Path

from . import events


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def render_timeline(trace_dir: str | Path) -> str:
    """把整个 trace 目录渲染成 markdown 时间线。"""
    directory = Path(trace_dir)
    agents_dir = directory / "agents"
    lines: list[str] = ["# Trace Timeline", ""]
    meta_path = directory / "run.meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        lines.append(f"- run_id: {meta.get('run_id', '')}")
        lines.append(f"- 开始: {meta.get('started_at', '')}")
        lines.append(f"- 结束: {meta.get('ended_at', '')}")
        lines.append("")

    event_files = sorted(agents_dir.glob("*.jsonl")) if agents_dir.exists() else []
    for event_file in event_files:
        if event_file.name.endswith(".messages.jsonl"):
            continue
        agent_id = event_file.stem
        lines.append(f"## {agent_id}")
        lines.append("")
        for event in _read_jsonl(event_file):
            event_type = event.get("type")
            if event_type == events.RUN_START:
                lines.append(f"- {event.get(events.TIMESTAMP, '')} run_start type={event.get(events.TYPE_KEY, '')} target={event.get(events.TARGET, '')}")
            elif event_type == events.LLM_CALL:
                lines.append(
                    f"- {event.get(events.STARTED_AT, '')} llm_call turn={event.get(events.TURN_NO, '')} "
                    f"input={event.get(events.INPUT_TOKENS, 0)} output={event.get(events.OUTPUT_TOKENS, 0)} "
                    f"duration={event.get(events.DURATION_MS, 0)}ms"
                )
            elif event_type == events.TOOL_CALL:
                lines.append(
                    f"- {event.get(events.STARTED_AT, '')} tool_call {event.get(events.TOOL, '')} "
                    f"duration={event.get(events.DURATION_MS, 0)}ms result={event.get(events.RESULT_PREVIEW, '')}"
                )
            elif event_type == events.TURN_END:
                lines.append(f"- {event.get(events.TIMESTAMP, '')} turn_end turn={event.get(events.TURN_NO, '')}")
            elif event_type == events.RUN_END:
                lines.append(f"- {event.get(events.TIMESTAMP, '')} run_end outcome={event.get(events.OUTCOME, '')} reason={event.get(events.REASON, '')}")
            elif event_type == events.DISPATCH:
                lines.append(f"- {event.get(events.TIMESTAMP, '')} dispatch → {event.get(events.TARGET, '')} (type={event.get(events.CALLEE_TYPE, '')})")
            elif event_type == events.LLM_TIMEOUT:
                lines.append(f"- {event.get(events.TIMESTAMP, '')} llm_timeout turn={event.get(events.TURN_NO, '')}")
        lines.append("")
    return "\n".join(lines)
