# 问题清单（计划外发现，待处理）

> 记录用户实测提出、但 F10-F14 计划**没有覆盖**的问题。每条带现状、影响、候选方案与建议归属。
> 处理一条删一条（或在 PLAN.md 立项后注明去向；已完成的移入文末「已处理（留档）」）。
> 记录时间：2026-08-21（F12 验收反馈）。

---

## I7 投影板与规划页的 UI 债（已处理，2026-08-22）

- **现状**（2026-08-21 四轮反馈）：①泳道图放大后出现左右滚动条（不应出现）；
  ②大量窗口自适应高度，拖时间轴时布局跳动过大；③地图规划左侧栏偏大。
  另：规划页与生产页曾是两套图表组件（ProjectionPairChart vs ProjectionBoard）——
  已在 P0 收敛为同一组件（双板 ProjectionBoard），此项关闭。
- **处理**：①P1 轮修（泳道固定 140px + overflow-x-hidden）；②概览经济数字
  tabular-nums（2026-08-22 十四轮），生产页三区固定后已基本消；③2026-08-22
  十四轮左抽屉化解决（工具/列表进可收起的抽屉，主区只剩画布）。**关闭。**
  （I1/I2/I4 的策略中文化是另一条线，仍开放。）

## I8 生产规划的 placement 引用了具体地图，规划却可能跨地图（已处理）

- **现状**（2026-08-21 用户指出）：生产规划的 build 项 placement 下拉引用**当前帧源地图**
  的槽位/点位/区域名；但规划是独立文件、可能跨地图使用（地图有好几份，规划不一定
  对应正在看的那张）。换一张地图，placement 名字就对不上。
- **进展**（同日 P2 切片 1）：地图规划已是后端文件（默认锁定 + 复制新建 + 出生点分支），
  会话装配已接 `?map_plan=`；**点位命名空间的方向用户已认同**：引用形态改为
  「地图规划名/点位名」——涉及 placement 匹配与契约，归 P2 切片 2 与区域划分一起做。
- **处理**（2026-08-22，P3 切片 3 / WORKLOG §0.13）：「规划id/点位名」限定引用落地 ——
  REST 命令面映射（会话装的是该规划就剥前缀，装的不是/没装 → 400 带理由，
  ViewFrame 零改动）；预设固定建造点 36 名进会话 layer.pos_marks（全局命名空间，
  裸名即可引用：精炼厂→蓝方主矿气井1）；编辑器「放置引用」下拉按所选地图规划
  过滤选项（槽位/点位带限定前缀 + 预设名 + 区域）。**关闭。**

---

## 已处理（留档）

- ~~I1 谓词/操作符没有中文别名~~ —— 2026-08-22 二十五轮关闭（WORKLOG §0.28）。
  按候选方案 1 落地：`flow.vocab` 增 PREDICATE_ZH/OPERATOR_ZH/ACTION_ZH，
  `dump_vocabulary()` 每项带 `name_zh`（契约 rev 12），前端 `renderValue` 经
  `vocabOf(schema)` 渲染中文谓词/运算符（≥ 且 或），无 schema 退回 identifier。
  提示词卡片同步受益。覆盖性测试锁死（新增词忘配 zh 当场红）。

- ~~I2 step_id / 策略名是裸英文标识符~~ —— 2026-08-22 二十五轮关闭。
  manifest 增 display_name_zh/description_zh（策略级 + step 级）+ `reasons`
  中文表 + params `description_zh`；assembly GroupSpec 增 display_name_zh。
  随 static/strategy 下发（rev 12，含 group_names）。三份策略源全部补写
  （garrison→驻守入口、armor_hop→坦克前压…）。前端节点头部/边标签/转移历史
  全部中文优先，identifier 降级为 tooltip。

- ~~I4 面板可读性：标识符前缀与组名没有解释~~ —— 2026-08-22 二十五轮关闭
  （组合拳）：Card `help` prop（？ 圆标 hover 说明）上策略图/转移历史/绑定与
  参数；前缀 `参数.min_units` → `参数 min_units`；组显示「步兵组（G_INF）」；
  参数声明 description_zh 进 tooltip。候选 3（组/槽位形状颜色标记）未做，
  需要时单开小项。

- ~~I9 敌方非人族单位/建筑显示「未知」~~ —— 2026-08-22 由并行目录线解决：
  protoss.json/zerg.json 数据落地，`catalog.py` 增 `load_protoss/load_zerg/
  load_all`，driver/sc2_adapter 与 api 全链路换三族加载（对手抽到 P/Z 不再
  unknown/）。741 测试全绿含 driver 侧。真机抽到 P/Z 的最终目验留给用户。

- ~~I10 「开局90秒（再生产链）」夹具的投影泳道并行度可疑~~ —— 2026-08-22
  二十五轮关闭，**根因不在 planner 经济参数**：① 投影 events 是 planner 模拟值
  （非录制真值），t=90 队列跑空后还掉进 draft 兜底（basic_opening 参考计划）；
  ② 假并行的真根因 = WorldSim `_op_train` 无产槽占用检查（count=6 的 TRAIN 让
  一个兵营同时爬 6 条机枪兵进度）+ planner 快照在途推进同样无槽并行。双层修：
  WorldSim `_train_busy` 产槽排队（真机语义：命令即受、进度条排队）；
  `Planner._advance_in_flight` 对齐 B15 slot_capacity。修后 90s 夹具机枪兵
  18s 链式完成。测试 +4。**遗留小项**：t=90 的 draft 兜底会把 basic_opening
  在已有 depot/barracks 的世界里原样重放（再造一个 depot）—— 参考计划与
  现状不匹配的显示语义，等参考计划换成 bio_tank_opening 或按 gs 去重再看。

- ~~I12 之 B2：装配与生产规划无交叉校验~~ —— 2026-08-22 二十五轮落地
  （I12 辨析本身留档）：`AlertService.assembly_gaps` 在干跑终态对账
  composition.target，`/api/plans/simulate` 返回 assembly_gap 前瞻警报
  （「步兵组要机枪兵 ×10，规划终局只有 4」）。live 窗口投影刻意不参与
  （截断的终态会假缺口）。B1（谓词补全）/B3（模块模板化）仍开放待立项。

