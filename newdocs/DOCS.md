# DOCS — 文档地图（2026-08-23 收归）

> 一页说清「文档放哪、去哪找」。本轮把 ~55 份 superseded 文档清掉（git 历史可追），
> 保留原则：**有代码/契约活性引用的留 · 决策理由留 ADR · 现状写 newdocs**。

## newdocs/ —— 现行文档（先看这里）

| 文档 | 内容 |
|---|---|
| `ARCHITECTURE.md` | 现行架构全景（模块分层与方向） |
| `PLAN.md` | 现行计划（F/P 批次） |
| `WORKLOG.md` | 执行史（§0.x 每轮：做了什么/为什么/回归数字），最新在上 |
| `ISSUES.md` | 问题清单 + 开放任务清单（17 条，处理一条关一条） |
| `AGENT-LOOP.md` | Agent 数据触达闭环蓝图（I17-I20 母题；§6 有新产物闭环检查清单） |
| `REFACTOR.md` | modules/ 代码债审计与重构进度（P0 bug/G1-G3 已清，B6/B7/死代码待做） |
| `前端对话框设计指导参考.md` | 聊天 UI 设计语言（提炼自 deepseek-harness） |

## docs/ —— 契约与数据（代码有活性引用，不可随手删）

| 文件 | 谁在引用 |
|---|---|
| `plan-frontend.md` | **帧契约唯一真相源**（web/contract/index.ts 头注、schema 注释） |
| `plan-backend-view.md` / `test-plan.md` | schema/constraint 注释引用 |
| `需求文档-v0.1.md` | R5 等产品级规则出处（ISSUES I12 引证） |
| `P0-影响边界.md` / `模块审查.md` / `issues-flow-production.md` / `driver_spike.md` | 决策理由留档 |
| `game_data_dump.json` | 三族 catalog 的数据源（tools/generate_catalog.py 消费） |
| `tank_marine_push.yaml` | 策略 YAML 的参考范本（与 strategies/ 同形） |
| `adr/0029`（地图区域与目标解析）、`adr/0030`（经济维持器与工兵所有权） | 现行 ADR |
| `full_flow.log` / `bare_addon.log` / `slot_scan.log` | 代码注释里的**真机证据**（放置静默失败/挂件报告位/槽位扫描），勿删 |

## docs（旧）/ —— 精华留档（历史契约，已被 newdocs 取代"现状"半边）

- `spec/001-006 + README`：**Flow v0.2 schema 契约**（group/strategy/step/FlowIR/assembly/
  allocation 六份）—— YAML 策略写法的深层语义仍在这些文件里，newdocs 只描述现状。
- `adr/0006`（单位所有权与域仲裁 §7-9）、`0013`（实例状态与热改）、`0014`（编辑态转移矩阵，
  I16 热改的参考）、`0024`（flow 历史事件溯源）、`0027`（放置与坐标语义，多处代码引用
  "ADR-0027 锁定公式"）、`0028`（Flow v0.2 取代关系）。

其余历史文档（旧 ADR 0001-0012/0015-0023/0025-0026、旧 plan、重构共识总览、
superseded 计划与探针截图）已删——`git log --diff-filter=D -- docs/` 可追。
