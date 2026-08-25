"""agent.segments：trace 事件的轮内簿记（2026-08-25 审计批6c 从 talk.py 拆出）。

收走 talk.py 的 **segment/steps 簿记**一职：把本轮 trace 事件切片序列化成
history 的 segments 字段（交错时间线）与 steps 字段（可见过程）。纯函数：
tracer/target/切片锚点全部显式入参，只读 trace 目录与 agent.state.messages，
**落盘格式（字段名/结构）是 eval 依赖的形状面，一个字段都不动**。
"""
from __future__ import annotations

import json
from datetime import datetime


def clip_text(text: str, limit: int) -> str:
    """args/preview/reasoning 的截断（talk.refresh_running_steps 也用，故公开）。"""
    return text if len(text) <= limit else text[:limit] + "…"


def round_segments(tracer, target: str, events_before: int,
                   messages_before: int, agent,             # noqa: ANN001 —— 避免引 vendor 内部类型
                   interjections: list[tuple[str, float]]) -> list[dict]:
    """本轮的**交错时间线**（A 批，2026-08-24）：正文/思考/工具/用户插话按真实
    顺序序列化进 history（segments 字段）。

    配对规则：trace 的每个 `llm_call` 事件对应本轮新增的一条 assistant 消息 ——
    先落该轮思考（reasoning_ref blob）再落正文（msg.content）；其后的 `tool_call`
    事件顺次落工具段。插话按排空时刻插到「正在跑的那个工具」之前（用户说话时
    该工具还在运行，工具完成事件的 ts ≥ 排空时刻）。独立插话条目（喂 LLM 用）
    仍照旧写入 history；前端渲染以 segments 为准。
    """
    assistant = [m for m in agent.state.messages[messages_before:]
                 if m.role == "assistant"]
    pending_user = list(interjections)
    segs: list[dict] = []
    ai = 0
    for ev in tracer.read_events(target)[events_before:]:
        et = ev.get("type")
        if et == "llm_call":
            ref = ev.get("reasoning_ref")
            if ref:
                try:
                    text = (tracer.trace_dir / str(ref)).read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                if text:
                    segs.append({"kind": "reasoning", "text": clip_text(text, 2000)})
            msg = assistant[ai] if ai < len(assistant) else None
            ai += 1
            if msg is not None and (msg.content or "").strip():
                segs.append({"kind": "text", "text": msg.content.strip()})
        elif et == "tool_call":
            ts = ev.get("ts")
            ts_epoch = None
            if isinstance(ts, str):
                try:
                    ts_epoch = datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    ts_epoch = None
            while pending_user and ts_epoch is not None and pending_user[0][1] <= ts_epoch:
                segs.append({"kind": "user", "text": pending_user.pop(0)[0]})
            segs.append({
                "kind": "tool",
                "tool": ev.get("tool", "?"),
                "args": clip_text(json.dumps(ev.get("args", {}), ensure_ascii=False), 200),
                "preview": clip_text(str(ev.get("result_preview") or ""), 300),
                "duration_ms": ev.get("duration_ms", 0),
            })
    # 没对上工具的插话（轮末才排空 / ts 缺失）如实放末尾
    for text, _ in pending_user:
        segs.append({"kind": "user", "text": text})
    return segs


def round_steps(tracer, target: str, events_before: int) -> list[dict]:
    """本轮的可见过程：工具调用（名称/参数摘要/结果摘要/耗时）+ 思考全文。

    事件按追加序切片（turn_no 被我们每轮归零，不能当过滤键）。reasoning_ref
    指向 trace 目录里的 blob 文件，读出来给前端折叠展示 —— 思考过程不该
    只存在于 trace.html 里，商量时就要看得见。
    """
    steps: list[dict] = []
    for ev in tracer.read_events(target)[events_before:]:
        et = ev.get("type")
        if et == "tool_call":
            steps.append({
                "kind": "tool",
                "tool": ev.get("tool", "?"),
                "args": clip_text(json.dumps(ev.get("args", {}), ensure_ascii=False), 200),
                "preview": clip_text(str(ev.get("result_preview") or ""), 300),
                "duration_ms": ev.get("duration_ms", 0),
            })
        elif et == "llm_call":
            ref = ev.get("reasoning_ref")
            if not ref:
                continue
            blob = tracer.trace_dir / str(ref)
            try:
                reasoning = blob.read_text(encoding="utf-8")
            except OSError:
                continue
            if reasoning.strip():
                steps.append({"kind": "reasoning",
                              "text": clip_text(reasoning.strip(), 2000)})
    return steps
