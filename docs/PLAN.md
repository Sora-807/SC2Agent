# sc2Agent 后续计划（交接用）

> 版本：对应提交 d42aa1e。基线：后端 **614 passed** / 前端 **96 passed / 9 files**，契约两侧 REV = 9
> （modules/view/schema.py:45 ↔ web/src/contract/index.ts:49）。
> 本文档吸收并取代此前的新docs/plan-frontend-ux.md（用户反馈驱动的 F10-F14 可用性整改提案），
> 并将其 §9 三个决策点落为结论（见 §12）。配套：docs/ARCHITECTURE.md（当前架构全貌）。
> 范围：**第一批** F10-F14 + B12-B14（可用性整改）；**后备批次** B15+（后端能力缺口，见 §9）。

---

## 0. 计划要解决什么

F0-F9 建成的驾驶舱功能齐全（每个数据面都有页面、提案审批闭环、真机 live 已通），但用户实测
反馈指出四条体验问题，其中三条是**结构性缺陷**而非样式问题：

| # | 用户原话（摘要） | 归类 |
|---|---|---|
| 1 | 滚轮缩放时窗口跟着滚；地图看不出悬崖/斜坡；建筑没名字；单位全是无法区分的小点 | 布局与输入（F10）+ 地图视觉语言（F11） |
| 2 | 策略图无法拖动、卡在屏幕外，感觉全是文字 | 策略图（F12） |
| 3 | 规划与地图完全错位，离线不能在地图上放标记 | 地图编辑器（F14） |
| 4 | 分不清离线/在线，布局没有主次、功能不知道在哪 | 模式轴与信息层级（F13） |

核心判断：问题 2 是「SVG 没有视口」，问题 3 是「写入路径从来没建」，问题 4 是
「三个正交轴被压成一条扁平栏」。只有问题 1 的一半（视觉主次）是纯样式。
完整自评（功能上符合设想、可用性落后于设想）见 ARCHITECTURE.md §11。

---

## 1. 根因诊断（代码级证据，截至 d42aa1e）

> 每条带 file:line。**已于 HEAD `1cc1c2e` 机械 re-verify（25 条论断全部成立，11 处行号已修正）**。
> 核对方法：按 file:line 读该行并用正则断言它确实是所声称的东西，未命中则在 ±12 行窗口内定位真实行号。
> 注意 `d42aa1e` **未改动 `web/`**（只改 live.py / sc2_adapter.py / run_session.py + 新测试），
> 故前端漂移源于原提案的范围书写不精确，不是代码变动。

### 1.1 滚轮缩放导致页面滚动（问题 1，波及所有页）

| # | 根因 | 证据 |
|---|---|---|
| A | e.preventDefault() 是空操作：React 18 把 wheel/touchstart/touchmove 注册在 root 且强制 passive，onWheel 里的 preventDefault 无效（控制台有 "Unable to preventDefault inside passive event listener"），滚轮同时缩放+滚动祖先。React 既定设计（facebook/react#22794），必须绕开 | web/src/canvas/MapCanvas.tsx:149 |
| B | 外壳允许长高（min-h-screen 是最小高度不是固定高度）+ main 是 overflow-auto → 永远有东西可滚 | web/src/App.tsx:50、:55 |
| B2 | 地图页强制 min-h-[420px]，小窗口把中间行顶出视口 | web/src/pages/MapPage.tsx:30 |
| C | 绘制 effect 依赖里有 props（每渲染新对象）→ 每渲染都拆建 rAF 循环 | web/src/canvas/MapCanvas.tsx:140 |
| D | host 尺寸一变就 fitViewport → 改窗口/收对话栏丢缩放平移 | web/src/canvas/MapCanvas.tsx:73（effect :71-74）|

### 1.2 地图看不出地形、主次不分（问题 1）

| # | 根因 | 证据 |
|---|---|---|
| E | 地形数据早就有（height/pathable/placeable 三栅格已下发），缺的只是画法 | modules/driver/sc2_adapter.py:132-140、modules/view/statics.py:147-152 |
| F | 高度映射成连续绿色渐变 46+min(120, v*3)，离散台地被糊成一团 | web/src/canvas/MapCanvas.tsx:79-81 |
| G | pathable 是 alpha 70 红/绿平涂，抢对比度；斜坡完全没表达 | web/src/canvas/MapCanvas.tsx:82-83 |
| H | 建筑 = 半透明矩形 + 无说明数字（画 producing.length），分不清兵营/工厂 | web/src/canvas/MapCanvas.tsx:308-311 |
| I | 单位 = scale*0.3 实心圆，50 个枪兵是 50 个不可辨点；建筑/单位/槽位无形状区分 | web/src/canvas/MapCanvas.tsx:316-349（圆点 :322-328；:350 起才是敌方聚类块）|
| J | 20+ 槽位常驻 alpha 0.55 虚线 + 每矿点一个圆点，噪声压过信息 | web/src/canvas/MapCanvas.tsx:252-283（槽位 :252-264 / 矿点 :265-283）|

