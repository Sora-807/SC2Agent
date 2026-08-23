# 计划：模板化 · 生产序列补齐 · 热切 V1（2026-08-23 立项，用户拍板）

> 本文档是**自足的执行上下文**：给执行 agent 冷启动用——背景、已定决策、三批任务、
> 文件级落点与验收标准都在这里。讨论过程见 WORKLOG §0.36-§0.40 与本轮对话；
> 决策理由浓缩在 §0，不要重新讨论已拍板的结论。
> 执行纪律：一批一提交、每任务带回归锁、行为与错误文案逐字保留（除非本计划明说改）。

---

## §0 已定决策（不要再争的结论）

1. **编译期模板展开 ≠ ADR-0028 反转**。ADR-0028 废的是**运行时可组合模块**（实例
   状态/热切/多实例复杂度）；本计划的模板是 **parse 阶段文本展开**——展开后仍是单体
   策略，运行时模型零变化，全套既有校验照跑。需补一条 ADR 留档此边界。
2. **运行时切换保留且要做**（用户原话：游戏过程中要能换策略执行决策转化）。形态 =
   热切 V1（批 C）：整份策略文件切换 + 同装配约束 + step_id 续位。
3. **标识符=英文索引、展示=中文别名**（与 rev 14 槽位同哲学）。branch 补别名层；
   reason 中文表全局化（vocab 默认表 + 策略覆盖）。
4. **全程 YAML，无格式转换**：读写、校验都是 YAML，校验器=编译器，不建 JSON 层。
5. **生产队列 = 未来清单**：已执行项出队（BUILD 进在途、TRAIN 直接走），队列里没有
   "过去区"——插入天然只具后效性；按 index 插入的位置=剩余队列位置，越界 400。
   在途项已下令、不可改（cancel 仍不支持，见 §4 不做清单）。
6. **分组**：成员动态（Allocator 按 composition 租借/释放，今天就能用）；结构
   （组集合/composition）装配期固定，热切 V1 不碰（同装配约束）。

---

## §1 批 A｜模板库与展示层（无依赖，先行）

### A1 step 模板库 + 编译期展开

**模板库文件** `runtime/strategies/_lib.yaml`（`_` 前缀 = 锁定，只有人能改）：

```yaml
step_templates:
  gather:
    display_name_zh: 集结
    description_zh: 等待部队成型（模板：min_units 达标即以 FORMED 退出）
    params:
      min_units: {type: int, default: 8}
      group_slot: {type: string, default: main}
    exits:                       # 接口契约（函数签名）：本模板可能的退出
      - {kind: done, reason: FORMED}
    branches:
      - branch_id: b_ready
        display_name_zh: 成型即走
        when: {op: ">=", args: [{op: group_count, group: {param: group_slot}}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: FORMED}]
      - branch_id: b_wait
        display_name_zh: 等待
        do: []
```

**策略侧引用**（manifest 增 `imports` 节，parse 阶段展开）：

```yaml
strategy:
  id: rush_bc
  params: {enemy_base: {type: point}}
  imports:
    hold:  {from: _lib, template: hold_ramp, params: {hold_position: {param: enemy_base}, min_breakout: 3}}
    gather: {from: _lib, template: gather, params: {min_units: 1}}
    push:  {from: _lib, template: push, params: {target: {param: enemy_base}}}
  steps: [...自定义 step，与导入的混用...]
  edges: [{from: hold, to: gather, kind: done, reason: BREAKOUT},
          {from: gather, to: push, kind: done, reason: FORMED}]
```

展开规则与实现落点：
- `modules/flow/manifest.py`：`parse_strategy` 读 `imports` → 从 StrategyStore 取
  `_lib` → 每个 import 以**键名为 step_id** 实例化模板 → `{param: X}`（模板参数）
  替换为绑定值（绑定值可引用策略级 `{param: Y}`，两层词法作用域）→ 合入 steps。
- 新校验（manifest，编译期）：import 的模板存在；绑定与模板 params 声明类型匹配；
  **edges 引用导入 step 的 (kind, reason) 必须落在其 exits 里**（接错线当场红）；
  模板不套模板（lib 只含裸 step）；展开后全部既有校验照跑（图可达/死边/谓词/环出口）。
