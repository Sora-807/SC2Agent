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
god file 拆分）已收官。**工程批 N1-N5 全部完结（2026-08-25）**，队列只剩 N6
（UI 后置项，排期随用户痛感）。回填 REFACTOR B6·§3·§4·§5 / ISSUES #11，
执行史 WORKLOG §0.66-0.70。

## ② 概念与原则

- **单一队列**：新任务只进本文件；发现新债先记 ISSUES/REFACTOR，要动工再排进来。
- **测试先行**：改行为先补类级测试（parametrize + 变体），修完不加个例测试——
  2026-08-25 审计的核心教训（泛化不对称）。
- **形状契约**：动 `AgentTalk` 构造/`say`/`prompt_snapshot`、trace 落盘格式、`create_app`
  签名、API 形状（proposals/plans/simulate/session/start）、`seed_memory_workspace`、
  `api.app.DEFAULT_MAP_PLANS_DIR`、vendor FakeLLM 系——tests/eval 装配测是裁判；
  动其它 modules 内部实现自由。
- **基线不降**：后端 1092+4skip / 前端 396 + typecheck（2026-08-25 N5 收官口径），每批收尾全绿才算完。
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

> N4（前端测试形态治理）已完结：2026-08-25 D3 拍板**分类处置而非全迁**——摸底后
> 12 个 readFileSync 文件分四类：fixtures 加载×5（正当）、跨语言契约对账×1
> （contract 精确解析后端 REV/TOPICS，正当）、约定扫描×2（theme/layout 全仓禁令
> = custom-lint，无行为可迁）、组件接线/决策锁×3（chat-dock/map-canvas/charts——
> 锁的是用户拍板的 UI/交互决策，本仓无渲染基建且 UI 用户自测，迁渲染测试不改变
> 锁的性质）。执行：扫描全部收敛到 source-scan.ts 单点（政策头注明三类正当用途；
> chat-dock/charts 的弱版内联 code() 正则删并、map-canvas 的 cwd 相对路径修掉、
> theme 的本地文件遍历器删并）；**it.each 立杆**（theme FONT_PX 三载体对齐）；
> 一条靠行尾注释才成立的断言改锁代码事实（sayChat 回退路径）。验收口径按 D3
> 修订：`grep readFileSync web/tests` = fixtures + contract 契约读 + source-scan
> 单点（396 绿 + typecheck 净）。执行史 WORKLOG §0.69。

> N5（质量杂项）已完结（2026-08-25，WORKLOG §0.70）：flaky 修为轮询真前置
> （static/terrain+static/map，30s 预算，finally 停会话防漏子进程）；llm_stream
> 迁直调单测 8 条（双字段容错 parametrize/零分片重试/预算三态，API 文件删 3 条
> 装配版）；覆盖缺口核实（agent 区 import 级全覆盖；view 裸模块仅 fmt/jsonl
> ——补 12 条直测，map_plans/initial_states/loadouts 有 api 集成测罩）；stub
> 三件收尾（REFACTOR §5 处置留档）。队列只剩 N6。

### N6 · 可用性后置项（UI，用户自验）

拖拽 dock（U9 债）、地形坡道渲染强化、策略图拖拽缩放、offline/live/replay 三轴分离。
数据全在帧里，不涉及 REV。排期随用户实际痛感调整，不抢 N1-N5 工程优先级。

---

## [待定] 决策点汇总

（已决全部：D4 三族化深度 = hybrid——catalog 推导结构、显式钩子管 Zerg 语义，2026-08-25；
D2 FrameSource 落点 = api 内独立模块 `api/frame_source.py`——纯函数 + 薄壳不给基类，
2026-08-25；D1 runtime 回吐边界 = Mixin 继续——flights 扩建造域 + ledger 新 Mixin，
协作对象明确不做（与 G2 拍板一致），2026-08-25；D3 前端测试 = 分类处置而非全迁——
约定扫描保留为 custom-lint（source-scan 单点）、决策锁如实标注、只迁真注释依赖，
2026-08-25。）
