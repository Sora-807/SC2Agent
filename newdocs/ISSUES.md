# 问题清单（计划外发现，待处理）

> 记录用户实测提出、但 F10-F14 计划**没有覆盖**的问题。每条带现状、影响、候选方案与建议归属。
> 处理一条删一条（或在 PLAN.md 立项后注明去向）。
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

## I3 live 会话期间 WS 断线的前端行为未验证

- **现状**：F13 打通了 live attach，但「会话停止/崩溃后 WsFrameSource 的断线表现」
  没有专门验收（浏览器端是静默等待还是报错、要不要自动重连）。
- **影响**：真机调试中会话结束（game_time_limit 到点）后的驾驶舱体验未定义。
- **建议归属**：真机欠账清单（ARCHITECTURE §10.2）追加一条；F14 后做一轮真机回归时一并验。

---

## 已处理（留档）

- ~~实时驾驶点击后全屏错误屏（近黑底红字）且无恢复入口~~ —— 2026-08-21 修复：
  无活跃会话时进 drive 模式不再 attach（顶栏提示「等待会话」）；全屏错误屏加「返回离线模式」按钮。
- ~~Flow 卡片文字重叠（条件长文本压到右侧去向；起点与活跃点重叠）~~ —— 2026-08-21 修复：
  branch 行改 foreignObject 左右分栏（左可换行 2 行截断、右固定不换行）；头部两行重排。
