# 后端计划 —— 观测缝(ViewFrame)+ 服务层 + 会话控制

> 目标:把运行时状态从私有字段变成**显式读模型**,加一层服务把帧送出去;前端(`docs/plan-frontend.md`)因此可以只做渲染。
> 契约:**唯一真相源是 `docs/plan-frontend.md` §2 ViewFrame v0.1**(消费方定义,本计划实现 + contract test 锁死)。
> 基线:全仓库 0 处服务层;`FlowEngine`/`Allocator` 运行时状态全为私有 `_` 字段、零 getter
>       (`ProductionRuntime` 例外:T4 已公开 `blocked`/`dropped`);
>       `SC2GamePort.start()` 阻塞在 `run_game()`、`stop()` 为 no-op、`events()` 恒空、`ApplyResult.failed_op_ids` 无人填;
>       无地形数据(仅 creep/visibility);`planner.ProjectionCurve` 已可直接上图。
> **前置约束已解除:`docs/plan-strategy-dsl-v02.md`(T1-T6)已完成**(至 `a4285d8`)。B1/B3/B4/B9 的门禁全部打开;
>       §2 的冲突矩阵转为历史记录。重构留下两份对本计划有直接价值的产出:
>       ① `modules/flow/vocab.py::dump_vocabulary()` —— 机器可读词表,`static/schema` 直接照搬(见 B0);
>       ② `ProductionRuntime.blocked`/`dropped` —— 队首阻塞可观测性已就绪,B1 范围因此缩小(见 B1)。
> 仓库惯例:每项任务完成后 `uv run python -m pytest tests -q` 全绿,再进下一项。

## 0. 本轮范围

- ✅ `modules/view`:ViewFrame 数据模型 + JSONL 读写 + 夹具生成器(**新文件,零冲突,可立即开工**)
- ✅ 显式读模型:`FlowEngine`/`Allocator`/`ProductionRuntime` 的 `snapshot()`
- ✅ `modules/api`:FastAPI + WS 多 topic + REST 静态面 + JSONL 回放服务
- ✅ 会话控制与进程分离(解 `start()` 阻塞)、地形静态面、命令写入面、提案存储、警报最小版
- ❌ 不做:任何 flow 语义改动(归 DSL 计划)、OP_CATALOG 增删、`Operation` 形态改动、recovery(D10)、
      多策略实例、timer 实现、鉴权/多用户

## 1. 决策记录(每项一句话理由)

| # | 决策 | 理由 |
|---|---|---|
| V1 | 新增 `view` 模块,位于 flow/production/planner **之上**;`api` 在 `view` 之上;两者都不被下层 import | `需求文档-v0.1` §1 模块表缺这两行,补上并写进 `tests/architecture/test_imports.py`,否则依赖方向会烂掉 |
| V2 | 读模型**优先作为 DSL 计划 T2/T3/T4 的补充条款顺手落**,而非事后独立 patch | T2 正在改 `eval_when` 分派、T4 正在改 `_drain` 的 outcome 分支 —— 记录"命中分支/转移/每项 outcome"在那里是 ~10 行,事后落是一次冲突合并 |
| V3 | `view.schema` 只依赖 `game`;把"从引擎取数"隔离在 `view.adapt` | 让 B0 真正零冲突:schema+夹具+contract test 可与 T1-T4 完全并行 |
| V4 | op 流水的 `origin` 用**端口装饰器**记录,不给 `Operation` 加字段 | DSL 不做清单明确"Operation 形态改动"不做;装饰器纯增量且天然知道调用方是谁 |
| V5 | 派生量全部后端算:footprint 矩形、滞回状态、阻塞原因、分组归属、警报 | 前端红线 C2/C3 的另一半;ADR-0027 的坐标换算在 TS 里出现第二份=灾难 |
| V6 | 复盘写盘**新增 `ViewRecorder`,不改 `StateRecorder`** | T6 真机冒烟仍可能用旧 recorder 取证;新旧并存,旧的等 ViewRecorder 站稳后再删 |
| V7 | 进程分离(游戏进程 + api 进程)而非线程 | `run_game()` 自带 asyncio 循环;分离后崩溃互不带走(对齐 S7"崩了重开游戏,保留计划数据"),且 UI 可在没开游戏时活着 |
| V8 | 地形走**一次性静态面**(`MapInfo`),不进每帧 | 地形不变;每帧发 grid 是纯浪费 |
| V9 | `live_editable` 被 DSL-T3 删除 → **live 参数热改本轮不做**,提案里的 `params`/`flow_ast` 只作用于离线草稿 | 没有声明源就没有"哪些参数可热改"的真相;强行做等于再造一个死字段 |
| V10 | 帧一律 stable_id;`burnysc2_name` 只在 `static/catalog` 出现 | 与 DSL-T1"flow 内部一套词汇"同向;避免第二套词汇泄漏到前端 |

---

