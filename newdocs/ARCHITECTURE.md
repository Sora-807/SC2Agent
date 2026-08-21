# sc2Agent 完整架构文档（交接用）

> 版本：对应提交 d42aa1e（真机验证与修复）。基线：后端 **614 passed** / 前端 **96 passed / 9 files**，契约两侧 REV = 9。
> 本文档是**当前仓库的完整快照描述**：模块、职责、依赖、契约、数据流、运行方式、真机状态。
> 配套文档：newdocs/PLAN.md（后续计划，F10-F14 / B12-B14 及后端剩余事项）。

---

## 0. 项目是什么

**sc2Agent**（sc2_agent_next）：星际争霸 II 的 Agent 框架。核心不是"打比赛"，而是：

1. **人在环审批**：agent 推草稿提案，人审批后才生效（审批红线 **P1-P7** 见
   `docs/plan-frontend.md` §6；后端机制侧不变量 **Q1-Q7** 见本文 §6.2）；
2. **严格分层**：flow 引擎不知道具体单位、driver 零业务规则、前端零规则复算；
3. **一套帧契约贯通一切**：live 推送、复盘录制、离线夹具、agent 观察包，全是**同一批字节**（ViewFrame）。

三条设计主线：
- **读面**：ViewFrame 契约（REV 9，14 个 topic）——前端组件是"帧 → 像素"的纯函数；
- **写面**：命令 API + 提案通道——UI 与 agent 用**同一入口**，没有后门；
- **会话**：三种驱动（offline / sim / sc2）同一套装配，真机走子进程隔离。

---

## 1. 仓库总览

~~~
sc2Agent/
├── modules/                # 后端（严格分层，tests/architecture 锁死依赖方向）
│   ├── game/               #   唯一地基：类型 + 操作目录 + 队列 schema + 边界契约 + catalog
│   ├── driver/             #   SC2 适配器（唯一碰 sc2 的地方）+ 假端口 + 录制器
│   ├── world/              #   RawGameState → GameState
│   ├── flow/               #   策略引擎：manifest/predicates/allocator/engine/vocab
│   ├── constraint/         #   (GameState, action) → bool
│   ├── planner/            #   生产投影
│   ├── tactical_map/       #   地图模型：区域/槽位/点位/空间查询
│   ├── mechanics/          #   （空占位，D11 后补 power/addon 规则层）
│   ├── production/         #   生产运行时 + 经济维持器
│   ├── view/               #   ViewFrame：schema/encode/statics/adapt/producer/recorder/...
│   └── api/                #   FastAPI：REST + WS + 会话 + 命令 + 提案 + agent 读写面
├── agent/                  # SC2 顾问 agent（HTTP 客户端 + 工具 + spec + 单回合 runner）
├── vendor/agentic/         # 用户提供的 BaseAgent 框架（vendored，不改）
├── tools/                  # 会话子进程 / 夹具生成 / 假世界 / API 启动器
├── web/                    # 前端（React+TS+Vite）
│   ├── src/contract/       #   契约的 zod 实现（类型由 schema 推导）
│   ├── src/source/         #   帧源：jsonl / mock-live / ws / reviewable
│   ├── src/store/          #   zustand 帧 store（组件唯一读帧入口）
│   ├── src/canvas/         #   地图分层渲染 / 栅格 / 视口
│   ├── src/charts/         #   uPlot 封装 + 投影图 + 双投影对比
│   ├── src/graph/          #   策略 AST 渲染 + 布局
│   ├── src/pages/          #   概览/地图/生产/Flow/规划/调试
│   ├── src/shell/          #   会话条/时间线/图标栏/对话栏
│   ├── src/panels/         #   提案审批面板
│   ├── src/planning/       #   生产规划草稿纯函数
│   └── src/api/            #   命令/提案/会话 HTTP 客户端
├── tests/                  # 后端 pytest（architecture/driver/flow/game/view/api/agent/...）
├── docs/                   # 需求文档 + 计划 + ADR（当前）
├── docs（旧）/             # 旧 ADR（被 v0.1 部分取代）
├── newdocs/                # 交接文档（本文件 + PLAN.md）
├── runtime/                # 运行时数据（提案日志、agent 工作区；gitignore）
├── traces/                 # agentic 的 trace 输出（gitignore）
└── .env                    # LLM 密钥（gitignore；本地测试用）
~~~

---

## 2. 分层与依赖方向（架构红线）

tests/architecture/test_imports.py 用一张 PROHIBITED 表机械锁死依赖方向，违反即测试失败：

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
| **api** | view + 引擎 | driver, sc2 |
| **agent** | api（通过 HTTP） | （进程外） |

三条专门测试：
- test_ports_module_removed：旧顶层 ports 模块已并入 game.ports；
- test_nobody_imports_view_or_api：没有下层模块 import view/api（视图层不反向污染引擎）；
- test_view_schema_depends_only_on_game：view/schema.py 只依赖 game —— 契约数据模型
  独立于任何引擎重构。

---

## 3. 模块职责详表

### 3.1 game（地基）

