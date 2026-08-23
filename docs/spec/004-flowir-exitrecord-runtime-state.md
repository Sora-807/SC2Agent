# 004 FlowIR、ExitRecord 与运行时状态

- 状态：草案 v0.2
- 范围：Strategy 编译产物 FlowIR；退出记录；Group / Strategy / Step 运行时状态

## 1. FlowIR

### 1.1 定位

- FlowIR 是 **Strategy + 绑定 + 参数**编译后的运行时格式；
- 运行期不解析文本、不解析 DSL；
- FlowIR 不可手工编辑，只能由编译器生成；
- 同一输入编译两次，FlowIR 必须等价。

### 1.2 结构

> 示例只展示 `formup` 一个 step 与一条边，完整 FlowIR 应包含 Strategy 的全部 steps/edges。

```json
{
  "flowir_schema_version": 1,
  "ir_hash": "sha256:...",
  "compiled_at_seq": 0,

  "source": {
    "strategy_id": "bio_push",
    "strategy_version": 1,
    "manifest_hash": "sha256:..."
  },

  "bindings": {
    "main": "G1",
    "support": null
  },

  "params": {
    "target_region": "enemy_natural",
    "min_marines": 12
  },

  "group_slots": [
    {"name": "main", "bound_group_id": "G1"},
    {"name": "support", "bound_group_id": null}
  ],

  "initial_step": "formup",

  "steps": [
    {
      "step_id": "formup",
      "locals": {},
      "branches": [
        {
          "branch_id": "b_formed",
          "when": {
            "op": "and",
            "args": [
              {"op": ">=", "args": [{"op": "group_count", "args": ["main", "terran/marine"]}, {"param": "min_marines"}]},
              {"op": ">=", "args": [{"op": "group_count", "args": ["main", "terran/medivac"]}, {"param": "min_medivacs"}]}
            ]
          },
          "do": [{"op": "exit_step", "kind": "done", "reason": "FORMED"}]
        }
      ]
    }
  ],

  "edges": [
    {"from": "formup", "to": "advance", "kind": "done", "reason": "FORMED", "max_iterations": null}
  ],

  "on_exit": "keep_idle",
  "loop_limits": {"max_step_transitions": 100}
}
```

### 1.3 编译规则

1. 编译期完成：槽位校验、参数类型/范围、原子存在性、边可达性、环出口检查、`(slot, type)` 粒度检查。
2. `params` 在绑定时解析为具体值并冻结；`live_editable=true` 的参数允许生成参数变更补丁，但必须重新编译并产生新 `ir_hash`；补丁不重置 Strategy 状态（见 002）。
3. FlowIR 中的条件与动作一律是 JSON AST；没有字符串形式的 `if` 文本参与执行。
4. `ir_hash` 按固定 canonical JSON 规则计算：键排序、数组保持声明顺序、枚举用大写标识、浮点按 IEEE-754 规范化；规则在 compiler contract test 中锁定。
5. FlowIR 进入执行器前必须通过 schema 校验。

## 2. ExitRecord

### 2.1 字段

```text
ExitRecord:
  event_id             # 事件 ID，单调递增
  seq                  # GameState seq
  game_time            # 绝对游戏时间，秒
  strategy_instance_id # Strategy 绑定实例 ID
  group_ids            # 该绑定涉及的 group ID 列表
  step_id              # 产生退出的 step
  branch_id            # 命中的分支
  kind                 # done | interrupted | failed | cancelled
  reason               # 退出原因枚举
  payload              # 相关单位/位置/事件等结构化信息
```

### 2.2 kind 与 reason

| kind | 含义 | V1 reason |
|---|---|---|
| done | 正常完成 | `ARRIVED`, `FORMED`, `JOINED`, `TARGET_CLEAR`, `ENEMY_CLEARED`, `SAFE`, `SIGNAL_RECEIVED` |
| interrupted | 被打断，可路由到其他 step | `AMBUSHED`, `TIMEOUT`, `USER_CANCEL`, `MANUAL_RETASK` |
| failed | 无法继续 | `UNITS_LOST`, `GROUP_EMPTY`, `ALLOCATION_FAILED`, `INVALID_TARGET`, `LOOP_LIMIT` |
| cancelled | 外部取消/切换策略 | `USER_CANCEL`, `MANUAL_RETASK` |

