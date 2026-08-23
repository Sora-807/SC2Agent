# 005 Flow Assembly Schema（Flow 装配层）

- 状态：草案 v0.2
- 范围：production_sequence、groups、bindings、装配层 patch 操作
- 前置：001 Group / 002 Strategy / 003 Step / 004 FlowIR
- 说明：V1 **没有 signals**。战斗启动完全由“Group 自动补兵 + Strategy 首步等待 group_count”完成。

## 1. 定位

装配层是 Flow 的最外层，只做三件事：

```text
1. 生产顺序：production_sequence（线性，不是图）
2. 兵权管理：groups 创建 / 补兵 / 拆分 / 释放
3. 行为绑定：group ↔ strategy 的 attach / switch / detach / pause / resume
```

- 装配层**没有行为图**；行为图全部在 Strategy 内部。
- 生产与战斗的衔接不需要 `m1.ready` 信号：新生产单位进入 free 池，Group 自动补兵，Strategy 用 `group_count` 判断是否具备行动条件。

## 2. 字段定义

```json
{
  "assembly_schema_version": 1,
  "id": "tvp_2medivac_push",
  "race": "terran",

  "production_sequence": [
    {"instance_id": "m0", "module_ref": "fast_expand", "version": 1,
     "params": {"expand_after": "after_barracks"}},
    {"instance_id": "m1", "module_ref": "fast_medivac_production", "version": 1,
     "params": {"marine_target": 16, "medivac_target": 2}}
  ],

  "groups": [
    {
      "group_id": "G1",
      "name": "前压主力",
      "composition": {
        "terran/marine": {"min": 12, "target": 16, "max": 16},
        "terran/medivac": {"min": 1, "target": 2, "max": 2}
      },
      "assemble_at": {"kind": "region", "ref": "main_base"},
      "refill_anchor": "group_center",
      "replenish": {"enabled": true, "check_every_seconds": 10},
      "allocation": {"pool_key": "army_ground", "share": 100, "priority": "strategy"}
    }
  ],

  "strategy_instances": [
    {
      "instance_id": "s1",
      "strategy_ref": "bio_push",
      "version": 1,
      "bindings": {"main": "G1"},
      "params": {"target_region": "enemy_natural", "min_marines": 12, "min_medivacs": 1}
    }
  ]
}
```

## 3. 提交时的启动顺序

一次 Flow 提交（或离线加载）按以下顺序执行：

```text
1. 静态校验 + 编译：
   - production 模块存在、参数合法
   - Group 的 composition/selector 合法
   - Strategy 编译为 FlowIR，槽位/原子/边检查通过
2. 创建 groups：首次填充（允许部分填充，见 001）
3. attach strategies：按 strategy_instances 声明顺序绑定，进入 initial_step
4. 启动 production_sequence：m0 开始，后续按 ADR-0004 顺序推进
```

- Strategy 被 attach 时不要求兵已满员；首步写 `group_count` 等待即可。
- Group 的自动补兵从创建后立即生效（按 001 的 min→target 滞回）。
- 生产模块产出的单位进入 free 池，由 Group 自动补兵接走。

## 4. 装配层操作（patch 原子）

每个操作都是原子 patch；提交前按第 6 节验证。

| 操作 | 语义 | 状态要求 |
|---|---|---|
| `create_group(spec)` | 创建 Group 并首次填充 | 无 |
| `set_group_composition(group_id, composition)` | 更新 min/target/max；高于 max 的部分释放回 free | ready / paused |
| `refill_group(group_id, types?)` | 显式补到 target | 除 released |
| `split_group(source_group_id, children)` | 原子拆分，见 001/006 | ready / paused |
| `release_group(group_id)` | 取消绑定 Strategy、stop、lease 回 free | ready / active / paused |
| `attach_strategy(instance_id, strategy_ref, version, bindings, params)` | 新建 Strategy 实例并绑定 | 相关 Group ready，且未绑定其他 Strategy |
| `switch_strategy(instance_id, strategy_ref, params)` | 换行为，绑定与 lease 不变 | 该实例存在 |
| `detach_strategy(instance_id)` | 结束 Strategy，Group 回 ready | RUNNING / PAUSED |
| `pause_strategy(instance_id)` / `resume_strategy(instance_id)` | 暂停/恢复求值 | RUNNING / PAUSED |
| `set_param(instance_id, path, value)` | 参数更新；hot 不重置，非 hot 拒绝 | 见 002 |
| `insert_production(instance_id, module_ref, position?, params?)` | 加入未完成队列 | 见 ADR-0014 |
| `remove_production(instance_id)` | 删除生产实例 | 见 ADR-0014 |
| `set_production_order([instance_id, ...])` | 重排未开始阶段 | 见 ADR-0014 |

- Strategy 内部不允许任何 Group 操作；违反即编译失败。
- `attach_strategy` 中若某 Group 已绑定其他 Strategy，必须拒绝。
- `switch_strategy` 不得修改 bindings；新 strategy 的 `group_slots` 必须覆盖现有绑定槽位。

## 5. 生产与战斗如何衔接（无 signals）

以“两船兵好了出门”为例：

```text
m1 每 1 游戏秒检查 policy：
  train_while_affordable(marine, limit=16)
  train_while_affordable(medivac, limit=2)

每完成一个单位 → 进入 free 池

G1.replenish（10s 或 free 池变化触发）：
  当前 marine < min(12) → 补到 target(16)
  当前 medivac < min(1) → 补到 target(2)

s1 = bio_push(main=G1)，initial_step=formup：
  formup 每帧：
    if group_count(main) == 0: exit_strategy(failed, GROUP_EMPTY)
    elif marine >= 12 and medivac >= 1: exit_step(done, FORMED)
    else: 不发命令，等待
```

- “什么时候出门” = `formup` 的 `group_count` 条件；
- “什么时候补兵” = Group 的 `min/target`；
- 两者数据源一致，不需要事件信号。

## 6. 提交门禁

任何 patch / 提交必须：

1. JSON schema 校验；
2. production_sequence 结构校验 + planner simulate；
3. Group 校验（composition 类型存在、share 合法）；
4. Strategy 编译（槽位、原子、边、环出口、`(slot,type)` 粒度）；
5. TaskAllocator 静态可行性检查（同一 pool_key 的 share 总和等，见 006）。

失败返回结构化错误，不部分提交。

## 7. 事件与版本

| 事件 | 触发 |
|---|---|
| flow_committed | Flow 提交，含 assembly revision/hash |
| group_created / group_refilled / group_split / group_released | 见 001 |
| strategy_attached / strategy_switched / strategy_detached / strategy_paused / strategy_resumed | Strategy 生命周期 |
| step_enter / step_exit / strategy_exit | 见 004 |

- assembly 每次提交进入版本树；bindings/params 的修改可回滚。
- 回滚 = 恢复到上一 assembly revision，并套用状态迁移规则（002 的 attach/switch/detach 语义）。

## 8. 验收要点

1. 提交只含 production_sequence + groups + strategy_instances，不存在 signals/combat_overlays/triggers。
2. `bio_push` 绑定 G1 后，先到 formup 等待；G1 补满 `min` 前不发出任何战斗 Intent。
3. 生产产出的每个单位最终可被 G1 自动补入，且不会从其他 Group / user_leased 抢兵。
4. 任意 patch 原子：要么全部生效，要么 assembly 不变。
5. Strategy 编译失败时整个提交失败，groups 不会被创建一半。
6. 同一 Flow 提交两次，group 分配顺序与 Strategy 初始状态确定一致。
