# PLAN-EVAL-FRONTEND：评测工作台（项目钻取 + 运行指标钻取）

> 状态：**修订稿 v2（2026-08-26，代码面全核实后），仍会改**。承接
> `PLAN-AGENT-EVAL`（D1-D16 已决：四契约 / Grade 双形态 / 归档全保留 / 盲评），
> 本文只补**前端面**——把评测页从「只读概览表」升级成「可钻取工作台」。
> 不碰 eval 管线核心（runner / grader / 归档逻辑零改动，钻取是只读叠加层）。
>
> 触发：用户实测 `EvalPage` 后提的三件事——①想可视化配置评测项目都有哪些、
> 每个怎么创建；②点进具体项目看细节；③每次运行要有更丰富的指标可在界面查看。
> 用户已预判「可视化配置若难，先保证每个项目有细节描述（靠每个模块必须设置的
> 类数据来确保）」——本文据此分档。
>
> v2 相对初稿的变化：EF1-EF6/EF9/EF10 由代码核实定死（见 §0 与决策表）；
> 补四个初稿没预见的坑（run_dir 含 `+` / 中断批次孤儿 run / prompts 批末才写 /
> messages 体积）；A、B 两批的方案落到具体端点签名与测试口径。
>
> v3（2026-08-26 用户拍板）：**EF7 定死——不做可视化创建，前端只负责观看**；
> 创建面改为「给 codeagent 的 skill」（批 C 重写：add-eval-scenario），EF8
> 随之定死（不做 hot reload，skill 写明重启）。

## 0. 代码面核实结论（v2 新增，全部看过实物）

初稿的关键断言逐条对过代码/归档，结果：

| 初稿断言 | 核实结果 |
|---|---|
| 全仓 grader/runner/fixture 把构造参数存同名 `self` 字段 | **成立**。九个类全对上：`ToolSequenceGrader.must/forbid/order`（graders.py:22-26）、`ProposalGrader.expect_*`（:58-68）、`SimOutcomeGrader.final_has/final_units/horizon`（:122-127）、`RegexGrader.reply_*`（:163-170）、`EitherGrader.graders/axis`（:195-201）、`JudgeGrader.rubric/_llm_factory`（judge.py:33-35）、`SingleRoundRunner.max_turns/mount_readonly`（runner.py:86-89）、`FakeFollowRunner` 五参数（:112-119）、`OfflineSessionFixture.setup_fn/prepare`（fixture.py:43-46）。**推论：`vars(instance)` 单源就够，不需要 `inspect.signature`**（初稿的 signature+vars 双取没有增量信息——签名里的参数全都落成了同名属性）。 |
| `Project`/`Task` 是 dataclass | 成立（contracts.py:17-23/51-63）。 |
| 主动 grader 期望藏在 `grade()` 逻辑里，introspect 拿不到 | **半成立，初稿略过头**。`PlansGrader` 确实零构造参数（planning.py:45-65，连 `__init__` 都没有）；但 `StrategyGrader(strategy_id, must_steps)` 和 `MapPlanGrader(plan_id)` 的关键期望就在参数里，introspect 看得见。→ describe() override 只有 PlansGrader 是必须，另两个是锦上添花。 |
| `result.json` 含 meta/tool_calls/messages/final_text/reasoning/segments/proposals/changes/workspace/session | 成立（result.py:61-69）。`tool_calls` 每步自带 `duration_ms`+`result_preview`（:128-133）——工具时间线零加工。`changes` 就是 ChangeRecord 形状 `{area,action,ref,label,target}`——前端 ChatDock 已在渲染同形状（ChangeChip，ChatDock.tsx:268，私有函数，可提取复用）。`proposals` 带全字段（id/title_zh/rationale_zh/hunks/validation/status/decision）。 |
| 钻取路由走 hash query | **现状已支持**：`route.ts:42-54` 的 `Route.params` 早已带 URLSearchParams，App.tsx:101-102 有 `params.get("plan")` 下发先例。EF9 被现状解决，只剩消费纪律（见坑③）。 |