- ~~真机（sc2）地形不可用：地图页纯色底~~ —— 2026-08-22 修复（WORKLOG §0.12）。
  根因 = `SC2DriverBot.on_step` 地形回调先于首帧 sink → 静态面顺序 [terrain, map, ...]，
  前端「map 未到即丢」的合并把整局地形吞掉（与 B16 同一个顺序坑，sim 侧修过、driver 漏了）。
  双层修：driver 重排（sink 先发）+ store `pendingTerrain` 挂起（帧序无关，A5）。
  回归锁 ×3（driver 顺序 / store 乱序 / store 正序）。

- ~~实时驾驶：点「启动真机」没反应；时间线在动但地图空、却有来历不明的槽位~~ ——
  2026-08-21 修复（WORKLOG §0.11）。根因链：① `mapPlanId` 硬编码旧默认值 `"default"`
  （预设已改名 default-bl/tr）→ 后端 400「规划不存在」→ `sessionAction` 把失败静默吞成
  null，UI 无任何显形；② 失败后照常 attach("live")，而 WS 对 source=live 会**惰性建一个
  进程内假世界**——出厂模板槽位 + 逐秒假帧 + 无地形，正是「神秘槽位 + 空地图」。
  修法：pickMapPlan 兜底 + sessionAction 错误显形（{ok:false,detail}）+ 两段式确认替代
  window.confirm；WS 无会话改为合成 `frame/session`「未连接」并保持连接等会话启动
  （REST 同步改 404），假世界入口连根拔除（UI 沙盒按钮一并退役）。
- ~~「关闭游戏按不下，感觉一直关不上」~~ —— 2026-08-21：停止按钮原先
  `disabled={!live}`，会话子进程死/崩后按钮禁用，孤儿 SC2 无法从 UI 收尾。改为按驱动
  显式命名（「关闭真机」/「停止沙盒」）且会话存在即可点（stop 树杀幂等，对死会话无害）。
- ~~顶栏残留「游戏实时」下拉框~~ —— 旧 vite 包残留（源码与 git 历史均无此控件）；
  清掉 stale 前后端进程、重启服务后消失。同轮顺带：顶栏 状态/地图/游戏时间 三 Pill、
  驾驶态的 播放×4/暂停 一并摘除（信息由会话 label / 时间线 / 「回到实时」承载）。

- ~~live 会话期间 WS 断线行为未验证（I3）~~ —— 2026-08-21 修复并给出确切答案：审查确认代码里原本**没有任何**断线处理。
  WsFrameSource 加 onDisconnect/disconnected（三态语义：握手后 close=断线 / dispose 不算 / 握手失败不算）；
  store 加 disconnected + reconnect()；SessionBar 红色横幅 +「重连」按钮。测试 tests/ws.test.ts 4 条。见 commit 8569c47。

- ~~实时驾驶点击后全屏错误屏（近黑底红字）且无恢复入口~~ —— 2026-08-21 修复：
  无活跃会话时进 drive 模式不再 attach（顶栏提示「等待会话」）；全屏错误屏加「返回离线模式」按钮。
- ~~Flow 卡片文字重叠（条件长文本压到右侧去向；起点与活跃点重叠）~~ —— 2026-08-21 修复：
  branch 行改 foreignObject 左右分栏（左可换行 2 行截断、右固定不换行）；头部两行重排。

- ~~I5 生产页投影泳道与矿曲线时间轴不对齐；布局只往下摊~~ —— 2026-08-21 完成：
  曲线与泳道合进一张卡（charts/ProjectionBoard），共享同一条时间轴 —— 同一 `sharedDomain`
  喂给曲线（`ProjectionChart` 新增 `domain` prop 锁轴）与泳道（百分比定位），泳道轨道与
  uPlot 绘图区像素对齐（`PROJECTION_GUTTER` 单点定义，验收实测偏差 < 1px）。
  真泳道：每个 stable_id 一条道、同类并发错开子行、按首次出现紧排、删掉右侧时间列。
  顺手修了旧 Gantt 配对的字典序坑（"id:10" < "id:9"）。
  测试 web/tests/gantt.test.ts 16 条。详见 WORKLOG §0.1。

- ~~I6 真机首帧等待期没有状态提示（被误读为「地图黑屏没同步」）~~ —— 2026-08-21 完成：
  `shell/mode.ts` 新增 `bootHint(mode, sourceKind, sessionState, mapArrived)` 纯函数 +
  `shell/BootHint.tsx` 横幅；地图页（含无地图分支）与概览页顶部接入。
  drive + live + static/map 未到 + 会话非终态 → 提示「正在连接 SC2 并等待首帧（真机约需
  1-2 分钟）」，数据到达自动消失；已结束/崩溃/未连接不提示。
  测试 web/tests/mode.test.ts 扩 5 条。详见 WORKLOG §0.1。

## I11 顶层时间轴的归属与形态（已处理，2026-08-22 十四轮）

- **现状**（2026-08-22 用户提出）：顶层时间轴并非所有模块都用（地图规划完全
  不用），且占据顶部一级位置；已先做小（py-1）。
- **处理**（2026-08-22 外壳重构轮）：时间轴**下沉进主列**（ModeBar 之下、页面
  之上，不再横跨 rail/对话栏；左 rail 顶到视口顶）。形态收敛成细轨（h-7）：
  去卡片壳与图例行，回看态染粉 / 跟随态蓝，标记 title 悬停看全文。**规划模式
  整条不渲染**；复盘的源/夹具选择与 播放×4/暂停/回到实时 并入同一条带
  （TimeStrip），时间范围数字随行。**关闭。**

## I12 Agent 自述「写不了策略」——属 R5 设计边界；策略层有真实能力缺口待补

