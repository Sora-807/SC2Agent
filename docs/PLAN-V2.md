# PLAN-V2：执行模型 / 地图规划 / 观测面 大重构

2026-08-24 立项。输入：agent 梳理的需求文档（runtime/agent-talk/workspace/
improvement-notes.md I1-I9 + templates/ 三份，**是输入不是规范** —— 字段命名已按
系统词汇表归一，见「命名对齐表」）+ 用户对话拍板（D1-D8 + 两个新增点）。
上一轮修复（§0.54-0.57，915 后端 + 386 前端绿）未提交，作为本计划的前置基线。

## 0. 目标架构（三条主线）

1. **一套执行语义，三个消费方**：队列项分类器 `classify(item, state) →
   ready | pending | skip(reason)` 抽成共享模块 —— live runtime、planner 仿真、
   健康检查全调它。语义改一处，三处同步。
2. **一份图层，三种解析**：会话图层 = 默认规划（裸名）+ 其余规划（`规划id/点位`
   命名空间键）合并装配；默认可热切，显式引用不受切影响。
3. **模板是工具输出层规范，不是帧契约**：observe/simulate 的 markdown 表格约束
   agent 看到的渲染层；ViewFrame 契约保持结构化，前端不因本重构大改（除批 2
   的红蓝适配与批 1 的队列状态展示）。

## 1. 决策记录（D1-D8 + 新增）

| # | 决策 |
|---|---|
| D1 | **B**：删 supply_block 前瞻警报；保留 prereq_missing 家族，覆盖三情形 —— ①前瞻：仿真/健康检查发现前置（含补给）根本没排；②live：供给建筑**被打掉**致无法建造（用 `_ever_ready` 记忆判定「曾有现无」）；③live：科技建筑被打掉同理。每条带 uid 建议（“在 qXX 前插 …”） |
| D2 | audit_queue 保留到批 3 的在线读落地，然后 `simulate_plan(queue_name, horizon=0)` 吸收，audit_queue 退役 |
| D3 | **侦查/修理不是队列任务**：SCV 编入 Group（flow 编组）执行 —— 侦查=移动到点位、修理=跟随大部队。生产队列不表达；仿真输出的 `workers.scouting` = 编入非经济组的 SCV 数（派生显示）。`repair` 操作原不在 OP_CATALOG → 立后续项 |
| D4 | 矿区划分**硬编码进地图基础数据**（每张图一份，含红蓝双方），不在 map-plan、不运行时推导。用户草案表（六矿区）作验收基准 |
| D5 | initial_state 本轮只做仿真侧；live 开局注入延后单独立项 |
| D6 | 分工：语法/作者错误（catalog 不认、YAML 坏、标记不存在）→ dropped 摘除；执行期失败（无位/缺前置/满产）→ 留队列标 skipped + reason |
| D7 | auto_supply 彻底删除（`_supply_guard` + 参数，H 批刚加的开关退役）；替代 = D1 警报 + uid 建议 |
| D8 | **不要 not_reached**：status 四值 `pending / in_progress / completed / skipped`，live 与仿真同枚举；仿真里 horizon 没碰到的项就是 pending |
| 新1 | 地图规划双分支后，**前端 UI 按实际出生端切换**（红/蓝）显示对应侧布局（编辑器 + 驾驶/复盘地图） |
| 新2 | **初始化模块**：发布态默认工作区 —— 系统提示词说明、工作区设计文档、游戏/数据手册（catalog 生成）等作为首次运行自动创建的默认库存（泛化现有 agent/seeds 只补缺失机制） |

待用户输入：**Agent 切换地图的设计文档**（只影响批 2 的 agent 工具面，不阻塞
图层/热切后端）。

## 2. 命名对齐表（agent 文档 → 系统词汇）

