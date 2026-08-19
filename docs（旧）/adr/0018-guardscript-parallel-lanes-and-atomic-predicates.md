# ADR-0018 GuardScript：并行泳道 + 原子判定 + 守卫转移
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：部分有效（原子判定目录为已确认草案）；编排模型由 ADR-0021 替代
- 范围：combat 模块的最终作者模型；替代 ADR-0016 的 Python-like 方案

## 背景

讨论后确定：

- 复杂 combat 模块的难点不是“顺序”，而是**判断条件**。
- `if / while` 虽然直观，但不适合直接运行 Python。
- YAML `states/on` 太啰嗦。
- 旧的通用 DSL 失败，是因为它开放给 live、条件随意、没有模块边界。

核心洞察：**游戏帧循环本身就是循环。** 我们不需要显式 while；只需要给每个动作配一个 `until` 条件。条件不满足就继续等，条件满足就转移。

## 决定

### 1. Combat 模块 = 多条并行泳道

```text
CombatModule
 ├─ groups        # 命名单位组，由 SelectorSpec 产生
 ├─ variables     # checkpoint、iteration 等本地状态
 ├─ lanes         # 每个命名组一条泳道，或少量协调泳道
 └─ done/fail     # 模块终态条件
```

每条泳道是一个步骤序列：

```text
lane:
  initial: step_name
  steps:
    - name: advance
      do: 当前每帧/进入时执行的动作
      until:
        - when: 原子判定
          to: 下一个 step
          on: 可选的本地状态更新
```

- 泳道之间并行推进。
- 泳道只通过**读其他泳道的状态**和**原子判定**协调，不直接修改其他泳道。
- 循环通过 `to` 指回之前的 step 表达，不需要 `while` 关键字。

### 2. 隐式循环：等待就是循环

每帧执行器对每个 active 模块做：

```text
1. 读取当前 GameState + 本模块实例状态
2. 对每条泳道：
     a. 执行当前 step 的 do 动作（进入 step 时执行；可选周期性重发）
     b. 按顺序评估 until 中的原子判定
     c. 第一个为真的判定触发转移，执行 on 更新
3. 更新并持久化实例状态
```

- 没有显式 `while`：条件不满足，下一帧继续评估，这就是循环。
- 没有显式 `if`：每个 `until` 就是一个 if；多个 `until` 按声明顺序判断。
- 没有 Python：转移、动作、判定都是固定原子。

### 3. 原子判定目录（V1）

#### 3.1 组状态

```text
arrived(group, target, radius)
distance_between(group_a, group_b) <|<=|>|>= N
distance_since(group, checkpoint) >= N
group_center_in_region(group, region)
group_size(group) <|<=|>|>= N
group_hp_ratio(group) <|<=|>|>= N
```

#### 3.2 交战

```text
engaged(group)
under_attack(group)
enemy_visible_in(region)
enemy_count_near(target, radius) <|<=|>|>= N
enemy_cleared(region)
```

> 需要表达“附近无敌人”时使用 `enemy_count_near(target, radius) == 0`，不要使用 `enemy_cleared(radius)`。

#### 3.3 泳道协调

```text
lane(group).state == step_name
lane(group).done
lane(group).failed
lane(group).progress >= N
```

#### 3.4 世界/生产信号

```text
time >= T
minerals/gas/supply 比较
supply_headroom <|<=|>|>= N        # = supply_cap - supply_used
has_building(type, region=null, ready=false)
has_ready_base(region)             # 某区域是否已有就绪基地
unit_count(type)
signal(instance_id.ready | instance_id.done)   # 只允许 instance_id，避免同名模块多实例歧义
```

#### 3.5 事件/控制

```text
event_occurred(unit_lost | engagement_started | order_completed)
timer_elapsed(name)
timeout
user_cancel
manual_trigger
```

- 判定可组合：`and / or / not`。
- 所有判定只读、确定性、绑定当前 GameState seq。
- 只有 `on` 能更新本地变量。

### 4. 场景一：两波兵会合

> 以下第 4~6 节示例为历史内容，仅用于说明判定用途；编排语法以 ADR-0021 为准。