**四个初稿没预见的坑（已核实，方案里已消化）**：

1. **run_dir 含 `+`**：批次目录名是场景 id 用 `+` 拼的（如
   `20260825-130647-B5-named-slot-uses-exact+L1-gas-block/L1-gas-block/run1`）。
   `+` 在 **query string** 里会被解码成空格（`parse_qsl` 语义），在 **path** 里是
   字面量。→ run 钻取 API 走 **path-param**（`/api/eval/runs/{run_dir:path}`）；
   前端 hash 写入必须走 `URLSearchParams.set()+toString()`（`+` 会编成 `%2B`
   再解回来），**禁止字符串拼接 hash**。
2. **中断批次的孤儿 run**：`index.jsonl` 的行是**批末**才 append（run.py:107），
   `report.md`/`prompts/` 也是批末写（report.py:71-79）。跑批中途被杀 → run 目录
   已落盘但概览看不见、没有提示词快照。当前盘上就有一批（`runtime/eval/
   20260826-005439-…x13-e3b8`，6/13 个项目，无 report/prompts/index 行）。
   → B1 端点直读 `result.json`（不依赖 index）天然能服务这种 run：响应里带
   `index_row`（从 index.jsonl 匹配 `run_dir` 的那行，匹配不到 = null，前端标
   「未入账」）；prompt 端点对无快照的 run 返 404（真实情况，不是异常路径）。
3. **messages 体积**：最大归档（H0 假 live 200 轮）`result.json` 197KB，其中
   `messages` 81.7K 字符/134 条、`tool_calls` 30.7K/71 条，其余字段合计 <15K。
   → 端点默认**不返 messages**（只返 `messages_count`），`?messages=1` 才附
   400 字/条摘要（复用 `result.py:_clip_messages` 现成逻辑）。全量永远在盘上。
4. **架构守卫与源码锁**：新端点必须留在 `modules/api/routes/eval.py` ——
   EVAL_SHELL 白名单只认这个文件（tests/architecture/test_imports.py:161），
   且该文件 import eval 必须延迟到函数体（eval 包顶层 import api.app 成环，
   eval.py:7-9 已注明）。`eval/describe.py` 只用 stdlib（inspect/json），无环、
   且 modules/ 反向 import eval 会被守卫拦（本来也不该）。前端
   route.test.ts:41-49 有源码级锁（App 挂载 EvalPage + overview fetch）——
   动 App.tsx 接线要同步改锁。theme G6：禁裸 `text-xs/sm`（theme.test.ts:33），
   新组件只用 token（text-label/note/dim/faint/ghost、border-l1、bg-inset）。

## ① 我想干什么

当前 `EvalPage`（`web/src/pages/EvalPage.tsx`，222 行）是两张平铺只读表：场景
注册表（id/tags/graders 名/默认 N/判官）+ 运行记录表（ts/批次/结果/通过/失败
项/模型）。行不可点，看不到「这个项目到底在验什么」「这次 run 具体怎样」。

升级成可钻取工作台，三个目标：

- **项目细节钻取**：点场景表某行 → 看这个评测项目的完整构成——任务
  （text/note）、fixture（什么局面、预置了什么）、runner（哪种回路、参数）、
  每个 grader（哪个轴、期望什么、判定参数）。这正好是用户说的「每个模块必须
  要设置的类数据」——`Project` 是四契约的组合 dataclass，类数据 = 各契约的
  构造参数。**用 introspection 自动生成描述，零侵入**（见 ②）。
- **运行指标钻取**：点运行记录某行 → 看这次 run 的丰富指标。`RunResult` 归档了
  tool_calls 序列（每步耗时+结果预览）、in/out tokens、duration、每轴
  `reason_zh`、LLM 判官 score、proposals/changes 明细、workspace 写入、会话
  终态、回复全文+reasoning——全是现成素材，当前表只露了 passed/axes。
