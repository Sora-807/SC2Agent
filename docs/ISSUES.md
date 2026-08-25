# 问题清单（开放项）

> 记录用户实测 / Agent 自述提出的问题。处理一条关一条：**已完成/失效的移入
> [ISSUES-ARCHIVE.md](ISSUES-ARCHIVE.md)**（已处理留档 + 垃圾箱），这里只留未完成。
> 最近核对：**2026-08-25**（逐条对代码 file:line 与 WORKLOG 核验后全面重排：
> 关闭 8 条、并入 1 条、修正 2 条状态描述；核对细节见 ARCHIVE「本次核对」节）。

---

## I24 Group 补员滞回 floor：单兵营慢出兵时 group 永远涨不到 target，新兵不自动入组（用户 2026-08-25 立项）
> **已修复（2026-08-25，方案 1 落地，测试锁定；待真机复验）**：`GroupState.reached_target` 区分成长期/伤亡期，floor 判定收敛 `_effective_floor` 单点（refresh/snapshot 同源）。成长期不受 min 截断（兵一个一个来也一路吸到 target）；到过 target 后伤亡才用 min 滞回（S3 原意保留）。observe 的 refill_state 成长期如实显示「补兵中」。方案 2（min 语义护栏）由新语义自然消解——min 语义现在只在伤亡期生效；方案 3（set_rally）仍在清单 #18。


> 2026-08-25 代码核对：**未动工**；下述点位与当前代码一致。

**现状**（用户实测 + 代码核对 2026-08-25）：用户让 Agent 把一个 group 派到防守点，
新产出的机枪兵却留在兵营原地，不跟随 group 的移动指令。排查结论——**是「根本没
入组」，不是「入了组但不触发指令」**：

- 装配 composition（`runtime/strategies/simple-tactics.yaml`，Agent 自写）：
  `terran/marine: {min: 2, max: 20, target: 8}`；策略 `hold` step 的 `b_ready` 要
  `group_count(main, terran/marine) >= min_units(8)` 才转 `attack`。
- `modules/flow/allocator.py:103-105` `Allocator.refresh`：`floor = _refill_floor(spec, target)`
  后 `if len(cur) >= floor: continue`。`_refill_floor`（`:18-28`）在给了 `min` 时返回
  `min` → floor=2。**group 一补到 2 就停**，`[floor, target) = [2, 8)` 区间不再从 free 池拉人。
- 单兵营一个个出机枪兵：cur=0→取 1→1；cur=1<2→取 1→2；cur=2≥2→**停止**。第 3 个起
  全留在 free 池，没组、没指令。
- 真机佐证（`rec-20260825-012256-sc2` + trace `2026-08-25T011422_b8d03d38` turn_76 推理）：
  Agent 原话「机枪兵组心已经移到 27,49——正在向驻扎点 [22.5, 51.5] 移动！但只有 1 个
  机枪兵，5 个在训 + 5 个排队」，之后一直「等更多机枪兵出来」却永远等不到 8 个。
- **指令重发链是好的**（排除「入组但不驱动」）：`flow/engine.py:91-92` 注释 +
  `:356-379` `_emit_group_action` 把 unit tag 集合进去重 signature（F1），新兵一旦 lease
  进组，下帧 tag 集变 → `move_to`/`attack_move_to` 重发给全组（含新兵）；`:360-365` 另有
  「空组 no-op 不写去重键」护栏，组首次求值时还空着也不会埋下死键。**入组后必被驱动，
  断点不在指令重发。**
- 另：无 rally point 机制（`game/operation.py` OP_CATALOG 与 driver 翻译表均无 `set_rally`，
  `_t_train` 只发 train 不设 rally）—— free 池里没入组的兵没有任何兜底往防守点走。

**影响**：滞回 floor（`min`）本是为「伤亡后别每死一个就抢一次 free 池」设计的
（`allocator.py:19-25` 注释），但它把**成长期慢慢补兵**和**伤亡后等一等再补**用同一条
规则处理——单兵营慢出兵场景下 group 一到 `min` 就停止吸收新兵，永远涨不到 `target`。
后果链：group 永久卡在 ~min 个 → 策略 step 的 `>= target` 条件永不成立 → 策略死锁在
hold → Agent 一直「等兵」但等不到。observe 段的 `refill_state` 其实已标「滞回区」
（`allocator.py:42`），但 Agent 没读出来/没当回事（与 I17 可观测性同源）。

**候选方案**：
1. **区分成长期 vs 伤亡期**（正解，中难）——给每个 group×type 加 `has_reached_target`
   标志：未到过 target 时**总往 target 补**（不走 floor）；到过 target 后再伤亡才用
   floor 滞回（cur < floor 才补回 target）。保留伤亡抗抖动意图、不误伤成长期。落点
   `allocator.py` `GroupState` 加状态 + `refresh` 改判据。
2. **min 语义护栏**（低难，短期）——`min` 省略时 floor=target（现状已如此）；在策略
   编译校验/提示词里警告「写 min 会让 group 在 [min, target) 停止吸收新兵」，或把 `min`
   语义改成「最小可用门槛」而非「补员触发下限」。推给 authoring 侧较脆（Agent 已踩坑）。
3. **rally point 兜底**（补，正交）——OP_CATALOG 加 `set_rally` + driver 翻译，兵营设
   rally 指防守点，未入组的兵至少往那走。不修「永远不入组」核心，但缓解症状；与命令面/
   `game/operation.py` 相关。

**建议归属**：方案 1 是正解，归 `flow/allocator.py`（中难，加 per-group-type 状态）。
方案 2 可作短期护栏与 1 并行。方案 3 与 I17/命令面可并行。**与 I17 同源**：group 卡在
滞回区 observe 已可见但 Agent 没行动——根治靠方案 1，可观测性侧可补「group 长期滞回区
且 free 池有同型空闲单位 → 警报」（让 Agent 看见这个坑）。

