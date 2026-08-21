# 工作记录：可用性整改执行轮（2026-08-21）

> 本文记录「三个并行审查 → 修复批次 → WS 断线 → F14 全链 → I5/I6 → F15-F19 → 用户四轮反馈」
> 这一执行轮的工作、决策与发现。
> 承接：newdocs/PLAN.md（计划）、newdocs/ARCHITECTURE.md（架构）、newdocs/ISSUES.md（问题清单）。
> 每节最后标了哪些 ISSUES/红线被关闭。

## 0. 提交清单（本执行轮，按时间序）

| 提交 | 内容 |
|---|---|
| `468219f` | 审查发现的 8 个功能缺陷：写入门禁绑错源 / P5 盲接受 / P2 按钮隐藏 / pos_marks 从未渲染 / pan 误选 / pointermove 旧闭包 / chip 档图层开关 / cluster 组归属顺序依赖 |
| `4a9abb4` | F12 收尾：exit_strategy 终局三态 / PanZoom fitKey / 持久化竞态 / matchExitBranch 收窄 |
| `8569c47` | WS 断线：显眼横幅 + 手动重连（**ISSUES I3 关闭**）|
| `e65b4ce` | F14 切片 1：地图规划画布 + 点位工具（离线放标记，草稿持久化）|
| `211cae2` | F14 切片 2 + B14：map_plan 全链（提案 → 接受 → 机器覆盖层 → 新会话生效），契约 REV 10→11 |
| `6c1abbe` | F14 切片 2b：槽位拖动 + 审批叠加画布 |

## 0.12 真机地形 bug 修复 + 「未知」确诊（2026-08-22 用户反馈）

**用户两问**：游戏实时地形不可用（进度还是 bug？）/ 很多数据显示「未知」。

**地形 = bug，双层修**（真机专用路径，沙盒从未触发）：
- 根因：`SC2DriverBot.on_step` 里地形回调（`_map_info_cb`）排在 `sink.on_game_state` **之前** ——
  真机会话的静态面顺序因此是 [terrain, map, ...]，而前端 store 的 terrain→map 合并是
  「map 未到即丢」→ 整局地形静默吞掉，地图页纯色底。**与 B16 是同一个顺序坑**（sim 侧
  当年修过并留了教训注释，driver 这条漏了）；昨天的地形采集走 REST（不关心顺序）所以没暴露。
- 修 A（driver）：on_step 重排 —— 首帧先走 sink（Session.on_state 同步发 static/map），
  地形回调排后。回归锁 `test_on_step_sinks_first_state_before_map_info_callback`
  （events == ["sink", "terrain"]，且地形只发一次）。
- 修 B（store，A5 防御）：`pendingTerrain` —— terrain 先到就挂起、map 到了再并，
  两个方向都成立（帧序不可假设；顺带让**旧真机录制的 JSONL**（terrain 在前）也能放出地形）。
  回归锁 mode.test 两条（乱序/正序都合并）。
- 验证：独立 WS 探针确认 sim 会话 statics 顺序 [map, catalog, schema, strategy, terrain]
  且 payload 完整；真机全流程留用户验收（subprocess 每次 spawn 重导 driver，无需重启 serve_api）。

**「未知」= 进度缺口（非 bug，设计如此显形）**：真机对手是 `Computer(Race.Random)`
（sc2_adapter.py），catalog 只有 `load_terran()` —— 敌方非人族单位/建筑翻不进 stable_id，
`_unknown_stable_id` 显式打 `unknown/` 前缀（C1：动态帧禁 burnysc2 名），前端如实显示「未知」。
归属 ISSUES I9（开放）：修法 = 补 protoss/zerg catalog 数据；临时缓解可把对手固定 Terran（未做，
随机族对测试覆盖反而有价值）。

**回归**：后端 **672 passed / 1 skipped**（+1 driver 顺序锁）；前端 **283 / 22**（+2 store
乱序合并锁）+ typecheck 无错。

## 0.11 实时驾驶整改：假世界连根拔起 + 顶栏收敛（用户实测反馈，未提交）

**用户症状三连**（点击启动真机没反应 / 时间线连上了但地图空 + 来历不明的槽位 /
游戏实时还有下拉框）——一条根因链 + 一个旧包残留：

1. **「点启动真机没反应」= 400 被吞**：`SessionBar` 的 `mapPlanId` 初始值硬编码 `"default"`，
   而 P2 双下拉模型（§0.10）后预设已改名 `default-bl/tr`、`layout-bl/tr` —— 不动下拉直接点按钮
   就发 `map_plan=default` → 后端 400「不存在」→ 前端 `sessionAction` 把 `!res.ok` **静默吞成
   null**（commands.ts）→ `startSession` 无视失败继续 attach。修法三件：
   - `pickMapPlan` 纯函数（shell/mode.ts）：当前值不在清单 → 落第一个；清单未到 → 不带参数；
   - `sessionAction` 失败返回 `{ok:false, detail}`，UI 顶栏内联红字显形（G7 同款纪律）；
   - 去掉 `window.confirm`（嵌入式浏览器可能直接拦掉 = 又一个静默无效）→ **两段式确认**
     （第一点变「再点一次 · 确认启动 SC2」，4s 自动还原）。
2. **「时间线在动 + 地图空 + 神秘槽位」= WS 惰性建假世界**：attach("live") 后
   `/api/frames?source=live` 会顺手建一个**进程内 OfflineSession（假世界）**——出厂模板
   槽位 + 逐秒假帧 + 无地形，被误读成「SC2 连上了但数据不对」。修法（api/app.py）：
   - `_session()` 不再惰性创建；无会话 = 如实说：WS 合成一条 `frame/session`「未连接」帧
     （bootHint 对「未连接」不显示首帧等待，顶栏「等待会话」接手）；
   - **WS 保持连接等会话**：pump 每轮重解析 `app.state.session`，会话出现（含停止后再启动）
     自动重置游标 + 补发全量快照，前端 attach 一次即可，不用重连；
   - REST `_source("live")` 无会话 → 404「没有活跃会话」（不再悄悄造一个）。
3. **「游戏实时下拉框」= 旧前端包残留**：当前源码与 git 历史都没有这个控件 —— 用户浏览器
   挂着旧 vite 包（同机还发现两个 stale serve_api 进程，已清）。重启服务即消。

**顶栏收敛（用户拍板「冗余摘掉」）**：删 状态/地图/游戏时间 三个 Pill（状态由会话 label 给、
游戏时间时间线上有）；播放×4/暂停只属于复盘模式（回放语义），驾驶态只有「回到实时」；
**删「启动沙盒」按钮**（sim 驱动保留在后端，测试/agent 走 REST——UI 无沙盒入口，
假世界不再有被误当真机的路径）；「停止会话」按驱动改名 **「关闭真机」/「停止沙盒」**，
且**会话存在即可点**（不再 `disabled={!live}`——子进程死了 SC2 可能还挂着，恰恰是最需要
点它的时候；stop 对死会话幂等无害）。这是「关闭游戏按不下、感觉一直关不上」的修法。

