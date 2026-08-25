# 问题清单 · 已处理与垃圾箱（留档）

> 2026-08-25 从 ISSUES.md 分出：**已完成的条目**与**被推翻/失效的描述**。
> 开放项见 [ISSUES.md](ISSUES.md)。每条保留原文要点 + 关闭时的去向（WORKLOG § / commit / ADR）。
> 本次核对方式：逐条对代码（file:line）与 WORKLOG 核验，不是只看文档自述。

---

## 一、本次核对时关闭（此前已完成但未标记）

- **原清单#1 agent 常驻监听 + 主动行动（P0）** —— **实已建成**（旧描述「缺常驻触发循环与
  触发节流」失效）：sleep 工具（§0.45，`agent/tools.py:530`）+ 对局跟随循环（§0.47「游戏
  没结束不许停」，`agent/talk.py:462-504` `_round_with_follow`，FOLLOW_MAX_ROUNDS=200）+
  警报唤醒（§0.55 D 批：warn+ 警报叫醒 sleep，同一 id 只叫一次，`tools.py:604-614`）+
  节流（0.5s 轮询 / 单次 sleep 300s 墙钟 / 时钟冻结兜底）。2026-08-25 对局里 agent 的
  sleep→observe→propose 自主循环即此机制在工作（I25 事故反证它跑起来了）。
  残项（有需要再立项）：现为 nudge 续轮而非纯事件驱动。
- **原清单#4 t=90 投影掉 draft 兜底的显示语义（I10 遗留）** —— §0.52 修复：`_live_seen` 门
  （`modules/view/producer.py:173-199`），见过 live 队列后**永不回退参考计划**，空队列投空、
  在途项从世界 build_progress 派生。刻意不按原条目提的「gs 去重」——WORKLOG 明示会让成本
  双扣 + 累计泳道重复条。
- **原清单#5 回放向后拖大幅重置累积** —— 症状已结构性消除：2026-08-24 复盘改版把 F17 跨帧
  累积整体退役（每帧只画当前投影 [T, T+horizon]，换帧整体重排、无历史叠加；
  `web/src/pages/ProductionPage.tsx:162`、`web/tests/layout.test.ts:506` 锁死）。原候选方案
  「录像全量 JSONL 进累积」未采用（见垃圾箱）。
- **原清单#9 docs/ 目录清理** —— 2026-08-23 §0.34 已关闭：36 份 tracked superseded + 41 个
  扫描日志删；保留按活性引用驱动。代价：state_trace.jsonl 夹具被清（3 条驱动翻译测试
  skip，下次真机 run_recorder.py 重新生成）。文档地图见 [`DOCS.md`](DOCS.md)。
- **原清单#13 I16 策略热改断层** —— 2026-08-23 §0.41 热切 V1 落地（详见下方 I16 节）。
- **原清单#14 I18 Agent 坐标理解** —— 2026-08-23 §0.36 落地（详见下方 I18 节）。
- **原清单#16 I20 文件契约闭环** —— 2026-08-23 §0.33 落地（详见下方 I20 节）。
- **I17 子项 5「observe 队列在建项映射」** —— 已落地（2026-08-25 核对确认）：REV16
  `InFlightView.from_index`（emit 时回填队列下标）+ REV18 `uid`/四态 `status`
  （pending/in_progress/completed/skipped，ADR-0032 账本，BUILD 项 emit 即标执行中不再弹出
  队列）。observe 段把队列 status 与 in_flight（带 uid）都摊开，agent 能答「队列执行到第
  几项」。原「缺失」描述失效。

## 二、2026-08-25 核对前已标记关闭的条目（自 ISSUES.md 原样移入）

### I7 投影板与规划页的 UI 债（已处理，2026-08-22）

- **现状**（2026-08-21 四轮反馈）：①泳道图放大后出现左右滚动条（不应出现）；
  ②大量窗口自适应高度，拖时间轴时布局跳动过大；③地图规划左侧栏偏大。
  另：规划页与生产页曾是两套图表组件（ProjectionPairChart vs ProjectionBoard）——
  已在 P0 收敛为同一组件（双板 ProjectionBoard），此项关闭。
- **处理**：①P1 轮修（泳道固定 140px + overflow-x-hidden）；②概览经济数字
  tabular-nums（2026-08-22 十四轮），生产页三区固定后已基本消；③2026-08-22
  十四轮左抽屉化解决（工具/列表进可收起的抽屉，主区只剩画布）。**关闭。**

