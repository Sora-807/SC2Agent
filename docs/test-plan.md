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
- ✅ **挂件翻译锁定**（`test_addon_build_uses_generic_build_ability`）：addon 走 catalog `build_ability` 通用能力（BUILD_REACTOR）——真机锁定：`Unit.build()` 静默返回 False（creation_ability 为 None）、per-parent 拼名 `BUILD_REACTOR_BARRACKS` 被接受扣钱却无实体产出。
- 🚧 **真机 op 集成**：`run_ops_check.py`（手动跑）——逐 op 发命令并回读 orders/state 验生效（move/attack_move/hold/stop/patrol/follow + train/build 等矿）；`run_driver_check.py`（形态+move）。
- ⏳ V1 未实现 op 缺口（**清单已锁死并有原因说明**）：unload/use_ability/cancel/morph（待 ability 稳定 ID 目录）、assign_workers（待生产运行时 WorkerAllocator 展开）。
- ⏳ CommandPolicy 表（per action 持续性/重发/清空）：move ✅（spike）；attack_move/hold/follow/patrol/stop 待续 spike。
- ⏳ 用户接管识别（order 从 auto 变用户命令）：续 spike。
- ⏳ 建筑坐标 size 2/3/5（placement grid↔世界坐标）：续 spike。
- ⏳ ApplyResult 字段（D6）/ GameEvent 目录（D7）：待定。

## game（modules/game）
- ✅ 类型构造 + 默认值（Point2/Grid/RawGameState/Unit/GameState/Operation/ActionRequest/QueueItem/Owner）。
- ✅ catalog（`test_catalog.py`）：`terran.json` 加载 + `where(role/capability)` 查询 + 稳定 ID ↔ burnysc2 名双向映射；加载边界校验（stable_id 两段式/role/capability/cost/build_time/重名）。
- ✅ **挂件条目真机锁定**：实体类型 = 父建筑专属（`BARRACKSREACTOR` 等，通用 `REACTOR` 在游戏里不产出实体）；`build_ability` = 通用建造能力名（addon 缺它加载当场报错）；`build_order_name` = 母建筑订单按钮名（在途确认检测用）。

## world（modules/world）
- ✅ 依赖：只 game（V1；dep-check 绿）。
- ✅ RawGameState→GameState 对齐：`alliance+type→Owner`（**按 TYPE 判 neutral**，过滤矿脉/气井/装饰物）、`health→hp`、orders adapt、position/grid 透传。
- ✅ V1 no-op：坐标原点一致（左下）、grid dims=map_size、无规则层（spike + 集成 check 已证）。
- ✅ resource_nodes 抽取：中性资源拆到 `GameState.resources`（矿脉/气井；WorkerAllocator/生产约束用；不进 units）。
- ⏳ 稳定 type_id / ability 映射（留 catalog）。
- ⏳ D11：mechanics.LayerComputer（power/addon）插入后补全（届时 adapt 注入 mechanics）。

## flow（modules/flow，引擎）
- ✅ 依赖：只 game + tactical_map（dep-check 绿）。port duck-typing（不 import driver）。
- ✅ .md parse（YAML/pyyaml）：Strategy/Flow → manifest + assembly。
- ✅ **编译期校验全集**（`test_manifest.py`，spec-003 验收 1-5）：else 必须在最后；do 操作词表（未知 op 拒绝）；谓词词表（未知/未实现谓词在编译期拒绝，不等到运行时）；未声明 group_slot；action 缺必需参数（对照 OP_CATALOG）；exit 之后再有动作；重复 edge；edge 端点；set_variable/set_local 只写已声明变量；param/var 引用存在性；assembly 绑定校验（strategy_ref/slot/group）。
- ✅ **谓词全目录单测**（`test_predicates.py`）：已实现谓词逐一断言（group_count/game_time/strategy_elapsed/step_elapsed/arrived/group_center/distance_between/enemy_count_near/group_hp_ratio/region_center/unit_count/group_center_in_region/enemy_visible_in/has_building + 6 比较符 + and/or/not + const/param/var 节点 + 空 group 语义）；未实现谓词（engaged/under_attack/has_ready_base/timer_elapsed/event_occurred/user_cancel）与空间工具（nearest_units/cluster_centers）**显式清单 + 原因 + 求值期拒绝**。
- ✅ **Allocator 单测**（`test_allocator.py`）：FCFS 补到 target、sticky lease 不重分配、死亡清 lease + 补位、只租 SELF、空 group/未知 group、expand_all 跨兵种。
- ✅ per-frame eval：branches 有序首匹配；else；exit_step 本帧结束、下帧求值新 step；exit 之后剩余 do 项运行时兜底跳过。
- ✅ 动作去重：相同 (slot,type,action_atom,params) 不重发；params 变重发；不同 type 独立去重键（spec-003 §2.1）。
- ✅ 展开：(slot,type)→unit_tags via Allocator.expand；空 group 动作 = no-op。
- ✅ set_variable 写入 + 下一帧 when 经 `{var: name}` 读回。
- ✅ loop_limits.max_step_transitions 有界环兜底（超限 → strategy 结束）。
- ✅ step 转移 + exit_strategy；FakeGamePort 驱动确定性测试（simple_push/bio_push 全战术链/空间谓词/区域谓词/名字解析端到端）。
- ✅ **端到端切片**（`run_flow_slice.py`/`run_flow_arrived.py`，真 SC2）：driver→world→flow→driver，scv_move flow 驱动 SCV 移动到 (50,50)，arrived 空间谓词真机验通。
- ⏳ hot-edit（live_editable）、full spec-006 仲裁、set_local 读节点、start/stop_timer 运行时、event_occurred/user_cancel 通道（GameEvent 目录 D7 后）。
- ⏳ burnysc2 Move order 的 target_world_space_pos 为 None（目标在 proto 别处；非 bug，review 留意）。