| 文件 | 内容 |
|---|---|
| geometry.py | Point2（左下原点浮点）、GridPos、Grid（data[y][x]） |
| raw.py | RawGameState/RawUnit/RawOrder：SC2 原生形态（driver 产出） |
| state.py | GameState/Unit/Order/Owner：处理后形态（world 产出，flow/constraint/planner 消费） |
| operation.py | Operation(op_id, unit_tags, action, params, seq) + OP_CATALOG（20 个原子，**只增不改**）+ ParamType |
| production.py | QueueItem/Queue/QueueOp/WorkerTask/PlacementExact/PlacementInRegion |
| ports.py | GamePort/RuntimeSink/WorkerPoolPort/ApplyResult/OpApply/GameEvent/GAME_EVENTS（D7 目录，8 种，只增不改） |
| catalog.py | Catalog/CatalogEntry（stable_id↔burnysc2 名、zh 名、cost、build_time、前置、footprint size）+ load_terran() |

**catalog 是 zh 文案与规则参数的单一真相源**：前端 i18n 字典、alerts 文案、agent 观察包里的中文名，
全部从这里来（红线 U6/C4）。

### 3.2 driver（唯一碰 SC2）

| 文件 | 内容 |
|---|---|
| sc2_adapter.py | extract_raw_state / extract_map_info（B4 地形：height/pathable/placeable 三栅格）、SC2DriverBot（on_step 抽帧→sink，drain op 队列→translate_op→do）、SC2GamePort（start() 阻塞在 run_game） |
| fake.py | FakeGamePort：脚本化 RawGameState 序列，离线测试用 |
| recorder.py | StateRecorder：RawGameState 的 1Hz JSONL（V1 取证用；ViewFrame 录制在 view.recorder） |

**B9 三态裁决**：SC2DriverBot._apply_op 逐 op 回填 OpApply{ok, reason}（None = 已受理待裁决）；
失败发 op_apply_failed GameEvent。events(cursor) 是真切片游标。

**B4 地形回调**：SC2GamePort.on_map_info(cb) 注册；bot 第一个 on_step 时若 game_info 就绪则回调一次。

### 3.3 world

adapt(raw) → GameState：alliance→Owner、health→hp、中性资源过滤（矿脉/气井从 units 拆出）。
V1 不做规则层（power/addon 是 D11 的事）。

### 3.4 flow（策略引擎）

| 文件 | 内容 |
|---|---|
| manifest.py | StrategyManifest/FlowAssembly/parse_strategy/parse_assembly/validate_strategy/validate_assembly；DO_OPS、UNIMPLEMENTED_DO_OPS、BRANCH_KEYS 白名单、step 键白名单（UNIMPLEMENTED_STEP_KEYS） |
| predicates.py | PREDICATE_SIGNATURES（命名参数签名表，validate 与 eval 共用）、eval_when（and/or 短路、None 比较降级 False 并记诊断）、UNIMPLEMENTED_PREDICATE_OPS |
| allocator.py | Allocator（FCFS + sticky lease）、S3 滞回（min→target，跌破 min 补回 target，上限截断）、snapshot()（composition/current/refill_state/leased_tags）、WorkerPoolPort 实现（unleased_workers/reserve/release/reserved_tags，ADR-0030 D3） |
| engine.py | FlowEngine：每帧求值 active step 的分支（首条命中），do 产 Operation 经 port；去重（单位集合+量化参数进键）；exit_step 按边路由；有界环兜底（max_step_transitions 是兜底**不是**出口）；观测记录 branch_hit/transitions（当场记，事后推不出）；snapshot() 读模型 |
| vocab.py | dump_vocabulary()：机器可读词表（谓词签名+arity+动作目录+未实现清单+节点形态+规则）。**同时喂校验器、LLM 提示词卡片、前端 static/schema** —— 前端只照搬不手抄 |

**authoring 语法**（YAML）：when 是结构化 AST（{op: and, args: [...]}），谓词/空间工具用命名参数，
比较/逻辑保留 args；definitions 别名节（{ref: name}）；unit 一律 stable_id（terran/marine），
编译期拒绝 burnysc2 名。**V1 单策略实例**（validate_assembly 拒绝 >1）。

### 3.5 constraint

check_build/check_train/check_gas/...：矿/气/供给/前置/放置/重叠 → ConstraintResult（含结构化 reasons）。
生产运行时的队首门控用它。

### 3.6 planner（生产投影）

- build_order.py：Op 模型（Build/Train/AssignWorkers/Research）、MODULE_REGISTRY、
  expand()（**透传裸 Op** —— 这是"live 队列可投影"的关键）；
- planner.py：Planner.project(gs, seq, until) → ProjectionCurve，逐秒模拟、队首可行性门控、
  **供给守卫**（卡人口/缺 depot 自动插补给站）、AssignWorkers 目标值语义（与 ADR-0030 D2 对齐，幂等）；
- curve.py：ProjectionPoint（逐秒）+ ProjectionEvent（started/completed/stalled+原因）+ 查询辅助。

### 3.7 tactical_map

- placement.py：BuildSlot(tl, size, kind, pos)；**ADR-0027 换算**全在这里：
  build_point（命令点，校准值优先，否则 TL+(size-1)/2）、reported_position（=TL+size/2）、
  tl_from_pos（ceil）、tl_from_reported（floor）。**全仓库唯一一份，前端与 view 都调用它，不重写**；
- region.py：Region/BigRegion/RegionLayer（标签网格 big_grid/leaf_grid + 索引，ADR-0029）；
- base.py：BaseTemplate/SpawnLayout/instantiate_spawn（出生点模板平移到实际 CC）；
- resolver.py / spatial.py：目标解析（point/region/group_center/nearest_enemy）、空间查询；
- data/ladder_map/base_layout.yaml：LadderMap 主基槽位（真机 can_place 扫描校准而来）。

### 3.8 production（生产运行时 + 经济维持器）