**回归**：后端 **671 passed / 1 skipped**（基线 669 +2：WS 无会话不建假世界 + WS 后启动自动接上）；
前端 **281 / 22**（+3 pickMapPlan、+8 session-drive 源码扫描锁：错误不吞/无沙盒入口/
无 window.confirm/关闭真机命名/无 Pill/播放暂停只留复盘/pickMapPlan 兜底）+ typecheck 无错。
**浏览器端到端实测**（IAB，vite 5273 + serve_api 8770 最新代码）：drive 顶栏新形态 ✓、
无会话「未连接」✓、REST 起 sim 会话后 UI 同连接自动接上（矿 987→1088、驻留 70→82s、
seq 69→81）✓、UI 点「停止沙盒」回未连接且无孤儿进程 ✓、两段式确认 ✓。
**真机（SC2）全流程留用户验收**：启动 → BootHint 等待 → 帧到达 → 关闭真机树杀。

## 0.10 规划模型重构：双下拉 + 预设瘦身（用户十三轮反馈）

**模型修正（用户拍板）**：规划不是"一张地图"，而是**某地图 × 红/蓝方的一种布局**
—— 双下拉设计：先锁定地图 + 出生点，第二个下拉只列这个**独立规划空间**的布局，
随时切换对比。旧模型（一个文件含 bl/tr 双分支 + 单独切出生点）退役。

- **文件形态**：单出生点分支 `{id, title_zh, map_name, spawn, origin, anchor,
  build_slots, pos_marks, updated_at}`；del 不再跨分支同步（规划就是单分支）。
- **锁定预设 ×4（init 自愈）**：`default-bl/tr` **空白地图**（0 槽位 —— 用户拍板
  "去掉所有自建槽位后才是默认地图"）+ `layout-bl/tr` **出厂校准布局**（手写模板
  对应方全部 26 槽位）。锁定前缀 `default-`/`layout-`，复制是唯一改动路径。
- **解析兼容**：`tactical_map.base.load_map_plan` 同时吃单出生点形态与 base_layout
  原形态（spawns 双分支）—— 会话装配（run_session/OfflineSession）换用它。
- **REST**：`GET /api/map-plans/{pid}` / `PUT {hunks}` 去掉 spawn 参数（规划自带）。
- **前端头部**：地图下拉（唯一 map_name 集合）→ 蓝方/红方 → 规划下拉（按
  map+spawn 过滤）；切地图/方 = 自动落该空间第一个规划；复制/删除随空间走。
- **气井名 LOD**（同轮小改，后按用户再反馈收紧）：气井名与普通槽位**完全同档
  同字体**（`LOD.buildingLabelFull=8` 才显示），基地名常驻（≥2.5）。
- 旧 runtime/map-plans 文件为双分支形态，与新模型不兼容 —— runtime 数据可弃，
  重启时清目录由预设自愈重建（用户尚无有价值的自建规划，损失为零）。
- **回归**：后端 **669/1s**（test_map_plans 按新模型重写 12 条）；前端 **270/21**
  + typecheck 无错。curl 验证：四预设 slots=0/0/26/26、locked、spawn 正确。

## 0.9 地形量化修正 + 预设命名 + 视觉改版（用户十二轮反馈）

- **黄色格点之谜（(62,80) 一带）**：真机高度有 40 个不同值，量化器把 1-2 单位的
  缓变也当独立层 → 满图"跨层"被判成斜坡暖色抖动。修法：`quantizeLevels` 加
  **MIN_LEVEL_GAP=4**（相邻高度差 <4 并同层；SC2 真实台地落差 ≥6）—— 只有真
  台地间才算层/悬崖/斜坡。缓变噪声并层有回归锁（189/190/191 交替 → count=1）。
- **悬浮窗只留世界坐标**（格/世界双坐标删格）。
- **资源视觉改版（用户拍板）**：去小圆点，全部描边框 + 加深 tint；气井改**紫色**；
  矿脉深蓝（高倍率才描边，98 个框低倍率太密）；地图页保留圆点（live 采集人数挂点）。
- **预设命名（用户拍板：基地/气井=预设不可动槽位，后续直接引用）**：
  `_reserved_boxes` 命名 —— 主基按出生点匹配（蓝方主矿/红方主矿），分矿归属最近
  主基按距离编号（二矿…七矿，只收离本方更近的），气井归属最近基地（蓝方主矿气井1/2）；
  **36 个命名条目**（12 基地 + 24 气井）进 payload。画布带名标注（LOD）；
  槽位列表新增「预设固定建造点」锁定段（虚线行，不可编辑删除）。
- **回归**：后端 671/1s（命名断言）；前端 **270/21**（缓变并层回归锁）+ typecheck 无错。

## 0.8 黑屏修复 + 图层面板收纳 + 去压暗（用户十一轮反馈）

- **黑屏（TDZ 崩溃）**：`reserved` 声明插在了 preview useMemo **之后**，点击槽位触发
  重渲染 → `Cannot access 'reserved' before initialization` → 整页黑屏。
  修法：声明上移到首个使用点之前。教训：**补丁脚本插码先看清引用顺序**，
  useMemo 的求值发生在渲染路径上，引用先于声明就是运行时炸弹。
- **图层面板收纳**（用户拍板：不常用功能不占侧栏）：新 `canvas/LayerPanel.tsx`
  —— 画布**左下角折叠小部件**（收起=一个小 chip，展开=开关清单）。地图页侧栏的
  「图层」整卡删除换它（「位置插值」开关收进 extra 行）；**规划页画布也挂上**
  ——planningLayers 从冻结 memo 改为真 state（可建区/矿区=初始强开，用户可再关）。
  放置横幅上移（bottom-2→8）让位。
- **去掉不可走压暗**（用户拍板「干脆全都没有深色」）：数据显示左下基地周围
  不可走格最多（70/400）、tr 主基几乎没有（5/400）—— 压暗随真实地形天然不对称，
  被误读成"只有左下有矿影"。移除 bakeTerrain 的压暗分支；悬崖硬描边保留
  （信息不丢），按格可走性由悬浮窗给出（F18）。
- 浏览器实测：黑屏消失、图层面板在、槽位列表正常；回归：前端 **269/21** +
  typecheck 无错（后端本轮未动，671/1s）。

## 0.7 固定建造点预留区（用户十轮反馈：别处矿看不到/红方没基地/基地气矿要预留）

**两个关键实测事实**：
1. **game_info 的 placeable/pathable 在矿脉/气井位置全是 1（不挡）** —— SC2 这两张
   栅格只表达地形可走性，不含资源占用。之前"深色=矿脉压暗"的解读是错的（那片深色
   是地形本身）；更要紧的是**放置校验真的允许把槽位放到矿上**。
2. burnysc2 的 `expansion_locations_list` 在首帧可用且与模板 origin 完全一致 ——
   第三次真机采集拿到 **12 个基地位置**（精确含 bl [48.5,28.5] / tr [127.5,119.5]）。