- **创建面走 skill**（用户拍板，EF7）：不做前端可视化创建/编辑——「怎么加
  评测项目」沉淀成给 codeagent 的 skill（批 C），以后加场景由 codeagent 按
  skill 操作。前端只负责观看；新场景注册即现在概览/详情页，天然成为 agent
  加完后的自检面。

**可行性判断**（核实后不变）：前端「创建新项目」难——`Project` 是 Python 类
组合，前端表单创建 = 代码生成（写 `eval/scenarios/*.py`），且 grader 参数异构
（`must/forbid` vs `expect_op/expect_type` vs `final_has/final_units`…），全表单
从零拼装工程量大、易错。但「自动生成每个项目的细节描述」零侵入可行。故分档：
**批 A+B（只读钻取，先做）→ 批 C（创建 skill，依赖 A）**。

## ② 概念

**1. 类数据 introspection（vars 单源）**：`Project`/`Task` 是 dataclass
（`fields()` 直读）；`Fixture`/`Runner`/`Grader` 是 Protocol，但全仓实现都是
普通类、构造参数全存同名 `self` 字段（§0 表逐个核实）——所以 **`vars(instance)`
就是全部构造参数**，不需要 signature。值渲染器处理非 JSON 类型：

- callable（fixture 的 `setup_fn`/`prepare`）→ `"<函数名> @ 相对路径:行"`
  （`inspect.getsourcefile/getsourcelines`，如 `<函数 _l1> @ eval/scenarios/
  live.py:41`）——够定位、不假装看得到闭包内部（EF2 定稿）；
- 嵌套 list 递归渲染（`EitherGrader.graders` → 子 grader 的 component 描述）；
- `_` 前缀属性照渲染（`JudgeGrader._llm_factory`：None → null =「同款判官」，
  工厂 → callable 标注）；
- grader 的 `name`/`axis` 是类属性，从类上取（不进 params）。

两种注册形态（`@scenario` 糖 + 显式 `register(Project(...))`）都产 `Project`
实例，introspection 对两者一致。

**2. `describe_project(p) -> dict`**：序列化 Project 全配置——`{id, tags, runs,
judge_model, task{text,note,max_turns}, fixture{class,module,params}, runner{同},
graders[{class,module,name,axis,params}]}`。**可选 `describe()` override**：
组件实现了 `describe() -> dict` 就 merge 进 params 之上（EF1/EF3 定稿）——给
期望藏在 `grade()` 逻辑里的主动 grader 用，不改基类签名。

**3. run 钻取数据源 = 已归档的 `result.json`/`grades.json` 直读**（EF5 定稿）：
`<run_dir>/result.json` = `RunResult.to_dict(full=True)` 减 prompt 全文，
`<run_dir>/grades.json` = `[Grade.to_dict()]`。**只读盘、不重跑、不重算**。
端点按 `run_dir`（相对 eval_root，来自 index 行或用户直贴）定位，合并 index 行
做上下文（孤儿 run 见 §0 坑②）。`prompts/<hash>.md` 在**批次目录**下（
`<run_dir>/../prompts/<hash>.md`），单独懒加载端点（EF6 定稿）。

**4. 前端钻取 = hash 路由子视图**：`route.ts` 的 query 参数已支持，overview
保留，加两个详情视图 `#/eval?project=<id>` 和 `#/eval?run=<run_dir>`。与
PlanningPage 的 `initOnce`（挂载时消费一次）不同，EvalPage 对 prop **响应式**
消费（useEffect 依赖 drill 参数）——因为 `#/eval` → `#/eval?project=x` 是同页
hash 变化，组件不重挂载。项目详情页底部挂「该项目的运行记录」小表（overview
.runs 过滤 project==id），点进单 run——形成「项目 → runs」导航闭环。

## ③ 逐条初步方案

