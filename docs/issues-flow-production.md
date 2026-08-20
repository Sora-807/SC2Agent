# 待修复问题清单 —— flow 引擎 × 生产运行时

> 来源：`plan-strategy-dsl-v02` T1-T5 合并后的一次系统梳理（2026-08）。
> 每条都有**离线复现证据**（临时探针跑过，探针未入库）；标 ✅ 的已在本轮修掉。
> 与前后端计划（`plan-backend-view.md` / `plan-frontend.md`）的文件归属见 §0，避免并行改同一文件。

## 0. 并行改动的文件归属（与前后端计划的握手）

**状态更新（本轮末）**：前后端那条线**已开工** —— 工作树里出现了未跟踪的 `modules/view/`、`tests/view/`、
`tools/`，并且 `tests/architecture/test_imports.py` 被改动（未提交，B0 加分层规则）。所以下面这张表从"预案"变成
"现行约定"：`production/runtime.py` 的改动**必须先和那条线打招呼**。
（另：他们的 `tests/view/test_statics.py` 当前 3 条红，原因在他们自己那边 ——
`spawn_layout_nearest()` 返回单个 `SpawnLayout`，测试按 2 元组解包。与本清单的改动无关。）
`plan-backend-view.md` §2 的冲突矩阵把 B1 排在"T3 合并后"，T3 已合并，所以 B1 随时可能进来。

| 文件 | 本清单要动 | 前后端计划要动 | 约定 |
|---|---|---|---|
| `modules/flow/manifest.py` | F3/F4/F5 编译期校验（纯增量） | 不需要（只读 `PREDICATE_SIGNATURES`） | **本清单独占** |
| `modules/flow/predicates.py` | F2（`{local}`）、F9 | 只读导出 | **本清单独占** |
| `modules/flow/engine.py` | F1 ✅/F6 ✅/F7/F8 ✅/F12 | B1 `snapshot()` | 小步提交，谁先谁快；B1 的读模型应把 `exit_record`/`eval_diagnostics` 直接包出去 |
| `modules/flow/allocator.py` | 无（F1 改在 engine 侧） | B1 `snapshot()` | 前后端计划优先 |
| `modules/production/runtime.py` | P1-P6/P9/P12/P13 | B1 `snapshot()`、B6 命令写入面 | **需要先约定**：建议本清单先做 P1/P3（门控与账本，核心语义），B1 的读模型后做 |
| `modules/production/worker.py` | P7/P11/P14（经济维持器） | 不在范围 | **本清单独占** |
| `modules/constraint/checks.py` | P1（`check_train` 前置） | 不在范围 | **本清单独占** |
| `modules/game/production.py` | ✅（`QueueItem.when` 已删） | 只读 | 已完成 |

---

## 1. flow 引擎

### ✅ F1（阻断，已修 `27b8426`）补兵后的新兵永远收不到命令

证据：去重键是 `(slot, type, atom) + params`，且在 `Allocator.expand` 之前判定 → lease 集合变化不可见。
复现：2 兵下令 → 补到 4 兵 → 无新 op，`102/103` 永远待命。变体：首次求值时组是空的会**先写键再判空**，
导致之后有兵也永远不发。
真机表现：`garrison` 用固定 `garrison_pos`，后造出来的枪兵站在兵营不动（tank 样例的动态前沿点掩盖了它）。
修法：先展开 tags（空组 no-op 且不写键），去重签名 = `(unit_tags, params_key)`。

### ✅ F6（中，已修）`start_timer/stop_timer` 死分支
T2/D8 已把它们改成编译期拒绝，`_exec_do` 里的 `pass` 分支不可达且注释过期。已删，由 `else: raise` 兜底。

### ✅ F8（中，已修）`exit_step` 无匹配边时运行期静默 return
编译期已拦，但热改/手构造 manifest 会静默卡在原 step。现在记一条 `eval_diagnostics`。

