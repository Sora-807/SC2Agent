# sc2Agent 完整架构文档（交接用）

> 版本：对应提交 58f7d79（2026-08-25 审计清理轮收官）；契约 **REV = 18**。
> 基线：后端 **1034 passed + 4 skipped**（另有 eval 装配测 ~20）/ 前端 **396 passed / 30 文件**
> + typecheck 绿。后续任务以此为准不许下降。
> 本文档是**当前仓库的完整快照描述**：模块、职责、依赖、契约、数据流、运行方式、真机状态。
> 配套文档：docs/ISSUES.md（问题清单）、docs/WORKLOG.md（执行史）、docs/PLAN-NEXT.md（后续任务单一队列）、
> docs/archive/（历史计划原文）。本文 2026-08-25 全面刷新过一次；再往后若局部过时，
> 以代码与 ADR 为准，别信旧快照。

---

## 0. 项目是什么

**sc2Agent**（sc2_agent_next）：星际争霸 II 的 Agent 框架。核心不是"打比赛"，而是：

1. **Agent 对局域只能提案**：agent 没有直接命令工具，改动走提案（hunk）——靠"不给那个工具"
   物理保证。**人审环节 2026-08-22 起停用**（用户拍板）：校验通过的提案创建后立即自动应用
   （`decision.auto=True` 留审计），accept/reject 端点休眠保留（恢复审批的锚点在
   `modules/api/routes/proposals.py` 头注）；
2. **严格分层**：flow 引擎不知道具体单位、driver 零业务规则、前端零规则复算；
3. **一套帧契约贯通一切**：live 推送、复盘录制、离线夹具、agent 观察包，全是**同一批字节**（ViewFrame）。

三条设计主线：
- **读面**：ViewFrame 契约（REV 18，14 个 topic）——前端组件是"帧 → 像素"的纯函数；
- **写面**：命令 API + 提案通道——UI 与 agent 用**同一入口**，没有后门；
- **会话**：三种驱动（offline / sim / sc2）同一套装配，真机走子进程隔离。

---

## 1. 仓库总览

~~~
sc2Agent/
├── modules/                # 后端（严格分层，tests/architecture 锁死依赖方向）
│   ├── game/               #   唯一地基：类型 + 操作目录 + 队列 schema + 边界契约 + catalog（三族 174 条）
│   ├── driver/             #   SC2 适配器（唯一碰 sc2 的地方）+ 假端口 + 录制器
│   ├── world/              #   RawGameState → GameState
│   ├── flow/               #   策略引擎：manifest/predicates/allocator/engine/vocab/templates
│   ├── constraint/         #   (GameState, action) → bool + semantics（执行语义单点权威）
│   ├── planner/            #   生产投影（逐秒仿真 + simulate v2 + initial_states）
│   ├── tactical_map/       #   地图模型：区域/槽位/点位/空间查询/地图规划
│   ├── mechanics/          #   （空占位，D11 后补 power/addon 规则层）
│   ├── production/         #   生产运行时（队列账本 ADR-0032）+ 经济维持器（ADR-0030）
│   ├── view/               #   ViewFrame 契约实现 + 只读视图层（observe/alerts/projection/...）
│   └── api/                #   FastAPI：app.py 装配壳 + routes/×13 + state.py
├── agent/                  # 对话式顾问 agent：talk（AgentTalk）/llm_stream/segments/client/
│   │                       #   tools/spec/readonly/workspace/memory_seed/seeds（提示词模板进版本库）
├── eval/                   # 评测管线（PLAN-AGENT-EVAL）：Fixture/Task/Runner/Grader 四契约 +
│   │                       #   场景注册 + RunResult/Grade 归一化 + 归档索引
├── vendor/agentic/         # 内嵌的文件契约式 agent 框架（vendored 第一方代码，不改）
├── tools/                  # 会话子进程 / 夹具生成 / 假世界 / API 启动器
├── web/                    # 前端（React+TS+Vite）
│   ├── src/contract/       #   契约的 zod 实现（类型由 schema 推导）
│   ├── src/source/         #   帧源：jsonl / mock-live / ws / reviewable
│   ├── src/store/          #   zustand 帧 store（组件唯一读帧入口）
│   ├── src/canvas/         #   地图分层渲染（MapCanvas paint = 23 行编排器 + 10 图层函数）
│   ├── src/charts/         #   uPlot 封装 + 投影图 + 双投影对比
│   ├── src/graph/          #   策略 AST 渲染 + 布局
│   ├── src/pages/          #   概览/地图/生产/Flow/规划/调试
│   ├── src/shell/          #   会话条/时间线/图标栏/对话栏（ChatDock）
│   ├── src/panels/         #   提案面板（审批门已休眠）
│   ├── src/planning/       #   生产规划草稿纯函数 + 地图规划编辑
│   └── src/api/            #   命令/提案/会话 HTTP 客户端
├── tests/                  # 后端 pytest（factories.py 共享工厂 + 各域；eval/ 装配测）
├── docs/                   # 六分类：根层现行 + contract/ + adr/ + spec/ + reference/ + data/
│   └── archive/            #   已完成计划原文
├── runtime/                # 运行时数据（gitignore：traces/recordings/plans/agent-talk/eval 归档）
└── .env                    # LLM 密钥（gitignore；本地测试用）
~~~

