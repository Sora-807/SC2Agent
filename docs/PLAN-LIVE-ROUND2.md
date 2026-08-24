# 实时对局第二轮修复计划（PLAN-LIVE-ROUND2）

> 立项：2026-08-24（§0.51）；**已执行：2026-08-24（§0.52，A→B→C→E→F→D 六批全落地）**。
> 执行偏差两处（详见 WORKLOG §0.52）：A1 的"重建 QueueItem 进投影"未做——planner
> `derive_from` 已把真实在途带进曲线（成本不重扣），重建反而双扣+泳道重复条；
> D0 三候选根因审计后收敛为「stale 后端」（征用全路径无泄漏）。
> 执行前提醒：先确认 serve_api 是新代码（stale 进程会复现已修复的旧病——REV 不匹配/
> 行为怪异先查进程再查代码，见记忆 sc2-build-env）。

## 0. 问题总览（用户原话摘要 → 对应批次）

| # | 现象 | 批次 |
|---|------|------|
| 1 | 实时对局投影矩阵「炸掉」：只提交 1 个农民，泳道图出现 20+ 农民条目 +「指挥中心×1·在产 6」；其他投影页面同样排查 | A |
| 2 | 右侧持续告警「补给站无 builder」，SCV 没有自动过去；为什么无 builder 会成为警告项且反复出现 | D |
| 3 | 游戏启动过程中 agent 又停了（无任何工具调用直接停）；强制 sleep 校验要做成系统回调，不要用用户消息显示；总轮数上限提到 200（或 1000），不许轻易放弃跟随 | B |
| 4 | start_session 无法指定开局策略、无法设置生产力默认值；提示词也没讲怎么配 | C |
| 5 | 工具图标：read 是数据库形状（应为文稿）、glob 应是放大镜 | F |
| 6 | done 工具不好用：聊天里只打出 result 字段；有了 sleep 后不需要它 | F |
| 7 | observe 应升级为用户设计的结构化读法：生产队列/资源/关键建筑数量（含挂件）/已有全部部队/生产序列在训待训/30 秒预估/区域信息（建筑兵种坐标血量）；只读文字信息不读地形 | E |

---

## A 批：投影真值——队列空了不许「回退参考计划」

### A0 现象与根因（已核实）

`modules/view/producer.py` `_project()`：live 队列**非空**才投真值；队列一旦为空就回退
`projection_plan`（`tools/run_session.py:176`、`modules/api/session.py:199` 都注入了
basic_opening 参考，22 项含大量农民+补给站）。

真机时序：agent 只 propose 了 1 个 train SCV → 该项被 emit 进 in_flight 的瞬间就从
queue.items 弹出 → 队列空 → **整张 basic_opening 参考计划被当成投影画出来**（截图里
20+ 农民条目、「指挥中心在产 6」全是参考计划的内容，不是真实队列）。
`web/src/pages/Overview.tsx:133-138` 有小字「队列为空，显示参考计划」，但图本身按参考
计划渲染，用户看到的仍然是假繁荣。

两个叠加缺陷：
1. **in_flight 不进投影**：`project_queue` 只吃 `queue.items`，正在建造/在训的项
   （in_flight，带 from_index/frames_waited）从投影里消失——单项目队列看起来像空。
2. **中途回退**：对局已经开始、生产历史非空时，参考计划已无参考价值，回退=误导。

### A1 in_flight 进投影（先行）

- `producer._project`：live 源头从 `queue.items` 扩为 `queue.items + in_flight 重建的
  QueueItem`（in_flight 有 stable_id/op；起始时间按已等待帧折算，完成时间按 catalog
  build_time 减已等待）。`view/projection.py` 的 op 翻译复用，`skipped` 语义不变。
- 投影帧 `source.kind` 仍为 `live_queue`；前端零改（契约不变，REV 不动）。

### A2 中途不回退参考计划

- 回退条件收紧：**仅在「本会话从未有过 live 队列内容」时**才投参考计划（开局展示用）。
  runtime 侧已有 emit 历史（economy._last_emitted_count 等），producer 需要一个
  「live 队列是否曾非空/in_flight 是否曾非空」的判定；实现放 producer（记
  `self._live_seen: bool`，见到一次真值就永久置位）。
- 已开局后队列+in_flight 全空：投「空曲线」（资源仍按当前收入斜率外推——
  `planner.project` 空 items 的行为确认一下，不行就给 zero-items 快路径），前端标题
  已有 kind=draft/live 之分，加「队列已清空」文案。

### A3 全页面排查（同一根因的消费者清单）