### I8 生产规划的 placement 引用了具体地图，规划却可能跨地图（已处理）

- **现状**（2026-08-21 用户指出）：生产规划的 build 项 placement 下拉引用**当前帧源地图**
  的槽位/点位/区域名；但规划是独立文件、可能跨地图使用。换一张地图，placement 名字就对不上。
- **处理**（2026-08-22，P3 切片 3 / WORKLOG §0.13）：「规划id/点位名」限定引用落地 ——
  REST 命令面映射（会话装的是该规划就剥前缀，装的不是/没装 → 400 带理由，
  ViewFrame 零改动）；预设固定建造点 36 名进会话 layer.pos_marks（全局命名空间，
  裸名即可引用：精炼厂→蓝方主矿气井1）；编辑器「放置引用」下拉按所选地图规划
  过滤选项。后续演进见 ADR-0033（地图规划双分支 + 放置近似 SlotPool）。**关闭。**

### I11 顶层时间轴的归属与形态（已处理，2026-08-22 十四轮）

- **处理**（2026-08-22 外壳重构轮）：时间轴**下沉进主列**（ModeBar 之下、页面
  之上，不再横跨 rail/对话栏；左 rail 顶到视口顶）。形态收敛成细轨（h-7）：
  去卡片壳与图例行，回看态染粉 / 跟随态蓝，标记 title 悬停看全文。**规划模式
  整条不渲染**；复盘的源/夹具选择与 播放×4/暂停/回到实时 并入同一条带
  （TimeStrip），时间范围数字随行。**关闭。**

### I12 Agent 自述「写不了策略」——属 R5 设计边界；策略层有真实能力缺口待补（辨析留档）

> **状态（2026-08-25 核对）**：B2 已落地（§0.28 assembly_gaps 终局对账）；B3 已落地含参数
> （`/api/plans/from-module` 支持 params + 前端「从模板落地」入口在）；B1 大半落地（§0.29
> timer 族/locals 族/engaged/under_attack 写读同批放行，`UNIMPLEMENTED_DO_OPS` 已空）。
> **剩余**：event_occurred / has_ready_base / user_cancel 三谓词（在 ISSUES 开放清单）。
> **A 类「维持现状不放开策略写」结论已被 I14 推翻**（同日稍晚拍板免审开放策略文件写，
> 见文末垃圾箱）。本条作为「角色边界 vs 真实缺口」辨析留档，原文如下。

- **现状**（2026-08-22 用户复盘）：Agent 在对话里自述「我无法写策略」，并列了
  几条理由（R5 规则、没有 write_strategy/edit_flow 工具、read_current_strategy
  只读、策略表达力弱、装配与规划无交叉校验、参考模块与规划是两套副本）。
  逐条核对代码，**Agent 说的全是事实，无幻觉**（工具清单只读、策略不在文件工作区、
  `UNIMPLEMENTED_PREDICATE_OPS` 真实存在等，细节见原文）。
- **影响**：Agent 把两类性质不同的东西混在一起——
  - **A 类｜设计边界（当时判断）**：「写不了策略」是 R5、是「生产顾问」角色定义。
  - **B 类｜真实能力缺口**：①谓词表达力弱（timer/engaged/under_attack/event_occurred…）；
    ②装配与生产规划无交叉校验；③参考模块与规划是独立副本会漂移。
- **候选方案**（去向）：B1 谓词补全（剩余三谓词在开放清单）；B2 装配↔规划交叉校验
  （已落地，深化项归 I17）；B3 模块模板化（已落地）。

### I13 二十六轮四个用户 issue（2026-08-23 当轮全部处理，留档）

- ~~① 对局记录没有保存~~ —— **属实**（此前 live 帧流只在内存 FRAME_BUFFER）。
  修：LiveSession 同步落 `runtime/recordings/rec-*.jsonl` + meta 侧车；
  `GET /api/recordings`（清单，录制中也列）+ `/api/recordings/{id}/jsonl`（帧流）；
  前端复盘下拉 = 夹具 + 📹 录像。测试默认不录，serve_api 传 runtime/recordings。
- ~~② 真机连接不是正常流速~~ —— 根因：`--realtime` 从未传给 run_session，
  burnysc2 realtime=False 时 step 不等墙钟、游戏时间飞跑。修：driver=sc2 默认
  realtime=True（sim 按原 tick 节拍不变）。
