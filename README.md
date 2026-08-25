# SC2Agent

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Status](https://img.shields.io/badge/status-experimental-orange.svg)

> 把大语言模型（LLM）接进《星际争霸 II》决策回路的 Agent 框架：在线让 LLM 克服自身延迟去操控实时对局，离线把人和 Agent 放进同一套规划与复盘回路。

包名 `sc2-agent-next`，当前版本 `0.0.1`。这是一个**个人实验项目**，不是成品，也尚未稳定——拿来跑可以，别拿去打比赛。

---

## 这是什么

不是"写一个能赢的 SC2 bot"。真正想回答的是两个工程问题：

1. **慢 LLM 怎么进快实时游戏**——LLM 一次决策要几秒，SC2 一秒 22 步。这个延迟差怎么桥接，才能让 Agent 不至于一上场就废？做法是：世界版本快照、命令三态裁决（受理 / 拒绝 / 待裁决）、按轮次节奏下发。
2. **人和 Agent 怎么进同一决策回路**——离线规划、复盘、跨会话记忆怎么串起来，让人和 Agent 用同一套语言商量打法？做法是：共享文件工作面、仿真当共同验证器、录像当跨会话记忆。

这两条被一个设计决定连起来：**live、复盘、离线模拟走同一条产帧路径**。所以离线测试看到的数据，和对局实时看到的是同一份——不是两套各算各的。

> 注：延迟治理是地基，不是卖点。真正想验证的样本是"慢 LLM 介入快实时系统"和"离线规划/复盘回路"这两个可迁移的工程模式。

## 现状（诚实自评）

- 后端核心链路通了：状态/驱动/策略引擎/约束/投影/生产运行时/经济维持器/只读视图/REST+WS API。
- 前端数据面齐了（概览/地图/生产/Flow/规划/调试六页 + 提案流（自动应用）+ 复盘时间线 + 真机 live）。
- 真机验过 terran 的一截（移动命令、帧流、地形三栅格、stdout 协议纯净）；protoss/zerg 未真机验。
- **可用性落后于设想**：固定布局逼用户来回切页、地形坡道在渲染里几乎看不见、策略图无拖拽缩放。这些当时是有意后置的取舍，欠的是渲染/交互层，数据都在。
- 多策略实例、timer/局部变量、敌人聚类、崩溃恢复、mechanics 规则层尚未做。
- 完整自评见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §11。

## 核心设计

- **一套帧契约贯通一切**：live 推送 / 复盘录制 / 离线模拟 / Agent 观察包，全是同一种帧（`ViewFrame`）。回放和实时看到的不是两份数据。改契约就 bump `REV`，前后端两侧由 contract test 锁死。
- **严格分层**：`game`（地基，零依赖）→ `driver`（唯一碰 SC2）→ `world` / `flow` / `constraint` / `planner` / `tactical_map` / `production` → `view`（只读视图）→ `api`（传输）。`tests/architecture` 用一张禁止表机械锁死依赖方向，违反即测试失败。
- **Agent 对局域只能提案**：Agent **没有直接命令工具**，只能推提案（hunk：insert/delete/modify/reorder）——靠"不给那个工具"物理保证，不是约定。提案附 `based_on_seq` 新鲜度版本号、校验错误回流给 Agent 下一轮。人审环节 2026-08 起停用：校验通过的提案创建后**立即自动应用**（`decision.auto=True` 留审计），accept/reject 能力休眠保留。
- **离线规划 + 复盘回路**：仿真器（确定性 `worldsim`）是人和 Agent 共同的验证器；提案可双投影对比"采纳前/后"曲线；录像跨会话回放；Agent 工作区是磁盘物理隔离的共享文件面。
- **三态裁决**：每个命令结局必居其一——`ok`（驱动确认）/ `failed`（驱动拒绝 + 原因）/ `None`（已受理待裁决，真机异步世界里的正常态，不是 bug）。

## 架构总览

```
sc2Agent/
├── modules/                # 后端，严格分层（tests/architecture 锁死依赖方向）
│   ├── game/               #   地基：类型 + 操作目录 + 队列 schema + catalog
│   ├── driver/             #   SC2 适配器（唯一碰 sc2）+ 假端口 + 录制器
│   ├── world/              #   RawGameState → GameState
│   ├── flow/               #   策略引擎：manifest/predicates/allocator/engine
│   ├── constraint/         #   (GameState, action) → bool
│   ├── planner/            #   生产投影（逐秒模拟 + 供给守卫）
│   ├── tactical_map/       #   地图模型：区域/槽位/点位/空间查询
│   ├── production/         #   生产运行时 + 经济维持器
│   ├── view/               #   ViewFrame 契约实现 + 只读视图层
│   └── api/                #   FastAPI：REST + WS + 会话 + 命令 + 提案
├── agent/                  # SC2 顾问 agent（HTTP 客户端 + 工具 + spec + 单回合 runner）
├── vendor/agentic/         # 内嵌的文件契约式 agent 框架（vendored，第一方代码）
├── tools/                  # 会话子进程 / 夹具生成 / 假世界 / API 启动器
├── web/                     # 前端（React + TS + Vite）
├── tests/                  # 后端 pytest
└── docs/                   # 架构 / 计划 / ADR / 契约 / spec
```

完整模块职责、依赖方向、契约、数据流、真机记录见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、`uv`、`burnysc2`（python-sc2 fork）、FastAPI、websockets |
| 前端 | React + TypeScript + Vite、pnpm、zustand、uPlot、vitest |
| Agent | 内嵌 `vendor/agentic`（文件契约式引擎）、OpenAI 兼容 LLM 客户端 |
| 真机 | 《星际争霸 II》客户端 + burnysc2 |
| 可选 | 语音交互栈（ASR + TTS + Live2D，实验性，本地开发中，未并入主分支） |

## 环境要求

- **Python 3.12**（3.13/3.14 与 burnysc2 不兼容）。用 `uv` 管依赖。
- **Node.js + pnpm**（前端）。
- **《星际争霸 II》**：仅真机模式需要。离线 / sim 模式不开游戏也能跑全链路。
- **NVIDIA GPU + CUDA**：仅实验性语音栈需要，不用语音可跳过。

## 快速开始

```bash
# 1. 后端依赖
uv sync

# 2. 配置 LLM（仅真机 / Agent 模式需要；测试用 FakeLLM 不打网络）
cp .env.example .env   # 然后填入你自己的 OpenAI 兼容 key 与 base_url

# 3. 前端依赖
cd web && pnpm install

# 4. 一键启动（Windows：一个窗口同起 API + Web）
./start.bat            # API @ 127.0.0.1:8770，Web @ localhost:5273
```

或分开起：

```bash
uv run python tools/serve_api.py        # 后端 API（默认 127.0.0.1:8770，--port 可换）
cd web && pnpm dev --strictPort         # 前端（--strictPort：端口占用会报错，不静默漂移）
```

数据源选 `jsonl` 夹具可纯离线复盘，不用开游戏、不用 LLM。真机模式建 session 时传 `driver=sc2`。

> ⚠️ 真机模式会 spawn SC2 子进程。子进程 `stdin` 必须是 `DEVNULL`（否则 burnysc2 继承一根打开的管道会挂起——真机教训）。退出时若残留黑屏 SC2 进程，需手动 `taskkill`。

## 测试

```bash
uv run python -m pytest tests -q       # 后端
cd web && pnpm test                    # 前端
cd web && pnpm typecheck               # 前端类型检查
```

测试落点：`tests/architecture/` 锁分层依赖方向；`tests/flow`/`tests/planner`/`tests/view`/`tests/api` 覆盖引擎/投影/契约/会话新鲜度门/子进程协议；前端测试全落在纯函数上（组件是帧→像素，不测 DOM）。

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 完整架构快照（模块/职责/依赖/契约/数据流/真机状态/自评），交接级。
- [`docs/adr/`](docs/adr/) —— 架构决策记录（坐标语义、区域模型、经济维持器、队列执行账户等）。
- [`docs/spec/`](docs/spec/) —— 契约与 schema 规范。
- [`docs/DOCS.md`](docs/DOCS.md) —— 文档索引与地图。

## 设计取舍（摘选）

| 取舍 | 一句话理由 |
|---|---|
| 组件 = 帧→像素的纯函数，帧源可换 | 复盘免费、时间线免费、前端算不出规则 |
| 复盘优先，live 最后接 | 不开游戏、确定性、迭代快 |
| 派生量一律后端算 | 坐标换算历史上反复踩坑，TS 里不留第二份 |
| 中文文案一律来自后端 catalog | 前端不建第二套 i18n 字典 |
| UI 与 Agent 共用同一套命令 API | UI 操作 Agent 可脚本化，Agent 动作 UI 看得见 |
| Agent 只能提案，不给直接命令工具 | 改动必经提案校验这道闸，单一入口可审计（红线 P1 / 机制 Q1） |

## 许可证

[Apache License 2.0](LICENSE)。

## 免责声明

- 《星际争霸 II》是 Blizzard Entertainment 的产品。本项目与 Blizzard 无任何隶属或背书关系，仅作为个人学习与工程实验。
- 本项目通过 [`burnysc2`](https://github.com/burnysc2/python-sc2)（python-sc2 的社区 fork）与游戏交互，遵守其许可。
- 使用本项目所需的 LLM 服务（及其费用、合规）由使用者自行承担。
- 当前为实验性早期版本，API、契约、数据格式均可能在不预先通知的情况下变动。
