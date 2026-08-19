# 006 Allocation Algorithm（Group 分配算法）

- 状态：草案 v0.2
- 范围：Group 创建、补兵、拆分时的确定性分配
- 前置：001 Group Schema
- 目标：相同输入（free/leased 单位集合 + 所有请求）下，分配结果完全确定、可测试。

## 1. 输入与优先级

### 1.1 AllocationRequest

每个 Group 操作在同一 game seq 内生成一个请求：

```text
AllocationRequest:
  request_id            # 单调递增
  group_id
  reason                # create | refill | split_child
  composition           # {type: {min, target, max}}
  anchor                # assemble_at / refill_anchor
  filters               # source + capability/role/tags/exclude_types
  priority              # user > safety > tactical > strategy
  pool_key              # 缺省为 "default"
  share                 # 可选；null 表示未声明
  seq                   # 进入请求队列的顺序
```

- `target` / `max` 未声明时视为 `+∞`（仅用于测试请求；正式 Group 请求由 001 composition 总给出有限值）。

- `user_leased` 与已有 Group lease 的单位**永不进入候选池**。
- 拆分请求的候选域是**源 Group 现有 lease 集合**，不是 free 池。

### 1.2 全局顺序

1. 先按 `priority` 排序；
2. 同 priority 按 `seq`（先到先得）；
3. 单元级 tie-break 永远按 `unit_ref` 升序。

## 2. 按兵种独立求解

每种 `stable_type` 独立求解，但一个请求的 `min` 表示该兵种维度必须满足。流程对每个 type 执行：

```text
① 构造候选映射：unit → 匹配的请求列表
② 将候选单位分为：
   exclusive units   只匹配一个请求
   shared units      匹配多个请求
③ Phase 1：保底 min（先 exclusive 后 shared）
④ Phase 2：追 target（只在 shared 池内按 share 权重）
⑤ 截断 max 并回流余量
```

### 2.1 Phase 1：保底 min

```text
for request in 请求顺序:
    # 先拿 exclusive 单位
    从 exclusive units 中按 anchor 距离取，直到 min 或取尽
    # 仍不足，再从 shared units 中按 anchor 距离取，直到 min 或取尽
    已取单位从所有候选映射中移除
```

- Phase 1 中 **share 不参与**，先保证每个请求的 `min`。
- 若 free 池总量不足，后到的低优先级请求可能拿不到 `min`；分配结果照常返回缺口。
- 请求拿到的数量低于 `min` 时，Group 进入 partial 状态；是否继续由 Strategy `group_count` 决定。

### 2.2 Phase 2：追 target

对每个 `(pool_key, type)` 的竞争组：

```text
参与方 = 当前数量 < target 的请求，且 target > 当前数量
可用量 = 竞争组共享候选单位中尚未分配的数量
```

#### 2.2.1 权重计算

| 情形 | 权重 |
|---|---|
| 所有参与方都声明 share | `weight_i = share_i`，总和为 100（离线已校验） |
| 全部未声明 share | `weight_i = 1 / n` 均分 |
| 部分声明 share | 已声明按声明值；未声明均分剩余权重：`weight = (100 - sum_declared) / count_undeclared`；若 `sum_declared >= 100`，未声明权重为 0；无论哪种情况都发 `ALLOCATION_IMPLICIT_EQUAL_SPLIT` 警告 |

- 运行时若 share 总和 > 100：归一化到 100，发 `ALLOCATION_OVERCOMMIT`，相关请求标 degraded。
- 运行时 share 总和 < 100：按原权重分配，剩余单位回 free。

#### 2.2.2 最大余数法

```text
capacity_i = min(target_i - current_i, max_i - current_i)
raw_i = available * weight_i / 100
base_i = floor(raw_i)
remainder_i = raw_i - base_i
先给每个请求 base_i
余量 = available - sum(base_i)
按 remainder 降序、再按 priority、再按 seq 依次分配余量，每次 1
```