- ~~③ 复盘/规划图移动太快（拖一下十几分钟）~~ —— 根因：试算投影把 until_complete
  后的整局 horizon 塞初始窗口。修：initialDomain 退役，板默认
  zoomSpan 钳 ZOOM_SPAN_MAX=600s（数据仍完整，拖动/跟随看全程）。
- ~~④ 试算投影事件太密、泳道字看不见~~ —— 与③同根 + 密集渲染。修：bar 只渲染
  视窗内的；窄于 30px 隐藏文字（title 悬停看全名）。

### I14 二十七轮用户反馈（当轮处理，留档）

- ~~平移方向锁死~~ —— 真 bug：板子 pointerup 挂在元素上，图外松手收不到，
  陈旧 startX 把悬停也当拖动。修：setPointerCapture + 左键校验（WORKLOG §0.30.1）。
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
  （后续热切 V1 已落地，见 I16。）

### I15 modules/ 代码债审计：god files / 死代码 / UI 假数据 bug（审计索引留档）

> **状态（2026-08-25 核对）**：审计指出的 **P0 bug 批已修**（commit `6e7370f`：producer 网格
> diff / progress 真值 / wall_ms 真时钟 / observe 改用 STATUS_* 常量 / CC 供给单源=13）；
> **三个 god file 全拆完**：G1 `app.py`+G3 `manifest.py` = `e13ca82`（app.py 998→薄装配+
> state.py+routes/×11），G2 `runtime.py` = `1316232`（948→564 编排+flights.py+placement.py）。
> **剩余**：B6（planner 仍 Terran-only）+ B7（命令返回 shape 不一致）+ 死代码清理 + 去重
> ——在 ISSUES 开放清单与 [`REFACTOR.md`](REFACTOR.md)。
> 另：审计原文 B5「真实 LotV 是 11」经查不适用于本机，已修正为 13（本机 game_data_dump
> + 真机录像实证，见 REFACTOR.md B5 行）。原文要点留档如下。

- **现状**（2026-08-23 全 67 文件 / ~1 万行只读审计）：底子过硬但已"长过头"。
  真实 bug（producer 网格陈旧 / progress 恒 0 / wall_ms 伪造 / observe 硬编码状态串 /
  CC 供给 15/13 自相矛盾 / planner 仍只认 Terran）；三个 god file（app.py 998 /
  runtime.py 948 / manifest.py 735）；一串词法死代码 + `constraint/__init__` 公开面倒挂。
- **影响**：god files「动一下碰 900 行闭包」；真实 bug 让 UI/agent 拿假数据；死代码与
  「不静默」红线精神相悖。
- **候选方案与去向**：全部 file:line + 拆分蓝图见 [`REFACTOR.md`](REFACTOR.md)。①P0 修 bug
  → 已修（6e7370f）；②god file 拆分 → 已拆完（e13ca82 / 1316232）；③planner
  race-agnostic → B6 仍开放；④死代码清理 + 去重 → 仍开放。

### I16 策略热改断层：生产层可动态调，战术层局中不可调（已关闭）

> **已关闭（2026-08-23 §0.41）**：热切 V1 落地——`POST /api/session/swap`（整份切换 +
> 同装配约束 + 同名 step 续位，offline/sim/sc2 全支持，sc2 走控制文件通道）；转移历史记
> swap 事件、observe 可见。决策留档 [`adr/0031-编译期模板展开.md`](adr/0031-编译期模板展开.md)。
> 明确不做：装配热切（组结构装配期固定）、参数级热改、多实例并行。
> 原三候选（维持现状/参数级热改/多实例）辨析原文留档如下。

- **现状**（2026-08-23 核对）：生产规划可随时改（propose hunks 免审自动应用），策略层
  不可热改——会话启动按 `?strategy=<id>` 装配，跑起来后固定。I14 已把「策略文件可读写」
  落地但明示「热改不存在 = 免审下的安全边界」。ADR-0013/0014 有设计但属未来 B15。
- **影响**：局中「该从防守切进攻」改不了——生产动态 vs 战术静态断层。

### I18 Agent 坐标理解工具：框选二维网格（已关闭）

> **已关闭（2026-08-23 §0.36）**：用户拍板**文件形态**（不做独立工具/端点）——
> `maps/<源>/<bbox>.md` 只读虚拟路径（渲染器 `tactical_map/region_view.py`，一格一词
> ≤3 字符），live 源 = 会话当前地图规划。词表与路径约定在 maps/index.md + 提示词 +
> write_surface readable。原方案（inspect_region 工具 + `GET /api/map/region` 端点）
> **未采用**，原文留档如下（标签词表/范围建议等设计被文件形态继承）。