## 2. 与 `plan-strategy-dsl-v02.md` 的文件级冲突矩阵(历史记录)

> ~~这张表决定任务顺序~~ —— **T1-T6 已完成,所有门禁打开**。保留此表作为记录:它解释了为什么
> B0 当时能与重构并行开工(全新文件零冲突)、B1 必须等待(读模型与 T1-T4 落在同一批文件),
> 以及前端 F0/F1 为何能在重构期间独立完成(夹具优先,对后端零依赖)。

| 文件 | DSL 计划的改动 | 冲突 | 本计划何时可碰 |
|---|---|---|---|
| `modules/flow/engine.py` | T1 catalog 必传、T2 求值改命名参数/defs、T3 删 on_exit | **高** | 只在 T2/T3 内作为补充条款(V2),或 T3 合并后 |
| `modules/flow/allocator.py` | T1 匹配重写、T3 min 滞回 | **高** | 只在 T3 内作为补充条款,或 T3 合并后 |
| `modules/flow/predicates.py` | T1 归一化路径、T2 签名表 | **高** | 不碰(读模型不需要);只**读取** `PREDICATE_SIGNATURES` 导出给 `static/schema` |
| `modules/flow/manifest.py` | T2c 校验补全、T3 删 ActionRequest/on_exit | **高** | T3 合并后 |
| `modules/production/runtime.py` | T4 队首 block + 未知 op 记 dropped | **高** | 只在 T4 内作为补充条款,或 T4 合并后 |
| `modules/game/production.py` | T3 删 `QueueItem.when` | 中 | T3 合并后(读模型只读字段) |
| `docs/tank_marine_push.yaml` / flow 测试 | T5 样例迁移 | 中 | 夹具生成器用它当输入 → T5 之后重生成一次 |
| `modules/driver/*` | **DSL 明确不动** | 零(但真机取证在 T6) | B3/B4 排在 T6 之后,避免干扰真机冒烟 |
| `modules/game/{catalog,state,raw,ports,operation}.py` | 不在范围 | 零 | 随时(只读) |
| `modules/tactical_map/*`、`modules/planner/*` | 不在范围 | 零 | 随时 |
| `modules/view/`、`modules/api/`、`tools/` | 不存在 | **零** | **立即** |
| `tests/architecture/test_imports.py` | T1-T3 不改它的规则,只需保持绿 | 低 | 立即(新增分层规则) |

**推论**:B0 立即开工;B1 建议合入 DSL 的 T2/T3/T4;B2/B8 在 B0 之后即可;B3/B4/B9 排在 T6 之后。

---

## 3. 任务总览与依赖顺序

| 顺序 | 任务 | 依赖 | 冲突 | 产出 |
|---|---|---|---|---|
| **B0** | `view` 模块 + `tools/worldsim.py` + `tools/make_fixtures.py` ✅**已完成** | 无 | 零 | 夹具改由**真引擎**产出 |
| **B1** | 显式读模型 `snapshot()` + `RecordingPort` + flow/production/ops 帧 ✅**已完成** | — | — | flow/组/生产的真实状态 |
| **B2** | `api` 模块:REST 静态面 + 帧源清单 + WS 帧流 + `tools/serve_api.py` ✅**已完成** | B0 | 零 | 前端 `WsFrameSource` 已接通 |
| **B5** | `view.producer.FrameProducer`(唯一产帧路径) + `view.recorder.ViewRecorder` ✅**已完成** | B0 | 零 | 复盘素材;快照锚点已接时间线 |
| **B8** | 警报最小版 `view/alerts.py` ✅**已完成**(节拍缓存 ProjectionMonitor 归 B2) | B0 | 零 | `frame/alerts` |
| **B6** | 命令写入面 + `api.session.OfflineSession` ✅**已完成**(提案接受归 B7) | B2 | 零 | 前端可写;R8 的门落地 |
| **B7** | 提案存储 + validate 网关 | B6 | 低 | 前端 F7 |
| **B3** | 会话控制 + 进程分离(解 `start()` 阻塞,`stop()` 真实现) | **T6** | 零 | 前端 F8 的 live |
| **B4** | `MapInfo` 地形静态面(driver 增量导出 game_info) | **T6** | 零 | 地图页从散点图变地图 |
| **B9** | D6 `ApplyResult` 字段 + D7 `GameEvent` 目录 | **T6**,B3 | 零 | 命令流水的落地状态 |
| **B10** | `ObservationPacket`:给 agent 的帧投影(agent 接缝) | B2 | 零 | agent 读面与 UI 同源 |
| **B11** | `frame/economy` ✅**已实装**(那边落地了 ADR-0030 第 1/3/4a/5 步) | — | 零 | 采矿维持可观测 |

执行顺序:`B0 ✅ → B1 ✅ → B8 ✅ → B2 ✅ → B11 ✅ → B5 ✅ → B6 ✅ → B7 ∥ B10 → B3 → B4 → B9`

### B6 的落地方式与原计划的差异(值得记账)