### 批 A：项目细节钻取（只读，introspection）

| # | 动作 | 说明 |
|---|------|------|
| A1 | 新 `eval/describe.py` | `describe_project(p)` + `_component(obj)`（class/module/params/name/axis）。值渲染器见 ②-1；纯 stdlib（inspect/json），无环。不变式：对 REGISTRY 全量 `json.dumps(describe_project(p))` 必须成功（新测试的骨架）。 |
| A2 | `describe()` override ×3 | `PlansGrader`（必须——零参数，期望「refinery 在 factory 前」只能显式写）；`StrategyGrader`/`MapPlanGrader`（可选——参数已可见，补一句人读语义）。各 ~5 行，不动基类。 |
| A3 | API `GET /api/eval/projects/{id}` | 返 `describe_project`。404 带可用 id 清单（沿用 I39「列出来别让人猜」语义）；describe 延迟 import（函数体）。overview 的 scenarios 仍只给概览，不膨胀。 |
| A4 | 前端项目详情视图 | 场景表行可点 → `#/eval?project=<id>`（URLSearchParams 写 hash）。新 `web/src/panels/EvalProjectDetail.tsx`：头部（id+tags+runs+judge_model）/ 任务卡（text 原话+note 人读）/ fixture（类+参数，函数显示 `<函数名> @ 文件:行`）/ runner（类+参数）/ graders 表（name+axis+类+参数键值，rubric 全文折叠）。底部「该项目的运行记录」小表（ov.runs 过滤）可点进 run。 |
| A5 | 测试 | ①tests/eval 或 tests/api：describe 不变式（REGISTRY 全量序列化 + L1 抽查 must/forbid 在 params 里 + PlansGrader 走 override）；②API：200 形状 + 404 带清单；③web：route.test.ts 源码锁更新（App 给 EvalPage 传 params）。 |

### 批 B：单 run 指标钻取（只读，已归档）

| # | 动作 | 说明 |
|---|------|------|
| B1 | API `GET /api/eval/runs/{run_dir:path}` | **path-param**（run_dir 含 `+`，query 形态有 + →空格解码坑，§0 坑①）。读 `result.json`+`grades.json`，剥 messages（只返 `messages_count`），合并 index 行（匹配 `run_dir`，孤儿 run 置 null）。错误语义拆开（I39 口径）：路径越界（绝对路径/`..`/resolve 后不在 eval_root 内）→ 400；run_dir 不存在或无 result.json → 404。可选 `?messages=1` 附 400 字/条摘要（`_clip_messages`）。 |
| B2 | API `GET /api/eval/runs/{run_dir:path}/prompt` | 定位 `<run_dir>/../prompts/<meta.prompt_hash>.md`（批次目录下，hash 从 result.json meta 读），返 text/plain。批末才写（§0 坑②）→ 中断批次的 run 返 404（detail 说明「该批次未写提示词快照」）。同 B1 的越界/不存在语义。 |
| B3 | 前端 run 详情视图 | 运行记录行可点 → `#/eval?run=<run_dir>`。新 `web/src/panels/EvalRunDetail.tsx`，分区全部来自归档、无新算（分区明细见下）。 |
| B4 | ChangeChip 提取复用 | `ChatDock.tsx:268` 的私有 ChangeChip + `api/agent-chat.ts:38` 的 ChatChange 提到可共享位置（shell/ui 或独立文件），run 详情的「变更」区直接渲染归档里的 changes（同形状，§0 已核实）。ChatDock 改引用，行为零变化。 |
| B5 | 测试 | API：正常 200 形状（meta/grades/tool_calls 在、messages 不在但 count 在）/ `?messages=1` 摘要在 / 400 越界 / 404 不存在 / prompt 端点 200+404 / 孤儿 run（盘上有 result.json 无 index 行）→ index_row=null。复用 tests/api/test_eval.py 的 eval_root=tmp_path 注入接缝（fixture 里手造 run 目录）。 |