### 1.3 策略图不能拖不能缩（问题 2）

| # | 根因 | 证据 |
|---|---|---|
| K | 固定 width/height 手写 SVG 塞在 overflow-auto div：无平移/缩放/拖动/fit，节点被 BFS 列推出容器后拉不回 | web/src/pages/FlowPage.tsx:65-66（固定 width/height 的 svg 套在 overflow-auto div 里）、:43-44（尺寸算式）|
| L | 节点 132x52 矩形内只有 step_id；有信息量的东西都在图下两张卡里 →「全是文字」 | web/src/pages/FlowPage.tsx:93-137（节点）vs :138-190（分支卡/转移历史卡）|
| M | branch 才是边，但 branch 关在卡里，边是匿名线只标 reason，讲不出「什么条件→去哪」 | web/src/graph/ast.ts:59-77（renderBranches）vs :100-146（layout 只吃 edges）|
| N | 布局 = BFS 深度=列 + indexOf 定行，无降交叉，回边靠固定 +70 偏移绕行 | web/src/graph/ast.ts:100-146（回边 +70 在 FlowPage.tsx:80-82）|
| O | rect 用 x - NODE_W/2 + NODE_W/2 配补偿 transform，半宽 off-by-one 温床 | web/src/pages/FlowPage.tsx:102 |
| P | flow?.strategies.at(0) 违反契约红线 C6（列表形状不假设长度 1；plan-frontend.md §2.4） | web/src/pages/FlowPage.tsx:24 |

### 1.4 规划与地图错位、无法在图上放标记（问题 3）

| # | 根因 | 证据 |
|---|---|---|
| Q | 「地图规划」是三张只读列表（槽位/点位/区域），根本没有画布 | web/src/pages/PlanningPage.tsx:56-109（MapPlanning 全函数）|
| R | 真实 authoring 路径 = 手改 YAML + 跑真机 can_place 扫描脚本；「图上放标记」无入口 | modules/tactical_map/data/ladder_map/base_layout.yaml |
| S | 写入路径整条是断的：APPLICABLE_KINDS = {"production_queue"}，map_plan 被显式拒绝（理由：需要 F9 的 patch 模型） | modules/view/proposals.py:41-44 |
| T | 契约早已预留 kind:"map_plan" 与 preview:{kind:"map_overlay", changed_slots}，patch 模型从没建 | docs/plan-frontend.md §6 |

### 1.5 分不清离线/在线（问题 4）

三个正交的轴被压成一条 6 图标扁平栏：

| 轴 | 取值 | 现在体现在哪 |
|---|---|---|
| 帧源 | fixture / mock-live / api 回放 / live 会话 | 两个下拉，交叉相乘 |
| 模态（权限） | 离线编辑 / 实时驾驶 / 复盘 | 没有任何控件 |
| 主题页 | 概览/地图/生产/Flow/规划/调试 | 左侧六个等权图标 |

| # | 根因 | 证据 |
|---|---|---|
| U | SourceKind 含 "live"，attach("live") 会构造真 live 源 + ReviewableSource（才有「回到实时」）；但 SessionBar 从不传 "live"：「启动沙盒」调 attach("api","live") → 真 live 模式 UI 不可达，caps.live=false，live 会话上反显「播放 x4/暂停」 | web/src/store/frames.ts:26、:146-153；web/src/shell/SessionBar.tsx:29-40（源下拉只给 fixture/mock-live/api）、:58（启动沙盒调 attach("api","live")）|
| V | live 在一个下拉是 fixtureKey、在另一个是 SourceKind，外壳里最糟的一处 | web/src/shell/SessionBar.tsx:16-40（两个 select 相邻）|
| W | 规划页用 sourceKind==="live" 做守卫，但 UI 永远产生不出这个值 → 警告永不触发，背后无真实约束 | web/src/pages/PlanningPage.tsx:34 |
| X | 131 处字号声明 121 处 <=12px（text-xs 30 / text-[11px] 40 / text-[10px] 51），108 处前景 neutral-500/600 → 头条和脚注长得一样 | grep -ro 统计 web/src/**/*.tsx |
| Y | 页脚硬编码「契约 ViewFrame v0.1（rev=1）」，两侧 REV 都是 9，黄金位置显示错误信息 | web/src/App.tsx:75 vs modules/view/schema.py:45 |

---

## 2. 决策记录（U13-U20，每项一句话理由）

