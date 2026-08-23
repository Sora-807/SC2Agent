"""agent 语义工具面（2026-08-22 文件工作区改造后的存活部分）。

规划文件的读写已收进文件契约（test_workspace.py 测其存储后端 ApiWorkspace），
这里锁的是文件表达不了的**动作**：干跑试算、起会话、战术素材只读。
用直连 ASGI 的 transport（同 test_round 的做法），transport 支持 PUT/DELETE。
"""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.client import ApiClient, ApiError
from agent.tools import make_planning_tools
from api.app import create_app


def _client_for(app_client: TestClient) -> ApiClient:
    def transport(method: str, path: str, body: dict | None):
        if method == "GET":
            res = app_client.get(path)
        elif method == "PUT":
            res = app_client.put(path, json=body or {})
        elif method == "DELETE":
            res = app_client.delete(path)
        else:
            res = app_client.post(path, json=body or {})
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {"detail": res.text}
    return ApiClient(transport=transport)


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    """磁盘态 store：start_session(map_plan=…) 需要真文件（子进程装配读它）。"""
    return TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                 plans_dir=tmp_path / "plans",
                                 map_plans_dir=tmp_path / "map-plans"))


@pytest.fixture()
def tools(api: TestClient) -> dict:
    return {t.name: t for t in make_planning_tools(_client_for(api))}


def run(tool) -> str:  # noqa: ANN001 —— Tool 类型从 agentic 来，窄化没意义
    return asyncio.run(tool.function({}))


def call(tool, args: dict) -> str:
    return asyncio.run(tool.function(args))


QUEUE = [
    {"op": "build", "type": "terran/refinery", "count": 1},
    {"op": "build", "type": "terran/barracks", "count": 2},
    {"op": "train", "type": "terran/marine", "count": 6},
]


# ---------------- 干跑试算 ----------------

def test_simulate_reports_curve_events_and_alerts(tools):
    out = call(tools["simulate_plan"], {"queue": QUEUE, "horizon": 240})
    assert "干跑" in out and "曲线末点" in out and "人口" in out
    assert "前瞻警报" in out  # 没有警报也要有这一行（（无）），agent 才知道算完了
    # 纯 build/train 无 research/assign 的队列不该有被跳过项
    assert "被跳过" not in out


def test_simulate_from_plan_id(tools):
    ws_api = tools  # noqa: F841 —— 命名沿用 fixture；建规划走 REST（文件域的活）
    out = call(tools["simulate_plan"], {"plan_id": "default", "horizon": 120})
    assert "default" in out and "曲线末点" in out


def test_simulate_needs_queue_or_plan(tools):
    assert "至少给一个" in call(tools["simulate_plan"], {})


# ---------------- 会话 ----------------

def test_start_session_offline_with_map_plan(tools, api):
    api.post("/api/map-plans", json={"id": "agent-m3", "copy_from": "default-bl"})
    out = call(tools["start_session"],
               {"driver": "offline", "map_plan": "agent-m3", "autotick": False})
    assert "会话已启动" in out and "agent-m3" in out
    assert api.app.state.session is not None


def test_start_session_rejects_unknown_driver(tools):
    assert "拒绝" in call(tools["start_session"], {"driver": "zerg"})


# ---------------- 战术素材（只读） ----------------

def test_list_modules_shows_reference_library(tools):
    out = run(tools["list_modules"])
    assert "bio_tank_opening" in out and "步坦" in out
    assert "factory_chain" in out and "basic_opening" in out


def test_read_module_exports_items(tools):
    out = call(tools["read_module"], {"ref": "bio_tank_opening"})
    assert "步坦" in out and "terran/" in out and "0." in out
    assert "工厂链" in call(tools["read_module"], {"ref": "factory_chain"})


def test_read_module_unknown_ref_is_readable(tools):
    assert "list_modules" in call(tools["read_module"], {"ref": "nope"})


def test_read_current_strategy_is_yaml(tools):
    out = run(tools["read_current_strategy"])
    assert "strategy" in out and "```yaml" in out
    assert "sandbox_hold" in out and "assembly" in out


# ---------------- transport 层 ----------------

def test_put_goes_through_transport(api: TestClient):
    """ApiClient._put 走 transport 的 PUT 分支（test_round 的旧 transport 只有 GET/POST）。

    用 map-plans 的 404 路径验：plans 的 PUT 是 upsert 语义（未知 id 不 404，直接建），
    那条路径抛不出来。
    """
    client = _client_for(api)
    try:
        client.map_plan_save("nope", [])
        raise AssertionError("404 应该抛 ApiError")
    except ApiError as exc:
        assert exc.status == 404


# ---------------- BaseAgent HTML 可视化（2026-08-22 用户要求开启） ----------------

def test_run_round_generates_trace_html(api: TestClient, tmp_path: Path):
    """run_round 收尾必须调 tracer.finalize_summary —— Engine.start 不代收尾，
    不调就只有 jsonl 没有 trace.html（自包含可视化）。

    demo=planning 现在走文件链：ls → read plans/default.yaml → write
    plans/agent-demo.yaml → simulate —— 顺带验了虚拟工作区在真引擎里能跑通。
    """
    from agent.run import run_round

    out = asyncio.run(run_round(base="", task="t", dry=True, source="live",
                                trace_root=tmp_path / "traces",
                                workspace_root=tmp_path / "ws",
                                demo="planning", client=_client_for(api)))
    html = Path(out["trace_html"])
    assert html.exists(), "trace.html 没生成 —— finalize_summary 没被调用？"
    text = html.read_text(encoding="utf-8")
    assert "<html" in text.lower() and "advisor" in text
    # 聚合产物一起落地（token/轮次汇总与树）
    assert (html.parent / "summary.json").exists()
    assert (html.parent / "tree.json").exists()
    assert (html.parent / "trace.md").exists()
    # 文件链真的落了盘：agent-demo 规划经虚拟工作区写进了 API 的 store
    assert any(r["id"] == "agent-demo" for r in api.get("/api/plans").json())
    # 轮末改动清单（ChangeRecord → 前端 chip 的数据源）
    assert any(c["area"] == "plan" and c["ref"] == "agent-demo" for c in out["changes"])
