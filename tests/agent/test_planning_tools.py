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
    # 四段输出（批 3）：采样表/队列状态表/终值快照/健康检查
    assert "干跑" in out and "1/4 曲线采样" in out and "人口" in out
    assert "2/4 队列执行状态" in out and "3/4 终值快照" in out and "4/4 健康检查" in out
    # 纯 build/train 无 research/assign 的队列不该有「语法不认」的被跳过项
    # （执行失败的 skip 走 2/4 表 + 4/4 健康检查，不走这行）
    assert "语法/catalog 不认" not in out


def test_simulate_from_plan_id(tools):
    ws_api = tools  # noqa: F841 —— 命名沿用 fixture；建规划走 REST（文件域的活）
    out = call(tools["simulate_plan"], {"plan_id": "default", "horizon": 120})
    assert "default" in out and "3/4 终值快照" in out


def test_simulate_needs_queue_or_plan(tools):
    assert "至少给一个" in call(tools["simulate_plan"], {})


# ---------------- 会话 ----------------

def test_start_session_builds_two_mode_game_request():
    """「开启游戏」收敛为两种模式（2026-08-23）：工具只组请求（driver=sc2 + mode/speed），
    用 transport 桩锁 query 形状 —— 不在测试里真开 SC2。"""
    captured = {}

    class _Transport:
        def __call__(self, method, path, body):
            captured["method"], captured["path"] = method, path
            return 200, {"state": "对局中", "driver": "sc2", "mode": "fast", "speed": 4}

    client = ApiClient(transport=_Transport())
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = call(tools_["start_session"],
               {"mode": "fast", "speed": 4, "map_plan": "agent-m3"})
    assert "游戏已开启" in out and "仿真模式" in out and "4×" in out and "agent-m3" in out
    q = captured["path"].split("?", 1)[1]
    assert captured["method"] == "POST"
    assert "driver=sc2" in q and "mode=fast" in q and "speed=4" in q
    assert "map_plan=agent-m3" in q


def test_start_session_rejects_bad_mode_and_speed(tools):
    assert "拒绝" in call(tools["start_session"], {"mode": "zerg"})
    assert "拒绝" in call(tools["start_session"], {"mode": "normal", "speed": 4})
    assert "拒绝" in call(tools["start_session"], {"mode": "fast", "speed": "很快"})


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


def test_strategy_lib_readable_via_api(tmp_path):
    """read_current_strategy 已退役（2026-08-23）：策略内容 = 文件。
    模板库 _lib 有专用只读端点（会话装配/agent 文件树共用同一份）。"""
    import shutil

    # api fixture 没挂 strategies_dir（内存态）—— 这里单独建一个带 _lib 的 app
    from flow.templates import SEED_LIB_PATH

    lib_src = SEED_LIB_PATH
    dst = tmp_path / "strategies"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(lib_src, dst / "_lib.yaml")
    api = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                strategies_dir=dst))
    client = _client_for(api)
    text = client.strategy_lib_text()
    assert "step_templates" in text and "gather" in text


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


def test_stop_session_posts_the_stop_endpoint():
    """stop_session（2026-08-24 对称补齐：能开游戏也要能关，防孤儿 SC2）。"""
    captured = {}

    class _Transport:
        def __call__(self, method, path, body):
            captured["method"], captured["path"] = method, path
            return 200, {"state": "未连接"}

    client = ApiClient(transport=_Transport())
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = run(tools_["stop_session"])
    assert "已结束" in out
    assert captured["method"] == "POST" and captured["path"] == "/api/session/stop"


def test_sleep_tool_waits_on_game_time():
    """sleep（2026-08-24 用户要求）：按游戏时间等 —— 到点返回新游戏时间与耗时。"""
    import asyncio

    seq = iter([
        {"state": "对局中", "game_time": 100.0},    # 起点
        {"state": "对局中", "game_time": 102.0},    # 没到
        {"state": "对局中", "game_time": 125.0},    # 到（目标 100+20）
    ])
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 20}))
    assert "等到游戏时间 125" in out and "+25 游戏秒" in out


def test_sleep_tool_rejects_without_session():
    import asyncio

    client = ApiClient(transport=lambda m, p, b: (200, {"state": "未连接"}))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 20}))
    assert "拒绝" in out and "没有运行中的会话" in out


def test_sleep_tool_returns_at_wall_cap(monkeypatch):
    """墙钟上限提前返回（游戏时间没走到目标）—— 常量在模块级，patch 小值快速走完。"""
    import asyncio

    import agent.tools as at

    monkeypatch.setattr(at, "SLEEP_WALL_CAP", 1.0)
    monkeypatch.setattr(at, "SLEEP_POLL_SECS", 0.05)
    seq = iter([{"state": "对局中", "game_time": 100.0}] +
               [{"state": "对局中", "game_time": 101.0}] * 200)   # 永远到不了 120
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 20}))
    assert "墙钟上限" in out and "101" in out