**修法**：
- 数据文件三件套：terrain.json = 地形三栅格 + 122 资源点 + **12 基地位置**
  （adapter `extract_map_info` 加 expansions 旁挂键，不进帧 payload、存 meta；
  采集脚本从 describe.meta 拿）。
- **预留矩形单点计算**（`map_plans._reserved_boxes`）：基地=CC footprint（catalog
  size）、气井=3×3（refinery size）、矿脉=2×2；payload 直出 `reserved` 数组 ——
  前端渲染与预览吃同一份矩形，TS 里不重写脚印算法。
- **两侧校验**：后端 save 对**本次改动**的槽位做预留重叠拒绝（存量出厂槽位不追溯）；
  前端 previewPlacement 加 `reserved` 参数（reason "reserved"，提示区分基地/气井/矿脉），
  悬停 ghost、点击落笔、拖动落点同用一份判定。
- **画布**：资源层不再依赖 world（规划视图 world=null 也画全图矿/气井点）；
  新 `reserved` prop 画预留区 tint（基地黄 + 描边 + 「基地」标注 LOD、气井绿、
  矿脉蓝）。红方切换后基地/矿区在图上直接可见（此前"红方没基地"= 无任何标记）。
- Vite 中途被停导致用户页面挂着旧包 —— 重启（资源点/预留区/真地形全在最新包里）。

**回归**：后端 **671 passed / 1 skipped**（+2：payload 带 134 预留矩形 / 压基地 400）；
前端 **269** / 21 files（+1 previewPlacement 预留单测）+ typecheck 无错。
payload 实测：reserved 134（12 基地 + 98 矿 + 24 气井）+ resources 122 + 真地形。

## 0.6 全图资源点接入（用户九轮反馈：地图颜色是啥/为什么只有左下角有矿/视野限制？）

**用户的三个问题与实测答案**：
1. **「丰富的颜色是什么数据」** = 真机地形三栅格（§0.5 采集）：高度分级台地 +
   悬崖描边 + 斜坡 + 可建区淡绿 + 不可走压暗 —— 全图、无战争迷雾限制（game_info）。
2. **「为什么只有左下角主矿有基地和矿的阴影」** = 两个独立来源叠加：左下角的
   "基地"是**蓝方槽位集群**（depot 网格 + rax 楼梯，规划数据，只在所选出生点）；
   "矿的阴影"是**真实地形里矿脉不可走格的压暗**（矿脉确实挡路）。夹具/沙盒的
   `resource_nodes` 是 worldsim 假造的 10 个（只围绕蓝方 CC）—— 那才是"只有左下"
   的另一半原因；规划页 payload 此前根本没传资源点。
3. **「是不是只能拿到视野内的数据」** —— 否，实测证伪：中性资源（矿脉/气井）
   不受战争迷雾限制（迷雾只藏敌方单位）。真机一帧 static/map 的 resource_nodes
   = **全图 98 矿脉 + 24 气井，聚成 14 个矿区**，坐标横跨全图（x 19..157）。

**修法**：资源点随真机地形一起落进数据文件（terrain.json 加 `resources` 节，
一次采集 122 点）；`view.statics.ladder_resource_nodes()` 出口；
**地图规划 payload 带全图资源点**（planning 画布的「矿区」图层强制开 ——
槽位绕开真实矿脉线这件事终于有数据可依）。真机采集第二次启停，无残留进程。

**回归**：后端 **669 passed / 1 skipped**（+1 全图资源断言）；前端 268/21 +
typecheck 无错。payload 实测：98 矿脉 + 24 气井直达规划页。

## 0.5 真地形接入 + 会话装配地图规划（用户八轮反馈：地形不可用/离线地图不准/槽位归属）

**三个症状一个根因链**：
1. 「实时驾驶地形不可用」——用户测的是沙盒（sim）：worldsim 没有 game_info，
   按设计不发地形 → 地图页一片「地形未下发」。
2. 「离线 LadderMap 数据不准 + 蓝红基地位置一样」——两件事：①夹具地形是 B16
   合成的（三级方形台地，不是真实地图）；②**规划画布混源**：出生点切到红方后
   槽位移到右上，但夹具（bl 开局）的 CC/工人/矿脉从 world 帧照画在左下 ——
   用户看到的"基地"没动，误读为数据错了。
3. 「depot13 这些不该是默认，应该是某个规划设定的」——地图规划文件建好了，
   但**会话装配仍读手写 base_layout**（进入游戏加载哪一份规划没有接口）。

**修法（四件）**：
- **真机地形一次采集、进版本库**：起真机会话 → `GET /api/sources/live/statics`
  抓 static/terrain → `modules/tactical_map/data/ladder_map/terrain.json`
  （height 非零格 17760，与 d42aa1e 真机验证记录一致）。`view.statics.ladder_terrain_view()`
  统一出口；三处消费：**地图规划 payload**（规划页画布直接带真地形）、**make_fixtures**
  （真地形优先，B16 合成器降为无文件兜底；夹具已重生成，三份全 17760）、
  **run_session 沙盒**（首帧 statics 之后发 terrain 控制行 —— 顺序不能反，B16 教训：
  早于 static/map 会被前端合并丢帧）。沙盒/离线从此看到**真实地图**。
- **规划画布去混源**：MapPlanning 的 MapCanvas 不再喂夹具 world/production/economy
  —— 规划视图只画地图规划本身（槽位/点位/真地形），出生点切换语义干净。
- **会话装配接地图规划**：`POST /api/session/start?map_plan=<id>`（幂等守卫加
  规划维度：同 driver 同规划才幂等）→ LiveSession `--map-plan` → run_session /
  OfflineSession `load_base_template(plan)` 装配。驾驶模式会话操作区加**地图规划
  下拉**（会话启动时装配；运行中禁用）。规划不存在/无落盘文件 → 400 带原因。
- **点位命名空间（方向确认，未实现）**：用户认同点位引用应为「地图规划名/点位」
  （I8 的解法方向）——涉及 placement 匹配与契约，归 P2 切片 2 与区域划分一起做。
- 真机地形采集时 SC2 正常启停、无残留进程（tasklist 验证）。

**回归**：后端 **668 passed / 1 skipped**（+2：会话装配规划+真地形、未知规划 400；
live 首测的 statics 断言更新为含 static/terrain）；前端 268/21 + typecheck 无错；
夹具重生成（`pnpm gen:fixtures`，三份全真地形）。

## 0.4 P2 切片 1：地图规划文件模型 + 规划页 tab 退役（用户七轮反馈）

**规划页两处切换 UI 删除（用户拍板「完全不需要」）**：
- **顶部 tab 行**（地图规划/生产规划/Flow装配）删除 —— P1 后左侧导航栏就是这三个入口，
  导航只应有一处。`PlanningPage` 的 tab 从 prop 派生（不再有本地 state，避免
  同组件复用不换页的坑）；R5 门控降级为 hash 直达兜底。
