# ADR-0013 模块实例状态与热切换语义

- 状态：已确认草案
- 范围：模块运行状态存哪里、提交新 FlowSpec 后旧状态是否保留、是否回溯

## 背景

需要明确回答：

- 模块/战术如何拥有状态？
- 每帧只有 GameState 时，模块自己的进度存哪里？
- 提交新 flow 后，是不是全部清空重来？
- 战术执行到一半被切换，状态是否丢失？
- 状态不满足时会倒退吗？

## 决定

### 1. GameState 是世界真相，不是模块状态

- `GameState` 只描述当前世界。
- 模块状态是**派生运行状态**，单独存放于 `FlowRuntimeState`。
- 模块状态包括：当前 phase、attempt、已分配 lease、行为进度游标、计时器、参数快照等。
- combat 模块（ADR-0021）编译为 FlowIR CFG 后，持久化的是 `当前策略 + 策略 attempt + 内部 step 游标 + variables`，不持久化任何代码对象。

### 2. 实例状态机

每个模块实例拥有状态机：

```text
IDLE → WAITING_ACTIVATION → ACTIVE → RUNNING → DONE
                                ↘ PAUSED
                                ↘ FAILED
                                ↘ ABORTED
```

- `WAITING_ACTIVATION`：activation 尚未满足，持续等待。
- `ACTIVE`：activation 满足，正在选择单位/等待分配。
- `RUNNING`：已绑定动作，正在执行。
- `PAUSED`：因缺单位、用户接管、显式暂停而暂停，条件恢复可继续。
- `DONE / FAILED / ABORTED`：终态；ABORTED 表示实例被删除或主动取消后归档。

状态机只向前或暂停，**不自动回溯**。

### 3. 状态保存与恢复

```text
FlowRuntimeStateStore
  key = (game_session_id, flow_id, instance_id)
  value = ModuleInstanceState
```

- 每帧执行后或关键转换后原子保存。
- 状态与 FlowSpec 分开存储，修改 FlowSpec 不会自动清空状态。
- agent/driver 重启后，如果仍是同一 game session，可恢复状态；session 结束则状态作废。
- 恢复分两类：
  - **骨架状态**：phase / strategy / attempt / step 游标 / mode / persistent 变量，从 FlowRuntimeStateStore 恢复。
  - **派生进度**：已训练数量 / 建筑进度 / 队列长度 / 计时器，一律用最新 GameState 重新计算，不恢复旧值。
- 恢复流程：最近 runtime snapshot → 恢复骨架 → 重放 flow_events 到当前 seq → 用当前 GameState 重校准派生进度。

### 4. 每帧执行顺序

```text
① 读当前 GameState
② 读 FlowRuntimeState
③ 对每个模块实例：
     state + GameState → 下一个 state + 本帧 Intents
④ 提交 Intents
⑤ 原子保存新的 FlowRuntimeState
```

上一帧的模块状态不是靠“重新扫 GameState 推出来”，而是从 state store 恢复。

### 5. 提交新 FlowSpec 时的状态保留规则

`flow_edit(patch)` 提交时，先 diff 旧 FlowSpec 与新 FlowSpec：

| 变化 | 状态处理 |
|---|---|
| 实例 id 未变，module ref + version 未变，参数是 hot 参数 | 状态完整保留 |
| 实例 id 未变，但 module ref/version 改变 | 释放该实例 lease，重置该实例，其他实例状态保留 |
| 实例 id 未变，但修改了结构性参数（如动作目标、selector 类型） | 重置该实例，其他实例保留 |
| 实例被删除 | 释放 lease，状态归档 |
| 新实例加入 | 从 IDLE 开始 |
| 仅 order / trigger 变化 | 不重置任何实例；只影响未来激活判定，已 active/running 的实例继续跑完当前生命周期 |

参数是否 hot 由模块 schema 声明：

```text
params:
  marine_target: {type: int, live_editable: true}
  target_region: {type: region, live_editable: false}
```

- `live_editable=true`：运行中修改立即生效，进度不重置。
- `live_editable=false`：结构性参数，修改后重置该模块实例。
- 默认：生产数量类参数 hot；selector/action 目标类参数非 hot。

### 6. 战术执行到一半切换后面的战术

示例：

```text
m1 = fast_medivac_production，已训练 10/16 枪兵
m2 = bio_push，正在推进
用户插入 m3 = hold_ramp 并调整 trigger
```

结果：

```text
m1 状态保留，继续从 10/16 训练
m2 状态保留，继续推进
m3 从 IDLE 开始
```

不会“整个 flow 重新来一轮”。

如果用户说“不要前压了，把 m2 换成空投”：

```text
m2 被替换为 bio_drop
→ 旧 m2 lease 释放
→ 新 m2' 从 IDLE 开始
→ m1 继续生产，不受影响
```

### 7. 状态不满足时怎么办

- activation 尚未满足：`WAITING_ACTIVATION`，等待。
- 运行中缺单位 / lease 丢失：按模块 fallback 进入 `PAUSED`、扩大选择范围、降级或 `FAILED`。
- 生产前置暂时不满足：等待/重试，由 ConstructionScheduler 排队。
- **不倒退**：m1 完成后不会因为 m2 失败而回到 m1；m2 失败后保持 FAILED，直到显式重新触发或新 patch 重置该实例。

### 8. 显式重置

只有以下情况会重置状态：

- 用户/agent 显式 `reset_flow`；
- 替换模块 ref/version；
- 修改结构性参数；
- 新 game session；
- 实例从未出现在新 FlowSpec 中。

## 反例（明确禁止）

- 每帧根据 GameState 从零重新推导模块进度。
- 提交新 FlowSpec 时无差别清空全部实例状态。
- 修改 m3 导致 m1/m2 状态重置。
- 模块失败后自动倒退到上一阶段重新执行。
- 状态只存在进程内存，进程重启即全部丢失。

## 验收标准

1. m1 训练到 10/16 时提交包含 m3 的新 FlowSpec，m1 仍从 10/16 继续。
2. m2 running 时调整 m3，m2 不重置。
3. 替换 m2 的 module ref 后，仅 m2 重置并释放 lease，m1 不受影响。
4. `live_editable=true` 的参数修改不重置实例；`live_editable=false` 的参数修改重置该实例。
5. m1 done 后 m2 failed，m1 不倒退、不重新执行。
6. 同一 game session 内重启 agent，骨架状态可恢复；派生进度与最新 GameState 重校准；新 game session 状态作废。