> 进展（2026-08-22 二十五轮）：**B2 已落地**（`AlertService.assembly_gaps` 干跑
> 终态对账装配 target，`/api/plans/simulate` 返回 assembly_gap 前瞻警报，
> 见「已处理」与 WORKLOG §0.28）。
> 进展（2026-08-23 二十六轮）：**B1 大半落地、B3 最小版落地**（WORKLOG §0.29）——
> B1：timer 族（start/stop_timer + timer_elapsed）+ locals 族（locals/set_local/
> {local}）+ engaged/under_attack（orders/射程/hp 历史推导）全部写读同批放行；
> 仍开放 `event_occurred`（要 D7 事件流）、`has_ready_base`（三族 town hall 目录）、
> `user_cancel`（接管通道）。B3：`GET /api/modules` + `POST /api/plans/from-module`
> 模板落地 + 前端「从模板落地」下拉 —— 模块成为唯一真相源。

- **现状**（2026-08-22 用户复盘）：Agent 在对话里自述「我无法写策略」，并列了
  几条理由（R5 规则、没有 write_strategy/edit_flow 工具、read_current_strategy
  只读、策略表达力弱、装配与规划无交叉校验、参考模块与规划是两套副本）。
  逐条核对代码，**Agent 说的全是事实，无幻觉**：
  - R5 规则真实存在：`docs/需求文档-v0.1.md:23`（`live 中不能创建/编辑模块与
    Strategy`），spec 提示词（`agent/spec.py:58`）、`agent_tools()` 的 rules
    清单（`modules/api/app.py:474`）、多处代码注释重申。
  - 工具清单里确实没有 `write_strategy` / `edit_flow`；`read_current_strategy`
    （`agent/tools.py:254-259`）只 dump 两个写死常量，`list_modules` / `read_module`
    （`:225-252`）只从 `MODULE_REGISTRY`（代码）导出，均只读。
  - 策略/装配根本不在文件工作区里：`ApiWorkspace._split`（`agent/workspace.py:74-84`）
    只认 `plans/` 与 `map-plans/`，其余路径落 scratch 自留地；运行期策略/装配是
    `modules/api/session.py:63-105` 的两个字符串常量 `DEFAULT_STRATEGY` /
    `DEFAULT_ASSEMBLY`，session 构造时直接 `parse_strategy(...)` 吃进去
    （`:156-157`），无 store、无文件路径、UI 也改不了。Agent 即使拿到 `write`
    工具也摸不到策略——路径上够不着。
  - `engaged` / `event_occurred` 确实待建：`predicates.py:65-72`
    `UNIMPLEMENTED_PREDICATE_OPS`，经 `agent_tools()` 的
    `unsupported.flow.predicates` 喂回 Agent；`enemy_has` / `building_completed`
    更是引擎根本不认识的 op，直接报 `unknown op`。
- **影响**：Agent 把两类性质不同的东西混在了一起，需要拆开判断——
  - **A 类｜设计边界（不是缺陷）**：「写不了策略」是 R5，是「生产顾问」这个角色
    的定义。Agent 的本职是生产规划，策略/装配留给人写。它把角色边界说成「断点」，
    是把自己的职责范围误当成系统缺陷。
  - **B 类｜真实能力缺口（该补）**：与谁来写策略无关，是策略层本身欠的账——
    1. 谓词表达力弱：`engaged`/`under_attack`/`has_ready_base`/`timer_elapsed`/
       `event_occurred`（`predicates.py:65-72`）+ 空间 `nearest_units`/
       `cluster_centers`（`:75-78`）全是 stub，编译/求值期直接拒；其写侧
       `start_timer`/`stop_timer`/`set_local`（`manifest.py:77-83`）同样待建。
       策略现在只能「攒够兵→走到点」。
    2. 装配与生产规划无交叉校验：装配 `composition.target=10` 但规划只造 4 个
       就转去造坦克——两者之间没有约束，规划不自动满足 target，装配也不反向告警，
       Agent 得自己两边肉眼对照。
    3. 参考模块与规划是独立副本会漂移：`MODULE_REGISTRY`（`planner/build_order`，
       代码）与 `plans/*.yaml`（文件）是两份内容近乎相同的独立副本，改一边不同步
       另一边。
- **候选方案**：
  - **A 类不取「放开 Agent 改策略」**：Agent 自提的第五条「让我有限度改策略参数
    （min_units、target 点位）」会直接违反 R5——那是改角色定义，不是修 bug。
    角色边界是否扩大是设计者（用户）的决策，不该由 Agent 自己提议放开。维持现状。
  - **B1 谓词补全**（高收益，难度中）：按「写侧与读侧一起还」的反模式守卫（D8），
    `timer_elapsed` + `start_timer`/`stop_timer`/`set_local`/`locals` 同批落地；
    `engaged`/`under_attack` 依赖 orders/攻击射程/hp 历史推导；
    `event_occurred` 依赖引擎事件流（GameEvent 目录 D7）。可分切片。
  - **B2 装配↔规划交叉校验**（高收益，难度中）：在 `simulate_plan` 或装配装载时
    加一层——组装的兵种 `target` 若规划队列的总产出达不到，发前瞻警报（沿用现有
    alerts 通道），让缺口显形而非静默。Agent 与人都不用肉眼盯。
  - **B3 模块模板化**（中收益，难度中）：把 `MODULE_REGISTRY` 的只读副本改成
    「可实例化的模板」——`read_module` 导出后可一键落到 `plans/<id>.yaml`，消除
    两份副本的漂移；或反过来校验模块与现有规划是否一致。
  - **附带（低收益，低难度）**：`read_current_strategy` 输出里标注哪些谓词/操作
    待建（已由 `agent_tools()` 的 `unsupported` 提供，可在策略转储里复述），省得
    Agent 去翻 `write_surface`。
- **建议归属**：A 类维持现状不开工；B1 归 flow 引擎能力切片（谓词三族：计时器/
  交火/事件）；B2 归 planner/assembly 装载校验（可并入现有 alerts 通道，独立小项
  可插队）；B3 归 planner 模块体系（依赖模板化设计，中期）。三条 B 互相独立，可
  拆成单独立项，本条先作为「角色边界 vs 真实缺口」的辨析留档。

## I13 二十六轮四个用户 issue（2026-08-23 当轮全部处理，留档）

