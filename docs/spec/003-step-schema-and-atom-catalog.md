# 003 Step Schema 与原子目录

- 状态：草案 v0.2
- 范围：Step 的字段与执行语义；V1 初始原子目录（条件/动作/筛选/空间工具）

## 1. Step 是什么

Step 是 Strategy 内部的**每帧求值的扁平分支表**：

- 不是原子操作，而是原子的装配件；
- 没有内部循环（帧循环本身就是循环）；
- V1 不允许 if 嵌套；复杂条件用 `and / or / not` 组合或拆成多个 Step；
- 每个 Step 是一次行为决策单元，带自己的 local 状态与退出原因。

## 2. Step 字段

```text
step:
  step_id: advance
  display_name_zh: 向目标推进

  locals:                             # 进入 step 时重置；可声明多个 local
    mode: advance

  progress:                           # 可选，给 UI / 回放显示进度
    metric: distance_to_target        # 指标名，见 4.4 空间工具
    label_zh: 距目标距离

  branches:                           # 有序，第一个命中的分支执行后本帧结束
    - branch_id: b_arrive
      when: arrived(main, params.target_region.center, 5)
      do:
        - exit_strategy(kind=done, reason=ARRIVED)

    - branch_id: b_ambush
      when: enemy_count_near(main, 15) > 8
      do:
        - exit_step(kind=interrupted, reason=AMBUSHED)

    - branch_id: b_hold_escort
      when: distance_between(tanks, infantry) > params.max_gap
      do:
        - set_local(name=mode, value=hold)
        - group_action(slot=tanks, type=terran/siege_tank, action=hold_position, params={})

    - branch_id: b_default
      # 无 when 的分支即 else，必须且只能放在最后
      do:
        - group_action(slot=tanks, type=terran/siege_tank, action=move_to,
                       params={target: params.target_region.center})
        - group_action(slot=infantry, type=terran/marine, action=move_to,
                       params={target: group_center(tanks)})
```

## 3. Step 执行语义

每游戏帧，对该 Strategy 的 active step：

1. 按 `branches` 声明顺序从上到下求值 `when`；
2. 第一个 `when=true` 的分支执行其 `do`，本帧结束；
3. 没有分支命中且存在无 `when` 的 else 分支 → 执行 else；
4. 没有分支命中且没有 else → **本帧不产生新 Intent**，保持上一帧已发出的命令；
5. `exit_step` / `exit_strategy` 在 `do` 中执行后，本帧结束，不再执行后续动作。

### 2.1 动作去重（防止每帧刷命令）

- 连续两帧对同一 `(slot, type)` 生成**相同** `(action, params)` 时，执行器**不重发 Intent**；
- 参数或 action 变化、单位进入/离开 group、上一帧命令返回失败/超时，才重新发送；
- `focus_fire` 等目标动态变化的动作，以解析后的目标值参与去重比较；
- 该规则是执行器硬约束，策略作者不能关闭；若未来需要“每帧强制重发”，必须新增带 `force_resend` 参数的动作原子，不得改变本条语义。

### 3.1 Step 退出语义

- `exit_step(kind, reason, payload?)`：结束当前 step，只在 Strategy 内部按 reason 边路由；若无内部边匹配 → 编译失败。
- `exit_strategy(kind, reason, payload?)`：结束整个 Strategy，按 Strategy `on_exit` 处置 Group。
- 不允许在同一个 `do` 中先 `exit_step` 再 `exit_strategy`；也不允许 exit 后再执行 group_action。

### 3.2 local 与 variable

- `locals`：进入 step 时重置；`set_local` 只允许写本 step 声明的 local。
- `variables`（Strategy 层声明）：跨 step 持久；`set_variable` 只允许写 Strategy 已声明的变量。
- Step 内不允许声明或写未声明的变量。
- variables 生命周期 = Strategy 绑定期间；switch / detach / Strategy 结束即销毁。
- 空 group 的求值规则：
  - `group_count(空槽位)` = 0；
  - 依赖组中心的谓词（`arrived`、`distance_between`、`group_center_in_region`、`group_hp_ratio`）对空槽位恒为 false；
  - 对空槽位的动作 = no-op。

## 4. V1 原子目录

原子目录**只增不改**。新增原子必须离线实现、测试、入库，并在版本号中体现。

### 4.1 条件原子（Predicate）