## I25 假敌方警报：可破坏岩石被当敌方，困 Agent 于空转死循环（用户 2026-08-25 立项）
> **已修复（2026-08-25，方案 1+2 同批落地；待真机复验）**：关键词分类器下沉 `game.catalog.neutral_kind()`（单一事实源，原 view/adapt 的模式表）——world/adapter 按模式归中性（白名单退役，只留 FORCEFIELD 显式补），岩石不再以 Owner.ENEMY 进 units；alerts `_contact_alerts` 加同源排除兜底（双保险）。


> 2026-08-25 代码核对：**未动工**；下述点位与当前代码一致。

**现状**（真机 `rec-20260825-012256-sc2` + trace `2026-08-25T011422_b8d03d38` 核对）：地图
`terran vs neutral`（无敌方玩家），整局却反复报「敌方踪迹（10s 窗）：见过 5 个不同敌兵，
峰值同屏 5；最后出现 障碍物 @ (57,62)」——文案里「敌方」与「障碍物」自相矛盾。所谓
5 个敌兵其实是出生点附近的可破坏岩石。

- `modules/world/adapter.py:16-22` `NEUTRAL_TYPES` 是硬编码白名单（共 12 个 type：矿脉 4 +
  气井 3 + 装饰/可破坏 5），可破坏**岩石**仍只有 3 种具名
  （`DESTRUCTIBLEROCK6X6/4X4/RAMPDIAGONAL`）。SC2 可破坏障碍物有几十种 type 名，本图
  岩石不在表里 → `is_neutral_resource()`（`:25-26`）返回 False → 岩石留在
  `GameState.units`，`owner=Owner.ENEMY`（alliance=3，`_alliance_to_owner` `:29-36`）。
- `modules/view/alerts.py:108-112` `_contact_alerts` 只按 `u.owner is not Owner.ENEMY:
  continue` 过滤，**没有排除 stable_id 以 `neutral/` 开头的单位**。同模块 `recap.py:172`
  是有这个排除的（`not sid.startswith(("neutral/", "unknown/"))`）——警报层漏了同款过滤。
- catalog 把岩石 type 映射成 `neutral/destructible`（`view/adapt.py:237-242`）→ 中文
  「障碍物」，于是警报文案变成「敌方踪迹……最后出现 障碍物」。severity=warn（峰值 5 ≥
  `CONTACT_WARN_PEAK`，`alerts.py:40-41`），正是能叫醒 sleep 的等级。
- 后果（trace 实证）：每 10s 滚动窗触发一次，整局把 Agent 困在 sleep→observe→sleep 循环
  ——31 次 sleep + 47 次 observe，全程只 propose 过 1 次，最后手动 stop_session。

**影响**：假警报让 Agent 在「无威胁」对局里空转 230 秒——被同一条岩石警报反复叫醒、observe
看不到真威胁、又睡回去，产线/战术一动没动。这是本局 Agent 啥都没干的主因。

**候选方案**：
1. **alerts 加 `neutral/` 排除**（正解，低难）——`_contact_alerts` 在 `owner is Owner.ENEMY`
   之外，再排除 stable_id 以 `neutral/` 开头的单位（与 `recap.py:172` 同款过滤），岩石/中立
   装饰物不再算敌方。
2. **`NEUTRAL_TYPES` 扩白名单 / 改按 capability 判**（根治，中难）——`world/adapter.py`
   白名单只列 3 种岩石，要么补全 SC2 全部可破坏障碍物 type 名，要么改按 catalog 归到
   `neutral/destructible` 的都重分类 `Owner.NEUTRAL`，从根上不让岩石进 ENEMY。方案 1 兜底、
   方案 2 根治，建议都做。
3. **文案自洽护栏**——「敌方踪迹……最后出现 障碍物」这种 owner=ENEMY 但 stable_id=neutral/
   的自相矛盾，警报层组装文案时检测到就降级或换文案。

**建议归属**：方案 1+2 在 view/world 层，低-中难，建议同批做（1 兜底、2 根治）。与 **I17**
（可观测性）同源——「看见的」是错的，比「看不见」更坑。

## I26 补给站反复建造：build flight 假失败重发不查既有实体，吃光矿饿死机枪兵（用户 2026-08-25 立项）
> **已修复（2026-08-25，方案 1+2 合一落地；待真机复验）**：flight 记 `emitted_pos`（历次发射位），新增 `_claim_new_entity`——「新于基线 + 无主 + 落在本 flight 发过的位置」三判据收编晚到实体（confirm 与三条 retry 路径共用）；drain 对已锁定实体的 flight 走确认不走重试。full_flow「不认没发过位置上的实体」教训保留为判据 3（测试锁死）。方案 3（flight 事件流）归 I17 家族仍开放。


> 2026-08-25 代码核对：**未动工**；下述点位与当前代码一致（`_retry_build` 无 pre-emit
> 查实体 guard；`_confirm_build` 90 帧判据原样）。

