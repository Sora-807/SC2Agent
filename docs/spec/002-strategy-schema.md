# 002 Strategy Schema（行为策略）

- 状态：草案 v0.2
- 范围：combat Strategy 的字段、group 槽位、步骤图、边、退出策略
- 说明：production 模块沿用 ADR-0004 的 policies 模型，不在本文档范围内；本文只覆盖 combat/行为策略。

## 1. 定位

Strategy 是**挂在 Group 上的行为**：

- Strategy 不拥有兵权；
- Strategy 内部是一张允许有界环的步骤图；
- Strategy 结束/被切换时，兵权仍在 Group 上，由 `on_exit` 决定是否释放。

## 2. 字段定义

```text
strategy:
  manifest_version: 1

  id: bio_push
  version: 1
  race: [terran]                      # 允许的种族

  display_name_zh: 两船兵前压
  description_zh: 运输机数量达标后的正面推进
  usage_zh: 适用于……
  boundaries_zh: 不负责生产；不处理 user_leased 单位；……

  group_slots:                        # 只声明槽位名字，不声明 required/min/max
    - name: main
      display_name_zh: 主战部队
    - name: support
      display_name_zh: 策应部队

  params:
    target_region:
      type: region
      live_editable: false
    min_marines:
      type: int
      default: 12
      live_editable: true

  variables:                          # 跨 step 持久变量；step locals 见 003
    checkpoint:
      type: point
      default: null
      # 生命周期：本 Strategy 绑定期间跨 step 持久；
      # switch_strategy / detach_strategy / Strategy 结束即销毁。

  static_preconditions:               # 编译/绑定期校验；与兵数量无关
    - race in strategy.race
    - params.target_region 存在且属于当前地图

  initial_step: formup

  steps:                              # step 定义见 003
    - {step_id: formup, ...}
    - {step_id: advance, ...}
    - {step_id: combat,  ...}
    - {step_id: retreat, ...}
    - {step_id: hold,    ...}

  edges:
    - from: formup
      to: advance
      kind: done
      reason: FORMED
    - from: advance
      to: combat
      kind: interrupted
      reason: AMBUSHED
    - from: combat
      to: advance
      kind: done
      reason: ENEMY_CLEARED
      max_iterations: 20              # 可选：这条回边最多触发 20 次
    - from: advance
      to: retreat
      kind: failed
      reason: UNITS_LOST
    - from: retreat
      to: hold
      kind: done
      reason: SAFE

  on_exit: keep_idle                  # keep_idle | release

  loop_limits:
    max_step_transitions: 100         # 全局步骤转移上限；超出 reason=LOOP_LIMIT

  tests:                              # 离线脚本化场景测试引用
    - scenario: two_wave_join_v1
    - scenario: bio_push_ambush_v1
```

## 3. Group 槽位规则

1. `group_slots` 只声明**名字和展示名**。
2. 不写 `required`、`min`、`max`；兵够不够是 Step 内部行为，由 `group_count` 分支处理。
3. 未绑定槽位 = **空 group**：
   - `group_count(空槽位)` 恒为 0；
   - 对空 group 发任何动作 = no-op，不产生 Intent，不报错。
4. Step 中引用的槽位必须存在于 `group_slots`，否则编译失败。

## 4. 步骤图语义

1. Strategy 同一时刻**只有一个 active step**；V1 不允许并行 step。
   - 多 group 协同在一个 step 的分支表内完成（见 003 场景）。
2. Step 以 `exit_step(kind, reason)` 退出时，按 `edges` 匹配 `from + kind + reason`：
   - 匹配到边 → 进入目标 step，`step_transition_count += 1`；
   - 没有匹配边 → 编译失败（这是保护：内部行为不能隐式漏到 Strategy 外）。
3. Step 以 `exit_strategy(kind, reason)` 退出时，Strategy 结束：
   - 记录 StrategyExitRecord；
   - 按 `on_exit` 处置 Group；
   - 外层可订阅该退出信号。