---

## 2. 分层与依赖方向（架构红线）

`tests/architecture/test_imports.py` 用一张 PROHIBITED 表机械锁死依赖方向，违反即测试失败
（2026-08-25 起覆盖扩展到顶层包，共 9 条测试）：

| 模块 | 可以依赖 | 禁止依赖 |
|---|---|---|
| **game** | （零依赖） | 一切上层 |
| **driver** | game + sc2 | 其余全部 |
| **world** | game | 其余 |
| **flow** | game, tactical_map | constraint/planner/world/driver/... |
| **constraint** | game, mechanics, tactical_map | planner/flow/... |
| **planner** | constraint, mechanics, game | flow/... |
| **tactical_map** | game | 其余 |
| **mechanics** | game | 其余（目前空模块） |
| **production** | game, constraint, tactical_map | **flow**（走 WorkerPoolPort 端口，ADR-0030 D3.4） |
| **view** | 以上全部（flow/production/planner/tactical_map） | driver, sc2 |
| **api** | view + 引擎 | driver, sc2；**agent 只许函数内 lazy import**（装配根构造，模块级即违规） |
| **agent**（顶层） | view/tactical_map/planner/constraint/game 读模型 + vendor agentic | api（命令走 HTTP 客户端）/driver/sc2/flow/production/world/mechanics |
| **eval**（顶层） | 任何项目代码（评测消费者，同 tests 地位） | 反向：modules/ 与 agent/ 不认识 eval |

专门测试：
- test_ports_module_removed：旧顶层 ports 模块已并入 game.ports；
- test_nobody_imports_view_or_api：没有下层模块 import view/api；
- test_view_schema_depends_only_on_game：view/schema.py 只依赖 game（契约独立于引擎重构）；
- test_agent_dependency_direction / test_nobody_imports_eval_or_voice / test_api_imports_agent_lazy_only
  （2026-08-25 新增三条，见上表 agent/eval 行）。

---

## 3. 模块职责详表

### 3.1 game（地基）

| 文件 | 内容 |
|---|---|
| geometry.py | Point2（左下原点浮点）、GridPos、Grid（data[y][x]） |
| raw.py | RawGameState/RawUnit/RawOrder：SC2 原生形态（driver 产出） |
| state.py | GameState/Unit/Order/Owner：处理后形态（world 产出，flow/constraint/planner 消费） |
| operation.py | Operation(op_id, unit_tags, action, params, seq) + OP_CATALOG（原子，**只增不改**）+ ParamType |
| production.py | QueueItem（**uid + 四值 status 账本**，ADR-0032）/Queue/QueueOp/WorkerTask/PlacementExact/PlacementInRegion |
| ports.py | GamePort/RuntimeSink/WorkerPoolPort/ApplyResult/OpApply/GameEvent/GAME_EVENTS（只增不改） |
| catalog.py | Catalog/CatalogEntry（stable_id↔burnysc2 名、zh 名、cost、build_time、前置、footprint）+ load_all()（**三族 174 条**）+ neutral_kind()（中性物关键词模式表，I25） |

**catalog 是 zh 文案与规则参数的单一真相源**：前端 i18n、alerts 文案、agent 观察包中文名、
气矿/工兵等角色判定全部从这里查（不写死单位名——REFINERY 三处硬编码已清）。

### 3.2 driver（唯一碰 SC2）

| 文件 | 内容 |
|---|---|
| sc2_adapter.py | extract_raw_state / extract_map_info（地形三栅格）、SC2DriverBot（on_step 抽帧→sink，drain op 队列→translate_op→do）、SC2GamePort（start() 阻塞在 run_game；stop() 仍是 no-op，靠 LiveSession 杀子进程兜底——已知债） |
| fake.py | FakeGamePort：脚本化 RawGameState 序列，离线测试用 |
| recorder.py | StateRecorder：RawGameState 的 1Hz JSONL（V1 取证用；ViewFrame 录制在 view.recorder） |

