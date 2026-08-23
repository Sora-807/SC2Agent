# AGENT-LOOP — Agent 数据触达闭环蓝图

> 来源：2026-08-23，把 I17/I18/I19/I20 四条 issue 串成一个母题的专档。
> ISSUES.md 的 I17–I20 是它的分项索引（开放任务清单 #12/#14/#15/#16）；本档持有全景、
> 反模式、目标架构与接法取舍。
> 母题一句话：**Agent 能不能"够着"系统已有的数据与能力**——够不着就是断点，够着了才是闭环。

---

## §0 结论

四个断点（I17 看懂对局状态 / I18 看懂地图 / I19 跨会话记忆 / I20 追溯上一局）表面是四件
不相干的事，**根因是一个**：Agent 的文件/工具世界是运行时世界的 ad-hoc 部分子集——系统建了
数据和能力（录像、notes、terrain 栅格、alerts），但没全接到 Agent 的读面。同一个"孤儿"反
模式已经第三次出现。正解不是四条各打补丁，而是**一个统一原则**：把写面已验证的"文件契约
统一承载"扩到读面——运行时产物挂成 Agent 可读的虚拟文件树，Agent 用现有 ls/read/grep 翻。

---

## §1 母题：Agent 数据触达闭环

Agent 的工作循环是：**读当前状态 → 想 → 改（写规划/提提案）→ 下一轮读**。要跨会话连续，
还要 **读过去（录像/记忆）→ 记教训（写记忆）→ 下局开局读回来**。

闭环的六层触达 + 现状：

| 层 | Agent 要够着什么 | 现状 | 断点 |
|---|---|---|---|
| 当前对局状态 | 经济/部队/队列/风险/投影 | observe 有但**浅**（警报无"怎么修"、采气工不报、死步骤不报、队列在建项无映射）| I17 |
| 空间布局 | 地形可建造/预留点/槽位/标记的二维分布 | 只有一维坐标清单，得心算拼图 | I18 |
| 过去的对局 | 上一局录像（帧流/事件时间线）| **数据已存**（I13）但 Agent 够不着 | I20 |
| 跨会话记忆 | 偏好/经验/复盘/反思 | 单 memory.md 无结构；notes.jsonl 孤儿；无自反思流 | I19 |
| 系统能力边界 | 现在能做/不能做/待建 | `write_surface` 有实时清单，但没落进记忆、没派生 | I19 |
| 改动写面 | 规划/策略/提案 | ✅ **已闭环**（文件契约 + propose 自动应用 + I14 策略可写）| — |

**写面已闭环，读面半断。** 闭环 = 读面也按写面的"统一承载"原则接齐。

---

## §2 反模式：孤儿基础设施（根因）

**症状**：后端有数据 + 有 REST 端点，但 `agent/client.py` 无方法 / `agent/tools.py` 无工具 /
`agent/workspace.py` 虚拟文件树不挂 → Agent 够不着，基础设施空转。

**已确认两次**：
- `notes.jsonl`：`agent/client.py:132 note_save()` + `app.py:464-474` 端点 + `app.py:129` 装配
  都在，`agent/tools.py` 无 note 工具 → 死的 client 方法。（I19）
- `runtime/recordings/`：`live.py:87,235` 写盘 + `app.py:705,749` 端点都在，`client.py` 无
  recordings 方法 / `tools.py` 无 recording 工具 / `workspace._split` 只认 `plans/`+`map-plans/`+
  `strategies/` → 录像不在文件树，Agent 的 ls/read/grep 看不见。（I20）

**怎么发现更多孤儿**：扫 `modules/api/app.py` 的 `@app.get/@app.post` 端点，凡 `agent/client.py`
没包、`agent/tools.py` 没工具、`workspace.py` 没挂的，都是潜在孤儿。`write_surface`
（`agent_tools()`）该把这些"能读的历史数据"也列给 Agent——现在它只列了
commands/rules/unsupported/max_stale_seq，**没列"你能读哪些历史产物"**。

