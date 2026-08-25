"""agent.llm_stream 直调单测（N5-b：从 tests/api/test_agent_chat.py 装配形式迁来）。

模块已独立可测（2026-08-25 批6c 拆分），这里直测三件事：
- 思考流双字段名容错（reasoning / reasoning_content 都要流出分片）；
- 零分片重试语义（瞬断且一个分片没发 → 重试一次；已发过 → 不重试照抛）；
- 轮输出 token 预算（用尽 → 不调 API 返回 budget-stop；计数跨调用累计）。

共享鸭子类型替身（SDK chunk 形态）集中在本文件头部——迁移前 api 文件里三份重复。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.llm_stream import ReasoningTolerantClient

# ---------------- SDK 形态替身（共享） ----------------


def chunk(text: str | None = None, *, reasoning: str | None = None,
          reasoning_content: str | None = None, tool_calls=None,
          usage: tuple[int, int] | None = None):
    """一个流分片：delta 块恒一个 choice；usage-only 末块 choices 为空（真 SDK 形态）。"""
    d = SimpleNamespace(reasoning_content=reasoning_content, reasoning=reasoning,
                        content=text, tool_calls=tool_calls)
    u = (SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
         if usage else None)
    return SimpleNamespace(usage=u, choices=[] if text is None and not reasoning
                           and not reasoning_content and not tool_calls and usage
                           else [SimpleNamespace(delta=d)])


class ScriptedCompletions:
    """按脚本响应：每项是一个分片列表（作为一条流）或一个要抛的异常。"""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return self._stream(item)

    @staticmethod
    def _stream(chunks):
        async def gen():
            for c in chunks:
                yield c
        return gen()


class _ExplodingStream:
    """create 成功但流中途抛——partial output 的重试判定在消费侧。"""

    def __init__(self, chunks, exc):
        self._chunks, self._exc = chunks, exc

    async def create(self, **kwargs):
        async def gen():
            for c in self._chunks:
                yield c
            raise self._exc
        return gen()


def make_client(script) -> tuple[ReasoningTolerantClient, ScriptedCompletions]:
    """绕过 __init__（那边连真实 SDK）——只测 _stream_call 层。"""
    comps = ScriptedCompletions(*script)
    c = object.__new__(ReasoningTolerantClient)
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=comps))
    return c, comps


# ---------------- 思考流双字段容错 ----------------

@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"],
                         ids=["o系字段", "百炼字段"])
def test_reasoning_flows_for_both_field_names(field):
    """端点用 reasoning（o 系）或 reasoning_content（百炼/Qwen）——思考分片都要流出来。"""
    from agentic.types import LLMDelta

    c, _ = make_client([
        [chunk(reasoning="我想想") if field == "reasoning" else chunk(reasoning_content="我想想"),
         chunk("答案", usage=(1, 2))],
    ])
    got: list[LLMDelta] = []
    resp = asyncio.run(c._stream_call({"model": "m", "stream": True}, on_delta=got.append))
    kinds = [(d.kind, d.text) for d in got]
    assert ("reasoning", "我想想") in kinds
    assert ("content", "答案") in kinds
    assert resp.reasoning == "我想想"
    assert resp.message.content == "答案"


def test_mixed_field_names_in_one_stream():
    """同一条流里两种字段名混用（代理转发时见过）——两段思考都收。"""
    c, _ = make_client([
        [chunk(reasoning="o系"), chunk(reasoning_content="百炼"), chunk("答案", usage=(1, 2))],
    ])
    resp = asyncio.run(c._stream_call({"model": "m"}))
    assert resp.reasoning == "o系百炼"


# ---------------- 零分片重试 ----------------

def test_retry_once_when_nothing_emitted(monkeypatch):
    """连接类瞬断：一个分片都没发 → 重试一次成功（重试间隔 sleep 打桩，别真等 1s）。"""
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    c, comps = make_client([
        ConnectionError("瞬断"),
        [chunk("重试成功", usage=(1, 1))],
    ])
    out = asyncio.run(c._stream_call({"model": "m"}))
    assert comps.calls == 2
    assert out.message.content == "重试成功"


def test_no_retry_after_partial_output():
    """已发过分片 → 不重试（重发会让前端正文重复），异常照抛给引擎记 run_end。"""
    expl = _ExplodingStream([chunk("半句话")], ConnectionError("中途断"))
    c = object.__new__(ReasoningTolerantClient)
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=expl))
    with pytest.raises(ConnectionError):
        asyncio.run(c._stream_call({"model": "m"}, on_delta=lambda d: None))


# ---------------- 轮输出 token 预算 ----------------

def test_budget_exhausted_returns_budget_stop_without_api_call():
    """累计已到预算 → 不再调 API，返回 budget-stop 空响应（runner 自然收轮，SSE 不炸）。"""
    c, comps = make_client([[chunk("x", usage=(1, 1))]])
    c.reset_round_budget(100)
    c.round_output_tokens = 100          # 轮内前几次调用已用满
    resp = asyncio.run(c._stream_call({"model": "m"}))
    assert comps.calls == 0, "预算用尽就别再打 API"
    # budget-stop 哨兵落在 model 字段（vendor 形状无独立 finish_reason）
    assert resp.model == "budget-stop"
    assert resp.message.content is None


def test_budget_counts_output_tokens_across_calls():
    """轮内计数跨调用累计：第二次调用带着第一次的 output_tokens 一起对预算。"""
    c, comps = make_client([
        [chunk("a", usage=(1, 60))],
        [chunk("b", usage=(1, 30))],
    ])
    c.reset_round_budget(100)
    r1 = asyncio.run(c._stream_call({"model": "m"}))
    assert c.round_output_tokens == 60
    r2 = asyncio.run(c._stream_call({"model": "m"}))
    assert c.round_output_tokens == 90
    assert r1.message.content == "a" and r2.message.content == "b"


def test_reset_round_budget_clears_limit():
    """budget=None/0 = 不设限（AgentTalk 每轮首调，None 轮不限量）。"""
    c, _ = make_client([])
    c.output_budget = 100
    c.round_output_tokens = 100
    c.reset_round_budget(None)
    assert c.output_budget is None and c.round_output_tokens == 0