run 详情视图分区（`EvalRunDetail`，全部来自归档）：

- **概要条**：outcome / ✅passed（grades 汇总）/ duration_s / in+out tokens /
  llm_model / index_row 的 ts+label+project（null → 「未入账 run」标）/
  prompt_hash+seed_hash 双列（点 prompt_hash 懒加载 B2 全文展开）
- **判定明细**：每轴 ✅/❌/⭐score + `reason_zh`（JudgeGrader 的 LLM 分单独标）
- **工具序列**：每行 tool + duration_ms + args 摘要 + result_preview（折叠，
  归档自带，零加工）
- **提案与变更**：proposals（id/title_zh/rationale_zh/validation.ok + hunks
  展开）+ changes（ChangeChip，B4）
- **回复与推理**：final_text 全文 + reasoning 折叠（meta.reasoning_clipped>0
  时标注「N 段超 12k 护栏被截」）
- **工作区**：写入文件清单（相对路径→字节数）
- **会话终态**：session{state, game_time, alive}
- **对话摘要**（可选交互）：默认显示「N 条消息（全量在盘：<traces 路径>）」，
  点「加载摘要」带 `?messages=1` 重取展示 400 字/条版

### 批 C：评测项目创建走 skill（codeagent 操作面，前端零改）

用户拍板（2026-08-26）：不做可视化创建——**创建面 = 给 codeagent 的 skill**，
前端只负责观看。新场景加完注册即现（overview 与批 A 的详情页），A+B 的钻取
面同时就是 agent 的自检面。

| # | 动作 | 说明 |
|---|------|------|
| C1 | 写 skill `add-eval-scenario` | 落 `~/.zcode/skills/add-eval-scenario/SKILL.md`（用户级——本仓 `.zcode/` 只有 plans、项目级 skill 无先例，生效位是用户目录；description 写清触发词「加评测场景 / 加评测项目 / add eval scenario」+ 限定 sc2Agent 仓）。**skill 是入口不是规范**（与 agent 文档同规矩）：工作流步骤自包含，事实面（四契约字段 / grader 参数 / 标签闭集）指回仓内单源——`eval/contracts.py`、`eval/scenario.py` 模块 docstring、`eval/graders.py`、`eval/registry.py:TAGS`——不复制参数表，防双源漂移。skill 在仓外不进版本控制；靠单源原则可再生，暂不在 docs 留快照。 |
| C2 | skill 工作流六步 | ①选域文件（`eval/scenarios/{live,boundary,planning,heavy}.py`）或新建；②按 `@scenario` 糖写场景——`setup_fn` 用 TestClient 造局面、`prepare` 写预置文件（约定式挂载 tmp/plans 等）；③grader 从 `eval/graders.py` 现货选（`EitherGrader` 组合多正确路径、`JudgeGrader` 盲评 rubric），现货不够→场景文件里自带主动 grader（只读 world、`name`/`axis` 类属性、**实现 `describe()`**——批 A 约定，否则详情页看不到期望）；④守卫：id 唯一、tags 闭集（加新标签 = 显式改 `registry.TAGS`）、modules/ 不得 import eval（架构守卫）、grader 只读世界（D2）；⑤验证：先 pytest 装配测（FakeLLMClient 不起真 LLM），再单场景真跑 `uv run python -m eval.run <id> --runs 1`（.env）；⑥收尾：重启 serve_api 前端才见（registry 随进程 import，EF8 不做热切）。 |
| C3 | skill 与 A 的闭环 | 最后一步让 agent curl `GET /api/eval/projects/<id>`（批 A 端点）确认描述完整：params 全序列化、自带 grader 的 `describe()` 生效——「加完即验」，不用等下次跑批。 |

C 依赖 A（describe() 约定 + 详情端点先在），A 落地后顺手一提交。

## [待定] 决策点汇总

v2 把 EF1-EF6/EF9/EF10 由代码核实定死；v3 用户拍板 EF7/EF8。**全部已决。**