| # | 决策 | 理由 |
|---|---|---|
| U13 | 外壳固定一屏（h-[100dvh] overflow-hidden），滚动权下放页面自己的 pane | 驾驶舱语义是「一屏全都在」；外壳能长高就永远有东西可滚（dvh 而非 vh：带地址栏时 vh 会比可视区大） |
| U14 | 画布类交互一律 native addEventListener + {passive:false}，不用 React onWheel | React 的 passive 是框架级决定；写成约定（G3）比每次踩坑再查快 |
| U15 | 地形改画「台地 + 悬崖描边 + 斜坡」，量化与边缘检测放前端渲染层 | 与现有 bakeGrid 调色板同类，不是规则计算（不违 C7）；一旦斜坡要被命名/被谓词引用就必须上移后端（B14 备注） |
| U16 | 形状语言：建筑=矩形、单位=圆角 chip、标记/槽位=菱形/虚线框、区域=只填充、命令=细虚线；不许跨类复用 | 颜色已被 owner（4 色）+状态（ok/warn/error）占满，区分类别只剩形状一个通道 |
| U17 | 单位按 (owner, stable_id) + 空间邻近聚成 chip（枪兵 24），LOD 门控，放大还原个体 | 用户明确要聚类点；LOD 防放大后仍糊成一团 |
| U18 | 显示层聚类放 canvas/cluster.ts，文件头写明「仅显示层 LOD 聚合，任何决策路径不得读它」 | enemy_clusters 是后端字段且恒 null（词表登记 forbidden）；不把边界写进代码早晚有人拿显示聚类喂谓词 |
| U19 | 模式提为一级轴（离线编辑/实时驾驶/复盘），一个控件三个值；干掉 SourceKind x fixtureKey 交叉积 | 模态是独立轴，折进页面列表就必然「规划和地图错位」 |
| U20 | live 下 authoring 控件置灰 + tooltip 引用规则（R5），不隐藏 | 控件消失正是「功能不知道在哪」的成因；置灰同时教会规则 |

---

## 3. 视觉与交互红线（G1-G8，每个任务都受约束）

| # | 红线 |
|---|---|
| G1 | 外壳固定一屏；任何页面不得让外壳产生滚动，页面自己决定哪个 pane 滚（U13） |
| G2 | 视口状态（scale/pan）只在换图时重置；resize/侧栏开合必须保住用户视角 |
| G3 | 画布交互（wheel/pointer）走 native 监听 + {passive:false}；禁止用 React onWheel 做需要 preventDefault 的事（U14） |
| G4 | 形状语言不跨类复用（U16）；颜色只表达 owner 与状态，不表达类别 |
| G5 | 一切聚合/量化只做显示，不产出任何被读回的语义字段（U15/U18；不违 C7） |
| G6 | 每个信息元素必须落在 6 个字号 token 之一（metric-xl/metric/label/body/note/mono）；禁止新增裸 text-[Npx] |
| G7 | 禁用必须带理由（tooltip 引用 R/C/G 编号），禁止静默隐藏功能（U20） |
| G8 | 沿用全部既有红线，本轮不放宽任何一条：**C1-C8**（契约红线，`docs/plan-frontend.md` §2.4 —— **唯一权威号段**，有 17 处源码注释背书）、**P1-P7**（审批红线，同文件 §6）、**U1-U12**（决策，同文件 §1）、**R1-R8**（需求红线，`docs/需求文档-v0.1.md`）；后端侧另有 **A1-A8**（架构不变量）与 **Q1-Q7**（提案机制）见 ARCHITECTURE.md §4.3/§6.2 —— 那是**另外两张清单**，引用时务必带字母不要只写编号 |

---

## 4. 任务总览与依赖顺序（含决策结论 1）

| 顺序 | 任务 | 依赖 | 成本 | 后端 | 产出 |
|---|---|---|---|---|---|
| F10 | 布局与输入修复（外壳一屏/native wheel/视口不重置/rAF 依赖收敛） | 无 | 半天 | 无 | 滚轮不再滚页面（全站） |
| F11 | 地图视觉语言（台地-悬崖-斜坡/建筑标签与关键数/单位聚类 chip/槽位矿点降权/theme token） | F10 | 1 天 | B12·B13 可并行 | 地图「像地图」 |
| F13 | 模式轴与信息层级（模式切换/真 live attach/chrome 强调/分组图标栏带角标/字号 token 全站化） | F10 | 1 天 | 无 | 一眼看出在线离线 |
| F12 | 策略图升级（共用 PanZoom/fit/节点拖动持久化/branch 锚定边/卡片节点/分层布局） | F10·F11 | 1 天 | 无 | 图「像图」 |
| F14 | 地图编辑器 + map_plan 落地（统一 MapView + 格点吸附工具 + 结构化 hunk） | F11·F13 | 2-3 天 | B14 必需 | 离线能放标记、在线能看标记 |

