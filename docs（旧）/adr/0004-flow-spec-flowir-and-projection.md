# ADR-0004 flow：FlowSpec、FlowIR、投影语义与模块库

- 状态：已确认草案
- 范围：`flow` 模块，替代旧文本 DSL 运行体系

## 背景

旧 flow 的设计目标之一是让 agent 在战斗中自由编写复杂 DSL。实践结果是：

- 复杂 DSL 写不出来、容易写错。
- 行编辑缺少幂等性，容易制造重复行。
- 运行期反复解析文本，并存在模块级全局战术状态。
- 简单指令反应慢，复杂流程不可靠。

重新讨论后确定：人类玩家不接触 flow；agent 也不应在战斗中创作新模块。因此 V1 删除旧自由文本 DSL 的运行路径；模块作者离线使用的 step/策略图语法必须编译后运行。

## 决定

### 1. 三层结构

```text
模块库 Module Library
   ↓ agent 插拔组合
FlowSpec（结构化装配描述）
   ↓ 编译
FlowIR（运行时执行格式）
```

- `Module Library`：离线创作、测试、验收后的模块。
- `FlowSpec`：agent 实际编辑的对象。
- `FlowIR`：运行时执行器消费的编译结果。

V1 不实现旧自由文本 DSL 的运行期 parser。模块作者语言在离线编译；未来若需要文本导入/导出，只作为离线工具，并另开 ADR 决定，绝不回到执行关键路径。

### 2. 模块双语义：行为与投影由受限操作集自动生成

- production 模块使用**受限声明式操作集**编写。
- combat 模块使用 **if/elif/else 分支 step + 策略图** 编写（见 ADR-0021），编译为可持久化的 FlowIR 控制流图。
- 编译时自动生成两份语义：

```text
behavior:   FlowIR      → 游戏里逐帧执行
projection: ProjectionSpec → planner 逐秒模拟（仅 production 可投影）
```

可投影操作示例：

```text
keep_workers(target)
train_while_affordable(unit, limit)
build_when(type, when, placement)   # placement 必填：精确坐标/区域/预留区/placement policy
upgrade_when(name, when)
expand_when(condition)
assign_gas_workers(...)
fixed_sequence(...)
parallel(...)
```

build 操作没有 placement 时，模块编译失败，错误码 `MISSING_PLACEMENT`。planner 对区域/策略类 placement 给出“预计位置”，实际建造时以 ConstructionScheduler 最终计算为准。placement/footprint 的坐标语义见 ADR-0027。

不可投影操作示例：

```text
move / patrol / attack / load / 架起 / 微操
依赖敌情的分支
```

因此：

- 使用可投影操作集写的模块自动获得投影语义，作者不需要手写模拟代码。
- 战斗/微操操作保留在执行层，但被投影层显式排除。
- 模块声明自己的投影等级：

```text
projectable = full | partial | none
```

  - `full`：全部经济行为可预测。
  - `partial`：经济段可预测，战斗段标注为不可模拟边界。
  - `none`：纯战术模块，planner 不给经济预测，只做结构校验。

### 3. FlowSpec 格式

- 磁盘持久化：JSON。
- 内存规范模型：Python dataclass。
- LLM 的编辑面：工具调用，不裸写 JSON 文件，更不写 Python 代码。

典型 FlowSpec 应保持短小：

```json
{
  "id": "tvz_2rax_3cc",
  "race": "terran",
  "production_sequence": [
    {"id": "m0", "ref": "reaper_expand", "version": 1, "params": {"reapers": 1}},
    {"id": "m1", "ref": "bio_mine", "version": 1, "params": {"marine_target": 30, "medivac_target": 4}}
  ],
  "combat_overlays": [
    {"id": "m2", "ref": "hold_ramp", "version": 1, "params": {}}
  ],
  "triggers": [
    {"when": "m1.ready", "start": "m2"}
  ]
}
```