| agent 提议 | 采用 | 依据 |
|---|---|---|
| `with_reactor`/`with_techlab` | `reactor`/`techlab` | zAddon 枚举值 |
| `normal_slots`/`tech_slots` | `normal_cap`/`tech_cap` | B15 产槽既有词 |
| “双倍挂件/科技挂件” | “反应堆/科技实验室” | C4：catalog display_name_zh |
| `missing_prereq_building`/`_tech` | `prereq_missing` 家族 + payload 区分 | 警报 kind 词汇表 |
| `workers` 五键 | 键 = WorkerTask 枚举值（mineral/gas/idle + 新增 scouting 派生）；building 单列派生量 | 闭环枚举 + 征用表 |
| status 中文值 | 英 key + STATUS_ZH 映射（等待中/执行中/已完成/已跳过） | REASON_ZH 覆盖层模式 |
| `t/minerals/gas/supply_used/supply_cap` | 原样 | ProjectionPoint 已有 |
| `uid/before_uid/started_at/completed_at` | 原样 | 无冲突；started_at 与 TrainingView 一致 |
| step 参数 | 删除；自动 step（≤14×14 全量，超出降密度并标注）；**14×14 上限保留**（用户批注） | |

## 3. 批次

### 批 1：队列执行模型（地基）
- `QueueItem` + `uid`（提交时分配，per-queue 递增，重排不变）；已执行项**保留标
  status**（四值），不再摘除。
- 共享分类器 `classify`：ready / pending（矿/气/人口未齐、前置在建或在队列）/
  skip（`prereq_missing`｜`placement_collision`｜`production_capacity`）。
- runtime drain 重构：队首 pending → 等待；skip → 标记后**继续下一项**（队首冻结
  退役）；TRAIN 补 flight 级超时语义（对齐 build 的重试→耗尽→skip）。
- dropped/skipped 分工（D6）；skipped 项进警报（D1 情形②③的 live 面）。
- propose hunk：`index` → `before_uid`（契约 + 前端 planning 编辑器）；observe
  生产段按 status 渲染。
- 契约 REV+1；ADR-0032（队列状态机与 skip 语义，取代队首冻结拍板）；
  ADR-0027 修订（placement null → auto，落地在批 2 但语义在本 ADR 一并记）。
- **删除 `_supply_guard` 与 auto_supply**（D7）。

### 批 2：地图规划改版
- 规划文件升级**双分支**（一份 = bl+tr 两套 build_slots/pos_marks；单分支兼容）；
  保存校验/编辑器/`load_map_plan` 跟进。
- 会话图层 = 默认规划裸名合并 + 全部其余规划 `规划id/点位` 命名空间键（同一套
  出生点平移）；`resolve_placement_refs` 的跨规划拒绝逻辑删除。
- placement null = auto（默认图层按序空位；无位 = skip/placement_collision）。
- 默认地图热切：`POST /api/session/map-plan?id=`，帧边界换层（swap_strategy 同款：
  重建图层 → 换 runtime/keeper/engine/producer 的 region_layer → 重发 static/map）；
  start_session 的 map_plan = 初始默认。
- 前端：红蓝端适配（新1）—— 按实际出生端显示对应分支。
- 矿区划分进地图基础数据（D4，批 4 消费，此处先落数据结构）。
- agent 切地图工具面：等用户文档（REST 先行，UI/agent 后接）。

### 批 3：simulate_plan v2（I1+I2+I3+I6 部分）
- 参数：+`sample_interval`/`sample_start`/`initial_state`（字符串引用
  initial-states/ 或内联对象）/`queue_name`（读在线队列）/`from_session`；
  删 `auto_supply`。
- 输出四段（渲染层，按归一后的模板）：曲线采样（workers 分任务 + 产位
  normal_cap/tech_cap 明细）/ 队列执行状态（uid/status/started_at/completed_at/
  reason，取代事件时间线）/ 终值快照（按类型 + 产线明细 + 已完成升级，标注
  近似派生）/ 健康检查（error=stalled/prereq_missing；warn=idle_production/
  resource_float/supply_capped（D1①前瞻）；info=assembly_gap 仅带装配时）。
- `initial-states/` 平级资源（REST + 工作区 + 校验）；`export_snapshot`（I6）：
  从活跃会话导出状态 + 剩余队列（带 uid/status）。
- audit_queue 合并路径打通（D2）。

### 批 4：observe v2（I9）
- 参数：source（live｜地图规划 id｜trace id）、time（复盘帧）、bbox（自动 step）。
- 无 bbox 输出两块：全局状态（资源/工人分任务/建筑汇总含挂件与在建/部队汇总/
  生产序列汇总）+ 区域信息（按基础数据的矿区分区；建筑表 + 部队表含集群、
  血量%/绝对血量、`敌方：` 前缀行；敌方按 D 批 10s 接触窗聚类对齐）。