```text
module: two_wave_rendezvous

groups:
  wave_a: select marine 8..12, nearest_to(staging_a)
  wave_b: select marine 8..12, nearest_to(staging_b)

lanes:
  wave_a:
    initial: gather
    steps:
      gather:
        do: wave_a.move(staging_a)
        until:
          - when: arrived(wave_a, staging_a, 5)
            to: ready

  wave_b:
    initial: gather
    steps:
      gather:
        do: wave_b.move(staging_b)
        until:
          - when: arrived(wave_b, staging_b, 5)
            to: ready

done:
  when: lane(wave_a).state == ready and lane(wave_b).state == ready

on_done:
  wave_a.attack_move(front)
  wave_b.attack_move(front)
```

### 5. 场景二：步坦协同

```text
module: tank_marine_push

groups:
  tanks: select siege_tank
  infantry: select marine 8..

variables:
  checkpoint: null

lanes:
  tanks:
    initial: advance
    steps:
      advance:
        do: tanks.move(target)
        until:
          - when: distance_since(tanks, checkpoint) >= step_distance
            to: hold
      hold:
        do: tanks.hold()
        until:
          - when: distance_between(tanks, infantry) <= catchup_gap
            to: advance
            on: checkpoint = group_center(tanks)

  infantry:
    initial: wait
    steps:
      wait:
        do: infantry.hold()
        until:
          - when: lane(tanks).state == hold
            to: catchup
      catchup:
        do: infantry.move(group_center(tanks))
        until:
          - when: distance_between(tanks, infantry) <= catchup_gap
            to: wait
```

循环就是 `advance → hold → advance`，没有 while。

### 6. 场景三：第一波交火后第二波包抄

```text
module: hammer_anvil

groups:
  first_wave: select marine 10..16
  second_wave: select marine 8..12

lanes:
  first_wave:
    initial: probe
    steps:
      probe:
        do: first_wave.attack_move(front)
        until:
          - when: engaged(first_wave)
            to: committed

  second_wave:
    initial: hidden
    steps:
      hidden:
        do: second_wave.hold(hidden_staging)
        until:
          - when: lane(first_wave).state == committed
            to: flank
      flank:
        do: second_wave.attack_move(flank_point)
        until:
          - when: enemy_cleared(flank_region) or timer_elapsed(flank_ttl)
            to: done
```

### 7. 与旧 DSL 的区别

| 旧 DSL | GuardScript |
|---|---|
| live 中由 agent 自由编写 | 只允许离线模块作者编写 |
| 条件是开放文本，解析宽松 | 固定原子判定目录，编译期校验 |
| 模块边界模糊 | 只存在于模块内部，不能跨模块乱引用 |
| 运行期 parse | 编译为 CFG 后运行 |
| 没有 UI 进度 | 每个 step 天然是一个进度节点 |

所以这是**受了旧 DSL 启发，但把范围压到模块内部的确定性 GuardScript**，不是旧 DSL 复活。

### 8. 测试

- 每个 step 的 `until` 和 `on` 都是可枚举对象。
- 场景测试驱动脚本化 GameState/事件，断言泳道状态转移和 Intent。
- 循环必须有退出路径：timeout、兵力不足、目标清除、user_cancel。
- 相同输入序列下，泳道状态与 Intent 完全确定。

## 反例（明确禁止）

- 模块内使用 Python、任意函数或外部库。
- 判定语法中出现未注册的条件名。
- live 中编辑 GuardScript。
- 一条泳道直接修改另一条泳道的 step 或变量。
- 没有退出条件的纯循环。

## 验收标准

1. 两波会合、步坦协同、交火包抄三个场景都能用 GuardScript 表达。
2. 条件不满足时泳道停留原 step，不重复发送无意义 Intent。
3. 条件满足时按声明顺序触发第一个转移。
4. 循环通过 `to` 回指表达，相同输入下转移序列确定。
5. UI 能把每个 step 显示为泳道进度节点。
6. live 中只能改参数/选模块/调 trigger，不能改 GuardScript。