### ✅ F3（已修 `c6efb79`）step / branch 级键名打错 → 静默改变语义
（step 键白名单 `{step_id, branches, locals}`、branch 键白名单 `{when, do}`、locals 必须是字符串列表。）

### ✅ F4（已修 `c6efb79`）声明与覆盖的类型
（params/variables 同一套形态校验、default 按 type 校验、instance params 键必须已声明且按 type 校验值。）

### ✅ F5（已修 `c6efb79`）绑定 / 兵种 / 地图名三处静默 no-op
（声明 slot 必须有绑定；`(slot,type)` 必须在绑定组 composition 里；新增 `validate_map_names(m, layer)`
在 `FlowEngine` 构造期校验字面量点位名/区域名，区域名参数要求真是区域 —— 点位名当区域名用会静默恒 False。）

### F2（高，未修）`set_local` 只能写、无法读
证据：`predicates.py` 没有 `{local: name}` 节点；`engine.py` 的 `set_local` 写进 `self._locals`，零读取路径。
这与 D8 拒绝 timer 的理由**一字不差**（写被允许 / 读被拒绝 = 静默无效）。
修法二选一：实现 `{local: name}`（+ 校验必须在本 step 的 `locals` 声明里），或把 `set_local`/`locals`
一起移入 `UNIMPLEMENTED_DO_OPS`。建议后者（V1 没有真实用例），T8 做 timer 时一起放回。

### F7（中）`_step_entry_count` 只写不读
ADR-0021 的 attempt 概念本该用它（UI 显示 "attempt 3"）。要么进 B1 读模型，要么删。

### F9（中）`variables` 声明不校验
`params` 有键/type 白名单，`variables` 没有；`set_variable` 也无类型检查。一致性问题。

### F11（低）`dropped` / `stalls` / `eval_diagnostics` 无上限增长
长局会一直累积。建议 ring buffer（保留最近 N 条 + 总计数）。

### F12（语义待决，不是 bug）`_last_emitted` 跨 step 重入不清除
回到 `tank_hop` 时若量化后的组心没变就不重发 `move_to`，但期间坦克可能已被 `siege_gate` 架起。
ADR-0021 的 attempt 语义倾向"重入 = 新一次尝试 → 重新下令"。改法是 step 转移时清 `_last_emitted`（1 行），
会改动 tank 样例的 op 计数断言 —— **需要人拍**。

### F10（明确不要动）性能不是瓶颈
实测 `tank_marine_push` 全链 300 帧平均：24 单位 0.066 ms / 150 单位 0.103 ms / 300 单位 0.150 ms，预算 2 ms。
`_group_units` 每次谓词调用全扫 `gs.units` 的 O(units×calls) 在这个规模无所谓 —— 不要为它加索引。

---

## 2. 生产运行时

### P1（高）`check_train` 不查前置 → 订单永久蒸发
`constraint/checks.py` 的 `check_train` 只查"类型存在 + 资源 + 供给 + 就绪产出建筑"，**没查 prerequisites**。
工厂就绪但无 techlab 时 `check_train("terran/siegetank") → ok=True` → 发单 → SC2 静默拒 → 队首被消费 →
坦克订单永久消失。修法：`check_train` 调 `check_prerequisites`；配合 P5（ApplyResult 回流）才能真正闭环。

### P2（高）队列工具操作不清在途 flights
`clear()`/`remove()` 只动 `_queues`，`_build_flights` 里的在途建造照常 retry 并重新发令（实测清队后 91 帧仍在重试）。
对 agent 来说"取消"是假的。修法：队列工具同时按 queue_name 撤掉 flights，并记一条结构化事件。

### P3（高）资源账本是 per-queue 的，且 TRAIN 完全不入账
`pending_min/pending_gas` 是 `_drain` 的局部变量（每条队列各自一份），且只有 BUILD 分支记账。
实测 60 矿 + 两台兵营同帧发出两条 50 矿 train，第二条靠 SC2 静默拒单兜底。
修法：把本帧账本提到 `on_game_state` 级（跨队列共享）+ TRAIN/挂件/气矿一起记账。