**执行顺序（结论 1）：F10 → F11 → F13 → F12 → F14。**

理由：F10 是一切的地基（其余四项都建在它修好的视口/滚动模型上）；F11 建立 theme token 与
形状语言，F13 复用 token、F12 复用 PanZoom 与 token，所以 F12 排在 F13 之后而不是紧跟 F11；
F14 需要 F11 的 placeable 渲染与 F13 的模式轴同时到位，且是唯一动契约的一项，排最后。
（原提案问「F13 要不要提到 F11 前」——不。提前会做两遍 token 化。）

**前置状态更新**：原提案担心「未提交的 B4 live 地形改动（live.py 的 terrain 控制行 + test_terrain_frame.py）
会卡住 F11」——该改动**已随 d42aa1e 提交**，F10 可以直接开工。

---

## 5. 任务详述

### F10 布局与输入修复

**目标**：滚轮缩放时页面绝不移动；改窗口大小不丢视角。全站问题，不只地图页。

**改动**
1. web/src/App.tsx：外壳根 min-h-screen → h-[100dvh] overflow-hidden；main 的 overflow-auto →
   overflow-hidden；补齐 min-h-0 链（外壳 → 中间行 → main → 页面根）。
2. 各页面自己声明滚动区：地图页画布 pane overflow-hidden + 右侧栏 overflow-y-auto；
   概览/生产/调试/规划页根 h-full min-h-0 overflow-y-auto。删掉 MapPage.tsx:30 的 min-h-[420px]。
3. web/src/canvas/MapCanvas.tsx：wheel 改 native 监听（useEffect + el.addEventListener("wheel", h,
   {passive:false})），删 React onWheel。
4. 同上：绘制 effect 的 props 进 useRef，依赖收敛到 [vp]（不再每渲染重建 rAF 循环）。
5. 同上：fitViewport 只在首次尺寸就绪与换图时调；resize 保 scale、按旧中心重锚 pan。
6. 页脚（App.tsx:75）从每页常驻改为状态浮层，rev 读 contract 真值（顺手修根因 Y）。

**测试**
- web/tests/viewport.test.ts（新）：fitViewport → zoomAt → resize 重锚，断言世界中心点不变、scale 不变。
- web/tests/layout.test.tsx（新）：渲染 App 骨架，断言根无 min-h-screen、main 非 overflow-auto。

**验收（手动，逐条过）**
- 地图滚轮缩放 → 页面纹丝不动，控制台无 passive 警告；
- 缩放后改窗口/收起对话栏 → 缩放与平移保留；
- 六页逐个切换 → 外壳无滚动条，只有页面内部该滚的地方滚。

---

### F11 地图视觉语言

**目标**：一眼看出高低地与斜坡；一眼看出每栋建筑是什么、关键数字是多少；一眼看出场上兵种与数量。

**(a) theme token（新 web/src/canvas/theme.ts + web/src/shell/tokens.ts）**
6 个字号 token（metric-xl/metric/label/body/note/mono）+ 语义色（ok/warn/error/blocked/live/owner x4）+
形状语言常量 + z 顺序与 alpha 预算表。一处定义全站引用（G6）。

**(b) 地形改画（web/src/canvas/grid.ts + MapCanvas.tsx）**
1. 高度量化成 level：height 栅格直方图聚类，每格得 level 索引，有序深→浅 slate 阶梯上色。
2. 悬崖画描边不画填充：4 邻域 level 不同 → 格边 1px 硬线，烤进同一张 ImageData（零运行时成本）。
3. 斜坡 = 跨 level 的可走格：pathable=1 且邻域跨越 >1 level → 单独色/斜纹。
4. 不可走不再红色平涂：只压暗/去饱和，饱和色预算留给单位/建筑/标记。

**(c) 建筑内标签 + 关键数（MapCanvas.tsx）**
footprint 内按 LOD 分级渲染：
- 第一行 catalog.short_name_zh（B13；未到位前用 display_name_zh 截断并如实标注）；
- 第二行是该类建筑唯一重要的数：主基地 矿 12/16 气 3/6（B12 的 base_tag 分组，
  数据来自 frame/economy.nodes[] 已有的 workers/capacity）；兵营/工厂 训 2/2；在建 62% +
  现有进度条（进度绝不插值，U5/ADR-0017）；挂件不画（作母建筑角标）。
- LOD 阶梯：屏幕尺寸不够 → 色块字形 → 点。防糊成一团的关键。

**(d) 单位聚类 chip（新 web/src/canvas/cluster.ts）**
按 (owner, stable_id) + 空间邻近聚类，一簇一个圆角 chip：枪兵 24 / 坦克 4 / SCV 16；
owner 上色 + 1-2 字字形 + 数字；编组带组标签（inf/armor）让地图与 Flow 页同词。
缩放过阈值 → 拆个体 → 再放大 → 个体 + 选中光环。**文件头必须写明 U18 边界声明。**