逐页确认改完后行为（每个都写一条测试断言 source.kind 与条目数）：
- `Overview.tsx`（投影面板，133-138）
- `ProductionPage.tsx`（甘特/泳道——截图所在页）
- `PlanningPage.tsx`（双投影：草稿 vs live 对比，确认 live 侧不再闪参考计划）
- `DebugPage.tsx`
- 复盘（recordings 回放路径——回放帧是录制时落下的，只确认**新录制**不再录到回退帧；
  旧录像带 draft 帧属正常历史，不修）

### A4 验收

- 单元：真机帧夹具（或合成）——queue 空 + in_flight 1 项 train SCV → 投影恰 1 项，
  `source.kind=live_queue`；live_seen 置位后 queue/in_flight 双空 → 不出现 basic_opening
  条目。
- 全量回归绿；真机快进一局只 propose 1 农民，泳道图从头到尾最多 1 条农民条目。

---

## B 批：对局跟随强化——不许停 + 系统级提醒 UI

### B0 现象与根因（已核实）

`agent/talk.py:155-156` `FOLLOW_MAX_ROUNDS=40`、`FOLLOW_MAX_IDLE_STOPS=3`。
启动期 agent 连续纯文字回复（无工具）→ 每轮 idle_stops+1 → 3 轮后「跟随停止」主动放
弃——这就是「游戏启动过程中 agent 停了」的直接根因。另一个隐患：`_game_alive()` 只认
state∈{启动中,对局中} 且 alive；SC2 启动窗口内 session 接口偶发取不到 → 误判「无对局」
→ 跟随直接不进入/中断。

且 nudge 目前以 **user 角色消息**注入历史（`_run_one_round(nudge, emit)`），UI 里渲染成
用户气泡——用户明确不要这个。

### B1 上限与放弃语义

- `FOLLOW_MAX_ROUNDS` 40 → **200**（用户拍板区间 200~1000，取 200 防失控长跑；
  模块常量可调）。
- **删除 idle-stops 放弃**：对局活着（_game_alive=True）就无限提醒下去；「止损」只保留
  给对局真正结束（alive=False → 正常收尾）。连续纯文字轮改为计数展示（系统条上
  「已提醒 N 次」），不再作为退出条件。
- 启动期容忍：`_game_alive` 对接口异常/取不到做**短重试**（如同 2s 内重问 2 次），
  state=启动中 期间不因单次取不到就判死。

### B2 提醒系统化（不再冒充用户消息）

- 历史 journal 里 nudge 消息打标（仿插话的 `interjection: True` 先例，加
  `nudge: True`），持久化保留（审计需要）。
- 流式事件发 `kind:"nudge"`（talk.py 的 emit 通道）；`ChatDock.tsx` 渲染为**独立系统条**
  （琥珀色、非气泡、不占用户消息位）：「对局进行中 · 不可暂停 · 已要求顾问用 sleep
  继续跟随（第 N 次）」。历史加载时同样按标记渲染成系统条。
- 后端注入给 LLM 的原文不变（LLM 仍需要那段「用 sleep 等待」指令），只改**展示层归属**。

### B3 提示词补充

`agent/spec.py` SYSTEM_PROMPT 对局域补两句：开局阶段（启动中）也不许只回文字——
起完 start_session 立刻 sleep 到对局中再 observe；文字回复不会结束回合，系统会持续
提醒直到用 sleep。

### B4 验收

- 单元：跟随循环——alive 时纯文字轮不再退出（跑 20 轮仍跟随）；alive=False 收尾；
  `_game_alive` 短暂失败 1 次不判死；nudge 事件带次数字段；ChatDock 快照测试出现系统条
  而非用户气泡。
- 全量回归 + 真机：启动期 agent 纯文字 → UI 出现系统提醒条，agent 随后调 sleep。

---

## C 批：start_session 开局配置面

### C0 现状（已核实）

`agent/tools.py:235` start_session 已有 `strategy` / `loadout` / `map_plan` / `mode` /
`speed`。缺：(a) 独立的**生产力默认值**参数——目前只有 loadout 打包带（loadout=地图+
策略+生产默认三件套）；(b) 提示词没教 agent 怎么配开局，用户观感=「无法指定」。

### C1 参数补齐

- 加 `production` 参数（可选，dict：如 `{"mineral_workers": 8, "gas_workers": 3}`——
  字段对齐 modules/production economy policy），后端
  `client.session_start` → `modules/api/routes/session.py` 透传到 runtime 装配。
- strategy 与 production 同给时的优先级：显式参数 > loadout 内含 > 出厂默认（写进
  tool description，避免歧义）。

### C2 提示词