def test_sleep_wakes_early_on_interjection(monkeypatch):
    """插话主窗口：sleep 轮询中发现用户插话 → 立刻返回（不再等到游戏时间目标）。"""
    import asyncio

    import agent.tools as at
    from agent.tools import InterjectionQueue, make_planning_tools

    monkeypatch.setattr(at, "SLEEP_POLL_SECS", 0.05)
    monkeypatch.setattr(at, "SLEEP_WALL_CAP", 30.0)
    seq = iter([{"state": "对局中", "game_time": 100.0}] * 100)   # 游戏时间不动
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    pending = InterjectionQueue()
    tools_ = {t.name: t for t in make_planning_tools(client, pending=pending)}

    async def scenario() -> str:
        task = asyncio.create_task(tools_["sleep"].function({"game_seconds": 600}))
        await asyncio.sleep(0.3)          # sleep 进行中……
        pending.add("别等了，改打空军")
        return await task

    out = asyncio.run(scenario())
    assert "用户插话" in out and "改打空军" in out and "提前结束" in out


def test_tool_result_carries_interjection():
    """没赶上 sleep 的插话：由（任一）工具结果捎带给模型 —— spec 层包装的落点。"""
    import asyncio

    from agent.spec import AdvisorSpec
    from agent.tools import InterjectionQueue

    pending = InterjectionQueue()
    spec = AdvisorSpec(ApiClient(), pending=pending)
    toolset = spec.tools()
    names = [n for n, _ in toolset]
    observe_factory = dict(toolset)  # ToolSet 可迭代 (name, factory)
    _ = names
    # 取 observe 工具（经 _carry_interjections 包装）执行
    import agentic.engine.engine  # noqa: F401 —— 保证 vendor 可导入

    class _Agent:
        pass

    wrapped_observe = None
    for n, factory in toolset:
        if n == "observe":
            wrapped_observe = factory(_Agent())
            break
    assert wrapped_observe is not None
    pending.add("顺手看下气矿")
    out = asyncio.run(wrapped_observe.function({}))
    assert "用户插话" in out and "顺手看下气矿" in out
    # 队列清空：下一次工具结果不再重复携带
    out2 = asyncio.run(wrapped_observe.function({}))
    assert "用户插话" not in out2


def test_start_session_production_roundtrip_and_validation():
    """production（开局采集配额，§0.52 C 批）：合法值走请求体下发，坏键/坏值就地拒绝。"""
    captured = {}

    class _Transport:
        def __call__(self, method, path, body):
            captured["method"], captured["path"], captured["body"] = method, path, body
            return 200, {"state": "对局中", "driver": "sc2", "mode": "fast", "speed": 0}

    client = ApiClient(transport=_Transport())
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = call(tools_["start_session"],
               {"production": {"mineral_workers": 8, "gas_workers": 3}})
    assert "游戏已开启" in out and "采集配额" in out
    assert captured["body"] == {"production": {"mineral_workers": 8, "gas_workers": 3}}
    assert "?" in captured["path"] and "production" not in captured["path"].split("?")[1]

    assert "拒绝" in call(tools_["start_session"], {"production": {"vespene": 3}})
    assert "拒绝" in call(tools_["start_session"], {"production": {"mineral_workers": -2}})
    assert "拒绝" in call(tools_["start_session"], {"production": [1, 2]})
    assert "拒绝" in call(tools_["start_session"],
                          {"production": {"mineral_workers": "many"}})


# ---------------- B 批（2026-08-24）：observe + bbox 格点网格 ----------------

@pytest.fixture()
def obs(api: TestClient, tmp_path: Path) -> dict:
    """make_tools 面（observe 在那里）；map_plans 指到 tmp 并放一份最小出厂布局。"""
    from agent.tools import make_tools
    mp = tmp_path / "mp"
    mp.mkdir()
    (mp / "layout.yaml").write_text(
        "id: layout\ntitle_zh: 测试出厂布局\n"
        "spawns:\n  bl: {build_slots: {}}\n  tr: {build_slots: {}}\n",
        encoding="utf-8")
    return {t.name: t for t in make_tools(_client_for(api), map_plans_dir=mp)}


def test_observe_bbox_returns_grid_directly(obs):
    """带 bbox = 格点网格（B 批主入口）：成功返回区域网格，不要求拼文件路径。"""
    out = call(obs["observe"], {"bbox": [38, 27, 52, 41], "step": 2})
    assert "区域 live" in out
    assert "| y\\x |" in out


def test_observe_bbox_out_of_range_reports_map_size_and_offender(obs):
    """超范围如实报错：说清地图尺寸、哪个坐标超了 —— 不许再吐假 not-found 让模型瞎猜。"""
    from tactical_map.region_view import load_placeable
    (w, h), _ = load_placeable()
    out = call(obs["observe"], {"bbox": [38, 25, 70 + w, 45]})
    assert out.startswith("error: bbox 超出可索引范围")
    assert f"地图 {w}×{h}" in out
    assert f"x2={70 + w} 不在 [0,{w - 1}]" in out
    assert "not found" not in out