| 原子 | 签名 | 语义 |
|---|---|---|
| group_count | `group_count(slot)` / `group_count(slot, type)` | 槽位当前总兵力 / 某兵种数量 |
| arrived | `arrived(slot, target, radius)` | 槽位组中心进入目标半径 |
| distance_between | `distance_between(slot_a, slot_b) <\|<=\|>\|>= N` | 两个槽位组中心距离 |
| group_center_in_region | `group_center_in_region(slot, region)` | 组中心位于区域 |
| group_hp_ratio | `group_hp_ratio(slot) <\|<=\|>\|>= N` | 组平均血量比例（0~1） |
| engaged | `engaged(slot)` | 近期攻击命令 / 敌人在射程内 / 近期交火事件 |
| under_attack | `under_attack(slot)` | 组内单位近期受击 |
| enemy_count_near | `enemy_count_near(anchor, radius) <\|<=\|>\|>= N` | 锚点附近可见敌人数量；anchor=point/region/slot |
| enemy_visible_in | `enemy_visible_in(region)` | 区域内有可见敌人 |
| has_building | `has_building(type, region=null, ready=false)` | 世界/生产状态 |
| has_ready_base | `has_ready_base(region)` | 区域已有就绪基地 |
| unit_count | `unit_count(type) <\|<=\|>\|>= N` | 我方某类型数量，默认 owner=self；后续可加 owner 参数 |
| game_time | `game_time >= T` | 绝对游戏时间，秒 |
| strategy_elapsed | `strategy_elapsed >= T` | 当前 Strategy 自绑定/切换以来的游戏秒数 |
| step_elapsed | `step_elapsed >= T` | 当前 step 自进入以来的游戏秒数 |
| timer_elapsed | `timer_elapsed(name)` | 命名计时器到期；未 start 过恒为 false；timer 存绝对 deadline |
| event_occurred | `event_occurred(unit_lost\|engagement_started\|order_completed)` | 当前 Strategy 生命周期内的事件流 |
| user_cancel | `user_cancel` | 用户显式取消 |

- 条件可用 `and / or / not` 组合；括号与比较运算符由 JSON AST 表达。
- 所有条件只读、确定性、绑定当前 GameState seq。

### 4.2 动作原子（Action）

动作目标粒度固定为 **`(group_slot, stable_type)`**；同一 slot 同兵种不能拆分执行不同动作。

| 动作 | 签名 | 语义 |
|---|---|---|
| move_to | `move_to(target)` | 移动到目标 |
| attack_move_to | `attack_move_to(target)` | 攻击移动 |
| hold_position | `hold_position(target=null)` | 原地/目标点坚守 |
| follow | `follow(slot_ref, type?)` | 跟随另一槽位的组或某兵种 |
| focus_fire | `focus_fire(target)` | 集火目标 |
| load | `load(transport_slot)` | 装载进运输组 |
| unload | `unload(target)` | 在目标卸载 |
| use_ability | `use_ability(ability_id, target=null)` | 使用指定能力；ability_id 为稳定 ID |
| patrol | `patrol(points)` | 沿路径巡逻 |
| stop | `stop()` | 停止当前命令（系统取消/释放时也使用） |

- 空 group 上任何动作 = no-op，不产生 Intent。
- `target` 类型：`point`、`region`、`group_center(slot)`、`nearest_enemy`。
- 初始目录**不包含** `scatter_from`、`enemy_type_near`；如需毒爆散开等微操，按“新增原子”流程扩展，不改变 Step 语义。

### 4.3 筛选原子（Selector）

筛选原子只用于 Group 创建/补兵/拆分（见 001）：

| 字段 | 说明 |
|---|---|
| source | `global` / `region(name)` |
| filters | `type_in`（composition 隐式）、`capability`、`role`、`tags`、`exclude_types` |
| rank | `nearest_to(assemble_at)` 首次；`nearest_to(refill_anchor)` 补兵 |
| take | 由 composition 的 `min/target/max` 表达 |
| tie-break | 同距离按 `unit_ref` 升序 |

### 4.4 空间工具（只读）

| 工具 | 签名 |
|---|---|
| region_center | `region_center(region)` |
| group_center | `group_center(slot)` |
| distance | `distance(a, b)` |
| nearest_units | `nearest_units(target, filters, k)` |
| cluster_centers | `cluster_centers(scope, filters, radius?)` |

- 空间工具只读、按 GameState seq 缓存；新 seq 后旧结果作废。
- `group_center` / `cluster_centers` 的算法（质心或中位数）实现时固定一种并在测试中锁定。

## 5. 分支内的可写操作

| 操作 | 作用范围 |
|---|---|
| `set_local(name, value)` | 本 step 的 locals |
| `set_variable(name, value)` | Strategy 持久 variables |
| `start_timer(name, duration)` | 以 `game_time + duration` 存绝对 deadline |
| `stop_timer(name)` | 取消计时器 |
| `exit_step(kind, reason, payload?)` | 结束 step |
| `exit_strategy(kind, reason, payload?)` | 结束 Strategy |

## 6. 场景：一个 step 内协同两个 group

```text
step gather:
  branches:
    - when: group_count(wave_a) == 0 or group_count(wave_b) == 0
      do: [exit_strategy(kind=failed, reason=GROUP_EMPTY)]

    - when: arrived(wave_a, params.staging_a, 5)
            and arrived(wave_b, params.staging_b, 5)
      do: [exit_step(kind=done, reason=JOINED)]

    - # else
      do:
        - group_action(slot=wave_a, type=terran/marine, action=move_to,
                       params={target: params.staging_a})
        - group_action(slot=wave_b, type=terran/marine, action=move_to,
                       params={target: params.staging_b})
```

说明：并行协同不需要并行 step；一个 step 的分支表同时指挥多个 group，条件不满足时每帧继续等待。

## 7. 验收要点

1. 同一帧多个 `when` 为真时，只执行第一个；行为确定。
2. 无命中且无 else 时，不产生新 Intent。
3. `exit_step` 无内部边匹配时编译失败。
4. 动作参数违反 `(slot, type)` 粒度（如同组枪兵拆 2+3）编译失败。
5. 原子目录外的新条件/动作在编译期被拒绝。
6. 相同输入、相同 GameState 序列下，分支命中与 Intent 序列完全确定。
