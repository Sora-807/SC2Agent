# ADR-0030 经济维持器与工兵所有权（EconomyKeeper × WorkerLease）

- 状态：草案（待确认）
- 范围：SCV/工兵的所有权归属、采矿维持机制、`assign_workers` 语义、生产建造工的取用方式
- 关联：需求文档 v0.1 §1（模块表）/ S3（补兵滞回）/ S11（生产队列）/ R1-R3、ADR-0021（策略图语义）、
  ADR-0029（目标解析）、`docs/P0-影响边界.md`「生产模块安排」、`docs/issues-flow-production.md` P7/P9/P11/P14
- 取代：P0「assign_workers 立即发、无维持规则」中"无维持规则"这一条（改为常驻维持器 + 目标值语义）

## 背景

1. **采矿现在没有维持者。** `WorkerAllocator.assign` 是一次性 delta：算完当时的差额就返回。后果是
   新造的 SCV 不会自动去采矿、矿采空后那些工兵永久闲置、`_pick_worker` 在无 idle 时会 `rest[0]`
   **可能抢走正在建造的 SCV**（issues P14）。`run_tank_marine_push.py` / `run_full_flow.py` 里的
   `steward` 队列就是**脚本里手写的维持循环** —— 业务规则跑到了编排层。
2. **工兵没有所有权模型。** 生产的 `_pick_builder` 从全场挑人，`WorkerAllocator` 从全场挑人，
   flow 的 Allocator 也可能把工兵租进战斗组 —— 三方零共享，"没抢到"纯属巧合。
3. **`assign_workers` 的意图会蒸发。** 精炼厂还在施工时执行 `assign_workers(gas,3)` → 0 分配 → 直接出队，
   `dropped`/`blocked`/`stalls` 全空（issues P9）。目前靠 `steward` 每隔几秒重来兜住。
4. **有人想把采矿写成 flow 策略。** 直觉是对的（配额判断确实是条件-动作），但组级动作与常驻任务这两点
   决定了它不能整体照搬，见 D1 的推导与「反例」。

## 决定

### D1 分层：flow 表达「配额意图」，维持器执行「分配机制」

1. **不用 `group_action` 直接发采矿命令。** `group_action` 的语义是"一条命令打给全组"
   （`engine._emit_group_action` 构造**一个** `Operation`，`unit_tags` = 全组、`params` 同一份，这是 R1 的必然结果）。
   采矿要求逐单位不同目标（1 号去 A 矿、3 号去 B 矿），用 group_action 写 `gather` 的结果是
   **全组挤同一个矿点**。扇出是 `WorkerAllocator` 的职责，`group_action` 刻意不是扇出算子。
2. **不把采矿写成多 step 策略图。** 策略图语义建立在"策略会终止"上（`exit` + 有界环 +
   `max_step_transitions` 兜底，ADR-0021 §4）；常驻经济任务会撞上限，或退化成
   **单 step + 单 else 分支 + 一个动作** —— 那个退化形态里 flow 的包装只剩"一个写条件的地方"。
   而分配本身是匹配问题（工兵 × 矿点，容量矿 2 气 3），需要遍历矿点，而 DSL 刻意没有循环/选择器。
3. **正确的缝：`assign_workers` 作为意图级动作。** 它已经在 `OP_CATALOG` 里（`task` + `count`），
   也已经被标成复合意图（`game.operation.COMPOSITE_ACTIONS`，driver 不直接执行）。
   flow 的 `do` 写它 = **设定配额**，维持器负责扇出。等维持器落地时把 flow 侧的编译期拦截放开。
4. 于是"很多时候战术需要不满采"这类需求，天然落在 flow 的条件-动作上（写配额），
   而"哪个工兵去哪个矿点"永远不进 authoring 面。

### D2 `assign_workers` 统一为**目标值**语义（不是"再派 N 个"）

1. `assign_workers(task, count)` = **维持 task 上有 count 个工兵**（绝对目标，幂等）。
   一次性 delta 语义在每帧重放的维持器里必然累加，所以绝对值是唯一自洽的选择。
2. 生产队列项 `QueueOp.ASSIGN_WORKERS` 的执行 = **写目标 + 立即出队**（无需资源门控）。
   **这顺手消灭 P9**：目标是持久的，精炼厂还没建好时目标就先挂着，建好后维持器自动补满 —— 意图不再蒸发。