`production_sequence` 自带先后语义，不允许给生产模块之间添加 trigger；triggers 只用于启动 combat_overlays。
```

如果 FlowSpec 普遍超过约 40 行，说明模块切得太碎或逻辑下沉不足，应把逻辑下沉到模块库，而不是把装配文件写复杂。

### 3.1 中文别名与本地化

- FlowSpec / FlowIR 内部只存稳定类型 ID，如 `terran/marine`，不存中文名。
- 模块库中的单位、建筑、科技参数可以使用中文别名**作为输入**，工具层通过 game 数据目录解析成稳定 ID 后落盘。
- 展示、diff、agent 观察、错误提示使用数据目录中的 `display_name_zh / description_zh`。
- 中文别名属于 game 数据目录，flow 运行时不需要理解中文，也不增加 flow 的语义负担。
- 模块本身的 `name / description` 提供中文，供模块库和 agent 描述使用。

### 4. 模块库是系统边界

- 模块带 schema、参数范围、种族、前置、版本和测试用例。
- 新模块只能离线创建，并经过：
  1. 结构校验；
  2. constraint 校验；
  3. planner 模拟；
  4. 测试场景验收；
  5. 入库。
- live 中禁止创建模块，只能：
  - 选择模块；
  - 插入/移除模块实例；
  - 修改参数；
  - 调整顺序/触发；
  - 提交前强制 validate + simulate。

### 5. 编辑工具：少量操作式工具 + 离线文件策略

V1 不采用“LLM 直接写/改 FlowSpec JSON 文件”作为 live 编辑方式；也不提供过多细粒度工具。编辑面收敛为少量工具：

```text
list_modules
get_module
read_flow
flow_edit(patch)
validate_flow
simulate_flow
commit_flow
```

`flow_edit(patch)` 是唯一的 FlowSpec 变更入口，patch 只允许以下幂等操作：

```text
insert_production(module_ref, instance_id, position?, params?)
insert_combat(module_ref, instance_id, params?)
remove_module(instance_id)
set_param(instance_id, path, value)
set_trigger(instance_id, trigger)
set_production_order([instance_id, ...])
move_production(instance_id, position)
```

- 生产模块先后顺序由 `production_sequence` 显式定义；`insert_production` 不写 position 时追加到剩余队列末尾；`set_production_order` 用于整体重排。
- 战斗模块加入 `combat_overlays`，不参与生产顺序；激活由 trigger / manual dispatch 决定。
- 每次 patch 原子应用：要么全部成功，要么不改变原 FlowSpec。
- `retrigger_instance(instance_id)` 不属于 flow_edit patch：它不改变 FlowSpec，因此不需要重新 validate/simulate，但必须写 flow_event。
- `commit_flow` 前必须自动执行 validate 和 simulate。未通过时默认拒绝提交，或按后续 ADR 决定“仅提交带警告版本”。

文件式编辑仍保留，但只用于以下场景：

- 离线模块库中模块文件的创作与测试；
- FlowSpec 的导出、diff、版本树展示；
- 人工在离线工具中检查或备份。

这样兼顾两点：live 中每个变更都经过 schema 校验、可回滚；离线复杂模块仍能像文件一样创作和做版本管理。

### 6. 运行时状态

- FlowIR 执行器不解析文本。
- 战术状态、征用单位等可变状态必须放在执行器实例或战术上下文中，禁止模块级全局变量。
- 单位征用通过 `agent` 的单位池仲裁（见 ADR-0005/0006），执行器只向池申请。

## 反例（明确禁止）

- live 中出现“创建新模块”工具。
- agent 直接写一段自由文本 flow 并热替换。
- 模块作者复制一份手写的模拟代码来“翻译”自己的 flow。
- 投影时静默忽略战斗操作，却把结果标记为 full。
- 运行期每帧 parse JSON 或文本。
- 战术状态存模块级可变全局变量。

## 验收标准

1. 一个只含可投影操作的模块，编译后 planner 能给出 full 投影。
2. 一个含攻击/移动操作的模块，编译后投影等级为 partial 或 none，且未模拟部分被明确列出。
3. FlowSpec 只能通过编辑工具变更，且每次变更可校验、可回滚。
4. 模块库新增模块不需要改动 flow 运行时代码。
5. 同一 FlowSpec 编译两次得到等价 FlowIR 与 ProjectionSpec。