原计划的 B6 假设"命令打给 live 会话",但真 live 要等 B3(`SC2GamePort.start()` 阻塞在 `run_game()`)。
所以先做 **`api.session.OfflineSession`**:`tools/worldsim.py` 驱动一套**完整真引擎装配**
(flow + production + economy + 帧生产 + 一张 lease 表)。三点收益:
1. 命令写入面现在就能验(而不是等 B3);
2. 提案审批(B7)有一个能真正 apply 的目标;
3. 这套装配与 B3 的 live 会话**同构** —— 届时只把 WorldSim 换成真 driver,api 与前端零改动。

它刻意长得像 `JsonlSource`(`info/statics/latest_at/between`),所以 WS 通道不用改:
前端把帧源切成 `live` 就在看这个会话。live 源在 pump 里**跟随**(新帧一律转发,不受 `rate` 限制;
`rate` 是回放语义)。

**R8 的门**:所有命令必带 `based_on_seq`,落后超 `MAX_STALE_SEQ=5` 返 **409** 并回报当前 seq
(409 而不是 400:这不是请求写错了,而是世界变了)。字段设成**必填**而不是"可选、缺了就跳过检查" ——
后者等于没有这道门。前端把 409/400 的区别显示给用户:前者提示重取最新帧,后者显示后端给的原因。

`remove`/`reorder` 用**下标**而不是对象引用(HTTP 传不了引用),下标是前端在同一帧看到的位置 ——
配合新鲜度门就足够安全。

**踩坑**:`asyncio.create_task` 在 FastAPI 的**同步** endpoint 里没有运行中的 loop(同步 endpoint
跑在线程池)。改成 `async def`;并把自动推进做成显式开关(`?autotick=false` + `POST /api/session/tick`),
否则测试里后台 tick 会和手动 tick 抢,`based_on_seq` 的断言变得不可预测 —— 这个开关顺带成了"单步调试"入口。

**B2 落地要点**:WS 的时间基准是 `game_time`(ADR-0025 §6),控制消息用 `_` 前缀
(`_hello`/`_eof`/`_error`/`_pong`)与契约的 topic 闭集区分,永不撞名;`_hello` **先于任何帧**
送出 `rev`,前端才能在渲染前判契约版本(红线 C8)。服务端 seek 与前端 seek **语义逐字一致**
(每 topic 取 `<= 游标` 的最后一帧),有测试比对两者给出同一份快照。
踩坑记录:`uvicorn` 不自带 WS 实现,少了 `websockets` 依赖时 WS 升级被静默拒绝
(`No supported WebSocket library`)——**pytest 的 TestClient 走 ASGI 直连不受影响**,
所以只有真起服才会暴露。已加进依赖。

### 实施中发现、需要记账的两个缺口

**① ~~planner 与生产运行时的 authoring 面没统一~~ → ✅ 已修（`view.projection` 这条桥）**

修法：`planner.build_order.expand()` **透传裸 `Op`**（3 行），`view.projection.queue_to_ops()`
把 `QueueItem` 一对一翻成 `Build/Train/Research/AssignWorkers`。于是
`frame/projection.source.kind="live_queue"` 有了真值，UI 不用再写"参考计划"（队列空了才退回它）。
翻不了的项（如 `cancel`）进 `skipped` 带原因下发 —— 不显示的话投影会悄悄少算一段。

**顺带修了一处语义分歧**：ADR-0030 D2 把运行时的 `assign_workers` 改成目标值语义，
但 planner 里还是 delta（`st.gas_workers += move`）。投影模型与真实行为不一致会让投影不可信，
而投影不可信等于**警报也不可信**（AlertService 的卡人口/缺前置都从投影来）。
已把 planner 对齐到目标值（含"下调配额把人退回另一池、总人数守恒"的测试）。

原缺口记录（保留）：

`Planner.project(gs, seq, until)` 吃 `ProductionModuleInstance` 列表(`MODULE_REGISTRY`:
`basic_opening`/`factory_chain`/`bio_tank_opening`),而 `ProductionRuntime` 执行 `QueueItem` 队列,
两者没有互转。所以**契约里 `frame/projection.source.kind="live_queue"` 现在填不出真值** ——
夹具诚实地填 `{"kind":"draft","plan_id":"basic_opening"}`。

这正是 DSL v0.2 不做清单里的"生产 authoring 统一(planner module_defs 代码注册 → YAML)"。
它落地前,概览页的"实际 vs 预测"只能是"实际 vs 某个参考计划"。
→ **前端 F3/F6 的文案必须诚实**:标"参考计划"而不是"当前队列",否则是骗人。

**② ~~`assign_workers` 的意图会静默蒸发~~ → ✅ 已修(issues P9)**