`agent/spec.py` 会话段补「开局配置」小节：开局怎么选（list_strategies/list_loadouts →
start_session 带 strategy=/production=），给一个一行示例。

### C3 验收

- 单元：start_session 带 production → 会话 describe 里可见（或 economy policy 生效断言）；
  参数校验拒绝负数。
- 手工：agent 被要求「用 xx 开局 + 8 农民采矿」能一次配对。

---

## D 批：「补给站无 builder」告警根因

### D0 现状与初步假设（待真机复现钉死）

链路：`modules/production/runtime.py` `_pick_builder` 返回 None → 队列 blocked
（reason=无builder 诊断，§0.46 已加 `_no_builder_reason` 计数）→
`modules/view/alerts.py:77` `queue_blocked/{queue}` 告警（冷却 20s）→ 右侧反复出现。
截图里该告警持续数分钟 → blocked 一直没解除：所有 SCV 要么被征用（reserved）要么
frame_busy 要么 foreign。

候选根因（按可能性排序，执行时用真机+日志钉死）：
1. **用户测试时后端是旧代码**（§0.46 就近选人当轮未提交/未重启 serve_api）——旧逻辑
   不会主动从采矿 SCV 抽人， queued build 永远等不到人。先排除这个。
2. **flight 征用泄漏**：flight 超时/丢弃路径漏 release（`flights.py:110/250`、
   `runtime.py:179/351/353` 有 release，审计 drop/timeout 全路径），一个卡死 flight
   占一个 SCV；多卡几个候选就空了。
3. **economy foreign 误伤**：§0.46 后 build 单属于 flight 征用（reserved），不应再
   foreign；确认两套标记不打架。

### D1 落地动作

- 真机复现一局，导出该时段 alerts+production 帧，钉死候选根因是哪个（或多个）。
- `_no_builder_reason` 的诊断计数（candidates/reserved/busy/foreign）**进告警文案**：
  「补给站无 builder（采矿空闲 0 / 征用 3 / 忙 5 / 外来 4）」——用户问「为什么会成为
  警告项」，答案是：阻塞原因上浮为告警是对的（否则静默卡死），错的是**没带诊断、
  反复裸喊**。文案带上计数后每次出现都可读。
- 泄漏路径修掉（若 D0-2 证实）：flight 任何退出路径（完成/丢弃/超时/实体出现）必须
  release；加一条回归测试模拟「flight 卡住 → 候选耗尽 → 修复后能重抽」。
- 告警冷却语义复查：blocked 期间同 id 20s 一条合理；确认没有多队列同 reason 造成
  刷屏（截图里多条是跨分钟的，冷却本身没问题）。

### D2 验收

- 单元：flight 丢弃后 reservations 里 builder 释放（tags 不含该 SCV）；
  告警文案含四计数。
- 真机：提交补给站后 10s 内告警消失（builder 被就近抽走），全程不出现连续「无builder」。

---

## E 批：observe 升级为用户结构化读法

### E0 用户格式 vs 现状（已核对 `modules/view/observe.py`）

| 用户要的段 | 现状 | 动作 |
|---|---|---|
| 资源状态 | ✓「矿 X / 气 Y / 供给 U·C」 | 不动 |
| 生产队列 | ✓ 队列+阻塞+在途 | 不动 |
| 生产序列在训/待训 | 半有：队列 items 带 status、in_flight 有「在途」 | 明确分「在训（in_flight）」与「待训（排队）」两行 |
| 30 秒预估 | ✓ 投影段「30s 后预计：矿/气/供给」 | 不动 |
| 关键建筑数量（含挂件） | ✗ 只有「可见单位：我方 N 敌方 N」计数 | 新增段：按 stable_id 聚类建筑（ barracks=2(含 reactor 挂件 1)、factory=1…），数据在 world 帧（UnitView 有 stable_id/build_progress；挂件=addon 归属，`adapt.py` 已有挂件派生） |
| 已有全部部队 | ✗ 只有 flow 组（带 target 的编成），散兵/未编组不可见 | 新增段：全部战斗单位按 stable_id 计数（不分组也列），与 flow 组互补 |
| 区域信息（建筑兵种坐标血量） | ✗ 无 | 新增段：按基地（经济节点 base）分桶，桶内列建筑+我方部队（坐标取整+hp 百分比）；**只文字不地形**（不与 I18 inspect_region 的网格图耦合，纯 observe 文本） |

注：用户说旧文档里可能写过这个格式——已在 docs/ 全文检索（生产列队/区域信息/在训待训/
三十秒预估等关键词），**没有逐字原文**；以本表（用户本轮口述）为准。

### E1 红线（不破）