3. `task=idle` 的语义 = 维持 count 个空闲备用工兵（给建造/修理/应急），不是"stop 掉 N 个"。
4. 默认策略（没人设过目标时）：
   ```
   mineral_per_patch: 2      # 0..2，不满采就调这个（饱和度是关于矿点的，不是关于人数的）
   gas_per_refinery: 3       # 0..3
   reserve_idle: 0           # 保留的空闲工兵数
   node_radius: 20.0         # 只管主基锚点附近（沿用 worker.NODE_RADIUS 现值，真机校准过）
   ```
   绝对目标（`gas_workers: 6`）存在时优先于 per-node 比例。

### D3 所有权统一在一张 lease 表（Allocator），三方都从它取人

1. **不新建"矿工组/气工组"。** 配额变化频繁（气 0→6→3），而组成员变化要走 Allocator 的
   `composition.target` 与滞回，把配额调整变成 lease 抖动。**任务分配不该表达成成员变更。**
2. **维持器的领地 = "没被任何组租用、也没被征用"的工兵。** 这条边界不需要新机制 ——
   它就是 Allocator 的 lease 表本身。战术要专用工兵（修理小队、前置建造、农民防守）时，
   照常声明一个组 `terran/scv: 6`，Allocator sticky lease 一租，维持器**自动**不再碰这 6 个人；
   组解散后它们回到维持器领地。**这正是"设定一个分组、自动从自由池拉人"该用的地方。**
3. **建造工 = 短期征用（reservation），也记在同一张表里。** 生产运行时在发出 build 命令前征用一个工兵，
   flight 确认/丢弃时释放。于是"抢走正在建造的 SCV"从"碰巧没发生"变成结构性不可能（P14）。
4. **production 不能 import flow**（`tests/architecture/test_imports.py` 锁死）。所以取用走**端口**：
   在 `modules/game/ports.py` 定义 `WorkerPoolPort`（与 `GamePort`/`RuntimeSink` 同一个 duck-typing 惯例），
   由 Allocator 实现，会话装配时注入给生产运行时与维持器：
   ```python
   class WorkerPoolPort(Protocol):
       def unleased_workers(self, gs) -> list[int]: ...      # 维持器领地
       def reserve(self, owner: str, tag: int) -> bool: ...   # 建造征用（owner 如 "production/build#3"）
       def release(self, owner: str) -> None: ...
       def reserved_tags(self) -> frozenset[int]: ...
   ```
5. Allocator 目前由 `FlowEngine` 内部构造，外部拿不到。改为**会话装配构造、注入**
   （`FlowEngine(..., allocator=None)`，缺省仍自建，保持现有测试不动）。
   ⚠️ 这条改 `modules/flow/engine.py`，与并行的 B1 读模型同文件 —— **落地前先协调**。

### D4 维持器形态：每帧幂等收敛，只发差量

1. 位置：`modules/production/economy.py`（复用 `production/worker.py` 的扇出与饱和度派生；
   production 允许 import game/constraint/tactical_map，不 import flow —— 靠 D3 的端口）。
   **不新建 `modules/economy`**：那要改分层表，而分层表此刻在并行那条线手里。
2. 每帧（或每 N 帧）执行：
   ```
   目标态 = f(矿点数, 精炼厂数, 配额)          # 见 D2
   实际态 = 从 orders.target_tag 派生            # 复用 worker._saturation，无内部状态
   差量   = 目标 - 实际 → 只对差额发 gather/stop
   ```
3. 取人优先级：真 idle > 采矿中 > （绝不动）建造征用中 / 背矿返程中。
   背矿返程的工兵被改派会丢一趟矿；建造中被改派会毁掉 flight。
4. **防抖**：同一 tag 在 `WORKER_RETASK_COOLDOWN_FRAMES` 内最多改派一次。初值 22 帧（≈4 游戏秒），
   **待真机校准**（红线：真机数值不信 wiki）。
5. 采空矿自动回收是收敛模型的副作用（节点从 `gs.resources` 消失 → 那些工兵变 idle → 下一帧被重派），
   不需要专门代码 —— 这正是选收敛模型而不是事件驱动的理由。
6. `snapshot()` 暴露：每 task 的目标/实际、每节点饱和度、征用中 tag、上次改派帧、本帧发的差量。
   直接喂 B1 读模型（`plan-backend-view` §3 的 `frame/economy`）。

### D5 V1 不做（写清楚，避免范围蔓延）

- 多基地/长距离矿区调度、跨基地工兵转移（`node_radius` 之外一律不管，与现状一致）；
- 矿点均衡的最优化（近矿/远矿、矿点排序）、MULE、修理、`_idle` 背矿返程语义细化；
- 三族资源节点词表（`worker.py` 现在硬编码 `REFINERY`/`MINERALFIELD`，issues P11）——
  与本 ADR 无关但同文件，建议一起做；
- 工兵防守（农民 pull）：那是战术，走 D3.2 的战术组，不进维持器。