### P4（高）气矿重试路径是坏的
第一次 `build_gas` 失败后，`_retry_build` 走 `_resolve_placement`，而气矿没有 placement → 直接 drop 精炼厂订单
（即使还有空闲气井）。修法：重试按 `capabilities` 分派到 `_try_build_gas` 的选井逻辑（与 addon 已有的分支对称）。

### P5（高）`ApplyResult` 被丢弃
`_emit` 不接 `port.submit_operations` 的返回值；driver 恒返回 `ok=True`。翻译失败/静默拒单永远回不到 runtime，
P1/P3 的"SC2 静默拒"就永远看不见。修法：接住返回值 → `failed_op_ids` 进结构化事件（对应需求文档 D6）。

### P6（中）死 flight 完全隐形
flight 永久 `waiting` 时无原因、无失速告警（H1 的 `stalls` 只覆盖队首门控，不覆盖在途建造）。
修法：flight 也接入 `stalls`（超时/重试次数/原因）。

### P9（中）`assign_workers` 零产出时意图静默蒸发
复现：`assign_workers(gas, 3)` 而场上无精炼厂 → 无 op、直接出队、`dropped`/`blocked`/`stalls` 全空。
`run_tank_marine_push.py` 的 macro 队列正是 `build refinery → assign_workers(gas,3)`，而 build 在**发出命令时**
就出队（不是建完），所以 assign 会在精炼厂还在施工时执行 → 意图消失。目前靠 `steward` 队列每隔几秒重分配兜住。
修法：0 分配且 count>0 → 走 `_block("无可用气矿槽")` 而不是出队（符合作者把 assign 排在 refinery 之后的意图，
卡住也会被失速告警照出来）。

### P7 / P8 / P10 / P12 / P13（中）
- P7：`WorkerAllocator._idle` 忽略 `skip`（同帧可能与其他队列命令同一 SCV → burnysc2 去重丢单）。
- P8：addon 重试不排除已试过的母建筑 → 可能反复撞同一台。
- P10：气井预留把挂件 flight 当精炼厂（`expect_pos` 不区分类型）→ 可能误占。
- P12：队列工具无 `count ≥ 1` / `reorder` 不变量校验（reorder 可以塞进不属于该队列的项）。
- P13：`stalls`/`dropped` 没有队列归属与"已解决"事件 → UI/agent 只能看到累积列表，看不出当前状态。

### P11（中，三族红线）`WorkerAllocator` 硬编码 `REFINERY` / `MINERALFIELD`
`_nodes` 直接写死 burnysc2 名，与"catalog 是词汇唯一权威源"冲突（虫族 extractor / 神族 assimilator 会失效）。
修法：走 catalog `capability="gas"` / 资源节点类型表。

### P14（高，架构）SCV 采矿没有常驻维持器
`WorkerAllocator.assign` 是**一次性 delta**：算完当时的差额就返回，没有维持循环。后果：
- 新造的 SCV 不会自动去采矿（没人再下 `assign_workers` 就一直空闲）；
- 矿采空后（节点从 `gs.resources` 消失）它的工兵永久闲置；
- `_pick_worker` 在没有 idle 工兵时会 `rest[0]` —— 可能抢走正在建造的 SCV。
`run_tank_marine_push.py` / `run_full_flow.py` 的 `steward` 队列就是**脚本里手写的维持循环**（业务规则跑到了
编排层）。修法见 §3。

---

## 3. 设计提案：经济维持器（回答"SCV 采矿怎么做好"）

把经济从"一次性队列项"升级为**常驻收敛器**，与 flow 引擎同构（每帧幂等求值 + 只发差量）：

```
EconomyKeeper.on_game_state(gs)          # 每帧（或每 N 帧）跑，无内部状态
  目标态 = f(矿脉数, 精炼厂数, 目标气比例, 上限)     # 每矿脉 2、每精炼厂 3
  实际态 = 从 orders 的 target_tag 派生（已有 _saturation，无状态）
  差量   = 目标 - 实际 → 只对差额发 gather/stop
  规则：优先真 idle 工兵；绝不抢正在建造/搬矿返程的；同一 SCV N 帧内最多改一次（防抖）
```