那边落地 ADR-0030 D2.2 后,队列项 = **写目标**(绝对值、幂等),配额持久挂着。
表征测试已按约定**翻转**为 `test_assign_workers_writes_a_persistent_quota_p9_fixed`:
`quota` 必须留着(=3)、`target` 允许为 0(没气矿)。夹具里可见完整闭环:开局设 `gas=3`,
精炼厂建好后维持器自动补满到 `gas=3/3`。

**B11 落地时的契约校准(rev 6)**:维持器的 `snapshot()` 给的是**可达目标**(受节点容量与领地人数夹紧),
而持久配额在 `policy` 里。所以帧里 **quota / target / actual 三个数都给** ——
只给 target 的话,"精炼厂没建好时气目标 0"会让用户以为意图又蒸发了,恰好与 P9 的修复相反。
另按实际能产出什么砍掉两项:`emitted` 改成 `emitted_count`(维持器只记条数)、去掉 `retask`
(防抖帧未暴露 —— 不为它改人家刚写的文件;真需要时再谈)。

**B1 实际落地方式(与原计划的差异,值得记账)**:原计划打算把读模型"合入 T2/T3/T4"以省一次冲突合并。
实际 T 系列先完成了,所以独立落;并且刻意把足迹压到最小以便那边继续重构 flow/plan ——
- `flow/engine.py`:+`snapshot()` + 记 `branch_hit`/`transitions`(命中分支与转移原因**事后推不出来**,必须当场记);
  顺带把 `_step_entry_count`(累计,无人读)改成 `_step_entries` 按 step 计数(契约要的是"该 step 第几次进入")。
- `flow/allocator.py`:+`snapshot()`,`refill_state` 判定**与 `refresh` 的分支一一对应**(同一套 floor/cap/need)。
- `production/runtime.py`:+`snapshot()`,**纯派生、零改动 `_drain`** —— 队首门控语义已经决定了队列里
  每项的状态(留在 `q.items` 的要么是被卡的队首、要么本帧没轮到;已发出的已出队或进 `_build_flights`),
  所以不需要在 drain 里埋状态位,也就不会和 drain 的任何重构打架。
- `view/port.py`:`RecordingPort` 装饰器打 origin + 回填 `ApplyResult`,**不给 `Operation` 加字段**。

### agent 模块的边界(为什么只预留、不在本轮设计)

本轮**只固定 agent 的接缝**,共三处,因为它们已被前端计划隐式决定,以后再改是破坏性变更:

| 接缝 | 落点 | 若不现在定的后果 |
|---|---|---|
| 读面 = ViewFrame 的投影 | B10 `ObservationPacket` | agent 会长出第二条从 GameState 直接摘要的路径,和 UI 派生量对不上 |
| 写面 = 同一套命令 API + 提案通道 | B6 / B7 | 会出现 agent 专用后门,UI 看不见 agent 干了什么(违前端 U7/P4) |
| 新鲜度 = `based_on_seq` 强制字段 | B6 | R8 无处落地;事后给所有命令加必填字段是破坏性变更 |

**不在本轮设计**(归后续 `plan-agent.md`):LLM 选型、prompt、`IntentRouter`(ADR-0005)、
工具目录细化、记忆与上下文压缩、思考深度分层。理由:这三样都依赖"命令 API 已稳定 + 有真实提案 diff 可看 +
有一局真机帧数据",现在写等于凭空想象。

**与 ADR-0005 的张力(需在 `plan-agent.md` 中显式处理)**:ADR-0005 把 V1 定为
`live_policy="no_think"` + `IntentRouter`(快速把自然语言变成 patch);而用户已确认的形态是
**重对话栏 + agent 推草稿 + 用户审批**——这是一个会思考、产出提案的 agent,主次关系相对 ADR-0005 已经反转。
两者不互斥(router 可作为 dispatch/look/start/stop 这类低延迟子集的优化),但 ADR-0005 的
"V1 重点 = router" 与 "validate → simulate → commit 三步必过"(需求文档 R6 只要求 validate + compile,
S11 明确队列 op 不走 validate/simulate)都需要在 `plan-agent.md` 里重新定位。

### 与 ADR-0030（经济维持器 × 工兵所有权）的握手

ADR-0030 有两条直接点到本计划,先对齐,免得落地时才发现。

**① `engine.py` 同文件冲突（ADR-0030 D3.5 明确要求"落地前先协调"）**

D3.5 要把 Allocator 改成会话装配构造、注入(`FlowEngine(..., allocator=None)`,缺省仍自建)。
与 B1 的改动**基本不重叠**:B1 动的是观测块(`_branch_hit`/`_transitions`)、`on_game_state` 的分支循环、
`_do_exit_step`、以及末尾新增的 `snapshot()`;D3.5 动的是 `__init__` 签名与 `self._alloc = Allocator(...)` 那一行。

唯一的**语义耦合**:`FlowEngine.snapshot()["groups"]` 来自 allocator 的 `snapshot()`。
→ 注入进来的实现**必须同样提供 `snapshot()`**(给出 composition/current/refill_state/leased_tags)。
已在 `engine._allocator_snapshot()` 里做成**显式报错**,缺了会当场说清楚,不会抛裸 AttributeError。
如果注入的是包装类,请把 `snapshot()` 透传下去。