- **地图规划右侧「点位/槽位」内部 tab** 删除 —— 两组列表**常显堆叠**（各带小节标题），
  双向高亮直接可见；scrollIntoView 不再依赖 tab 切换。

**P2 切片 1：地图规划文件（`view/map_plans.py` 新模块 + REST + 前端头部）**：
- **一个地图规划一份 YAML**（`runtime/map-plans/`，base_layout 同构 + title_zh/updated_at）。
  默认 `default` **锁定**，从手写模板**逐字节复制**（保留真机校准注释；永不重写，
  复制是唯一改动路径）。
- **出生点切换**：bl/tr 按出生点分支存各自的世界坐标；读取 =
  `instantiate_spawn(cc=origin)` 零平移 → `map_static` 直接产 **static/map 形状 payload**
  —— 前端画布零改动（同款渲染路径）。
- **离线保存不走提案**（用户已拍板）：草稿 hunks → `apply_map_overrides` 同一套校验
  （重名/重叠/尺寸/指向不存在，400 结构化理由）→ 写回**正在编辑的出生点分支**；
  `del` 按全局名同步其它分支（与 B14 名字空间语义一致）。前端「提为提案」按钮
  从规划页退役，改「保存到地图规划文件」；草稿 localStorage 退役（后端文件是真相源）。
- 前端头部：地图下拉（锁定标记+槽位数）/ 蓝方红方出生点切换 / 新地图名 + 复制新建 /
  删除；无后端时回退夹具底图并提示。terrain 由前端叠加夹具地形（规划文件不含栅格）。
- REST：GET/POST/PUT/DELETE `/api/map-plans`、`GET /api/map-plans/{id}?spawn=`；
  serve_api 加 `--map-plans`（默认 runtime/map-plans）。
- **事故记录**：MapPlanStore.create 持锁调 list_meta（内部再拿非重入锁）→ 测试挂死，
  与上轮 planner 缩进事故同一天。教训再记一笔：**持锁方法里不得调用会拿同一把锁的
  对外方法**——对外方法（带锁）与内部方法（无锁）分层。
- 浏览器实测：头部（下拉/蓝红方/复制新建）与右侧常显列表渲染正确；
  IAB 内 main 区点击被画布页 window 级原生监听拦（环境怪癖，真实浏览器不受影响，
  bl/tr 切换与保存的功能路径由 9 条后端测试锁定：两出生点坐标各自正确、
  hunks 校验、锁定、复制删除、重启持久化）。
- **P2 切片 2（未做，下一步）**：主矿/二矿区域划分 authoring（用户指出的数据缺口，
  region 现状只有 main_base 且 zh 名为空）——文件形状已预留 regions 节；
  ISSUES I8（placement 跨地图）也归切片 2 定。

**回归**：后端 **666 passed / 1 skipped**（+9 map_plans）；前端 **268** / 21 files
+ typecheck 无错。

## 0.3 P1 导航重构 + 警报复用（用户六轮反馈：placement 立项 + 警报组件 + 推进 P1）

**导航按模式重组（P1，用户拍板「离线编辑不应有驾驶栏、复盘不应有规划栏」）**：
- `shell/rail.ts`：`railGroups(mode)` 改为**模式的函数**——offline → 规划组+诊断，
  drive/replay → 驾驶组+诊断；组级门控退役（可发现性由顶栏一级三段模式保证，
  G7 的 R5 理由由 PlanningPage 守卫兜底 hash 直达）。新增 `homePageOf(mode)`。
- `App.tsx`：模式切换/旧链接直达时当前页不属于本模式 → 自动跳该模式首页
  （offline → 生产规划[主场景]，drive/replay → 概览）。
- `IconRail`：w-16 → **w-44 加宽横排**（图标+完整标签+右侧角标）。
- `SessionBar`：offline 的夹具下拉加「背景数据」标签（它只是规划的静态面来源，
  不是回放语义）。
- 浏览器实测过三个形态：offline 左栏=规划+诊断、主区自动落生产规划（默认规划
  22 项含 research 完整可编辑）；切复盘后左栏=驾驶+诊断、角标正常（概览 1 条
  警报 / 生产「阻塞」）、主区自动落概览；顶栏一级三段 + 源选择器随模式出现。

**警报复用（用户问「生产规划的警报和实时生产是不是没复用组件」）**：
- 根因：两处数据面不同——实时 = `frame/alerts`（AlertView，AlertService 带冷却
  + 运行时来源），规划 = stalled 事件直渲染（自己拼文案）。复用的正确层次是
  **数据模型**不是像素。
- 修法：`AlertService.from_curve(curve)`（无状态、无冷却，规划干跑专用）→
  stalled 死局全量转 `plan_stalled` AlertView（error 级，文案后端拼含 mm:ss）；
  `/api/plans/simulate` 返回体加 `alerts`。前端抽 `panels/AlertsList.tsx`，
  Overview 风险面板与规划前瞻警报卡共用同一组件（severity 点 + text_zh + eta）。
- `plan_stalled` 不进帧契约闭集（只走 REST），前端用结构化类型，无 REV 变化。

**ISSUES I8 立项（用户指出，暂不动代码）**：生产规划的 placement 引用当前帧源
地图的槽位/点位名，但规划是独立文件、可能跨地图 —— 换图对不上。用户没想好
匹配方式（也许规划↔地图绑定），归 P2 地图规划文件模型一并定。

**回归**：后端 657/1s（不变）；前端 **268** / 21 files（rail.test 重写为模式
函数语义 +2）+ typecheck 无错。运行中的服务已换最新代码（vite 5273 +
serve_api 8770，可直接验收）。

## 0.2 实机 static/map 修复 + P0 生产规划可用化（用户四轮反馈）

**用户四轮反馈**（投影板打磨之外的系统性反馈）确立了两件事：
1. **导航按模式重构**（P1，未动工）：顶部一级 实时游戏|复盘|离线规划，二级导航是模式的
   函数（离线规划才有 地图规划/生产规划/Flow装配）；现状 IconRail 只在 drive 下置灰规划组，
   offline/replay 全可见，用户判定"相互矛盾"。「规划（离线工作台）」条幅已删。
2. **审批可跳过**（用户原话"不走审批让 agent 直接去改都行类似 codeagent"）：离线规划域
   agent 直改文件、diff/撤销兜底；live 自动应用（原 §5.3）后置——**没有 agent 接 live 之前
   做了也没法调试**（用户拍板）。提案通道降级为可选，不删。

