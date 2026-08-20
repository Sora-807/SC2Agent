# 前端计划 —— ViewFrame 契约 + 复盘优先驾驶舱 + 提案审批工作台

> 目标:把"看不见的运行时"变成可看、可拖时间、可和 agent 商量的工作台;前端零规则复算。
> 基线:后端无任何服务层(全仓库 0 处 fastapi/ws);flow 运行时状态全是私有字段;planner 投影曲线已可直接画图。
> 关联:`docs/plan-backend-view.md`(后端配套任务)、`docs/需求文档-v0.1.md`(红线 R1-R8)、`docs/P0-影响边界.md`、
>       `docs（旧）/adr/0017/0022/0023/0024/0025`(UI 构想来源,本文件按代码现状取舍)。
> 并行前提:`docs/plan-strategy-dsl-v02.md` 正在执行(T1-T6 重写 flow/allocator/predicates/engine/manifest/runtime)。
>          **F0-F6 对后端零依赖**(跑在自己定义的夹具上),因此本计划可与 DSL 重构同时进行。

## 0. 本轮范围

- ✅ 帧契约 ViewFrame v0.1(消费方定义,后端 `plan-backend-view.md` 实现)
- ✅ 复盘优先:先在夹具/JSONL 上做出全部只读视图,live 是最后接上的一个帧源
- ✅ 六个页面:概览 / 地图 / 生产 / Flow / 规划 / 调试
- ✅ 命令流水面板 + 摆放调试叠加层(老 ADR 没有,本项目 debug 史要求)
- ✅ 重对话栏:agent 推草稿 → 用户审批(含队列提案的双投影曲线对比)
- ❌ 不做:可拖拽 dock 布局、表达式文本 DSL、鉴权/多用户、任意帧平滑回放、WebGL、移动端
- ❌ 不做:live 中编辑模块/Strategy(R5);live 参数热改(`live_editable` 已被 DSL-T3 删除,等 hot-edit 轮)

## 1. 决策记录(每项一句话理由)

