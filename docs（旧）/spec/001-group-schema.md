# 001 Group Schema（兵权租约）

- 状态：草案 v0.2
- 范围：Group 的创建、composition、补兵、拆分、释放，以及它与 Strategy 的绑定关系

## 1. 定位

Group 是**兵权租约**：

- Group 创建时从 free 池分配单位，创建后长期持有；
- Group 可以绑定 Strategy、切换 Strategy，但兵权不随 Strategy 变化；
- 只有以下情况单位离开 Group：显式释放、拆分、单位死亡、被用户接管、Flow 装配层显式 retask；
- Strategy / Step **不能**创建、拆分或释放 Group。

## 2. 字段定义

```text
group:
  group_id: G1                       # 本局唯一，不可变
  name: 前压主力                      # 人读名称
  display_name_zh: 前压主力

  composition:                       # 兵员契约；键为稳定类型 ID
    terran/marine:
      min: 12                        # 低于 min 触发补兵
      target: 16                     # 一次补到 target
      max: 16                        # 硬上限，永不超编
    terran/medivac:
      min: 1
      target: 2
      max: 2

  assemble_at:                       # 首次填充的“就近”锚点
    kind: region                     # region | point
    ref: main_base

  refill_anchor: group_center        # 后续补兵锚点；默认 group_center

  replenish:
    enabled: true
    check_every_seconds: 10          # 默认 10，游戏时间秒

  selector:                          # 可选的额外筛选；composition 已隐式 type_in
    source: global                   # global | region(name)
    filters:
      capability: []                 # 如 build / gather_gas
      role: []
      tags: []
      exclude_types: []              # 稳定类型 ID

  allocation:                        # 与其他 Group 抢 free 兵时的仲裁参数
    pool_key: army_ground            # 同 pool_key 参与竞争
    share: 60                        # 可选百分比，缺省时按均分兜底并告警
    priority: strategy               # user | safety | tactical | strategy

  idle_timeout_seconds: null         # null=不自动释放；否则 idle 超时后释放
```

### 2.1 composition 简写与默认值

| 写法 | 展开 |
|---|---|
| `terran/marine: 16` | `min=target=max=16`，且补兵默认关闭 |
| 省略 `min` | `min = target`，不会自动补兵 |
| 省略 `max` | `max = target` |

`replenish.enabled=true` 时，未写 `min` 的兵种永不触发补兵。

## 3. 创建与首次填充

```text
create_group(spec)
```

规则：

1. 候选池只包含 `free` 单位；`user_leased`、其他 Group 已 lease 的单位不可见。
2. `selector.source` 决定候选范围，`composition` 的键作为隐式 `type_in`。
3. 按到 `assemble_at` 的距离从近到远取；同距离按 `unit_ref` 升序。
4. 每种兵最多取到 `max`，优先满足 `target`。
5. 允许部分填充：实际拿到的数量可能低于 `target`。
   - 拿不到任何单位时，Group 仍创建成功，但处于空状态；是否继续由 Strategy 的 `group_count` 分支决定。
6. 多 Group 同时创建并竞争同一批 free 单位时，按 `pool_key + share + priority` 用 TaskAllocator 仲裁。

## 4. 补兵（replenish / refill）

### 4.1 显式补兵

```text
refill_group(group_id, types?={type: target})
```

- 一次调用补到各兵种 `target`；未指定 types 时对所有 composition 条目执行；
- 只从 free 池取，不抢任何已 lease / user_leased 单位；
- 允许部分补齐，返回实际补入数量和缺口。

### 4.2 自动补兵

开启 `replenish.enabled` 后：

1. 每 `check_every_seconds`（游戏时间）或 free 池发生相关变化时检查；
2. 某兵种当前数量 `< min` 才触发；
3. 按到 `refill_anchor` 的距离从近到远补到 `target`；
4. 补完后的下一次触发条件仍是 `< min`，形成 min→target 滞回区间，避免“死一个补一个”的震荡；
5. 太远不补：若未来需要，给 `replenish` 增加 `max_distance` 字段，V1 不做。

## 5. Group 生命周期与状态

```text
forming → ready ⇄ active ⇄ paused
   ↓        ↓        ↓
releasing → released
```