**实机「等待 static/map」bug（已修）**：真机首帧 `bot.time=0.0`，四张静态帧以 game_time=0
落进会话缓冲；前端在会话出帧前 attach（真机开机 1-2 分钟，必然先 attach），WS live 泵的
游标过滤（`game_time > cursor`）让 game_time=0 的静态帧**永远落在游标后面**，一次都发不出
——生产页有数据、地图页一直等待。sim 沙盒没暴露是因为 worldsim 先 `t+=dt` 再产帧
（静态帧 game_time=1.0 能过过滤）。修法（`api/app.py` WS 泵 live 分支）：静态帧改
「快照出现即发」（`statics()` 增量转发，不受游标门控；between 通道跳过静态帧防重发）。
回归测试 `test_ws_live_forwards_statics_landing_at_game_time_zero`（stub 逐字复刻真机时序，
含心跳线程保证预修时快速失败而非挂死）；已做修前验证（预修 5.4s 失败，修复后过）。

**P0 生产规划可用化（本轮主体，已落地）**——用户定位"重中之重：必须 agent 能用它才能流程规划"：
- **后端规划存储**（`view/plans.py` 新模块）：**一个规划一个 YAML**（用户拍板独立规划文件；
  `runtime/plans/`，serve_api 已接 `--plans`）。Plan = 地图引用 + 出生点(bl/tr) + 队列三件套；
  默认规划 `default` **锁定**（从 bio_tank_opening 参考计划导出，连续同型合并 count → 22 项），
  不可覆盖/删除，要改就复制。校验复用提案通道的 `parse_item` + catalog 存在性，
  原因中文 400 返回（agent 拿着就能改）。REST：GET/POST/PUT/DELETE /api/plans + GET /api/plans/{id}。
- **无会话干跑**（`POST /api/plans/simulate`）：`planner/opening.py` 新增标准开局种子
  （CC + 12 SCV 挂采矿 + 8 矿脉 + 50 矿，供给 12/15）——SCV 必须带 HARVEST_GATHER 指向矿脉
  tag，否则 derive_from 把 12 工全算 idle、收入为 0。真 Planner 逐秒投影，
  返回 frame/projection 的 payload（ProjectionBoard 直接渲染），**不需要会话**
  （与提案预览互补：那个要会话当起点）。stalled 事件（缺矿/缺气/前置没…）就是前瞻警报。
- **前端生产规划 tab 重建**（PlanningPage）：规划下拉/复制新建/空白新建/保存/删除
  （草稿退役 localStorage，后端文件是真相源）；编辑器支持 **research** 项（DraftItem.op 扩枚举，
  类型列表用 upgrade role）；试算 → **双板同款 ProjectionBoard**（草稿 + 已保存对照，
  新 `initialDomain` prop 让规划从 0 起轴不出负时间）——用户反馈的「规划页与生产页
  两套图表组件脱节」在此收敛；前瞻警报卡列出全部卡点（时间 + zh 名 + 原因）与 skipped 项。
  「提为提案」按钮移除（那是 live 语义，且无会话时整条走不通）。
- 冒烟：default 规划 300s 投影 29/31 供给、枪兵×4、卡点如实报缺矿（早期 SCV 生产快于收入，
  模型的诚实输出）；marine-only 草稿 t=0 即报「前置没」。
- **回归**：后端 **654 passed** / 1 skipped（+9：test_plans.py 9 条）；前端 **266 passed**
  / 21 files（+4：draftFromJson 往返/research/缺省回退）+ typecheck 无错。

**遗留（用户明确可延后）**：泳道放大出现左右滚动条；自适应高度导致拖时间轴布局跳动；
地图规划左侧栏偏大。归 P1/可读性轮。

**警报语义修正（用户五轮反馈：警报太多、红线不知道为什么）**：
- 根因：stalled 事件把**时序等待**和**死局**混在一起。顺序队列里缺矿（攒钱）、
  无产槽/无科技槽（排队，槽空即启）、前置在途（建筑落成即启）都是正常时序，
  不是警报——之前全画成红线。
- 修法（`planner._feasible` 返回 `(ok, reason, wait)` 三元组）：`wait=True` 的失败
  不记 stalled 事件（泳道 bar 的起点天然表达等待）；只有死局才记——
  **前置不在场也不在途**（顺序队列里后面的 op 永远救不了队首）、
  **供给顶到 200**（新增 `SUPPLY_MAX=200` 真实上限：sim 供给封顶 + 守卫在 200 后
  不再白插 depot——「缺供给」由此成为用户要的"不能再往后生产"硬停警报）、
  **收入永远为 0**（无矿工且无 SCV 在训 = 缺矿死局；无气工或无精炼厂 = 缺气死局——
  "建了精炼厂没人采"这类规划缺一步会如实报出来）。
- 判据只看当前 SimState（顺序语义：排在后面的 op 救不了队首）。
- 效果：默认规划 300s 投影从 5+ 个红线降到 **0**；marine-only 草稿仍报「前置没」。
- 前端顺带：**对照板默认收起**（用户拍板：平时只看草稿，不占横屏；「▸ 对照已保存规划」
  按钮展开才双板并排）。
- 事故记录：改主循环时一次 Edit 弄坏了 `while queue:` 的缩进（内层逃出外层、
  `st.t += 1` 被带进内层）→ 外层永不推进 + 守卫每秒插 depot 把队列撑爆 → 测试挂死。
  faulthandler 重复采样定位。教训：Edit 后必须立刻跑受影响的最小测试，不能攒到最后。

**后续批次（用户已拍板顺序）**：P0（本轮）→ P1 导航重构（顶部三模式 + 二级导航）→
P2 地图规划文件模型（默认地图锁定 + 出生点切换 + 复制新建；**主矿/二矿区域划分是
authoring 数据缺口**：现状 region 只有 main_base 且 display_name_zh 为空）→
P3 live 自动应用 + agent 工具面（read_plan/write_plan/simulate 直连 REST）→
P4 复盘记录列表（ViewRecorder 落盘接 registry）+ flow 装配定位（只读图 + agent 改 YAML 接缝）。

## 0.1 I5 + I6（投影泳道共享时间轴 + 真机首帧等待提示）

按 §5.1 顺序推进的两个前端项（不动契约、不动后端）：

**I5 投影板**（ISSUES I5 关闭）：
- 曲线与泳道合进一张卡（`charts/ProjectionBoard.tsx`），共享同一条时间轴：
  - 同一定义域：`gantt-data.ts` 的 `sharedDomain(frame)` 同时喂给曲线（`ProjectionChart` 新增 `domain` prop 锁轴）与泳道（百分比定位）；
  - 像素对齐：泳道左标签列宽 = y 轴宽（46px）、右留白 = 曲线右内边距（8px），都在 `PROJECTION_GUTTER` 单点定义；
  - 锁轴时关掉 uPlot 的 x 框选缩放（曲线缩了泳道跟不上，对齐就毁了）。
- 真泳道：每个 stable_id 一条道，道内 started→completed 一条 bar，同类并发错开子行（贪心打包，row）；
  泳道按首次出现紧排；删掉右侧时间列（时间由共享轴给）。旧 Gantt 的「每个事件一行 + 每行重复 label + 只往下摊」三问题一并解决。
- 配对语义沿用旧 Gantt（同类型最早未闭合 started）但修正了一处字典序坑：
  旧代码 `[...entries].sort()[0]` 按**字符串**排，"id:10" < "id:9" —— 10s 的 started 会被 9s 的抢先闭合。
  现在 `toLanes` 按数值排。
