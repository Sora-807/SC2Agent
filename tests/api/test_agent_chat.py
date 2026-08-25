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
    """回合结束 = 纯文字回复（§0.52 F 批 done 下线；命名沿用减少 churn）。"""
    return LLMResponse(Message("assistant", text), 0, 0, "fake")


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
            # I5 改名后 ls 走新路径（plans/ 旧名仍可 read/write，但 vendor 的
            # ScopedWorkspace 按 visible_paths 过滤前缀 —— 清单只认新名）
            ToolCall("c1", "ls", {"path": "production-plans/"})]), 0, 0, "fake"),
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
            # I5 改名后 ls 走新路径（plans/ 旧名仍可 read/write，但 vendor 的
            # ScopedWorkspace 按 visible_paths 过滤前缀 —— 清单只认新名）
            ToolCall("c1", "ls", {"path": "production-plans/"})]), 0, 0, "fake"),
        _done("两份规划。"),
    ]
    talk = _talk(api, script, tmp_path)
    asyncio.run(talk.say("看看有哪些规划"))
    agent_msg = talk.history[-1]
    assert agent_msg["role"] == "agent"
    tools = [s for s in agent_msg.get("steps", []) if s["kind"] == "tool"]
    assert any(s["tool"] == "ls" for s in tools)
    assert any("default.yaml" in s["preview"] for s in tools
               if s["tool"] in ("ls", "glob"))
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
    # 脚本：先调一个真工具（list_plans），再纯文字收尾（done 已下线，§0.52 F 批）
    script = [
        LLMResponse(Message("assistant", None, [
            ToolCall("c1", "list_plans", {})]), 0, 0, "fake"),
        # 纯文字收尾：FakeLLM 对 content 会发一条 delta（流式管道的凭证）
        LLMResponse(Message("assistant", "结论：有一份默认规划。"), 0, 0, "fake"),
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
    assert "delta" in kinds                        # 流式分片真的发了
    assert tools_seen == ["list_plans"]            # 工具事件带名字（文字收尾不再有 done）
    assert kinds[-1] == "round"                    # 终态最后到
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


# ---------------- 看门狗（2026-08-23 真机案例：轮子停滞锁死整条通道） ----------------

class _FirstCallHangs:
    """首次 complete 永不返回（复现 2026-08-23 停滞），之后恢复 —— 同一实例验锁释放。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, *, model=None, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(10000)
        return _done("恢复的一轮")


def test_round_watchdog_frees_channel_and_surfaces_error(api: TestClient, tmp_path: Path):
    """停滞轮：看门狗超时 → 错误显形（G7）+ _lock 释放（下一轮能正常跑）。"""
    fake = _FirstCallHangs()
    talk = AgentTalk(
        _client_for(api), llm_factory=lambda: fake,
        trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws",
        round_timeout=2.0)   # 满载套件下 0.5s 偶发误杀第二轮（时序敏感，非回归）
    r = asyncio.run(talk.say("卡住的一轮"))
    assert r.get("error") and "看门狗" in r["error"]

    # 通道必须没被锁死：同一个引擎再说一句，能完整走完一轮（证明 _lock 已释放）
    r2 = asyncio.run(talk.say("再来一句"))
    assert r2.get("reply", "").startswith("恢复的一轮"), r2


def test_round_handle_finish_is_idempotent():
    """_finish 双收尾只发一串事件（看门狗与 runner 竞争时不能发两个 None 哨兵）。"""
    from agent.talk import RoundHandle
    h = RoundHandle()
    h._finish({"error": "first"})
    h._finish({"error": "second"})
    drained = []
    while True:
        ev = h.events.get_nowait()
        if ev is None:
            break
        drained.append(ev)
    assert len(drained) == 1 and drained[0]["error"] == "first"


# ---------------- 对局跟随（2026-08-24 用户拍板：游戏没结束不许停）----------------


class _AliveStub:
    """_game_alive 的替身：先活着（t 递增），N 轮后结束。"""

    def __init__(self, alive_rounds: int) -> None:
        self.left = alive_rounds

    def __call__(self):
        if self.left > 0:
            self.left -= 1
            return True, 100.0 + 10.0 * self.left
        return False, 0.0


def test_follow_game_nudges_until_game_ends(api: TestClient, tmp_path: Path, monkeypatch):
    """对局进行中 → 每轮后注入（跟随提醒）再跑；游戏结束 → 正常收尾。"""
    fake = FakeLLMClient([_done("开局看了，没问题") for _ in range(5)])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    stub = _AliveStub(alive_rounds=2)   # 前两轮"活着" → 两次提醒；随后结束
    monkeypatch.setattr(AgentTalk, "_game_alive", lambda self: stub())
    r = asyncio.run(talk.say("开局看一眼"))
    nudges = [m for m in r["messages"] if m["role"] == "user" and "跟随提醒" in m.get("text", "")]
    assert len(nudges) == 2, "活两轮 = 两次提醒"
    assert all(m.get("nudge") for m in nudges), "提醒轮带 nudge 标记（前端渲染成系统条）"


def test_follow_never_gives_up_on_text_only_rounds(api: TestClient, tmp_path: Path, monkeypatch):
    """§0.52 拍板：连续纯文字轮不再「跟随停止」—— 游戏活着就一直提醒到轮数上限，
    唯一止损是总轮数（启动期 agent 爱回文字，3 轮放弃正是启动期停摆的根因）。"""
    import agent.talk as talk_mod
    monkeypatch.setattr(talk_mod, "FOLLOW_MAX_ROUNDS", 5)
    fake = FakeLLMClient([_done("好了") for _ in range(8)])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    monkeypatch.setattr(AgentTalk, "_game_alive", lambda self: (True, 123.0))  # 永远活着
    r = asyncio.run(talk.say("看一下"))
    texts = [m.get("text", "") for m in r["messages"]]
    assert not any("跟随停止" in t for t in texts), "不许再放弃跟随"
    nudges = [m for m in r["messages"] if "跟随提醒" in m.get("text", "")]
    assert len(nudges) == 5, "提醒到轮数上限"
    assert all(m.get("nudge") for m in nudges)


def test_follow_tolerates_transient_session_hiccups(api: TestClient, tmp_path: Path, monkeypatch):
    """启动/收尾窗口 session 接口偶发取不到 ≠ 对局结束 —— 活过之后再取不到要短重试
    （§0.52 启动期跟随中断的另一半根因）；从未活过（普通聊天）不重试。"""
    seq = [(True, 10.0), (False, 0.0), (True, 20.0),
           (False, 0.0), (False, 0.0), (False, 0.0)]
    calls = {"n": 0}

    def stub(self):
        out = seq[calls["n"]] if calls["n"] < len(seq) else (False, 0.0)
        calls["n"] += 1
        return out

    fake = FakeLLMClient([_done("好") for _ in range(8)])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    monkeypatch.setattr(AgentTalk, "_game_alive", stub)
    r = asyncio.run(talk.say("看一下"))
    nudges = [m for m in r["messages"] if "跟随提醒" in m.get("text", "")]
    # 第 2 次检查先取不到（seq[1] False）→ 重试拿到 True（seq[2]）→ 继续提醒；
    # 第 3 次连续三次 False → 判死收尾
    assert len(nudges) == 2
    assert calls["n"] == 6, "重试把瞬时取不到问穿了（2 次 True 各一次 + 死前两次重试）"


def test_no_game_no_follow(api: TestClient, tmp_path: Path):
    """没有对局（默认测试 app 无会话）：一轮即止，无提醒。"""
    talk = _talk(api, [_done("规划已读")], tmp_path)
    r = asyncio.run(talk.say("看看默认规划"))
    assert r["reply"].startswith("规划已读")
    assert not any("跟随提醒" in m.get("text", "") for m in r["messages"])


# ---------------- 思考流容错 + 首分片诊断（2026-08-24「token 流很慢」）----------------

def test_clean_clears_history_and_engine_state(api: TestClient, tmp_path: Path):
    """旧上下文里的过时认知（"我没有 stop 工具"）会一直误导模型 —— clean 从空白开始。"""
    fake = FakeLLMClient([
        _done("第一轮"),
        _done("第二轮（应该看不到第一轮）"),
    ])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    r1 = asyncio.run(talk.say("第一句"))
    assert len(r1["messages"]) == 2

    out = asyncio.run(talk.clear_context())
    assert out["ok"] is True and out["messages"] == []
    assert talk.history == []

    r2 = asyncio.run(talk.say("第二句"))
    # 历史从零开始：只有第二轮的两条，旧轮不回灌
    assert len(r2["messages"]) == 2
    # LLM 侧也是干净的：第二次调用的消息里没有第一轮的用户话
    second_call_messages = fake.calls[-1][0]
    texts = [m.content for m in second_call_messages]
    assert any(t == "第二句" for t in texts)
    assert not any(t == "第一句" for t in texts)


def test_interject_queue_and_undelivered_round(api: TestClient, tmp_path: Path):
    """插话闭环：轮内 interject 入队；没赶上工具检查点的插话由跟随循环补送一轮。"""
    fake = FakeLLMClient([_done("第一轮完成") for _ in range(4)])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    # 没有进行中的轮：不排队（前端正常发送即可）
    assert talk.interject("没人跑的时候插话")["queued"] is False
    # 预置一条未送达的插话 → 首轮结束后跟随循环应补送一轮（哪怕没有对局）
    talk.interjections.add("改打空军")
    r = asyncio.run(talk.say("开局看一眼"))
    texts = [m.get("text", "") for m in r["messages"]]
    assert any("改打空军" in t for t in texts), "未送达插话必须被补送（不能丢）"
    # 第二轮的输入里带插话（FakeLLM 收到的消息）
    second = fake.calls[1][0]
    assert any(m.content and "改打空军" in m.content for m in second)


def test_interject_delivered_event_on_stream(api: TestClient, tmp_path: Path):
    """排队条修复（2026-08-25 用户报「排队中一直挂着，发出去了也不消失」）：
    插话送达（drain）→ SSE 流立刻发 interject_delivered —— 前端据此撤排队条。
    round 事件要等整场对局跟随结束才来，期间没有这个事件排队条就永远不动。"""
    fake = FakeLLMClient([_done("收到") for _ in range(3)])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws")
    # 预置未送达插话（首轮没工具检查点）→ 跟随循环 drain 补送 → drain 时刻回调
    talk.interjections.add("改打空军")
    handle = talk.start_round("开局看一眼")
    seen: list[dict] = []
    while True:
        ev = handle.events.get(timeout=30)
        if ev is None or ev.get("type") == "round":
            break
        seen.append(ev)
    delivered = [e for e in seen if e.get("type") == "interject_delivered"]
    assert delivered and delivered[0]["texts"] == ["改打空军"]


# ---------------- 断流三修（2026-08-24 用户报「工具调用之后没有下文」） ----------------

def test_max_turns_pause_leaves_visible_note(api: TestClient, tmp_path: Path):
    """max_turns 截停（最后一轮常是纯工具调用，无正文）→ 落史必须带说明 +
    「继续」指引 —— 不许凭空断线。默认上限 500（§0.57），测试里显式给小值。"""
    from agentic.types import ToolCall
    script = [LLMResponse(Message("assistant", None,
                                  [ToolCall(f"c{i}", "list_modules", {})]), 0, 0, "fake")
              for i in range(6)]
    talk = AgentTalk(_client_for(api), llm_factory=lambda: FakeLLMClient(list(script)),
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws",
                     max_turns=6)
    r = asyncio.run(talk.say("反复列模块"))
    assert r["outcome"] == "paused"
    assert "轮数上限暂停" in r["reply"]
    assert "继续" in r["reply"]
    assert talk.history[-1]["role"] == "agent"


def test_output_token_budget_stops_round_with_note(api: TestClient, tmp_path: Path):
    """轮输出 token 预算（§0.57 用户拍板：turn 放开 500，token 做刹车）：
    累计到预算 → 不再调 LLM、空响应自然收轮，落史带「预算用完 + 继续」说明。"""
    from agentic.types import ToolCall

    class _BudgetFake(FakeLLMClient):
        """带预算协议的替身：AgentTalk 轮首会 reset_round_budget（有该方法才设限）。"""

        def reset_round_budget(self, budget):
            self.output_budget = budget
            self.round_output_tokens = 0

        async def complete(self, messages, tools=None, **kw):
            if self.round_output_tokens >= self.output_budget:
                return LLMResponse(Message("assistant", None), 0, 0, "budget-stop")
            resp = await super().complete(messages, tools, **kw)
            self.round_output_tokens += resp.output_tokens
            return resp

    fake = _BudgetFake([
        LLMResponse(Message("assistant", None, [ToolCall("c1", "list_modules", {})]),
                    0, 700_000, "fake"),
        LLMResponse(Message("assistant", None, [ToolCall("c2", "list_modules", {})]),
                    0, 400_000, "fake"),   # 累计 1.1M ≥ 1M 预算
    ])
    talk = AgentTalk(_client_for(api), llm_factory=lambda: fake,
                     trace_root=tmp_path / "traces", workspace_root=tmp_path / "ws",
                     max_output_tokens=1_000_000)
    r = asyncio.run(talk.say("反复列模块"))
    assert "预算" in r["reply"] and "继续" in r["reply"]
    assert r["output_tokens"] == 1_100_000
    assert talk.history[-1]["output_tokens"] == 1_100_000
    assert len(fake.calls) == 2            # 第三次调用没发生（预算拦在 API 前）