- ~~① 对局记录没有保存~~ —— **属实**（此前 live 帧流只在内存 FRAME_BUFFER）。
  修：LiveSession 同步落 `runtime/recordings/rec-*.jsonl` + meta 侧车；
  `GET /api/recordings`（清单，录制中也列）+ `/api/recordings/{id}/jsonl`（帧流）；
  前端复盘下拉 = 夹具 + 📹 录像。测试默认不录，serve_api 传 runtime/recordings。
- ~~② 真机连接不是正常流速~~ —— 根因：`--realtime` 从未传给 run_session，
  burnysc2 realtime=False 时 step 不等墙钟、游戏时间飞跑。修：driver=sc2 默认
  realtime=True（sim 按原 tick 节拍不变）。
- ~~③ 复盘/规划图移动太快（拖一下十几分钟）~~ —— 根因：试算投影把 until_complete
  后的整局 horizon（30-60 分钟）塞初始窗口。修：initialDomain 退役，板默认
  zoomSpan 钳 ZOOM_SPAN_MAX=600s（数据仍完整，拖动/跟随看全程）。
- ~~④ 试算投影事件太密、泳道字看不见~~ —— 与③同根 + 密集渲染。修：bar 只渲染
  视窗内的；窄于 30px 隐藏文字（title 悬停看全名）。

## I14 二十七轮用户反馈（当轮处理，留档）

- ~~平移方向锁死~~ —— 真 bug：板子 pointerup 挂在元素上，图外松手收不到，
  陈旧 startX 把悬停也当拖动（方向恒定在锚点一侧）。修：setPointerCapture +
  左键校验（WORKLOG §0.30.1）。
- ~~默认窗口 10 分钟太长~~ → 5 分钟；~~右侧空白~~ → 右缘钳数据末端（与左缘
  零点对称）；仿真尾部 +30s（最后事件后的余势）。
- ~~复盘源三选项冗余~~ → 收敛成「对局记录」一条（时间 · 族 vs 族 · 地图 ·
  时长）；夹具退居规划模式内部数据源；真机假数据 enemy_race="protoss" 改为
  从首个可见敌方推导。
- ~~开放点① agent 写策略~~ —— **当轮拍板并落地（免审）**：策略从此是文件
  （runtime/strategies/<id>.yaml，strategy+assembly 两段），保存过全套编译期校验；
  agent 工作区 `strategies/` 可读写（ChangeRecord area=strategy）；会话按
  `?strategy=<id>` 装配（热改不存在 = 免审下的安全边界）；StartCard 加策略下拉。
  R5 的「live 中不能编辑 Strategy」语义改为「不能热改正在跑的会话」。
- 开放点② agent 常驻监听 → 见「开放任务清单」#1。

## I15 modules/ 代码债审计：god files / 死代码 / UI 假数据 bug

- **现状**（2026-08-23 全 67 文件 / ~1 万行只读审计）：底子过硬但已"长过头，不整洁"。
  分层干净（`game` 真叶子、无循环依赖）、docstring 普遍解释为什么、stub 老实标"待建"
  （全 modules 唯一假实现是 `driver/sc2_adapter.py:496` 的 `SC2GamePort.stop()`）。债集中在三块——
  - **真实 bug**（会出错误数据）：`view/producer.py:112` 网格只第一帧发之后不刷新、
    `view/adapt.py:267` 生产进度永远 `0.0`、`producer.py:206` `wall_ms` 伪造、
    `view/observe.py` 硬编码提案状态字符串（与 `proposals.STATUS_*` 脱同步隐患）、
    `planner/opening.py:17` vs `economy.py:24` CC 供给 15/13 自相矛盾、
    三族 catalog 扩到 174 条但 planner 仍只认 Terran。
  - **三个 god file**：`api/app.py`(998，单 930 行 `create_app` 闭包)、
    `production/runtime.py`(948，单 ~890 行类含 340 行建造飞行状态机)、
    `flow/manifest.py`(735，`validate_strategy` ~200 行 + params/variables 复制粘贴)。
  - **词法死代码**：一串死导出/死函数（`_p_group_center`/`nearest`/`cells_of_big`/
    `check_assign_workers`/`_seq`/`EnemyClusterView`/`curve` 三 helper/空 `UNIMPLEMENTED_*`
    dict），且 `constraint/__init__` 公开面倒挂（导出死的、没导出被 runtime 用的活的）。
- **影响**：god files 到了"动一下碰 900 行闭包、下一个人不敢重构"的程度；真实 bug 让
  UI/agent 拿假数据（网格陈旧、进度条永远 0%、诊断 wall_ms 是假的）；死代码与"不静默"
  红线精神相悖——抓得严的是语义死路，没清的是词法死代码。
- **候选方案**：全部 file:line + 拆分蓝图见 [`docs/REFACTOR.md`](REFACTOR.md)。按优先级——
  ① **P0 修 bug**：producer 网格 diff + progress 真值 + observe 改用 `STATUS_*` 常量 +
    CC 供给中央化（四条，不是清理是修 bug）。
  ② **P1 god file 拆分**：`app.py` 按路由组拆 routes/ + `state.py`；`runtime.py` 抽
    `build_flights`/`placement`/`drain`/`queue`；`manifest.py` 拆 `validate_*` + 抽声明块校验。
  ③ **P1 planner race-agnostic**：从 catalog capabilities 推导气矿/人口建筑，或老实标
    "投影仅 Terran"。
  ④ **P2 死代码清理 + 去重**：删一串死导出/死函数、把 `constraint/__init__` 公开面正过来、
    抽 `FrameSource` 基类干掉帧源三份复制。
- **建议归属**：P0 bug 项可拆单独立项即时修（不依赖重构）；god file 拆分是结构性的、建议
  作为执行轮后的独立重构批次，一次拆一个 file 跑回归；本条为审计索引，细节去 REFACTOR.md。

## I16 策略热改断层：生产层可动态调，战术层局中不可调