## constraint（modules/constraint）
- ✅ 依赖：game（V1；dep-check 绿）；规则来源 = catalog 单点（改 JSON 不改代码）。
- ✅ `check_build/check_train/check_assign_workers → ConstraintResult(ok, reasons)`：资源/供给/前置/放置格点占用（V1 单格近似，footprint 闭区间待 ADR-0027 升级）。
- ✅ 结构化错误（reasons 带资源缺额/前置/供给/占用）。
- ⏳ 与 mechanics 同源（upgrade 目录落地后 research 校验）；placement/overlap 完整 footprint。

## production（modules/production，生产运行时）
- ✅ 依赖：game/constraint/tactical_map（dep-check 绿）；port duck-typing（不 import driver）。
- ✅ **队首 constraint 门控 drain**（`test_runtime.py`）：矿不够阻塞不发、够了再发；队首阻塞按住后续项；train count>1 逐帧排队；被丢弃项不占帧预算。
- ✅ build：placement 解析（PlacementExact→PosMark/BuildSlot、PlacementInRegion→按声明顺序找未被占 slot、全占→阻塞）；缺 placement/未登记名/越界 index → 出队记入 `dropped`（R7 降级，不阻塞整队）。
- ✅ assign_workers 无门控立即消费，展开成 gather/stop 级 op（`test_worker.py`：矿 2/气 3 饱和、矿脉分摊、饱和度从 orders 派生、idle 只解放采集者、catalog role 选工兵）。
- ✅ 队列工具操作：submit/append/prepend/clear/remove/reorder。
- ✅ research/cancel = V1 不支持清单（UNSUPPORTED_QUEUE_OPS，带原因，出队记入 dropped）。
- ✅ `gather` 动作已落地到 OP_CATALOG + driver 翻译 + burnysc2 契约（assign_workers 的落地原子）。
- ✅ **挂件路径**（`test_runtime.py`）：builder = 母建筑（非 SCV）+ 无目标能力（position=None）；确认 = 挂件实体出现在母建筑右下 2×2 的预期报告位（`_expected_addon_reported`：R = TL+(size,0)+(1,1)，裸实验锁定）——并行多条挂件队列也不互认；母建筑右下 2×2 已被挂件占用 → 换下一台；只选完全空闲（无任何订单）的母建筑——真机锁定：带训练订单的兵营挂件命令被拒/同帧冲突静默消失；订单检测走 catalog `build_order_name`（订单按钮名如 Reactor 与实体类型名 BARRACKSREACTOR 不同）；挂件重试 = 重选母建筑重发，只在实际重发时计次（等待帧不计——踩坑：等待帧烧次数 3 帧内误丢弃），上限 6 次防永久阻塞。
- ✅ **训练槽节奏**（`test_runtime.py`）：`_pick_producer` 只选有空槽的产出建筑（挂件双槽 ≈ 2 条订单为满；在建挂件不可训练）——真机锁定（full_flow.log）：SC2 训练队列满时静默拒绝新订单（无反馈、资源不扣但命令作废）。
- ✅ **在途建造按位置确认**（`test_runtime.py`）：实体出现 ≠ 类型计数+1，须匹配预期报告位（锁定公式：奇数尺寸 R=P、偶数尺寸 R=P+0.5；半径 1.5）——真机锁定（full_flow.log）：同类型建筑连续建时晚到实体误确认 → 重选已下单位 → 丢补给站 → 供给卡死。
- ✅ **在途放置位预留**（`test_runtime.py`）：命令已发、实体未出现的 slot/气井不能被别的项重选（`_resolve_placement` 并入在途 footprint；`_pick_free_geyser` 跳过在途气井）——SC2 对重复下单位静默拒绝。
- ✅ **同帧跨队列去重**（`test_runtime.py`）：每帧 `_frame_busy` 记录已命令单位，`_pick_builder/_pick_producer/_pick_parent_for_addon` 与 WorkerAllocator(skip) 跳过——真机锁定：burnysc2 同帧同单位命令被去重丢单（建造工兵被 steward gather 抢走）。
- ✅ 真机集成 `run_production_check.py`：**固定位置顺序摆放**——depot1/depot2/barracks1 实体落位 ≈ 模板校准点（dist≤0.75，兵营 dist=0.00）、每个 build 只发一次零重试、train SCV 8→11、idle/mineral 采集数往返、dropped=0；bl/tr 两出生点变体自动选择均验证。
- ✅ 真机集成 `run_full_flow.py`（**engine_done=True 全链打通**，trace 见 docs/full_flow.log）：补给站×8→精炼厂×2→兵营×4→反应堆×4（4 条独立队列并行，t≈295 全部挂上）→枪兵到 50（t≈426 formup→advance）→attack_move 敌方主矿→arrived→exit（t≈536 done=True）；dropped=0。
- ✅ 演示粘合（真机教训驱动）：枪兵维持（macro 排空 + 每台兵营挂上反应堆后按空槽补训，<50 且有空槽才补，formup 后停补——advance 后新兵拖拽组心让 arrived 永不成立）；补给维持（macro 排空后被打掉的补给站补建——丢 1 个补给站 = 供给 69 卡死第 50 枪兵）；与 macro 抢位的教训（补给补建必须等 macro 排空，否则多建到 12 个）。
- ✅ **挂件真机锁定**（`run_bare_addon.py` + `docs/bare_addon.log`；与参考工程 C:/dev/project/sc2_agent 的写法交叉验证一致——其用母建筑 + BARRACKSREACTOR 等父专属类型的 creation_ability + has_add_on 守卫）：通用 `BUILD_REACTOR` 订单一帧内产 `BARRACKSREACTOR` 实体、常驻 ~36 游戏秒（= build_time）后完工；贴附格点 = 母建筑右下 2×2（报告位置 (120.0,110.0) vs 兵营 (117.5,110.5)，TL=floor(R-1) 反推吻合）。