- reason 枚举只增不改，带版本号。
- `USER_CANCEL` 同时可出现在 `interrupted` 与 `cancelled`；具体 kind 由发生来源决定。
- `LOOP_LIMIT`：`max_step_transitions` 或边 `max_iterations` 超限时由系统产生，Strategy 以 failed 结束。
- `SIGNAL_RECEIVED`、`ALLOCATION_FAILED` 为 V1 预留原因：当前原子目录尚不产生，但枚举先占位，语义冻结；V1 后续扩展只能使用，不得改变。

### 2.3 事件写入

| 内部事件 | 触发 |
|---|---|
| step_enter | 进入 step |
| step_exit | `exit_step` 产生 ExitRecord |
| strategy_exit | `exit_strategy` 产生 ExitRecord |
| strategy_loop_limit | `LOOP_LIMIT` |
| strategy_attached | 绑定 Strategy |
| strategy_switched | 切换 Strategy |

## 3. 运行时状态对象

### 3.1 GroupState

```text
GroupState:
  group_id
  status              # forming | ready | active | paused | releasing | released
  composition         # 配置快照
  unit_counts         # {type: current_count}
  leased_units        # 稳定 unit_ref 列表
  center              # 当前组中心
  bound_strategy_instance_id | null
  replenish           # 配置快照 + 上次检查 seq/time
  idle_since_game_time | null
```

- `unit_counts` 与 `center` 是**派生状态**，用最新 GameState 重算，不靠旧值恢复。
- `leased_units`、`status`、`bound_strategy_instance_id` 是**骨架状态**，从 state store 恢复。

### 3.2 StrategyState（按绑定实例）

```text
StrategyState:
  strategy_instance_id
  strategy_ref / version / ir_hash
  status              # RUNNING | PAUSED | DONE | FAILED | ABORTED
  active_step_id
  step_transition_count
  variables           # 策略持久变量；timer 存绝对 deadline
  last_exit           # ExitRecord | null
```

### 3.3 StepState

```text
StepState:
  strategy_instance_id
  step_id
  step_entry_count    # 本 step 第几次进入（含环重入）
  status              # RUNNING | EXITED
  entered_seq
  entered_game_time
  locals              # mode 等 step locals
  last_branch_id      # 本帧命中的分支
  last_exit           # ExitRecord | null
  progress            # 0..1 或 null；来自 step 声明的 progress 指标
```

- 进入 step 时 `locals` 重置，`step_entry_count += 1`。
- 环回边重入同一步 = 新 entry；`StepState` 重新初始化。

## 4. 持久化与恢复

1. **骨架状态**（从 store 恢复）：
   - GroupState：status、leased_units、bound_strategy_instance_id；
   - StrategyState：status、active_step_id、step_transition_count、variables（含 timer 绝对 deadline）；
   - StepState：step_id、step_entry_count、locals。
2. **派生状态**（用最新 GameState 重算）：
   - unit_counts、center、progress、距离/位置类缓存。
3. 恢复流程：
   ```text
   FlowRuntimeStateStore（权威）
     → 恢复骨架状态
     → 重放 flow_events 到当前 seq
     → 用最新 GameState 重算派生状态
   ```
   - runtime snapshot 用于历史/复盘，**不是**骨架状态的权威来源；
   - 若 state store 与最新 flow_events 冲突，以 state store + 事件重放结果为准，并告警。
4. 恢复后若 `active_step` 引用的 Group 已不存在，该 Strategy 以 `failed / GROUP_EMPTY` 结束，不自动猜测。

## 5. 验收要点

1. 同一 Strategy + 绑定 + 参数编译两次，`ir_hash` 相同。
2. FlowIR 不包含可执行文本；schema 校验拒绝未知原子/未知槽位。
3. 环回边触发 `max_iterations` 或全局上限时，产生 `failed / LOOP_LIMIT`。
4. `exit_step` 与 `exit_strategy` 的 ExitRecord 均写入 flow_events，可回放。
5. 恢复后 timer 使用绝对 deadline，跨进程重启不漂移。
6. Group 的 unit_counts 与最新 GameState 一致，不沿用旧计数。