- **现状**（Agent 实测 + 代码核对 2026-08-23）：生产规划可随时改——`propose` 提 hunks，
  校验通过自动应用（基于 `based_on_seq`，落后 5 seq 拒）。但**策略层不可热改**：策略在
  会话启动时按 `?strategy=<id>` 装配（`modules/api/session.py:156-157` `parse_strategy`），
  跑起来后 step/branch/装配固定，换策略只能写新 `strategies/<id>.yaml` → 起新会话装配。
  I14 已把"策略文件可读写"落地但明示"热改不存在 = 免审下的安全边界"。ADR-0013/0014
  （docs/adr/ 与 docs/spec/）有 hot-swap/多实例状态保留设计但属未来 B15，未建。
- **影响**：局中"机枪不够该多出坦克"→ 提案改生产队列 ✅；局中"该从防守切进攻"→
  改不了 ❌。生产动态 vs 战术静态是个断层——Agent（及人）局中只能调"造什么"，
  调不了"部队怎么动"。侦查到对面出飞要转防空，能改生产多造对空，但"部队行为切换"
  控制不了。这是"跟游戏接入不够好"的一块（与开放任务清单 #1 常驻监听同源）。
- **候选方案**：
  1. **维持现状（当前设计）**——热改是复杂状态机问题（状态保留/回滚/多实例），ADR-0013/0014
     有设计但代价大；免审下"不能热改"是安全边界。可接受为中期缺口，不强行上。
  2. **有限热改参数（不改结构）**——允许局中改策略 `params`（如 `min_units`、`target`
     点位），不动 step/branch/edge。这是 Agent 最想要的窄口子。需设计"哪些 param 可热改
     + 改时运行态如何迁移"。I12-A 类当时否了"放开写策略"，但 I14 已放开策略文件写；
     局中参数热改是更窄、更安全的下一步。
  3. **多策略实例 + 运行时切换**（ADR-0013/0014 完整版）——B15 才动。
- **建议归属**：设计决策项，需用户拍板走哪条。短期维持现状；中期评估方案 2（参数级
  热改）。与 I17（死步骤检测）互补：热改落地前，至少让 Agent 能"看见"策略会死锁。

## I17 对局可观测性深度不足：警报无"怎么修" + 采气工不报 + 死步骤不报

- **现状**（Agent 实测 rush-bc 试算 + 代码核对 2026-08-23）：alerts 系统覆盖 8 种 kind
  （`modules/view/alerts.py`），但对局循环里几处关键缺口——
  - **警报不带"怎么修"**：`AlertView`（`modules/view/schema.py:713-722`）只有
    `id/kind/severity/at/eta/text_zh/source/payload`，**无 remediation/cause 字段**；
    `text_zh` 偶尔嵌软提示（`mineral_float` 写"看队首是否阻塞"）但非结构化。Agent 看到
    "装配缺口"但不知该改规划加机枪、降 `min_units`、还是换策略。
  - **采气工不足不报**：`assign_workers` 是目标配额（维持 N，`production/economy.py:108-119`
    + 模块 docstring），系统静默接受任何值。**无采气工/mining shortfall 警报**（唯一 gas
    警报是 `gas_float`——资源没花掉，正好相反）。Agent 实测采气工 3 vs 需要的 6 差距从没被警报。
  - **装配缺口只报终局无时序**：`assembly_gaps`（`alerts.py:166-201`）读 `curve.points[-1]`
    比 final vs target，`eta=None`，无"t=X 后 step Y 卡了"的时序信息；且**只在 simulate
    路径**（`app.py:649`），live 的 `producer.py:141 evaluate()` 不调它。
  - **策略-规划可行性交叉校验缺失**（I12-B2 的更深半边）：I12-B2 的终局 composition 缺口
    校验已建（`assembly_gaps`），但**没有策略 `when:` 分支可满足性 vs 规划产出的交叉检查**
    ——rush-bc（0 机枪）配 sandbox_hold（gather 等 6 机枪）→ gather 永不触发、部队死等，
    系统不报。flow 引擎每帧 `eval_when`（`engine.py:122-133`）但永不满足的分支只是不触发、
    无警报；唯一痕迹是 observe 策略段的"求值诊断"（`engine.py:256-258`），非警报。
  - **observe 队列在建项映射缺失**：生产段列了 queue item 的 index/op/status，但无"当前
    在建项"标记（head-gating 把已发项弹出队列，`schema.py:511-513`）；`in_flight` 段有
    `stable_id`/`frames_waited` 但不映射回 queue index。Agent 想"知道队列执行到第几项"得
    自己推。（注：per-group current/target 在 observe 部队段**有**，非缺口——纠正 Agent
    的担心。）
- **影响**：Agent 的理想循环（observe→看历史→判断一件事→propose→等下轮）依赖 observe
  足够细 + 警报够深。现在警报只报"发生了什么"、不报"怎么修"也不报"会死锁"，Agent 要
  自己从终局数字反推根因（为什么浮矿 2901、为什么采气工 3），断裂在"看见问题"到"知道
  改哪"之间——这正好是 I12-B2（交叉校验）当初想堵的缝，但只堵了终局半边。
- **候选方案**：
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
  5. **observe 队列在建项映射**（低难）——`in_flight` 回填 queue index，或队列标"在建"项。
- **建议归属**：1/2/5 低难可插队先做；3/4 是 I12-B2 的深化（交叉校验），归同一批。与 I16
  （热改）互补——热改落地前先让 Agent 能"看见死锁"。与 I15/REFACTOR B4（observe 状态
  字符串脱同步）同在 view 层，可同轮收。

## I18 Agent 坐标理解工具：框选二维网格（inspect_region）

- **现状**（Agent 自述 + 代码核对 2026-08-23）：Agent 看地图只能读一维坐标列表
  （build_slots / pos_marks 清单），要在脑里拼二维布局——给 50 个点拼不出"这片区域
  长什么样"。空间直觉缺失是"看不懂地图"的根因。Agent 提了三种读法（长表格 / 逐行
  x_y_info / 框选+二维网格），自评方式 3 是**唯一能获得空间直觉**的，前两者信息量一样
  但都得心算拼图。
