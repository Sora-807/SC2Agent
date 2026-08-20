# 待修复问题清单 —— flow 引擎 × 生产运行时

> 来源：`plan-strategy-dsl-v02` T1-T5 合并后的一次系统梳理（2026-08）。
> 每条都有**离线复现证据**（临时探针跑过，探针未入库）；标 ✅ 的已在本轮修掉。
> 与前后端计划（`plan-backend-view.md` / `plan-frontend.md`）的文件归属见 §0，避免并行改同一文件。

## 0. 并行改动的文件归属（与前后端计划的握手）

当前工作树里前后端那条线只有 `web/` 与两份计划文档（未跟踪），`modules/` 零改动 ——
也就是 **B0（`modules/view`）还没开工，B1（`snapshot()` 读模型）也没开工**。
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

### F2（高）`set_local` 只能写、无法读
证据：`predicates.py` 没有 `{local: name}` 节点；`engine.py` 的 `set_local` 写进 `self._locals`，零读取路径。
这与 D8 拒绝 timer 的理由**一字不差**（写被允许 / 读被拒绝 = 静默无效）。
修法二选一：实现 `{local: name}`（+ 校验必须在本 step 的 `locals` 声明里），或把 `set_local`/`locals`
一起移入 `UNIMPLEMENTED_DO_OPS`。建议后者（V1 没有真实用例），T8 做 timer 时一起放回。

### F3（高）step / branch 级键名打错 → 静默改变语义
复现：
- `branchs:`（拼错 `branches`）→ **编译通过**，该 step 每帧什么都不做，永远。
- `wehn:`（拼错 `when`）→ **编译通过**，条件被丢掉 → 变无条件分支，第一帧就 `exit_strategy`。
修法：step 键白名单 `{step_id, branches, locals}`、branch 键白名单 `{when, do}`（与顶层键白名单同一套机制，~10 行）。
LLM 产脚本时这是高频错误面。

### F4（高）instance `params` 键名打错 → 静默用 default
复现：声明 `radius`，实例写 `params: {radus: 3.0}` → `validate_assembly` 通过，引擎 params =
`{radius: 8.0, radus: 3.0}`，覆盖意图丢失。
修法：`si.params` 键必须 ⊆ 声明的 params；顺带按声明 type 做值检查（point 要 `[x,y]`、int/float 要数值）——
即 T2c #10 剩下的部分。

### F5（高）绑定 / 兵种 / 点位名三处静默 no-op
复现（三条同时通过校验，运行期全部 no-op，`eval_diagnostics` 空）：
1. 声明 `group_slots: [inf, armor]` 但只绑定 `inf` → armor 的所有动作永久 no-op。
2. `group_action(slot=inf, type=terran/siegetank)` 而 inf 绑的组 composition 里只有枪兵 → 永久 no-op。
3. 点位名拼错（`hoem`）→ `resolver` 明文"原样保留 → driver 静默失败"（`resolver.py` docstring 自己承认"编译期校验后补"）。
修法：`validate_assembly` 补两条（每个声明 slot 必须有绑定；每个 `(slot,type)` 必须在绑定组的 composition 里）；
`FlowEngine.__init__` 已经拿到 `region_layer`，构造期即可全量校验字面量点位名/区域名（R6：提交必须 validate）。

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
2. **编译期五道口子**：F3（step/branch 键白名单）+ F4（instance params 键/类型）+ F5（绑定/兵种/点位名）。
   全部是"信息已经在手里、只是没查"，且正是 LLM 产脚本的高频错误面。
3. **生产语义止血**：P1（train 前置）+ P3（全局帧账本）—— 这两条决定"发出去的单会不会被 SC2 静默吃掉"。
4. **写-读不对称收口**：F2（`set_local`）+ P9（`assign_workers` 蒸发）+ P2（clear 不清 flight）。
5. **经济维持器**（P14 + P7/P11）：这是"SCV 采矿做得不好"的根治。
6. **可观测性收口**：P5（ApplyResult 回流）+ P6（死 flight）+ P13（事件结构化）+ F11（环形缓冲）——
   与 B1 读模型天然合流，建议与前后端那条线一起排。
7. 最后才是 T8（timer）/ T9（威胁谓词）这类加功能。