> **2026-08-25 复验（trace `2026-08-25T093323_e5c71d81` + 录像 `rec-20260825-093336`）
> 发现残留缺口**：方案 1+2 堵住了「重发造重复建筑」，但 **flight 被丢 → 队列项被提前标
> COMPLETED** 这条仍开。本轮现象：兵营 BUILD 项 q04 被判 `prereq_missing` 跳过（并非
> 重造多座兵营，而是**前置 depot 在建期间对 ExecView 不可见**），连带 q07/q10/q11 机枪兵
> 级联跳过，直到 agent 手动重提 q09 兵营 + q12 机枪兵才出兵。
>
> 链路：depot 实体若未被 `_claim_new_entity` 锁定（位置不匹配 `emitted_pos`），`_confirm_build`
> 落到 builder 路径（`flights.py:131-158`），90 帧无 build_order / builder 丢失 → 返回
> `"failed"`；`_drain` Phase 1（`runtime.py:545-547`）release 后留 still_pending 重试，
> `_retry_build`（`flights.py:175-186`）若候选耗尽返回 False → **flight 不入 still_pending
> 而被丢弃**。紧接着 `_sweep_completions`（`runtime.py:330-331`）看到 depot 项 IN_PROGRESS
> 且 `id(it) not in flight_items` → **直接 `_finish_item` 标 COMPLETED，完全不查
> `build_progress`**（docstring `:317-318` 假设「flight 移除 = 实体完工」，但 flight 会因
> 假 failed 被丢、实体还在建 → 假设破裂）。于是 depot 三处全无（不在 `ready_types`
> [没建成]、不在 `inflight_types` [flight 丢了]、不在 `queued_types` [项已 COMPLETED]），
> 兵营 `_prereq_verdict`（`semantics.py:97-110`）判缺前置 → SKIP。skip 是终态（ADR-0032），
> depot 物理建成后也不复活。
>
> 注意：runtime 的前置判定**不读** observe 的 `buildings` JSON（`view/observe.py:_count_buildings`
> `:342-343` 按 `build_progress<1` 排除在建建筑，是独立的显示口径问题，不是跳过根因——
> 本轮 agent 自诊误把显示字段当根因，见 `memory/sc2-agent-self-diagnosis-rationalizes`）。
>
> **录像直证（`frame/production.in_flight` 逐帧）**：q02 depot flight 在 gt=100（frames=31）
> 至 gt=110（frames=87）全程 `builder=None`、`entity=None`（`_claim_new_entity` 未锁到实体，
> 落 builder 路径），gt=115 **in_flight 由 1→0**——flight 在 ~90 帧超时被丢，此刻 depot
> 远未建成。flight 一丢，`_sweep_completions` 即把 q02 项标 COMPLETED（不查 build_progress），
> depot 自此对 ExecView 三处全无，兵营 q04 随即 skip。链路从推理升级为逐帧实锤。
>
> **补丁方向**：`_sweep_completions`（`runtime.py:330-331`）标 BUILD 项 COMPLETED 前加一道
> `build_progress` 对账——flight 缺席时去 `gs.units` 核对该 type 实体是否真有 `>=1.0` 的；
> 没有就保留 IN_PROGRESS（不假完成）。属 I26 方案 3（flight 事件流）的轻量子集，或单列。

**现状**（同一局 + trace 核对）：计划只要 2 个补给站（`runtime/plans/simple-test.yaml`
queue 只有 2 个 `build supplydepot count:1`；Agent turn_26 的 propose 也只提交 2 个），
但真机实际造了 **9 个补给站**，矿全被吃光，机枪兵（q07，count:8）反复报「晶体矿不足」，
终局 9 补给站 / 5 机枪兵 / 人口 20-85。t=184「q02 补给站 已跳过：放置无可用位」。

- `modules/production/flights.py:103-108` `_confirm_build` 有两条假失败路径：90 帧后若判
  builder 无 build order（`:103-105`）、或超时（`:106-108`），返回 `"failed"`。
- 一旦 failed，`modules/production/runtime.py:542-544`（_drain Phase 1）调 `_release_flight`
  + 保留 flight，下一帧 `_retry_build`（`flights.py:111-149`）**直接换下一个候选槽位重发一条
  新的 build 命令（`:137-140`），且不检查上一座补给站是否其实已在 SC2 里在建/建成**。每次重试
  还 `_charge`（`:141`）扣一次矿。
- map-plan `barracks-stack-test` 有 D1–D9 共 9 个补给槽位。q02 一路重试，把 9 个槽位各发了
  一座真补给站，耗尽后 t=184 skip。
- Agent turn_76 推理自诊：「q02 一直执行中在不停造补给站——可能是 build count:1 的项只
  造了一个但状态没更新」，并报「7 完工+1 在建、人口 69 只用 14」。

**影响**：build flight 的「失败→重试」本是为「SC2 对非法放置位静默丢命令」设计的
（`flights.py:44-45` 注释），但重发不查既有实体，于是「确认逻辑误判失败」会反复造出真实
建筑。单次会话就吃光经济、把整条生产链饿死，比 I24（group 不入组）更直接地毁了这局经济。

**候选方案**：
1. **重发前查既有实体**（正解，中难）——`_retry_build` 重发前先查场上是否已有该 type 的
   在建/建成实体；有就改判 started、不重发。把「确认失败」与「是否真没造出来」对账。
2. **收紧 `_confirm_build` 假失败判据**（中难）——90 帧无 build order 就判 failed 太粗：先
   确认实体真没出现（`_type_entity_tags` 无增量且无锁定 entity）才判失败；build order ability
   名归一后匹配。减少误判。
3. **flight 生命周期事件流**（与 I17/trace 同源）——recording 只存原始帧，没有「flight X 在
   t=Y 重试、emit 第 Z 座」的汇总事件，这次只能靠 Agent 自诊 + 计划对比钉症状、逐帧机制钉
   不死。补 production 事件流让复盘能逐帧验证。

**建议归属**：方案 1 是正解，落 `production/flights.py` `_retry_build`（中难）。方案 2 并行。
方案 3 归 trace/可观测性（I17 家族）——是这次诊断「机制命名级、非逐帧实锤」的根因。

## I27 「大概率被摧毁」误报：_ever_ready 只增不减，把「曾建成」当「现在被毁」（用户 2026-08-25 立项）
> **已修复（2026-08-25，方案 1+2+3 全落地；待真机复验）**：alerts 新增 `_producer_alive`（当前帧对账）——建筑在场时无论 ever 与 reason 一律不挂「被摧毁」，改报「在场，等矿/等训练槽位」；真消失才报。runtime `_ever_ready` 改名 `_ever_built`（语义=曾建成；payload 键 `producer_ever_ready` 是契约不动）。


> 2026-08-25 代码核对：**未动工**；`_ever_ready` 仍只有 `:464` 一个 `.add()` 突变点，
> hint 仍纯按 `ever` 挂、不与 block reason 对账。

