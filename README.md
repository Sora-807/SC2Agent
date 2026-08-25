# SC2Agent

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Status](https://img.shields.io/badge/status-experimental-orange.svg)

把大语言模型（LLM）接入《星际争霸 II》决策回路的 Agent 框架：在线让 LLM 克服自身延迟操控实时对局，离线把人和 Agent 放进同一套规划与复盘回路。

包名 `sc2-agent-next`，当前版本 `0.0.1`。**实验性个人项目**，API 与数据格式可能随时变更。

---

## 目录

- [核心特性](#核心特性)
- [架构概览](#架构概览)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [运行模式](#运行模式)
- [测试](#测试)
- [文档](#文档)
- [设计要点](#设计要点)
- [许可证](#许可证)

## 核心特性

**一套帧契约贯通全链路。** live 推送、复盘录制、离线模拟、Agent 观察包全部基于同一份 `ViewFrame` 契约（REV 18，14 个 topic）。回放与实时看到的不是两份数据；契约由前后端两侧的 contract test 锁死。

**Agent 以提案方式介入对局。** Agent 没有直接命令工具，对生产队列的修改只能通过提案（hunk：insert / delete / modify / reorder）提交，经校验、双投影预览后生效。提案自动应用（人审环节已于 2026-08 停用，审计记录保留）；校验错误回流给 Agent 下一轮。这一边界靠"不提供该工具"物理保证。

**命令三态裁决。** 每个命令的结局必居其一：`ok`（驱动确认）/ `failed`（驱动拒绝并附原因）/ `None`（已受理待裁决）。真机异步世界里的 `None` 是正常状态，不视为错误。

**离线规划与复盘回路。** 确定性仿真器作为人与 Agent 的共同验证器：提案可双投影对比"采纳前后"曲线；对局录像支持跨会话回放；Agent 工作区是磁盘物理隔离的共享文件面；对局期间 Agent 持续跟随（observe → propose → 验证闭环）。

**严格分层。** `game`（零依赖地基）→ `driver`（唯一接触 SC2）→ 引擎层（`world` / `flow` / `constraint` / `planner` / `tactical_map` / `production`）→ `view`（只读视图）→ `api`（传输）。依赖方向由架构测试机械锁死，违反即测试失败。

**评测管线。** `eval/` 提供可组装的 Agent 评测框架：Fixture / Task / Runner / Grader 四契约，确定性 grader 与 LLM 盲评双轨，场景注册制 CLI。

## 架构概览

```
sc2Agent/
├── modules/                # 后端（严格分层，tests/architecture 锁死依赖方向）
│   ├── game/               #   类型 + 操作目录 + 队列 schema + 三族 catalog（174 条）
│   ├── driver/             #   SC2 适配器（唯一接触 sc2）+ 假端口 + 录制器
│   ├── world/              #   RawGameState → GameState
│   ├── flow/               #   策略引擎：manifest / predicates / allocator / engine / vocab
│   ├── constraint/         #   行为约束 + 执行语义（单点权威）
│   ├── planner/            #   生产投影（逐秒仿真 + simulate v2）
│   ├── tactical_map/       #   地图模型：区域 / 槽位 / 点位 / 空间查询
│   ├── production/         #   生产运行时（队列执行账本）+ 经济维持器
│   ├── view/               #   ViewFrame 契约实现 + 只读视图层
│   └── api/                #   FastAPI：REST + WebSocket + 会话 + 命令 + 提案
├── agent/                  # 对话式顾问 Agent（AgentTalk / 工具 / 提示词种子）
├── eval/                   # 评测管线（四契约 + 场景注册 + 归档索引）
├── vendor/agentic/         # 内嵌文件契约式 Agent 框架（vendored，第一方代码）
├── tools/                  # 会话子进程 / 夹具生成 / 假世界 / API 启动器
├── web/                    # 前端（React + TypeScript + Vite）
├── tests/                  # 后端 pytest（含架构守卫与共享测试工厂）
└── docs/                   # 架构 / 计划 / ADR / 契约 / spec（见 docs/DOCS.md）
```

完整模块职责、依赖规则、契约细节与数据流见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.12 | 3.13/3.14 与 burnysc2 不兼容；依赖管理用 [`uv`](https://docs.astral.sh/uv/) |
| Node.js + pnpm | 前端构建 |
| 《星际争霸 II》 | 仅真机模式需要；离线 / 模拟模式无需安装游戏 |
| OpenAI 兼容 LLM | 仅 Agent 对话模式需要；测试使用 FakeLLM，不访问网络 |

## 快速开始

```bash
# 1. 安装后端依赖
uv sync

# 2. 配置 LLM（仅 Agent 模式需要）
cp .env.example .env    # 填入 OpenAI 兼容的 key 与 base_url

# 3. 安装前端依赖
cd web && pnpm install

# 4. 一键启动（Windows：同窗口启动 API + Web）
./start.bat             # API @ 127.0.0.1:8770，Web @ localhost:5273
```

或分开启动：

```bash
uv run python tools/serve_api.py        # 后端 API（--port 可更换端口）
cd web && pnpm dev --strictPort         # 前端（端口占用时报错，不静默漂移）
```

> ⚠️ 真机模式会 spawn SC2 子进程，子进程 `stdin` 必须为 `DEVNULL`（否则继承打开的管道会导致挂起）。会话异常退出时若残留黑屏 SC2 进程，需手动 `taskkill`。

## 运行模式

| 模式 | 命令 | 说明 |
|---|---|---|
| 离线复盘 | 前端数据源选 `jsonl` 夹具 | 无需游戏与 LLM；`pnpm gen:fixtures` 用真引擎生成夹具 |
| 模拟对局 | 建 session `driver=sim` | 确定性假世界（worldsim），可重复、可测试 |
| 真机对局 | 建 session `driver=sc2` | 需 SC2 客户端与 Ladder 地图；当前仅 Terran 经真机验证 |
| Agent 对话 | `POST /api/agent/chat`（SSE 流式） | 常驻顾问，对局跟随 + 插话；`python -m agent.run --dry` 可单回合试跑 |
| 评测 | `python -m eval.run --list` | 场景注册表浏览；`--scenarios <标签>` 跑指定场景 |

## 测试

```bash
uv run python -m pytest tests -q       # 后端（基线 1034 passed + 4 skipped）
cd web && pnpm test                    # 前端（基线 396 passed）
cd web && pnpm typecheck               # 前端类型检查
```

覆盖范围：`tests/architecture/` 锁分层依赖方向；`tests/factories.py` 提供共享测试工厂；各域测试覆盖引擎 / 投影 / 契约 / 会话新鲜度门 / 子进程协议 / 提案通道 / Agent 装配；前端测试集中在纯函数与契约形状。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构全景（模块 / 职责 / 依赖 / 契约 / 数据流 / 真机状态） |
| [docs/PLAN-NEXT.md](docs/PLAN-NEXT.md) | 后续任务单一队列 |
| [docs/DOCS.md](docs/DOCS.md) | 文档索引与地图 |
| [docs/adr/](docs/adr/) | 架构决策记录（ADR） |
| [docs/contract/](docs/contract/) | 契约与边界定义 |
| [docs/spec/](docs/spec/) | Flow v0.2 schema 规范 |

## 设计要点

| 决策 | 理由 |
|---|---|
| 组件 = 帧→像素的纯函数，帧源可替换 | 复盘免费、时间线免费、前端不重复实现规则 |
| 复盘优先，live 最后接入 | 无需开游戏、确定性、迭代快 |
| 派生量一律后端计算 | 坐标换算历史踩坑多，TypeScript 侧不保留第二份 |
| 中文文案一律来自后端 catalog | 前端不建第二套 i18n 字典 |
| UI 与 Agent 共用同一套命令 API | UI 操作可被 Agent 脚本化，Agent 动作对 UI 可见 |
| Agent 只能提案，不提供直接命令工具 | 队列修改有单一可审计入口 |

## 当前状态

后端核心链路（状态 / 驱动 / 策略引擎 / 约束 / 投影 / 生产运行时 / 经济维持器 / 视图 / REST+WS API）与前端六个数据页面已贯通；真机验证覆盖 Terran 战术链一截，Protoss / Zerg 未验证；多策略实例、崩溃恢复、mechanics 规则层尚未实现。完整功能缺口与可用性自评见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §11。

> 实验性语音交互栈（ASR + TTS + Live2D）为本地开发中功能，未并入主分支。

## 许可证

[Apache License 2.0](LICENSE)

## 免责声明

- 《星际争霸 II》是 Blizzard Entertainment 的产品。本项目与 Blizzard 无任何隶属或背书关系，仅用于个人学习与工程实验。
- 本项目通过 [`burnysc2`](https://github.com/burnysc2/python-sc2)（python-sc2 社区 fork）与游戏交互，遵守其许可。
- 使用本项目所需的 LLM 服务（及其费用、合规）由使用者自行承担。