- 卡点是时间点：一条红色竖线贯穿全部泳道（叠加层），与曲线里的红色虚线同一条轴。
- 验收截图：`newdocs/I5-projection-board-verify.png`（含像素级对齐测量：泳道轨道与 uPlot 绘图区
  左边缘 x 均为 146.8，7 条卡点竖线两侧偏差均 < 1px）。

**I6 首帧等待横幅**（ISSUES I6 关闭）：
- 判定收在 `shell/mode.ts` 的 `bootHint(mode, sourceKind, sessionState, mapArrived)` 纯函数：
  drive + live 源 + static/map 未到 + 会话非终态（启动中/对局中/会话帧未到）→ 提示
  「正在连接 SC2 并等待首帧（真机约需 1-2 分钟）」；数据到达自动消失；
  已结束/崩溃/未连接不提示（真实终态另有显示）。
- `shell/BootHint.tsx` 只订阅 store 与展示；地图页顶部（含无地图分支）与概览页顶部接入。
  离线/回放模式不出现。

**回归**：后端 638 passed / 1 skipped（不变）；前端 **244 passed / 20 files**
（基线 223/19 + 新 gantt.test.ts 16 条 + mode.test.ts 扩 5 条）；`pnpm typecheck` 无错。

遗留小项（不阻塞）：泳道左标签列 46px，非 catalog 的 id（如 assign_gas 事件的 stable_id）
会被截断，靠 title tooltip 兑底；可读性轮（§5.2）给词表加 zh 后自然缓解。

## 0.2 F15-F16 + B15-B16（用户二轮反馈：投影板交互重构 + 规划编辑体验）

用户对生产页/规划页的二轮实测反馈，四个任务一次做完（计划：F15-F16 + B15-B16，
全部不动契约 REV）。

**B15 planner 产槽模型**（后端，`planner/slots.py` 新模块）：
- 调研结论：多兵营并行**早已支持**（`_feasible` 按产建筑数计槽）；用户看到的机枪兵串行
  是因为场景只有 1 兵营，18s/个 +「无产槽」卡点是正确投影。
- 真 bug：techlab 母建筑被当普通产槽（tank 只需 factorytechlab 建过一次，之后普通工厂
  也能"造" tank）；且 `derive_from` 不数 live 已有挂件（`st.addons` 只在投影内落成时更新）。
- 新模型（用户拍板优先序）：科技单位只能用科技槽；普通单位用普通槽
  （容量 = 母建筑数 − techlab − reactor + reactor×2），普通满才溢出到空闲科技槽；
  失败原因细分「无科技槽」/「无产槽」。
- 溢出记账的坑（实现中发现）：溢出到科技槽的普通单位若计回 busy_normal，第二次判定会
  误判还有空闲科技槽 —— 正确式子：普通单位看总占用 < 总容量；科技单位看
  `busy_tech + max(0, busy_normal − normal_cap) < tech_cap`（贪心：普通先占满普通槽）。
- 测试 tests/planner/test_slots.py 6 条（tank 串行于科技槽 / 溢出 / reactor 双槽 / derive 数挂件）。

**B16 夹具合成地形**（`tools/make_fixtures.py`）：
- 根因：三组离线夹具 static/terrain 帧数 = 0（sim 会话也没有）——规划页看到的一片绿
  是「地形未下发」降级态，F11 的台地-悬崖渲染只在真机有数据时生效。
- 修法：make_fixtures 加确定性合成地形（以主基为中心的三级方形台地 + 悬崖墙 +
  y∈[28,32] 东西向斜坡走廊 + 3×3 平坦 placeable），每 scene 首帧（statics 已落盘）后
  emit 一条 static/terrain —— 必须在 static/map **之后**，否则 store 合并（`map 到了才把
  terrain 并进去`）会丢帧。
- `pnpm gen:fixtures` 重生成，三夹具各含 1 条 terrain；前端零改动。

**F15 生产投影板 v2**（`charts/ProjectionBoard.tsx` 重构 + UPlotChart/ProjectionChart 扩展）：
- 版式：泳道在上、曲线在下（一张图两带）；hover 游标线贯穿两带（uPlot 光标与泳道
  mousemove 双向同步，都是 native 监听）；卡点红竖线仍贯穿。
- 滚轮调视窗宽度（G3 native passive:false，围绕光标缩放，clamp 20..600s）；
  **中心跟随顶层时间轴**（store.position；live 跟随帧），拖时间轴只移中心不动宽度。
  UPlotChart 新增受控 `xRange`（变化 → u.setScale）—— setData 会重算自适应刻度，
  受控时必须压回去。
- 点击检查（用户拍板：逐秒采样 + 点击检查，不做 22.4 帧/秒真逐帧）：点击任意时刻 →
  右侧面板显示该秒完整状态：资源/供给/工人（points 直读）+ 建筑/单位明细 +
  在产明细（`gantt-data.activeAt`：显示层从 events 配对，与 toLanes 同层同配对）+
  产线摘要（每类产建筑「X 座 · 在产 N」，catalog.produced_by join）。
- 脚注重组成一行；卡点列表不再常驻（竖线 tooltip + 检查面板列出视窗内卡点）。
- Overview 的小投影图不动。测试：gantt.test 扩 activeAt，新 projection-range.test（zoom/center/anchor）。

**F16 规划编辑体验**（MapCanvas/PlanningPage/map-draft/layers/grid）：
- hover 预览 ghost：新 `previewPlacement` 纯函数（吸附 + 重叠 + placeable，
  reason: overlap/unplaceable）—— hover 算 ghost、点击用**同一结果**落笔，预览与落笔
  永远一致；画布新 props ghost/onHover，绿=可放红=非法。
- placeable 即时校验：放置/拖动落点新增地形校验（之前只查重叠）；地形未下发时如实
  降级为未知（不算非法）。
- 槽位可见化：规划模式（slotsOverride 非 null）槽位画实线框 + 名字（LOD 同 marks）；
  地图页驾驶态维持四角刻度低权（F11e 原则不变）。
- 可建区层：`layers.ts` 新图层 placeable（默认关，规划页强制开），淡绿 tint；
  B16 后离线也有数据。
- 双向高亮：MapCanvas 新增 onMarkClick/onSlotClick/selectedName（点位菱形半径命中、
  槽位原地点击也算选中）；右侧列表项高亮 + scrollIntoView + 点位聚焦改名；
  反向点击列表行 → 地图高亮。
- 布局压缩：点位/槽位合并成一张卡（内部 tab + 固定高度内滚）；整页去外滚
  （根 h-full flex，画布 flex-1）；layout.test 相应更新（PlanningPage 移出文档页清单）。

**回归**：后端 **644 passed** / 1 skipped（+6）；前端 **259 passed** / 21 files（+8）
+ typecheck 无错。规划页手动验收（地形/ghost/双向高亮）由用户进行。

