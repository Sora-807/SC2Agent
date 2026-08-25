"""eval.judge：LLM-as-judge（PLAN §3.6 JudgeGrader，D8/D12）。

- D12 盲评：判官材料里**没有**提示词版本/hash/模型名 —— 只看产出（回复 + 推理 +
  工具序列 + 提案标题），减同源偏吹；换强判官 = 换 llm_factory 一行。
- D8：reasoning 全量给（RunResult 提取层已带 12k/段护栏）+ 总材料上限兜底。
- 输出约定：判官回 JSON `{"score": 0-5, "reason": "一句话"}`；解析宽松（剥代码围栏、
  正则兜底），解析不出 = score None + 原因说明（没判成不算过）。
- grade 是 async（LLM client.complete 是协程）——run.py 对 awaitable grade 会 await。
"""
from __future__ import annotations

import json
import re

from agentic.types import Message

from eval.result import Grade, RunResult

#: 判官材料总上限（字符）——reasoning 每段已各自护栏，这里是整包兜底
JUDGE_MATERIAL_CAP = 24_000

JUDGE_SYSTEM = (
    "你是严格的评审。根据评分标准给 agent 的表现打 0-5 分（5=优秀，3=合格，0=差）。"
    '只回 JSON：{"score": <0-5 的整数或一位小数>, "reason": "<不超过60字的中文理由>"}'
    "，不要输出其他内容。"
)


class JudgeGrader:
    name = "judge"
    axis = "文字"

    def __init__(self, rubric: str, llm_factory=None) -> None:
        self.rubric = rubric
        self._llm_factory = llm_factory   # None = agent 同款判官（D12 起步）

    async def grade(self, result: RunResult, world=None) -> Grade:
        if self._llm_factory is None:
            from agent.talk import openai_from_env
            factory = openai_from_env
        else:
            factory = self._llm_factory
        material = _material(result)
        prompt = (f"# 评分标准\n{self.rubric}\n\n# agent 的表现材料\n{material}\n\n"
                  "按评分标准打分。")
        try:
            resp = await factory().complete([Message("user", prompt)])
        except Exception as exc:  # noqa: BLE001 —— 判官失败要可见，不许炸整轮评测
            return Grade(self.axis, self.name, score=None,
                         reason_zh=f"判官调用失败：{type(exc).__name__}: {str(exc)[:120]}")
        text = _resp_text(resp)
        score, reason = _parse(text)
        if score is None:
            return Grade(self.axis, self.name, score=None,
                         reason_zh=f"判官输出解析失败：{text[:160]}")
        return Grade(self.axis, self.name, score=score, reason_zh=reason)


def _material(result: RunResult) -> str:
    """盲评材料：产出四件（回复/推理/工具序列/提案标题），不带任何版本信息。"""
    parts = []
    tools = " → ".join(str(t.get("tool")) for t in result.tool_calls) or "（无）"
    parts.append(f"## 工具调用序列\n{tools}")
    titles = [f"- {p.get('title_zh') or p.get('id')}" for p in result.proposals]
    parts.append("## 提案\n" + ("\n".join(titles) if titles else "（无）"))
    parts.append(f"## 最终回复\n{result.final_text or '（空）'}")
    if result.reasoning:
        parts.append("## 思考过程\n" + "\n\n---\n\n".join(result.reasoning))
    material = "\n\n".join(parts)
    if len(material) > JUDGE_MATERIAL_CAP:
        half = JUDGE_MATERIAL_CAP // 2
        material = (material[:half]
                    + f"\n\n…[judge 材料超 {JUDGE_MATERIAL_CAP} 字，中段省略]…\n"
                    + material[-half:])
    return material


def _resp_text(resp) -> str:
    msg = getattr(resp, "message", None)
    if msg is not None and getattr(msg, "content", None):
        return str(msg.content)
    return str(getattr(resp, "text", "") or resp)


def _parse(text: str) -> tuple[float | None, str]:
    """宽松解析（真判官经常不按格式回——冒烟实测有裸「5分。」形态）：
    JSON（含围栏）→ score: x 键值 → 短文本里的「N分」/孤立 N。"""
    stripped = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        data = json.loads(stripped)
        return float(data["score"]), str(data.get("reason") or "")[:120]
    except (ValueError, KeyError, TypeError):
        pass
    m = re.search(r"score[\"']?\s*[:=]\s*([0-5](?:\.\d)?)", stripped, re.I)
    if m:
        rm = re.search(r"reason[\"']?\s*[:=]\s*[\"']?([^\"'}\n]{1,120})", stripped, re.I)
        return float(m.group(1)), (rm.group(1) if rm else "")[:120]
    # 短回复（判官偷懒只给结论）：「5分」「4.5 分，理由…」——必须带「分」，
    # 且文本要短（长文本里的孤立数字太容易误伤，宁可判解析失败）
    if len(stripped) <= 24:
        m = re.search(r"([0-5](?:\.\d)?)\s*分", stripped)
        if m:
            rm = re.search(r"[，,。]\s*(.{1,100})", stripped)
            return float(m.group(1)), (rm.group(1) if rm else "").strip()[:120]
    return None, ""
