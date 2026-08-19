# 测试计划（per-module test suite）

> 每个模块一套专门测试合集。下表列每套的内容供你制定/增删。
> 依据：docs/需求文档-v0.1.md §（红线/场景）+ docs/P0-影响边界.md + docs/driver_spike.md。
> 状态标记：✅ 已建 / 🚧 部分 / ⏳ 待建。

## 通用
- ✅ `tests/architecture/test_imports.py`：依赖方向红线（game 零 sc2；driver 只 game+sc2；flow 不碰 driver/world/planner/mechanics；world 不碰 driver/flow；等）。加新非法 import 会让测试 fail。

## driver（modules/driver）
- ✅ extraction 单测：`extract_raw_unit/order/state`（duck-typed 假 burnysc2 对象）→ 字段/类型对齐。
- ✅ FakeGamePort：脚本驱动 + 捕获 submitted ops + stop 中断（**给 world/flow/engine 测试用，不测 driver 本身**）。
- ✅ **翻译层全量单测**（`test_translate.py`）：OP_CATALOG 每个 action 逐一断言 → burnysc2 方法+参数；目标缺失/空单位/未知 action 的 no-op；`resolve_point` 各形态与非法输入；**清单对齐**——`TRANSLATORS ∪ UNIMPLEMENTED_ACTIONS == OP_CATALOG`（catalog 加 action 不会静默漏掉）。
- ✅ **burnysc2 API 契约测试**（`test_sc2_api_contract.py`）：inspect 真实 `sc2.Unit`——translator 调用的每个方法存在且必需参数个数匹配（升级 burnysc2 版本时当场红，不等真机）。
- ✅ **bot 应用层单测**（`test_bot_apply.py`）：op_queue FIFO drain → `self.do`；命令失败静默跳过且不影响其余 op（R7）；on_step = 抽 state 推 sink → drain 队列。
- 🚧 **真机 op 集成**：`run_ops_check.py`（手动跑）——逐 op 发命令并回读 orders/state 验生效（move/attack_move/hold/stop/patrol/follow + train/build 等矿）；`run_driver_check.py`（形态+move）。
- ⏳ V1 未实现 op 缺口（**清单已锁死并有原因说明**）：unload/use_ability/cancel/morph（待 ability 稳定 ID 目录）、assign_workers（待生产运行时 WorkerAllocator 展开）。
- ⏳ CommandPolicy 表（per action 持续性/重发/清空）：move ✅（spike）；attack_move/hold/follow/patrol/stop 待续 spike。
- ⏳ 用户接管识别（order 从 auto 变用户命令）：续 spike。
- ⏳ 建筑坐标 size 2/3/5（placement grid↔世界坐标）：续 spike。
- ⏳ ApplyResult 字段（D6）/ GameEvent 目录（D7）：待定。

## game（modules/game）
- ✅ 类型构造 + 默认值（Point2/Grid/RawGameState/Unit/GameState/Operation/ActionRequest/QueueItem/Owner）。
- ⏳ catalog：`terran.json` schema 校验；`reg.where(role/capability/kind)` 查询；改 JSON 不改下游。
- ⏳ 稳定 type ID 映射（burnysc2 `SCV`→`terran/scv`）。

## world（modules/world）
- ✅ 依赖：只 game（V1；dep-check 绿）。
- ✅ RawGameState→GameState 对齐：`alliance+type→Owner`（**按 TYPE 判 neutral**，过滤矿脉/气井/装饰物）、`health→hp`、orders adapt、position/grid 透传。
- ✅ V1 no-op：坐标原点一致（左下）、grid dims=map_size、无规则层（spike + 集成 check 已证）。
- ⏳ 稳定 type_id / ability 映射（留 catalog）。
- ⏳ resource_nodes 抽取（给 map 模块：矿脉/气井位置）。
- ⏳ D11：mechanics.LayerComputer（power/addon）插入后补全（届时 adapt 注入 mechanics）。

## flow（modules/flow，引擎）
- ✅ 依赖：只 game（V1；dep-check 绿）。port duck-typing（不 import driver）。
- ✅ .md parse（YAML/pyyaml）：Strategy/Flow → manifest + assembly。
- ✅ 最小 validate：initial_step / edge 端点 / exit_step 匹配 edge。
- ✅ per-frame eval：branches 有序首匹配；else（无 when）；exit_step 本帧结束、下帧求值新 step。
- ✅ 谓词 V1：group_count / game_time / strategy_elapsed / step_elapsed（结构化 AST）。
- ✅ 动作去重：相同 (slot,type,action_atom,params) 不重发（spec-003 §2.1）。
- ✅ 展开：(slot,type)→unit_tags via Allocator.expand。
- ✅ Allocator V1：FCFS+sticky lease、create_group/refresh/count/expand（spec-006 接口预留）。
- ✅ step 转移 + exit_strategy；FakeGamePort 驱动确定性测试（simple_push：formup→advance→exit）。
- ✅ **端到端切片**（`run_flow_slice.py`，真 SC2）：driver→world→flow→driver，scv_move flow 驱动 SCV 移动到 (50,50)（到目标距离单调下降）✓。
- ⏳ 空间谓词（arrived/enemy_count_near/group_center）需 tactical_map（P4）。
- ⏳ 空 group 语义、loop max_iterations 兜底、hot-edit（live_editable）、full spec-006 仲裁、set_local/timer。
- ⏳ compile 全集（未知槽位/原子、环无出口、(slot,type)粒度）。
- ⏳ burnysc2 Move order 的 target_world_space_pos 为 None（目标在 proto 别处；非 bug，review 留意）。

## constraint（modules/constraint）
- ⏳ 依赖：game/mechanics/tactical_map（dep-check）。
- ⏳ `(GameState, action)→bool`：minerals/vespene/supply/prereq/placement/overlap。
- ⏳ 与 mechanics/tactical_map 同源（不另算规则）。
- ⏳ 结构化错误（含原因+位置）。

## planner（modules/planner）
- ⏳ 依赖：constraint/mechanics/game（**不依赖 flow 运行期**，dep-check）。
- ⏳ `(GameState, queue)→投影曲线`；确定性（同输入同输出）。
- ⏳ 缺字段 → `INCOMPLETE_INITIAL_STATE`。
- ⏳ 不模拟战斗。

## tactical_map（modules/tactical_map）
- ⏳ 依赖：game only（dep-check）。
- ⏳ 点位名↔坐标登记。
- ⏳ 空间查询：`region_center/group_center/distance/nearest_units/cluster_centers`。
- ⏳ seq 缓存（同 seq 同结果、新 seq 重算）。
- ⏳ 空 group 语义（count=0、中心谓词 false）。
- ⏳ footprint/overlap（size 2/3/5 placement）。

## mechanics（modules/mechanics）
- ⏳ 依赖：game only（dep-check）。
- ⏳ 三族规则：造价/建造时间/前置（Terran V1 实；Protoss/Zerg 夹具锁接口）。
- ⏳ LayerComputer（power/addon）D11。
- ⏳ 在线/离线同结果。

## E2E（跨模块，后置）
- ⏳ 离线：速开二矿 → 两船兵生产（planner 投影）。
- ⏳ 离线：bio_push 脚本化 GameState（flow 走 formup→advance→combat→retreat）。
- ⏳ live 冒烟：FakeGamePort/真 driver 跑一份手写 flow → 验 op 生效。