**② ADR-0030 D4.6 引用了 `frame/economy`,但契约里还没有这个 topic**

原文:"`snapshot()` 暴露：每 task 的目标/实际、每节点饱和度、征用中 tag、上次改派帧、本帧发的差量。
直接喂 B1 读模型(`plan-backend-view` §3 的 `frame/economy`)"。本计划此前没有这一项 —— 现补为 **B11**。
**现在不加进 `TOPICS`**:维持器还不存在,加一个永远为空的 topic 就是死字段(违背仓库"不静默/无死字段"的规矩)。
维持器落地时一并进 `TOPICS` 并 rev+1。

### B11 `frame/economy`（**预留**，等 ADR-0030 的维持器落地）

**目标**:让"采矿维持"这个常驻子系统可观测 —— 它是"SCV 去哪了、为什么闲置"的唯一解释来源。

**payload 形状**(按 D4.6 的 snapshot 清单一对一,前端零派生):

```ts
interface EconomyFrame {
  tasks: {                                   // mineral / gas / idle
    task: "mineral" | "gas" | "idle"
    target: number                           // 目标值语义（D2：绝对值、幂等）
    actual: number                           // 从 orders.target_tag 派生
    source: "explicit" | "default"            // 显式配额 or per-node 默认比例
  }[]
  nodes: {                                   // 每资源节点饱和度
    tag: number
    kind: "mineral" | "geyser"
    workers: number
    capacity: number                         // 矿 2 / 气 3（后端给，前端不硬编码）
    saturated: boolean
  }[]
  reserved: { tag: number; owner: string }[]  // 建造征用中（owner 如 "production/build#3"）
  retask: { tag: number; last_frame: number }[]   // 防抖：上次改派帧
  emitted: { action: string; unit_tags: number[]; params: object }[]   // 本帧发的差量
  quotas: { mineral_per_patch: number; gas_per_refinery: number; reserve_idle: number }
}
```

**为什么单独一个 topic 而不塞进 `frame/production`**:节拍不同(维持器每帧/每 N 帧收敛,生产队列是事件式的)、
面板不同(经济面板 vs 队列面板),而且 D4.5 那句"采空矿自动回收是收敛模型的副作用"这类行为,
只有把目标/实际/差量并排看才解释得清。**验收 6"稳定态零命令"也只有 `emitted` 为空才看得出来。**

**顺带影响(不改契约,但 UI 文案要改)**:`assign_workers` 从"再派 N 个"变成"**维持** N 个"(D2)。
`frame/production` 里该项的渲染要跟着改,否则用户会以为点一次加一批。

## 4. 全局红线

1. 分层依赖(`tests/architecture/test_imports.py` 锁死):`view` 可 import game/tactical_map/mechanics/constraint/planner/flow/production;
   `api` 可 import view;**下层一律不得 import view/api**;driver 仍零业务规则(R2)。
2. 不改 flow 语义。任何 flow 行为变化归 DSL 计划;本计划只**读**与**导出**。
3. OP_CATALOG 只增不改;`Operation` 形态不动(V4)。
4. 不静默:读模型导不出的字段就在 schema 里写 `null` 并注明原因,不许拿默认值糊过去。
5. 派生量在后端算完再出帧(V5);前端红线 C2/C3 的对侧责任在这里。
6. 帧一律 stable_id(V10);`rev` 变更 = 两份计划同步改。
7. 真机相关任务(B3/B4/B9)不得在 T6 真机冒烟完成前落地。

---

## 5. 任务详述

### B0 `view` 模块:schema + JSONL + 夹具生成器(**可立即开工**)

**目标**:前端 F0 有形状正确、确定性的数据;契约有机器可校的定义。

**改动**:
1. `modules/view/schema.py`:按 `plan-frontend.md` §2 写 dataclass —— `Envelope`、`MapStatic`、`CatalogStatic`、
   `SchemaStatic`、`SessionFrame`、`WorldFrame`、`FlowFrame`、`ProductionFrame`、`OpsFrame`、`ProjectionFrame`、`AlertsFrame`。
   **只依赖 `game`**(V3)。`REV = 1` 常量。
2. `modules/view/encode.py`:`to_json(obj) -> dict`(dataclass→dict,枚举取 value,`Point2`→`[x,y]`);
   `grid_to_b64(Grid) -> GridB64`(行主序 uint8 + base64)。