**(e) 降权（web/src/canvas/layers.ts）**
- 矿点默认关（on:false），改名「矿区细节」；饱和度由主基地那行 矿 12/16 表达。
- 槽位只在「规划模式」或「有在途建造指向它」时实线；其余降为四角小刻度。

**测试**
- web/tests/terrain.test.ts（新）：量化——3 级台地 height 栅格恰好 3 个 level；
  悬崖边缘检测在人工阶梯上位置正确；斜坡掩码只在跨 level 的 pathable 格为真。
- web/tests/cluster.test.ts（新）：同类同 owner 邻近单位聚成一簇且 count 正确；跨 owner/跨
  stable_id 不合并；超过 LOD 阈值还原个体数。
- web/tests/theme.test.ts（新）：扫描 web/src/**/*.tsx 断言不存在裸 text-[Npx]（G6 机制化）。

**验收**
- 三组夹具 + live：能指着地图说出「这是高地、那是斜坡」；
- 主基地直接读 矿 x/x 气 x/x，兵营读 训 x/x；
- 50 枪兵 + 4 坦克场面，chip 一眼读出兵种与数量；放大还原个体；
- terrain=null 仍不报错（纯色底降级不回退）。

---

### F13 模式轴与信息层级

**目标**：任何时刻一眼知道「在线还是离线、现在能做什么」；功能找得到。

**(a) 模式轴（web/src/shell/SessionBar.tsx + web/src/store/frames.ts）**
1. 一级控件 模式：离线编辑 · 实时驾驶 · 复盘。模式决定：合法帧源、authoring 是否启用、全局 chrome 配色。
2. 两个下拉合并为 模式 +（随模式变化的）数据源；干掉 SourceKind x fixtureKey 交叉积。
3. 让 live 成为真正会调 attach("live", …) 的模式（修根因 U）→ caps.live=true →
   「回到实时」出现，live 会话上不再显示「播放 x4」。
4. 规划页守卫从 sourceKind==="live" 改读模式（修根因 W），并真的据此禁用编辑。

**(b) 模式的视觉不可忽略**
live = 顶部绿色细线 + 呼吸点 +「跟随实时」；复盘 = 琥珀 +「只读回看 08:12」；
离线 = 中性蓝 +「离线草稿，不影响任何对局」。

**(c) 图标栏分组（web/src/shell/IconRail.tsx + route.ts）**
分 驾驶（概览/地图/生产/Flow）· 规划（地图规划/生产规划/Flow 装配）· 诊断（调试/帧检查），
带分组标题；规划组按模式门控（置灰 + tooltip 引 R5，G7）。
有事的项打角标：待审批 N / 警报 N / 队首阻塞 —— 现在缺失的「视觉引导」。

**(d) 字号 token 全站化**
把 121 处 <=12px 字号 + 108 处 neutral-500/600 收进 F11 的 6 个 token：资源/供给/待审批/
队首阻塞原因走 metric；出处与红线注释走 note。注释不删，但不能和数据同权重。

**测试**
- web/tests/mode.test.ts（新）：模式 → 合法帧源集合映射；选「实时驾驶」时 store 收到
  attach("live", …) 且 caps.live===true（锁死根因 U 不复发）。
- web/tests/rail.test.tsx（新）：分组结构；角标数来自帧字段而非本地推断；
  离线模式驾驶组可用、live 模式规划组置灰且带 tooltip 文案。

**验收**
- 不看下拉只看 chrome 就能说出当前离线/在线/复盘；
- live 会话上「回到实时」可用、「播放 x4」不出现；
- live 下点规划入口 → 置灰并说明「R5：live 不能创建/编辑模块与 Strategy」；
- 待审批提案出现时图标栏对应项立刻出现角标。

---

### F12 策略图升级

**目标**：图能拖能缩能 fit，节点自己承载信息，边讲得出「什么条件 → 去哪」。

**(a) 共用视口（新 web/src/canvas/useViewport.ts + web/src/graph/PanZoom.tsx）**
把 canvas/view.ts 的平移缩放抽成可复用 hook + SVG 包装：拖动平移、滚轮缩放（native 非 passive，G3）、
「适应窗口」按钮、1:1 按钮、双击节点居中。有 fit 就不可能有节点卡在屏幕外。
节点拖动允许，位置按 strategy.id@version 存 localStorage（必须带 version，否则重编译的策略
继承过期坐标），保留「重新布局」按钮兜底。

**(b) 卡片节点 + branch 锚定边（web/src/pages/FlowPage.tsx + web/src/graph/ast.ts）**
- 节点头部：step_id + 第几次进入 + step_elapsed（active 时）+ 热度（从 transitions 数进入次数，
  环才读得出是环）。