- **可行性（已核对，数据齐备）**：每一层数据都现成，这是净新增渲染工具，非研究——
  - **可建造地形栅格**：terrain.json 的 `pathable`/`placeable`（`sc2_adapter.extract_map_info`
    返回、进 static 帧）；`tactical_map/pnggrid.py` 能解区域 authoring PNG。全图栅格，
    checked-in 数据，driver 无关。
  - **预设固定建造点**：`tactical_map/reserved.py:55 reserved_boxes(catalog)` → 134 块矩形
    `tl/br/kind/name/label_zh`，命名的 12 基地+24 气井（如"蓝方主矿气井1"）——正是 Agent
    最想标出的；`reserved_marks` → name→PosMark。
  - **建造槽位**：`tactical_map/placement.py BuildSlot(tl/br/size/kind/name/pos + contains(cell))`，
    map plan 装载后可查。
  - **战术标记点**：`PosMark(name/pos/description_zh)`。
  - **坐标约定已锁**：左下原点、`GridPos` 整数格、奇偶半格换算（`placement.py`，ADR-0027/0029
    真机锁定）——渲染器直接复用，勿在别处复制奇偶分支。
  - **无现成"按 bbox 查格点"端点/工具**——净新增，无破坏。
- **影响**：Agent 写生产规划 placement 时，I8 的"点名引用"（精炼厂→蓝方主矿气井1）已能
  用名字指预设点，但**没法看空间布局来决定新槽位摆哪**——得盲摆或反复试。补这个工具 =
  补上"空间直觉"，让 Agent 局中/离线都能"框一块→直接看图"。是"看懂地图"的根因修复。
- **候选方案（implementation-ready 设计）**：
  - **工具** `inspect_region`（`agent/tools.py` 加一个，thin，调后端）：
    - 入参：`bbox = [x1,y1,x2,y2]`（左下+右上）、`step`（格步长，默认 1；给 2 降密度）、
      `layers`（可选：`terrain`/`reserved`/`slots`/`marks`/`units`，默认前四者）
    - 输出：二维 markdown 网格，X 列头 + Y 行头，**Y 降序**（高 Y 在顶 = 俯视），格内短标签
  - **后端端点** `GET /api/map/region?x1=&y1=&x2=&y2=&step=&layers=`（`app.py` 加 thin route；
    **渲染逻辑下沉到 `tactical_map` 新文件（如 `region_view.py`），不进 app.py 闭包**——
    I15/REFACTOR 已警告 app.py 是 998 行 god file）
  - **标签词表**（映射现有 kind，不用裸坐标/数字——Agent 担心的"数字不好懂"正解）：
    | 标签 | 含义 |
    |---|---|
    | `·` / 空 | 可建造空地（placeable=1 且无占用）|
    | `✗` | 不可建造（地形障碍）|
    | `cc` | 基地槽（reserved base）|
    | `gas` | 气井（reserved geyser，带名：`gas(蓝方主矿气井1)`）|
    | `矿` | 矿脉（reserved mineral）|
    | `depot`/`rax`/`factory`/`starport` | 生产建筑槽（BuildSlot by kind）|
    | `addon` | 挂件预留槽 |
    | `mark:名` | 战术标记点（PosMark）|
    | 单位 stable_id | live 层（可选，仅 live 加）|
  - **范围建议** ≤10×10（Agent 偏好 5×5；大了退化成长表）；多次查询拼大局。
  - **静态层**（terrain+reserved+slots+marks）driver 无关，sim/offline/live 都能用；
    `units` 层仅 live 叠加。
  - **需确认的 UX 默认**（不阻塞，可后调）：`step` 默认 1 vs 2；`units` 层要不要第一版就做。
- **建议归属**：新增 agent 工具 + 后端端点（净新增、无破坏）。渲染器放 `tactical_map`
  （数据本来在这层，`reserved.py` docstring 已写"计算下沉 tactical_map"）；端点 thin。
  与 **I8**（点名引用）互补：名字指落点 + 网格看布局 = 完整空间表达。与 **I17**（可观测性）
  同源：让 Agent"看懂地图"是可观测性的空间半边。实现量中（thin 工具 + thin 端点 + 一个
  渲染器 ~80-120 行 + 几条测试）。

## I19 Agent 跨会话记忆：结构化分文件 + 自反思/改进建议流

- **现状**（Agent 自述 + 代码核对 2026-08-23）：Agent 记忆 = 单个 ad-hoc `memory.md`
  scratch（`agent/spec.py` 提示词："你的自留地：memory.md…开局先 read 它"）+ `analysis-*.md`，
  无结构无约定。混了不同生命周期的东西（用户偏好长期稳定 vs 对局复盘只增 vs 策略经验
  会修正），grep 一词同时命中策略经验和对局复盘，噪音大；矛盾/膨胀无机制控制。Agent 自述
  这"直接决定跨会话的智商上限"。
- **核查发现**（三处，影响方案）：
  1. **scratch 持久跨会话**——`modules/api/app.py:126` 把 `workspace_root=
     Path("runtime/agent-talk/workspace")` 传给 AgentTalk，磁盘稳定路径 → 4 文件记忆结构
     **可行**，`memory.md` 已在那儿持久化，只缺结构/约定。
  2. **孤儿基础设施**——后端已有一套 `agent_notes.jsonl` append-only 流（`agent/client.py:132
     note_save()` + `app.py:464-474` 后端端点 + `app.py:129` 路径装配），但 **Agent 工具集
     没有 note 工具**（`agent/tools.py` make_tools 无 note）→ `note_save` 是死的 client 方法，
     基础设施和 Agent 断了。一条本可用的反思通道没接上。
  3. **`system-capabilities` 数据源现成**——`write_surface` 调 `agent_tools()` 返回的
     `unsupported` 清单（含待建谓词/操作）就是能力的实时真相 → 应**派生而非手维护**。
- **影响**：跨会话学习断裂——单 `memory.md` 越长越难维护，每个新会话要么"失忆"要么被噪音
  淹没；孤儿 `notes.jsonl` = 反思通道空接；无自反思流 = Agent 撞墙/缺功能的发现无法沉淀回
  开发 backlog。