3. `modules/view/jsonl.py`:`write_frames(path, iter[Envelope])` / `read_frames(path) -> iter[Envelope]`。
4. `modules/view/statics.py`:`catalog_static(Catalog)`、`map_static(RegionLayer, spawn)`、`schema_static()`。
   `map_static` 必须**算好** `br`/`build_point`/`reported_position`(用 `BuildSlot` 现成属性,ADR-0027)。
   `schema_static` = **`flow.vocab.dump_vocabulary()` 逐字下发** + 生产/目标解析侧闭集
   (`game.production.QueueOp`/`WorkerTask`、`production.runtime.UNSUPPORTED_QUEUE_OPS`(带原因)、target_kinds)。
   **不得加工、不得重排、不得补默认值** —— 那张表同时喂校验器与 LLM 提示词卡片,前端只是第三个消费方,
   任何加工都会让"UI 画得出、编译不过"重新变成可能。
   > 前端已按契约 rev 2 落地此形状,并有 `web/tests/schema.test.ts` 逐字段比对;
   > 过渡期由 `tools/dump_vocab.py` 导出 JSON 给前端,B0 完成后该脚本与 JSON 一并删除。
5. `tools/make_fixtures.py`:用 `FakeGamePort` 脚本化状态 + 离线 `Planner.project` 生成三组夹具
   (①开局 60s ②生产阻塞:队首缺气 ③蛙跳推进中)→ `web/fixtures/*.jsonl`。**不需要 SC2,确定性**。
6. `tests/architecture/test_imports.py`:新增规则 —— 无模块 import `view`/`api`;`view.schema` 只 import `game`。

**测试**:`tests/view/test_schema.py`(每个 dataclass 往返 to_json + 字段名与 §2 逐一比对的清单测试)、
`test_encode.py`(grid b64 往返、Point2 编码)、`test_fixtures.py`(三组夹具可读、`rev` 正确、字段齐全)。

**验收**:`pytest tests/view -q` 全绿;`web/fixtures/` 三份 JSONL 存在且前端 zod 校验通过;
架构测试证明无人 import view;**全程未触碰任何 T1-T6 涉及的文件**。

### B1 显式读模型(建议合入 DSL 的 T2/T3/T4)

**目标**:运行时状态有显式出口,前端/agent 不伸手读 `_` 字段。

**改动**(按合入点分组):

*随 T2(engine 求值改造)*:
- `FlowEngine`:记录 `_branch_hit = (step_id, branch_id, index)`(命中即写,未命中写 None);
  `_transitions = deque(maxlen=20)` 在 `_do_exit_step` 边路由时追加 `(from,to,kind,reason,at)`。
- `FlowEngine.snapshot() -> FlowRuntimeView`:导出 `instance_id/strategy_ref/version/params/variables/locals/
  definitions/active_step/step_entered_at/step_elapsed/step_entry_count/branch_hit/transitions/
  transition_count/transition_limit/done/exit_record/bindings`。

*随 T3(allocator 滞回)*:
- `Allocator.snapshot() -> list[GroupView]`:`group_id` + `composition{stable_id: {min,target,max,current}}`
  + `refill_state`(**在实现滞回的同一处判定**:满足/滞回区/补兵中/已截断)+ `leased_tags`。
- `center`/`hp_ratio` 不在 Allocator 算,由 `view.adapt` 用 `tactical_map.spatial.center_of_units` join
  GameState 得出(不新增几何实现)。

*生产侧(T4 已完成 → 范围缩小)*:
- ✅ **已有,直接用**:`ProductionRuntime.blocked[queue] = {item, reason, since, frames, warned}`
  (队首阻塞原因 + 起始时间 + 是否已超 `STALL_WARN_SECS=30`)、`dropped: [(item, reason)]`。
  → `ProductionFrame.queues[].blocked` 与 `.dropped` **零新增代码**即可产出(契约 rev 2 已按此形状定)。
- ⏳ **仍需补**:`_drain` 每项 outcome 落到 `_item_status[(queue,index)] = (status, block_reason)`
  (`队首阻塞/未处理/已发出/在途`);`_resolve_placement` 成功时记 `resolved_point`。
- `ProductionRuntime.snapshot() -> ProductionView`:queues(含每项 status/block_reason/resolved_point)
  + `in_flight`(从 `_build_flights` 摊平:stable_id/kind/builder_tag/expect_pos/radius/frames_waited/
  timeout_frames/retries/confirmed)+ `dropped` 全量。

*独立(零冲突,新文件)*:
- `modules/view/port.py` `RecordingPort`:装饰真实 port,`submit_operations` 时按调用方打 `origin`
  (flow/production/worker/user/agent),记录 op 流水环形缓冲(V4);`apply`/`landing` 字段 B9 前留 `null`。
- `modules/view/adapt.py`:`world_frame(gs, groups)`、`flow_frame(engine, alloc, gs)`、
  `production_frame(runtime)`、`ops_frame(recording_port)`、`projection_frame(curve, meta)`。
  `world_frame` 负责:stable_id 翻译 + 变体拆 `form`、`footprint` 矩形(catalog `size` + ADR-0027)、
  `group_id` join、`producing` 从 `orders` 推、`addon` 判定、`resource_state` 饱和度。

