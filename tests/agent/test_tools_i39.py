"""agent 工具面 I39 批 A 直测：observe source 管道 / open 芯片 / swap 工具。

三个都是 tools.py 的薄壳行为——transport 打桩记录调用，不起真 app。
"""
import asyncio

from agent.client import ApiClient
from agent.tools import make_tools
from agent.workspace import ChangeLog


class _StubTransport:
    """记录调用的假 transport：GET/POST 都回固定 body，断言看 .calls。"""

    def __init__(self, body=None, status=200):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.body = body if body is not None else {"seq": 1, "facts": {}, "sections": {}}
        self.status = status

    def __call__(self, method: str, path: str, body: dict | None):
        self.calls.append((method, path, body))
        return self.status, self.body


def _tools(transport=None, changes=None):
    return make_tools(ApiClient(transport=transport or _StubTransport()),
                      source="live", changes=changes)


def _by_name(tools, name):
    (t,) = [t for t in tools if t.name == name]
    return t


def test_observe_passes_source_arg_to_api():
    """args.source 必须进 API 调用（I39 修的真 bug：此前闭包 source 吞掉参数，
    agent 传录像 id 也被当 live 查 → 误导性的「没有活跃会话」404）。"""
    tr = _StubTransport()
    tool = _by_name(_tools(tr), "observe")
    out = asyncio.run(tool.function({"source": "rec-20260825-104557-sc2"}))
    assert any("source=rec-20260825-104557-sc2" in p for _, p, _ in tr.calls), tr.calls
    assert "取观察失败" not in out


def test_observe_defaults_to_closure_source_when_arg_absent():
    tr = _StubTransport()
    tool = _by_name(_tools(tr), "observe")
    asyncio.run(tool.function({}))
    assert any("source=live" in p for _, p, _ in tr.calls)


def test_open_tool_adds_clickable_chip_for_supported_areas():
    """open(path)：三区白名单 → ChangeLog 出「打开」芯片（前端可点击 hash 由
    change_target 统一算，不信模型拼链接）。"""
    log = ChangeLog()
    tool = _by_name(_tools(changes=log), "open")
    out = asyncio.run(tool.function({"path": "map-plans/barracks-stack-test.yaml"}))
    (rec,) = log.drain()
    j = rec.to_json()
    assert j["area"] == "map_plan" and j["action"] == "open"
    assert j["target"].startswith("#/plan-map?map=barracks-stack-test")
    assert "已通知" in out


def test_open_tool_rejects_unsupported_paths():
    """区外路径（绝对路径/别的目录/裸 id）拒绝并说明白名单 —— 不静默成功。"""
    log = ChangeLog()
    tool = _by_name(_tools(changes=log), "open")
    for bad in ("C:/x/y.yaml", "traces/run1", "barracks-stack-test"):
        out = asyncio.run(tool.function({"path": bad}))
        assert out.startswith("拒绝"), bad
    assert log.drain() == []


def test_swap_strategy_tool_posts_to_session_swap():
    """swap_strategy：薄壳打 POST /api/session/swap?strategy=<id>，结果原样回给模型。"""
    tr = _StubTransport(body={"state": "运行中", "swap": {"ok": True}})
    tool = _by_name(_tools(tr), "swap_strategy")
    out = asyncio.run(tool.function({"strategy": "hold-ramp-v2"}))
    (method, path, body), = tr.calls
    assert method == "POST" and path.startswith("/api/session/swap")
    assert "strategy=hold-ramp-v2" in path
    assert "hold-ramp-v2" in out or "swap" in out


def test_swap_strategy_tool_relays_api_error_reason():
    """API 的约束拒绝（编译红/group_slots 不一致）原样透传 —— 模型要能读原因修策略。"""
    from agent.client import ApiError

    class _ErrTransport(_StubTransport):
        def __call__(self, method, path, body):
            self.calls.append((method, path, body))
            raise ApiError(409, "新策略 group_slots ['main','scout'] 与当前装配 "
                                "['main'] 不一致 —— 热切不碰装配")
    tool = _by_name(_tools(_ErrTransport()), "swap_strategy")
    out = asyncio.run(tool.function({"strategy": "bad"}))
    assert "group_slots" in out and "409" in out