**根因**：写面用了一个统一抽象（`ApiWorkspace` 把 REST 虚拟成 `plans/<id>.yaml` 等路径，Agent
用 ls/read/edit/write 统一操作），读面的历史产物没进这个抽象，各活在后端各自的路由下。
**修法 = 把读面也收进文件契约。**

---

## §3 目标架构：Agent 文件世界 = 运行时世界的连贯视图

写面已证明的模式（虚拟路径 → REST，Agent 用 ls/read/edit/write 统一操作）扩到读面：

```
Agent 文件视图（虚拟路径 → 后端）
  可写区（已有，写面已闭环）：
    plans/<id>.yaml          生产规划      → REST 规划 API
    map-plans/<id>.yaml      地图规划      → 地图规划 API
    strategies/<id>.yaml    策略+装配     → 策略 API（I14）
    memory/...              跨会话记忆    → scratch 磁盘（I19 结构化）
  只读区（要补的闭环）：
    recordings/rec-*.jsonl   对局录像      → /api/recordings + 帧 jsonl（I20）
    recordings/rec-*.meta    录像 meta     → /api/recordings（清单侧车）
    notes/notes.jsonl        Agent 笔记    → /api/agent/notes（I19）
    traces/                  会话 trace    → runtime/agent-talk/traces
    region/<bbox>            二维网格快照  → 新端点 /api/map/region（I18）
```

- **可写区**：Agent 能 edit/write，保存即校验（写面纪律不变）。
- **只读区**：Agent 只能 ls/read/grep，不能改（历史产物不可变）。
- **统一工具**：ls/read/grep/glob + 现有 observe/propose/simulate_plan。**几乎不新增 bespoke
  工具**——I18 的 inspect_region 是唯一例外（它不是"读已有文件"，是"按 bbox 渲染一个新视图"，
  所以走 thin 工具 + 端点；但它的端点也可虚拟成 `region/<bbox>.grid` 只读路径，让 Agent 用 read
  调——进一步统一。先 thin 工具落地，后续可收编进文件树）。

这样 Agent 的"文件世界"和"运行时文件世界"对齐：能 ls 看到全部产物、read 翻任意一个、grep
跨产物搜（如跨录像搜"二矿"事件）。这就是用户要的"都放在一个文件夹内，能随时理解/阅读/翻阅"。

---

## §4 接法取舍：挂文件树 vs bespoke 工具

| | 挂虚拟文件树（推荐）| bespoke 工具（次选）|
|---|---|---|
| Agent 侧 | 零新工具，用现 ls/read/grep | 每产物一个 list/read 工具 |
| 一致性 | 与写面同一抽象，读写对称 | 碎片化，每个产物一套参数 |
| grep 跨产物 | ✅ 天然支持（跨录像搜事件）| ❌ 要单独实现 |
| 改动 | `workspace.py` 加只读区 + 后端给只读端点 | `tools.py` + `client.py` 各加 |
| 已验证 | 写面（plans/strategies）就是这个模式，跑通了 | notes 若走这条路又是一个孤儿 |

**例外**：I18 inspect_region 不适合挂文件（它返回的是"按 bbox 渲染的网格"，不是某个已存
文件），所以走 thin 工具 + 端点。先落地，后续可收编进文件树。

**反例**：若 recordings 走 bespoke（`list_recordings`/`read_recording` 工具），那 notes、
traces、未来的每个历史产物都得来一套——正是现在孤儿反模式的延续。挂文件树则一次接齐。

---

## §5 优先级与顺序

> **2026-08-23 进展（WORKLOG §0.33 + §0.36）**：①②已落地——I20 只读区挂载 + 录像
> 衍生摘要 + readable 清单 + I19 记忆结构与种子全齐；**④ I18 也落地**（用户拍板文件
> 形态：maps/<源>/<bbox>.md，§4 预言的"收编进文件树"直接成为首选）。孤儿反模式清单
> 清空；剩 ③（I17 可观测性）。

四项互相不阻塞，但 **I20 是根因项**（解一个反模式顺便解多个孤儿），优先级最高：