**三态裁决**：SC2DriverBot._apply_op 逐 op 回填 OpApply{ok, reason}（None = 已受理待裁决）；
失败发 op_apply_failed GameEvent。events(cursor) 是真切片游标。

### 3.3 world

adapt(raw) → GameState：alliance→Owner、health→hp、中性资源过滤（矿脉/气井从 units 拆出）、
`_EXTRA_NEUTRAL_TYPES` 逃逸舱（FORCEFIELD 这类模式判不了的中性物）。

### 3.4 flow（策略引擎）

| 文件 | 内容 |
|---|---|
| manifest.py | StrategyManifest/FlowAssembly/parse/validate（编译期校验拆成 _validate_* 小函数）；definitions 别名、locals、timer 谓词均已落地（二十六轮 T8） |
| predicates.py | PREDICATE_SIGNATURES（命名参数签名表）、eval_when、谓词目录 |
| allocator.py | Allocator（FCFS + sticky lease）、S3 滞回 + **成长期/伤亡期区分**（I24：到过 target 后才用 min 滞回，此前一路吸到 target）、snapshot() |
| engine.py | FlowEngine：每帧求值 active step 分支（首条命中）、do 产 Operation 经 port、去重、exit 按边路由、有界环兜底、观测记录 |
| vocab.py | dump_vocabulary()：机器可读词表——校验器/LLM 提示词卡片/前端 static/schema 三方共用的权威表 |
| templates.py | ADR-0031 编译期模板展开（imports/_lib.yaml） |

**authoring 语法**（YAML）：when 是结构化 AST、谓词命名参数、definitions 别名（{ref}）、
unit 一律 stable_id。V1 单策略实例。

### 3.5 constraint

check_build/check_train/check_gas/... → ConstraintResult（结构化 reasons）；
**semantics.py 是执行语义单点权威**（classify 判 pending/skip；planner/健康检查共享——
production 与 planner 都从这里 import，别再造第二份）。

### 3.6 planner（生产投影）

- build_order.py：Op 模型（Build/Train/AssignWorkers/Research）、MODULE_REGISTRY、expand()（透传裸 Op）；
- planner.py：Planner.project 逐秒模拟、队首可行性门控。**无供给守卫**（ADR-0034 彻底删除
  auto_supply——诊断取代掩盖：卡人口由投影事件如实报，不再自动插 depot）；
- sim_state.py：derive_from(gs, catalog) —— live 帧派生仿真起始态（气矿建筑走 catalog 三族）；
- slots_model.py / initial_state.py / opening.py / curve.py：槽位近似、initial-states 文档校验、
  开局种子、投影曲线与事件；
- ⚠️ **B6（开放债）**：投影器仍只认 Terran（refinery/supplydepot 写死 + supply_provided 单族），
  三族 catalog 数据就绪但消费不了。

### 3.7 tactical_map

- placement.py：BuildSlot；**ADR-0027 换算全仓库唯一一份**（build_point/reported_position/tl_from_*）；
- region.py：RegionLayer（标签网格 + 索引，ADR-0029）；地图规划双分支与会话图层合并（ADR-0033）；
- base.py / resolver.py / spatial.py：出生点模板、目标解析、空间查询；
- map_plan.py：地图规划文档模型（marks/slots/reserved）。

### 3.8 production（生产运行时 + 经济维持器）

- runtime.py（852 行，全仓最大——**已知回胖债**）：ProductionRuntime —— 命名队列 + 队首
  constraint 门控（阻塞原因+起始时间进 blocked，超 STALL_WARN_SECS 升级告警）+ **队列执行
  账本（ADR-0032）**：每项 uid + 四值 status（pending/in_progress/completed/skipped），
  skip-and-continue（执行期失败不冻结整队）、完成项留队供回放/审计；工具 op
  （submit/append/.../reorder）；snapshot() 读模型；
- flights.py（Mixin）：在途建造确认状态机——uid 匹配 + 实体对账（I26：晚到同型实体按
  emitted_pos 收编；无完工实体不假完成）、超时换位重试、挂件、气矿；
- placement.py（纯函数）：槽位类别/尺寸过滤 + 在途预留 → 放置点；
- economy.py：EconomyKeeper（ADR-0030）——常驻采矿维持器、目标值语义、reserve_idle、
  矿绝对目标地板化（I28：目标满但矿脉未饱和继续补满）；
- worker.py：WorkerAllocator（矿 2 气 3 饱和；气矿建筑走 catalog 三族）。

### 3.9 view（只读视图层 —— 契约实现）