- 节点主体：按顺序一行一个 branch，每行 = 紧凑条件 chip（arrived(inf,...)<=3）+ 去向，
  本帧命中的行高亮。
- 每个 branch 一条边，锚在该 branch 所在行，按 未走过/走过/最近一次 上色（修根因 M）。
- 清掉 x - NODE_W/2 + NODE_W/2 补偿 transform 写法（修根因 O）。
- 加实例选择器，不再 .at(0)（修根因 P / C6）。

**(c) 分层布局（web/src/graph/layout.ts，从 ast.ts 拆出）**
不引依赖，从「BFS 列」升到 Sugiyama-lite：最长路径分层 → 层内重心排序（2-3 轮）降交叉 →
回边走保留车道的正交/弧线绕行。2-10 节点规模约 120 行。超过约 30 节点再评估 ELK。

**测试**
- web/tests/layout.test.ts（新）：有环图不崩；重心排序后交叉数 <= BFS 版；回边被标记且走保留车道；
  不可达节点仍被布出。
- web/tests/graph.test.ts（扩展现有）：branch → 边一对一；branch_hit 高亮落在正确行；
  节点位置持久化键含 version（换 version 不复用旧坐标）。

**验收**
- 蛙跳夹具上：拖得动、缩得动、fit 一键收回全部节点；
- 不看下面卡就能读懂「哪个条件命中、要去哪」；
- 环在图上读起来是环（热度 + 回边可见）。

---

### F14 地图编辑器 + map_plan 落地

**目标**：离线能在图上放标记与格点规划；在线能看到自己标了什么。

**第 1 步：统一成一个 MapView**（web/src/canvas/MapView.tsx，由 MapCanvas 提升），接受 tools prop。
地图页与规划工作台必须是同一块画布。模态：驾驶（只读，悬停/选中）/ 规划（工具启用），由 F13 模式轴驱动。

**第 2 步：格点吸附编辑工具**
- 点位工具（PosMark）：点一下放命名点，吸附格心/格角（可切），行内改名、拖动、删除。
- 槽位工具（BuildSlot）：先选 footprint 尺寸（2/3/5，取 catalog.size），光标同时显示真实
  footprint 矩形与 reported_position 点 —— ADR-0027「偶数尺寸半格」的坑在 authoring 阶段被消灭。
  按该尺寸合法锚点吸附；对已有槽位 + placeable 栅格做重叠校验，非法摆放当场拒。
- 区域工具：笔刷往标签网格涂 leaf region（数据模型本来就是「标签网格 + 索引」
  regions.leaf_grid/leaf_index，笔刷是天然编辑器，ADR-0029）。
- 编辑时把 placeable/pathable 作为背景 —— 在真实地面上放而不是猜。
  这一步让真机 can_place 扫描从 authoring 工具降级为验证步骤。

**第 3 步：草稿是结构化 patch 不是画布状态**
每次编辑追加 MapPlanHunk：
{kind:"add_mark"|"move_mark"|"del_mark"|"add_slot"|"move_slot"|"del_slot"|"paint_region", payload, text_zh}。
满足「人与 agent 同一表示」，直接插进现有提案信封（hunks[] 可逐条接受）。

**第 4 步：后端 B14 —— 补上写入路径**（见 §6）

**测试**
- web/tests/map-edit.test.ts（新）：吸附（奇偶尺寸各一组，断言 footprint 与 reported_position
  同时正确且前端零几何换算，只用后端给的换算或工具函数单点）；重叠校验拒绝非法摆放；
  编辑序列 → hunk 序列一对一。
- tests/api/test_map_plan_apply.py（新，后端）：hunk → YAML patch → 重载后 layer 字段正确；
  非法 hunk（重名/越界/重叠/不可建）被拒且理由结构化；map_plan 不再在 NON_APPLICABLE_KINDS 里
  （锁死根因 S 已修）。
- tests/view/test_proposals.py（扩展）：map_overlay preview 的 changed_slots 正确。

**验收**
- 离线模式：图上放 3 个点位 + 1 个 3x3 槽位 → 提为提案 → 审批接受 → 重开会话后标记还在；
- 非法摆放（压已有槽位/落在 placeable=0）→ 编辑器当场拒绝并说明原因；
- 实时模式：同一块画布看得见刚标的东西，且编辑工具置灰带理由（G7）。

---

## 6. 后端配套（B12-B14，含决策结论 2、3）

| # | 任务 | 为谁 | 契约影响 |
|---|---|---|---|
| B12 | frame/economy.nodes[] 增 base_tag（该矿/气归哪个基地） | F11 主基地 矿 12/16 气 3/6 | REV+1 |
| B13 | static/catalog.entries[] 增 short_name_zh（footprint 内标签用 2 字短名） | F11 建筑内标签 | REV+1 |
| B14 | map_plan 可应用：patch applier 改 authoring YAML + 热重载 + 校验；补 map_overlay preview | F14 | REV+1 |

