"""P3 切片 A：对话式顾问（离线商量回路）。

锁四件事：
1. **续话成立**：同一 target 上两轮 say，对话历史延续（BaseAgent create_or_get
   复用实例 + runner 写回 state.messages），turn_no 每轮归零（终身累计会立刻
   max_turns 退出 —— 那是这条服务最容易踩的坑）；
2. **工具面在对话里可用**：聊天中的 agent 走的仍是 AdvisorSpec 的规划工具；
3. **G7**：LLM 未配置 → 503 带理由（.env 指引），不是启动崩溃或静默；
4. 每轮收尾 finalize_summary —— 聊到一半 trace.html 也在。
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.client import ApiClient
from agent.talk import AgentTalk
from agentic.llm import FakeLLMClient
from agentic.types import LLMResponse, Message, ToolCall
from api.app import create_app


def _client_for(app_client: TestClient) -> ApiClient:
    def transport(method: str, path: str, body: dict | None):
        if method == "GET":
            res = app_client.get(path)
        elif method == "PUT":
            res = app_client.put(path, json=body or {})
        else:
            res = app_client.post(path, json=body or {})
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {"detail": res.text}
    return ApiClient(transport=transport)


def _done(text: str) -> LLMResponse:
    return LLMResponse(Message("assistant", None, [
        ToolCall("c", "done", {"result": text})]), 0, 0, "fake")


def _talk(api: TestClient, script: list, tmp_path: Path) -> AgentTalk:
    return AgentTalk(
        _client_for(api), llm_factory=lambda: FakeLLMClient(list(script)),
        trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                 plans_dir=tmp_path / "plans",
                                 map_plans_dir=tmp_path / "mp"))


# ---------------- 续话 ----------------

def test_two_turns_continue_one_conversation(api: TestClient, tmp_path: Path):
    talk = _talk(api, [_done("第一轮：默认规划是步坦开局。"), _done("第二轮：接着上文说。")], tmp_path)
    r1 = asyncio.run(talk.say("现在有什么规划？"))
    r2 = asyncio.run(talk.say("那帮我看看这套的问题"))
    assert r1["reply"].startswith("第一轮")
    assert r2["reply"].startswith("第二轮")
    assert [m["role"] for m in talk.history] == ["user", "agent", "user", "agent"]
    assert talk.describe()["turns"] == 2


def test_trace_html_exists_mid_conversation(api: TestClient, tmp_path: Path):
    """每轮收尾 finalize —— 商量到一半也能打开可视化。"""
    talk = _talk(api, [_done("ok")], tmp_path)
    asyncio.run(talk.say("看一眼"))
    html = Path(talk.describe()["trace_html"])
    assert html.exists() and html.stat().st_size > 0


# ---------------- 端点 ----------------

def test_chat_endpoints_roundtrip(api: TestClient, tmp_path: Path):
    app = create_app(tmp_path / "f2", tmp_path / "p2.jsonl",
                     plans_dir=tmp_path / "plans2", map_plans_dir=tmp_path / "mp2",
                     agent_talk=_talk(api, [_done("端点回复"), _done("第二条")], tmp_path))
    c = TestClient(app)
    r = c.post("/api/agent/chat", json={"text": "你好"})
    assert r.status_code == 200 and r.json()["reply"] == "端点回复"
    r2 = c.post("/api/agent/chat", json={"text": "再说说"})
    assert r2.status_code == 200 and r2.json()["reply"] == "第二条"
    h = c.get("/api/agent/chat").json()
    assert h["enabled"] is True and len(h["messages"]) == 4


def test_empty_message_is_400(tmp_path: Path):
    app = create_app(tmp_path / "f3", tmp_path / "p3.jsonl", agent_talk=object())
    c = TestClient(app)
    assert c.post("/api/agent/chat", json={"text": "  "}).status_code == 400


def test_disabled_talk_reports_reason(tmp_path: Path):
    c = TestClient(create_app(tmp_path / "f4", tmp_path / "p4.jsonl"))
    assert c.get("/api/agent/chat").json()["enabled"] is False
    assert c.post("/api/agent/chat", json={"text": "hi"}).status_code == 503


# ---------------- G7：LLM 未配置 ----------------

def test_unconfigured_llm_shows_reason_not_crash(api: TestClient, tmp_path: Path):
    def boom():
        raise KeyError("OPENAI_API_KEY")

    talk = AgentTalk(_client_for(api), llm_factory=boom,
                     trace_root=tmp_path / "t", workspace_root=tmp_path / "w")
    out = asyncio.run(talk.say("你好"))
    assert "OPENAI_API_KEY" in out["error"] and ".env" in out["error"]
    # 端点侧：503 带理由
    app = create_app(tmp_path / "f5", tmp_path / "p5.jsonl", agent_talk=talk)
    r = TestClient(app).post("/api/agent/chat", json={"text": "你好"})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]


# ---------------- 对话中的工具面 ----------------

def test_chat_agent_can_use_planning_tools(api: TestClient, tmp_path: Path):
    script = [
        LLMResponse(Message("assistant", None, [
            ToolCall("c1", "ls", {"path": "plans/"})]), 0, 0, "fake"),
        _done("有一份默认规划（锁定），共 22 项。"),
    ]
    talk = _talk(api, script, tmp_path)
    out = asyncio.run(talk.say("看看现在有哪些生产规划"))
    assert "默认规划" in out["reply"]
    # 工具真的跑过（不是 LLM 编的）：FakeLLM 的第二次调用收到了 ls 的结果
    assert not out.get("error")


# ---------------- 回复提取与过程展示（用户反馈 2026-08-22） ----------------

def test_plain_text_reply_is_a_reply(api: TestClient, tmp_path: Path):
    """LLM 直接用文字回答（没调 done）也是合法回复 —— 不该出现「没有结论」占位。"""
    script = [LLMResponse(Message("assistant", "直接文字回答：默认规划有 22 项。"), 0, 0, "fake")]
    talk = _talk(api, script, tmp_path)
    out = asyncio.run(talk.say("默认规划有几项？"))
    assert out["reply"].startswith("直接文字回答")
    assert "没有产出" not in out["reply"]


def test_agent_message_carries_tool_steps(api: TestClient, tmp_path: Path):
    """过程可见：每轮 agent 消息带 steps（工具名/参数/结果摘要/耗时）。

    2026-08-22 文件工作区改造后，规划读取的入口是文件契约的 ls。
    """
    script = [
        LLMResponse(Message("assistant", None, [
            ToolCall("c1", "ls", {"path": "plans/"})]), 0, 0, "fake"),
        _done("两份规划。"),
    ]
    talk = _talk(api, script, tmp_path)
    asyncio.run(talk.say("看看有哪些规划"))
    agent_msg = talk.history[-1]
    assert agent_msg["role"] == "agent"
    tools = [s for s in agent_msg.get("steps", []) if s["kind"] == "tool"]
    assert any(s["tool"] == "ls" for s in tools)
    assert any("default.yaml" in s["preview"] for s in tools)
    assert all(s["duration_ms"] >= 0 for s in tools)


# ---------------- 记忆：对话持久化 + 笔记（用户拍板「工具化 + 记忆文件」） ----------------

def _talk2(api: TestClient, script: list, tmp_path: Path) -> AgentTalk:
    return AgentTalk(
        _client_for(api), llm_factory=lambda: FakeLLMClient(list(script)),
        trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws",
        history_path=tmp_path / "talk" / "history.json")


def test_history_persists_and_reseeds_llm_context(api: TestClient, tmp_path: Path):
    """重启续聊：UI 历史还在，且历史被 seed 进新引擎的 messages —— LLM 也记得。"""
    t1 = _talk2(api, [_done("第一场：默认规划 22 项。")], tmp_path)
    asyncio.run(t1.say("默认规划几项？"))
    # 「重启」：新实例（同 history 文件、新引擎、新 FakeLLM 队列）。
    # 用闭包捕获 FakeLLM 实例 —— 验证 seed 要读它记录的 calls。
    holder: list[FakeLLMClient] = []

    def factory() -> FakeLLMClient:
        c = FakeLLMClient([_done("第二场：接着上一场说。")])
        holder.append(c)
        return c

    t2 = AgentTalk(_client_for(api), llm_factory=factory,
                   trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws",
                   history_path=tmp_path / "talk" / "history.json")
    assert len(t2.history) == 2, "UI 历史从盘上回来了"
    out = asyncio.run(t2.say("刚才我们聊了什么？"))
    assert out["reply"].startswith("第二场")
    # seed 生效的硬证据：本轮发给 LLM 的 messages 含第一场的 assistant 文本
    assert holder and holder[0].calls
    first_messages = holder[0].calls[0][0]
    joined = "".join(getattr(m, "content", "") or "" for m in first_messages)
    assert "第一场：默认规划 22 项。" in joined, "重启后 LLM 上下文里应看到旧对话"


def test_notes_roundtrip_through_tools(api: TestClient, tmp_path: Path):
    """笔记工具已退役（2026-08-22 文件工作区改造）：跨会话记忆 = scratch 里的
    memory.md，走文件契约读写 —— 这里锁文件路径在真工具面上可用。"""
    from agentic.workspace.tools import make_workspace_tools

    from agent.workspace import ApiWorkspace, ChangeLog
    tools_ = {t.name: t for t in make_workspace_tools(
        ApiWorkspace(_client_for(api), tmp_path / "scratch", ChangeLog()))}
    out = asyncio.run(tools_["write"].function(
        {"path": "memory.md", "content": "用户偏好：补给站别提前太多\n"}))
    assert "wrote memory.md" in out
    back = asyncio.run(tools_["read"].function({"path": "memory.md"}))
    assert "补给站别提前太多" in back


# ---------------- 流式过程（running_steps，2026-08-22 动效轮） ----------------

def test_running_steps_empty_when_idle(api: TestClient, tmp_path: Path):
    talk = _talk2(api, [], tmp_path)
    talk.refresh_running_steps()
    assert talk.running_steps == []
    assert talk.describe()["running_steps"] == []


def test_get_chat_returns_running_steps_field(api: TestClient, tmp_path: Path):
    app = create_app(tmp_path / "f9", tmp_path / "p9.jsonl",
                     plans_dir=tmp_path / "plans9", map_plans_dir=tmp_path / "mp9",
                     agent_talk=_talk2(api, [], tmp_path))
    h = TestClient(app).get("/api/agent/chat").json()
    assert h["enabled"] is True
    assert h["running_steps"] == []


# ---------------- 真流式（2026-08-22 十五轮：BaseAgent start_stream → SSE） ----------------

def test_start_round_streams_deltas_and_round(api: TestClient, tmp_path: Path):
    """流式轮：事件从第一个思考 token 起就有（delta），工具/终态齐全，收尾 None。"""
    # 脚本：先调一个真工具（list_plans），再 done —— 事件流应含 delta/tool_call/run_end/round
    script = [
        LLMResponse(Message("assistant", None, [
            ToolCall("c1", "list_plans", {})]), 0, 0, "fake"),
        # 带正文的 done：FakeLLM 对 content 会发一条 delta（流式管道的凭证）
        LLMResponse(Message("assistant", "结论：有一份默认规划。", [
            ToolCall("c2", "done", {"result": "结论：有一份默认规划。"})]), 0, 0, "fake"),
    ]
    talk = _talk(api, script, tmp_path)
    handle = talk.start_round("看看规划")
    kinds: list[str] = []
    tools_seen: list[str] = []
    while True:
        ev = handle.events.get(timeout=30)
        if ev is None:
            break
        kinds.append(ev["type"])
        if ev["type"] == "tool_call":
            tools_seen.append(ev["tool"])
    assert "delta" in kinds                      # 流式分片真的发了
    assert tools_seen == ["list_plans", "done"]   # 工具事件带名字（done 也是工具）
    assert kinds[-1] == "round"                  # 终态最后到
    assert handle.result is not None and handle.result["reply"].startswith("结论")
    assert [m["role"] for m in handle.result["messages"][-2:]] == ["user", "agent"]


def test_sse_endpoint_event_order(api: TestClient, tmp_path: Path):
    """SSE 端点搬运事件流：data: 行可解析，末条是 round。"""
    app = create_app(tmp_path / "f10", tmp_path / "p10.jsonl",
                     plans_dir=tmp_path / "plans10", map_plans_dir=tmp_path / "mp10",
                     agent_talk=_talk(api, [_done("流式回复")], tmp_path))
    with TestClient(app) as c:
        lines = [
            line[len("data: "):]
            for line in c.post("/api/agent/chat/stream", json={"text": "你好"}).text.splitlines()
            if line.startswith("data: ")
        ]
    events = [json.loads(line) for line in lines]
    assert events, "SSE 至少要有事件"
    assert events[-1]["type"] == "round"
    assert events[-1]["reply"] == "流式回复"
    assert any(ev["type"] == "run_end" for ev in events)


def test_start_round_empty_text_finishes_with_error(api: TestClient, tmp_path: Path):
    talk = _talk(api, [], tmp_path)
    handle = talk.start_round("   ")
    ev = handle.events.get(timeout=5)
    assert ev["type"] == "round" and "空消息" in ev["error"]
    assert handle.events.get(timeout=5) is None
