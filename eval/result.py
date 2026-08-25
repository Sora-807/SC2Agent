"""eval.result：RunResult 归一化层 + Grade（D1/D3/D16）。

RunResult = 纯数据快照（可序列化/可 diff/可进报告），从 Tracer 落盘文件提取，
**不另造记录机制**（PLAN §2.3：trace 是地基）。活世界不进 RunResult —— 主动
grader 单独收 world 参数。

提取源（trace_dir = 本 run 的 Tracer 根目录，恰好一个 run 子目录）：
- agents/<target>.jsonl：tool_call / llm_call / run_end 事件；
- agents/<target>.messages.jsonl：完整消息（首条 system = 组装后全文提示词）；
- prompts/*.json：reasoning blob（llm_call.reasoning_ref 相对路径）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

#: D8：给判官的 reasoning 全量为默认 + 上限护栏（超长截中段并标注，静默爆上下文更糟）
REASONING_CAP = 12_000


@dataclass
class Grade:
    """一个 grader 的一次判定。确定性轴用 passed，LLM 轴用 score（0-5），reason 必填。"""

    axis: str
    grader: str
    passed: bool | None = None
    score: float | None = None
    reason_zh: str = ""

    @property
    def ok(self) -> bool:
        """报告汇总口径：passed 优先，LLM 轴 >=3 算过（rubric 中位）。"""
        if self.passed is not None:
            return self.passed
        return self.score is not None and self.score >= 3.0

    def to_dict(self) -> dict:
        return {"axis": self.axis, "grader": self.grader,
                "passed": self.passed, "score": self.score,
                "reason_zh": self.reason_zh}


@dataclass
class RunResult:
    """所有 runner 产出的同一形状（seam，PLAN §2.2）。"""

    meta: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    final_text: str = ""
    reasoning: list[str] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)   # /api/proposals（D9 的持久侧）
    changes: list[dict] = field(default_factory=list)     # ChangeRecord（D9 的轮内侧）
    workspace: dict = field(default_factory=dict)         # scratch 终态快照（相对路径→字节数）
    session: dict | None = None                           # 游戏终态 {state, game_time, alive}

    def to_dict(self, full: bool = False) -> dict:
        """full=True 归档用（messages 不截断）；默认报告用（system 全文在
        meta.prompt_full_text/prompts/<hash>.md 单列，messages 只留摘要）。"""
        return {"meta": self.meta, "tool_calls": self.tool_calls,
                "final_text": self.final_text, "reasoning": self.reasoning,
                "segments": self.segments, "proposals": self.proposals,
                "changes": self.changes, "workspace": self.workspace,
                "session": self.session,
                "messages": self.messages if full else _clip_messages(self.messages)}


def _clip_messages(messages: list[dict]) -> list[dict]:
    """messages 落报告时瘦身（system 全文在 meta.prompt_full_text 单列，不重复两份）。"""
    out = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        out.append({"role": role, "content": content[:400]})
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _workspace_snapshot(root: Path) -> dict:
    """scratch 终态：相对路径 → 字节数（memory/session 有没有写对，看得到形状）。"""
    if not root.is_dir():
        return {}
    out: dict[str, int] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root)).replace("\\", "/")] = p.stat().st_size
    return out


def _seed_fingerprint(root: Path) -> str:
    """D16：工作区种子指纹（改模板/种子 = 改提示词，只 hash SYSTEM_PROMPT 会漏这半边）。"""
    if not root.is_dir():
        return ""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def extract_result(trace_root: Path, target: str, *, talk_outcome: dict,
                   run_no: int, workspace_root: Path, seed_hash: str,
                   proposals: list[dict], session: dict | None,
                   duration_s: float, prompt_text: str | None = None) -> RunResult:
    """从 trace 落盘文件 + AgentTalk 轮结果提取 RunResult。

    trace_root 下恰好一个 run 子目录（eval 每 run 用独立空根，见 runner）。
    seed_hash 由 fixture 在 run 前算好传入（工作区随后会被 agent 写动）。
    prompt_text：组装后全文提示词优先取 runner 传入的快照（AgentTalk 路径
    messages.jsonl 不含 system 消息）；None 时回退从 messages 首条 system 提取。
    """
    run_dirs = [d for d in trace_root.iterdir() if d.is_dir()] if trace_root.is_dir() else []
    events: list[dict] = []
    messages: list[dict] = []
    if run_dirs:
        trace_dir = run_dirs[0]
        events = _read_jsonl(trace_dir / "agents" / f"{target}.jsonl")
        messages = _read_jsonl(trace_dir / "agents" / f"{target}.messages.jsonl")

    tool_calls = [
        {"tool": e.get("tool"), "args": e.get("args"), "turn_no": e.get("turn_no"),
         "duration_ms": int(float(e.get("duration_ms") or 0)), "ts": e.get("ts"),
         "result_preview": e.get("result_preview")}
        for e in events if e.get("type") == "tool_call"
    ]
    llm_calls = [e for e in events if e.get("type") == "llm_call"]
    run_end = next((e for e in reversed(events) if e.get("type") == "run_end"), {})

    # reasoning：blob 全量提取（D8 默认不截；超护栏截中段并在 meta 标注）
    reasoning: list[str] = []
    clipped = 0
    if run_dirs:
        for e in llm_calls:
            ref = e.get("reasoning_ref")
            if not ref:
                continue
            blob = trace_dir / ref
            if not blob.is_file():
                continue
            try:
                data = json.loads(blob.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            text = str(data if isinstance(data, str) else data.get("text") or data.get("content") or "")
            if len(text) > REASONING_CAP:
                half = REASONING_CAP // 2
                text = (text[:half] + f"\n…[eval 截断：原 {len(text)} 字超 {REASONING_CAP} 护栏，中段省略]…\n"
                        + text[-half:])
                clipped += 1
            reasoning.append(text)

    # 组装后全文提示词：runner 快照优先，回退 messages 首条 system（vendor 自组路径才有）
    prompt_full_text = prompt_text or next(
        (str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
    model = next((e.get("model") for e in llm_calls if e.get("model")), "")

    meta = {
        "run_no": run_no,
        "target": target,
        "llm_model": model,
        "outcome": talk_outcome.get("outcome") or run_end.get("outcome"),
        "duration_s": round(duration_s, 1),
        "prompt_hash": _sha(prompt_full_text),
        "prompt_full_text": prompt_full_text,
        "seed_hash": seed_hash,
        "input_tokens": int(float(run_end.get("total_input_tokens") or 0)),
        "output_tokens": int(float(run_end.get("total_output_tokens") or 0)),
        "reasoning_clipped": clipped,
    }
    return RunResult(
        meta=meta,
        tool_calls=tool_calls,
        messages=messages,
        final_text=str(talk_outcome.get("reply") or ""),
        reasoning=reasoning,
        segments=list(talk_outcome.get("segments") or []),
        proposals=proposals,
        changes=list(talk_outcome.get("changes") or []),
        workspace=_workspace_snapshot(workspace_root),
        session=session,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
