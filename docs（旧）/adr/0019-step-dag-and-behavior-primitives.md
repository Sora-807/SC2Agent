# ADR-0019 Step DAG 编排 + 守卫 Step + 行为原语
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：部分有效（尝试层 DAG 与行为原语保留）；`do/until` 由 ADR-0020 替代，策略环由 ADR-0021 定义
- 范围：combat 模块的编排模型；回答“循环放在 step 内还是 DAG 外”的问题

## 背景

用户提出的新模型：

- 一个模块是一条甬道，内部由 step 构成。
- step 之间是 DAG，有先后顺序和依赖关系。
- 前置 step 全部完成后，后续 step 才执行。
- 例：三支部队都到达各自位置后，再执行下一步。

问题：步坦协同这种“走一段、等一段、再走”的循环，应该放在 step 内部，还是把外部 DAG 改成环？

## 决定

### 1. 外层保持 DAG，不引入环

- 模块编排图必须是 DAG。
- 节点是 step，边是“前置完成才能开始”的依赖。
- DAG 保证 UI 简单、进度可预测、不会出现“绕回去”的复杂状态。
- V1 的 join 语义：一个 step 的所有前置都 `done` 后，该 step 才开始。

### 2. Step 自带隐式等待循环

每个 step 是：

```text
step:
  do:    进入时执行的动作
  until: 原子判定
  on_done: 可选状态更新
```

- 每帧检查 `until`。
- 条件不满足：留在当前 step，下一帧再检查。这就是循环，但它是 step 内部的隐式循环。
- 条件满足：step 标记 `done`，下游 step 解锁。

因此“判断就是循环”落在这里：**循环不属于图，属于 step 的执行语义。**

### 3. 常见连续控制封装为“行为原语”

有些循环比“等待一个条件”复杂，例如步坦协同要反复执行“前进一段 → 停下等步兵”。不让模块作者在 DAG 里画环，也不允许 step 内写任意逻辑，而是把这种循环封装成**可复用行为原语**：

```text
step:
  behavior: escort_advance
  params:
    leader: tanks
    follower: infantry
    step_distance: 20
    catchup_gap: 8
    target: enemy_natural
  until:
    - arrived(tanks, target, 5)
```

- `escort_advance` 是一个离线实现、测试过的行为原语。
- 它内部拥有自己的循环状态，但对外只是一个 step。
- UI 显示这一个节点 + 内部阶段名 + iteration，不在外部 DAG 中画环。
- 新行为原语离线开发和入库；live 只允许选用和调参数。

### 4. Step 类型

| 类型 | 说明 |
|---|---|
| action step | 一个动作 + 一个 until；最常见 |
| behavior step | 引用命名行为原语，内部可循环 |
| join step | 只等待前置完成，无动作，用于多路汇合 |
| signal step | 等待模块/泳道信号或 manual trigger |
| spawn step | 生成子模块实例 |
| terminal step | done / failed |

### 5. 场景一：三支部队会合

```text
steps:
  S1a: group_a.move(A); until arrived(a,A,5)
  S1b: group_b.move(B); until arrived(b,B,5)
  S1c: group_c.move(C); until arrived(c,C,5)

  S2: 依赖 S1a + S1b + S1c
      do: all.attack_move(front)
```

- S1a/S1b/S1c 并行。
- 三个都 done，S2 才开始。
- DAG 没有环。

### 6. 场景二：步坦协同

```text
steps:
  S1:
    behavior: escort_advance
    params:
      leader: tanks
      follower: infantry
      step_distance: 20
      catchup_gap: 8
      target: enemy_natural
    until: arrived(tanks, target, 5)

  S2:
    依赖 S1
    do: tanks.hold(); infantry.hold()
```

- 循环被封装在 S1 的 `escort_advance` 原语内部。
- 外部 DAG 仍然是线性的 S1 → S2。
- 模块作者不写循环，只写“用什么原语、参数多少、到哪算完成”。

### 7. 场景三：第一波交火后第二波包抄

```text
steps:
  S_probe:
    do: first_wave.attack_move(front)
    until: engaged(first_wave)

  S_hidden:
    do: second_wave.hold(hidden_staging)
    until: lane(S_probe).done

  S_flank:
    依赖 S_hidden
    do: second_wave.attack_move(flank_point)
    until: enemy_cleared(flank_point) or timer_elapsed(flank_ttl)
```

- S_probe 和 S_hidden 并行开始。
- S_probe 交火并 done 后，S_hidden 的 until 满足并 done。
- S_flank 启动包抄。

### 8. 如果现有行为原语不够怎么办

- 模块作者**不能在 live 中发明新循环**。
- 离线流程：提出新原语 → 用原子动作/判定实现 → 脚本化场景测试 → 入库 → 模块作者引用。
- 这保持模块编排始终是 DAG，复杂循环只存在于少量、经过测试的库原语中。

## 反例（明确禁止）

- 把外部 DAG 改成有环图。
- 让模块作者在 step 内部写任意循环逻辑。
- 每个步坦协同模块都复制一份自己的 pacing 循环实现。
- live 中创建新行为原语。
- UI 把行为原语内部循环展开成外部环。

## 验收标准

1. 三部队会合、步坦协同、交火包抄都能用 DAG + step 表达。
2. 所有模块编排图可验证为 DAG。
3. step 的 until 不满足时停留原 step，不重复发送无意义 Intent。
4. `escort_advance` 行为原语通过脚本化场景测试，并在多个模块中复用。
5. UI 显示 DAG 节点与行为原语内部进度，不显示外部环。