- **候选方案**（Agent 提 4 文件 + replays；本条补 3 点）：
  1. **结构化记忆（分文件，按生命周期隔离）**——Agent 方案合理，落地：
     - `memory/user-preferences.md`（短、稳定、开局 read）
     - `memory/strategy-notes.md`（带 ID `[E1]`/`[B2]`、≤2 行/条、定期合并去重；矛盾改旧条
       不新写；验证过的标"实测"）
     - `memory/system-capabilities.md`（可用/不可用/怪癖；**从 `write_surface` 派生/对账，
       非手维护**——系统更新自动反映，正解"系统更新后会变"）
     - `memory/replays/replay-NNN.yaml`（结构化复盘，只增；lesson 验证后迁移到 strategy-notes
       带 ID）
     - **开局只 read 前两个短文件**；strategy/replays/improvement 按需 grep——控 token。
  2. **新增 `improvement-notes.md`（Agent 自反思/改进建议流）**——用户要、Agent 没提：遇
     繁杂问题/撞墙/发现缺功能时写一条"撞了什么墙 + 系统该补什么"。**这是 Agent → 开发
     backlog 的反馈回路**：定期由人扫，该立项的进 ISSUES.md（I17/I18 即此回路促成）。格式
     同 strategy-notes（ID + ≤2 行）。
  3. **决定孤儿 `notes.jsonl` 去留**——要么给它接个 `remember` 工具（Agent 显式记反思），
     要么退役让结构化 scratch 文件统一承载。别留着断着。
  4. **搜索能力**——grep + glob + read + 分文件 = 精准隔离，无需新搜索工具。Agent 的搜索
     映射表（read 偏好/grep 经验/grep 复盘）成立。
- **建议归属**：记忆结构化是 Agent 自治范畴（scratch 文件约定 + 提示词指引，**不改后端**）；
  `system-capabilities` 派生 + `improvement` 流可在提示词 + 工具面落地；`notes.jsonl` 去留是
  小决策。与开放任务清单 #1（常驻监听）强相关——常驻循环靠记忆才有连续性，常驻前记忆要
  到位。**本轮只立项不写代码**（用户拍板）。

## I20 文件契约闭环：运行时产物（对局录像等）挂成 Agent 可读文件树

- **现状**（Agent 自述 + 代码核对 2026-08-23）：Agent 自述"不能追溯上一局，工作区无
  replay/session/history 目录，原始数据会话后消失"。**核对后部分纠正**——原始数据其实
  **已存在**：I13（二十六轮）已让 LiveSession 落盘 `runtime/recordings/rec-<stamp>-<driver>.jsonl`
  + meta 侧车（`modules/api/live.py:87,235`；`app.py:61 DEFAULT_RECORDINGS_DIR=runtime/recordings`），
  录像 = 完整帧 JSONL = 观察包序列/事件时间线的原始数据；且有 REST 端点 `GET /api/recordings`
  （清单，`app.py:705`）+ `GET /api/recordings/{rid}/jsonl`（完整帧流，`app.py:749`）。
- **真缺口不是"数据没有"，是"Agent 够不着"**（三处证据）：`agent/client.py` 无 recordings
  方法、`agent/tools.py` 无 recording/replay 工具、`agent/workspace.py` 的 `ApiWorkspace._split`
  只挂 `plans/`+`map-plans/`+scratch → `runtime/recordings/` **不在虚拟文件树**，Agent 的
  ls/read/grep 根本看不见录像。
- **同一模式第三次出现**：后端有数据+端点、Agent 工具面没接 → 孤儿。I19 的 `notes.jsonl`
  （`client.note_save` 有、agent 无工具）是第二次；recordings 是第三次。**根因 = 文件契约
  没闭环**：Agent 的文件世界是运行时文件世界的 ad-hoc 部分子集。
- **影响**：Agent 想复盘上一局（"t=251 二矿开建、t=300 被压制"这种精确数据）只能靠压缩前
  的对话记忆——压缩后丢。系统明明存了完整帧流，Agent read 不到。"看懂过去"和 I18"看懂
  地图"一样是断的。用户判断：**整个文件系统没嵌合**——模块各自以文件交付交互
  （plans/strategies/scratch 是 Agent 能碰的；recordings/notes/traces/history 是后端有但
  Agent 看不见的），没有"闭环的契约"让 Agent 浏览全部运行时产物。
- **候选方案**：
  1. **把运行时产物挂成 Agent 可读的虚拟文件树（推荐，契"Agent 以文件交互"愿景）**——在
     `ApiWorkspace` 加只读虚拟区：`recordings/`（ls 列清单、read 看 meta+帧摘要、grep 跨
     录像搜事件）、`notes/`、`traces/`。Agent 用现有 ls/read/grep 翻，**不新增 bespoke 工具**。
     这是"闭环"正解：文件契约统一承载，而非每个产物一个工具。
  2. **thin 工具兜底（次选）**——若挂文件树改动大，先加 `list_recordings`/
     `read_recording(rid, kind=summary|frames)` 工具 + client 方法。但 bespoke 会碎片化
     （跟 I19 notes 一样）。
  3. **`session/current.md` 短期层（归 I19 记忆栈，交叉引用）**——压缩前覆盖写"当前在
     干什么/改了什么/下一步"，下局开局 read 恢复连续性。是 I19 长期记忆之上缺的一层；
     I20 文件树做成后它自然落在 scratch 区。
  4. **统一原则**：Agent 的文件世界 = 运行时文件世界的可读视图（plans/strategies 可写，
     recordings/notes/traces 只读），而非 ad-hoc 部分子集。
- **建议归属**：I13 已建数据层；I20 是"把它接到 Agent"。挂虚拟只读区在 `agent/workspace.py`
  （+ 后端给录像 meta/帧摘要，或直接 read jsonl 文件）。与 I19（notes 孤儿同模式）、I18
  （Agent 触达数据同主题）同源——三件事都是"Agent 能不能够着系统已有的数据"，建议作为
  一批"文件契约闭环"一起做（四 issue 串成母题的专档见 [`AGENT-LOOP.md`](AGENT-LOOP.md)）。
  `session/current.md` 标 I19 扩展。**本轮只立项不写代码**。


