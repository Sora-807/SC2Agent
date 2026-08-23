# DOCS — 文档地图（2026-08-23 三目录合一）

> 此前 `newdocs/`（现行）、`docs/`（契约与数据）、`docs（旧）/`（历史精华）三足鼎立，
> 本轮合并为**一个 `docs/`**，分类收纳；被取代/无意义的已删（历史可追
> `git log --diff-filter=D -- docs/`）。代码里的路径引用已全部同步。

## 根层 —— 现行文档（每天看这些）

| 文档 | 内容 |
|---|---|
| `ARCHITECTURE.md` | 现行架构全景（模块分层与方向） |
| `PLAN.md` | 现行计划（F/P 批次） |
| `WORKLOG.md` | 执行史（§0.x 每轮：做了什么/为什么/回归数字），最新在上 |
| `ISSUES.md` | 问题清单 + 开放任务清单（17 条，处理一条关一条） |
| `AGENT-LOOP.md` | Agent 数据触达闭环蓝图（I17-I20 母题；§6 新产物闭环检查清单） |
| `REFACTOR.md` | modules/ 代码债审计与重构进度（P0 bug/G1-G3 已清；剩 B6/B7/死代码/去重） |

## contract/ —— 契约与边界（代码有活性引用，不可随手改）

| 文件 | 是什么 |
|---|---|
| `plan-frontend.md` | **帧契约唯一真相源**（web/contract/index.ts 与 view/schema 的 §2） |
| `plan-backend-view.md` | 后端视图层计划（frame/flow.groups 等出处） |
| `test-plan.md` | constraint 门控项 / world resource_nodes 的定义处 |
| `需求文档-v0.1.md` | 产品级规则出处（R5 等；ISSUES I12 引证） |
| `P0-影响边界.md` | D1 状态两面（raw/处理后）/ D2 操作权威源 —— game 层五个模块的边界定义 |

## adr/ —— 决策记录（背景/决定/边界/反例/验收）

现行：`0029`（地图区域与目标解析）、`0030`（经济维持器与工兵所有权）、
`0031`（策略模板的编译期展开 —— imports/_lib.yaml，与 ADR-0028 的边界）。
历史精华：`0006`（单位所有权与域仲裁 §7-9）、`0013`（实例状态与热改，I16 参考）、
`0014`（编辑态转移矩阵）、`0024`（flow 历史事件溯源）、`0027`（放置与坐标语义——
代码多处"ADR-0027 锁定公式"指向它）、`0028`（Flow v0.2 取代关系）。

## spec/ —— Flow v0.2 schema 契约（YAML 策略的深层语义）

`001-group` / `002-strategy` / `003-step 与 atom 目录` / `004-FlowIR 与 ExitRecord` /
`005-assembly` / `006-allocation` + README。写策略 YAML 遇到"为什么这样写"先翻这里。

## reference/ —— 参考与设计语言

`前端对话框设计指导参考.md`（聊天 UI 设计语言，ChatDock 遵循）、
`driver_spike.md`（driver 选型 spike：alliance 二义等独家理由）。

## data/ —— 数据与范本（工具消费，路径勿动）

`game_data_dump.json`（三族 catalog 数据源，generate_catalog.py 消费）、
`tank_marine_push.yaml`（策略 YAML 范本，run_tank_marine_push 与测试消费）。

## evidence/ —— 真机证据（代码注释引用的踩坑实录，勿删）

`full_flow.log`（放置静默失败/挂件拼名）、`bare_addon.log`（挂件报告位锁定实验）、
`slot_scan.log`（can_place 槽位扫描校准）；探针脚本的新输出也落这里
（`state_trace.jsonl` 等，gitignored，缺失时相关测试自动 skip）。

## 已删除（去哪找）

旧 ADR 0001-0012/0015-0023/0025-0026、旧 plan、重构共识总览、模块审查.md
（被 REFACTOR.md 审计取代）、issues-flow-production.md（被 ISSUES.md 取代）、
superseded 计划与探针截图、41 个一次性扫描日志 —— 全部 `git log --diff-filter=D -- docs/` 可追。