| 文件 | 内容 |
|---|---|
| schema.py | **契约数据模型**：REV = 18、TOPICS（14 个）、全部 *View。只 import game |
| encode.py | dataclass→JSON、grid_to_b64、envelope() |
| statics.py | catalog/map/schema/strategy/terrain 五个静态面（后端算好派生量再发） |
| adapt.py | 动态面：world/flow/production/ops/economy/session/projection 的帧装配（派生量全在这） |
| producer.py | FrameProducer：**唯一产帧路径**（live/录制/夹具共用）；节拍对齐 game_time |
| projection.py | queue_to_ops()：QueueItem→planner Op；**终态/零余量项不进仿真**（I32：completed/skipped/count=0 过滤，在途由 derive_from 建模） |
| recorder.py | ViewRecorder：ViewFrame→JSONL |
| clusters.py | 敌方聚类算法单点 |
| alerts.py | AlertService：警报唯一来源；`_producer_alive` 当前帧对账（I27）；关键词中性物排除（I25 同源） |
| proposals.py | ProposalStore：提案存储/校验/应用/双投影（**自动应用**在 create 内） |
| observe.py | ObservationPacket：agent 读面（段落中文 + facts 机器可读 + 提案历史段 + based_on_seq 闭环） |

### 3.10 api（传输层）

| 文件 | 内容 |
|---|---|
| app.py | FastAPI 装配壳（G1 拆分后 ~124 行）：create_app() 组装 state + 挂路由；agent 运行时在函数内 lazy 构造 |
| routes/×13 | meta/sources/session/commands/agent/proposals/plans/map_plans/strategies/recordings/loadouts/initial_states/frames_ws（WS：多 topic 流 + seek/play/pause） |
| state.py | 共享辅助 |
| sources.py | SourceRegistry + JsonlSource（帧源三份复制之一——去重债见 PLAN-NEXT） |
| session.py | OfflineSession：进程内假世界 + 真引擎；MAX_STALE_SEQ 新鲜度门（409） |
| live.py | LiveSession（690 行，偏重——已知债）：子进程会话同形接口；sc2 用 stdin=DEVNULL；project 走子进程往返 |

### 3.11 agent / eval / vendor / tools