- **现状**（2026-08-23）：Agent 看地图只能读一维坐标列表，空间直觉缺失是「看不懂地图」
  的根因；自评「框选+二维网格」是唯一能获得空间直觉的读法。
- **可行性**：数据全齐（terrain pathable/placeable 栅格、reserved 134 块矩形、BuildSlot、
  PosMark、坐标约定已锁 ADR-0027/0029）——净新增渲染工具，非研究。

### I19 Agent 跨会话记忆：结构化分文件 + 自反思/改进建议流（主体已落地）

> **主体已落地**：§0.33 约定 + §0.36 补全（memory/ 四文件：user-preferences /
> strategy-notes / system-capabilities（派生）/ replays/ + improvement-notes.md）+ §0.42
> 记忆种子（`agent/seeds` 只补缺失）+ 提示词六条整改。
> **剩余**（在 ISSUES 开放清单「I19 记忆效果观察」）：约定靠 agent 自觉执行，几局后校验
> 是否真读真写、派生是否对账——不行再上机制（开局自动 seed 检查）。
> 另：孤儿 `notes.jsonl` 去留（原方案 3）**仍未拍板**——后端 `note_save` 端点还在
> （`routes/agent.py:153`），Agent 工具面仍无 note 工具，链路仍断着。

- **现状**（2026-08-23）：Agent 记忆 = 单个 ad-hoc `memory.md`，混了不同生命周期的东西，
  grep 噪音大、矛盾/膨胀无机制控制；后端有孤儿 `agent_notes.jsonl` 流但 Agent 没工具；
  `system-capabilities` 应从 write_surface 派生而非手维护。
- **候选方案**（1 结构化分文件 / 2 improvement-notes 反思流 / 3 notes.jsonl 去留 /
  4 搜索能力用 grep+glob 即可）：1/2/4 已落地，3 待拍板。

### I20 文件契约闭环：运行时产物挂成 Agent 可读文件树（已关闭）

> **已关闭（2026-08-23 §0.33）**：只读区落地（`agent/readonly.py`：recordings 索引 + 每局
> 衍生摘要、traces 白名单、proposals/log.jsonl）+ 录像摘要渲染器（`view/recap.py`，收尾
> 自动落盘 + 懒生成）+ write_surface `readable` 清单 + write 拒绝与 scratch 遮蔽防护。
> 后续新产物过 [`AGENT-LOOP.md`](AGENT-LOOP.md) §6 的四项闭环检查清单即可。
> ⚠️ 后续教训（§0.39 事故）：traces 递归白名单 × 引擎每轮快照 = 指数膨胀到 4.2GB →
> MemoryError 假停滞。修法 = 顶层小文件 + 2MB 上限 + snapshot 只快照 scratch（§0.38
> 看门狗配套）。

- **现状**（2026-08-23）：I13 已让录像落盘 + REST 端点，但 Agent 够不着（client 无方法、
  工具面无工具、`_split` 只挂 plans/map-plans/scratch）——「同一模式第三次出现」，
  根因 = Agent 文件世界是运行时文件世界的 ad-hoc 部分子集。
- **方案**（已按推荐落地）：只读虚拟区 recordings/ + notes/ + traces/，Agent 用现有
  ls/read/grep 翻，不新增 bespoke 工具。

### 早期已处理条目（2026-08-21/22，原 ISSUES.md「已处理（留档）」整块移入）

- ~~I1 谓词/操作符没有中文别名~~ —— 2026-08-22 二十五轮关闭（WORKLOG §0.28）。
  `flow.vocab` 增 PREDICATE_ZH/OPERATOR_ZH/ACTION_ZH，`dump_vocabulary()` 每项带
  `name_zh`（契约 rev 12），前端 `renderValue` 经 `vocabOf(schema)` 渲染中文谓词/运算符。
  覆盖性测试锁死。
- ~~I2 step_id / 策略名是裸英文标识符~~ —— 2026-08-22 二十五轮关闭。
  manifest 增 display_name_zh/description_zh（策略级 + step 级）+ reasons 中文表；
  assembly GroupSpec 增 display_name_zh。前端节点/边标签/转移历史中文优先。
- ~~I4 面板可读性：标识符前缀与组名没有解释~~ —— 2026-08-22 二十五轮关闭：
  Card `help` prop + 前缀解释 + 组显示「步兵组（G_INF）」。候选 3（组/槽位形状颜色
  标记）未做，单开小项（在 ISSUES 开放清单）。