- runtime.py：ProductionRuntime —— 命名队列 + **队首门控**（队首不可行则整队冻结，
  原因+起始时间进 blocked，超 STALL_WARN_SECS=30 升级 error 并记 stalls）；
  drain 出的 build 记 _build_flights（在途确认：实体出现/位置匹配/超时换位重试，retries≤6）；
  工具 op：submit/append/prepend/clear/remove/reorder；snapshot() 纯派生读模型
  （队首门控语义已决定每项状态：队首阻塞/未处理）；dropped 审计（不静默）。
- economy.py：EconomyKeeper —— 常驻采矿维持器（ADR-0030）：每帧 目标-实际 差量收敛、
  **目标值语义**（assign_workers(task, n) = 维持 n 个，幂等；缺气矿时配额挂着不蒸发 = issues P9 修复）、
  气优先、防抖 22 帧、领地 = 未被租用/征用的工兵；snapshot()：policy/targets/nodes/reserved/last_ops/assigned。
- worker.py：WorkerAllocator 扇出（矿 2 气 3 饱和）、Emission。

### 3.9 view（只读视图层 —— 契约实现）

| 文件 | 内容 |
|---|---|
| schema.py | **契约数据模型**：REV = 9、TOPICS（14 个）、全部 *View dataclass。只 import game |
| encode.py | dataclass→JSON（RENAME 处理 Python 保留字 from）、grid_to_b64（行主序 uint8；None 进 None 出）、envelope() |
| statics.py | catalog_static（全量目录含 zh）、map_static（区域标签网格+索引、槽位 br/build_point/reported_position 后端算好）、schema_static（**逐字** dump_vocabulary() + 生产闭集）、strategy_static（steps/branches 值树原样/edges/声明）、terrain_static |
| adapt.py | 动态面：world（stable_id 翻译+形态归一+footprint 矩形+在训+挂件+资源饱和度+订单原子映射）、flow/production/ops/economy/session/projection。**派生量全在这里，前端零复算** |
| producer.py | FrameProducer：**唯一产帧路径**（live/录制/夹具共用）。节拍对齐 game_time（1s 观察/3s 投影/5s ops）；投影优先投 live 队列（view.projection 桥），队列空退回参考计划 |
| projection.py | queue_to_ops()：QueueItem→planner Op 一对一翻译；翻不了的项进 skipped 带原因（不静默） |
| recorder.py | ViewRecorder：ViewFrame→JSONL（每帧 flush，崩了也能播）；5s 快照锚点；与 StateRecorder 并存不替换 |
| port.py | OpRing（op 流水环形缓冲）+ RecordingPort（装饰器打 origin，不给 Operation 加字段；回填三态 apply：results > failed_op_ids > None） |
| alerts.py | AlertService：警报唯一来源（队列阻塞/投影卡人口/缺前置/浮矿浮气/产线空闲）；后端拼 zh 文案；去重冷却 20s |
| proposals.py | ProposalStore：提案的存储（追加日志）/校验/应用/双投影/过期失效。apply_hunks 纯函数（insert/delete/modify/reorder，按序应用） |
| observe.py | ObservationPacket：**帧的投影**（agent 读面）。段落中文、facts 机器可读、based_on_seq 闭环、提案历史段（被拒的+待审批的**永远带上**，拒绝理由回流） |

### 3.10 api（传输层）

| 文件 | 内容 |
|---|---|
| app.py | FastAPI 应用。REST：health/schema/sources/session(CRUD+驱动选择)/commands(队列+工人)/proposals(CRUD+预览+接受拒绝)/observation/agent_tools/frames(jsonl+静态面+seek)。WS：/api/frames?source=&rate= 多 topic 流 + seek/play/pause 控制。_hello 先带 rev（前端先判版本再渲染） |
| sources.py | SourceRegistry（目录 JSONL → 帧源）+ JsonlSource（latest_at 快照语义、静态面任何游标都有效） |
| session.py | OfflineSession：进程内假世界 + 真引擎装配；MAX_STALE_SEQ=5 新鲜度门（R8 落点，命令必带 based_on_seq，过期返 409 并回报当前 seq）；会话三件套协议（queue_items/apply_queue/project） |
| live.py | LiveSession：子进程会话。同形接口；driver=sim（假世界）/sc2（真机）。**sc2 用 stdin=DEVNULL**（否则 SC2 继承打开的 stdin 管道会挂起）；terrain 控制行→真 static/terrain 帧；project 走子进程往返（GameState 在子进程里） |
| commands.py | pydantic 请求模型（based_on_seq 必填；未知/不支持 op 400+后端原因） |

### 3.11 tools / agent / vendor

- tools/run_session.py：**会话子进程**。stdin 收命令（sim）、stdout 出帧（一行一 JSON），
  {"_": meta|ack|error|projection|terrain|bye} 控制行与帧分开；命令在**帧边界**应用；
  崩溃结构化报父进程；--driver sim|sc2 同一条产帧路径；loguru 重定向 stderr（burnysc2 污染 stdout）。
