"""api.app：应用装配（REST + WS 的入口壳）。

路由本体在 `api/routes/`（按资源分组），共享辅助在 `api/state.py`（沙盒泵 /
帧源解析 / 命令新鲜度门）—— 本文件只做三件事：常量（serve_api 的默认值来源）、
`create_app` 装配（状态挂 `app.state` 扁平键 + 挂载 routers + CORS）、模块级 `app`。

设计对着前端的 `FrameSource`（`plan-frontend.md` §4）：
- `GET /api/sources`              帧源清单（含中文标签、时间范围、topic 列表）
- `GET /api/sources/{id}/statics` 三个静态面（每局一次的东西不该走 WS）
- `GET /api/sources/{id}/jsonl`   整份 JSONL（复盘/夹具直接 fetch，就是现在的 `JsonlFrameSource`）
- `GET /api/schema`               不依赖任何会话的 flow 词表（编辑器可以先加载）
- `WS  /api/frames?source=&topics=&rate=`  按**游戏时间**节拍推帧 + 客户端控制

**不做**：鉴权、多用户、HTTPS（localhost 单用户，写在计划的不做清单里）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from game.catalog import load_all
from view.proposals import ProposalStore
from view.schema import REV

from api.routes import ROUTERS
from api.sources import SourceRegistry

#: 默认帧源目录（夹具与录制都落这里）
DEFAULT_FRAME_DIR = Path("web/public/fixtures")
#: 提案日志的**建议**落点（`tools/serve_api.py` 显式传它）。
#: **不放帧源目录**：那个目录是静态服务的（会被公开），而且提案是运行时数据、不该进版本库。
#: 注意 `create_app` 的默认是**不持久化**（见下）—— 默认落到一个共享文件会让每个
#: 不传路径的测试互相污染（实测踩过：手动跑的 35 条提案混进了单测）。
DEFAULT_PROPOSAL_LOG = Path("runtime/proposals.jsonl")
#: 规划文件的**建议**落点（同提案日志的姿态：runtime 数据不进版本库）。
#: `create_app` 默认**不持久化**（内存态）—— 理由同上：测试不传路径就不互污染。
DEFAULT_PLANS_DIR = Path("runtime/production-plans")   # I5 改名；旧 runtime/plans 由 serve_api 兜底识别
#: 地图规划文件的**建议**落点（同上）
DEFAULT_MAP_PLANS_DIR = Path("runtime/map-plans")
#: 对局记录的**建议**落点（二十六轮：复盘要有真数据源 —— live 帧流同步落 JSONL）。
#: 同样是 runtime 数据：测试不传路径就不录，serve_api 显式传 runtime/recordings。
DEFAULT_RECORDINGS_DIR = Path("runtime/recordings")
#: 装配清单（B1）的**建议**落点：loadout 是人管的装配预设（三件套引用），只读。
DEFAULT_LOADOUTS_DIR = Path("runtime/loadouts")


def create_app(frame_dir: Path | str | None = None,
               proposal_log: Path | str | None = None,
               plans_dir: Path | str | None = None,
               map_plans_dir: Path | str | None = None,
               recordings_dir: Path | str | None = None,
               strategies_dir: Path | str | None = None,
               loadouts_dir: Path | str | None = None,
               initial_states_dir: Path | str | None = None,
               agent_talk: "object | None" = None,
               agent_base: str | None = None) -> FastAPI:
    registry = SourceRegistry(Path(frame_dir) if frame_dir else DEFAULT_FRAME_DIR)
    registry.load_labels_from_index()

    app = FastAPI(title="sc2Agent view API", version=str(REV))
    # CORS：驾驶舱是独立 vite dev server（localhost:5273），API 在 127.0.0.1:8770 ——
    # 跨源。没这个中间件浏览器会把 probe/命令/提案全部拦掉，前端永远显示「后端未连接」
    # （真机教训：start.bat 时代用 Node 脚本验证时同源没问题，浏览器一上就断）。
    # 开发驾驶舱只绑 127.0.0.1；来源收在 localhost/127.0.0.1 任意端口，不构成暴露面。
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.registry = registry
    #: 离线沙盒会话。惰性创建：没人访问 `source=live` 就不建，也就不烧 CPU。
    app.state.session = None
    app.state.session_task = None
    #: 上一份观察包的 seq —— ADR-0009 的"替换而非追加"：新包 supersedes 旧包
    app.state.last_observation_seq = None
    #: 提案存储（B7）。落盘在帧源目录旁边 —— 提案的价值一半在历史，追加日志天然保留。
    # 默认 path=None = 只在内存里（进程结束就没了）。持久化必须显式要求 ——
    # 默认持久化到共享文件会让测试互相污染，而且"哪些提案属于这次运行"变得说不清。
    app.state.proposals = ProposalStore(
        load_all(), path=Path(proposal_log) if proposal_log else None)
    #: 规划存储（P0）：一个规划一个 YAML 文件；默认内存态，serve_api 显式传 runtime/plans。
    from view.plans import PlanStore

    app.state.plans = PlanStore(load_all(), Path(plans_dir) if plans_dir else None)
    #: 地图规划文件（P2）：默认地图锁定 + 复制新建；默认内存态。
    from view.map_plans import MapPlanStore

    app.state.map_plans = MapPlanStore(
        Path(map_plans_dir) if map_plans_dir else None, catalog=load_all())
    #: initial-state 快照（PLAN-V2 批 3）：默认内存态（出厂示例种子恒在）。
    from view.initial_states import InitialStateStore

    app.state.initial_states = InitialStateStore(
        Path(initial_states_dir) if initial_states_dir else None)
    #: 对局记录目录（二十六轮）：None = 不录（测试默认；录了测试之间会互相污染）。
    app.state.recordings_dir = Path(recordings_dir) if recordings_dir else None
    #: 策略文件存储（二十七轮「开放写策略，免审」）：default 从内置常量播种（锁定）。
    from api.session import DEFAULT_ASSEMBLY, DEFAULT_STRATEGY
    from view.strategies import StrategyStore

    app.state.strategies = StrategyStore(
        Path(strategies_dir) if strategies_dir else None, seed=(DEFAULT_STRATEGY, DEFAULT_ASSEMBLY))
    #: 装配清单（B1）：`session/start?loadout=<id>` 解析三件套 + 生产序列自动入队。
    #: 只读、文件即真相（默认内存态恒空 —— serve_api 显式传 runtime/loadouts）。
    from view.loadouts import LoadoutStore

    app.state.loadouts = LoadoutStore(
        Path(loadouts_dir) if loadouts_dir else None)
    #: 对话式顾问（P3 切片 A）。两条装配路径：测试注入现成实例（FakeLLM 脚本 +
    #: transport client）；serve_api 传 agent_base（自己回环访问 REST —— 工具面与
    #: UI 同一入口 U7，不走 store 直连）。None = 端点报「未启用」。
    app.state.agent_talk = agent_talk
    if app.state.agent_talk is None and agent_base:
        from agent.client import ApiClient
        from agent.talk import AgentTalk, openai_from_env

        app.state.agent_talk = AgentTalk(
            ApiClient(base=agent_base), llm_factory=openai_from_env,
            trace_root=Path("runtime/agent-talk/traces"),
            workspace_root=Path("runtime/agent-talk/workspace"),
            history_path=Path("runtime/agent-talk/history.json"),
            # 只读区（I20）：Agent 的文件树挂运行时产物 —— 录像/提案史与持久化同路径
            recordings_dir=app.state.recordings_dir,
            proposals_log=Path(proposal_log) if proposal_log else None,
            map_plans_dir=Path(map_plans_dir) if map_plans_dir else DEFAULT_MAP_PLANS_DIR)
    #: agent 记忆笔记（append-only jsonl）。默认挂在与对话同一棵 runtime 下。
    app.state.agent_notes_path = Path(
        "runtime/agent-talk/notes.jsonl"
        if agent_talk is not None or agent_base else "runtime/agent-notes.jsonl")

    for router in ROUTERS:
        app.include_router(router)
    return app


#: 供 `uvicorn api.app:app` 直接用（frame_dir 取默认；开发起服用 `tools/serve_api.py`，
#: 那个壳会先把 `modules/` 塞进 sys.path）
app = create_app()