- 这样"新兵自动去采矿""矿采空自动回收""气矿建好自动补 3 个"全部是**收敛的副作用**，不需要任何人下令。
- `assign_workers` 队列项保留为**一次性人工/agent 干预**（并修掉 P9 的静默蒸发）。
- `steward` 那种脚本里的手写维持循环可以删掉 —— 业务规则回到 production 层，脚本只做会话编排（R2 同精神）。
- 与 flow 不冲突：flow 只管战斗组，经济组的工兵不进 flow 的 group（free 池归 flow 的 Allocator 管，
  经济维持器只碰"没被任何 group lease 的工兵" —— 这条边界需要在实现时写进 docstring 并加测试）。

---

## 4. 建议顺序（止血 → 收口 → 加功能）

1. ~~F1 去重键~~ ✅ 已修（唯一会在真机上表现为"兵不动"的阻断 bug）。
2. ~~编译期五道口子（F3/F4/F5）~~ ✅ 已修 `c6efb79`。
3. **生产语义止血**：P1（train 前置）+ P3（全局帧账本）—— 这两条决定"发出去的单会不会被 SC2 静默吃掉"。
4. **写-读不对称收口**：F2（`set_local`）+ P9（`assign_workers` 蒸发）+ P2（clear 不清 flight）。
5. **经济维持器**（P14 + P7/P11）：这是"SCV 采矿做得不好"的根治。
6. **可观测性收口**：P5（ApplyResult 回流）+ P6（死 flight）+ P13（事件结构化）+ F11（环形缓冲）——
   与 B1 读模型天然合流，建议与前后端那条线一起排。
7. 最后才是 T8（timer）/ T9（威胁谓词）这类加功能。

---

## 5. 生产侧完整审查补充（并行子代理只读审查，逐条带行号）

§2 已记的 P1-P14 之外，补齐下面这些（同一份报告，未在上面展开的）：

### P15（中）`_block_reason` 是隐式实例状态
`runtime.py:99-102` `_block()` 写 `self._block_reason`，`_note_block()`（`109-126`）读它 —— 隐藏状态 + 时间耦合。
将来若 Phase 1（在途确认）或非队首路径也调 `_block`，读到的可能是别人留下的原因。
（这是 T4/H1 我自己引入的接缝，欠一次机械重构：改成返回 `(outcome, reason)` 元组，约十几处调用点。）

### P16（低）`constraint/checks.py` 过时注释 + 死 API
- `checks.py:10` docstring 说"footprint 重叠为单格近似，TL+BR 待升级"，但 `occupied_cells`（`64-79`）已实现完整 TL+BR。
- `checks.py:131-133` `check_assign_workers()` 无生产调用方（runtime 自己处理），只有一条测试在断言它 —— 被测试养着的死 API。

### P17（中，结构）五条建造路径重复
`_try_build`(`264-306`) / `_try_build_addon`(`594-618`) / `_try_build_gas`(`653-679`) 三段重复
`check_* → pick → _emit → 手写 flight dict`；`_retry_build`(`376-406`) / `_retry_build_addon`(`408-433`) 再重复一份。
flight dict 的键（item/type/builder/frames/attempted/seen_tags/expect_pos/radius/retries）散落 5 处，拼错要到运行期才炸。
建议：(a) `BuildFlight` dataclass + `_start_flight(...)` 工厂；(b) 统一 outcome 协议 `(status, reason)`（顺带消灭 P15）；
(c) 职责切分（`QueueManager` / `PlacementPlanner` / `FlightTracker` / `TrainPlanner`，runtime 当 facade）——
697 行现在承担全部职责，每次改动都要读懂完整 FSM。(c) 建议与 B1 读模型那轮一起做。

