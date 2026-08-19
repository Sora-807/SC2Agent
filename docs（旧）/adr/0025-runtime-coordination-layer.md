# ADR-0025 runtime 协调层：职责、端口、依赖与节拍

- 状态：已确认草案
- 替代：无（补充 ADR-0001 缺失的运行时归属）
- 范围：ConstructionScheduler / WorkerManager / UnitPool / TaskAllocator / FlowRuntimeStateStore / GameSessionController / ProjectionMonitor 等运行时组件的归属与解耦

## 背景

ADR-0006/0011/0013 定义了多个运行时组件，但 ADR-0001 的模块表和依赖图中没有它们的归属。为避免重蹈旧项目“driver 什么都管”的覆辙，必须固定一个 runtime 层。

## 决定

### 1. runtime 层位置

```text
game
 ↑
tactical_map / mechanics
 ↑
constraint
 ↑
planner
 ↑
flow
 ↑
runtime
 ↑
agent / api / browser

driver 只依赖 game，通过 GamePort 注入 runtime
```

### 2. runtime 子域与职责

| 子域 | 组件 | 只做什么 | 不做什么 |
|---|---|---|---|
| session | GameSessionController | 游戏进程/会话状态机、start/stop、心跳、崩溃检测 | 不直接发游戏命令，不懂 flow |
| world | ObservationService | 接收 GameState，调用 mechanics.LayerComputer 合成 map_layers，生成 ObservationPacket，管理 seq 与过期 | 不直接调 driver，不推理 |
| allocation | UnitPool | free / user_leased / flow_leased 所有权 | 不计算选择策略 |
| allocation | TaskAllocator | 评估 Selector，执行 pool_key/share 冲突分配，生成 lease | 不直接操作单位 |
| allocation | ConstructionScheduler | 统一建造请求队列、找位置、借工人、跟踪建造 | 不决定“要不要造” |
| allocation | WorkerManager | 采矿/采气/侦察/建造/修理配额 | 不抢单位，只按配额发放 |
| execution | FlowRuntimeExecutor | 驱动 FlowIR：production policies、combat 策略图、step if 分支、退出原因 | 不解析 DSL，不创建模块 |
| execution | FlowRuntimeStateStore | 保存/恢复模块实例状态 | 不知道 GameState 业务含义 |
| projection | ProjectionMonitor | 按节拍调用 planner.simulate，缓存投影 | 不自己实现模拟算法 |
| projection | AlertService | 消费 GameState + Projection，结构化警报、去重、冷却 | 不直接修改 flow |
| history | EventJournal / SnapshotStore / WorkspaceVersionStore | 记录 flow_events、GameState+FlowRuntimeState 快照；管理 workspace/versions 与 FlowSpec 版本树 | 不参与 live 决策 |
| facade | RuntimeFacade | 对 agent/api/browser 暴露 start/stop、flow_edit、observe、projection、history | 不包含业务细节 |

### 3. flow 不依赖 runtime，runtime 依赖 flow 端口

flow 定义端口：

```text
AllocatorPort      申请/释放 UnitGroup lease
StateStorePort     读写 ModuleInstanceState
IntentSinkPort     提交 Intent
EventPort          写 flow_events
```

runtime 实现这些端口并注入 FlowRuntimeExecutor。

禁止：

```text
flow → runtime 的直接 import
agent 绕过 RuntimeFacade 直接操作 UnitPool / driver / planner
driver import flow/runtime/agent
```

### 4. driver 接入（推模型，详见 ADR-0026）

```text
driver 拥有 SC2 主循环，实现：
  start()
  stop()
  submit_intents(intents)          # runtime 提交，下一 step 生效
  events(cursor) -> [GameEvent]

runtime 实现 RuntimeSink：
  on_game_state(GameState)
  on_session_event(SessionEvent)
```

- driver 只实现上述端口，不 import flow/runtime/agent。
- runtime 通过 RuntimeSink 接收推送，不 import SC2。
- 单测时可用 FakeGamePort / FakeSC2Loop 重放脚本化 GameState。

### 5. 状态恢复规则

见 ADR-0013：

```text
恢复骨架状态：
  phase / strategy / attempt / step 游标 / mode / persistent 变量

重校准派生进度：
  已训练数量 / 建筑进度 / 队列长度 / 计时器
  一律用最新 GameState 重新计算
```

恢复流程：

```text
最近 runtime snapshot
  → 恢复骨架
  → 重放 flow_events 到当前 seq
  → 用当前 GameState 重校准
```

### 6. 统一节拍

V1 默认节拍：

| 频率 | 事项 |
|---|---|
| 1 秒 | 基础观察、地图刷新、Flow 状态 UI、ObservationPacket |
| 3 秒 | 生产投影重算 |
| 5 秒 | runtime snapshot；关键事件发生时立即快照 |
| 事件驱动 | 警报、flow 提交、策略转移、会话状态变化 |

所有定时器以 game seq/time 对齐，不使用多套漂移的墙钟定时器。

生产 policy 求值节奏：

- production policies 每 **1 游戏秒** 求值一次，与 planner 的 1 秒步长一致。
- combat step 的 if 分支每帧求值。
- 生产 policy 跨秒时的求值结果用于提交建造/训练请求；投影与运行时语义使用同一节奏。

### 7. 验收自动化

runtime 层必须提供：

- FakeGamePort 与脚本化 GameState 序列；
- 依赖检查：runtime 不得 import driver，flow 不得 import runtime；
- contract tests：端口行为、状态恢复、快照重放。

## 反例（明确禁止）

- 把 runtime 组件堆进 driver。
- agent 直接操作 UnitPool 或 planner。
- flow 直接调用 runtime 实现类。
- 多个定时器各自漂移、不与 game seq 对齐。
- 恢复状态时直接采用旧进度计数。

## 验收标准

1. 所有运行时组件都能映射到 ADR-0025 的子域。
2. import 检查证明 flow 不依赖 runtime、driver 不依赖 flow/runtime。
3. FakeGamePort 可驱动完整 runtime 做确定性回放。
4. 重启恢复后，骨架状态来自快照，进度计数与最新 GameState 一致。
5. 节拍实现与上表一致，关键事件即时触发。
