# ADR-0012 模块场景演练：速开二矿与速出两船兵
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：已确认草案
- 范围：用具体场景锁定模块写法、agent 调用方式、生产与对战模块的分割

## 背景

需要确认两件事：

1. 游戏过程中 agent 如何调用战术模块、改参数、调顺序。
2. “两船兵”应如何切分。

结论方向：**“速出两船兵”是生产模块；“两船兵出门/空投/前压”是战斗模块。二者分离。**

## 决定

### 1. V1 生产模块按阶段顺序执行

- production 模块在 FlowSpec 中按 `production_sequence` 依次执行。
- V1 默认同一时刻只有一个 production 模块处于 active。
- 如果确实需要同时推进两类生产，应写成一个生产模块内部的多个 policy，而不是让两个生产模块并行打架。
- combat 模块可以多个并行，由 TaskAllocator 按 ADR-0011 解决单位竞争。

### 2. 模块示例：速开二矿

```text
module:
  id: fast_expand
  category: production
  race: [terran]

  params:
    expand_after: {enum: [immediate, after_barracks], default: after_barracks}
    worker_target: {int, default: 22}
    supply_margin: {int, default: 3}

  policies:
    - keep_workers(target=params.worker_target)
    - build_when(type=supply_depot, when=supply_headroom < params.supply_margin,
                 placement=zone("main_base"))
    - build_when(type=barracks, when=has_building(supply_depot) and minerals >= 150,
                 placement=zone("main_base"))
    - expand_when(condition=params.expand_after)

  signals:
    done: has_ready_base(natural)
```

说明：

- 它是 production，不选择战斗单位。
- planner 可以从 policies 自动生成 ProjectionSpec。
- “二矿先还是兵营先”只是参数 `expand_after`，不换模块。

### 3. 模块示例：速出两船兵（只生产，不进攻）

```text
module:
  id: fast_medivac_production
  category: production
  race: [terran]

  params:
    marine_target: {int, default: 16}
    medivac_target: {int, default: 2}
    worker_target: {int, default: 45}

  policies:
    - keep_workers(target=params.worker_target)
    - build_when(type=supply_depot, when=supply_headroom < 3,
                 placement=zone("main_base"))
    - ensure_production(building=barracks, addon=reactor)
    - ensure_production(building=starport, addon=reactor)
    - train_while_affordable(unit=marine, limit=params.marine_target)
    - train_while_affordable(unit=medivac, limit=params.medivac_target)

  signals:
    ready:
      - unit_count(terran/marine) >= params.marine_target
      - unit_count(terran/medivac) >= params.medivac_target
```

说明：

- 这个模块只管“把两船兵造出来”，不包含 move/attack。
- 它发出 `ready` 信号，供后续战斗模块作为 activation。
- planner 可完整投影它，projectable = full。

### 4. 模块示例：两船兵出门（战斗模块）

```text
module:
  id: bio_push
  category: combat
  race: [terran]

  activation:
    signal: m1.ready        # 只允许 instance_id 引用，避免同名模块多实例歧义
    or: manual_dispatch

  selector:
    groups:
      marines:
        source: global
        filters: {type_in: [terran/marine]}
        rank: nearest_to(rally_point)
        take: {min: 8, max: 16}
        pool_key: army_ground
        share: 100

      medivacs:
        source: global
        filters: {type_in: [terran/medivac]}
        rank: nearest_to(group_center("marines"))
        take: 2

  action:
    marines:
      pipeline:
        - attack_move_to(enemy_region_center)
    medivacs:
      pipeline:
        - follow(group="marines")

  lifecycle:
    done:
      - target_region_clear
      - or user_cancel
      - or ttl
    failed:
      - allocated_count(marines) < take.min

  fallback: wait
```

说明：

- 选择与执行分离：先选“机枪兵组 + 运输机组”，再分别绑定动作。
- 该模块不关心枪兵是谁造出来的；只要 activation 满足，就从 free 池选。
- 它不含生产政策，projectable = none 或 partial。

### 5. 组装成 FlowSpec

```json
{
  "id": "tvp_2medivac_push",
  "race": "terran",
  "production_sequence": [
    {"id": "m0", "ref": "fast_expand", "version": 1, "params": {"expand_after": "after_barracks"}},
    {"id": "m1", "ref": "fast_medivac_production", "version": 1, "params": {"marine_target": 16, "medivac_target": 2}}
  ],
  "combat_overlays": [
    {"id": "m2", "ref": "bio_push", "version": 1, "params": {}}
  ],
  "triggers": [
    {"when": "m1.ready", "start": "m2"}
  ]
}

`production_sequence` 自带 m0→m1 先后；triggers 只用于启动 combat_overlays。
```

### 6. 游戏过程中 agent 如何调用

用户说：

> “速开二矿，开完转两船兵，好了之后出门打。”

no_think router 将其拆成 patch：

```text
insert_production(m0, fast_expand, params={expand_after: after_barracks})
insert_production(m1, fast_medivac_production, params={marine_target:16, medivac_target:2})
insert_combat(m2, bio_push)
set_production_order([m0, m1])
set_trigger(m1.ready → m2.start)
validate_flow
simulate_flow
commit_flow
```

游戏内实际运行：

```text
m0 active → 完成二矿 → m0.done
m1 active → 补农民、造兵、出运输机
m1.ready 满足 → m2 activation 满足
TaskAllocator 为 m2 分配 16 枪兵 + 2 运输机
ActionBinder 绑定 action
m2 开始 attack_move
```

### 7. 后续用户改口

| 用户说 | patch |
|---|---|
| “先别出门，兵留家里” | `set_trigger(m2, manual)` 或移除触发，m2 不自动启动 |
| “16 个枪兵改 12 个” | `set_param(m1, marine_target, 12)` 后重新 validate + simulate |
| “不要前压，改空投” | 把 m2 从 `bio_push` 换成 `bio_drop`；m0/m1 不变 |
| “守家优先级最高” | 插入 `hold_ramp` 并设置 activation/优先级 |
| “两船兵好了先投主矿” | `set_param(m2, target, enemy_main)` |

关键效果：换“怎么用兵”时，不需要重写“怎么造兵”；生产模块与战斗模块彻底解耦。

## 反例（明确禁止）

- 把 `attack_move` 写进 `fast_medivac_production`。
- 把“造两船兵”写进 `bio_push`。
- 在 m1 内直接点名 m2 的模块 ID。
- 游戏中让 agent 现场创建新的 production/combat 模块。
- 两个 production 模块并行且同时修改工人目标/建造策略。

## 验收标准

1. `fast_medivac_production` 编译后 projectable = full，且不产生任何 move/attack Intent。
2. `bio_push` 编译后 projectable = partial 或 none，且不产生任何 build/train 政策。
3. `m1.ready` 未满足时，m2 不申请单位。
4. 用户说“改 12 个枪兵”只改变 m1 参数并重新模拟，不触发模块重建。
5. 把 m2 从 `bio_push` 换成 `bio_drop` 时，m0/m1 不受影响。