- 移除：30s 预估、事件时间线段落。
- EnemyClusterView 从 stub 变实现（own+enemy 聚类）。
- 复盘读录像：source=trace id + time → 从 recordings 帧流取态。

### 批 5：数据与初始化
- catalog/ 手册从 game/catalog **生成**（三族 markdown，模板按
  templates/catalog-structure.md 归一后）；matchups 从 strategy-notes 种子化；
  挂工作区只读区。
- `plans/` → `production-plans/`（I5，机械迁移 + 引用方）。
- **初始化模块**（新2）：首运创建默认工作区（系统提示词说明 / 工作区设计文档 /
  游戏·数据手册 / memory 种子），泛化 agent/seeds。
- scouting（D3）：`workers.scouting` = 编入非经济组 SCV 派生；`repair` 操作立项。

### 批 6：收尾
- audit_queue 退役（simulate_plan 吸收）；提示词/seeds/文档同步；
  改名后的写面校验全绿；WORKLOG/ISSUES 收档。

## 4. 依赖与顺序

批 1 → 批 3（分类器/uid 复用）；批 2 → 批 4（矿区数据、图层）；批 2 与批 1 可
并行；批 5 独立可穿插；批 6 收尾。每批后端+前端全绿再进下一批。

## 5. ADR 清单（执行时写）

- ADR-0032：队列状态机（uid/保留/skip 语义/no-cascade）—— 取代队首冻结。
- ADR-0027 修订：placement null = auto。
- ADR-0033：地图规划双分支 + 命名空间引用 + 默认热切。
- ADR-0034：auto_supply 移除（诊断取代掩盖）。

## 6. 测试策略

行为锁先行：分类器纯函数表驱动测试（ready/pending/skip×reason 全矩阵）；
runtime skip-and-continue 与 dropped 分工；propose before_uid（含错位回归）；
双分支装载（bl/tr 各验）+ 热切帧边界；simulate 四段输出快照测试（模板对齐）；
observe v2 段落快照；矿区表与用户草案表对账；catalog 生成幂等。

---

## 7. 执行细节附录（防上下文压缩丢失 —— 2026-08-24 立项时核过代码，逐批落点）

**基线**：提交 `b202068`（§0.51-0.58 全部成果）；测试基线 = 后端 915 passed /
4 skipped、前端 386 passed + tsc 绿；契约 **REV=17**；max_turns=500 / 轮预算
1M token / 活性看门狗**已落地勿重做**（agent/talk.py）。PLAN-ROUND3 的 G 批
（复盘投影截断线）已落地，不在 V2 范围。

**红线速记**：vendor/agentic 不改（接缝=子类/drop/特判挂我方层）；文件契约走
REST 同一入口；REV 每批一次性 +1（不逐字段），契约只增不改（`.default()` 兜底
旧录像）；zh 命名两形态（实体 _zh 兄弟字段；枚举英 key→中文映射表）；中文名
取 catalog display_name_zh（C4）；前端不自算判定（C3）。**已踩过的坑**：
①live.py describe() 持 `_lock` 时派生读取不得再取锁（普通 Lock 不可重入）；
②只读区 `exists()` 必须与 `read()` 同源（vendor read 的 contains 预检会把
WorkspaceError 吞成假 not-found —— maps 和 _lib.yaml 两次事故同款）；
③`produced_by=None` 是**所有建筑**的属性（工兵建造），不能拿它认起始建筑。

### 批 1 落点：队列执行模型

- `game/production.py`：QueueItem +`uid`（默认 None；runtime 入队时分配
  per-queue 递增 `q01…`，重排/插入不变）。
- **新模块** `modules/production/semantics.py`：`classify(item, view) →
  ready | pending(reason) | skip(reason)`。数据视图 view = 资源 + 就绪建筑 +
  在途/队列前序 + 产位占用。消费方三处：runtime._drain、planner 仿真、
  simulate 健康检查。reason 枚举（SKIP_REASON_ZH 映射）：`prereq_missing` /
  `placement_collision`；**production_capacity 建议归 pending**（槽满是瞬态，
  "等一等就满足"；挂升级警报：capacity-pending 超 60s → warn）—— 此点偏离
  agent 文档，执行时向用户确认一次。