## 0.3 F17-F19（用户三轮反馈：投影板打磨 + 地图悬浮窗 + 规划试算）

**F17 投影板 v3**（纯前端）：
- **历史累积**（拖时间轴时左侧内容不再消失）：投影帧只含 [based_on, +horizon]，
  中心跟随时左半视窗会空 —— `gantt-data.accumulateInto` 显示层把走过的每秒累积
  （points 按 t 去重后到覆盖；events 按 kind|t|id|reason 去重；超出当前帧末端的旧预测
  丢弃；based_on 大幅回退时清空重新累积）。与 ReviewableSource 环形缓冲同类。
- **左键拖图 = 拖时间轴**：按住绘图区横拖（位移 >4px）→ seek（仅可 seek 源；
  live 跟随模式禁用）；拖完的 click 不触发检查面板（lastScrubMoved 门控）。
- **hover 线两处修复**：① 拖时间轴后线滞后 ≈ 中心位移（hoverT 存绝对时间，range 变了
  线不动）—— 改存 **frac**（几何位置），换算全部在 root 级统一 native mousemove 里做
  （不再分泳道/uPlot 两套换算）；② 线越过时间轴延伸到 legend —— uPlot 自带 legend
  是 canvas 下方的独立 div，隐藏它（ProjectionChart 新 hideLegend），hover 读数
  （时间/矿/气/供给）进 footer。
- **泳道重构**：名字进 bar（圆角矩形内 zh 名），行变**全局打包行**（跨类型贪心，
  toLanes→packBars）；固定 7 行高度、超出滚动（角标显示总行数）。
- **检查面板常驻**：默认显示「现在」（based_on 点），点击任意时刻切换，× 回到现在 ——
  面板不再开关移位。

**F18 地图格子悬浮窗**（MapCanvas 内置，所有用画布的页都有）：左上角显示鼠标
所在格坐标 + 地形分类（grid.ts 新 `terrainClassifier`：与 bakeTerrain 同源的量化/
边缘检测，U15 显示层）—— 悬崖/斜坡/平地/不可走 + level + 可建/不可建；
height 未下发时如实只给坐标（不编造）。规划页的放置模式横幅让出左上角（移到底部）。

**F19 规划试算联动**（用户问「规划没图怎么仿真」）：生产规划 tab 加「试算（双投影）」
—— 草稿提为提案 + 立即 fetchPreview → ProjectionPairChart 展示当前队列 vs 草稿后。
仿真是**后端真 planner 干跑**（A3/C7：前端不本地算）；没有会话时后端如实报错
（「没有会话，算不了投影」→ 先起沙盒）。

**回归**：前端 **262 passed** / 21 files（+3，gantt.test 重写为 packBars/accumulateInto
语义）+ typecheck 无错；后端未动（644/1s 不变）。

## 1. 审查轮的关键发现（全部验证过再修）

三个并行审查（地图页 / Flow 页 / 会话与生产页）+ 自己审出的问题，共 12 条，
**每条都带 file:line 证据、修前验证、修后回归锁**。最重要的四条：

1. **写入门禁钉在回放源上**（`sourceKind === "api"`，api 是历史回放、live 才是会话源）——
   「启动沙盒 → 下命令」主链路在 UI 上整条走不通，且横幅说假话。修成 `writeGate(sourceKind, timeline)`
   纯函数：可写的唯一源是 live 且不在回看（R8）。
2. **P5 盲接受有洞**：`_expire()` 只在 list/get 调，accept 不调 → 过期提案照单全收。
   注意：**原有 P5 测试因为先 GET 了一次列表，恰好把这个洞掩盖了** —— 测试顺序掩盖 bug 的典型。
3. **P2 只守了一半**：校验未过的提案"接受按钮隐藏"而不是"禁用可见"，且**拒绝按钮一起没了**，
   理由回流通道（P3）被顺带堵死。
4. **pos_marks 从数据到渲染整条缺失**：layer.pos_marks 恒空（instantiate_spawn 硬编码 `{}`）、
   画布零引用、图层清单没有这一层。"在线看到标记"不是免费能力，是三层缺失。

其余：pan 无位移阈值误选 / pointermove 旧闭包丢增量（**是 F10 自己的漏修**，wheel 用了函数式、
紧邻的 move 没改）/ chip 档两个图层开关无效 / cluster 组归属 `null` 哨兵冲突（`[null,"inf"]` 报成
"inf"，结果看遍历顺序）/ exit_strategy 终局被画成"留在本步" / PanZoom 用内容包围盒当换图信号
（拖节点即重新 fit，拖拽与视口打架）/ 持久化竞态丢最后一段位移。

## 2. F14 的设计决定（后续改动要遵守）

### 2.1 机器覆盖层（B14 的核心决定）

map_plan 接受后**不改写**手写 `base_layout.yaml`，而是写**机器生成的**
`base_layout.overrides.yaml`。理由：手写文件每行带真机校准注释（"rax1-2 真机 can_place+挂件
2×2 双锁定"这类），机器改写必吞注释；覆盖层是机器产物、`safe_dump` 往返安全。
合并语义：**先按名删（`*_removed`）再按名增/覆盖**；覆盖条目标记 `world_fixed`——
它们存**最终世界坐标**，实例化时不再随出生点平移（手写条目才平移）。

### 2.2 move = del + add（不给 move_slot 动契约）

槽位拖动落点表示为 `del_slot` + `add_slot` 两条 hunk。后端 `apply_map_overrides` 顺序执行
天然正确（先删后增、diff 回覆盖层 = 位置覆盖），免掉 REV 12。代价：逐条审批时用户看到
"删除 X + 新增 X@新位置"两条 —— 诚实且可读，接受。**前端草稿模型同理**（拖动生成两条 hunk）。

### 2.3 画布对草稿零认知

MapCanvas 只接受 `marksOverride` / `slotsOverride`（投影结果），谁要叠加草稿谁自己
`applyDraft` 算好传进来。驾驶页、规划页、审批叠加画布共用同一块画布、同一套纯函数。
这是决策 U1（组件 = 帧→像素纯函数）的画布版。

### 2.4 槽位几何的单点例外

编辑器**输入侧**需要算新槽位的 footprint（画它、做重叠校验），前端有 `slotTl`
（= 后端 `placement.tl_from_pos`，黄金用例锁同步）。这是 C2"零几何换算"的唯一合法例外：
**输入换算**单点 + 测试锁；渲染已下发数据仍然零换算。

### 2.5 V1 语义限制（文档写明，勿误用）

- 覆盖层坐标是世界坐标，两个 spawn 变体（bl/tr）同名条目共享名字空间：bl 优先、tr 补缺，
  用户编辑 bl 视图（离线夹具 spawn=bl），tr 变体按世界坐标**不镜像**。
- 接受提案**不热更正在跑的会话**；新会话加载时生效（"重开会话后标记还在"的机制）。
- 后端 validate 做几何与名字校验；**placeable 地形校验只在前端编辑器即时做**，
  后端下次真机运行时自然生效（B14 没有地形数据在手，如实标注）。