**现状**（同一局核对）：终局明明有 2 座兵营，但机枪兵队列项整局反复报「产出建筑曾建成、
现在不在——大概率被摧毁」。游戏摘要甚至出现自相矛盾文案（t=194/214）：「机枪兵 训练槽满
（production_capacity：兵营 就绪但订单已满）；产出建筑曾建成、现在不在——大概率被摧毁」
——前半句说兵营就绪在忙、后半句说兵营被毁。

- `modules/production/runtime.py:464` `self._ever_ready.add(entry.stable_id)`：任一己方建筑
  `build_progress>=1` 就塞进 `_ever_ready`，**全模块只有 `.add()`、无 discard/remove/clear**
  （`:107` 初始化、`:464` 唯一突变点）→ 只增不减，语义是「曾建成过」。
- `runtime.py:339-357` `_item_ever_ready` 返回 `any(sid in self._ever_ready for sid in ever)`
  ——只判「曾建成」，不判「现在还在不在」。
- `modules/view/alerts.py:177-182` 的 hint：`if ever is True: hint = "产出建筑曾建成、现在
  不在——大概率被摧毁"`——**纯粹按 `ever` 决定文案，不与 block reason 对账**。于是凡是
  train 项被阻塞（矿不够/训练槽满/缺产出建筑），只要该建筑曾建成过，都挂「大概率被摧毁」。
  矿不够时兵营明明活着也报被毁。

**影响**：误导 Agent/人去重建本来没被拆的建筑。本局 Agent 若按警报行动会去重排/重建兵营，
反而加剧混乱。与 **I17**（警报不带「怎么修」+ 误报）同源——这条是「带了的修法是错的」。

**候选方案**：
1. **hint 与 block reason 对账**（正解，低难）——`alerts.py:177-182` 的「曾建成现在不在
   大概率被摧毁」只在 block reason 属于「产出建筑缺失」类（prereq_missing/producer gone）
   时挂；矿不够/训练槽满（capacity）等建筑明明在场的 block，不挂或换文案（如「兵营在场，
   等矿/等槽位」）。`is_capacity_wait`/`_is_resource_wait`（`alerts.py:168-171`）已有判据
   可复用。