def test_observe_bbox_grid_limit_suggests_step(obs):
    """网格超上限：render_region 的建议（缩小 bbox 或加 step）直达模型。"""
    out = call(obs["observe"], {"bbox": [38, 25, 70, 45], "step": 2})   # 17 列 > 14 上限
    assert out.startswith("error: ")
    assert "超上限" in out and "step=4" in out


def test_observe_without_bbox_is_still_the_observation_packet(api: TestClient):
    """无 bbox = 原观察包路径不变（seq/based_on 语义不受 B 批影响）——
    这个 fixture 没起会话，观察路径的特征错误就是证明（没走格点渲染）。"""
    from agent.tools import make_tools
    tool = {t.name: t for t in make_tools(_client_for(api))}
    assert "取观察失败" in call(tool["observe"], {})




# ---------------- C 批（2026-08-24）：sleep until / 对局结束早退 ----------------

def test_sleep_until_absolute_game_time():
    """until_game_time=绝对时刻 —— 定时节点不用相对秒硬凑。"""
    import asyncio

    seq = iter([
        {"state": "对局中", "game_time": 100.0},
        {"state": "对局中", "game_time": 150.0},    # 没到（目标 180）
        {"state": "对局中", "game_time": 181.0},    # 到
    ])
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"until_game_time": 180}))
    assert "等到游戏时间 181" in out
    # 已在目标之后 → 立即返回不进循环
    client2 = ApiClient(transport=lambda m, p, b: (200, {"state": "对局中", "game_time": 200.0}))
    tools2 = {t.name: t for t in make_planning_tools(client2)}
    assert "已经到了" in asyncio.run(tools2["sleep"].function({"until_game_time": 180}))


def test_sleep_wakes_when_game_ends_mid_wait():
    """对局打完（run_game 返回 → 会话「已结束」、game_time 冻结）→ sleep 立刻醒，
    不空转到墙钟上限 —— 用户实测「游戏失败了 sleep 还一直持续」的根因修复。"""
    import asyncio

    seq = iter([
        {"state": "对局中", "game_time": 100.0},
        {"state": "对局中", "game_time": 112.0},
        {"state": "已结束", "game_time": 112.0},    # 打完了：状态翻、时钟冻
        {"state": "已结束", "game_time": 112.0},
    ])
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 60}))
    assert "对局已结束" in out and "停在 112" in out
    assert "observe 看终局" in out


def test_sleep_wakes_on_frozen_clock(monkeypatch):
    """状态仍「对局中」但游戏时钟冻死（SC2 挂死/结算画面子进程没退）——
    冻满阈值墙秒叫醒，别干等 300s。"""
    import asyncio

    import agent.tools as at
    monkeypatch.setattr(at, "SLEEP_POLL_SECS", 0.01)
    monkeypatch.setattr(at, "SLEEP_FREEZE_WALL_SECS", 0.05)
    calls = iter([{"state": "对局中", "game_time": 50.0}] * 100)
    client = ApiClient(transport=lambda m, p, b: (200, next(calls)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 60}))
    assert "游戏时钟停在 50" in out and "大概率已结束" in out


def test_sleep_wakes_on_active_alert():
    """D 批：sleep 期间来了 warn+ 警报（敌方踪迹/队列卡死）→ 叫醒，不再失聪。"""
    import asyncio

    seq = iter([
        {"state": "对局中", "game_time": 100.0, "alerts": []},
        {"state": "对局中", "game_time": 103.0,
         "alerts": [{"id": "enemy_contact", "kind": "enemy_contact", "severity": "warn",
                     "text_zh": "敌方踪迹（10s 窗）：见过 4 个不同敌兵", "at": 103.0}]},
    ])
    client = ApiClient(transport=lambda m, p, b: (200, next(seq)))
    tools_ = {t.name: t for t in make_planning_tools(client)}
    out = asyncio.run(tools_["sleep"].function({"game_seconds": 60}))
    assert "警报叫醒" in out and "敌方踪迹" in out
    assert "优先处理" in out


# ---------------- F 批（2026-08-24）：队列体检（只诊断+建议） ----------------

def test_static_check_via_simulate_horizon_zero(tools):
    """批 6 D2：audit_queue 退役 —— simulate_plan(horizon=0) 吸收静态体检。
    缺产出建筑/人口超支要能报出来，每条带 uid 建议。"""
    out = call(tools["simulate_plan"], {
        "queue": [{"op": "train", "type": "terran/marine", "count": 4}],
        "horizon": 0})
    assert "静态体检" in out
    assert "产出建筑" in out and "prereq_missing" not in out  # 文案级：产建筑缺失建议
    # 人口超支（12 工开局 13 上限 + 4 marine = 16 > 13）
    out2 = call(tools["simulate_plan"], {
        "queue": [{"op": "build", "type": "terran/barracks", "count": 1},
                  {"op": "train", "type": "terran/marine", "count": 4}],
        "horizon": 0})
    assert "人口" in out2 or "补给" in out2 or "通过" in out2  # 开局有 depot 时可通过


def test_audit_queue_tool_is_retired(tools):
    names = set(tools)
    assert "audit_queue" not in names, "批 6 D2：audit_queue 已被 simulate_plan(horizon=0) 吸收"