- `modules/production/runtime.py`：_drain 重构 —— classify=pending → 队首等待
  （资源类照旧）；skip → 标记 + 继续**下一项**（队首冻结退役）；_note_block/
  blocked 字段随状态机重写。in_progress 判定：TRAIN 用 `_trainings` 账本
  （G3 已建，含 started_at）+ world producing；BUILD 用 `_build_flights`。
  dropped（R7）保留给语法/作者错误（D6）。
- `modules/view/adapt.py` + `web/src/contract/index.ts`：QueueItemView
  +`uid/status/reason`；status 四值 `pending/in_progress/completed/skipped`
  （STATUS_ZH：等待中/执行中/已完成/已跳过）。
- propose/queue 命令改 `before_uid`：`view/proposals.py` hunk payload、
  `api/routes/session.py` queue_op（insert/remove/reorder）、
  `agent/tools.py` propose schema、前端 `planning/QueueTable.tsx` +
  `api/commands.ts`。旧 index 入参一并迁移（同仓前后端，不保双轨）。
- **删除**：planner `_supply_guard`（planner.py ~L111-142 + 调用点 ~L86）、
  `auto_supply` 参数（routes/plans.py simulate + tools.py，H 批产物）。
- D1 警报三情形落点：①前瞻 = simulate 健康检查扫描（skip 项 + prereq 缺口
  vs 终态）；②③live 被打掉 = alerts.py 用 runtime `_ever_ready`（E 批已建）
  判"曾有现无"→ prereq_missing 家族 + uid 建议。新 kind `supply_capped`
  （warn：used≥cap 且队列/在途无供给建筑，建议插 depot before uid）；
  旧 `supply_block` 前瞻警报删除。
- ADR-0032（状态机/skip/no-cascade）+ ADR-0027 修订（null=auto，批 2 落地）。

### 批 2 落点：地图规划改版

- 文件形态：map-plans/<id>.yaml 增 `spawns: {bl: {…}, tr: {…}}` 双分支；
  单分支（`spawn:` + 平铺）兼容 —— `load_map_plan`（tactical_map/base.py
  ~L121）已包一层，双分支直通。保存校验对两分支各跑几何校验。
- **图层合并**（新 helper，建议 tactical_map/merge.py）：默认规划槽位=裸名 +
  其余规划槽位=`规划id/名字` 键（同一 instantiate_spawn 平移，按实际出生点
  pick_spawn_layout 选分支）+ catalog reserved_marks 全局裸名。装配点：
  `api/session.py` OfflineSession ~L155-165；live 子进程 tools/run_session.py
  （_detect_spawn 后）。`view/plans.py resolve_placement_refs`（~L86-114）
  删跨规划拒绝，前缀直通。
- null=auto：`production/placement.py` ~L34 的 null 拒绝改为默认
  in_region("home")；planner 侧无层 —— **仿真需要槽位近似模型**（批 3 依赖）：
  从默认规划槽位集合按声明序消耗（类别/尺寸过滤同 placement.py），
  产出 placement_collision skip。注意：批 2 只落数据通道，仿真近似归批 3。
- 默认热切：`POST /api/session/map-plan?id=`，帧边界挂起 → 重建合并图层 →
  换 runtime/keeper/engine/producer 的 region_layer 引用 → 重发 static/map
  （抄 swap_strategy：api/session.py ~L348 / run_session swap 通道）。
  start_session 的 map_plan = 初始默认。裸名在新默认缺失 → 作者错误 dropped。
- 前端红蓝：编辑器按分支两页签；驾驶/复盘按会话 meta.spawn 显示对应侧。
- 矿区进基础数据：tactical_map/data/ladder_map/ 增 mine_areas
  （name/side/bbox），用户草案：蓝主[38,25,50,40] 蓝二[65,70,80,80]
  中岛[95,90,115,105] 红主[108,64,120,75] 红二[118,115,135,130]
  红三[98,98,108,108] —— **待校准**，作批 4 验收基准。
- agent 切地图工具面：等用户文档（REST 先行）。

### 批 3 落点：simulate_plan v2

- `routes/plans.py plans_simulate`：+`sample_interval`(10)/`sample_start`(0)/
  `initial_state`(str 引用｜object 内联)/`queue_name`(在线队列)/`from_session`。
- ProjectionPoint 已逐秒采样 → 输出层抽取；采样点增字段在渲染时派生：
  workers 分任务（mineral/gas 已有；building=并发在途建造数，idle=总数减，
  scouting=0 占位=编组派生）、产位 normal_cap/tech_cap 从 st.buildings+
  挂件构成推导（bare=1、reactor=2 普通；techlab=1 科技；building_addon=0）。
  近似处如实标注。
