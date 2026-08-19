# ADR-0026 driver 游戏循环与步进契约

- 状态：已确认草案
- 替代：无（细化 ADR-0025 的 GamePort）
- 范围：谁驱动 SC2 步进、Intent 何时生效、观察按什么时间对齐

## 背景

GamePort 之前只写 read/apply/events/start/stop，没有回答最根本的问题：burnysc2 需要 driver 在同一进程内同步调用 step() 游戏才会前进。runtime 不能“按墙钟拉取”GameState。

## 决定

### 1. driver 拥有 SC2 主循环

- driver 是唯一调用 burnysc2 step() 的模块。
- runtime 不驱动游戏循环，只消费 driver 推送的状态。
- driver 主循环是 SC2 进程内的循环；runtime 进程通过本地端口/IPC 接收推送。

### 2. 推模型接口

```text
driver:
  start()
  stop()
  submit_intents(intents)          # runtime 提交，下一 step 生效
  events(cursor) -> [GameEvent]    # 事件增量

runtime 实现 RuntimeSink:
  on_game_state(GameState)         # 每次 driver step 后推送
  on_session_event(SessionEvent)   # started/stopped/crashed
```

- 不再把 `read()` 作为运行时拉取主入口；`read()` 仅用于离线探测/单测或显式取当前缓存。
- driver 每 step：
  1. 执行 burnysc2 步进；
  2. 从 SC2 读原始状态，生成 GameState（含动态原始层）；
  3. 推送 `on_game_state`；
  4. 下一 step 开始时应用 `submit_intents` 已提交的 Intent。

### 3. Intent 生效时机

- runtime 在本 step 处理完 GameState 后提交的 Intent，进入 driver 队列。
- driver 在**下一次 step** 执行时应用队列中的 Intent。
- 同一 Intent 队列按提交顺序执行；冲突由 driver 返回 ApplyResult。
- 禁止在 driver 步进过程中异步写入队列。

### 4. 观察节拍以游戏时间为准

- ObservationPacket 以**游戏时间整秒边界**生成，而不是墙钟 1 秒。
- 游戏时间跨过整秒时，driver 通知 runtime 生成观察。
- 地图 UI/Flow UI 消费最近 ObservationPacket；刷新节拍为游戏时间 1 秒。
- 快节奏下墙钟与游戏时间不同，一律以 GameState.time 为准。

### 5. 动态层分工：raw → enriched 两段

```text
driver 输出 raw GameState：
  buildings/units/资源 + 动态原始层（creep/visibility）
  map_layers 中的确定性层尚未补齐

runtime.world.ObservationService：
  raw GameState + mechanics.LayerComputer
  → 完整 GameState.map_layers
  → 才发布给 tactical_map/constraint/planner/agent/browser

离线导入/初始状态工具：
  用同一个 mechanics.LayerComputer 补齐 map_layers
```

- `map_layers` 始终是 GameState 的字段；在线由中间层计算后写入，离线由导入工具计算后写入。
- driver 只填动态原始层，不调用 mechanics，不写“哪个建筑提供 power/addon”的规则。
- 核心消费者只允许看到 enriched GameState。

### 6. driver 探测 spike 提前

- 在 M1/M2 阶段做 driver spike，探测：
  - step 内读/写时序；
  - orders 与 auto-order 白名单；
  - 用户操作识别能力；
  - 坐标转换 fixture（size 2/3/5）；
  - creep/visibility 原始数据形态。
- 探测结论写回 GameState 完整性清单与 ADR-0006。

## 反例（明确禁止）

- runtime 或 agent 调用 burnysc2 step()。
- 用墙钟 1 秒替代游戏时间整秒生成观察。
- driver 在步进中读 runtime 的并发写队列。
- driver 计算 power/addon 业务规则。
- Intent 提交后立即在同一个已开始的 step 内生效。

## 验收标准

1. 用 FakeSC2Loop 重放 10 个 step，driver 按序推送 10 个 GameState。
2. runtime 提交的 Intent 在下一 step 生效，当前 step 不生效。
3. 游戏时间 0.9s→1.1s 跨过整秒时生成一次观察，未跨秒不生成。
4. driver 源码不包含 power/addon 计算逻辑。
5. size 2/3/5 建筑坐标转换通过 fixture 测试。