2. **`_ever_ready` 加「现在是否在场」**（中难，更深修）——补「当前同型 alive 实体数」判据，
   `ever=True 且当前 0 在场」才报「被摧毁」，否则只报「曾建成」。从根上让「现在不在」名实
   相符。
3. **改名/语义澄清**（低难，护栏）——`_ever_ready` 名字暗示「现在就绪」实际是「曾就绪」，
   改名 `_ever_built` 或加注释，防下一个人再误用。

**建议归属**：方案 1 是正解，落 `view/alerts.py`（低难，复用已有 reason 判据）。方案 2 并行
更深修。方案 3 护栏。与 **I17** 同轮收。

## I17 对局可观测性深度不足：警报无"怎么修" + 采气工不报 + 死步骤不报（2026-08-23 立项）

> **2026-08-25 代码核对**：子项 1/2/3/4 均未动工（AlertView 仍无 remediation 字段、alerts
> 无 shortfall 类 kind、assembly_gaps 仍仅 simulate 路径且 `points[-1]`/`eta=None`）。
> **子项 5（observe 在建项映射）已落地关闭**：REV16 `from_index` + REV18 `uid`/四态
> `status`（ADR-0032），详见 ARCHIVE。

- **警报不带"怎么修"**：`AlertView`（`modules/view/schema.py:757-770`）只有
  `id/kind/severity/at/eta/text_zh/source/payload`，**无 remediation/cause 字段**；
  `text_zh` 偶尔嵌软提示（`mineral_float` 写"看队首是否阻塞"）但非结构化。Agent 看到
  "装配缺口"但不知该改规划加机枪、降 `min_units`、还是换策略。
- **采气工不足不报**：`assign_workers` 是目标配额（维持 N，`production/economy.py:108-119`
  + 模块 docstring），系统静默接受任何值。**无采气工/mining shortfall 警报**（唯一 gas
  警报是 `gas_float`——资源没花掉，正好相反）。
- **装配缺口只报终局无时序**：`assembly_gaps`（`alerts.py:344-379`）读 `curve.points[-1]`
  比 final vs target，`eta=None`，无"t=X 后 step Y 卡了"的时序信息；且**只在 simulate
  路径**（`routes/plans.py:261`），live 的 `producer.py` `evaluate()` 不调它。
- **策略-规划可行性交叉校验缺失**（I12-B2 的更深半边）：**没有策略 `when:` 分支可满足性
  vs 规划产出的交叉检查**——rush-bc（0 机枪）配 sandbox_hold（gather 等 6 机枪）→ gather
  永不触发、部队死等，系统不报。flow 引擎每帧 `eval_when`（`engine.py:122-133`）但永不满足
  的分支只是不触发、无警报；唯一痕迹是 observe 策略段的"求值诊断"（`engine.py:256-258`），
  非警报。
- ~~observe 队列在建项映射缺失~~ **已落地**（REV16/18，见 ARCHIVE）。

**影响**：Agent 的理想循环（observe→看历史→判断一件事→propose→等下轮）依赖 observe
足够细 + 警报够深。现在警报只报"发生了什么"、不报"怎么修"也不报"会死锁"，Agent 要
自己从终局数字反推根因，断裂在"看见问题"到"知道改哪"之间。

**候选方案**：
1. **`AlertView` 加 `remediation_zh`/`cause_zh` 字段**（低难高收益）——每个 kind 配一句
   "往哪改"：装配缺口 → "降 min_units 或改规划补该兵种"；采气工不足 → "提 assign_workers
   gas 配额"。走 schema REV+1。
2. **加 gas-worker/mining shortfall 警报**（低中难）——`EconomyKeeper` 已有 actual/target，
   compare 后不足发 warn。
3. **策略死步骤检测**（中难，I12-B2 深化）——simulate 时把策略 `when:` 的可满足性
   （谓词依赖的 stable_id 是否在规划产出里）做静态可达性检查，不可达发"策略步骤 X 的
   条件依赖 Y，规划不产 Y → 死锁"。
4. **装配缺口时序化 + live 化**（中难）——不只 final，报"step X 自 t=Y 卡"；把
   `assembly_gaps` 接进 live `evaluate()`。
5. ~~observe 队列在建项映射~~ ——已落地，关闭。

**建议归属**：1/2 低难可插队先做；3/4 是 I12-B2 的深化（交叉校验），归同一批。I27（假
「被摧毁」hint）与本条同在 `view/alerts.py`，建议同轮收。

## I21 Agent 感知用户当前视窗：看什么页/哪份规划/哪张地图/鼠标在哪（用户 2026-08-25 立项，排期之后）

> 2026-08-25 代码核对：**未动工**（全仓 `ui_context`/`uiContext` 零命中；后端 chat 三入口
> 只读 `text`，前端三入口只发 `{text}`）。

**诉求**：用户不想每次在对话里重新交代「我正在看 XX」。打开某个界面后，
agent 发消息时就该自动知道上下文：正在哪个页面（规划地图/生产规划/驾驶/
复盘）、正在看哪份地图规划/生产规划、地图视窗的中心与缩放（正在看哪个
区域）、鼠标悬停位置（格点/悬停的槽位名）。**注入时机 = 每次发消息时自动
附带**（用户拍板），不是 agent 主动拉取。

### 设计要点（立项时想清楚的）

1. **采集点（前端）**：前端已有全部状态——`session-store`（模式）、
   `map-plan-store`（selId/spawn）、`queue-store`（plan.id）、MapCanvas
   的 viewport（中心/缩放）与 `hoverCell`（F18 悬停窗已在算世界坐标+格点）。
   缺一个**汇聚点**：建议 zustand 里加一个 `uiContext` selector 或独立
   小 store，各处订阅写入。
2. **注入通道（后端）**：`POST /api/agent/chat` 请求体加可选
   `ui_context` 字段（结构化：`{mode, map_plan_id?, spawn, plan_id?,
   view_center?, view_scale?, hover_cell?, hover_slot?}`）；
   `AgentTalk.say` 把它作为**系统侧附注**拼进本轮输入（不进 history 落史
   —— 每轮都变，落史会把对话历史灌满导航噪音；只当轮有效）。
   插话（interject）同样带。
3. **隐私/噪音边界**：只带「正在看什么」，不带草稿未保存内容（那是用户的
   半成品，AI 看了会以为已定稿）；视窗/鼠标坐标带但**节流**（发消息时快照
   一次即可，不做轮询推送——常驻监听已另成机制）。
4. **提示词侧**：spec.py 说明 ui_context 的语义（「用户正在看的界面上下文，
   回答时可以自然引用『你现在打开的那份规划』，不用让对方重复交代」）。
5. **验收**：用户打开地图规划 default、把视窗拖到红方半场、悬停某槽位，
   然后问「我这看的是什么」——agent 应能答出「default 的 bl 分支、视窗在
   红方区域、悬停槽位 R5」级别的回答，全程用户零交代。

依赖：无硬依赖，可独立做。估 0.5-1 天。

## I22 坐标系整数化评估：slot pos 的浮点双形态（用户 2026-08-25 立项，先挂账）

> 2026-08-25 核对：仍是挂账状态（短期护栏已在：前端数学锁 + 后端 `tl_from_pos` 架构测试）。

**现状（为什么是浮点）**：slot 的 `pos` 是 **SC2 命令目标点**（协议就是浮点），
且按 ADR-0027 分两种形态——奇数尺寸 footprint 中心=整数（3×3 的 R=41），
偶数尺寸=半整数（2×2 的 R=40.5）。格点（tl）本身恒为整数，由
`tl = ceil(pos - size/2)` 派生（前端 slotTl / 后端 tl_from_pos 双侧同公式）。

**代价（用户直觉正确：偏移事故与它直接相关）**：pos 双形态意味着每个消费方
都必须走同一份 ceil 派生——2026-08-25 拖拽 ghost 自推了一套 round 公式，
偶数尺寸在小数 <0.5 段与落点公式差一格（2×2 补给站实测偏移一格、3×3 不偏），
已修（统一 snapToCellCenter）但这是**第三次**坐标换算分叉事故
（此前 §0.51/§0.52 各一次）。

**整改方向（评估后定，不急）**：存储与契约层只留 `tl + size`（整数），
`pos` 一律派生（发送 SC2 命令时现算：奇数 R=tl+size/2、偶数 R=tl+size/2-0.5
——单点函数）。牵扯面：`game/production.BuildSlot`（pos 是 can_place 扫描的
校准产物，得连带改扫描器）、driver 命令、ADR-0027 全部公式、契约
`static/map.build_slots[].pos`、前后端全部槽位编辑/渲染路径。
**不整改的理由**：can_place 校准的 pos 有亚格精度价值（贴墙时半格之差决定
能不能放），整数化会丢这层信息——评估时要先确认校准 pos 是否真的用过
亚格偏移，没有再动。**短期护栏**：数学锁已进
`web/tests/map-canvas.test.ts`（ghost 与 drop 同格 + tl 恒整数），
后端 `tl_from_pos` 有架构测试锁单点。

## I23 策略面缺口：术语统一（flow→策略）+ 装配可视化 + 编写向导（用户 2026-08-25 立项）

> **2026-08-25 代码核对**：A 未做（`web/src/shell/route.ts:18` 仍「Flow 装配」，另有 4 处
> flow 残留 label：route.ts:15「Flow」/ PlanningPage.tsx:358 / Overview.tsx:88「Flow 状态」/
> layers.ts:54「flow 分组」）；B 部分（驾驶页有运行态绑定/参数卡 FlowPage.tsx:475-522，但
> plan-flow 无装配区块，min/max 编成表无处可看）；C 未做（strategies.ts 只有只读 list）。
> 原清单#3「策略编辑 UI（人用）」已并入本条 C 项。

**用户三问的现状核对**（立项时逐条核实过代码）：

1. **observe 的 step 参数**——已经取消（批 4 改自动 step：≤14×14 全量、超出
   自动降密度并在输出尾部标注）。无遗留，不立 issue。
2. **"flow 怎么装配"前端缺失**——属实。现状：装配 = 策略文件（strategy 段）
   + assembly 段（组编成 + strategy_instances 绑定）两段 YAML，**装配关系
   （哪个策略实例绑哪个组、组的 min/target/max 构成）没有任何前端可视化**。
   现有的两页各画半边：
   - `FlowPage`（驾驶模式）＝**运行态图**：static/strategy 的步骤图 +
     frame/flow 的当前 step/转移历史，live 下只读（R5）；
   - `plan-flow` tab（规划模式）＝**结构图**：同一张步骤图 + 边的 branch
     条件，同样只读。
   两页都不画 assembly（组编成表 / 实例绑定 / 参数绑定）；写策略只能靠
   agent 走文件契约（strategies/<id>.yaml + _lib 模板库），**人没有图形化
   编写入口**——这就是用户说的"一直没解决的遗留问题"。
3. **术语**——半统一状态：面向用户的文案已全说「策略」（StartCard 下拉、
   route label 除外：`plan-flow` 的 label 还是「Flow 装配」）；内部模型/
   帧名不变也不必变（`frame/flow`、`FlowEngine`、`static/strategy` 是
   契约与架构名，改名纯 churn）。要做的是把**用户可见面**统一成「策略」，
   内部名保留。

### 要补的三块（建议拆法）

- **A 术语清扫（小）**：route label「Flow 装配」→「策略装配」（或「策略」）；
  FlowPage 标题/文案同步；内部 frame/flow/FlowEngine 不动。
- **B 装配可视化（中）**：plan-flow 页加「装配」区块——组编成表
  （group_id/兵种构成 min-target-max，数据源 assembly）+ 实例绑定
  （strategy_instances：实例→策略文件→组），与步骤图并排；驾驶模式的
  FlowPage 加同源只读版（对局中看"这波是哪个组在执行哪步"）。
- **C 策略编写向导（大，可再拆）**：结构化表单/块编辑器（步骤=卡片、
  branch=条件行、_lib 模板可插入），写完走既有保存校验（全套编译验证，
  错误带 step 定位）。依赖：无硬依赖；C 建议在 I21（视窗上下文）之后——
  向导要"写时看到 agent 会怎么读"，视窗注入能显著降向导的说明成本。

验收（用户口径）：一个没写过 YAML 的人能在前端从零搭出「集结→推进→
堵口」三步策略并装配给步兵组，全程不碰文件；对局中能一眼看到当前
策略实例卡在第 2 步、等什么条件。

---

## I28 建造完工的 SCV 不回采矿：维持器目标已满不派空闲工，建造工被 SC2 清单后不 auto-gather（用户 2026-08-25 立项）

> **2026-08-25 已定位（trace `2026-08-25T093323` + 录像 `rec-20260825-093336`，未动工）**：
> 录像逐帧坐实——barracks 建造工（tag 4339269633）完工（gt=191, bp=1.0）后，SC2 在
> 191→195 之间把 build 单清成 `order=None`，但该 SCV **不会 auto-gather**（不像从 CC
> 新训出的 SCV 会自动采矿），原地 [126.5,110.2] 发呆到 gt=230+。`frame/economy` gt=200：
> `mineral {quota:8,target:8,actual:13}` + `idle {actual:1}` + 多个矿脉 workers<cap（未饱和）。

**现状**：开局 `start_session` 设 `mineral_workers=8` 全程未提；采矿目标硬卡 8。真机里
新训 SCV 从 CC 出来 auto-gather 自然入矿（actual 涨到 13，超目标），但**完工释放的建造工
不会 auto-gather**。维持器两条设计合谋把它晾着：

- `_targets`（`economy.py:319,323`）：`m_target = min(m_want=8, m_cap, m_room)` → 目标 8。
- `_plan`（`economy.py:349-394`）：Step1 只 keep 8 个在矿上、Step2 `need=m_target-kept=0`
  不再派 → 空闲建造工 `plan[tag]=None`（保持空闲，:393-394 setdefault）；且 Step3 对超额矿工
  「不主动 stop」（:392 注释）→ 形成**「矿超员(13>8) + 同时有空闲工(1) + 矿脉有空位」
  的退化态**：既不把空闲工派去空矿脉、也不把超员工拉下矿。

- `_release_flight`（`runtime.py:834-838`）只 `reservations.release(owner)` 解除征用，
  **不主动下令回矿**——靠维持器接管，而维持器因目标已满不接管。

**影响**：每造完一座建筑就少一个采矿工且不恢复；多建筑后采矿人力持续流失，收入曲线
比预期低。与 I26 同一局暴露，但根因独立（I26 是 flight 假丢→假完成，I28 是维持器目标
封顶 + 建造工不 auto-gather）。

**候选方案**：
1. **维持器把绝对目标当地板而非硬上限**（正解，中难）——`_plan` Step2 之后补一轮：
   仍有 `cur=None` 且非 foreign 的空闲工、且矿脉 `room>0`（未饱和）时，派去填容量
   （不超过 m_cap），不再因 `m_want` 已满而停手。直接让建造工回矿。
2. **`_release_flight` 主动回矿**（低难，与 1 互补）——释放征用时同步给该 SCV 发一条
   gather/return 指令（`Emission`），不等维持器。代价：与维持器双路由，要错峰或标位防顶单。
3. **目标随工人数自适应**（策略面）——`mineral_workers` 不写死 8，按 `总工 - gas - 在建 -
   reserve` 派生，或 agent 在出工高峰期重提 `assign_workers` 提高配额。属 [[sc2-planner-direction]]
   的经济校准范畴。

**建议归属**：方案 1 是正解，落 `production/economy.py:_plan`（中难，要加测试锁「空闲工+
空矿脉→必派」）。方案 2 并行兜底。方案 3 归 planner/策略。与 I26 区分：I26 修队列项假完成，
I28 修维持器不派空闲工。

---

## I29 规划页「文件与试算」侧栏概念混乱+拥挤：规划文件与从模板落地应合并成一个「新建」入口（用户 2026-08-25 立项，先挂账不改）

> **2026-08-25 已定位（查 `QueueSidebar.tsx` + `api/plans.ts` + `api/routes/plans.py`，未动工）**：
> 用户反馈右侧「文件与试算」卡里框太多、显杂；核心困惑是「规划文件」节和「从模板落地」像重复。

**两者本不是一回事**（查实后端来源）：
- **规划文件** = `runtime/plans/<id>.yaml` 磁盘文件（`PlanStore`）。操作 列表/打开/保存/复制新建/空白新建/删除。
  它的下拉框 = 选「**已存在的规划文件**」来编辑。性质 = 可编辑工作副本（文件是真相源，人+agent 改同一份）。
- **从模板落地** = `MODULE_REGISTRY`（`planner/build_order.py` 代码定义的内置战术模板，如 `bio_tank_opening`，
  **只读**、唯一真相源）。操作 模块清单(只读) + 落地 = 调模块函数生成队列 → **写一份新 plan YAML**（`plans_from_module`
  最终走 `plans.create`）。它的下拉框 = 选「**内置模板**」来实例化成新规划文件。

**为什么像重复**（根因）：
1. 两个下拉框并排（文件清单 + 模块清单），都「从列表选一个、产出一个规划」——看着像平行功能，其实一个是
   选已有文件改、一个是用模板生新文件。
2. 「新建规划」实际有**三个动作散落**：复制新建（复制当前→新文件）、空白新建（空→新文件）、从模板落地（模块→新文件）。
   三者同是「新建一份规划文件」只是来源不同，却拆成 3 个按钮/下拉 → 显得是两套。
3. **落地后的模块就是份普通规划文件**（open/save/改/删全走规划文件那套），所以模块下拉只在「生新文件」那一刻
   有意义，平时不该和文件下拉并排常驻。

**拥挤的另一面**：`QueueSidebar` 把 4 节塞进 1/3 宽窄列——规划文件 / 放置引用 / 成本与试算 / 前瞻警报，
含 3 个下拉框（文件/模块/放置引用）+ 5 按钮（保存/复制新建/空白新建/从模板落地/删除）+ 成本 + 视野输入 + 试算按钮。
高频的「选文件→改队列→保存→试算」和低频的空白/从模板/删除平铺，故显杂。

**候选方案**：
1. **合并「新建」入口**（正解，中难）——三个新建动作收成一个「新建规划」，带来源选择
   （空白 / 复制当前 / 从模板[选这时才出模块下拉]）。两个并排下拉消失，模块下拉折进来源选择。
   配套：空白新建/从模板/删除这些低频项收进「更多」折叠，日常只留 文件下拉+保存。
2. **「文件」与「试算」物理分离**（并行）——试算按钮/视野秒/前瞻警报移到下方「试算投影」卡头部（触发紧贴结果），
   侧栏纯「文件」（规划文件+放置引用）。贴合用户心智「文件」与「试算」本就是两件事；与 I30 自动试算配合更顺。
3. **侧栏内分「文件/试算」两 tab**（低难兜底）——不动位置，顶部 tab 切换降竖向密度，代价是切 tab。

**建议归属**：方案 1 是正解（解决概念混乱），落 `web/src/panels/QueueSidebar.tsx` + `queue-store.ts`
（`create`/`createFromModule` 可合成一个带 `source` 的 `create(source, payload)`）。方案 2 与 I30 同批做最顺。
等用户定布局方向再动（见 [[sc2-planning-redesign-direction]] 前端编辑四修后续）。

---

## I30 试算不自动：改队列后应防抖自动重算，现仅手动 + 开/存时跑对照版（用户 2026-08-25 立项，先挂账不改）

> **2026-08-25 已定位（查 `queue-store.ts`，未动工）**：用户反馈改完队列不自动试算，要手点。

**现状**（`web/src/planning/queue-store.ts`）：
- `open(id)`（:122）：打开规划时自动跑一次 `baseSim`（**已保存文件**的干跑，做对照）。
- `save()`（:140）：保存后再跑一次 `baseSim`。
- `simulate()`（:232）：**纯手动**，按钮触发（`QueueSidebar:132-137`），跑的是**草稿**干跑 `sim`。
- `update()`/`setItems()`（:252-257）：改队列只置 `dirty=true`，**不触发任何试算**。

→ 改队列后投影卡停留在旧结果（顶部标「未保存草稿」），用户得手点「试算」。`baseSim` 那次自动只在开/存时跑，
且跑的是文件版不是草稿版。

**候选方案**：
1. **改队列后防抖自动重算**（正解，低-中难）——`update`/`setItems` 后挂 ~400-600ms 防抖自动调 `simulate()`；
   空队列跳过（置 `sim:null`）、`busy` 时跳过（不并发）、出错只落 `msg` 不阻断编辑。跑的是草稿 `sim`（与现手动同）。
   实现：queue-store 加一个 `scheduleAutoSim()`（`setTimeout` + 存 token 防重叠），`update`/`setItems`/`patch`
   （改 horizon 也该触发）末尾调；`open`/`save` 后清防抖（已自跑 baseSim）。
2. **跑前自动保存**（否决）——草稿未存就跑仿真会让人困惑「我改的算进去了吗」；保持「跑的是当前草稿、与存不存无关」。

**建议归属**：方案 1，落 `queue-store.ts`（加防抖 + 在 `update`/`setItems`/`patch`(horizon) 末尾触发）。
与 I29 方案 2 同批做最顺（试算入口移到底部投影卡后，自动试算让手动按钮退居二线）。

---

## 开放任务清单（2026-08-25 核对版；处理一条关一条）

> 2026-08-25 对代码与 WORKLOG 全面核对后重排（原清单 24 条 → 现 18 条开放；关闭/失效条目
> 与去向见 [ISSUES-ARCHIVE.md](ISSUES-ARCHIVE.md)）。原编号以（原#n）标注，外部文档引用
> 请改引 issue 号（Ixx）或条目名，勿引清单序号。

**P0（本局真机三报，2026-08-25 立项）**

1. **I24 group 补员滞回 floor——已修复（2026-08-25 待真机复验）**单兵营慢出兵时 group 卡在 min 永远涨不到 target，
   新兵不入组 → 策略死锁 hold、Agent 一直「等兵」等不到；详见 I24 节。
2. **I25 假敌方警报——已修复（2026-08-25 待真机复验）**`NEUTRAL_TYPES` 白名单太窄 + `alerts._contact_alerts` 无
   neutral/ 排除 → 岩石算敌方、warn 叫醒 sleep，困 Agent 于 sleep-observe 空转整局；
   详见 I25 节。
3. **I26 补给站反复建造——已修复（2026-08-25 待真机复验）**`_confirm_build` 假失败 → `_retry_build` 重发不查既有
   实体，q02 把 9 个槽位各发一座真补给站，吃光矿饿死机枪兵；详见 I26 节。

**P1**

4. **I27「大概率被摧毁」误报——已修复（2026-08-25 待真机复验）**`_ever_ready` 只增不减 + hint 不与 block reason
   对账 → 兵营活着也报被毁，误导重建；详见 I27 节。与 I17 同轮收。
5. **I23 策略面缺口**——术语 flow→策略清扫 + 装配可视化 + 编写向导（原#3「策略
   编辑 UI（人用）」已并入 C 项）；详见 I23 节。
6. **I21 用户视窗上下文自动注入**——发消息时带 ui_context：页面/规划/地图/视窗/
   悬停（用户不想重复交代正在看什么）；详见 I21 节。
7. **event_occurred / has_ready_base / user_cancel 谓词（I12-B1 剩余）（原#2）**——
   **事件源已拍板（2026-08-25）：世界推导**——从帧差/现有警报派生结构化事件（建筑被毁/
   单位伤亡/敌方接触/用户取消队列项），不走 driver 原始 events（driver 无关、sim/live/
   回放同源）；has_ready_base 从 catalog 三族 town hall 判 built≥1 落地。
   （2026-08-25 核对：`predicates.py:68-72` 仍 3 条未实现；timer/locals 写侧已全通。）
8. **对局可观测性深度（I17 剩余）（原#12）**——警报加 `remediation_zh`（"怎么修"）+
   采气工 shortfall 警报 + 策略死步骤检测（I12-B2 深化：`when:` 可满足性 vs 规划产出）+
   装配缺口时序化/live 化。子项 5（observe 在建项映射）已落地关闭。1/2 低难可插队先做。
9. **modules/ 代码债剩余（I15）（原#11）**——P0 bug 批与 god files（G1-G3）已清，B7
   （命令 shape）§0.41 已修；剩：B6（planner 仍 Terran-only，**路线已拍板 2026-08-25：
   catalog capabilities 推导**，投影真支持三族）+ 死代码清理 + 去重
   （详见 [`REFACTOR.md`](REFACTOR.md)）。
10. **Agent 跨会话记忆效果观察（I19 剩余）（原#15）**——结构与种子全齐（memory/ 四文件 +
    improvement-notes + agent/seeds + 提示词整改）；剩余：几局后校验 agent 是否真读真写、
    `system-capabilities` 派生是否对账——不行再上机制（开局自动 seed 检查）。
    附带待拍板：孤儿 `notes.jsonl` 去留（后端 note_save 端点在、Agent 无工具，链路仍断）。
11. **观察包"零收入检测"（原#18，用户拍板随后做）**——观察包经济段加资源产出速率
    （近 N 秒 Δ矿/Δ气）+ 采矿工人距目标矿脉的距离分布；结构化警报「收入为 0 且有采矿
    分配 → 疑似采错矿/路途过远」（I17 家族）。2026-08-25 核对：未做（observe 经济段
    仍只有分配数口径）。

**P2**

12. **开局工人口径：真机 8 工 vs 种子 12 工（原#17）**——真机录像首帧 8 工/13 cap，
    种子（planner.opening / worldsim.bootstrap / session 默认）仍全 12 工（2026-08-25
    核对确认）。供给值已单源修正为 13；工人数是另一处 sim/真机偏差：干跑经济曲线比真机
    乐观。**已拍板（2026-08-25）：先查 8 工根因**——12 工是标准 melee 口径，先确认那局
    用的地图/模式是否非标准开局，再决定改种子还是改测试环境（直接改种子会波及全部干跑
    数字与夹具，且可能把干跑永久校到一张非标准图上）。
13. **槽位 placeable 后端校验收口（原#6）**——terrain.placeable 栅格进摆放校验面
    （2026-08-25 核对：仍只查不压己方建筑/在途预留，不查地形栅格）。
14. **模块模板参数化 UI（B3 增量，原#7）**——from-module 端点已支持 params，前端
    「从模板落地」不带参数（marine_target/tank_count 调不了）。
15. **组/槽位形状颜色标记（I4 候选 3，原#8）**——地图 chip 与策略图同词的视觉语言
    （现为纯文字同词，无按 group 的颜色/形状）。
16. **live 投影窗口语义（原#10）——已拍板（2026-08-25）：until_complete + 封顶**——
    与试算同口径（`COMPLETION_CAP` 钳制）；live 队列有界，预期 horizon 不会失控，落地时
    实测帧大小，超预期再回调。
17. **复盘（回放源）切换加载慢（原#19，用户拍板可后排）**——换源/拖时间轴前端卡顿：
    整份 JSONL 拉取+逐行解析+全量重建。候选：分窗解析/增量渲染/录制索引。
18. **repair / set_rally 操作（原尾注，D3 立项批 6 后排期）**——repair 不在 OP_CATALOG
    （SCV 修理目标/跟随大部队）；set_rally（集结点）为 I24 方案 3 的兜底项，可同批。
