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
god file 拆分）已收官，这里装的是**剩下的**：三族化、去重与死代码、生产运行时瘦身、
前端测试形态、质量杂项、可用性后置项。

## ② 概念与原则

- **单一队列**：新任务只进本文件；发现新债先记 ISSUES/REFACTOR，要动工再排进来。
- **测试先行**：改行为先补类级测试（parametrize + 变体），修完不加个例测试——
  2026-08-25 审计的核心教训（泛化不对称）。
- **形状契约**：动 `AgentTalk` 构造/`say`/`prompt_snapshot`、trace 落盘格式、`create_app`
  签名、API 形状（proposals/plans/simulate/session/start）、`seed_memory_workspace`、
  `api.app.DEFAULT_MAP_PLANS_DIR`、vendor FakeLLM 系——tests/eval 装配测是裁判；
  动其它 modules 内部实现自由。
- **基线不降**：后端 1034+4skip / 前端 396 + typecheck，每批收尾全绿才算完。
- **提交纪律**：`git mv`/暂存后尽快 commit（2026-08-25 并行互卷教训）；push 走临时代理 7890。

## ③ 任务批次（初步方案，会改）

### N1 · planner 三族化（B6，第一优先）

**问题**：三族 catalog（174 条）就绪，投影器仍只认 Terran——`planner.py` 写死
`terran/refinery`/`terran/supplydepot`、`sim_state`/`economy` 的 `supply_provided` 单族、
`module_defs.py` 新蔓延的 terran/* 写死。Z/P 对局投影不可用（REFINERY 三处硬编码已于
2026-08-25 清掉，这是同族剩余）。
**方案**：气矿/人口建筑从 `catalog.where(capability=...)` 推导；`supply_provided` 收敛单源；
module_defs 参数化。**测试先行**：先补 protoss/zerg 的 planner 投影测试（现状零覆盖，
没有它重构无驱动——见 2026-08-25 审计覆盖缺口）。
**验收**：三族各一条完整开局投影测试绿；REFACTOR B6 条目关闭。
**[待定] D4**：race-agnostic 深度——完全 catalog 推导 vs 显式三族表（推导更干净，
但 supply 语义各族有差，可能要 hybrid）。

### N2 · 去重与死代码（REFACTOR §3/§4 清偿）

**内容**（明细见 REFACTOR.md，2026-08-25 核对后的仍开放项）：
- 帧源接口三份复制（sources/session/live 的 info/statics/latest_at/between）→ 抽
  `FrameSource` ABC——顺带是 live.py 瘦身的突破口；**[待定] D2**：落点 game.ports vs 独立模块；
- 死代码：`spatial.nearest` 死导出、`region.cells_of_big` 死方法、`producer._seq` 只写不读、
  manifest 空脚手架 dict 两只、`catalog.load_terran` 未跟 `_load_race` helper；
- 小去重：`_mmss` 双份、production 常量双份（MINERAL_SATURATION 等）、最近 base 平方距离
  五处、reserved-box 重叠检查两遍、map_plans 穿透 import 私有（`_footprint/_overlaps`）。
**验收**：REFACTOR §3/§4 表格清空或逐条标注保留理由；全仓绿。

### N3 · 生产运行时瘦身

**问题**：runtime.py 拆后回胖 564→852（ADR-0032 逻辑加回编排文件）；live.py 690 五职责；
routes/session(374)/plans(353) 越过拆分时 178 行天花板。
**方案**：runtime 的 `_sweep_completions`/`_producer_ever_ready`/`_mark_skip` 等回吐
flights/semantics；live.py 借 N2 的 FrameSource 抽离帧源职责；两个胖路由各抽辅助层。
**[待定] D1**：回吐边界——flights 继续当 Mixin（共享状态）vs 设计协作对象（更纯、手术更大）。
**验收**：runtime ≤650 行且行为零变化（账本/flight 测试原样绿）；live.py ≤550。

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
| D1 | runtime 回吐边界：Mixin 继续 vs 协作对象 | N3 |
| D2 | FrameSource ABC 落点：game.ports vs 独立模块 | N2 |
| D3 | 前端测试改造范围：仅源码扫描型 vs 全量梳理 | N4 |
| D4 | 三族化深度：catalog 全推导 vs 显式三族表/hybrid | N1 |