- **结论 2：short_name_zh 后端加字段**，不做前端截断。U6/C4 说 zh 文案一律来自后端；
  前端截断「陆战队员→陆战」会在别的词上出洋相。
- **结论 3：斜坡现在不上移后端**（不建 terrain.levels/ramp 掩码字段）。等它真被 flow 谓词
  或点位命名引用时再上移（U15），否则是为未用的能力付契约成本。可选 REV 11→12 保留为占位。
- **B12/B13 合并成一次 REV+1**（同一轮契约改动），B14 单独一次。
- B14 细节：patch applier 改 authoring YAML（base_layout.yaml / region YAML）+ 热重载 layer，
  前置校验（名字唯一、尺寸合法、不重叠、placeable 通过）；从 NON_APPLICABLE_KINDS 移出
  （modules/view/proposals.py:41-44）；补 map_overlay preview，审批时在同一块画布看
  「当前 vs 提案」、变更槽位高亮。
- B14 模态约束：R5 管的是模块与 Strategy，不管地图数据；但「live 不编辑」的姿态对地图数据
  同样正确 —— 只在离线模式开放编辑；live 下把规划作为只读叠加层显示。
  「在线能看到标记」是免费的：标记本来就在 static/map 里、渲染在同一块画布上
  （F14 第 1 步的直接回报）。


---

## 7. 契约改动汇总

| 轮次 | REV | 改动 | 触发任务 |
|---|---|---|---|
| 第一次 | 9 → 10（✅ 已落地） | frame/economy.nodes[] 增 base_tag；static/catalog.entries[] 增 short_name_zh | B12·B13（F11 需要） |
| 第二次 | 10 → 11 | map_plan 的 hunk payload 模型 + map_overlay preview 字段 | B14（F14 需要） |
| 可选 | 11 → 12 | static/terrain 增 levels + ramp 掩码 | 仅当斜坡需要被命名/被谓词引用时（结论 3 已决定：现在不建） |

每次都按红线 C8 走：REV+1 + docs/plan-frontend.md §2 修订 + 后端 contract test 同步 + 两份计划同改。

---

## 8. 不做清单（防范围蔓延）

- 不做可拖拽 dock 布局与布局持久化 —— 沿用 U9（固定布局 + 可折叠）。F12 的节点拖动是图内坐标，不是面板 dock。
- 不做 WebGL / Pixi —— F11 的量化与描边都是烤一次的位图，Canvas2D 足够（沿用 plan-frontend §3）。
- 不引 React Flow / ELK —— F12 自写 Sugiyama-lite；超过约 30 节点再评估（沿用 U8 选型修正）。
- 不做 AST 结构编辑器 —— 沿用 U8：F12 只做「看得懂」，改结构留 next。
- 不做主题系统 / i18n 框架 / 移动端适配 —— token 是一套，不是可切换主题；zh 文案仍全来自后端（U6/C4）。
- 不在前端算任何语义派生量 —— 聚类/量化只做显示且不被读回（G5/U18）；enemy_clusters 仍等后端。
- 不放宽 R5 —— live 中不创建/编辑模块与 Strategy；F14 的地图编辑只在离线模式开放。
- 不伪造进度动画 —— 位置可插值，进度与计数绝不插值（U5/ADR-0017），F11 建筑标签同样受约束。

---

## 9. 后端后续（B15+，本计划之外的 backlog）

第一批 F10-F14/B12-B14 之后，后端能力缺口按依赖粗略排序如下（与 ARCHITECTURE.md §11.1 对应）：

| # | 缺口 | 现状 | 解锁条件/备注 |
|---|---|---|---|
| B15 | 多策略实例 | 编译期拒绝 >1 | 架构已预留（U10 列表形状、allocator lease）；放开前先定策略间调度语义 |
| B16 | timer / step-local 变量 | 编译期拒绝（T8） | flow 词表已登记未实现；需要 DSL 级设计与 rev 变化 |
| B17 | 敌人聚类 | 恒 null | 区域层已有；实现后词表解除 forbidden，前端显示层聚类（U18）退役 |
| B18 | 崩溃恢复 | 无 | 会话挂了要重建；ViewRecorder 快照锚点可做恢复起点，但语义要定 |
| B19 | mechanics 规则层 | 空模块 | power/addon 等（D11）；届时 constraint/planner 接入 |
| B20 | 生产 authoring 统一 | planner 计划与 live 队列两套语法 | view.projection 桥是翻译层；统一后 bridge 可删 |

