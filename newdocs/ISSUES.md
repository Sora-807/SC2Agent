# 问题清单（计划外发现，待处理）

> 记录用户实测提出、但 F10-F14 计划**没有覆盖**的问题。每条带现状、影响、候选方案与建议归属。
> 处理一条删一条（或在 PLAN.md 立项后注明去向；已完成的移入文末「已处理（留档）」）。
> 记录时间：2026-08-21（F12 验收反馈）。

---

## I1 谓词/操作符没有中文别名，策略图条件难读

- **现状**：`frame/flow` 分支条件与 `static/strategy` 的 branches 值树里，谓词是裸标识符
  （`group_count(group=inf)`、`arrived(group=armor, target=别名.front, radius=3.5)`、
  `>= / 且 / 或`）。前端 `graph/ast.ts` 的 `renderValue` 只做了「结构 → 文本」映射
  （中缀、命名参数、中文引用标注），**没有谓词级中文**。
- **用户原话**：像 group_count、arrived 这些不好读。
- **影响**：不懂 DSL 的人读不懂策略图上的条件 chip；可读性是 F12 的验收目标之一，这一块没达标。
- **候选方案**：
  1. **后端词表加 zh 别名（推荐）**：`flow.vocab.dump_vocabulary()` 的 predicates/operators
     每项加 `name_zh`（如 `group_count` → 组内数量、`arrived` → 已抵达、`>=` → ≥）。
     前端 `renderValue` 读 `static/schema` 里的别名渲染。符合 U6/C4（zh 文案单一真相源在后端）；
     同时喂 agent 提示词卡片（词表三消费方自动受益）。契约影响：`static/schema` 内容变化
     但**字段结构没变**（predicates 的 value 里多一个键）→ 按 rev 5 的先例（开放分组表），
     值内加键走 REV+1 稳妥。
  2. 前端硬编码别名表 —— 违反 C4（第二个命名真相源），**不取**。
- **建议归属**：后端小项（B 系列新编号），与 F12 前端渲染改 `renderValue` 一轮做完。

## I2 step_id / 策略名是裸英文标识符，读不出意图

- **现状**：`formup / advance / garrison / armor_hop / inf_hop`、策略 `formup_probe /
  leapfrog_probe`。`static/strategy` 没有任何人类可读描述字段；YAML authoring 侧
  （flow/manifest.py）也没有 description。
- **用户原话**：formup advance 这些让人读不懂，也不知道这个策略是干什么的。
- **影响**：策略图节点标题、Flow 页标题、agent 观察包里的策略引用全都不可读；
  「驾驶舱是和 agent 商量打法的地方」（U12）的基础是人说得清策略名。
- **候选方案**：
  1. **manifest 加 `display_name_zh` / `description_zh`（step 级 + 策略级），随
     `static/strategy` 下发（推荐）**：authoring YAML 里写「装甲前压 / 步兵跟进」这类
     意图描述；前端节点头部/页面标题优先显示 zh，identifier 降级为小字或 tooltip。
     契约影响：`static/strategy` 加字段 → REV+1，可与 I1 同轮做。
  2. 前端维护 id→zh 映射 —— 同 I2 违反 C4，不取。
  3. 只改 authoring 侧命名规范（起更语义化的 id）—— 治标，且 id 改名会破坏既有引用。
- **建议归属**：涉及 flow/manifest.py + view/schema.py + 前端 FlowPage/PlanningPage，
  建议与 I1 合并成一个「策略可读性」B 项（一次 REV+1）。

## I4 面板可读性：标识符前缀与组名没有解释，缺「怎么看这张表」的入口

- **现状**：策略图条件里的 `参数.min_units / 变量.x / 别名.front` 前缀、
  「绑定与参数」面板里的 `G_INF 机枪兵 10/10`（G_INF 是 flow 分组 id；10/10 是
  current/target）、`槽位 main→G_INF` 这类展示都是裸标识符，没有任何解释入口。
- **用户原话**：G_INF 是一个组吗？要不要特殊的标记代表不同判断，
  或者增加一个 ？ 图标介绍这个面板怎么看，或者给策略语言加中文别名。
- **候选方案**（不互斥，建议组合）：
  1. **面板标题旁加 ？ 帮助图标（hover/tip：本面板每个字段怎么读）** —— 纯前端，便宜，
     先做；
  2. **与 I1/I2 同轮的中文化**：词表 zh 别名（参数/变量/别名前缀换成「参数 min_units」
     这类更自然的写法）+ 组名/槽位的 zh（assembly 的 group_id 加 display_name_zh，
     随 static/strategy 下发）；
  3. 组/槽位在 UI 上加形状或颜色标记（与地图 chip 的组标签同词，U17 已有此意图）。
- **建议归属**：1 独立小项可插队；2、3 并入 I1/I2 的「策略可读性」轮。

## I7 投影板与规划页的 UI 债（用户明确可延后）

- **现状**（2026-08-21 四轮反馈）：①泳道图放大后出现左右滚动条（不应出现）；
  ②大量窗口自适应高度，拖时间轴时布局跳动过大；③地图规划左侧栏偏大。
  另：规划页与生产页曾是两套图表组件（ProjectionPairChart vs ProjectionBoard）——
  已在 P0 收敛为同一组件（双板 ProjectionBoard），此项关闭。
- **用户态度**：能用，留到后续解决。
- **建议归属**：P1 导航重构顺带（布局结构会重排，先修这些会做两遍）。

## I8 生产规划的 placement 引用了具体地图，规划却可能跨地图

- **现状**（2026-08-21 用户指出）：生产规划的 build 项 placement 下拉引用**当前帧源地图**
  的槽位/点位/区域名；但规划是独立文件、可能跨地图使用（地图有好几份，规划不一定
  对应正在看的那张）。换一张地图，placement 名字就对不上。
- **进展**（同日 P2 切片 1）：地图规划已是后端文件（默认锁定 + 复制新建 + 出生点分支），
  会话装配已接 `?map_plan=`；**点位命名空间的方向用户已认同**：引用形态改为
  「地图规划名/点位名」——涉及 placement 匹配与契约，归 P2 切片 2 与区域划分一起做。
- **建议归属**：P2 切片 2（主矿/二矿区域划分 + 点位命名空间）。

## I9 敌方非人族单位/建筑显示「未知」（catalog 只有 terran）

- **现状**（2026-08-22 用户指出）：真机对局里很多单位/建筑显示「未知」。根因：对手是
  `Computer(Race.Random)`（driver/sc2_adapter.py），抽到 protoss/zerg 时其单位不在
  `load_terran()` 目录里 → `_unknown_stable_id` 打 `unknown/` 前缀（C1 禁 burnysc2 名进
  动态帧，这是**有意的显形**不是静默污染）→ 前端 `names.get(...) ?? "未知"`。
- **影响**：地图页敌方一片「未知」chip；聚类、投影、约束检查对敌方同样不适用
  （V1 本来也只建了己方战术链，影响主要在读面可读性）。
- **候选方案**：1. 补 protoss/zerg catalog 数据（game.catalog 加载三族，正确修法，
  数据工作量中等）；2. 临时把对手固定 Race.Terran（一行，但损失随机族测试覆盖，
  且掩盖缺口）；3. 前端给 `unknown/` 前缀加「未登记」样式与计数（不改数据，先让
  「为什么未知」可解释）。
- **建议归属**：B 系列数据项（三族 catalog）；方案 3 可作为可读性轮小项先行。

---

## 已处理（留档）

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