4. 同一 step 声明多条同 `(kind, reason)` 出边时，按声明顺序取第一条（确定性）。
5. Step 图允许环：
   - 每个环必须至少有一条离开该环的路径，且该路径最终可达某个 `exit_strategy`；
   - 每条回边可声明 `max_iterations`；
   - 全局 `max_step_transitions` 兜底；超过后以 `failed / LOOP_LIMIT` 结束 Strategy。

## 5. 绑定与切换

```text
attach_strategy(strategy_ref, bindings={main: G1}, params={...})
switch_strategy(strategy_instance_id, strategy_ref, params={...})
detach_strategy(strategy_instance_id)
pause_strategy(strategy_instance_id)
resume_strategy(strategy_instance_id)
```

规则：

- `attach_strategy` 后 Strategy 立即进入 `initial_step` 开始每帧求值；是否“开始行动”由 step 条件决定。
- 若绑定中任一 Group 已绑定其他 Strategy，`attach_strategy` 拒绝；必须先 `detach_strategy` 或改用 `switch_strategy`。
- `switch_strategy`：
  1. 旧 Strategy 状态归档（含 variables）；
  2. 新 Strategy 从 `initial_step` 开始；
  3. Group 绑定与 lease 不变；新 strategy 的 `group_slots` 必须覆盖现有绑定槽位，否则拒绝切换；
  4. 旧 Strategy 的持久变量不传给新 Strategy。
- `detach_strategy`：以 `cancelled / MANUAL_RETASK` 结束当前 Strategy；Group 回 `ready`，lease 不释放。
- `pause_strategy` / `resume_strategy`：只暂停/恢复 Strategy 求值；Group lease 不变，对应 Group 状态 `active ↔ paused`。
- 同一 Group 同一时刻最多绑定一个 Strategy；多 Group 可绑定同一 Strategy 的不同绑定实例。
- `live_editable=true` 的参数更新：重新编译 FlowIR、更新参数快照，**不重置** Strategy 状态与 active step。
- `live_editable=false` 的参数更新：V1 默认拒绝在 RUNNING 期间修改；离线修改后重新绑定生效。

## 6. 编译规则汇总

| 规则 | 失败行为 |
|---|---|
| Step 中引用的槽位必须存在于 group_slots | 编译失败 |
| 每个 edge 的 step 端点必须存在 | 编译失败 |
| `exit_step` 必须有可匹配的内部边 | 编译失败 |
| step 动作引用不存在的兵种/原子 | 编译失败 |
| 存在无离开路径的环 | 编译失败 |
| 存在 Strategy 内创建/拆分/释放 Group 的写法 | 编译失败 |
| 同一类型两个 unit 动作粒度违反 `(slot, type)` 规则 | 编译失败 |
| 同一输入编译两次 | FlowIR 等价 |

## 7. 示例：两船兵前压（伪代码，完整 step 语法见 003）

```text
strategy bio_push:
  group_slots: [main]
  params:
    target_region: {type: region, live_editable: false}
    min_marines: {type: int, default: 12, live_editable: true}
    min_medivacs: {type: int, default: 2, live_editable: true}
    retreat_marines: {type: int, default: 6, live_editable: true}
  initial_step: formup
  on_exit: release

  steps: [formup, advance, combat, retreat, hold]

  edges:
    formup  --done(FORMED)------------> advance
    advance --interrupted(AMBUSHED)---> combat
    combat  --done(ENEMY_CLEARED)-----> advance     # 环
    advance --failed(UNITS_LOST)------> retreat
    combat  --failed(UNITS_LOST)------> retreat
    retreat --done(SAFE)--------------> hold
```

> `hold` step 内以 `exit_strategy(done, SAFE)` 结束；其余 step 定义见 003。

执行路径：`formup` 等待 `group_count` 满足 → `advance` 推进 → 遇敌切 `combat` → 清完回 `advance` → 兵力不足切 `retreat` → 安全后结束 Strategy。
