# ADR-0014 Flow 编辑与状态切换矩阵

- 状态：已确认草案
- 范围：修改参数、顺序、删除、新增、trigger 时，各模块实例状态如何变化

## 背景

需要穷举 FlowSpec 编辑场景，明确每次编辑是否重置状态。旧设计最危险的行为是“任何编辑都重建执行器并清空全部状态”。本 ADR 定义逐实例状态迁移。

## 决定

### 1. FlowSpec 结构区分生产序列与战斗覆盖层

```json
{
  "id": "example",
  "production_sequence": [
    {"id": "m0", "ref": "fast_expand", "version": 1, "params": {}},
    {"id": "m1", "ref": "bio_production", "version": 1, "params": {}}
  ],
  "combat_overlays": [
    {"id": "m2", "ref": "hold_ramp", "version": 1, "params": {}},
    {"id": "m3", "ref": "bio_push", "version": 1, "params": {}}
  ],
  "triggers": [
    {"when": "m1.ready", "start": "m2"}
  ]
}

`production_sequence` 的 m0→m1 顺序由列表本身表达，不写 trigger；triggers 只允许启动 combat_overlays。
```

- `production_sequence`：生产阶段，按顺序执行。
- `combat_overlays`：战斗模块，由 trigger / 手动 dispatch 激活，可多个并行。
- 战斗模块不进入生产顺序，避免“改顺序导致战斗状态被生产阶段重排”。

### 2. 生产阶段只前进，不倒退

- 已完成的生产阶段标记为 `phase_done`，持久保存。
- 修改 order 只能影响尚未开始的阶段。
- 已 active/running 的生产阶段继续执行到 done/aborted。
- 已完成阶段不会被普通编辑重新激活；要重跑必须显式 `retrigger_instance(instance_id)`。
- 新增生产模块默认追加到未完成阶段队列末尾；也可指定插入到剩余队列中的位置。

### 3. 状态切换矩阵

#### 3.1 修改参数

| 参数类型 | 实例当前状态 | 结果 |
|---|---|---|
| `live_editable=true` | 任意非终态 | 参数即时生效，状态与进度保留 |
| `live_editable=true` | DONE/FAILED | 仅更新配置快照，不自动重跑 |
| `live_editable=false` | 非终态 | 释放 lease，实例重置到 IDLE，其他实例不受影响 |
| `live_editable=false` | DONE/FAILED | 保持终态，记录“配置已变，需显式 retrigger” |

示例：

```text
m1 已训练 10/16，marine_target 从 16 改为 12
→ m1 继续训练 2 个，不重置

m2 正在推进，target_region 从二矿改为主矿
→ 只重置 m2，释放其 lease，重新选择/执行
```

#### 3.2 修改顺序

| 对象 | 结果 |
|---|---|
| 尚未开始的生产阶段 | 按新顺序重新排队，不重置状态 |
| active/running 的生产阶段 | 继续执行，不受影响 |
| 已完成的生产阶段 | 冻结，不因顺序变化重跑 |
| 战斗覆盖层 | 不参与生产顺序；用 priority/trigger 控制，顺序字段对其无状态影响 |

#### 3.3 删除实例

| 实例状态 | 结果 |
|---|---|
| IDLE / WAITING | 直接删除，状态归档 |
| 生产 active/running | 取消未来请求；已开工的建筑/训练默认继续；状态标记 aborted |
| 战斗 active/running | 先执行该模块 `on_cancel`（默认对 leased 单位发 stop），再释放 lease，状态归档 |
| DONE / FAILED | 直接归档，不重跑 |

#### 3.4 新增实例

| 类型 | 结果 |
|---|---|
| 生产模块 | 从 IDLE 开始，加入未完成队列 |
| 战斗模块 | 从 WAITING_ACTIVATION 开始，等待 trigger/manual；不影响任何现有实例状态 |

#### 3.5 修改 trigger

| 场景 | 结果 |
|---|---|
| 给 WAITING 实例新增/修改 trigger | 下一帧按新 trigger 评估，不重置 |
| 移除某 trigger | 该实例保持 WAITING（或按 `trigger=none/manual`），不重置 |
| 修改 running 实例的 trigger | 不影响其当前生命周期，只影响未来激活 |

### 4. 显式 retrigger

以下情况需要显式 `retrigger_instance(instance_id)`：

- 想重新执行一个 DONE/FAILED 的战斗模块；
- 想重跑一个已完成的生产阶段；
- 对终态实例修改了 `live_editable=false` 参数并希望其重新执行。

`retrigger_instance` 会创建新 attempt，旧 attempt 状态归档到 trace。它不改变 FlowSpec，不重新 validate/simulate，但写 flow_event。

### 5. 状态迁移统一原则

1. 编辑默认是“局部生效”：只影响被编辑实例，不波及其他实例。
2. 运行中的状态尽量保留；只有模块替换或结构参数变化才重置本实例。
3. 已完成阶段不自动回滚、不自动重跑。
4. 所有重置都先释放 lease / 取消未来请求，不允许泄漏单位。
5. 每次编辑后的新 FlowSpec + 状态迁移结果必须写入 trace，可审计、可 undo。

## 反例（明确禁止）

- 任何 FlowSpec 编辑触发全量状态清空。
- 修改 order 导致已完成生产阶段重跑。
- 删除 running 战斗模块后，其 leased 单位继续执行原攻击命令且未释放。
- 新增战斗模块导致现有模块重置。
- 终态模块因参数修改被悄悄重新激活。

## 验收标准

1. 修改 hot 参数不重置实例，修改 structural 参数只重置该实例。
2. 重排未开始阶段不重置 active/running 阶段。
3. 删除 running 战斗模块后，其 leased 单位收到 stop 并回到 free。
4. 新增生产/战斗模块时，现有实例状态全部保留。
5. 修改 trigger 不重置任何实例。
6. 所有状态迁移可回放、可 undo。