观察包仍**只从 ViewFrame 派生**（ADR-0007/observe.py 头注红线）：建筑/部队/坐标/血量
全部在 `frame/world` 的 UnitView（schema.py:387 起 pos/hp/hp_max/build_progress 都有），
不需要新帧、不动 REV。新增三段都进 `sections`（中文文本），facts 补
`buildings: {stable_id: n}` 与 `army: {stable_id: n}` 机器可读副本。

### E2 篇幅控制

PLANNING_CHARS 上限（tools.py:31 观察包文本上限）不放宽；新段用紧凑行
（`兵营×2(挂1) 工厂×1` / `基地A：指挥中心 92% @88,44；SCV×12 枪兵×6 @90,50`），
超限截断保关键段（风险/生产优先）。

### E3 验收

- 单元：合成 world 帧（含带挂件兵营、散枪兵）→ 三新段文本断言 + facts 副本断言；
  篇幅超限截断顺序断言。
- 真机 observe 一眼能答：「我有几个兵营带没带挂件、部队都有啥在哪、在训待训各是啥」。

---

## F 批：UI 小项（图标 + done）

### F1 工具图标语义化（`web/src/shell/ChatDock.tsx:51` toolGlyph）

现状字符表：read=`▤`（用户：像数据库，应为文稿）、glob 落到通用 `◆`（应为放大镜）。
改为**内联 SVG 迷你图标**（12px，currentColor，沿用 ThinkRow 的 AtomIcon 先例）：
read=文稿（矩形+横线）、glob=放大镜、grep=放大镜+横线（区分）、list=☰ 保留或列表 SVG、
write/append/edit/insert=铅笔系、simulate=▷、start_session=◎、observe=◉、propose=✑
保留。快照测试更新。

### F2 done 工具下线（后端不绑定 + 提示词清理；用户拍板可动，2026-08-24 复核）

- **绑定可摘**：`ToolSet` 的 `drop` 是 vendor 自己设计的组装接缝（toolset.py 头注
  「业务方也可以从零构造自己的组合」），我们的 `AdvisorSpec.tools()`（spec.py:142）
  **已经在用** `.drop("write","append","edit","insert")` —— 加一个 `"done"` 即可，
  vendor 源码一行不动。
- **终止不依赖 done（已核实）**：runner.py:179 `state.status=="done" or not
  response.message.tool_calls` —— 纯文字轮自然结束，收轮语义相同（outcome 都是 done）。
- **暗桩：vendor 追加的提示词行**。`build_system_prompt`（vendor prompt.py:27）在无
  WorkContract 时会把我们的 SYSTEM_PROMPT 包一层，末尾固定追加「完成任务后调用 done
  提交结果」；talk.py:495 与 runner.py:82 都走它。**只删工具不处理这行** → agent 每次
  收尾都试调一次不存在的 done → error 回喂 → 改文字，白多一轮试错。所以要三件套：
  1. spec.py:142 drop 加 `"done"`；
  2. 我们的 SYSTEM_PROMPT 删 done 两处（112-113 用法两句、120 docstring）；「输出纪律」
     改为「直接文字回复即回合自然结束」；
  3. talk.py:495 不再调 vendor `build_system_prompt`，换本地组装：
     `spec.system_prompt()` + 三行工具要点（done 行换成自然结束语义）+
     `tool_summary(tools)`（复用 vendor 函数）。执行轮核实 runner.py:82 的自组路径
     何时触发（talk 传 msgs 时应不经过），两条路径都要盖到。
- 连带清理：run.py 两条冒烟链的末步 `done` 调用改纯文字 Message 收尾；
  talk.py:565 `pending_tool.get("tool") != "done"` 过滤分支删除（done 不存在后永真）。
- 前端**零改动**（done 从工具流消失，无需特判渲染）。

### F3 验收

- 快照：read/glob 新图标；工具流不再出现 done。
- 单元：AdvisorSpec 工具集断言不含 done；冒烟链（observe→propose→文字收尾）绿；
  本地组装的系统提示词含工具清单、不含 done 行。
- tsc/build 绿。

---

## 执行顺序与依赖

```
A（投影）→ D（告警根因，需真机复现，与 A 无依赖可并行）
B（跟随）独立；C 依赖 B3 同文件（spec.py）注意合并
E（observe）独立；F（UI）独立
建议序：A → B → C → E → F → D（D 要真机，放最后专时段跑）
```

每批：测试先行/同批补齐 → 全量回归（后端 pytest + 前端 test/tsc/build）→ WORKLOG
§0.5x → 一批一提交（等用户说「提交」）。D 批涉及真机验证，执行轮先读本文 D0 的
候选根因清单再动手。