**测试**:三个 `snapshot()` 的字段完整性测试;`refill_state` 四态测试(与滞回三区间测试同处);
`_item_status` 与 block 语义一致性测试(队首阻塞时后续项恒 `未处理`);`RecordingPort` origin 归属测试;
`world_frame` 的 footprint 与 `BuildSlot.reported_position` 一致性测试(锁 ADR-0027,防第二份换算)。

**验收**:`grep -rn '\._[a-z]' modules/view modules/api` 无命中(读模型不越界);pytest 全绿;
真机/夹具下 `view.adapt` 产出的帧过 B0 的 contract test。

### B2 `api` 模块

**目标**:帧送得出去,静态面拿得到,复盘不用开游戏。

**改动**:`pyproject.toml` 加 `fastapi`/`uvicorn`(+ `pydantic` 仅用于 api 边界,核心模块继续纯 dataclass);
`modules/api/app.py`:`GET /static/{map,catalog,schema}`、`WS /frames?topics=...`(多 topic 订阅、各自节拍)、
`GET /replays` + `WS /frames?replay=<path>`(JSONL 回放服务,给前端 `WsFrameSource` 提供无游戏测试环境)、
`POST /commands/*`(B6 填充)。节拍调度器统一以 `game_time` 对齐(ADR-0025 §6),不用墙钟定时器。

**测试**:WS 订阅/退订、topic 过滤、`rev` 头;回放服务播完一份夹具的帧序与文件一致;
静态面 JSON 与 B0 contract test 同一断言集。

**验收**:`uvicorn` 起得来;前端把 `FixtureFrameSource` 换成 `WsFrameSource` 指向回放服务,页面表现一致。

### B5 `ViewRecorder`(新增,不改 `StateRecorder`)

**目标**:live 与复盘用**同一 schema**;复盘素材不再是自定义 dict。

**改动**:`modules/view/recorder.py` `ViewRecorder`(RuntimeSink):按 1s 边界把 world/flow/production 帧写 JSONL;
5s 一份"完整快照帧"并在时间线上标记(ADR-0024 §6 的最小版);`grids` 用 `GridB64` 且仅变化时写。
另修 `docs/state_trace.jsonl` 的过期字段(`type_id` → `type_name`)或直接标注废弃。

**测试**:写→读往返一致;grid 变化检测(不变则不写);快照帧间隔正确。
**验收**:一份录制的 JSONL 能被前端 `JsonlFrameSource` 直接播放。

### B8 警报最小版

**目标**:警报只有一个来源(ADR-0007/0022:禁止前端另算一套)。

**改动**:`modules/view/alerts.py`:`ProjectionMonitor`(3s 节拍缓存 `Planner.project` 结果)+
`AlertService`(从 `ProjectionCurve.events` 的 `stalled` + 阈值派生 `supply_block/mineral_float/gas_float/
line_idle/prereq_missing/plan_drift/queue_blocked`;去重 + 冷却;`text_zh` 在这里生成)。

**测试**:同一 stall 连续帧只出一条(去重);冷却窗口;`eta` 计算;`text_zh` 里的类型名取自 catalog。
**验收**:阻塞夹具上,`frame/alerts` 与 `frame/production.head_status` 指向同一原因。

### B6 命令写入面 / B7 提案存储与 validate 网关

**B6 目标**:UI 与 agent 同一入口(前端 U7/P4)。
**改动**:`POST /commands/queue/{submit,append,prepend,clear,remove,reorder}`(转 `ProductionRuntime` 工具 op)、
`/commands/session/{start,stop}`(B3 后真实现)、`/commands/proposal/{accept,reject}`。
每条命令返回 `accepted_seq`,前端据此做"下一 step 生效"的 pending 显示(Operation 下一 step 生效语义)。
**agent 接缝预留(必须现在就有,以后加是破坏性变更)**:每条命令请求体**必带 `based_on_seq`**,
后端在 `based_on_seq` 落后于当前 seq 超过阈值时**拒绝并返回最新 seq**(R8"旧观察不得作为当前行动依据"
的服务端落点;ADR-0009 §3 的失效时机)。UI 与 agent 共用此字段:UI 用当前帧 seq 填,agent 用其
ObservationPacket 的 seq 填。
**红线**:命令必须过与 agent 相同的校验路径;队列 op 轻量不走 validate/compile(S11),flow 提交必须 validate+compile(R6)。

**B7 目标**:提案是一等对象,可存、可校、可审批。
**改动**:`modules/view/proposals.py`:**`Proposal` 字段按 `plan-frontend.md` §6 逐字段实现**
(含 `rationale_zh` 非空校验 —— 前端 P3 要求空理由不可接受、拒绝理由必须回流);存储(JSONL + 内存索引)、`hunks` 逐条接受、
`validation`(队列提案→`constraint` 可行性 + `Planner` 双投影;flow 提案→`manifest.validate_*`)、
`anchor` 过期检测(帧走远/队列变更 → `已失效`)、拒绝理由回流通道。
**验收**:前端 F7 的三种 diff 都有对应 `preview` 数据;`validation.ok=false` 的提案后端拒绝 accept。
**agent 接缝预留**:`Proposal` 是 **agent 的唯一产出面**,不是 UI 专用结构 —— agent 写 flow / 改队列 /
划区域全部经此通道,`author="agent"`。因此 `POST /proposals` 必须对 agent 与 UI 同权开放,
且 `rationale_zh` 对 agent 是强制字段(P3 的拒绝理由回流因此可作为下一轮输入)。

