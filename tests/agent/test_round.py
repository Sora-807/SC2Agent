"""agent 回合（引入 BaseAgent 之后的第一个闭环）。

守的第一条最重要：**agent 拿不到直接改状态的工具**（§6 P1）。
这不是靠提示词请求它别那么做 —— 提示词能被忽略，缺失的工具不能被调用。
所以有一条测试直接断言工具集里没有命令类工具。

用 `FakeLLMClient` + 直连 ASGI 的 transport：不打网络、不起服务，
验的是**装配**（工具能被调用、提案能落地、边界能拦住），不是 LLM 的判断力。
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.client import ApiClient, ApiError
from agent.spec import AdvisorSpec
from agent.tools import make_tools
from agentic import DiskWorkspace, Engine, Tracer
from agentic.llm import FakeLLMClient
from agentic.types import LLMResponse, Message, ToolCall
from api.app import create_app


def _client_for(app_client: TestClient) -> ApiClient:
    def transport(method: str, path: str, body: dict | None):
        res = (app_client.get(path) if method == "GET"
               else app_client.post(path, json=body or {}))
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {"detail": res.text}
    return ApiClient(transport=transport)


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "proposals.jsonl"))
    c.post("/api/session/start", params={"autotick": "false"})
    sess = c.app.state.session
    for _ in range(5):
        sess.tick()
    # 造一个"缺气卡队首"的局面：工厂要气，队列里没有气的来源
    c.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main", "items": [
            {"op": "build", "type": "terran/factory",
             "placement": {"kind": "in_region", "region": "home"}},
            {"op": "train", "type": "terran/marine", "count": 6}]})
    for _ in range(3):
        sess.tick()
    return c


PROPOSAL_ARGS = {
    "kind": "production_queue",
    "title_zh": "补精炼厂解开缺气的队首",
    "rationale_zh": "队首在等气而队列里没有气的来源，先插精炼厂，否则整队一直冻结。",
    "target": {"queue": "main"},
    "hunks": [{"id": "h1", "kind": "insert", "text_zh": "队首插精炼厂",
               "payload": {"index": 0,
                           "item": {"op": "build", "type": "terran/refinery", "count": 1}}}],
}


def _script(*calls: ToolCall) -> FakeLLMClient:
    return FakeLLMClient([
        LLMResponse(Message("assistant", None, [c]), 0, 0, "fake") for c in calls
    ])


async def _run(api_client: TestClient, llm: FakeLLMClient, tmp_path: Path) -> object:
    engine = Engine(llm, Tracer(tmp_path / "traces"),
                    workspace=DiskWorkspace(tmp_path / "ws"), max_turns=6)
    engine.register("advisor", AdvisorSpec(_client_for(api_client)), entry=True)
    return await engine.start("advisor#1", "找出最值得修的一件生产问题并提案")


# ---------------- 授权边界（§6 P1，P3 起边界收窄为「对局状态」） ----------------

#: 完整工具面（2026-08-22 文件工作区改造）：文件契约 + 对局域 + 语义动作。
#: 改这个集合 = 改授权边界，必须连着 WORKLOG 的决策记录一起改。
EXPECTED_TOOLS = {
    "done",
    # 文件契约（规划文件读写；存储后端 = agent.workspace.ApiWorkspace；
    # write/append/edit/insert 是 lint 版 —— 行为不变，memory 写入附软提示）
    "ls", "read", "glob", "grep", "write", "append", "edit", "insert", "delete", "stat",
    # 对局域（提案制，提交即自动应用）。写面清单不是工具：只读文件 system/surface.md
    "observe", "propose",
    # 语义动作（文件表达不了的：干跑/会话/战术素材）。
    # 2026-08-23 工具审视 19→17：write_surface/read_current_strategy 退役
    "simulate_plan", "start_session",
    "list_modules", "read_module",
}


def test_agent_has_no_direct_command_tools():
    """**最重要的一条**：agent 拿不到直改**对局状态**的工具。

    §6 P1 靠"不给那个工具"保证，不靠提示词自觉。这条测试就是那道锁。
    文件工作区改造（2026-08-22）把边界收窄为「对局状态」：离线规划文件经
    文件契约直改（写时校验）；但 queue_op / set_worker_quota 这类对局内命令
    **仍然不存在** —— live 改动依旧只有 propose 一条路。
    """
    names = AdvisorSpec(ApiClient()).tools().names()
    assert set(names) == EXPECTED_TOOLS
    for forbidden in ("queue_op", "set_worker_quota", "submit_queue", "commands"):
        assert forbidden not in names


def test_agent_has_the_file_contract_tools():
    """文件契约必须在场：规划文件的读写全走它（CRUD 包装层已删，2026-08-22）。"""
    names = set(AdvisorSpec(ApiClient()).tools().names())
    for file_tool in ("write", "edit", "read", "ls", "glob", "grep", "insert", "delete",
                      "append", "stat"):
        assert file_tool in names
    # CRUD 包装层不该回来 —— 它们是文件契约的重复面
    for gone in ("list_plans", "read_plan", "create_plan", "write_plan",
                 "list_map_plans", "read_map_plan", "create_map_plan", "write_map_plan",
                 "list_notes", "save_note"):
        assert gone not in names


# ---------------- 一个完整回合 ----------------

def test_round_auto_applies_a_valid_proposal(api: TestClient, tmp_path: Path):
    """审批停用（2026-08-22）：校验通过的提案创建后**立即自动应用**，decision.auto 留审计。"""
    llm = _script(
        ToolCall("c1", "observe", {}),
        ToolCall("c2", "propose", PROPOSAL_ARGS),
        ToolCall("c3", "done", {"result": "提了一条：队首插精炼厂"}),
    )
    asyncio.run(_run(api, llm, tmp_path))

    rows = api.get("/api/proposals").json()
    assert len(rows) == 1
    p = rows[0]
    assert p["author"] == "agent"
    assert p["status"] == "已接受"
    assert p["decision"]["auto"] is True
    assert p["validation"]["ok"] is True
    assert p["preview"]["kind"] == "projection_pair"
    # anchor 基于会话的当前 seq —— 这是 P5 失效判断的依据
    assert p["anchor"]["seq"] == api.app.state.session.seq
    # 应用真的落了地：队首现在是精炼厂（走的是与 agent 相同的命令路径，P4）
    items = api.app.state.session.queue_items("main")
    assert items[0].type == "terran/refinery"


def test_observe_gives_the_agent_the_seq_to_use(api: TestClient):
    """观察工具必须把 based_on_seq 明确告诉 agent（否则它会瞎猜或漏填）。"""
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    text = asyncio.run(tools_["observe"].function({}))
    assert "当前观察" in text
    assert "based_on_seq" in text
    assert "[机器可读]" in text
    assert str(api.app.state.session.seq) in text


def test_observe_reports_the_blockage_so_the_agent_can_act(api: TestClient):
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    text = asyncio.run(tools_["observe"].function({}))
    assert "阻塞" in text
    assert "工厂" in text


def test_surface_file_tells_it_what_it_cannot_do(api: TestClient):
    """写面清单挂成只读文件 system/surface.md（write_surface 工具退役，读=文件）。"""
    from agent.readonly import SurfaceArea

    text = SurfaceArea(_client_for(api)).read("system/surface.md")
    assert "queue_ops" in text and "不支持" in text
    assert "based_on_seq" in text
    # 只读：handles 覆盖 system/ 前缀，read 别的路径要指路而不是空字符串
    import pytest
    from agentic.workspace.workspace import WorkspaceError

    with pytest.raises(WorkspaceError):
        SurfaceArea(_client_for(api)).read("system/other.md")


# ---------------- 工具层的拦截（在打到 api 之前） ----------------

def test_propose_refuses_without_a_rationale(api: TestClient):
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    out = asyncio.run(tools_["propose"].function({**PROPOSAL_ARGS, "rationale_zh": "  "}))
    assert "rationale_zh 必填" in out
    assert api.get("/api/proposals").json() == [], "不该产生半成品提案"


def test_propose_refuses_empty_hunks(api: TestClient):
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    out = asyncio.run(tools_["propose"].function({**PROPOSAL_ARGS, "hunks": []}))
    assert "hunks 不能为空" in out
    assert api.get("/api/proposals").json() == []


def test_invalid_proposal_comes_back_with_the_reason_not_silence(api: TestClient):
    """校验不通过要把原因喂回 agent，它才可能改对（P2 的 agent 侧）。"""
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    out = asyncio.run(tools_["propose"].function({
        **PROPOSAL_ARGS,
        "hunks": [{"id": "h1", "kind": "delete", "text_zh": "删第 9 项",
                   "payload": {"index": 9}}]}))
    assert "校验未通过" in out and "越界" in out
    assert "对历史可见" in out
    assert api.get("/api/proposals").json()[0]["validation"]["ok"] is False


def test_rejection_reason_flows_back_into_the_observation(api: TestClient, tmp_path: Path):
    """**§6 P3 的另一半**：用户拒绝时写的理由必须回到 agent 面前。

    审批停用后这是**预留的人工通道**（reject 端点保留）：用户手动否掉一条
    已自动应用的提案时，理由照样回流 —— 否则它会一遍遍推同一个被拒的提案。
    """
    llm = _script(
        ToolCall("c1", "observe", {}),
        ToolCall("c2", "propose", PROPOSAL_ARGS),
        ToolCall("c3", "done", {"result": "ok"}),
    )
    asyncio.run(_run(api, llm, tmp_path))
    pid = api.get("/api/proposals").json()[0]["id"]
    api.post(f"/api/proposals/{pid}/reject", json={"comment_zh": "这波先开矿，不急着补气"})
    api.app.state.session.tick()          # 推一帧让 proposals 帧带上新状态

    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    text = asyncio.run(tools_["observe"].function({}))
    assert "提案历史" in text
    assert "这波先开矿" in text, "用户的拒绝理由必须出现在观察包里"
    assert "被拒过的方向不要原样再提" in text


def test_applied_proposals_show_in_history_so_it_does_not_double_propose(api: TestClient,
                                                                          tmp_path: Path):
    """自动应用后没有"待审批"了，防重复提的依据变成提案历史里的 [已接受] 行。"""
    llm = _script(
        ToolCall("c1", "observe", {}),
        ToolCall("c2", "propose", PROPOSAL_ARGS),
        ToolCall("c3", "done", {"result": "ok"}),
    )
    asyncio.run(_run(api, llm, tmp_path))
    api.app.state.session.tick()
    tools_ = {t.name: t for t in make_tools(_client_for(api))}
    text = asyncio.run(tools_["observe"].function({}))
    assert "提案历史" in text
    assert "[已接受]" in text
    assert PROPOSAL_ARGS["title_zh"] in text


def test_api_error_classifies_stale_observation():
    """agent 要能区分"世界变了"（重取观察）和"请求不合法"（别重试）。"""
    stale = ApiError(409, {"reason": "过期", "based_on_seq": 3, "current_seq": 20})
    assert stale.stale is True
    assert ApiError(409, "没有运行中的会话").stale is False
    assert ApiError(400, "research 暂不支持").stale is False


def test_agent_round_is_visible_in_the_ui_frames(api: TestClient, tmp_path: Path):
    """agent 干的事必须在 UI 看得见（决策 U7：共用入口，没有后门）。"""
    llm = _script(
        ToolCall("c1", "observe", {}),
        ToolCall("c2", "propose", PROPOSAL_ARGS),
        ToolCall("c3", "done", {"result": "ok"}),
    )
    asyncio.run(_run(api, llm, tmp_path))
    api.app.state.session.tick()          # 推一帧，让 proposals 帧发出来
    frames = api.get("/api/sources/live/frames", params={"game_time": 999999}).json()
    by = {f["topic"]: f for f in frames}
    assert "proposals" in by, "提案必须出现在帧里，UI 才看得见"
    assert by["proposals"]["payload"]["proposals"][0]["author"] == "agent"