## 3. 额外发现（ISSUES 之外的）

1. **投影页写入面的源头错误**（已修）：见 §1.1。
2. **簇归属顺序依赖**（已修）：见 §1。
3. **测试掩盖 bug 的三个例子**（教训）：P5 测试先 GET 列表掩盖了 accept 不重查过期；
   夹具 rev 落后于契约时大批测试红（这是好的，但要记住 `pnpm gen:fixtures`）；
   mock 缺新方法导致 attach 抛错（恰好证明调用路径真实存在）。
4. **paint_region 有前置依赖**：区域笔刷面向全图 RegionLayer（标签网格），而会话装配走主基
   `instantiate_spawn`（leaf_grid=None），没有区域网格可涂。**要先建全图区域 authoring 通路**
   （region.yaml → RegionLayer → 会话装配），再做笔刷。已从切片 2b 移出。
5. **live 下 proposals 不推送**（未修，见 §5.2）：子进程 FrameProducer 没接 ProposalStore。
6. **WS 断线语义三分**（已修）：握手成功后的 close = 断线；dispose 主动关不算；握手失败不算。

## 4. 流程教训（给后续执行者）

1. **edit 工具对 CRLF 长段多行匹配不可靠**（PlanningPage.tsx 反复报 not found，但字节级 diff
   完全一致）。可靠姿势：同一 run_code 内先 fresh read 全文，old_string 用 `indexOf`/slice
   **程序化提取**，替换后立即验证（grep/typecheck）。
2. **`String.replace` 静默失败比 edit 报错更危险**：不匹配时原样返回、不报错，
   一次"声明成功"的假象（"}" 写成 ">" 导致 drag 声明根本没插入）。用 replace 必带
   `includes` 断言或 replace 后 grep 验证。
3. **heredoc 追加测试时，双引号嵌套直接写坏 JS**（esbuild 在测试运行时才炸）。
   `cat >> file <<'EOF'` 里写 `toContain("{ kind: "x" }")` 必须用单引号字符串。
4. **负向断言容易假绿或误伤**：fit 断言写成"全文不得出现某依赖"误伤了合法代码（doFit 本该用
   当前内容尺寸）。优先写精确的**正向**断言；负向断言必须配正向兜底。
5. **run_code 里写模板字符串，反引号必须转义或用 `String.fromCharCode(96)`**——已踩多次。
6. **union 成员字段撞名**：`{ kind: "add_slot"; ...; kind: string }` 两个 kind，
   对象字面量后者覆盖前者，运行时操作名变成 "supply"。类别字段命名 `slotKind`。
7. **vitest node 环境没有 localStorage**：测试里 stub 内存实现（行为同浏览器）。

## 5. 下一步计划（P3 批次：agent 接入离线规划域 —— 下一执行轮的立项）

> P0（生产规划文件+干跑）/P1（导航按模式）/P2（地图规划：真地形+资源+基地命名+双下拉）
> 已全部落地（§0.2–§0.10）。**下一批次 = P3：把 agent 接到离线规划域** ——
> 让前面积累的规划文件/试算/预设点位真正被 agent 使用（用户定位的重中之重）。
> 属于"比较大"的批次，此节为压缩上下文后的接续计划。

### 5.1 P3 切片（建议顺序）

1. **agent 规划工具面**（`agent/tools.py` 扩展，直连 REST，codeagent 语义不走提案）：
   - 生产规划：`list_plans` / `read_plan` / `write_plan`（PUT 全量队列）/ `simulate_plan`
     （干跑 → 曲线 + 前瞻警报，AlertView 文案直接可读）；
   - 地图规划：`list_map_plans` / `read_map_plan`（含 reserved 预设名清单）/
     `write_map_plan`（hunks，与画布同一套校验）；
   - `start_session`（带 map_plan，进入游戏加载所选布局）——沙盒即可验。
2. **spec 更新**：AdvisorSpec 系统提示词加规划域职责（读 → 改 → 试算 → 报告
   曲线/卡点/与当前布局对比），工件是规划文件；提示词里说明预设点位名
   （蓝方主矿/蓝方主矿气井1…）可直接被 placement 引用。
3. **placement 点名引用（I8 收口）**：生产规划 build 项 placement 支持引用
   地图规划的槽位/预设名（编辑器按所选地图规划过滤选项）；涉及
   `PlacementExact.mark` 匹配 resolver 接受「规划名/点位」形式 —— 动契约要谨慎，
   先在 REST 层做映射、不动 ViewFrame（无 REV 变化）。
4. **验收**：`agent.run --dry`（FakeLLM）走通"读规划→改→试算→读警报"；
   有密钥时真 LLM 一轮；沙盒会话用 agent 选定的地图规划启动。
5. **明确不做（本轮）**：live 自动应用与 agent 常驻触发（§5.3，等 agent 接 live
   一起做）；flow 装配编辑器。

### 5.2 小项池（随手/插队用）

- 可读性轮（I1/I2/I4 合并，REV 11→12）：词表/step/策略 zh 名。
- P4 复盘记录列表：ViewRecorder 落盘目录接 registry（前端复盘模式加记录下拉）。
- I7 UI 债：泳道放大左右滚动条、自适应高度跳动、地图规划左侧栏偏大。
- 槽位 placeable 后端校验收口（terrain.placeable 栅格进校验面）。

### 5.3 live 提案的"自动应用"设计（方向已定，随 agent 接 live 落地）

用户态度（2026-08-21 四轮反馈）：**审批体系可跳过，agent 直接改（codeagent 模式）优先**。
两域拆分后语义更清晰：
- **离线规划域**：agent 直改规划文件（P0 已给 REST 面），diff/撤销兜底，不走提案；
- **live 驾驶域**：production_queue 自动应用，三道护栏保留——**R8 新鲜度门**（based_on_seq
  过期 409）、**可审计**（append-only 历史 + ops 帧 origin=agent）、**可撤销**（队列工具 op
  是天然 undo）；live 下 map_plan / flow_ast / params 根本不产生（R5 一致）。
这是对 P1 语义的正式变更，落地时写 ADR。技术选型已定：**父进程 ProposalStore 变化注入
WS 帧流**（与 OfflineSession 的 producer.proposals 同构）；在"自动应用"语义下，该推送从
"待审批收件箱"变为"应用历史流"。

### 5.4 观察

- ISSUES：I1/I2/I4 开放（§5.2）；I3/I5/I6 已关；I7（UI 债）开放；I8 收口进 P3 切片 3。
- 回归基线（P2 双下拉模型后）：后端 **669 passed / 1 skipped**，前端 **270 passed /
  21 files**，REV 11（规划端点是 REST 传输层，不动 ViewFrame 契约）。
- 运行环境：serve_api(8770) 与 vite(5273) 常驻最新代码；数据文件
  `tactical_map/data/ladder_map/terrain.json`（地形+122 资源+12 基地）已进版本库。