- **运行时零改动**：展开产物与手写策略同构；前端策略图/转移历史零改动。
  `static/strategy` 增 `imported: [step_id…]` 标记（REV+1），前端可显示「模板」出身（V1 可只透传不渲染）。
- 锁定语义：`_lib.yaml` 复用 strategies 的锁定前缀机制（`_` 前缀 = 不可写、可读）。

### A2 种子模板（进 `_lib.yaml`，全部经编译+sim 会话实测）

| 模板 | 来源 | 注意 |
|---|---|---|
| `gather`（集结） | default 策略现有 step | 参数 min_units/group_slot；exit FORMED |
| `push`（推进） | 同上 | attack_move_to 为**每帧续压**语义（b_move 无 when）；模板注释写明；想只发一次用 set_local |
| `hold_ramp`（堵口） | 新写 | 词表已核实：`not`+`arrived`（无 not_arrived 谓词，用 not 包）、`move_to`、`hold_position`；exit BREAKOUT |
| `garrison`（驻守入口） | 从现有策略提炼 | — |
| `armor_hop`/`inf_hop`（蛙跳前压） | tank_marine_push.yaml 提炼 | definitions 属策略级，模板内不引用 |

### A3 展示层（REV+1，与 A1 同一版）

- `branches[].display_name_zh`（可选，manifest 解析 + 校验字符串 + 前端转移历史/求值诊断显示）。
- **reason 全局中文默认表**：`flow/vocab.py` 增 `REASON_ZH`（FORMED=成型/ARRIVED=抵达/
  BREAKOUT=突围/DONE=完成/FAIL=失败/TIMEOUT=超时/…）；`static/strategy.reasons`
  = 默认表 ∪ 策略覆盖。策略内 `reasons` 只写增量。
- `agent/spec.py` 提示词 + `write_surface`：imports 写法一句话指引（看 `strategies/_lib.yaml`）。

### A4 memory lint（写钩子软提示，不拒绝）

`agent/workspace.py` 写钩子：路径 `memory/*.md` 保存成功后跑 lint——条目需带
`[ID]`（E/S 系）、strategy-notes 条目含状态字段（实测/未验证/词表已核实）；
缺则在写结果附提示（写入照常）。规则表放 `agent/memory_lint.py`（纯函数，测试锁）。

### A5 工具精简（详见 §4 审视表）

- **退役 `read_current_strategy`**：它 dump 的是写死常量而非当前会话真装的策略
  （对 live 会话有误导）。替代：session describe / observe facts 带 `strategy_id`；
  看内容 = `read strategies/<id>.yaml`。
- **`write_surface` 挂成文件 `system/surface.md`**（渲染 commands/rules/unsupported/
  readable），工具退役；提示词「不确定能不能做 → read system/surface.md」。
- `list_modules`/`read_module` 保留（生产规划域的 Python 模块库，与策略模板域不同）。

**批 A 验收**：含 imports 的策略编译通过且图正确；edges 接错 exits 编译红；`_lib`
不可写可读；S1-S5 模板全部过「编译 + sim 会话跑到对应转移」；branch 别名与 reason
默认表在前端显示；memory 缺字段有提示；read_current_strategy/write_surface 工具
消失且 system/surface.md 可读；全量回归绿 + 前端 tsc/build 绿。

---

## §2 批 B｜生产序列补齐 + loadout（与批 A 无依赖，可并行）

### B1 loadout 三件套

- `runtime/loadouts/<id>.yaml`：`{map_plan: layout-bl, strategy: rush-bc, plan: bio-tank-v4, spawn: bl?}`。
- `modules/api/routes/session.py`：`session/start?loadout=<id>` → 解析三引用
  （缺项用默认）→ 装配 + **生产序列自动 submit 入队**（plan 的 queue → runtime.submit_queue）。
- StrategyStore/PlanStore/MapPlanStore 各暴露 by-id 读取（已有）；loadout 文件不存在 → 400 带清单。
- `start_session` 工具 + StartCard 增 loadout 参数/下拉；`agent/spec.py` 一句话。

### B2 队列命令补齐

- **`insert(index)`**：index = 剩余队列位置（0=队首前），越界 400；语义文档写明
  「只影响未来，已执行项不在队列里」。落点：`modules/api/commands.py`（QueueCommand
  增 op）、`production/runtime.py`（队列 insert，帧边界由既有锁语义保证）、B7 的
  shape 统一顺带做（items 统一返回，见 REFACTOR B7）。
