# ADR-0017 运行时实例泳道与进度 UI
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：已确认草案
- 范围：FlowRuntime 的可视化模型；浏览器中的模块进度展示

## 背景

用户设想的 UI 模型：

- 很多列长条，每个长条下有它待进行的 flow；
- 循环时能看到当前迭代与分支；
- 每个模块有 trigger 条件和内部状态进度；
- 完成绿色、失败叉号、删除即移除；
- 实时修改参数后 UI 进度跟着变化。

这个模型与 FlowRuntime 实例状态一致，应作为浏览器对局驾驶舱的核心视图。

## 决定

### 1. 每个模块实例 = 一条 lane

```text
lane = {
  instance_id,
  module_ref,
  params_snapshot,
  phase,            # IDLE/WAITING/ACTIVE/RUNNING/PAUSED/DONE/FAILED
  progress,         # 0..1，由当前 step / 策略图推导
  current_strategy, # 当前策略节点，如 ADVANCE/COMBAT
  strategy_attempt, # 当前策略第几次 attempt
  current_step,     # 策略内部当前 step
  step_branch,      # 本帧命中的 if 分支
  exit_reason,      # 最近一次退出原因
  leases,           # 已分配组
  trigger_text,     # 人可读的激活条件
}
```

UI 不自己推断状态，只渲染 FlowRuntimeState。

### 2. 泳道分区

```text
生产泳道
  m0 fast_expand          [done]      ✓
  m1 fast_medivac_production [running] 10/16 枪兵
战斗泳道
  m2 bio_push             [waiting]  等待 m1.ready
  m3 hold_ramp            [running]  守二矿
```

- 生产泳道按 `production_sequence` 顺序纵向排列。
- 战斗泳道并列，按优先级/激活时间排列。
- trigger 关系用边表示：`m1.ready → m2.start`。

### 3. 状态显示

| 状态 | 显示 |
|---|---|
| IDLE | 灰条 |
| WAITING_ACTIVATION | 蓝色，显示等待条件 |
| ACTIVE | 黄色，正在分配单位 |
| RUNNING | 绿色进度条，显示当前 checkpoint |
| PAUSED | 橙色，显示暂停原因 |
| DONE | 绿色 ✓ |
| FAILED | 红色 ✗，显示失败原因 |
| ABORTED | 灰色 ✗，显示取消原因 |

### 4. 循环与策略转移显示

- 策略层显示当前策略节点、`strategy_attempt` 和最近转移历史。
- 例如 `ADVANCE#3 ← COMBAT#2 ← ADVANCE#2`。
- 策略内部只显示当前尝试的 step DAG 进度，不展开无限历史。
- 循环历史只显示最近 K 次转移，完整历史进 trace。
- 子模块实例仍显示为新的子 lane。

### 5. 编辑与 UI 的对应

| 操作 | UI 反应 |
|---|---|
| 新增模块 | 对应泳道出现新 lane，状态 IDLE/WAITING |
| 删除模块 | lane 消失；若 running，先显示“取消中”，单位释放后移除 |
| 改 hot 参数 | lane 参数显示变化，进度保留 |
| 改结构参数 | 该 lane 重置，lease 显示释放 |
| 改 trigger | 触发边更新，waiting lane 条件文本变化 |
| retrigger | attempt + 1，lane 重新进入 WAITING/ACTIVE |

### 6. 实时更新

- 浏览器通过 API 轮询 FlowRuntimeState，而不是轮询 GameState 自己拼进度。
- 更新频率与 ObservationPacket 相同或更慢（1–2 秒）。
- 进度变化必须来自模块执行器写回的 checkpoint/progress，不允许前端本地动画伪造。

### 7. 历史与回放

- 每局保存实例状态历史：lane 快照 + 事件，存储方案见 ADR-0024。
- 复盘时可拖动时间轴查看 lane 的 phase/progress/iteration 变化。
- 历史 lane 只读，不能作为当前行动依据（见 ADR-0009）。

## 反例（明确禁止）

- 浏览器从原始单位列表自行计算“模块完成百分比”。
- UI 把循环展开成无限条 lane。
- 删除 running lane 后 UI 立刻隐藏但单位实际未释放。
- live 状态覆盖历史回放视图。
- 前端本地伪造进度动画。

## 验收标准

1. 生产泳道按顺序显示，战斗泳道并列显示。
2. running lane 能显示当前 checkpoint、iteration、attempt 和 lease 组。
3. 修改参数/trigger 后，UI 状态与 FlowRuntimeState 一致。
4. 删除 running lane 时先显示取消中，lease 释放后移除。
5. 复盘时间轴可回放 lane 状态历史。
