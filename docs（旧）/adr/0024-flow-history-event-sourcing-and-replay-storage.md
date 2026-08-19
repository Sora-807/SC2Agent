# ADR-0024 Flow 历史存储：事件溯源 + 快照 + 版本树

- 状态：已确认草案
- 替代：无
- 范围：复盘/时间线中如何还原任意时刻的 FlowSpec、FlowRuntimeState、GameState 视图

## 背景

需要回答：复盘时，任意时刻的 flow 和 flow 状态怎么得到？

两种候选：

1. 根据 agent trace 和初始 workspace 推导；
2. 直接保存多个 flow 版本。

结论：不二选一，采用**事件溯源 + 定期快照 + 版本树引用**的混合方案。

## 决定

### 1. 三个存储层次

```text
A. workspace 初始快照
   - 开局前 workspace 的 FlowSpec / 模块版本 / 地图 / 生产计划基线

B. flow_events.jsonl（权威事件日志）
   - 所有影响 flow 与运行时状态的事件，机器可读，单调递增

C. runtime snapshots
   - 定期保存同一时刻的完整 GameState + FlowRuntimeState + flow_revision + event_cursor
   - 关键策略转移 / 提交 / 失败时立即保存
```

每份 runtime snapshot 结构：

```text
{
  "seq": ...,
  "game_time": ...,
  "game_state": {...},          # 完整 GameState，含 map_layers
  "flow_runtime_state": {...},
  "flow_revision": "...",
  "event_cursor": N
}
```

### 2. flow_events 事件类型

```text
flow_committed      # 新 FlowSpec 提交，含 revision/hash
flow_patch          # flow_edit patch，含修改前后 revision
retrigger           # 实例重触发，attempt 变化
strategy_transition # 策略层边转移，含 from/to/reason
step_enter          # 进入 step
step_exit           # 退出 step，含 ExitRecord
runtime_snapshot    # 指向某份 runtime snapshot 文件
```

每个事件至少包含：

```text
seq
game_time
flow_revision
instance_id / strategy / step（按事件类型）
payload
```

### 3. 版本树仍然是 FlowSpec 的真相

- `workspace/versions` 保存每次提交的 FlowSpec 版本。
- flow_events 只引用 `flow_revision`，不重复保存整个 FlowSpec。
- 版本树负责回答“过去某个提交点 flow 长什么样”。
- 事件日志负责回答“版本之间发生了什么状态变化”。

### 4. 时间点还原算法

```text
seek(t):
  1. 找到 t 之前最近的 runtime snapshot
  2. 得到该快照中的 GameState + FlowRuntimeState + flow_revision
  3. 从快照 event_cursor 开始，按 seq 重放 flow_events 直到 t
  4. 输出该时刻的 GameState 视图 + FlowRuntimeState + FlowSpec revision
```

- 任意提交点之间的 FlowSpec 可精确还原。
- GameState 视图与 FlowRuntimeState 可精确还原到 snapshot 或关键事件点。
- 任意帧连续还原是 V1.5；V1 只保证快照/事件点。

### 5. 为什么不只依赖 agent trace

agent trace 是给人/agent 看的推理记录，不是稳定的机器接口：

- 可能包含大量 reasoning，体积大；
- 格式可能变化；
- 不一定包含每个运行时状态转换。

因此：

```text
agent trace   → 用于查看思考过程
flow_events   → 用于状态还原
版本树        → 用于 FlowSpec 版本
```

三者并存，各司其职。

### 6. 存储控制

- runtime snapshot 默认每 5 秒一份；关键转移即时一份。
- flow_events 全量保留到对局结束。
- 对局结束后：
  - 保留版本树、关键事件、周期快照；
  - 精细 if 日志按配置保留最近窗口或抽样；
  - 可选压缩中间快照，只保留可重建所需的最小集合。

## 反例（明确禁止）

- 用 agent reasoning trace 作为状态还原的唯一依据。
- 每帧保存完整 FlowSpec 文件。
- 只有 FlowSpec 版本，没有运行时事件。
- 时间线回放时现场重新调用 LLM 推导。
- 回放修改当前 live 状态。

## 验收标准

1. 给定任意一次 flow_committed 事件，能还原该时刻的 FlowSpec。
2. 给定任意 runtime snapshot 或关键事件点，能还原 GameState 视图与 FlowRuntimeState。
3. 从一个 snapshot 重放到下一个 snapshot，得到的状态与该 snapshot 一致。
4. 版本树、flow_events、agent trace 各自独立可读。
5. 回放不会改动 live 状态。