## 反例（明确禁止）

- 用 `group_action` 发 `gather`/`assign_workers` 而没有扇出层（全组挤一个矿点 / driver 静默 no-op）。
- 把采矿写成多 step 策略图（常驻任务不是状态机；会撞 `max_step_transitions` 或退化成单分支空壳）。
- 为"矿工/气工"建常驻分组来表达任务分配（配额抖动变成 lease 抖动）。
- `production` 直接 import `flow`（分层禁止；必须走 `WorkerPoolPort`）。
- 每帧全量重发 `gather`（命令风暴 + 同帧同单位被 burnysc2 去重丢单）。
- 改派正在建造 / 背矿返程的工兵。
- 维持器碰被战斗组租用或被用户接管的工兵（R3：用户优先）。

## 验收标准（全部可离线测）

1. 新造 SCV 在 N 帧内自动进矿 —— 不需要任何人下令（现状：永久闲置）。
2. 矿点采空 → 该矿的工兵在 N 帧内被重派到未饱和矿点（现状：永久闲置）。
3. `assign_workers(gas, 6)` 在精炼厂建好之前设置 → 建好后自动补满 6（现状：意图静默蒸发，P9）。
4. 建造征用期间维持器不改派该工兵；flight 结束后它自动回矿（现状：可能被抢走，P14）。
5. 战术组声明 `terran/scv: 6` → 维持器立即只管剩下的；组解散后这 6 人自动回矿。
6. **稳定态零命令**：连续两帧目标与实际一致 → 不发任何 op（幂等）。
7. `mineral_per_patch: 1` → 每矿只 1 人（不满采可控）。
8. 性能护栏：300 单位 + 10 矿点 + 2 精炼厂，单帧 < 2 ms（issues P18：生产侧目前零性能护栏）。
9. `steward` 那类脚本里的手写维持循环可以删掉，真机行为不变差（T6 真机对照）。

## 落地顺序

1. ✅ `WorkerPoolPort` + Allocator 实现 + 注入（D3.4/D3.5）。协议在 `game/ports.py`；`Allocator` 实现
   `unleased_workers/reserve/release/reserved_tags`，并在 `refresh` 里把**征用中的单位排除出 free 池**
   —— 战斗组抢不走正在盖房子的 SCV（issues P14 的结构性修法）。`FlowEngine(..., allocator=None)` 支持注入。
2. ✅ `production/economy.py` 维持器 + 目标值语义（D2/D4）+ 离线验收 1-8。
   **实现细化**：征用登记独立成 `WorkerReservations` 对象，由会话装配同时交给 Allocator 与生产运行时
   （flow 不 import production，反之亦然）。气优先于矿落在 `_targets`：先扣 `gas` 与 `reserve_idle`，
   剩下的人数才是矿的上限（否则人不够时矿先占满人、气目标永远补不齐）；`reserve_idle` 因此才有真实行为。
3. ✅ 生产运行时改走征用（D3.3）：`_pick_builder` 排除征用中；发 build 即征用，确认/丢弃/换人时释放。
   顺带修 P7（`WorkerAllocator._idle` 忽略 `skip`，会 stop 掉本帧刚派去建造的 SCV）。
   `worker._pick_worker` 的 `rest[0]` 兜底**暂留**：旧的一次性 `assign_workers` 路径还在用它，
   等脚本全部迁到维持器后一起删。
4. 🟡 `assign_workers` 队列项改写目标（D2.2）✅；**flow 侧编译期拦截仍关着**（D1.3 待接线）——
   flow 的 `do` 要发经济意图，还缺一层复合意图路由（把 `assign_workers` 从 Operation 流里截下来交给维持器）。
   在那之前保持编译期拒绝是诚实的：能写但静默失效比写不了更糟。
5. 🟡 删 `steward` ✅（`run_tank_marine_push.py` 的手写采矿维持循环已删，改为每帧 tick 维持器）；
   性能护栏 ✅（离线 300 单位 < 2 ms）。**验收 9 仍需真机对照**。
   `run_full_flow.py` 的 steward 尚未迁移（旧脚本，等 tank 脚本真机验过再动）。

## 真机验证要点（T6 时看）

- tick 日志新增 `eco={目标, 征用, 本帧差量}`：稳定态的「本帧差量」应为 0（幂等），
  只在新兵进场 / 矿采空 / 配额变化时非 0。
- 气工应在第 1 座精炼厂完工后到 3、第 2 座后到 6（macro 队列现在写的是**目标值** 3 → 6，不是各派 3 个）。
- 建造期间被征用的 SCV 不应出现在 gather 命令里；建完立刻回矿。