- **agent/**：对话式顾问。
  - talk.py：AgentTalk（常驻对话 + 专属事件循环线程 + 对局跟随 + 插话队列 + 看门狗 + 轮预算）；
  - llm_stream.py：ReasoningTolerantClient（思考流双字段名容错 + 零分片重试 + 轮 token 预算）；
  - segments.py：trace 轮内簿记（交错时间线 segments / steps，纯函数）；
  - tools.py：observe/write_surface/**propose**（无直接命令工具）+ planning tools + InterjectionQueue；
  - client.py（urllib HTTP）/spec.py（系统提示词）/readonly.py（只读区挂载）/workspace.py/
    bootstrap/memory_seed（工作区种子，只补缺失）/seeds/（提示词模板进版本库，D16 种子指纹）；
- **eval/**：评测管线（PLAN-AGENT-EVAL）——四契约 Fixture/Task/Runner/Grader、@scenario 注册、
  RunResult/Grade 归一化、确定性 grader 四件 + LLM judge、markdown 报告 + 归档索引
  （runtime/eval/，append-only index）、CLI `python -m eval.run`。**形状契约**：eval 依赖
  AgentTalk 构造/say/prompt_snapshot、trace 落盘格式、create_app 签名、若干 API 形状、
  seed_memory_workspace、vendor FakeLLM——重构动这些面时 tests/eval 装配测会红；
- **vendor/agentic/**：文件契约式 agent 框架（第一方 vendored）。DiskWorkspace 物理隔离（R5）、
  FakeLLMClient（测试不打网络）、trace 可视化；
- **tools/**：run_session.py（会话子进程：stdin 收命令/stdout 出帧/控制行与帧分开/loguru 重定向
  stderr）、worldsim.py（确定性假世界）、make_fixtures.py、serve_api.py、语音栈工具（本地 WIP 未入库）。

---

## 4. ViewFrame 契约（核心中的核心）

### 4.1 信封

~~~json
{ "topic": "frame/world", "rev": 18, "seq": 512, "game_time": 87.0, "wall_ms": 412, "payload": { ... } }
~~~

- topic / rev / seq / game_time / wall_ms 五键每帧必有；payload 随 topic 变。
- **seq 就是 GameState.seq（世界版本号）**。world 帧 seq 严格等于世界版本；派生帧可各自迭代。
- wall_ms 是生成时刻（毫秒），仅诊断。

### 4.2 14 个 topic

> topic 名是带前缀的字面量，订阅必须逐字一致（`modules/view/schema.py::TOPICS` ↔
> `web/src/contract/index.ts`，两侧 contract test 锁死）。动态面前缀 `frame/`，唯一例外
> `proposals`。

| topic | 静态/动态 | payload 要点 |
|---|---|---|
| `static/map` | 静态 | 区域标签网格+索引 + 槽位（br/build_point/reported_position 后端算好） |
| `static/catalog` | 静态 | 全量单位目录（三族 174 条：stable_id、zh 名/短名、cost、前置、footprint） |
| `static/schema` | 静态 | 逐字 dump_vocabulary()（含 name_zh，rev 12） |
| `static/strategy` | 静态 | steps/branches 值树/edges/声明 + 可读名/reasons 中文 + imported（模板出身，rev 15） |
| `static/terrain` | 静态 | height/pathable/placeable 三栅格；事件式（map 先到、terrain 补到） |
| `frame/session` | 动态 | 会话状态机、地图、种族、error |
| `frame/world` | 动态 | economy + units（stable_id + footprint + 在训 + 挂件）+ enemy_clusters + resource_state |
| `frame/flow` | 动态 | strategies[]（活跃 step/branch_hit/transitions）+ groups[]（composition/refill_state） |
| `frame/production` | 动态 | queues[].items[]（**uid + 四值 status + reason**，ADR-0032）/ in_flight[] / dropped[] 审计 |
| `frame/economy` | 动态 | tasks[]/nodes[]（含 base_tag）/reserved/domain_workers |
| `frame/ops` | 动态 | Operation 环形缓冲（origin + 三态 apply 回填） |
| `frame/projection` | 动态 | 未来曲线 + 事件 + skipped；终态项不进仿真（I32） |
| `frame/alerts` | 动态 | 结构化警报，zh 文案后端拼 |
| `proposals` | 动态 | 提案与状态（**校验通过即自动应用**；decision.auto=True 留审计） |

**不是 topic 的读面**：`observation`（agent 观察包）只有 REST，不在 TOPICS 里——它是帧的投影。

### 4.3 架构不变量（A1-A8）

> 号段说明：契约红线的唯一权威号段是 `docs/contract/plan-frontend.md` §2.4 的 **C1-C8**。
> 本节 8 条是传输与产帧层的架构不变量，编号 **A1-A8**。引用写全号。

| # | 架构不变量 |
|---|---|
| **A1** | REV 两侧一致：view/schema.py ↔ web/src/contract/index.ts；不匹配前端提示版本不符不渲染 |
| **A2** | 信封五键不可缺 |
| **A3** | 派生量全部后端算（供给饱和/在训/挂件/坐标/队列状态/投影/zh 文案），前端零复算 |
| **A4** | 单一真相源：zh 在 catalog；规则参数在 catalog/vocab；坐标换算只在 tactical_map.placement |
| **A5** | 帧是快照：一帧内自洽，任何游标/时序可独立渲染 |
| **A6** | 静态面一次性拉全，前端不拼不增量 |
| **A7** | 动态面按 rate 推；帧间顺序 = 服务端发送顺序 |
| **A8** | 录制/夹具/live 同一产帧路径（FrameProducer）——回放与实况字节级同构；GAME_EVENTS/OP_CATALOG/契约只增不改 |

### 4.4 REV 历史（1 → 18）

> 权威来源是 `modules/view/schema.py` 头部行内注释（每条写了为什么）。消费方视角在
> `docs/contract/plan-frontend.md` §2。下表照 schema.py 转录（近期版本详列，早期从简）。

| REV | 变更 | 原因摘要 |
|---|---|---|
| 18 | **队列执行账本**（ADR-0032）：items[] 增 uid + reason；status 扩为四值闭集 pending/in_progress/completed/skipped（已执行项**留队**）；in_flight/blocked 带 uid | 完成项永久留队后，投影/重放/审计都需要稳定锚点；skip-and-continue 取代队首冻结 |
| 17 | 新增 `frame/production.training[]`（在训条目：started_at 记账） | SC2 订单不带进度（rev 13 收窄 None），复盘截断线左侧"训练中部分条"由它 + build_time 算出 |
| 16 | in_flight[] 增 from_index | observe 能答"队列执行到第几项"（B3） |
| 15 | ADR-0031 模板化：static/strategy 增 imported；reasons 改默认表 ∪ 覆盖 | 模板出身可见；zh 单源在后端 |
| 14 | build_slots[] 增 alias_zh | 槽位中文别名（标记归 name、展示归别名） |
| 13 | world.units[].producing[].progress 收窄为可空 | SC2 订单不带进度，恒 0.0 是把未知伪装成刚开始 |
| 12 | 策略可读性：schema 各项值内 name_zh；static/strategy 增 display_name_zh/reasons/group_names | 裸标识符读不出意图（用户原话） |
| 11 | 提案 hunks.kind 增 map_plan 六种操作；preview.map_overlay 增 changed_marks | 地图规划提案（F14） |
| 10 | economy.nodes[] 增 base_tag；catalog 增 short_name_zh | F11 地图视觉语言 |
| 1-9 | 初版 → schema 逐字镜像 vocab → 区域标签网格 → flow 校准（eval_diagnostics/队列 blocked/in_flight 形状）→ forbidden 开放分组 → 新增 frame/economy → 新增 static/strategy → projection 增 skipped → 新增 static/terrain（事件式静态面） | 详见 schema.py 注释 |

历史帧兼容：**只增不改**——旧帧永远能按新契约解析（缺字段有默认值）；新字段老前端看不到是预期。

---

## 5. 会话模型与子进程协议

### 5.1 会话装配（三种驱动同一台机器）

~~~
GameState(子进程内) ── world.adapt ── flow.engine ── constraint ── production ── economy
                                        │
                                   FrameProducer ── ViewFrame(JSONL/WS) ── 前端
                                        │
                                   ProposalStore ── ObservationPacket ── agent
~~~

驱动只有两处不同：**帧从哪来**（offline=FakeGamePort / sim=worldsim / sc2=SC2DriverBot）、
**命令去哪**（fake=进程内队列 / sc2=真机）。engine/planner/production/economy/FrameProducer/
ProposalStore 完全同一份代码。

### 5.2 子进程协议（tools/run_session.py）

- 子进程 stdout = **一行一 JSON**：数据行是 ViewFrame 信封；控制行 `{"_": "meta|ack|error|projection|terrain|bye"}`；
- stdin：sim 收 JSON 命令；**sc2 模式 stdin 必须 DEVNULL**（SC2 继承打开管道会挂起——真机教训）；
- stderr：loguru 全量重定向（burnysc2 会污染 stdout，不重定向帧协议被日志淹没）；
- 命令在**帧边界**生效（新鲜度门 R8 的基础）；崩溃 = 结构化 error 行。

### 5.3 LiveSession（modules/api/live.py）

与 OfflineSession 完全同形：queue_items/apply_queue/project/terrain；project 走子进程往返
（GameState 在子进程里）；terrain 控制行到达转真 static/terrain 帧广播。

### 5.4 三态 apply

每个 Operation 的结局必居其一：**ok**（驱动确认）/ **failed**（明确拒绝+原因，发 GameEvent）/
**None**（已受理待裁决——异步世界正常态，不是 bug）。

---

## 6. 提案 + agent 环路

### 6.1 提案通道（ProposalStore，2026-08-22 起自动应用）

- agent 的 propose 工具产 hunk 数组（insert/delete/modify/reorder + map_plan 六种，rev 11）；
- apply_hunks 纯函数（按序应用，先模拟后落盘）；日志 append-only（runtime/proposals.jsonl）；
- 预览 = 干跑 apply 后的形态 + 双投影（采纳后曲线与当前曲线同图对比）；
- **自动应用**：校验通过的提案创建后立即生效（`decision.auto=True` 留审计）；accept/reject
  端点休眠保留（恢复人审 = 删 routes/proposals.py 头注标记的自动应用段）；
- 过期失效：based_on_seq，序列走远即 stale。

### 6.2 机制不变量（Q1-Q7，现行口径）

| # | 机制不变量 | 现状 |
|---|---|---|
| **Q1** | agent 没有直接命令工具（observe/write_surface/propose/sleep/规划工具），物理上不能绕过提案 | ✅ 仍立（这是边界本身） |
| **Q2** | 提案必须附 based_on_seq（新鲜度闭环） | ✅ |
| **Q3** | 提案必须具体到队列与 hunk | ✅（I31/I33 整改后 placement 写法有专节教学） |
| **Q4** | 一次一个可审单元 | ✅ |
| **Q5** | 校验错误回流给 agent（下一轮 observe 带历史段） | ✅（自动应用后"拒绝"= 校验失败，回流机制同源） |
| **Q6** | 应用前必经校验 + 双投影 | ✅ |
| **Q7** | 提案日志 append-only 全量可审 | ✅ |

> 历史注：`docs/contract/plan-frontend.md` §6 的 P1-P7（审批 UI 红线）描述的是人审时代的
> 门控形态；审批 UI 现休眠（前端三件套无 mount），P 号段仍是恢复人审时的需求基线。

### 6.3 agent 读写面

- observe 工具：段落中文摘要 + facts 机器可读 + 提案历史段（校验错误回流）+ based_on_seq 闭环；
- write_surface 只能写 agent 工作区（DiskWorkspace 物理隔离，R5）；
- 提案自动应用 → 生产运行时应用 hunk → 下一帧 production/ops 可见（可验证闭环）；
- agent spec/提示词模板在 `agent/seeds/`（进版本库；eval 种子指纹 D16 与真机同面）。

---

## 7. 前端架构（web/）

### 7.1 分层

| 目录 | 职责 |
|---|---|
| src/contract/index.ts | 契约 zod 实现（schema 唯一真相源，类型 z.infer 推导）；REV = 18 与后端对齐 |
| src/source/ | FrameSource 抽象：jsonl/mock-live/ws/reviewable。**只有这里碰"帧从哪来"** |
| src/store/frames.ts | zustand 帧 store：组件唯一读帧入口 |
| src/canvas/ | 地图分层渲染：MapCanvas（paint = 23 行编排器 + 10 个图层函数，2026-08-25 拆分）/layers/grid/view/theme/cluster |
| src/charts/ | uPlot 封装 + ProjectionChart + 双投影对比 |
| src/graph/ | 策略 AST 渲染 + 布局 |
| src/pages/ | 六页：Overview/Map/Production/Flow/Planning/Debug |
| src/shell/ | SessionBar/Timeline/IconRail/ChatDock（对话栏：流式 token/思考内联/插话排队条） |
| src/panels/ | 提案面板（审批门休眠） |
| src/planning/ | 规划草稿纯函数 + 地图规划编辑（拖拽/吸附/校验） |
| src/api/ | 命令/提案/会话 HTTP 客户端 |

### 7.2 关键决定（U 系列，全录见 archive/PLAN.md；存目）

U1 组件=帧→像素纯函数；U2 复盘优先 live 最后接；U4 派生量一律后端算；U6 zh 文案来自后端；
U7 UI 与 agent 共用命令 API；U9 V1 固定布局（可用性欠账已部分改善：短名/图层/规划模式实线框）。

### 7.3 前端测试（396 passed / 30 文件 + typecheck）

contract 测试锁 REV 与 payload 形状；planning/canvas/charts/store 纯函数测试。
已知形态债：chat-dock/map-canvas 等部分测试是**源码扫描式断言**（读源文件 toContain 子串），
重构被强加"子串逐字保留"约束——治理项见 PLAN-NEXT。

---

## 8. 端到端数据流（四条主线）

### 8.1 读面：世界 → 帧 → 像素

~~~
SC2 on_step ──extract_raw_state──> RawGameState ──world.adapt──> GameState
GameState ──FrameProducer──> ViewFrame(JSONL/WS) ──FrameSource──> zustand store ──> 页面纯函数 ──> 像素
~~~

复盘 = 读 JSONL 走同一 FrameSource 接口；同一条产帧路径（A8）保证三种玩法字节一模一样。

### 8.2 写面：命令（人）

~~~
前端/agent ──HTTP──> api/commands ──based_on_seq 校验（>5 过期 409）──> 会话 ──> 驱动 ──> OpApply 三态 ──> ops 帧回显
~~~

### 8.3 写面：提案（agent，自动应用）

~~~
agent.tools.propose ──hunks──> ProposalStore ──validate──> 通过即应用（auto）+ 双投影预览 ──> 下一帧可见
校验失败 ──> 错误回流 agent 下一轮 observe
~~~

### 8.4 会话生命周期

~~~
POST /api/session {driver, map} ──> LiveSession spawn run_session.py ──> stdout 帧流 ──> WS 广播
控制行(meta/ack/error/projection/terrain/bye) ──> 会话状态机 ──> session 帧
~~~

---

## 9. 运行手册

### 9.1 测试与类型

- 后端：`uv run python -m pytest tests -q`（基线 1034 + 4 skip）；
- 前端：`cd web && pnpm test && pnpm typecheck`（基线 396 / 30 文件）；
- 评测：`uv run python -m eval.run --list`（场景注册表）/ `--scenarios <标签>`（真 LLM 冒烟）。

### 9.2 离线复盘（不开 SC2）

1. `pnpm gen:fixtures`（真引擎产夹具到 web/public/fixtures/）；
2. `cd web && pnpm dev`；数据源选 jsonl 夹具。

### 9.3 本地 API + 模拟世界

- `uv run python tools/serve_api.py`（127.0.0.1:8770，--port 可换）；
- driver=sim 走 worldsim（确定性可重复）；提案走真实 ProposalStore + 双投影，无 LLM 也能测全链路；
- ⚠️ REV 不匹配先查 stale serve_api 进程再谈改代码（历史教训）。

### 9.4 真机（SC2）

- 前置：SC2 客户端 + burnysc2、--map 用 Ladder 图名；
- 建 session driver=sc2；sc2 子进程 stdin=DEVNULL（见 §5.2）；日志在 stderr；
- 退出 kill session；**残留黑屏 SC2 进程需手动 taskkill**。

### 9.5 agent 对话

- `python -m agent.run --dry`（FakeLLM 不打网络）；真实跑需 .env 配 LLM；
- 常驻对话走 API 的 /api/agent/chat（AgentTalk，SSE 流式 + 轮询 running_steps）；
- trace 在 runtime/agent-talk/traces/<run_id>/（messages.jsonl 全量 + trace.html）；
  录像在 runtime/recordings/。

---

## 10. 真机验证记录

### 10.1 早期基线（commit d42aa1e）

| 项 | 结果 |
|---|---|
| driver move 命令 | 通过 |
| LiveSession(sc2) 帧流 | 通过（一行一 JSON） |
| static/terrain 三栅格 | 通过（176×160，height 非零格 17760） |
| stdout 协议纯净 | 通过（loguru 重定向后零噪声） |

真机教训（已固化）：stdin=DEVNULL；loguru 重定向 stderr；黑屏残留进程手动清理。

### 10.2 后续真机轮次

2026-08-24/25 有多轮真机对局驱动的修复（步坦推、I24-I32 连环：滞回/中性物误报/flight
收编/维持器地板化/泳道图幻影条等），逐轮根因与证据在 docs/WORKLOG.md §0.4x-0.57 与
docs/ISSUES.md。种族覆盖：**只验过 Terran**，protoss/zerg 未真机验。

---

## 11. 已知缺口与诚实自评

### 11.1 功能缺口

| 缺口 | 现状 | 备注 |
|---|---|---|
| 多策略实例 | 编译期拒绝 >1 | 架构已预留（列表形状、lease） |
| 崩溃恢复 | 无 | 会话挂了要重建；录制器只做取证 |
| mechanics 规则层 | 空模块 | power/addon 留 D11 |
| 生产 authoring 统一 | planner 计划与 live 队列两套语法 | view.projection 桥做了翻译 |
| planner 三族化（B6） | 只认 Terran | 三族数据就绪；PLAN-NEXT 第一优先 |

（timer/step-local 变量、敌人聚类、可读名等早期缺口已落地——见 WORKLOG 对应批次。）

### 11.2 可用性自评

功能面：六页数据面 + 提案（自动应用）+ 双投影 + 复盘时间线 + 真机 live + 对话栏全通。
可用性欠账（U9 系，2026-08 批次已部分改善：短名/图层可见性/规划模式实线框/地图规划编辑）：
拖拽 dock 仍后置；地形坡道渲染仍弱；策略图无拖拽缩放。offline/live/replay 三轴仍压平在
一条"数据源"下拉里。

### 11.3 测试与代码债

集中台账在 **docs/REFACTOR.md**（B6/死代码/去重）与 **docs/PLAN-NEXT.md**（执行队列）——
此处不重复罗列，以那两份为准。

---

## 12. 测试布局（基线 1034+4skip / 396）

| 目录 | 覆盖 |
|---|---|
| tests/architecture/ | 分层依赖方向（含 agent/eval/voice 守卫与 api→agent lazy-only，共 9 条） |
| tests/factories.py | 共享测试工厂（make_unit/make_gs/FakePort）——新测试的标准造数方式 |
| tests/game/ | 队列 schema、operation 目录（只增不改）、catalog |
| tests/driver/ | fake 驱动、三态 apply、录制器、SC2 API 契约、trace 夹具 |
| tests/flow/ | manifest（拒绝族=44 条 parametrize 表）/predicates/allocator/engine/templates/variants |
| tests/planner/ | 投影曲线、simulate v2（含 I32 终态过滤直测）、sim_state、slots、initial_state |
| tests/constraint/ | 每类检查 + 结构化 reasons |
| tests/production/ | runtime（flight 收编/账本/挂件）、economy（ADR-0030 验收）、worker（三族气矿） |
| tests/tactical_map/ | 区域/槽位/空间/出生点 |
| tests/view/ | schema REV、encode、statics、adapt、producer、projection、alerts、observe、readmodels |
| tests/api/ | REST/WS/新鲜度门/子进程协议/提案/规划路由/simulate v2 |
| tests/agent/ | 工具集（无直接命令）、round/segments、memory seed |
| tests/world/ | adapter（含 neutral 12 模式全覆盖 + 完整性守卫） |
| tests/eval/ | 评测装配测（形状契约的裁判，~20 条） |
| web/tests/ | contract 锁 REV、canvas/charts/planning/store 纯函数（30 文件） |