- `active → paused`：pause Strategy，Group 保留 lease。
- `paused → active`：resume Strategy。
- `ready / active / paused` 均可进入 `releasing`（显式释放、`on_exit=release`、用户 retask）。

| 状态 | 含义 | 可执行的操作 |
|---|---|---|
| forming | 正在首次填充 | 等待填充完成 |
| ready | 已完成首次填充尝试（可为空），未绑定运行中的 Strategy | 绑定/切换 Strategy、拆分、释放、refill |
| active | 已绑定且 Strategy 在运行 | pause、refill |
| paused | Strategy 暂停，lease 仍持有 | resume、switch、split、release、refill |
| releasing | 正在释放 | 等待释放完成 |
| released | 已释放，所有 lease 回到 free | 无 |

规则：

- `split_group` 只允许源 Group 处于 `ready` 或 `paused`；`active` 必须先 pause。
- `release_group` 时若仍绑定 Strategy，先取消 Strategy，对 leased 单位发 `stop`，再释放 lease。
- Strategy 正常/异常结束后按 Strategy 的 `on_exit` 决定 `ready`（keep_idle）或 `releasing`（release）。
- `idle_timeout_seconds` 为 null 时 Group 不会因 idle 自动释放；显式释放是唯一路径。

## 6. 拆分

```text
split_group(source_group_id, [
  {group_id, name, composition, assemble_at?}
])
```

规则：

1. 源 Group 必须先处于 `ready` 或 `paused`。
2. 每个子 Group 的 `composition` 是源 Group 现有 lease 的子集；按类型拆分，不直接写单位 ID。
3. 拆分排序：若子 Group 给了 `assemble_at`，从源 Group 内按距离取；否则按 `unit_ref` 升序。
4. 拆分是原子事务：任一子 Group 无法满足其 `min`，则整体失败，源 Group 不变。
5. 拆分成功后，源 Group 剩余部分继续存在（可能为空）。
6. 拆分全程单位不回 free 池，其他 Group / 用户无法插队。

## 7. 与 Strategy 的关系

- Strategy 只声明 `group_slots`（见 002）。
- Flow 装配层执行绑定：

```text
attach_strategy(strategy_ref, bindings={
    main: G1,
    support: null          # 允许；未绑定槽位=空 group
}, params={...})
```

- `switch_strategy(strategy_instance_id, strategy_ref, params)` 只重置 Strategy 状态，**不改变 Group 绑定与 lease**；如果新 params 与新 strategy 的 group_slots 不兼容，则拒绝切换。
- `detach_strategy(strategy_instance_id)` 取消当前 Strategy（kind=cancelled, reason=MANUAL_RETASK），Group 回到 `ready`，lease 不释放。
- Strategy 内部不得出现 create_group / split_group / release_group / refill_group 操作。

## 8. 单位离开 Group 的系统反应

- 单位死亡：立即从 Group 移除，记录 `group_units_lost`。
- 用户接管：立即从 Group 移除，记录 `group_user_takeover`。
- 以上两类都不自动暂停/结束 Strategy；Strategy 通过 `group_count` 分支决定等待、换 step 或失败。
- 自动补兵对这两类流失一视同仁：按 composition 的 `min/target` 补缺，但绝不从 `user_leased` / 其他 Group 拿兵。

## 9. 事件

| 事件 | 触发 |
|---|---|
| group_created | 创建并完成首次填充尝试 |
| group_refilled | 显式或自动补兵 |
| group_split | 拆分成功 |
| group_paused | 绑定 Strategy 暂停，lease 保留 |
| group_resumed | 绑定 Strategy 恢复 |
| group_released | lease 全部归还 free |
| group_units_lost | 单位死亡被移除 |
| group_user_takeover | 用户接管，单位离开 Group |

## 10. 验收要点

1. 同距离候选按 `unit_ref` 升序，结果确定。
2. 自动补兵只在 `count < min` 时触发，并一次补到 `target`。
3. 补兵结果不含 user_leased / 其他 Group 已 lease 单位。
4. 拆分是原子事务；失败时源 Group 不变。
5. `switch_strategy` 后 Group lease 完全不变。
6. Strategy 内部任何创建/拆分/释放 Group 的写法都在编译期被拒绝。