- ~~I9 敌方非人族单位/建筑显示「未知」~~ —— 2026-08-22 三族 catalog 落地解决
  （protoss/zerg 数据 + `load_all` 全链路换，commit ba8ceb5）。
- ~~I10 「开局90秒（再生产链）」夹具的投影泳道并行度可疑~~ —— 2026-08-22 二十五轮
  关闭：真根因 = WorldSim `_op_train` 无产槽占用检查 + planner 快照在途推进同样无槽
  并行。双层修（`_train_busy` 产槽排队 + `_advance_in_flight` 对齐 B15 slot_capacity）。
  遗留小项（t=90 draft 兜底显示语义）后由 §0.52 `_live_seen` 修复。
- ~~I12 之 B2：装配与生产规划无交叉校验~~ —— 2026-08-22 二十五轮落地：
  `AlertService.assembly_gaps` 干跑终态对账 composition.target，`/api/plans/simulate`
  返回 assembly_gap 前瞻警报。深化（时序化/live 化）归 I17。
- ~~真机（sc2）地形不可用：地图页纯色底~~ —— 2026-08-22 修复（WORKLOG §0.12）：
  driver 地形回调顺序坑 + store `pendingTerrain` 挂起（帧序无关）。
- ~~实时驾驶：点「启动真机」没反应；时间线在动但地图空、却有来历不明的槽位~~ ——
  2026-08-21 修复（§0.11）：mapPlanId 硬编码旧默认值 → 400 被静默吞 + WS 惰性假世界。
  修法：pickMapPlan 兜底 + sessionAction 错误显形 + 两段式确认；假世界入口连根拔除。
- ~~「关闭游戏按不下，感觉一直关不上」~~ —— 2026-08-21：停止按钮原先
  `disabled={!live}`，会话死后禁用。改为按驱动显式命名且会话存在即可点（stop 幂等）。
- ~~顶栏残留「游戏实时」下拉框~~ —— 旧 vite 包残留；清 stale 进程后消失。同轮顺带
  顶栏收敛。
- ~~live 会话期间 WS 断线行为未验证（I3）~~ —— 2026-08-21 修复：WsFrameSource
  onDisconnect/disconnected 三态语义 + store reconnect + 红横幅（commit 8569c47）。
- ~~实时驾驶点击后全屏错误屏且无恢复入口~~ —— 2026-08-21 修复：无活跃会话不 attach
  + 「返回离线模式」按钮。
- ~~Flow 卡片文字重叠~~ —— 2026-08-21 修复：branch 行 foreignObject 左右分栏。
- ~~I5 生产页投影泳道与矿曲线时间轴不对齐~~ —— 2026-08-21 完成：合进 ProjectionBoard
  共享时间轴，像素对齐（WORKLOG §0.1）。
- ~~I6 真机首帧等待期没有状态提示~~ —— 2026-08-21 完成：`bootHint` 纯函数 + BootHint
  横幅（WORKLOG §0.1）。

---

## 三、垃圾箱（描述已被推翻或失效，仅留档，勿再引用）

- **I12-A 类结论「不取放开 Agent 改策略，维持现状」** —— **被 I14 推翻**：2026-08-23
  同日稍晚用户拍板免审开放策略文件写（runtime/strategies/*.yaml + 工作区 strategies/
  可读写 + `?strategy=<id>` 装配），R5 语义改为「不能热改正在跑的会话」。I12 当时的
  「A 类不取」段落已过时；B 类缺口辨析仍有效（B2/B3 已落地，B1 剩三谓词在开放清单）。
- **原清单#5 候选方案「录像全量 JSONL 直接全量进累积」** —— **未采用，方案失去对象**：
  2026-08-24 复盘改版把 F17 跨帧累积整体退役（每帧只画当前投影窗口），「向后拖重置
  累积」的症状不复存在，也就没有「全量进累积」可做。
- **原清单#1 描述「缺常驻触发循环与触发节流（别每帧一轮 LLM）」** —— **描述失效**：
  sleep/跟随/警报唤醒/节流已于 2026-08-24 建成（见上文已处理节）。条目本体移入已处理。
- **I17 子项 5 描述「observe 队列在建项映射缺失」** —— **描述失效**：REV16/REV18 已落地
  （from_index + uid + 四态 status）。条目本体移入已处理。