## planner（modules/planner）
- ⏳ 依赖：constraint/mechanics/game（**不依赖 flow 运行期**，dep-check）。
- ⏳ `(GameState, queue)→投影曲线`；确定性（同输入同输出）。
- ⏳ 缺字段 → `INCOMPLETE_INITIAL_STATE`。
- ⏳ 不模拟战斗。

## tactical_map（modules/tactical_map）
- ✅ 依赖：game only（dep-check 绿）。
- ✅ 区域模型（ADR-0029）：双层分区 + 加载校验 + `region_at/contains/anchor`（`test_region.py`）；PNG 调色板格点层（`test_pnggrid.py`）。
- ✅ 空间查询：`center_of_units/distance/units_within/nearest`（`test_spatial.py`）；目标解析 resolver（`test_resolver.py`）。
- ✅ **主基建造模板**（`base.py` + `data/ladder_map/base_layout.yaml`，`test_base.py`）：按出生点分变体（bl/tr）+ 平移实例化 + **固定有序建造位**（真机 can_place 扫描校准，`run_slot_scan.py`；BL 主矿左上 4×4 补给站网格 + 兵营×2，TR 镜像）。
- ✅ BuildSlot：TL+BR+size（ADR-0027）+ 校准世界点 pos（偶数尺寸建筑半格，真机验证）；生产运行时按 catalog footprint size 过滤 slot（兵营不落补给站位）。
- ⏳ seq 缓存、nearest_units/cluster_centers（spec-003 §4.4 未实现清单）、完整 footprint 闭区间重叠。
- ⏳ 全图区域 authoring（大区/leaf PNG；观察层区域化摘要用）。

## mechanics（modules/mechanics）
- ⏳ 依赖：game only（dep-check）。
- ⏳ 三族规则：造价/建造时间/前置（Terran V1 实；Protoss/Zerg 夹具锁接口）。
- ⏳ LayerComputer（power/addon）D11。
- ⏳ 在线/离线同结果。

## E2E（跨模块，后置）
- ⏳ 离线：速开二矿 → 两船兵生产（planner 投影）。
- ⏳ 离线：bio_push 脚本化 GameState（flow 走 formup→advance→combat→retreat）。
- ⏳ live 冒烟：FakeGamePort/真 driver 跑一份手写 flow → 验 op 生效。