### P18（中，性能护栏缺失）每帧重复全扫
- `occupied_cells`（`checks.py:64-79`）每次全扫 `gs.units`，一帧内被 `check_build`、`_resolve_placement`、
  `_has_addon`（per 母建筑）反复调用 → O((2N+P)·U)。
- `_confirm_build`（`runtime.py:331-342`）每个 flight 各做一次 `_type_entity_tags` 全扫 + 位置匹配再扫一遍；
  10 个并行 flight = 20 次 O(U)。
- `_pick_free_geyser`（`622-651`）每次重建 buildings 列表；`_pick_builder` / `_addon_order_names` / `_worker_names`
  每帧重算静态 frozenset（`__init__` 缓存一次即可）。
现在 U<100 不会破 2 ms，但那是"碰巧够用"而非"测得够用"——**flow 侧我已实测有数（F10），生产侧完全没有性能护栏**。

### P19（中，接口面）读写面缺口（与 B1 读模型直接衔接）
已有 `queue(name)` / `dropped` / `blocked` / `stalls`；缺：
1. `list_queues()` + 队列级快照（head / 长度 / blocked 状态）；
2. `_build_flights` 的公开读模型（类型/预期位置/帧数/最后等待原因/attempted）；
3. `apply_rejects`（ApplyResult 回流，P5）；
4. dropped/stalls 带 queue 归属 + game_time + resolved 事件（P13）；
5. 队列工具操作的输入校验与错误返回（P12）；
6. WorkerAllocator 的"为什么没派 / 为什么只派了 k 个"原因回传（P9）。

### 低危杂项（各一行）
- `runtime.py:333-336` `expect is None` 分支不可达（所有 flight 创建点都设了 `expect_pos`）+ 注释误导。
- `runtime.py:155` 空队列的 `_build_flights[q.name] = []` 不删 key → 历史队列留空列表，读模型噪声。
- `runtime.py:541-543` `_slot_point` 是 `return bs.build_point` 的单行包装，无接缝价值。
- `runtime.py:304/405/432/616/677` 的 `radius`（1.5 / 3.0）是散落魔法数，无来源注释。
- `runtime.py:243-248` `_base_anchor` 直接吃 `big_grid.data[0][0]`（隐式依赖区域层已校验）；
  `region_layer=None` 时 `_pick_free_geyser` 静默退化为**全图选井**（可能选到敌方气井），无告警（R7 要求降级告警）。
- `runtime.py:311-316` `entry is None` 时默认 `size=2`，掩盖未知类型（flight 创建前已校验，属防御性死路径）。
- `worker.py:119-130` `_idle` 只 stop"正在采集"的工兵，背矿返程中的不解放（可能符合直觉但未文档化）。

---

## 6. 可离线补的测试清单（12 条，都能从现有 helper 扩展）

1. `train siegetank` 有工厂无 techlab → 应阻塞（现会 emit，P1）。
2. 同帧两个生产建筑 + 60 矿两条 marine → 只应发一条（现发两条，P3）；跨队列同帧超发同理。
3. 气矿第一次失败后应重试到第二个气井，两井耗尽才 drop（现直接 drop，P4）。
4. `clear()/remove()/reorder()` 后有在途 flight → flight 不应再 retry/发令（P2）。
5. flight 永久等待（无 SCV / 无母建筑 / 气井全占）→ 应记 wait reason + stalls（P6）。
6. addon 重试应排除已试母建筑、试第二台（P8）。
7. IDLE 的 skip 生效：先 build 后 assign(idle) 不 stop 同一 SCV（P7）。
8. `assign_workers` 无节点/无工兵 → 应有 dropped/blocked 原因而非静默（P9）。
9. 在途挂件位置靠近气井 → 不应误占气井（P10）。
10. `reorder` 子集/外来项应拒绝；`count=0` 应拒绝（P12）。
11. `stalls` 恢复事件 / dropped 带队列归属（P13）。
12. 性能护栏：300-500 units + 10 条队列 → 断言单帧 < 2 ms（P18；flow 侧已有实测，生产侧没有）。