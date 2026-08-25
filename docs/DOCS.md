# DOCS — 文档地图（2026-08-23 三目录合一）

> 此前 `newdocs/`（现行）、`docs/`（契约与数据）、`docs（旧）/`（历史精华）三足鼎立，
> 本轮合并为**一个 `docs/`**，分类收纳；被取代/无意义的已删（历史可追
> `git log --diff-filter=D -- docs/`）。代码里的路径引用已全部同步。

## 根层 —— 现行文档（每天看这些）

| 文档 | 内容 |
|---|---|
| `ARCHITECTURE.md` | 现行架构全景（模块/职责/依赖/契约/数据流/真机状态）——2026-08-25 全面刷新（REV 18/账本语义/自动应用口径） |
| `PLAN-NEXT.md` | **后续任务单一队列**（N1-N6 批次 + D1-D4 待定决策；新任务只进这份） |
| `PLAN-AGENT-EVAL.md` | eval 专线（用户主导，活跃中；D1-D16 已裁决，MVP+四批已落地） |
| `WORKLOG.md` | 执行史（§0.x 每轮：做了什么/为什么/回归数字），最新在上 |
| `ISSUES.md` | 问题台账：开放 issue 详解 + 开放任务清单（三账分工见 PLAN-NEXT 头注） |
| `ISSUES-ARCHIVE.md` | 已处理留档 + 垃圾箱（被推翻/失效描述；2026-08-25 从 ISSUES.md 分出） |
| `AGENT-LOOP.md` | Agent 数据触达闭环蓝图（I17-I20 母题；§6 新产物闭环检查清单） |
| `REFACTOR.md` | 代码债明细账（file:line 级；B6/死代码/去重——执行排期在 PLAN-NEXT N2） |

> 三账分工：PLAN-NEXT=执行队列，ISSUES=问题台账（为什么），REFACTOR=债明细（在哪）。

## contract/ —— 契约与边界（代码有活性引用，不可随手改）

| 文件 | 是什么 |
|---|---|
| `plan-frontend.md` | **帧契约唯一真相源**（web/contract/index.ts 与 view/schema 的 §2） |
| `plan-backend-view.md` | 后端视图层设计史（frame/flow.groups 等出处；⚠️ 设计快照性质，现状以 ARCHITECTURE/ADR 为准——2026-08-25 已补队列账本修正注） |
| `test-plan.md` | constraint 门控项 / world resource_nodes 的定义处 |
| `需求文档-v0.1.md` | 产品级规则出处（R5 等；ISSUES I12 引证） |
| `P0-影响边界.md` | D1 状态两面（raw/处理后）/ D2 操作权威源 —— game 层五个模块的边界定义 |

## adr/ —— 决策记录（背景/决定/边界/反例/验收）

现行：`0029`（地图区域与目标解析）、`0030`（经济维持器与工兵所有权）、
`0031`（策略模板的编译期展开 —— imports/_lib.yaml，与 ADR-0028 的边界）、
`0032`（队列执行账本与 skip 语义——uid + 四值 status）、
`0033`（地图规划双分支与会话图层合并）、
`0034`（auto-supply 移除——诊断取代掩盖）。
历史精华：`0006`（单位所有权与域仲裁 §7-9）、`0013`（实例状态与热改，I16 参考）、
`0014`（编辑态转移矩阵）、`0024`（flow 历史事件溯源）、`0027`（放置与坐标语义——
代码多处"ADR-0027 锁定公式"指向它）、`0028`（Flow v0.2 取代关系）。

## spec/ —— Flow v0.2 schema 契约（YAML 策略的深层语义）

`001-group` / `002-strategy` / `003-step 与 atom 目录` / `004-FlowIR 与 ExitRecord` /
`005-assembly` / `006-allocation` + README。**状态：已实施**（早于 ADR-0031/0032/0033，
模板展开与队列账本未入——以后两者为准，见 README 头注）。写策略 YAML 遇到"为什么这样写"先翻这里。

## reference/ —— 参考与设计语言

`前端对话框设计指导参考.md`（聊天 UI 设计语言，ChatDock 遵循）、
`driver_spike.md`（driver 选型 spike：alliance 二义等独家理由）。

## data/ —— 数据与范本（工具消费，路径勿动）

`game_data_dump.json`（三族 catalog 数据源，generate_catalog.py 消费）、
`tank_marine_push.yaml`（策略 YAML 范本，run_tank_marine_push 与测试消费）。

## evidence/ —— 真机证据（本地留档，未入库）

`full_flow.log`（放置静默失败/挂件拼名）、`bare_addon.log`（挂件报告位锁定实验）、
`slot_scan.log`（can_place 槽位扫描校准）。**注意：`*.log` 与 `state_trace.jsonl` 均被
.gitignore 忽略**——fresh clone 没有它们；代码注释里的引用指向本机留档，本机勿删，
缺失时相关测试自动 skip。

## archive/ —— 已完成的计划原文（2026-08-25 归档）

`PLAN.md` / `PLAN-V2.md` / `PLAN-LIVE-ROUND2.md` / `PLAN-ROUND3.md` 四份（README.md
有逐份去向表；执行史在 WORKLOG、决策在 ADR，此处只留计划原文作根因证据）。
注意 `PLAN-ROUND3.md` 的 H 批已被 ADR-0034 推翻。

## 已删除（去哪找）

旧 ADR 0001-0012/0015-0023/0025-0026、旧 plan、重构共识总览、模块审查.md
（被 REFACTOR.md 审计取代）、issues-flow-production.md（被 ISSUES.md 取代）、
superseded 计划与探针截图、41 个一次性扫描日志 —— 全部 `git log --diff-filter=D -- docs/` 可追。