| # | 决策 | 理由 |
|---|---|---|
| U1 | **组件是 `ViewFrame → 像素` 的纯函数**,帧源可换(live/JSONL/夹具/环形缓冲) | 一条决定同时买到:复盘免费、时间线拖回免费、"前端不许算规则"从口号变成机制(帧里没有=画不出来) |
| U2 | **复盘优先,live 最后接**(ADR-0023 说复盘照抄 live,本计划反过来) | 不用开游戏、确定性、迭代快;live 只是换帧源 |
| U3 | **契约由消费方(前端)先写并冻结**,后端实现 + contract test 锁死 | 前端不能等后端;UI 才知道要什么派生量;改契约 = rev+1 且两份计划同步 |
| U4 | 派生量**一律后端算**:footprint 矩形、滞回状态、阻塞原因、警报、分组归属、聚类 | ADR-0027 的坐标换算在 git 史上反复踩坑,TS 里绝不能有第二份;ADR-0007/0022 禁止前端另算警报 |
| U5 | **位置可插值,进度/计数绝不插值** | 1Hz 采样不插值则地图一格一格跳;但 ADR-0017 明令禁止伪造进度动画,两者要分开 |
| U6 | 中文文案**一律来自后端 catalog/region 数据**,前端不建 i18n 字典 | `display_name_zh`/`aliases_zh`/`description_zh` 已在数据层,前端再建一份=第二个命名真相源 |
| U7 | **UI 与 agent 共用同一套命令 API**,不开 UI 后门 | UI 操作 agent 可脚本化,agent 动作 UI 看得见;写入面本来就小(队列 op/会话/提案) |
| U8 | Flow 编辑 = **AST 直编 + 图渲染**,不做文本表面语法(定 D12) | `when` 已是结构化 AST。DSL v0.2 落地后 `flow.vocab.dump_vocabulary()` 提供签名表 + arity + 动作目录 + 未实现清单 + 节点形态 + 规则,且**与校验器和 LLM 提示词同源** → 无往返解析问题,schema 约束连线的数据全齐,编辑器风险大幅下降 |
| U9 | V1 **固定布局 + 可折叠**,拖拽 dock 后置(违 ADR-0023 §2.1) | dock 引擎+布局序列化是独立工程量,早期收益低 |
| U10 | Flow 页**不围绕泳道设计**,但帧 schema 一律列表形状 | 引擎单实例(DSL-T2c#6 编译期拒绝多实例),ADR-0017 的 lane 模型现在没后端;列表形状让多实例长出来时不改协议 |
| U11 | React + TS + Vite(而非 NiceGUI/Streamlit 单语言方案) | 1Hz canvas + 交互式图编辑 + 长曲线,Python 原生 UI 框架做不好;代价是引入第二语言,已确认接受 |
| U12 | 对话栏是**重形态**:消息流 + 内联提案卡 + 待审批收件箱;**不做快捷指令按钮** | 用户确认;这不是观战器,是和 agent 商量打法的地方 |

---

## 2. 帧契约 ViewFrame v0.1(冻结面)

> **本节是前后端唯一契约**。后端按此实现(见 `plan-backend-view.md` B0/B1),前端按此写 TS 类型 + 运行期校验。
> 改动流程:改字段 = `rev` +1 + 本节修订 + 后端 contract test 同步 + 两份计划都改。

**变更记录**

| rev | 变更 | 原因 |
|---|---|---|
| 1 | 初版 | DSL v0.2 之前,签名表尚不存在,`static/schema` 降级为空参数表 |
| 7 | 新增 topic `static/strategy`(steps/branches/edges/声明节) | F4 的图**不在任何帧里**:`frame/flow` 只有"现在在哪个 step",图本身从来没下发过。只靠转移历史推图会看不见"一次都没走过的 step"。归静态面(每个 flow 版本只变一次);hot-edit(S8)落地后改事件驱动。`branches` 原样带值树 —— F4 只要 step/edge,但 F9 的 AST 编辑器要完整结构,摊平一次就得再补通道 |
| 6 | 新增 topic `frame/economy`;`tasks` 给 **quota / target / actual 三个数** | ADR-0030 的经济维持器落地。维持器的 `snapshot()` 给"可达目标"(受节点容量与人数夹紧),持久配额在 policy 里。只给 target 的话,"精炼厂没建好时气目标 0"会让用户以为 `assign_workers` 的意图又蒸发了 —— 恰好与 issues P9 的修复相反,所以两个数都要给 |
| 5 | `static/schema.forbidden` 定为**开放分组表**(`Record<组名, Record<op, 原因>>`),不枚举分组名 | 后端给 `forbidden` 加了 `composite_actions`(assign_workers 需扇出层,ADR-0030 D1)与 `step_keys` 两组。后端因为"整块转发"自动就有了;前端 zod 写成封闭对象会**静默 strip** 新分组,编辑器就以为那些 op 可用。这才是 rev 2"逐字镜像、不加工"该有的样子:连分组名都不枚举 |
| 4 | `frame/flow` 增 `eval_diagnostics`;`items[].status` 收窄为 队首阻塞/未处理 并删 `resolved_point`;`in_flight[]` 删 `timeout_frames`/`confirmed`、增 `queue`/`attempted_slots` | B1 落地时按后端**实际能产出什么**校准:① 引擎本来就在记求值诊断("条件其实没求出来"),不暴露等于丢功能;② 队首门控语义下已发出的项已出队或进 `in_flight`,队列里不可能有"已发出";③ `timeout_frames` 要在前端复制一份魔法公式、`confirmed` 恒 false(在途的都还没确认),而 `attempted_slots` 正是摆放调试叠加要的 |
| 3 | 区域几何改为**一张标签网格 + 索引**(`regions.big_grid`/`leaf_grid`/`big_index`/`leaf_index`),删掉 `leaf[].cells` | per-region mask 不可扩展:LadderMap 176×160=28160 格,一份 mask ≈37KB;20 个区域按 mask 发就是 750KB,按标签网格发始终 37KB。且直接镜像后端 `tactical_map.RegionLayer` 的模型,前端画区域变成"一张位图 + 一份调色板" |
| 2 | `static/schema` 改为**逐字镜像** `modules/flow/vocab.py::dump_vocabulary()`;`frame/production` 队列增 `blocked` | DSL v0.2 落地后 flow.vocab 成了"校验器 / LLM 提示词卡片 / 可视化编辑器"共用的权威表,前端照搬即不可能漂移。rev 1 手抄的代价已经出现:`follow`/`research`/`use_ability` 的参数、`point_toward` 的 `origin` 全抄错。队列 `blocked` 来自后端 T4 已实现的 `ProductionRuntime.blocked` |

### 2.1 信封与通道

```ts
interface Envelope<T> {
  topic: Topic
  rev: 5                 // 契约版本;不匹配前端拒绝渲染并提示。与 modules/view/schema.py::REV 由测试锁死一致
  seq: number            // **世界版本号** = GameState.seq。同一 tick 的多个 topic **共享**它;
                         // 帧内顺序由流的顺序给(JSONL 行序 / WS 发送序),不靠 seq 排。
                         // 它同时是命令的 based_on_seq(R8 的门靠 session.seq - based_on_seq 判断)——
                         // 用自增计数器会让这道门对 agent 永久失效,有两侧回归测试锁死。
  game_time: number      // 游戏秒(唯一时间基准;所有定时器对齐它,不用墙钟 —— ADR-0025 §6)
  wall_ms: number         // 墙钟毫秒,仅诊断,不参与语义
  payload: T
}
```

| topic | 传输 | 节拍 | 说明 |
|---|---|---|---|
| `static/map` | REST | 每局一次 | 地图静态:尺寸/区域/槽位/标记点/资源点/地形 |
| `static/catalog` | REST | 每局一次 | 单位建筑目录(zh 名/造价/时间/前置/footprint 边长) |
| `static/schema` | REST | 每局一次 | 谓词签名表/do 目录/动作原子/枚举白名单 —— AST 编辑器的 schema 权威源 |
| `frame/session` | WS | 事件驱动 | 会话状态机 |
| `frame/world` | WS | 1s | 经济 + 单位 + 建筑 + 图层 |
| `frame/flow` | WS | 1s | 策略实例状态 + 组状态 |
| `frame/production` | WS | 1s | 队列 + 在途 + 掉项 |
| `frame/ops` | WS | 事件驱动 | 命令流水(提交/应用结果/落地) |
| `frame/projection` | WS | 3s | planner 滚动投影 |
| `frame/alerts` | WS | 事件驱动 | 结构化警报 |
| `proposals` | WS | 事件驱动 | 草稿提案与审批状态 |

节拍与 ADR-0025 §6 一致(1s 观察/3s 投影/5s 快照/事件驱动警报)。

```ts
type Pt = [number, number]                                   // 世界坐标(左下原点浮点)
type Cell = [number, number]                                 // 格点
interface GridB64 { w: number; h: number; bpp: 8; data_b64: string }  // 行主序 data[y][x];禁发嵌套数组
```

### 2.2 静态面

```ts
interface MapStatic {
  map_name: string
  size: [number, number]
  spawn: string                    // 本局出生变体("bl"/"tr")
  terrain: null | { height: GridB64; pathable: GridB64; placeable: GridB64 }   // B4 前为 null,前端降级纯色底
  // 区域几何 = 一张标签网格 + 索引(不是每区一份 mask,见 rev 3)。索引键在 JSON 里是字符串。
  regions: {
    big: { stable_id: string; anchor: Pt; display_name_zh: string; aliases_zh: string[]; children: string[] }[]
    leaf: { stable_id: string; parent: string; anchor: Pt; display_name_zh: string;
            aliases_zh: string[]; build_slots: string[] }[]
    big_grid: GridB64 | null
    leaf_grid: GridB64 | null
    big_index: Record<string, string>    // 标签值 -> big region stable_id
    leaf_index: Record<string, string>
  }
  build_slots: { name: string; tl: Cell; br: Cell; size: number;
                 kind: "supply" | "production" | "addon";
                 build_point: Pt; reported_position: Pt }[]   // br/build_point/reported_position 后端按 ADR-0027 算好
  pos_marks: { name: string; pos: Pt; description_zh: string }[]
  resource_nodes: { tag: number; stable_id: string; pos: Pt; kind: "mineral" | "geyser" }[]
}

interface CatalogStatic {
  entries: {
    stable_id: string; display_name_zh: string
    role: "worker" | "combat" | "building" | "upgrade"
    capabilities: string[]
    cost: { minerals: number; vespene: number; supply: number }
    build_time: number
    produced_by: string | null
    prerequisites: string[]
    size: number | null                       // footprint 格边长(建筑)
    attack_range: number | null; siege_range: number | null
    burnysc2_name: string                     // 仅允许调试面板显示;语义一律用 stable_id
  }[]
}

// 前 8 个字段**逐字镜像** modules/flow/vocab.py::dump_vocabulary() —— 后端已把它做成
// "校验器 / LLM 提示词卡片 / 可视化编辑器目录"共用的权威表,前端照搬,绝不手抄。
// 后两个字段是生产与目标解析侧闭集,不属 flow.vocab,由后端另行导出。
interface SchemaStatic {
  predicates: Record<string, {
    params: { name: string; required: boolean }[]
    kind: "value" | "bool"          // bool=可作 when 条件;value=可放参数位(group_center/point_toward…)
  }>
  operators: Record<string, { min_args: number; max_args: number | null }>   // max_args=null → and/or 不限
  actions: Record<string, { params: { name: string; type: string; required: boolean }[] }>  // = OP_CATALOG
  do_ops: string[]
  // 不可用的东西:{组名: {op: 原因}}。编辑器置灰 + 显示**后端给的原因**。
  // **分组名刻意不枚举** —— 后端目前有 predicates / spatial_tools / do_ops /
  // composite_actions / step_keys 五组,以后还会加;写成封闭对象 = 新分组被静默 strip。
  forbidden: Record<string, Record<string, string>>
  declarations: { param_keys: string[]; param_types: string[]; loop_limit_keys: string[] }
  node_forms: Record<string, string> // 各节点形态的示例写法(编辑器提示直接用)
  rules: string[]                    // 编译规则的人类可读清单(编辑器侧栏 / 校验失败解释)

  queue: {                           // game.production.QueueOp/WorkerTask + runtime.UNSUPPORTED_QUEUE_OPS
    ops: string[]
    unsupported_ops: Record<string, string>   // op -> 不支持的原因(不静默)
    worker_tasks: string[]
  }
  target_kinds: string[]             // point/region/group_center/nearest_enemy
}
```

### 2.3 动态面

```ts
interface SessionFrame {
  state: "未连接" | "启动中" | "对局中" | "已结束" | "崩溃"
  frame_source: "live" | "replay" | "fixture"
  map_name: string | null; my_race: string | null; enemy_race: string | null
  game_time: number; error: string | null
}

interface WorldFrame {
  economy: { minerals: number; vespene: number; supply_used: number; supply_cap: number }
  units: {
    tag: number
    stable_id: string                      // 后端已翻译并归一变体;禁出现 burnysc2 名
    form: string | null                    // 形态变体("sieged");null=主形态
    owner: "self" | "ally" | "enemy" | "neutral"
    pos: Pt; facing: number
    hp: number; hp_max: number; shield: number; energy: number
    build_progress: number
    group_id: string | null                // flow 分组归属,后端 join
    order: { atom: string | null; ability_raw: string; target_tag: number | null;
             target_pos: Pt | null; is_auto: boolean } | null
    footprint: { tl: Cell; br: Cell } | null            // 建筑;后端算,前端只画矩形
    producing: { stable_id: string; progress: number }[] | null
    addon: "reactor" | "techlab" | null
    carrying: "minerals" | "vespene" | null
    buffs: string[]
  }[]
  enemy_clusters: { center: Pt; radius: number; count: number;
                    by_stable_id: Record<string, number> }[] | null   // 聚类未实现时 null
  resource_state: { tag: number; remaining: number | null; workers: number }[]
  grids: { creep?: GridB64; visibility?: GridB64 } | null              // 仅变化时下发
}

interface FlowFrame {
  strategies: {                            // V1 恒长度 1;列表形状为多实例预留(U10)
    instance_id: string; strategy_ref: string; version: number
    params: Record<string, unknown>        // 生效值(default + instance 覆盖)
    variables: Record<string, unknown>; locals: Record<string, unknown>
    definitions: Record<string, unknown>   // DSL-T2b defs 节(编辑器/调试展示)
    active_step: string
    step_entered_at: number; step_elapsed: number; step_entry_count: number
    branch_hit: { step_id: string; branch_id: string | null; index: number } | null
    transitions: { from: string; to: string; kind: string | null;
                   reason: string | null; at: number }[]               // 最近 K=20
    transition_count: number; transition_limit: number
    done: boolean
    exit_record: { kind: string; reason: string } | null
    bindings: Record<string, string>       // slot -> group_id
  }[]
  groups: {
    group_id: string
    composition: Record<string, { min: number; target: number; max: number; current: number }>
    refill_state: "满足" | "滞回区" | "补兵中" | "已截断"    // 后端按 DSL-T3 滞回语义判定
    leased_tags: number[]
    center: Pt | null; hp_ratio: number | null
  }[]
}

interface ProductionFrame {
  queues: {
    name: string
    head_status: "空" | "可执行" | "阻塞"
    // 队首阻塞详情(后端 ProductionRuntime.blocked[name]);warned = 已超后端 STALL_WARN_SECS 阈值
    blocked: { reason: string; since: number; waited: number; warned: boolean } | null
    items: {
      index: number
      op: "build" | "train" | "research" | "cancel" | "assign_workers"
      stable_id: string | null; count: number
      placement: { kind: "exact"; mark: string }
                | { kind: "in_region"; region: string; index: number | null } | null
      task: "mineral" | "gas" | "idle" | null
      status: "队首阻塞" | "未处理" | "已发出" | "在途"       // 后端给,前端不推断(DSL-T4 队首门控语义)
      block_reason: string | null                            // 缺矿/缺气/缺供给/前置没/无产槽/无builder
      resolved_point: Pt | null                              // placement 解析后落点(摆放叠加用)
    }[]
  }[]
  in_flight: { stable_id: string; kind: "build" | "train" | "addon" | "gas";
               builder_tag: number | null; expect_pos: Pt | null; radius: number;
               frames_waited: number; timeout_frames: number; retries: number; confirmed: boolean }[]
  dropped: { at: number; op: string; stable_id: string | null; reason: string }[]
}

interface OpsFrame {
  ops: {
    op_id: number; seq: number; at: number
    action: string; unit_tags: number[]; params: Record<string, unknown>
    origin: "flow" | "production" | "worker" | "user" | "agent"        // 谁发的(追责)
    apply: { ok: boolean; failed: boolean; detail: string | null } | null   // B9(D6)前恒 null
    landing: { kind: "confirmed" | "timeout" | "retried" | "unknown";
               at: number | null; detail: string | null } | null
  }[]
}

interface ProjectionFrame {
  based_on_seq: number; based_on_game_time: number; horizon: number
  source: { kind: "live_queue"; queue_name: string } | { kind: "draft"; plan_id: string }
  points: { t: number; minerals: number; gas: number; supply_used: number; supply_cap: number;
            mineral_workers: number; gas_workers: number;
            buildings: Record<string, number>; units: Record<string, number>;
            in_flight_count: number }[]
  events: { kind: "started" | "completed" | "stalled";
            stable_id: string | null; t: number; reason: string | null }[]
}

interface AlertsFrame {
  alerts: { id: string
            kind: "supply_block" | "mineral_float" | "gas_float" | "line_idle"
                | "prereq_missing" | "plan_drift" | "queue_blocked"
            severity: "info" | "warn" | "error"
            at: number; eta: number | null
            text_zh: string
            source: "projection" | "production" | "session"
            payload: object }[]
}
```

### 2.4 契约红线

| # | 红线 |
|---|---|
| C1 | 帧内类型一律 **stable_id**(`terran/marine`);`burnysc2_name` 只出现在 catalog 且只许调试面板显示 |
| C2 | footprint / build_point / reported_position **后端算**(ADR-0027);前端零几何换算 |
| C3 | 所有状态判定(`block_reason`/`refill_state`/`status`/`landing`/`alerts`)后端给闭集字符串,前端只映射颜色图标 |
| C4 | zh 文案来自 catalog/region 数据 |
| C5 | grid 一律 `GridB64` 行主序,禁嵌套数组 |
| C6 | `strategies`/`groups`/`queues` 保持列表形状,不假设长度 1 |
| C7 | 帧里没有的字段,前端不许现算补齐 |
| C8 | 改契约 = `rev`+1 + 本节修订 + 两份计划同步 |

---

## 3. 技术栈与目录

| 位置 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TS + Vite + pnpm | React Flow / Monaco / uPlot 生态是硬理由(U11) |
| 状态 | Zustand + 帧源抽象 | 帧是外部事件流,store 只做最新帧 + 环形缓冲 |
| 校验 | zod(运行期校帧) | 契约漂移当场报错而不是渲染出错值 |
| 地图 | Canvas2D 分层合成 | 400 单位 @1Hz 绰绰有余;grid 走 offscreen ImageData;WebGL 属过早优化 |
| 曲线 | uPlot | 逐秒长曲线,小且快 |
| 图 | **手写 SVG + 分层布局**(原定 React Flow + ELK) | 实际策略图只有 2~5 个节点;那两个依赖加起来 ~1.4MB,为这点规模不值。手写反而更可控:回边画绕行弧、边上直接标退出原因、当前节点脉冲动画。F9 的编辑器若需要拖拽再评估 |
| 样式 | Tailwind | 密集信息面板,原子类迭代快 |
| 测试 | vitest(组件/契约) | 组件是纯函数 → 夹具进、快照出 |

```
web/
  src/
    contract/     # §2 的 TS 类型 + zod schema(唯一真相源的镜像)
    source/       # FrameSource: ws / jsonl / fixture / ringbuffer 装饰器
    store/        # zustand: frames / selection / layout / proposals
    canvas/       # 分层渲染、grid 位图、footprint、位置插值
    charts/       # uPlot 封装(单曲线/双曲线对比)
    graph/        # React Flow + ELK:策略图渲染 + AST 编辑
    panels/       # 纯 props 面板(只吃 contract 类型)
    pages/        # overview / map / production / flow / planning / debug
    shell/        # 会话条 / 时间线 / 左图标栏 / 右对话栏
  fixtures/       # *.jsonl 帧夹具(F0 手写 → B0 后由后端脚本生成)
  tests/
```

## 4. 帧源抽象

```ts
interface FrameSource {
  readonly caps: { live: boolean; seek: boolean }
  subscribe<T>(topic: Topic, cb: (env: Envelope<T>) => void): () => void
  seek(game_time: number): void          // caps.seek=false 时抛
  range(): { from: number; to: number }
}
```

- `FixtureFrameSource`(F0)→ `JsonlFrameSource`(F1,复盘)→ `WsFrameSource`(F8,live)。
- `RingBuffer` 装饰器保留最近 N 帧:**live 下拖回最近几分钟不碰后端**;超出范围才请求后端 seek。
- 页面组件**只允许**经 store 读帧,禁止 import 任何 FrameSource 实现 —— 这是 U1 可执行化的地方。
- live 中拖回历史 = 进只读回看模式,live 后台继续收帧,右上角"回到实时"(ADR-0023 §3)。

## 5. 页面与布局

### 全局外壳

```
┌────────────────────────────────────────────────────────────────┬──────────┐
│ 会话条  ● 对局中 08:42  LadderMap  T vs P   [停止] [回到实时]  │  对话     │
├────────────────────────────────────────────────────────────────┤  +       │
│ 时间线  ─▏▏▎──●────▲──────▲────────────────[拖动 08:12]────   │  提案     │
├──┬─────────────────────────────────────────────────────────────┤  收件箱   │
│图│                                                             │  (可收起) │
│标│                    主区(按页面切换)                          │  ⓷ 待审批 │
│栏│                                                             │          │
└──┴─────────────────────────────────────────────────────────────┴──────────┘
```

时间线只在 实时/复盘 出现;刻度标注快照点、警报、策略转移(ADR-0023 §3)。

### 页面清单

| 页 | 内容 | 关键数据 |
|---|---|---|
| **概览** | 上:资源曲线(实际 vs 预测) + 生产泳道 + 警报;下:地图 + Flow 状态。四面板可折叠、点击跳转 | 全部 topic |
| **地图** | 全屏 canvas + 图层开关 + 选中检查器 | `static/map` + `frame/world` |
| **生产** | 左:命名队列(拖拽排序、队首阻塞高亮);右:投影曲线 + Gantt;下:目录选择器(前置不满足置灰) | `frame/production` + `frame/projection` + `static/catalog` |
| **Flow** | 策略图(有环、按类别分子图、当前节点动画、边悬停显示 reason);live 只读 | `frame/flow` + `static/schema` |
| **规划** | 离线三工作台:地图规划 / 生产规划 / Flow 装配(AST 编辑器) | 草稿态,**绝不叠加 live**(ADR-0022) |
| **调试** | 命令流水表 + 摆放调试叠加 + 原始帧检查器 + 掉项审计 | `frame/ops` + `frame/production.dropped` |

### 地图图层与可实现性

| 图层 | 数据来源 | 现状 |
|---|---|---|
| 区域边界/大区着色 | `MapStatic.regions` | ✅ |
| BuildSlot 槽位框(按 kind 着色) | `MapStatic.build_slots` | ✅ |
| 建筑(含在建进度) | `WorldFrame.units.footprint` + `build_progress` | ✅ |
| 单位;已分组按 flow 组图标 | `units.group_id` | ✅(需 B1) |
| 生产建筑浮动显示在训单位 | `units.producing` | ✅(需 B1) |
| 矿脉/气井 + 饱和度 | `resource_state` | ✅ |
| Intent 连线(当前点→目标虚线) | `units.order.target_pos` | ✅ |
| 视野 / 菌毯 | `grids` | ✅ 待 B5 决定压缩 |
| 未来 30s 待建虚位 | `items.resolved_point` + `projection.events` | ⚠️ 需 B1 解析落点 |
| 敌方聚类 | `enemy_clusters` | ⚠️ 后端未实现,null 时降级为逐个单位 |
| 地形(高度/可走/可建) | `MapStatic.terrain` | ❌ 需 B4;之前纯色底降级 |

### 调试页两个面板(老 ADR 没有,本项目 debug 史要求)

- **命令流水**:每条 Operation(op_id/seq/action/unit_tags/params/origin)→ 提交 → `apply` → `landing`
  (确认/超时/换位重试),按 seq 排列可筛选。覆盖 `unit_command_uses_self_do`、挂件实体不出现、
  建造确认超时、换位重试这一整类历史 bug,并逼着后端把 D6/D7 定下来。
- **摆放调试叠加**:意图槽位矩形 / 期望报告位 vs 实际报告位 / 每次换位重试落点 / 可建区(B4 后)。
  ADR-0027 那个"偶数尺寸命令点 = TL+(size-1)/2"的坑,有这张图就是几分钟的事。

## 6. 草稿提案审批流(重对话栏)

> agent 不改状态,只推提案;用户审批。这与 R5(live 不能创建/编辑模块与 Strategy)、
> R6(提交必须 validate+compile)、R3(用户优先于 flow)天然对齐。

```ts
interface Proposal {
  id: string
  author: "agent" | "user"
  created_at: number
  anchor: { seq: number; game_time: number } | null    // 基于哪一帧;可跳回"当时的状态"
  kind: "production_queue" | "map_plan" | "flow_ast" | "flow_commit" | "group_composition" | "params"
  title_zh: string
  rationale_zh: string                                  // agent 的理由;**空理由的提案不可接受**
  target: object                                        // queue name / plan id / strategy instance
  hunks: { id: string; kind: "insert" | "delete" | "reorder" | "modify";
           text_zh: string; payload: object }[]         // 可逐条接受的最小单元
  validation: { ok: boolean; errors: { hunk_id: string | null; text_zh: string }[] } | null
  preview: { kind: "projection_pair"; current_ref: string; proposed_ref: string }
         | { kind: "map_overlay"; changed_slots: string[] }
         | { kind: "graph_diff"; added: string[]; removed: string[]; changed: string[] } | null
  status: "待审批" | "已接受" | "部分接受" | "已拒绝" | "已失效"
  decision: { at: number; accepted_hunks: string[]; comment_zh: string | null } | null
}
```

审批红线:

| # | 规则 |
|---|---|
| P1 | agent 只能推提案,不能直接改状态 |
| P2 | `validation.ok=false` 的提案**接受按钮禁用**,但必须可见(agent 要学、用户要诊断) |
| P3 | 拒绝**必须能附理由**,理由回流给 agent 作为下一轮输入(否则 agent 重复推同一提案) |
| P4 | 接受走与 agent 相同的命令 API(U7),无 UI 专用后门 |
| P5 | `anchor` 帧过期(live 走远 / 队列已变)→ 自动 `已失效`,禁止盲接受 |
| P6 | 用户自己的编辑**直接生效**(用户是权威 R3),但同样过 validate 门;可选"请 agent 复核"生成反向 review |
| P7 | V1 的 `params` / `flow_ast` 提案只作用于**离线草稿**,不作用于 live 实例(`live_editable` 已被 DSL-T3 删除) |

三种 diff 视图:

- `production_queue` → 队列行 diff + **双投影曲线并排(当前 vs 提案)**。接受前先看未来,这是本 UI 的杀手功能。
- `map_plan` → 地图上叠"当前 vs 提案",变更槽位高亮。
- `flow_ast` → 策略图 diff(新增/删除节点、改动分支高亮)。

对话栏形态:消息流 + **内联提案卡片**(可展开到主区全屏 diff)+ 顶部"待审批 N"徽标 + 收件箱视图。
提案与 `anchor` 帧绑定 → 时间线上可标出提案点,讨论历史可回溯。**不做快捷指令按钮**(用户已确认)。

---

## 7. 任务总览与依赖顺序

| 顺序 | 任务 | 前端依赖 | 后端依赖 | 产出 |
|---|---|---|---|---|
| F0 | 契约落地 + 夹具 + 骨架 | 无 | **无** | contract TS+zod、手写 fixtures、vite 骨架、FixtureFrameSource |
| F1 | 外壳 + 时间线 + 帧源切换 | F0 | 无 | shell、JsonlFrameSource、RingBuffer、只读回看模式 |
| F2 | 地图渲染 ✅**已完成** | F0 | 无(terrain 降级) | 分层 canvas、footprint、区域标签网格、插值、选中检查器、摆放调试叠加 |
| F3 | 生产页 + 投影图 ✅**已完成**(含写入面) | F0 | B6 已接通 | 队列(队首阻塞/掉项审计)、投影曲线+Gantt 泳道、经济维持器面板、目录选择器(按前置置灰) |
| F4 | Flow 状态图(只读) ✅**已完成** | F0 | 无(需 `static/strategy`) | **手写 SVG + 分层布局**(改了选型,见下)、分支 AST 渲染、转移历史、回边可见 |
| F5 | 调试页 ✅**已完成** | F2 | — | 命令流水(按 origin/动作筛)、求值诊断、掉项审计、警报、原始帧检查器;摆放叠加已在地图页 |
| F6 | 概览页组装 ✅**已完成** | F1-F4 | — | 六面板 + 可折叠(localStorage) + 点击跳完整页 + 内嵌地图与投影 |
| F7 | 对话栏 + 提案审批 ✅**已完成**(agent 侧待 B10) | F1,F3 | B7 ✅ | 重对话栏(消息流+内联提案卡+待审批徽标)、主区审批面板、**双投影对比图**、逐条接受、拒绝附理由 |
| F8 | 接 live | F1-F6 | B2,B3 | WsFrameSource、会话 start/stop、回到实时 |
| F9 | 规划页(地图/生产/Flow AST 编辑器) | F2,F3,F4 | B2,B4 + DSL-T2 | 离线创作工具 |

**F0-F6 对后端零依赖** —— 可与 DSL v0.2 重构完全并行。F5 先跑夹具、B1 落地后自动变真数据。

---

## 8. 任务详述

### F0 契约落地 + 夹具 + 骨架

**目标**:前端在无后端的情况下有形状正确的数据可渲染。
**改动**:`web/` 初始化(vite+ts+tailwind+vitest);`src/contract/` 按 §2 写 TS 类型 + zod schema;
`src/source/fixture.ts`;`web/fixtures/` 手写 3 组夹具:①开局 60s ②生产阻塞(队首缺气)③蛙跳推进中。
**测试**:每份夹具过 zod 校验;`rev` 不匹配时拒绝渲染。
**验收**:`pnpm dev` 起得来;夹具切换下拉框可换 3 组数据;契约类型与 §2 逐字段对齐(人工 review 清单)。

### F1 外壳 + 时间线 + 帧源切换

**目标**:时间是一等公民,帧源可换。
**改动**:shell(会话条/时间线/左图标栏/右栏骨架);`JsonlFrameSource`;`RingBuffer`;只读回看模式 + "回到实时"。
**测试**:seek 到任意快照点后所有 store 切片一致;超出环形缓冲范围时降级为请求 seek。
**验收**:拖时间线,概览四面板同步回到该时刻;live 模拟源下拖回后 live 帧仍在后台累积。

### F2 地图渲染

**目标**:地图看起来像地图,且零几何复算。
**改动**:分层 canvas(底/区域/槽位/建筑/单位/图层/调试);`GridB64` → offscreen ImageData;
footprint 矩形直接用 `tl/br`;位置插值(仅位置,U5);选中检查器。
**测试**:footprint 渲染快照测试(断言不出现任何 size/2 之类的本地换算);grid 解码正确性;
插值在两真实采样点间单调。
**验收**:三组夹具都能画;图层开关逐层生效;`terrain=null` 时降级不报错。

### F3 生产页 + 投影图

**目标**:planner 已有数据的最大化利用;替掉 `run_sim.py` 的文本输出。
**改动**:队列视图(队首阻塞 + block_reason 高亮、拖拽排序产生 patch 意图);uPlot 双 y 轴矿/气 + 供给;
Gantt 由 `projection.events` 的 started/completed 展开;`stalled` 点标红;目录选择器按 `prerequisites` 置灰。
**测试**:events → Gantt 泳道映射;stalled 点位置;前置置灰规则来自 catalog 而非硬编码。
**验收**:阻塞夹具上,队首阻塞原因、后续项"未处理"、投影 stalled 点三者一致。

### F4 Flow 状态图(只读)

**目标**:看得见"现在在哪个 step、为什么转过来"。
**改动**:ELK 布局 + React Flow 渲染 steps/edges;当前节点旋转进度;边悬停显示 `reason`;
转移历史链(`ADVANCE#3 ← COMBAT#2`);`transition_count/limit` 进度;`exit_record` 终态卡。
**测试**:有环图布局不崩;`branch_hit` 高亮正确;`done` 态渲染。
**验收**:蛙跳夹具上能看出环、当前 attempt、最近转移原因。

### F5 调试页

**目标**:把 4 个一次性探针脚本(`run_addon_probe`/`run_bare_addon`/`run_slot_scan`/`run_barracks_open_scan`)的信息变成常驻视图。
**改动**:命令流水表(筛 origin/action/落地状态);摆放调试叠加层;原始帧检查器(允许显示 burnysc2 名);掉项审计表。
**测试**:`apply=null`(B9 前)时表格显示"未知"而不是空白;叠加层在无 terrain 时仍可用。
**验收**:能从流水表定位到"发了但没落地"的 op 并在地图上看到它的意图落点。

### F6 概览页组装 / F7 对话栏 / F8 接 live / F9 规划页

按 §5、§6 实现;各自验收 = 对应页面的老 ADR 验收标准(0022 §验收 / 0023 §验收)在夹具或 live 上过。
F9 的 AST 编辑器**必须**以 `static/schema` 为唯一 schema 源:非法连线直接禁止,
`unimplemented_*` 的 op 置灰并显示后端给的拒绝原因。

---

## 9. 全局红线(每个任务都受约束)

1. 组件只吃 `contract/` 类型;帧里没有的字段不许现算(C7)。
2. 零规则复算:footprint/滞回/阻塞原因/警报/分组/聚类一律后端给(U4/C2/C3)。
3. 位置可插值;进度条与计数器**绝不**插值(U5,ADR-0017)。
4. zh 文案来自后端数据,前端无 i18n 字典(U6/C4)。
5. 任何组件不得假设帧源是 live(U1);不得 import FrameSource 实现。
6. 列表形状不假设长度 1(C6)。
7. 离线草稿绝不默认叠加 live(ADR-0022 反例)。
8. live 中不出现模块/Strategy 的创建与编辑入口(R5)。
9. 契约改动 = `rev`+1 + §2 修订 + `plan-backend-view.md` 同步。

## 10. 不做清单(防范围蔓延)

- 可拖拽 dock 布局与布局持久化(V1 固定 + 折叠状态;违 ADR-0023 §2.1,已记账)
- 表达式字符串 DSL 的文本编辑器 / 语法高亮(AST 直编,定 D12)
- 鉴权、多用户、远程部署、HTTPS(localhost 单用户)
- 任意帧平滑回放(V1 只跳快照/事件点)
- WebGL / Pixi / 2.5D 抛物线路径(ADR-0023 已列为后续)
- 前端 i18n 框架 / 主题系统 / 移动端适配
- 前端自算敌方聚类、警报、可建性、投影
- live 参数热改与模块插拔(等 `live_editable` 回归的 hot-edit 轮)
- ADR-0017 的多实例泳道视图(引擎单实例,等多实例支持)