- 分配后若某请求超过 `capacity_i`，截断到 `capacity_i`，截下的单位回流 available；
- 对未满 capacity 的请求重复上述过程，直到 available=0 或所有请求到 capacity。

#### 2.2.3 exclusive 单位不受 share 影响

- Phase 1 后剩余的 exclusive 单位，先按请求顺序直接分配给唯一申请者，直到 `target`（受 `max` 截断）；
- 之后才在 shared 池按 share 权重分配；
- exclusive 单位永远不参与 share 竞争。

## 3. 多请求同帧 / 新建与补兵同帧

- 同帧的新建 Group 与补兵请求进入同一个请求队列，统一按第 1 节顺序求解。
- 每个单位一帧只能被分配一次（请求求解后单位从候选池移除）。
- 自动补兵周期到了但 free 池无变化时，也允许重算；结果必须与同输入一致。

## 4. 拆分

```text
split_group(source_group_id, children)
```

1. 源 Group 必须 `ready` 或 `paused`。
2. 候选域 = 源 Group 的 leased units。
3. 每个 child 按第 1~2 节算法求解，但：
   - child 之间竞争同一个候选域；
   - `reason = split_child`；
   - 任一 child 的任一 type 未能达到其 `min` → 整个拆分失败，源 Group 不变。
4. 成功时：child 建立新 lease；源 Group 保留剩余 units（可能为空）。
5. 拆分过程单位不回 free，其他请求不可见。

## 5. 违反规则的运行期兜底

| 异常 | 行为 |
|---|---|
| share 总和 > 100 | 归一化 + `ALLOCATION_OVERCOMMIT`，不崩溃 |
| 未声明 share 的竞争 | 均分 + `ALLOCATION_IMPLICIT_EQUAL_SPLIT` |
| min 无法满足 | 返回缺口；Group partial；Strategy 按 `group_count` 处理 |
| 请求进入时单位已死亡/被接管 | 从候选池剔除，重新排序，不崩溃 |
| 同距离候选 | `unit_ref` 升序，确定性 |

## 6. 固定 Fixture（实现必须通过）

1. 8 个枪兵，两个请求 share 60/40，无 min/max 约束 → 5/3。
2. 7 个枪兵，两个请求均未声明 share → 4/3（余量给高优先级），发警告。
3. 8 个枪兵，请求 A share=60、target=∞、max=2，请求 B share=40、target=∞、max=∞ → A 截到 2，余量回流给 B，结果 A=2、B=6。
4. 8 个枪兵，A.min=4 share=60，B.min=3 share=40，target/max=∞ → 先保底 4/3，余 1 按最大余数给 A（结果 5/3）。
5. 10 个枪兵，A 匹配前 8 个、B 匹配后 6 个（交集 4 个），share 50/50，A/B 均 min=0、target=max=6/4 → A 独占 4 + shared 2 = 6，B 独占 2 + shared 2 = 4。
6. 同帧 A 新建（min=8）与 B 补兵（min=4），A 优先级 safety、B strategy，free 只有 10 → A=8、B=2，缺口 2。
7. split：源 Group 有 10 枪兵，child A 与 child B 均 min=target=max=4 → 成功，源 Group 剩 2。
8. split：源 Group 有 6 枪兵，child A 与 child B 均 min=target=max=4 → 整体失败，源 Group 不变。
9. 两个请求 share 均为 60，总和 120 → 运行期归一化为 50/50，结果均分，发 ALLOCATION_OVERCOMMIT。
10. 同距离单位：unit_ref 升序，重复运行结果逐单位一致。

## 7. 验收要点

1. 以上 10 个 fixture 全部通过。
2. 相同输入重复求解 100 次，分配的单位列表完全一致。
3. 已 lease 单位永不出现在任何候选池。
4. 同一单位同帧不会被两个 Group 分走。
5. 拆分失败时源 Group 的 leased_units 集合逐单位不变。