- **`replace_head`**：原子换队首（= remove 未执行队首 + prepend 新项一步完成，不留
  409 窗口）。在途项不受影响（已下令）。
- 两个 op 都走 based_on_seq 门（R8 既有）。

### B3 状态感知收口（I17 的一小块）

- `production/runtime.py` 的 in_flight 快照**回填来源队列序号**（flight 记 emit 时的
  queue 序号，快照输出 `from_index`）；`view/adapt.py` InFlightView 增字段（REV 同批）。
- observe 生产段文案：队首状态 + 剩余队列 + 在途（带原序号）+ op 流水 = 「跑到哪了」一眼可读。

**批 B 验收**：loadout 起会话三条全装配且队列自动入队；insert 越界 400、插 0 位 =
下一执行；replace_head 原子（无中间 409 态）；observe 能答「队列执行到第几项」；
全量回归绿。

---

## §3 批 C｜热切 V1（依赖批 A：模板让续位自然）

- **API**：`POST /api/session/swap?strategy=<id>`——对**运行中的会话**（含 live）生效；
  帧边界执行（会话 tick 循环里找个安全点，或 OfflineSession 加 swap_pending 帧首应用）。
- **约束校验**（不满足 → 409 带原因，会话不受影响）：新策略 `group_slots` 与当前
  assembly 一致；编译通过。
- **续位规则**：新策略含同名 `active_step` → 停留该 step，locals/timers 同名 step 的
  保留、否则清零；不含 → 从 initial_step 起。variables 保留（策略级）。
- **审计**：转移历史加 `swap` 事件（from strategy_id@version → to，续位或重起）；
  observe 策略段显示当前 strategy_id。
- 引擎落点：`modules/api/session.py`（swap 入口 + 约束）+ `flow/engine.py`
  （swap_strategy(manifest) 方法：替换 manifest、迁移 active_step/locals/timers、
  记 transition）。LiveSession 经子进程命令通道转发（driver 增加 swap 控制行，或
  V1 仅 offline/sim 支持、live 走重启会话——**执行时先查 driver 通道成本再定，倾向全支持**）。
- **不做**（本批明确排除）：装配热切（composition/组结构）、参数级热改（被整份
  切换覆盖）、多实例并行。

**批 C 验收**：sim 会话 mid-game swap 同名 step 续位（locals 保留可证）、异名重起、
group_slots 不一致 409；live（真机）swap 一次端到端；转移历史/observe 可见 swap；
全量回归绿。

---

## §4 工具审视（2026-08-23 盘点，19 → 17）

| 工具 | 域 | 处置 | 理由 |
|---|---|---|---|
| ls/read/glob/grep/write/append/edit/insert/delete/stat/done（11） | 文件契约 | **全保留** | 框架级原语（等同 IDE），read/grep/ls 的"多"是正交维度不是冗余 |
| observe | 现在 | 保留 | 唯一的"当前事实"入口（R8） |
| propose | 写 | 保留 | 对局内唯一产出面 |
| simulate_plan | 试算 | 保留 | 改规划必跑 |
| start_session | 会话 | 保留 | 批 B 加 loadout 参数 |
| list_modules / read_module | 生产模块库（Python） | 保留 | 服务规划域；与策略模板域不同源，暂不强行统一 |
| **read_current_strategy** | 策略 | **退役（批 A）** | dump 写死常量，非当前会话实况（live 误导）；被 `read strategies/<id>.yaml` + session strategy_id 覆盖 |
| **write_surface** | 能力清单 | **退役（批 A）** | 挂成 `system/surface.md` 只读文件——「读=文件」彻底统一；readable 清单同文件 |

> 原则沉淀（进 AGENT-LOOP §6 检查清单）：**新能力优先问"能不能是一个文件"，其次才
> 是"要不要一个工具"**。三次孤儿教训 + 本次两处退役都指向同一条。

## §5 顺序与回归纪律

- A 与 B 可并行；C 在 A 后。每批一个 commit；行为不变的搬运禁止顺手改文案。
- 每任务的最小回归锁写在各节验收里；批内全量后端 + 前端（tsc/build/361）必须绿。
- ADR：批 A 提交里带新 ADR（`docs/adr/0031-编译期模板展开.md`：背景/决定/边界/反例
  ——写清与 ADR-0028 的关系）。
