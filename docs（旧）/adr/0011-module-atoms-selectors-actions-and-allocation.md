# ADR-0011 模块原子：选择子、动作子、空间小工具与冲突分配
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：已确认草案
- 范围：模块的底层定义、FlowSpec 组装、单位分配冲突处理

## 背景

模块库测试门禁依赖一个可穷举、可验证的底层模型。讨论后确定：一切模块最终追溯到两类原子：

- 选择原子：从单位集合中选出哪些单位。
- 动作原子：选出的单位执行什么操作。

选择与执行必须分离；集群中心、区域中心等属于空间小工具，为选择原子服务。

## 决定

### 1. 四类底层对象

| 对象 | 定义 | 副作用 |
|---|---|---|
| SpatialTool | 从当前 GameState/战术地图计算区域中心、集群中心、最近单位等 | 只读，每帧快照缓存 |
| SelectorAtom | 一个同质选择：source + filters + rank + take | 无所有权副作用 |
| SelectorSpec | 一个或多个**命名 SelectorAtom** 的组合，输出命名候选组 | 无所有权副作用 |
| PredicateAtom | 对当前 GameState/事件/组状态做布尔判断，如 arrived、distance、engaged、timer | 只读 |
| ActionAtom | 消费一个已分配 UnitGroup + 参数，输出 Intent 列表 | 不直接调 driver |
| ActionSpec | 把命名组绑定到 ActionAtom 或 pipeline | 不直接调 driver |

### 2. SelectorAtom 原子结构

```text
SelectorAtom(
    source,      # global / region(name) / cluster(center_query)
    filters,     # type_in / role / capability / tags / exclude
    rank,        # nearest_to(point | region_center | cluster_center | group_center)
    pool_key,    # 竞争池键；同 pool_key 的 selector 参与冲突分配
    take,        # {min, max, share}
)
```

- `source` 决定候选范围。
- `filters` 决定“选什么种类”。
- `rank` 决定“按什么顺序取”。
- `take` 决定“取多少”，并携带冲突分配参数。

SelectorSpec 是命名组合，例如两船兵前压需要同时选择机枪兵和运输机：

```text
SelectorSpec(groups={
    "marines":  SelectorAtom(type=terran/marine, take={min:8, max:16}, ...),
    "medivacs": SelectorAtom(type=terran/medivac, take=2, ...),
})
```

每个 SelectorAtom 输出有序候选列表：

```text
{unit_ref, score, source_region, cluster_id}
```

模块永远拿不到“全局原始单位列表的任意访问权”，只能通过 SelectorAtom 查询。

### 3. 空间小工具

初始工具集：

```text
region_center(region)
cluster_centers(scope, filters, radius?)
group_center(unit_refs)
nearest_units(target, filters, k)
nearest_groups(region, count)
distance(a, b)
```

- 全部由 `tactical_map` 提供，模块不允许自己计算距离或中心。
- 结果按 GameState seq 缓存；新 seq 到达后旧结果作废。
- `region_center` 使用区域配置中的锚点。
- `group_center / cluster_centers` 使用确定性算法；具体是质心还是中位数在实现时固定为一种，并在测试中锁定。

### 4. Action 原子结构

初始动作原子集：

```text
move_to(target)
attack_move_to(target)
hold_position(target)
patrol(points)
follow(target_or_group)
use_ability(ability, target?)
load/unload(transport)
wait(seconds)
release()
```

- 每个动作原子是纯函数：`(UnitGroup, params, snapshot) -> list[Intent]`。
- ActionSpec 按命名组绑定动作；每个命名组可以是单个原子或 `pipeline([原子...])`。
- 简单 combat 模块使用 pipeline；复杂协同/交战判断使用 **step if 分支 + 策略图**（见 ADR-0020/0021）。
- pipeline 只允许**顺序执行、无内部循环、无内部条件分支**。
- 循环只允许出现在策略图的有界环中；step 内只做每帧 if 分支判断。

### 4.1 PredicateAtom 初始集合

```text
arrived(group, target, radius)
distance_between(group_a, group_b)
distance_since_checkpoint(group, variable)
engaged(group)              # 近期攻击命令 / 敌人在射程内 / 近期掉血事件
enemy_in_region(region)
group_alive_count(group)
timer_expired(timer)
```

- PredicateAtom 只读，不改变状态。
- 具体判定依赖 GameState、TraceEvent 与模块运行时变量。
- 复杂协同场景由 step if 分支和策略图组合这些 predicate。

### 5. 装配与执行顺序

```text
① GameState 到达
   → UnitPool 更新 free/user_leased/flow_leased
   → tactical_map 更新 region/cluster 索引

② 评估 FlowSpec 中所有模块实例的 activation
   → 生成本轮候选任务集
   → 按 user > safety > tactical > strategy 排序

③ 每个候选任务用 Selector 生成有序候选列表
   → 提交 LeaseRequest，而不是单位 ID

④ TaskAllocator 解决候选重叠，产生 UnitGroup lease

⑤ ActionBinder 把 UnitGroup 与 action 绑定
   → 校验 lease 仍有效、单位仍满足 selector
   → 生成 Intent

⑥ driver 执行
   → done/failed/超时/用户接管 → 释放 lease
```

### 6. 冲突检测与百分比分割

选择子可能重叠：一共 8 个枪兵，两个模块都想选，且各自最多可拿 5 个，就会冲突。

V1 采用显式 `pool_key + share` 模型：

```text
Selector:
  pool_key = "army_ground"   # 参与竞争的池子键
  share = 60                 # 与同 pool_key 的其他 selector 竞争时的百分比
  take = {min: 4, max: 12}
```