| # | 决策 | 结论 | 备注 |
|---|------|------|------|
| EF1 | grader 描述来源 | **已决：`vars()` 单源 + 可选 `describe()` override** | 九个类构造参数全存同名 self（§0 核实），signature 无增量；初稿的「签名+vars」简化掉 |
| EF2 | 闭包 setup_fn 展示 | **已决：函数名+文件:行** | `inspect.getsourcefile/getsourcelines`；场景 setup_fn 全是模块级函数，定位够用 |
| EF3 | 主动 grader 的「期望」 | **已决：describe() override；只有 PlansGrader 必须** | 核实修正：Strategy/MapPlan 的期望在构造参数里，introspect 可见；初稿「三个都藏逻辑里」过头了 |
| EF4 | traces/messages 全量前端可读 | **已决：默认不返（count），`?messages=1` 返 400 字/条摘要；全量在盘给路径** | 197KB 极端归档里 messages 占 81.7K/134 条（§0 坑③） |
| EF5 | run 详情数据源 | **已决：result.json/grades.json 直读** | 不重跑不重算；还能服务中断批次的孤儿 run |
| EF6 | prompts 全文展示 | **已决：懒加载端点 `GET /api/eval/runs/{run_dir:path}/prompt` 返 text/plain** | eval_root 在 runtime/ 非 web 直访目录；中断批次无快照 → 404 兜底 |
| EF7 | 可视化创建程度 | **已决（用户拍板 2026-08-26）：不做可视化创建，前端只观看；创建面 = skill（批 C）** | 克隆改参/全表单都不做 |
| EF8 | 新场景 hot reload | **已决：不做**，skill 写明「加完重启 serve_api」 | registry 随进程 import；与「改提示词要重启 serve_api」同规矩 |
| EF9 | 钻取路由形态 | **已决（现状解决）：hash query，`?project=`/`?run=`** | route.ts 已带 params、App 已有 `params.get("plan")` 先例；纪律：hash 必须走 URLSearchParams 构造（`+`→`%2B`），禁字符串拼接 |
| EF10 | 批次依赖 | **已决：A、B 后端互不依赖，可分两提交；C（skill）依赖 A 的 describe() 约定，A 后一提交** | A=describe/registry 面，B=归档读面；B3 的「项目→runs」闭环在 A4 已埋（详情页小表两边都能接） |

## 执行与验收口径

- **提交拆分**：批 A 一个提交、批 B 一个提交、批 C（skill）一个提交（EF10）；
  每个提交后端 pytest + 前端 vitest + typecheck 全绿。
- **批 C 验收**：skill 落盘后，下次真要加评测场景时说「加一个评测场景验 X」
  应能触发并按六步走通；skill 内不含会漂移的参数表（指回仓内单源，C1）。
- **落地提醒**：新端点要重启 serve_api 才生效（改的是运行中进程的代码面）；
  本计划不动提示词，无 seeds 双份同步问题。
- **手工验收**：用户自测 UI（惯例）——场景行点进看四契约构成、run 行点进看
  分区指标、prompt_hash 点开看全文、未入账 run 有标注、`+` 批次名直链可开。

## 不做（边界）

- 不改 eval 管线核心（runner / grader / 归档逻辑零改动——钻取是只读叠加层）。
- 不做前端的可视化创建/编辑（EF7 已决）——创建走 skill + codeagent，前端只读
  观看。
- 不做新场景 hot reload（EF8 已决）——skill 写明「加完重启 serve_api」。
- 不做实时跑批的可视化配置（跑批按钮已够；「配置」指项目定义，非跑批参数）。
- 不把 messages/reasoning 全量内联进 API 默认响应（摘要+盘上路径，EF4）。
- 不做孤儿 run 的自动发现/清理（只保证点进去能看 + 标注「未入账」；那是跑批
  断点续跑的题，不在本计划）。
