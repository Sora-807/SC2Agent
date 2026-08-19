# ADR-0015 战斗行为状态机与协同算法

> 本文件已废弃，以下内容仅作演进历史，不要作为实现依据。

- 状态：已废弃，由 ADR-0018/0019/0020/0021 替代（其中原子判定目录在 0018 保留）
- 范围：复杂进攻/协同/交战检测的表达方式

## 背景

简单 pipeline 无法表达以下需求：

- 判断部队是否到达某区域。
- 两波兵协同，判断距离是否太远。
- 步坦协同中速度不同，需要周期性地停下等待。
- 判断第一波已经交战，再让第二波侧翼包抄。

早期方案：给 combat 模块增加确定性的行为状态机。该手写 YAML 方案已被后续 ADR 替代，保留此文档仅作演进历史。

## 决定

### 1. Combat 模块的 behavior 两种形态

```text
behavior =
  | pipeline        # 简单：顺序动作，无分支循环
  | state_machine   # 复杂：有限状态 + 守卫转移，允许环
```

- 两者都由 ActionAtom、PredicateAtom、SpatialTool 组合而成。
- `state_machine` 只允许离线模块作者定义，进入模块库前必须通过场景测试。
- live 中只能修改模块参数、选择模块、调整 trigger，不能现场写状态机。

### 2. StateMachine 结构

```text
state_machine:
  variables:
    distance_since_checkpoint: 0
    checkpoint: null

  states:
    advance:
      actions:
        tanks:    {move_to: next_waypoint}
        infantry: {move_to: next_waypoint}
      on:
        - when: distance_between(tanks, infantry) > max_gap
          to: wait_fast_group

    wait_fast_group:
      actions:
        tanks:    {move_to: next_waypoint}
        infantry: {hold_position: null}
      on:
        - when: distance_between(tanks, infantry) < resume_gap
          to: advance
```

- 每帧只处于一个状态。
- 每个状态给命名组绑定动作；未列出的组默认 hold，或模块显式声明 default_action。
- 转移由 PredicateAtom 守卫；多个守卫同时满足时按声明顺序取第一个。
- 环允许存在，因此“前进一段、停下等、再前进”可以表达。
- 本地变量只允许内置操作更新，如 `record_checkpoint`、`distance_since_checkpoint`。

### 3. Predicate 判定来源

- `arrived(group, target, radius)`：组中心进入目标半径。
- `distance_between(group_a, group_b)`：两组中心距离。
- `distance_since_checkpoint(group, var)`：组中心相对 checkpoint 的累计移动距离。
- `engaged(group)`：满足任一：
  - 组内单位近期有对敌攻击 order；
  - 敌方单位进入组内单位的武器射程；
  - trace 中出现该组受击/交火事件。
- `enemy_in_region(region)` / `group_alive_count` / `timer_expired`。

### 4. 场景一：简单到达二矿自动战斗

```text
module: push_natural
selector:
  groups:
    army: {type_in: [terran/marine], take: {min: 8}}
behavior: pipeline
states/actions:
  army:
    - attack_move_to(enemy_natural_center)
lifecycle:
  done: arrived(army, enemy_natural_center, radius=5)
```

到达即交给游戏自动战斗，模块结束并释放 lease。

### 5. 场景二：两波兵会合后再进攻

```text
state_machine:
  states:
    gather:
      actions:
        wave_a: {move_to: staging_a}
        wave_b: {move_to: staging_b}
      on:
        - when: arrived(wave_a, staging_a, 5) and arrived(wave_b, staging_b, 5)
          to: engage

    engage:
      actions:
        wave_a: {attack_move_to: front_point}
        wave_b: {attack_move_to: front_point}
```

### 6. 场景三：步坦协同速度差

```text
variables:
  checkpoint: null

states:
  tanks_advance:
    actions:
      tanks: {move_to: target}
      infantry: {hold_position: null}
    on:
      - when: distance_since_checkpoint(tanks, checkpoint) > step_distance
        to: infantry_catch_up

  infantry_catch_up:
    actions:
      tanks: {hold_position: null}
      infantry: {move_to: tanks_center}
    on:
      - when: distance_between(tanks, infantry) < close_gap
        to: {record_checkpoint: tanks_center, then: tanks_advance}
```

语义：

- 坦克每前进 `step_distance`，停下等步兵；
- 步兵靠近到 `close_gap` 后记录新 checkpoint，坦克继续；
- 用两个阈值/步长，避免每帧抖动。

### 7. 场景四：第一波先交火，第二波侧翼包抄

```text
states:
  first_wave_probe:
    actions:
      first_wave: {attack_move_to: enemy_front}
      second_wave: {hold_position: hidden_staging}
    on:
      - when: engaged(first_wave)
        to: second_wave_flank

  second_wave_flank:
    actions:
      first_wave: {attack_move_to: enemy_front}
      second_wave: {attack_move_to: enemy_flank_point}
    on:
      - when: timer_expired(flank_ttl) or group_alive_count(second_wave) < min
        to: retreat_or_done
```

`engaged(first_wave)` 由攻击 order、敌人进入射程、受击事件综合判断。

### 8. 状态机测试

- 所有状态机模块必须带脚本化场景测试：
  - 给定 GameState/事件序列；
  - 断言每个状态发出的 Intent；
  - 断言守卫触发后的状态转移；
  - 断言不会向 user_leased 单位发命令。
- 允许环，但必须证明可终止或存在退出守卫（超时、兵力不足、用户取消）。
- 状态机是确定性的：同一输入序列，同一状态转移和 Intent 序列。

## 反例（明确禁止）

- 在模块内写任意 Python 来比较距离或分支。
- live 中创建或修改 state_machine。
- 用随机数决定转移。
- 无退出条件的死循环状态机入库。
- 把生产 policy 写进 combat state machine。

## 验收标准

1. “到达二矿自动战斗”模块能由 pipeline 表达。
2. “两波会合”模块在两组均到达后才进入 engage。
3. “步坦协同”模块按步长循环等待，且相同输入下转移序列确定。
4. “第一波交火后再包抄”模块在 engaged(first_wave) 为真后才启动第二波。
5. 状态机模块通过离线脚本化场景测试后，live 只允许改参数。