## 开放任务清单（2026-08-23 二十七轮末快照；处理一条关一条）

> 用户要求：把所有未完成项集中立案于此。优先级 P0 > P1 > P2；标注建议切片。

1. **[P0] agent 常驻监听 + 主动行动（P3 尾款）**——对局期间 agent 自动观察
   （含「风险」警报）、主动提队列提案（校验通过即自动应用的通道已有）。
   §5.3 三护栏方向已定（R8 新鲜度 / 可审计 / 可撤销）；缺常驻触发循环与
   触发节流（别每帧一轮 LLM）。这是「跟游戏接入不够好」的最后一块。
2. **[P1] event_occurred / has_ready_base / user_cancel 谓词（I12-B1 剩余）**
   —— 依赖：引擎事件流（GameEvent 目录 D7）/ 三族 town hall 目录 / 用户接管通道。
   设计决策未做（事件从哪来：driver events vs 世界推导）。
3. **[P1] 策略编辑 UI（人用）**——策略文件已可读写（agent/REST/YAML），
   但人改策略还没有界面（规划-策略页只读图）。最小版：YAML 文本编辑器 +
   保存校验回显（像地图规划的 doc 编辑）；完整版 = F9 AST 编辑器（一直 next）。
4. **[P2] t=90 投影掉 draft 兜底的显示语义（I10 遗留）**——队列跑空后
   basic_opening 参考计划在已有建筑的世界里原样重放（再造 depot）。方向：
   参考计划按 gs 去重或换 bio_tank_opening。
5. **[P2] 回放向后拖大幅重置累积**——回放没有全量历史，WholeOpsList 只能向前
   累积。录像文件（二十六轮起有）本身就是全量 JSONL：复盘装载录像时直接
   全量进累积，重置消失。
6. **[P2] 槽位 placeable 后端校验收口**——terrain.placeable 栅格进摆放校验面
   （现在只查不压预留区/重叠）。
7. **[P2] 模块模板参数化 UI（B3 增量）**——from-module 端点已支持 params，
   前端「从模板落地」不带参数（marine_target/tank_count 调不了）。
8. **[P2] 组/槽位形状颜色标记（I4 候选 3）**——地图 chip 与策略图同词的视觉语言。
9. **[P2] docs/ 目录清理——已关闭（2026-08-23 §0.34）**：36 份 tracked superseded
   + 41 个扫描日志删；保留按活性引用驱动（契约真相源/真机证据日志/Flow v0.2 spec/
   精华 ADR）；文档地图见 [`DOCS.md`](DOCS.md)。代价：state_trace.jsonl 夹具被清
   （3 条驱动翻译测试 skip，下次真机 run_recorder.py 重新生成）。
10. **[P3] live 投影窗口语义**——live 仍是 120s 窗口投影；要不要像试算一样
    until_complete？（涉及 live 帧大小，需拍板）
11. **[P1] modules/ 代码债（I15 / [`REFACTOR.md`](REFACTOR.md)）**——**P0 bug 批
    （B1-B5+B8）已修完（§0.32）；god files 全部拆完：G1(`app.py`)/G3(`manifest.py`)
    §0.33，G2(`runtime.py`→编排+flights+placement）§0.34**；剩：B6（planner 仍
    Terran-only）+ B7（命令返回 shape 不一致）+ 死代码清理 + 去重（§3/§4）。
12. **[P1] 对局可观测性深度（I17）**——警报加 `remediation_zh` 字段（"怎么修"）+
    采气工 shortfall 警报 + 策略死步骤检测（I12-B2 深化：`when:` 可满足性 vs 规划产出）
    + 装配缺口时序化/live 化 + observe 队列在建项映射。1/2/5 低难可插队先做。
13. **[P1→已拍板（2026-08-23）] 策略热改断层（I16）**——方向定为**热切 V1**：
    整份策略文件切换 + 同装配约束 + step_id 续位（比参数级强、比完整多实例简）。
    落地计划见 [`PLAN-TEMPLATES-HOTSWAP.md`](PLAN-TEMPLATES-HOTSWAP.md) 批 C。
14. **[P1] Agent 坐标理解工具 inspect_region（I18）——已关闭（2026-08-23 §0.36）**：
    用户拍板**文件形态**（不做独立工具/端点）——`maps/<源>/<bbox>.md` 只读虚拟路径
    （渲染器 tactical_map/region_view.py，一格一词 ≤3 字符），live 源 = 会话当前
    地图规划。词表与路径约定在 maps/index.md + 提示词 + write_surface readable。
15. **[P1] Agent 跨会话记忆（I19）**——**结构与种子全齐（§0.33 约定 + §0.36 补全：
    strategy-notes 4 条 / system-capabilities 派生快照 / replays/ / improvement-notes）**。
    **剩余**：约定靠 agent 自觉执行，实际效果待观察（几局后校验是否真读真写、
    派生是否对账）——不行再上机制（开局自动 seed 检查）。与 #1 常驻监听强相关。
16. **[P1] 文件契约闭环（I20）——已关闭（2026-08-23 §0.33）**：只读区落地
    （`agent/readonly.py`：recordings 索引+每局衍生摘要、traces 白名单、
    proposals/log.jsonl）+ 录像摘要渲染器（`view/recap.py`，收尾自动落盘 + 懒生成）
    + write_surface `readable` 清单 + write 拒绝与 scratch 遮蔽防护。后续新产物
    过 AGENT-LOOP §6 的四项闭环检查清单即可。
17. **[P2] 开局工人口径：真机 8 工 vs 种子 12 工（B5 附带发现）**——2026-08-23
    真机录像首帧是 **8 工/13 cap**（1 CC），而种子口径（planner.opening /
    worldsim.bootstrap / session 默认）全是 12 工。供给值已单源修正为 13（WORKLOG
    §0.32），但工人数是另一处 sim/真机偏差：干跑投影的经济曲线会比真机乐观。
    需拍板：把种子改成 8 对齐本机，还是先确认真机地图/模式是否非标准开局（改动会
    波及全部干跑数字与夹具，宜单独一批）。