分配规则：

1. 所有同 `pool_key` 的 selector 视为一个竞争组。
2. 编译/装配时校验：同一竞争组内 `share` 总和不得超过 100。离线创作与提交门禁默认拒绝此类 FlowSpec。未声明 share 不阻止编译，但给出离线警告，提示作者补齐。
3. 运行时兜底：如果因历史版本、热载异常等原因仍出现 share 总和超过 100 的竞争组，**绝不崩溃**。TaskAllocator 将 share 归一化到总和 100 后按比例分配，发出 `ALLOCATION_OVERCOMMIT` 警报，把该模块/FlowSpec 标记为 `degraded`，等待离线修正。
4. 只有一个模块竞争该 pool_key 时，按该模块自己的 `take` 取，share 不生效。
5. 多个模块竞争且已声明 share 时：
   - 按各 selector 的 `share` 计算应得数量；
   - 整数分配采用最大余数法，确定性无随机；
   - 平局按模块优先级决定。
6. 多个模块竞争但没有声明 share 时，不阻断 flow 执行：
   - 对竞争集合均分；
   - 奇数无法均分时，多余单位按模块优先级分配；
   - 发出 `ALLOCATION_IMPLICIT_EQUAL_SPLIT` 警告；
   - 该 FlowSpec 标记为 `allocation=implicit`，提示离线补齐 share。
7. 实际挑人时，按优先级从高到低，每个模块在自己排序后的候选列表中取走应得数量；已被取走的单位跳过。selector rank 相同距离时，以稳定 `unit_ref` 升序作为 tie-break。
8. 未被竞争的单位优先分给唯一申请者；竞争集合按上述 share 或均分规则分割。
8.1 **Sticky lease**：已建立的 lease 不参与下一轮重新求解。仅当任务 done/fail/cancel、单位死亡、被用户接管、结构参数重置或显式 refresh 时才释放；share/参数变化只影响未来新分配，不抢回已分配单位。
9. 分配结果低于模块 `take.min` 时，触发模块的 fallback：`wait / relax_scope / degrade / abort`。
10. share 总和小于 100 时，剩余单位留回 free 池。

示例：

```text
8 个枪兵，两个模块 pool_key 都是 army_ground：
  defend_natural  share=60  → 应得 5
  push_from_main  share=40  → 应得 3
```

- `0.6 * 8 = 4.8`，余数 0.8；
- `0.4 * 8 = 3.2`，余数 0.2；
- defend 获得额外 1 个，结果 5/3。

未声明 share 时：

```text
8 个枪兵，两个模块都未声明 share：
  → 均分为 4/4，并发出 ALLOCATION_IMPLICIT_EQUAL_SPLIT 警告

7 个枪兵，两个模块都未声明 share：
  → 3/3 后余 1，优先级高的模块获得，结果 4/3
```

### 7. 与用户单位的关系

- Selector 只能看到 `free` 池；`user_leased` 与 `flow_leased` 不在候选内。
- 用户单位归还后进入 free，才会被下一轮 selector 看到。
- 用户中途接管一个已租借单位时，该单位离开 lease，模块单位数减少，低于 min 走 fallback。

### 8. 模块类别：生产与对战分离

V1 模块只允许两类，不允许混合模块：

| 类别 | 内部结构 | 投影 |
|---|---|---|
| production | 声明式 policies，无 SelectorSpec/ActionSpec | full |
| combat | SelectorSpec + 策略图/step if 分支 + ActionSpec + activation + lifecycle + fallback | partial 或 none |

生产模块使用：

```text
WorkerManager          工人配额
ConstructionScheduler  全局建造调度
```

它们不参与战斗单位 Selector 竞争。

“速开二矿”“速出两船兵”是 production 模块；“两船兵出门/空投/前压”是 combat 模块。二者通过 FlowSpec 的 `production_sequence` / `combat_overlays` / `triggers` 连接。

### 8.1 模块优先级

- 每个模块 manifest 声明 `default_priority`，枚举：

```text
user | safety | tactical | strategy
```

- 默认值：`strategy`。
- FlowSpec 实例可选择性覆盖 `priority`，但 `user` 只允许来自用户显式指令。
- 分配顺序与冲突处理按该优先级执行。

## 反例（明确禁止）

- 模块直接持有单位 ID 列表。
- 模块自己计算“哪个兵离矿最近”。
- 两个模块竞争同一批单位但未声明 share，系统随机分配。
- 每帧重新分配 lease，导致单位在不同模块之间跳动。
- ActionAtom/pipeline 内部包含循环或条件分支（分支属于 step if 表，环只属于策略图）。

## 验收标准

1. 给定 8 个枪兵和两个 share=60/40 的选择子，分配结果确定且为 5/3。
2. 同一 pool_key 的 share 总和超过 100 时，离线编译/提交被拒绝。
3. 如超 100 的配置在运行时出现，系统不崩溃：归一化后继续分配，并发出 ALLOCATION_OVERCOMMIT 警报。
4. 检测到重叠但未声明 share 时，不阻断执行：均分竞争集合，奇数余量按优先级分配，并发出警告。
5. Selector 结果不包含 user_leased / flow_leased 单位。
6. 空间小工具结果在相同 seq 内缓存一致，新 seq 后重新计算。
7. 所有 Action 只产生 Intent，不直接调 driver。
8. 相同输入下多次装配与分配结果完全一致。
9. 同距离候选按 unit_ref 升序 tie-break，结果确定。
10. 已 lease 单位在无释放条件时不参与重新分配。