### B3 会话控制 + 进程分离(**T6 之后**)

**目标**:解 `start()` 阻塞;UI 在没开游戏时也活着;游戏崩不带走 api。

**改动**:`modules/api/session.py` `GameSessionController`:游戏子进程 spawn/监控/心跳/崩溃检测;
子进程内跑 driver+engine+runtime+ViewRecorder,帧经管道/本地 socket 送 api 进程;`stop()` 真实现;
`SessionFrame` 状态机(未连接/启动中/对局中/已结束/崩溃)。
**风险**:这是本计划最大的一块,且直接碰真机路径 —— 必须在 T6 真机冒烟拿到证据之后再动。
**验收**:UI 可 start/stop 一局;杀掉游戏子进程,api 与 UI 仍活并显示"崩溃";S7 的"崩了重开、保留计划数据"成立。

### B4 `MapInfo` 地形静态面(**T6 之后**)

**目标**:地图页从散点图变成地图;摆放调试叠加能显示真实可建区。

**改动**:`driver/sc2_adapter.py` **纯增量**:`start()` 时从 `game_info` 抽 `terrain_height`/`pathing_grid`/
`placement_grid`/`map_ramps`/`expansion_locations` 成 `RawMapInfo`;`world` 转 `MapInfo`;
`view.statics.map_static` 填 `terrain`。driver 仍零业务规则(R2):只抽不算。
**验收**:`MapStatic.terrain` 非 null;前端地图页显示地形与可建区;driver 既有真机行为无变化(回归 `run_driver_check`)。

### B9 D6 `ApplyResult` + D7 `GameEvent`(**T6 之后**)

**目标**:命令流水的 `apply`/`landing` 不再恒 `null`;`需求文档-v0.1` 的 D6/D7 定下来。

**改动**:`ApplyResult` 补字段(每 op 的接受/拒绝 + 原因);`GameEvent` 目录定义(建成/训成/死亡/接管/拒单)
+ `SC2GamePort.events(cursor)` 真实现;`RecordingPort` 回填 `apply`/`landing`。
**注**:DSL 不做清单里"跨帧超支根治(依赖 D6)"也解锁于此。
**验收**:命令流水面板能区分"SC2 静默拒单"与"发出但未落地";`train` 有确认路径。

---

### B10 `ObservationPacket`:给 agent 的帧投影(**agent 接缝预留,排在 B2 之后**)

**目标**:agent 的读面与 UI 的读面是**同一条观测路径的两种渲染**,不是两套实现。

**改动**:`modules/view/observe.py`:`observation_packet(frames, sections) -> ObservationPacket`
——从**已有的 ViewFrame** 投影出 LLM 可读的紧凑文本/结构(`seq`/`game_time`/`supersedes`/`sections`,
字段对齐 ADR-0009 §2);`sections` 至少含 当前经济、组状态、队列与队首阻塞原因、最近转移、警报、30s 投影摘要。
zh 名一律取自 `static/catalog`(与 UI 同源,V5/U6)。

**红线**:严禁为 agent 另建一条从 GameState 直接摘要的路径(那就是 ADR-0007 禁止的"第二份派生"的 agent 版);
`ObservationPacket.seq` 必须等于其来源帧的 seq,以便 B6 的 `based_on_seq` 校验闭环。

**不做**:prompt 模板、router、记忆压缩、思考分层 —— 全部归后续 `plan-agent.md`(见本文件 §6 说明)。

---

## 6. 不做清单(防范围蔓延)

- 任何 flow 语义/语法改动(归 `plan-strategy-dsl-v02.md`)
- OP_CATALOG 增删、`Operation` 加字段
- 多策略实例 / ADR-0017 泳道模型(DSL-T2c#6 编译期拒绝多实例)
- live 参数热改与模块插拔(`live_editable` 已删,等 hot-edit 轮 —— V9)
- 崩溃自动恢复(D10)、`FlowRuntimeStateStore`、骨架/派生重校准
- ADR-0024 的完整事件溯源(`flow_events.jsonl` + 版本树 + seek 重放);B5 只做"快照帧 + 时间线标记"最小版
- 鉴权、多用户、远程部署
- `mechanics` 模块实现(空模块,归别的轮次)
- 敌方聚类算法(`enemy_clusters` 暂发 null,前端已降级)
- 把 16 个 `run_*.py` 改成帧生产者(登记 backlog:B2 之后一次性收编,收益是观测路径合一)