- 队列状态表：**uid 穿透 planner Op**（ops_to_items/Build/Train +uid 字段），
  终值映射回 status/started_at/completed_at/reason；事件时间线段落删除。
- initial-states/：runtime/initial-states/<id>.yaml + REST CRUD + 工作区
  虚拟目录（ApiWorkspace _split 加分支，同 plans 模式）；校验：workers 各分项
  和=SCV 总数、catalog 类型存在、supply_cap 与建筑构成一致。
- export_snapshot（agent 工具 + GET /api/session/export）：从 live 帧拼
  initial-state + 剩余队列（带 uid/status）。**缺口**：upgrades 导出需要
  已完成研究账本（runtime research 记账，类比 _trainings）—— 小任务。
- from_session：export 逻辑内联进 simulate 起点。
- 健康检查按归一模板：error=stalled/prereq_missing；warn=idle_production
  （产位空闲>15s）/resource_float/supply_capped；info=assembly_gap 仅带装配。
- audit 合并路径打通：`simulate_plan(queue_name, horizon=0)`=静态体检。

### 批 4 落点：observe v2

- `view/observe.py` 重构为**帧驱动渲染**（输入=ViewFrame 组，live 当前帧与
  录像帧同路）—— 这是 source/time 的前提；recap.py 已有 jsonl 帧解析可复用。
- 无 bbox 两块按模板：全局（资源/工人分任务/建筑汇总含挂件+在建/部队汇总/
  生产序列汇总= _trainings+队列 train 计数）+ 矿区（基础数据 mine_areas；
  建筑表坐标/挂件/正在做什么；部队表集群/血量%/绝对血量，`敌方：` 前缀
  仅当前视野内；历史踪迹仍在 enemy_contact 警报，不混入）。
- EnemyClusterView（schema.py ~L404 stub）落地：own+enemy 就近聚类
  （网格桶半径≈5 格），HP=均值%/总和。
- 自动 step：agent/tools.py `_region_grid` 删 step 参数 → 取最小 step≥1 使
  列×行 ≤14，输出标注实际 step（14×14 上限保留，用户批注）。

### 批 5 落点：数据与初始化

- catalog 手册：tools/ 已有 dump 工具链（game_data_dump.json 单源）→ 生成
  workspace 端 markdown（terran 完整 11 字段 / 虫神参考 9 字段，模板已归一）；
  **serve_api 启动时幂等再生成**（防 catalog 演进漂移）；matchups 从
  memory/strategy-notes 种子。
- `plans/` → `production-plans/`：agent/workspace.py PLAN_PREFIX/_split、
  磁盘目录、seeds/模板/测试/UI 文案。**REST 路径 /api/plans 不改**（内部名，
  避免无谓 churn；虚拟目录名对齐即可）。
- 初始化模块（新 agent/bootstrap.py，泛化 memory_seed「只补缺失」）：
  首运/启动创建默认库存 = 系统提示词快照（system/prompt.md，取
  spec.SYSTEM_PROMPT 生成）、工作区设计文档、游戏·数据手册（catalog 生成）、
  memory 种子。
- scouting 派生：workers.scouting = 被 flow 组 lease 的 SCV 数
  （reservations/allocator 交叉）；`repair` op 不在 OP_CATALOG → ISSUES 立项。

### 批 6 落点：收尾

- audit_queue 工具删除（simulate_plan 吸收，D2）；spec.py 提示词更新
  （uid/before_uid/simulate v2/observe v2 用法）；seeds 同步；ISSUES/WORKLOG
  收档；全量回归 + 提交。

### 提交与执行纪律

- 每批后端+前端全绿即提交一次（消息引用批号与 §0.5x），不攒大包。
- 批内契约改动一次 REV+1；改 `web/src/contract/index.ts` 必同步后端 REV
  （schema.py L72）与 contract.test 两侧。
- 行为锁先行：先写表驱动测试锁语义，再动实现（分类器/状态机/双分支装载）。
- 执行顺序默认 1→2→3→4→5→6；批 5 可穿插。批 1 的 production_capacity
  归 pending 微调、矿区草案坐标两处，执行时找用户确认。
