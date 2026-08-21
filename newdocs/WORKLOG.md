# 工作记录：可用性整改执行轮（2026-08-21）

> 本文记录「三个并行审查 → 修复批次 → WS 断线 → F14 全链」这一执行轮的工作、决策与发现。
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

## 5. 下一步计划（建议顺序）

### 5.1 功能性（按价值排）

1. **I5 投影泳道共享时间轴**（前端，不动契约）：曲线与 Gantt 目前两张卡、各画各的轴，
   "这段产能对应矿曲线的哪一段"读不出来 —— 投影因此没法用于决策。真泳道化 + bar 内 zh 文字。
2. **live proposals 推送 + agent 自动应用设计**（见下 5.3）：动后端传输层。
3. **paint_region 前置**：全图区域 authoring 通路（region.yaml → RegionLayer → 会话装配），
   之后才有区域笔刷。这同时是观察层区域化摘要的前置。
4. **槽位 placeable 后端校验**：B14 现在只做几何校验；把 terrain.placeable 栅格引入
   ProposalStore 的校验面（需要地形在手，接 B4 静态面数据即可）。
5. **F14 验收收尾**：真机/沙盒跑一遍"离线放标记/槽位 → 提案 → 接受 → 重开会话仍在"，
   并验证 world_fixed 在真机 cc 偏移下的表现。

### 5.2 可读性轮（I1/I2/I4 合并，一次 REV+1）

- `flow.vocab.dump_vocabulary()` 的 predicates/operators 加 `name_zh`（同时喂 agent 提示词卡片）；
- manifest 加 step/策略级 `display_name_zh` / `description_zh`，随 static/strategy 下发；
- 面板「?」帮助图标 + 组名/槽位 zh（assembly 的 group_id 加 display_name_zh）。
- 三者共一个前置：static/schema 与 static/strategy 加 zh 字段，一次 REV 11→12 做完。

### 5.3 live 提案的"自动应用"设计（需要用户拍板，已讨论但未落地）

用户倾向：**live 下 production_queue 提案自动应用**（不等审批——真机对局逐条审批时机早过了；
队列修改可逆、低风险、本来就只过 constraint 门控）。精确边界：
- live + production_queue → 自动应用，但三道护栏保留：**R8 新鲜度门**（based_on_seq 过期 409）、
  **可审计**（append-only 提案历史 + ops 帧 origin=agent）、**可撤销**（队列工具 op 是天然 undo）；
- live 下 map_plan / flow_ast / params 等 **根本不产生**（R5 一致）；
- 离线/复盘保留审批流。
这是对 P1 语义的正式变更，值得写 ADR。**不紧急**：agent 尚未接 live（常驻触发未做），
建议与 agent 常驻触发一起做。技术选型已定：**父进程 ProposalStore 变化注入 WS 帧流**
（与 OfflineSession 的 producer.proposals 同构，避免前端长出第二个轮询源）；
在"自动应用"语义下，该推送从"待审批收件箱"变为"应用历史流"。

### 5.4 观察

- ISSUES I1/I2/I4 保持开放（见 5.2）；I3 已关闭；I5 排 5.1.1；I6（真机首帧等待期提示）未动。
- 回归基线：后端 638 passed / 1 skipped，前端 223 passed / 19 files，REV 11。
