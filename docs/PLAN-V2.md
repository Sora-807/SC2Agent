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