1. **I20 文件契约闭环（挂 recordings/notes/traces 只读区）**——根因项，一次接齐多个孤儿。
   不依赖任何重构（`workspace.py` 加只读区；后端只读端点录像已有 `app.py:705,749`，notes 已有
   `app.py:464-474`，traces 是磁盘文件直读）。**与 I15/REFACTOR 的 app.py god-file 拆分正交**
   ——挂只读区是 `workspace.py` 的事，不往 app.py 闭包里塞逻辑。
2. **I19 记忆结构化 + notes.jsonl 随 I20 一起接**——notes 孤儿的去留 I20 一起解（挂成 `notes/`
   只读 + Agent 用 write 落到 scratch 的 `memory/`）。system-capabilities 从 `write_surface`
   派生。improvement-notes 流是提示词约定，不改后端。
3. **I17 可观测性深度**——AlertView 加 `remediation_zh`、采气工 shortfall 警报、死步骤检测。
   独立于文件闭环，可并行；死步骤检测的输出也可挂进 alerts 只读区（与文件闭环同源）。
4. **I18 inspect_region**——独立 thin 工具 + 端点，数据齐备，不依赖前三项。

---

## §6 闭环检查清单（给未来功能用）

新加一个后端能力/数据时，过一遍——全过才算闭环，缺一项就是下一个孤儿：

- [ ] **先问"能不能是一个文件"**：`system/surface.md` / `strategies/_lib.yaml` /
      `maps/<bbox>.md` / `recordings/<id>.md` 都是这个原则的产物。三次孤儿工具教训
      （notes.jsonl、read_current_strategy、write_surface）+ 两处退役（2026-08-23 工具
      审视 19→17）都指向同一条：**新能力优先是一个只读文件，其次才是一个工具**。
- [ ] `agent/client.py` 包了方法？
- [ ] `agent/tools.py` 暴露了工具，**或** `agent/workspace.py` 挂了只读虚拟路径？
- [ ] `system/surface.md`（`agent_tools()` 渲染）把它列进了"Agent 能读什么"？
- [ ] 提示词告诉 Agent 它存在 + 怎么用？

> 这条清单本身可塞进 `system/surface.md` 的输出——让 Agent 也能反过来审计"我有没有够着"。

---

## §7 与其他专档/issue 的关系

- **REFACTOR.md（I15）**：modules/ 代码债。与本档正交——REFACTOR 是"代码整洁"，AGENT-LOOP 是
  "Agent 够不够着数据"。唯一交汇：I20 挂只读区别往 app.py god file 里塞（挂 `workspace.py`）。
- **I12**（Agent 角色边界 vs 真实缺口）：本档是 I12 B 类缺口的具体落地——B1 谓词补全是 I17
  的死步骤检测前提；B2 装配↔规划交叉校验是 I17 的深化。
- **开放任务清单 #1 常驻监听**：常驻循环 = 本档闭环的"运行态"。常驻前，读面（I17/I18/I20）
  和记忆（I19）要到位，否则常驻的 Agent 每轮都在失忆和够不着里空转。

---

## 附录：四 issue 现状对照

| issue | 断点 | 数据在吗 | Agent 够得着吗 | 接法 |
|---|---|---|---|---|
| I17 | 警报无"怎么修"+采气工不报+死步骤不报+队列在建项 | 警报系统在（8 kind，`alerts.py`）| 部分（observe 有、深度缺）| AlertView 加 `remediation_zh` + 新 shortfall kind + 死步骤检测 |
| I18 | 地图二维布局 | ✅ 全在（terrain+reserved+slots+marks）| ❌ 只有一维清单 | thin 工具 inspect_region + 端点 |
| I19 | 跨会话记忆 | memory.md 在（持久）；notes.jsonl 在（孤儿）| 部分（scratch 可写、无结构；notes 够不着）| 结构化分文件 + notes 挂文件树 + improvement 流 |
| I20 | 追溯上一局录像 | ✅ I13 已存 `runtime/recordings/` | ❌ 无方法/无工具/不在文件树 | 挂 `recordings/` 只读虚拟区 |