- tools/worldsim.py：极简确定性世界（build/train/gather/move/siege 有后果；战斗寻路故意不做）。
- tools/make_fixtures.py：真引擎产夹具（pnpm gen:fixtures 调它）。
- tools/serve_api.py：API 启动器（把 modules/ 塞进 sys.path）。
- agent/：client.py（urllib + transport 接缝）、tools.py（observe/write_surface/**propose** ——
  **没有**直接命令工具，P1/Q1 靠"不给工具"保证）、spec.py（系统提示词：三条做不到+提案要具体）、
  run.py（单回合，--dry 用 FakeLLM 不打网络）。
- vendor/agentic/：BaseAgent 框架（vendored）。DiskWorkspace 物理隔离（R5 机制保证）、
  FakeLLMClient（测试不打网络）、trace 可视化。见 vendor/agentic/NOTICE.md。

---

## 4. ViewFrame 契约（核心中的核心）

### 4.1 信封

~~~json
{ "topic": "frame/world", "rev": 9, "seq": 512, "game_time": 87.0, "wall_ms": 412, "payload": { ... } }
~~~

- topic / rev / seq / game_time / wall_ms 五个键每个帧都有；payload 随 topic 变。
- **seq 就是 GameState.seq（世界版本号）**，不是计数器。同一 game_time 下投影/ops 等
  派生帧可以各自迭代 seq；world 帧的 seq 严格等于世界版本。
- wall_ms 是生成该帧的时刻（毫秒），供前端算延迟、测试验时间线单调。

### 4.2 14 个 topic

> **topic 名是带前缀的字面量**，订阅时必须逐字一致（`modules/view/schema.py:50-55` ↔
> `web/src/contract/index.ts:731-744`，两侧由 contract test 锁死）。动态面前缀是 `frame/`，
> 唯一例外是 `proposals`（无前缀）。写 `subscribe("world")` 会一帧都收不到。

| topic | 静态/动态 | payload 要点 |
|---|---|---|
| `static/map` | 静态 | 区域标签网格（big/leaf）+ 索引 + 槽位（br / build_point / reported_position 后端算好） |
| `static/catalog` | 静态 | 全量单位目录（stable_id、zh 名、cost、build_time、前置、footprint） |
| `static/schema` | 静态 | 逐字 dump_vocabulary()：谓词签名+arity、动作目录、未实现清单、节点形态、生产闭集 |
| `static/strategy` | 静态 | steps / branches 值树原样、edges、声明（strategy.yaml 的镜像） |
| `static/terrain` | 静态 | height / pathable / placeable 三栅格（176x160），后端转 b64。**事件式静态面**：`static/map` 先到（terrain=null），game_info 就绪后本帧补到（rev 9 的原因，见 §4.4） |
| `frame/session` | 动态 | 会话状态机（未连接/启动中/对局中/已结束/崩溃）、地图、种族、error |
| `frame/world` | 动态 | economy + units（已翻译 stable_id + 形态归一 + footprint）+ enemy_clusters + resource_state + grids |
| `frame/flow` | 动态 | strategies[]（活跃 step、branch_hit、transitions、params/variables）+ groups[]（composition/refill_state/leased_tags） |
| `frame/production` | 动态 | queues[]（队首阻塞+原因）/ in_flight[]（在途确认+attempted_slots）/ dropped[] 审计 |
| `frame/economy` | 动态 | tasks[]（quota/target/actual 三个数）/ nodes[]（workers/capacity/saturated）/ reserved / domain_workers |
| `frame/ops` | 动态 | 最近产出/提交的 Operation（OpRing 环形缓冲，带 origin + 三态 apply 回填） |
| `frame/projection` | 动态 | 未来曲线（逐秒点 + 事件 started/completed/stalled+原因）+ skipped（投不了的项，不静默） |
| `frame/alerts` | 动态 | 结构化警报（队列阻塞/卡人口/缺前置/浮矿浮气/产线空闲），zh 文案后端拼 |
| `proposals` | 动态 | 草稿提案与审批状态（**唯一无前缀的 topic**） |

**不是 topic 的读面**：`observation`（agent 观察包）只有 REST `GET /api/observation`
（`modules/api/app.py:248`），不在 `TOPICS` 里 —— 它是"帧的投影"，由 `view.observe` 从已有帧派生。

### 4.3 架构不变量（A1-A8）

> ⚠️ **号段说明（务必先读）**：契约红线的**唯一权威号段是 `docs/plan-frontend.md` §2.4 的 C1-C8** ——
> 它有代码背书（`view/adapt.py:191` 引 C1、`flow/allocator.py:137` 引 C3、
> `view/schema.py:469` 引 C6、`view/schema.py:14` 引 C8 等 17 处源码注释）。
> 本节这 8 条是**传输与产帧层的架构不变量**，与 C1-C8 是不同的东西，故编号为 **A1-A8**，
> 避免与 C 号段撞号。引用时请写全 `C6`（契约红线）或 `A6`（架构不变量），不要只写编号。

| # | 架构不变量 | 与 C 号段的关系 |
|---|---|---|
| **A1** | REV 版本号两侧一致：`modules/view/schema.py` REV ↔ `web/src/contract/index.ts` REV。不匹配时前端 `_hello` 判 rev 直接提示版本不符，不渲染 | 是 **C8**（改契约 = rev+1）的运行期执行机制 |
| **A2** | 信封五键不可缺（topic/seq/game_time/wall_ms/payload + rev）：任何 topic 的帧缺一个键都算违约 | 对应契约 §2.1 信封定义 |
| **A3** | 派生量全部后端算：供给饱和度、在训、挂件、槽位坐标、队列状态、投影曲线、zh 文案 —— 前端不许重算业务量 | = **C2**（footprint 后端算）+ **C3**（状态闭集后端给）+ **C7**（帧里没有的不许现算）的合并陈述 |
| **A4** | 单一真相源：zh 文案在 catalog；规则参数在 catalog/vocab；坐标换算只在 `tactical_map.placement` | = **C4**（zh 来自 catalog）+ **C2**（换算不许有第二份） |
| **A5** | 帧是快照：一帧内自洽，任何游标/时序下都可独立渲染；前端组件不许跨帧做假设 | 是决策 **U1**（组件 = 帧→像素纯函数）的契约侧表述 |
| **A6** | 静态面一次性拉全：静态 topic 返回完整 payload（目录/词表/地形全量），前端不拼不增量 | 传输层约定，**无 C 对应** |
| **A7** | 动态面按 rate 推：同一 websocket 多 topic 订阅，帧间顺序 = 服务端发送顺序 | 传输层约定，**无 C 对应**（契约 §2.1 有同义表述："帧内顺序由流的顺序给，不靠 seq 排"）|
| **A8** | 录制/夹具/live **同一产帧路径**（FrameProducer）：回放与实况字节级同构，不允许"回放专用"简化帧。`GAME_EVENTS` / `OP_CATALOG` / 契约都是**只增不改** | 是决策 **U2**（复盘优先、live 最后接）的机制保证，**无 C 对应** |

### 4.4 REV 历史（1 → 9）

> 权威来源是 `modules/view/schema.py:14-44` 的行内注释（每条都写了**为什么**改）。
> 消费方视角的同一份历史在 `docs/plan-frontend.md` §2「变更记录」。下表照 schema.py 转录。

| REV | 变更 | 原因摘要 |
|---|---|---|
| 1 | 初版 | DSL v0.2 之前，签名表尚不存在，`static/schema` 降级为空参数表 |
| 2 | `static/schema` 改为**逐字镜像** `flow.vocab.dump_vocabulary()`；`frame/production` 队列增 `blocked` | rev 1 手抄已出错（`follow`/`research` 参数、`point_toward` 的 origin 全抄错）；vocab 是校验器/提示词/编辑器共用的权威表 |
| 3 | 区域几何改为**一张标签网格 + 索引**（`big_grid`/`leaf_grid`/`*_index`），删 `leaf[].cells` | per-region mask 不可扩展：20 个区域按 mask 发 750KB，按标签网格始终 37KB |
| 4 | `frame/flow` 增 `eval_diagnostics`；`items[].status` 收窄为 队首阻塞/未处理 并删 `resolved_point`；`in_flight[]` 删 `timeout_frames`/`confirmed`、增 `queue`/`attempted_slots` | B1 落地时按后端**实际能产出什么**校准；`attempted_slots` 正是摆放调试叠加要的 |
| 5 | `static/schema.forbidden` 定为**开放分组表**（`{组名: {op: 原因}}`），不枚举分组名 | 后端新加 `composite_actions`/`step_keys` 两组；前端 zod 写成封闭对象会**静默 strip** 新分组，编辑器就以为那些 op 可用 |
| 6 | 新增 topic `frame/economy` | ADR-0030 经济维持器落地；payload 按 `EconomyKeeper.snapshot()` 实际产出校准（`emitted_count` 而非 `emitted`）|
| 7 | 新增 topic `static/strategy`（steps/branches 值树/edges/声明节） | 策略图**不在任何帧里**：`frame/flow` 只有"现在在哪个 step"。刻意原样下发 branches 值树 —— 摊平一次就得再补通道 |
| 8 | `frame/projection` 增 `skipped`；`source.kind="live_queue"` 有真值 | `Planner.project` 与 `ProductionRuntime` 之前没有互转，"当前队列的投影"产不出来；`view.projection` 补上这条桥。`skipped` 是"不静默" |
| 9 | 新增 topic `static/terrain`（B4：driver 从 game_info 导出三栅格） | 它是**事件式静态面**：真机上 game_info 在 bot 第一个 on_step 才可用，而 `static/map` 在那之前就得发出去 |

历史帧兼容：**只增不改**意味着旧帧永远能按新契约解析（缺的字段有默认值）。
向前兼容只保留"解析"，不承诺"语义"，新字段老前端看不到是预期。

---

## 5. 会话模型与子进程协议

### 5.1 会话装配（三种驱动同一台机器）

~~~
GameState(子进程内) ── world.adapt ── flow.engine ── constraint ── production ── economy
                                        │                │
                                   FrameProducer ── ViewFrame(JSONL/WS) ── 前端
                                        │
                                   ProposalStore ── ObservationPacket ── agent
~~~

驱动只有两处不同：
1. **帧从哪来**：offline 用 FakeGamePort 的脚本序列 / sim 用 worldsim / sc2 用 SC2DriverBot；
2. **命令去哪**：fake → 进程内队列；sc2 → 真机 SC2GamePort。

engine/planner/production/economy/lease/FrameProducer/ProposalStore **完全同一份代码**。

### 5.2 子进程协议（tools/run_session.py）

- 父进程 spawn：python tools/run_session.py --driver sim|sc2 --map 图名；
- 子进程 stdout = **一行一 JSON**：数据行是 ViewFrame 信封；控制行是 {"_": "meta|ack|error|projection|terrain|bye"}；
- stdin：sim 模式收 JSON 命令行（队列/工人/征用）；**sc2 模式 stdin 必须是 DEVNULL**，
  否则 SC2 主进程继承一根打开的 stdin 管道，burnysc2 挂起（真机教训，commit d42aa1e）；
- stderr：loguru 全量重定向（burnysc2 的 sc2.main 会 logger.add(sys.stdout)，不重定向就污染帧协议）；
- 命令在**帧边界**生效：一帧期间世界不变，命令可见性有精确语义（也是新鲜度门 R8 的基础）；
- 崩溃 = 结构化 {"_": "error"} 行，父进程把 cause 带回 API。

### 5.3 LiveSession（modules/api/live.py）

- 与 OfflineSession 完全同形：queue_items / apply_queue / project / terrain；
- project 走子进程往返（GameState 在子进程里，父进程不复制世界）；
- terrain 控制行到达 → 转成真 static/terrain 帧经 _frame 广播（曾经直接丢字节导致前端拿不到，已修）。

### 5.4 三态 apply（B9）

每个 Operation 的结局必居其一，全部可见：
1. **ok**：驱动确认受理（回填 results）；
2. **failed**：驱动明确拒绝 + 原因（failed_op_ids，发 op_apply_failed GameEvent）；
3. **None（待裁决）**：已受理、未裁决（真机异步世界里正常，不是 bug，UI 有第三态）。

---

## 6. 提案 + agent 环路（人在环的核心）

### 6.1 提案通道（ProposalStore）

- agent 的 propose 工具产 hunk 数组，每 hunk 四型：insert / delete / modify / reorder；
- apply_hunks 是纯函数（按序应用，先模拟后落盘）；日志 append-only（proposals.jsonl）；
- 预览 = 干跑 apply 之后的形态（**不会真的改生产**）；
- 双投影：对提案后形态跑 queue_to_ops → Planner.project，得"采纳后曲线"与当前曲线同图对比；
- 过期失效：proposal 带 based_on_seq，序列走远即标记 stale，UI 不让过期提案过审。

### 6.2 提案机制不变量（Q1-Q7）

> ⚠️ **号段说明**：审批红线的**唯一权威号段是 `docs/plan-frontend.md` §6 的 P1-P7**（含两条本节
> 没有的 UI 硬要求：**P2** `validation.ok=false` 时接受按钮禁用但**必须可见**、**P5** `anchor` 帧
> 过期自动置「已失效」禁止盲接受）。本节这 7 条是**后端机制侧**的不变量，编号为 **Q1-Q7** 以免撞号。
> **F14 实现 map_plan 提案时必须同时满足 P1-P7 与 Q1-Q7**，两者不是同一张清单。

| # | 机制不变量 | 与 P 号段的关系 |
|---|---|---|
| **Q1** | agent 没有直接命令工具（工具集 = `done`/`observe`/`write_surface`/`propose`），物理上不能绕过审批 | = **P1**（agent 只能推提案）的实现手段："不给那个工具" |
| **Q2** | 提案必须附 `based_on_seq`（新鲜度闭环，R8）| 是 **P5**（anchor 过期即失效）的前置数据 |
| **Q3** | 提案必须具体到队列与 hunk（"多造点兵"被 spec 拒绝）| spec 层要求，**无 P 对应** |
| **Q4** | 一次一个可审单元（不能一条提案塞十个意图）| 对应契约 `hunks[]`「可逐条接受的最小单元」 |
| **Q5** | 拒绝必须给理由，理由**回流给 agent**（下一轮 observe 带历史段）| = **P3**（拒绝必须能附理由 + 回流）|
| **Q6** | 采纳后必须经校验 + 双投影，投影不可行可直接拒 | 是 **P2**（validation 门）+ 需求 **R6**（提交必过 validate）的后端落点 |
| **Q7** | 提案日志 append-only，全量可审（`runtime/proposals.jsonl`）| 存储层要求，**无 P 对应** |

> **P 有而 Q 没有的两条**（F14 必须自己实现）：**P2** 的"禁用但可见"（agent 要学、用户要诊断）、
> **P6** 用户自己的编辑直接生效但同样过 validate（用户是权威 R3）。

### 6.3 agent 读写面（ObservationPacket + propose）

- observe 工具返回：段落中文摘要 + facts 机器可读（seq/game_time/资源/供给/活跃步骤/队列状态/
  阻塞原因/投影事件）+ 提案历史段（**被拒的 + 待审批的永远带上**，拒绝理由回流）+ based_on_seq 闭环；
- write_surface 只能写 agent 工作区（vendor agentic DiskWorkspace，R5 物理隔离），碰不到引擎状态；
- 提案被接受 → 生产运行时应用 hunk → 下一帧 production/ops 面可见，形成可验证闭环；
- agent spec 三条"做不到"：不能直接命令、不能改策略、不能跨红线（docs/plan-agent.md）。


---

## 7. 前端架构（web/）

### 7.1 分层

| 目录 | 职责 |
|---|---|
| src/contract/index.ts | 契约的 zod 实现（schema 为唯一真相源，类型全部 z.infer 推导）；REV = 9 与后端对齐 |
| src/source/ | FrameSource 抽象：types.ts（接口）、jsonl.ts（复盘）、mock-live.ts（离线演示）、ws.ts（真 API）、reviewable.ts（带游标包装）。**只有这里碰"帧从哪来"** |
| src/store/frames.ts | zustand 帧 store：组件唯一读帧入口。页面组件禁止 import 任何 FrameSource 实现（U1 可执行化） |
| src/canvas/ | 地图分层渲染：MapCanvas / layers / grid / view（视口变换、缩放） |
| src/charts/ | uPlot 封装 + ProjectionChart + ProjectionPairChart（双投影对比，提案预览用） |
| src/graph/ | 策略 AST 渲染（graph/ast.ts）+ 布局 |
| src/pages/ | 六个页面：Overview / MapPage / ProductionPage / FlowPage / PlanningPage / DebugPage（Stubs 是占位） |
| src/shell/ | SessionBar（会话条）/ Timeline（时间线拖回）/ IconRail（图标栏）/ ChatDock（对话栏） |
| src/panels/ | ProposalHost / ProposalReview / proposal-gate：提案审批面板与门控 |
| src/planning/ | queue-draft.ts：生产规划草稿的纯函数（本地草稿，提交时走命令 API） |
| src/api/ | commands.ts / proposals.ts：HTTP 客户端（命令/提案/会话） |

### 7.2 关键决定（U1-U12 全录）

| # | 决定 | 一句话理由 |
|---|---|---|
| U1 | 组件是 ViewFrame → 像素的纯函数，帧源可换 | 复盘免费、时间线免费、前端算不出规则 |
| U2 | 复盘优先，live 最后接 | 不用开游戏、确定性、迭代快 |
| U3 | 契约由消费方（前端）先写并冻结 | UI 才知道要什么派生量；改契约 = rev+1 |
| U4 | 派生量一律后端算 | 坐标换算历史上反复踩坑，TS 里绝无第二份 |
| U5 | 位置可插值，进度/计数绝不插值 | 1Hz 采样不插值一格一格跳，但 ADR-0017 禁止伪造进度动画 |
| U6 | 中文文案一律来自后端 catalog/region | 前端不建 i18n 字典（第二个命名真相源） |
| U7 | UI 与 agent 共用同一套命令 API，不开后门 | UI 操作 agent 可脚本化，agent 动作 UI 看得见 |
| U8 | Flow 编辑 = AST 直编 + 图渲染，不做文本表面语法 | 看与导出先做扎实（已落地），编辑器留 next |
| U9 | V1 固定布局 + 可折叠，拖拽 dock 后置 | dock 引擎独立工程量，早期收益低（**已造成可用性欠账，见 §11**） |
| U10 | Flow 页不围绕泳道设计，但帧 schema 一律列表形状 | 引擎单实例；列表形状让多实例长出来时不改协议 |
| U11 | React + TS + Vite（非 NiceGUI/Streamlit） | 1Hz canvas + 图编辑 + 长曲线，Python 原生 UI 做不好 |
| U12 | 对话栏是重形态：消息流 + 内联提案卡 + 待审批收件箱；不做快捷指令按钮 | 不是观战器，是和 agent 商量打法的地方 |

### 7.3 前端测试（96 passed / 9 文件）

- contract 测试锁 REV 与关键 payload 形状；
- planning 草稿、proposal-gate、ast、projection-data、store 纯函数测试；
- vitest 跑于 pnpm test；前端不重算业务逻辑，测试全部落在纯函数上。

---

## 8. 端到端数据流（四条主线）

### 8.1 读面：世界 → 帧 → 像素

~~~
SC2 on_step ──extract_raw_state──> RawGameState ──world.adapt──> GameState
GameState ──FrameProducer──> ViewFrame(JSONL/WS) ──FrameSource──> zustand store ──> 页面纯函数 ──> 像素
~~~

复盘 = 读 JSONL 走同一 FrameSource 接口；live = 换 ws 实现；夹具 = jsonl 实现。
**同一条产帧路径（架构不变量 A8）保证三种玩法看到的字节一模一样。**

### 8.2 写面：命令（人）

~~~
前端/agent ──HTTP──> api/commands ──based_on_seq 校验（R8，>5 过期 409）──> 会话 ──> 驱动 ──> OpApply 三态 ──> ops 帧回显
~~~

### 8.3 写面：提案（agent，人在环）

~~~
agent.tools.propose ──hunks──> ProposalStore ──validate──> 干跑 apply ──> 双投影 ──> 前端收件箱
人批 ──accept──> 生产运行时应用 ──> 下一帧 production/ops 可见；人拒 ──> 理由回流 agent 下一轮
~~~

### 8.4 会话生命周期

~~~
POST /api/session {driver, map} ──> LiveSession spawn run_session.py ──> stdout 帧流 ──> WS 广播
控制行(meta/ack/error/projection/terrain/bye) ──> 会话状态机 ──> session 帧
~~~

---

## 9. 运行手册

### 9.1 后端测试与类型

- 后端：python -m pytest tests -q（基线 614 passed）；
- 前端：cd web && pnpm test（基线 96 passed / 9 文件）+ pnpm typecheck。

### 9.2 离线复盘（不开 SC2）

1. pnpm gen:fixtures（tools/make_fixtures.py 真引擎产夹具，写入 web/public/fixtures/）；
2. cd web && pnpm dev（web 是静态壳，Vite 入口只搭壳，靠 dsh web 注入 window.__DSH_BOOT__ 或 serve_api 同源服务）；
3. 数据源选 jsonl 夹具。

### 9.3 本地 API + 模拟世界

- python tools/serve_api.py（本地 http://127.0.0.1:8770/api/health，端口可用 --port 换）；
- 前端 ws 源指到该 API；driver=sim 走 worldsim（确定性、可重复、可测）；
- 提案走真实 ProposalStore + 双投影，无 LLM 也能测全链路（用 FakeLLMClient 或直接 HTTP 提交 hunk）。

### 9.4 真机（SC2）

- 前置：SC2 客户端 + burnysc2、--map 用 Ladder 图名；
- python tools/serve_api.py 后建 session driver=sc2；
- **必须记住**：sc2 模式子进程 stdin=DEVNULL（d42aa1e 修复）；日志在 stderr；
- 退出：kill session（子进程收 bye）；SC2 进程随会话终止。**曾出现过黑屏残留 SC2 进程，需手动 taskkill**（见 §10 注意项）。

### 9.5 agent 单回合

- python -m agent.run --dry（FakeLLM 不打网络）；真实跑需 .env 里的 LLM 密钥；
- traces/ 下是 agentic trace；runtime/ 下是提案日志与 agent 工作区（均 gitignore）。

---

## 10. 真机验证记录（commit d42aa1e，SC2 真机）

### 10.1 已验证

| 项 | 结果 |
|---|---|
| driver_check move 命令 | 通过（工人移动在真机生效） |
| LiveSession(sc2) 帧流 | 通过（world 帧正常产出，一行一 JSON） |
| static/terrain（B4） | 通过：height/pathable/placeable 三栅格 176x160，height 有 17760 个非零格 |
| stdout 协议纯净 | 通过：零噪声（loguru 重定向 stderr 后） |
| 会话 error 通道 | None（无错误） |

### 10.2 尚未真机验证

- landing 的驱动侧确认回填（三态 apply 的 ok 分支在真机没走全）；
- 完整生产链（build → 队首门控 → 在途确认）真机重跑；
- 种族覆盖：只验了 terran，protoss / zerg 未验。

### 10.3 真机教训（都进了 commit d42aa1e）

1. sc2_adapter 漏 import OpApply → 第一步就 NameError（已修）；
2. stdin=PIPE 保持打开会让 SC2 主进程挂起（burnysc2 继承管道）→ 改为 stdin=DEVNULL；
3. burnysc2 sc2.main 会 logger.add(sys.stdout)，帧协议被日志污染 → run_session 重定向 loguru 到 stderr；
4. **黑屏残留**：一次会话挂起留下孤儿 SC2_x64.exe 进程（黑屏游戏窗口），已 taskkill。收尾时注意清理。

---

## 11. 已知缺口与诚实自评

### 11.1 功能缺口（后端）

| 缺口 | 现状 | 备注 |
|---|---|---|
| 多策略实例 | 编译期拒绝 >1 | 架构已为多实例预留（U10 列表形状、allocator lease） |
| timer / step-local 变量 | 编译期拒绝（T8） | flow 词表里明示未实现 |
| 敌人聚类 | 返回 null | tactical_map 有区域层，聚类 D 后补 |
| 崩溃恢复 | 无 | 会话挂了要重建；StateRecorder/ViewRecorder 只做取证 |
| mechanics 规则层 | 空模块 | power/addon 等留 D11 |
| 生产 authoring 统一 | planner 计划与 live 队列两套语法 | view.projection 桥做了翻译，但 authoring 面仍是两份 |

### 11.2 可用性自评（回答"前端是否符合最初设想"）

**功能上：符合甚至超过**——所有数据面都有页面（概览/地图/生产/Flow/规划/调试），
提案审批、双投影、复盘时间线、真机 live 都通了。

**可用性上：落后于设想**，欠账三处（都是当时有意的后置决定，不是事故）：
1. **拖拽 dock 欠账（U9）**：V1 固定布局+可折叠。页面已多到六个，固定布局逼用户来回切页；
2. **地图视觉语言欠账**：地形坡道/悬崖在连续绿色渐变里几乎不可见（terrain 三栅格有数据但
   渲染没做高度分段/坡道标记）；建筑无名字标注；单位是同色小点无法区分兵种；
3. **策略图（Flow 页）欠账**：无拖拽/缩放，复杂策略图会跑出屏幕，交互是纯文本表格兜底。

另有一个**结构性问题**：offline/live/replay 三个轴被压平进一条"数据源"下拉条，
用户分不清自己此刻看的是真机实况还是回放（这也是下文 PLAN.md 的出发点）。

### 11.3 为什么欠这些账（当时是合理的）

- 每一条都对应一个当时"先跑通全链路"的取舍（U9 明确记录在案）；
- 帧契约没有为它们留任何债——欠的都是渲染/交互层，数据全在（terrain 栅格、区域标签、
  帧源类型），补齐不需要动 REV。

---

## 12. 测试布局（基线 614 / 96）

| 目录 | 覆盖 |
|---|---|
| tests/architecture/ | 分层依赖方向（PROHIBITED 表）、ports 模块移除、view/api 不被下层 import、schema 只依赖 game |
| tests/game/ | 队列 schema、operation 目录（只增不改）、catalog |
| tests/driver/ | fake 驱动、三态 apply、录制器、地形回调（假端口） |
| tests/flow/ | manifest/predicates/allocator（lease、S3 滞回、上限截断）/engine（转场、去重、有界环、观测） |
| tests/planner/ | 投影曲线、队首门控、供给守卫、透传裸 op |
| tests/constraint/ | 每类检查 + 结构化 reasons |
| tests/view/ | schema REV、encode/roundtrip、static 面、adapt 派生量、producer 节拍、recorder、projection 桥、proposals hunk、observe 包 |
| tests/api/ | REST/WS/session 新鲜度门（409）/子进程协议/terrain 控制行（含死锁回归）/提案预览接受拒绝 |
| tests/agent/ | 工具集（无直接命令）、spec、FakeLLM 单回合 |
| web/src/**/*.test.ts | contract 锁 rev、planning 草稿、proposal-gate、ast、projection-data、store（9 个文件） |

