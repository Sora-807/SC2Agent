# PLAN-NEXT —— 后续任务单一队列

> 状态：2026-08-25 立项（用户拍板「不要再让各种文档到处乱飞」）。
> **分工三账**：本文件 = 执行队列（做什么、什么顺序）；`docs/ISSUES.md` = 问题台账
>（为什么改、真机证据，引用用 issue 号）；`docs/REFACTOR.md` = 代码债明细（file:line 级清单）。
> 三账互相引用不互相复制：ISSUES 里立的项要动工了就在本文件排批；本文件做完了就在
> ISSUES/REFACTOR 对应条目回填，不在本文件留执行史（执行史归 WORKLOG）。
> 专线例外：**eval** 归 `docs/PLAN-AGENT-EVAL.md`（用户主导，活跃中）；**语音栈** 是本地
> WIP 未入库，自带蓝图，不入本队列。

---

## ① 我想干什么

把 2026-08-25 全面审计后确认的全部后续工程任务收进**一份**按优先级排好的执行队列，
让「下一步做什么」只有一个答案来源。审计清理轮（护栏/测试基建/泛化/REFINERY/归档/
god file 拆分）已收官，这里装的是**剩下的**：
前端测试形态、质量杂项、可用性后置项。
（三族化 N1 / 去重与死代码 N2 / 生产运行时瘦身 N3 均已完结：2026-08-25，回填
REFACTOR B6·§3·§4 / ISSUES #11，执行史 WORKLOG §0.66-0.68。）

## ② 概念与原则

- **单一队列**：新任务只进本文件；发现新债先记 ISSUES/REFACTOR，要动工再排进来。
- **测试先行**：改行为先补类级测试（parametrize + 变体），修完不加个例测试——
  2026-08-25 审计的核心教训（泛化不对称）。
- **形状契约**：动 `AgentTalk` 构造/`say`/`prompt_snapshot`、trace 落盘格式、`create_app`
  签名、API 形状（proposals/plans/simulate/session/start）、`seed_memory_workspace`、
  `api.app.DEFAULT_MAP_PLANS_DIR`、vendor FakeLLM 系——tests/eval 装配测是裁判；
  动其它 modules 内部实现自由。
- **基线不降**：后端 1075+4skip / 前端 396 + typecheck（2026-08-25 N3 收官口径），每批收尾全绿才算完。
- **提交纪律**：`git mv`/暂存后尽快 commit（2026-08-25 并行互卷教训）；push 走临时代理 7890。

## ③ 任务批次（初步方案，会改）

> N1（planner 三族化）已完结：2026-08-25 D4 拍板 hybrid（结构推导走 catalog、
> Zerg 供给/吞工语义显式钩子），回填 REFACTOR B6 / ISSUES #11，执行史 WORKLOG §0.66。
> N2（去重与死代码）已完结：2026-08-25 D2 拍板 api 内独立模块（api/frame_source.py，
> 纯函数+薄壳不给基类），REFACTOR §3/§4 全表处置留档，执行史 WORKLOG §0.67。

> N3（生产运行时瘦身）已完结：2026-08-25 D1 拍板 Mixin 继续（协作对象明确不做，
> 与 G2 同一结论）——runtime 852→482（flights 592 扩建造域 + ledger.py 250 新建）、
> live 675→538（live_io.py 抽录制/进程树/种族推断）、routes/session 374→93 +
> plans 353→99（session_start.py / plans_simulate.py 各自成路由文件）。
> 执行史 WORKLOG §0.68。

### N4 · 前端测试形态治理

**问题**：chat-dock/map-canvas/charts/layout 等测试是**源码扫描式断言**（readFileSync +
toContain 子串）——锁死 CSS 类名/JSX 文本，重构被强加「子串逐字保留」约束（MapCanvas
拆分实锤）；前端 `it.each` 全仓零处。
**方案**：源码扫描测试迁为行为测试（纯函数提取 + vitest 直测）；新测试默认 `it.each`。
**[待定] D3**：改造范围——只迁源码扫描型（约 4-5 文件）vs 全部 30 文件梳理。
**验收**：`grep readFileSync web/tests` 只剩 fixtures 加载类正当用途。

### N5 · 质量杂项

- **flaky 立项**：`tests/api/test_map_plans::test_sim_session_assembles_from_map_plan_and_emits_terrain`
  全量跑偶发红、单跑绿（2026-08-25 两次复现）——定位时序依赖，修或隔离；
- llm_stream 重试/预算逻辑测试从 tests/api 装配形式迁直调单测（模块已独立可测）；
- 覆盖缺口补齐（2026-08-25 审计）：view（5686 源/2747 测）、agent（3384/1635）两区
  最危险，planner 缺口随 N1 补；
- stub 收尾（REFACTOR §5）：mechanics 空模块挂 issue 引用、`engine.on_session_event`
  bare pass 加注或 wire、`driver.stop()` no-op 改显式命名。

### N6 · 可用性后置项（UI，用户自验）

拖拽 dock（U9 债）、地形坡道渲染强化、策略图拖拽缩放、offline/live/replay 三轴分离。
数据全在帧里，不涉及 REV。排期随用户实际痛感调整，不抢 N1-N5 工程优先级。

---

## [待定] 决策点汇总

| # | 决策 | 关联批次 |
|---|---|---|
| D3 | 前端测试改造范围：仅源码扫描型 vs 全量梳理 | N4 |

（已决：D4 三族化深度 = hybrid——catalog 推导结构、显式钩子管 Zerg 语义，2026-08-25；
D2 FrameSource 落点 = api 内独立模块 `api/frame_source.py`——纯函数 + 薄壳不给基类，
2026-08-25；D1 runtime 回吐边界 = Mixin 继续——flights 扩建造域 + ledger 新 Mixin，
协作对象明确不做（与 G2 拍板一致），2026-08-25。）