真机验证欠账（属流程而非功能）：
- landing 的驱动侧确认回填（三态 apply 的 ok 分支真机走全）；
- 完整生产链（build → 队首门控 → 在途确认）真机重跑；
- protoss / zerg 覆盖（目前只验了 terran）。

---

## 10. 回归基线与提交粒度

- **回归基线（d42aa1e 实测）**：后端 uv run python -X utf8 -m pytest tests -q → 614 passed；
  前端 cd web && pnpm test → 96 passed / 9 files。本轮任何任务都不许让这两个数字下降。
- 每项任务结束：上面两条全绿 + pnpm typecheck 无错，再进下一项。
  本轮新增测试文件预计 9 个（F10:2 / F11:3 / F13:2 / F12:1 扩 1 / F14:1 扩 2 + 后端 1），
  完工时前端应在 96 + 约 60 条量级。
- F10 与 F13 结束：必须人工过一遍六个页面（滚动/模式 chrome 是自动化测不到的部分）。
- F14 结束：真机或沙盒会话跑一遍「离线放标记 → 提案 → 接受 → 重开会话仍在」完整回路，
  日志/截图存档 docs/。
- git 提交粒度：一任务一提交，commit message 带 F10: / B12: 前缀（沿用 plan-step-tank §3 约定）。
- 前置状态：原提案担心的 B4 live 地形改动已随 d42aa1e 提交，无阻塞前置。

---

## 11. 相对原 UX 提案的变更记录（吸收/删除）

| 项 | 处理 |
|---|---|
| 用户反馈表（§0） | 吸收（本计划 §0 摘要版） |
| 根因诊断 A-Y（§1） | 吸收（本计划 §1，保留 file:line 证据） |
| 决策 U13-U20（§2） | 吸收（本计划 §2 原文保留） |
| 红线 G1-G8（§3） | 吸收（本计划 §3 原文保留） |
| 任务总览与顺序（§4） | 吸收，执行顺序定为 F10→F11→F13→F12→F14 |
| 任务详述（§5） | 吸收（本计划 §5，文字微调为计划语气） |
| 后端 B12-B14（§4.1） | 吸收进本计划 §6 |
| 契约改动汇总（§6） | 吸收进本计划 §7 |
| 不做清单（§7） | 吸收（本计划 §8） |
| 回归基线与粒度（§8） | 吸收进本计划 §10 |
| 「待批准」提案语气 | 删除（本文件即获批后的计划） |
| 「前置：未提交 B4 live 地形改动」 | 更新为已完成（d42aa1e），不再是前置 |
| §9 三个待确认决策点 | 落为结论（本计划 §12） |
| 后端缺口 backlog | 新增 §9（来自 ARCHITECTURE.md §11.1，原提案未覆盖） |

---

## 12. 三个决策点的结论（原提案 §9）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 顺序按 F10 → F11 → F13 → F12 → F14 还是把 F13 提到 F11 前？ | 按表中顺序 F10 → F11 → F13 → F12 → F14。F13 要复用 F11 的 token，提前会做两遍 |
| 2 | short_name_zh（B13）是后端加字段，还是前端截断 display_name_zh？ | 后端加字段。U6/C4 规定 zh 文案一律来自后端，前端截断会在别的词上出洋相 |
| 3 | 斜坡要不要现在就上移后端（terrain.levels/ramp）？ | 先不。等它真被 flow 谓词或点位命名引用时再上移（U15），否则是为未用能力付契约成本 |

---

## 附：本计划与 ARCHITECTURE.md 的交叉引用

- 契约红线 **C1-C8**（唯一权威号段）：`docs/plan-frontend.md` §2.4 —— 本计划 §1 根因 P 引的 C6、
  §3 G5 与 §2 U15 引的 C7、§7 引的 C8 **都是这一套**
- 契约信封 / topic 字面量（带 `frame/` 前缀）/ REV 1-9 历史 / **架构不变量 A1-A8**：ARCHITECTURE.md §4
- 会话模型与子进程协议（B14 热重载要动它）：ARCHITECTURE.md §5
- 审批红线 **P1-P7**（F14 的 map_plan 提案 UI 必须满足）：`docs/plan-frontend.md` §6 ——
  其中 **P2**（`validation.ok=false` 时接受按钮禁用但**必须可见**）与 **P5**（`anchor` 帧过期
  自动置「已失效」、禁止盲接受）是 A/Q 两套里**没有**的 UI 硬要求，F14 要自己实现
- 提案通道机制与 **Q1-Q7**（后端侧不变量，F14 的 applier 走它）：ARCHITECTURE.md §6
- 前端架构与 U1-U12（本计划 U13-U20 的续接）：ARCHITECTURE.md §7
- 已知缺口与可用性自评：ARCHITECTURE.md §11
- 测试布局（新测试文件的落点）：ARCHITECTURE.md §12

