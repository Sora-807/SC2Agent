# ADR-0021 有界环图：策略层循环 + 尝试层 DAG + if 分支语法
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：已确认草案
- 替代：无
- 范围：模块图到底用 DAG 还是环图；step 分支语法

## 背景

讨论后出现两个问题：

1. step 的 `do/until` 不够直观，希望改成 `if 条件: 行动`。
2. 如果退出原因已经能路由，是否应允许图中有环：例如前进 → 遇敌 → 交战 → 敌清 → 回到前进。

## 决定

### 1. Step 分支语法改为 if / elif / else

每个 step 是一个每帧求值的分支块：

```text
step advance:
  if arrived(tanks, target, 5):
      exit done, reason=ARRIVED

  elif enemy_count_near(tanks, 15) > 8:
      exit interrupted, reason=AMBUSHED

  elif distance_between(tanks, infantry) > threshold:
      tanks.hold()
      infantry.move(target)

  else:
      tanks.move(target)
      infantry.move(target)
```

- 每帧从上到下判断，第一个命中的分支执行后本帧结束。
- `exit` 表示退出本 step 并携带原因。
- 普通行动分支不退出，下一帧继续判断。
- 游戏循环就是循环。
- **格式载体**：`if/elif/else` 文本是给人阅读的规范与示例；模块 manifest 的唯一权威格式是 JSON。可视化方块图编辑器最终也生成 JSON；不存在运行期执行的 `.flow` 文本文件。

### 2. 图分两层：策略层允许环，尝试层保持 DAG

如果只允许 DAG，就必须不断复制“继续前进”节点，很别扭；如果允许任意环图，并行、join、重入语义会失控。

因此采用两层：

```text
策略图 StrategyGraph（允许有界环）
  ├─ ADVANCE   内部：尝试 DAG（无环）
  ├─ COMBAT    内部：尝试 DAG（无环）
  ├─ RETREAT   内部：尝试 DAG（无环）
  └─ HOLD      内部：尝试 DAG（无环）
```

- **策略层**：节点是策略模式，边由退出原因驱动，允许环。
- **尝试层**：每个策略内部是一个 DAG 的 step 序列，完成一次尝试。
- 从 COMBAT 回到 ADVANCE 时，是开始 ADVANCE 的一次**新尝试**，不是在同一 DAG 内回边。
- **并行 step 约束**：同一命名 group 同一时刻最多出现在一个 active step 中；并行 step 必须使用不同 group。编译期校验。

### 3. 策略图示例：步坦推进遇敌

```text
strategies:
  ADVANCE:
    steps: [move_with_escort, confirm_arrival]
  COMBAT:
    steps: [focus_fire, assess]
  RETREAT:
    steps: [fall_back, hold_safe]

transitions:
  ADVANCE --done(ARRIVED)------------> HOLD
  ADVANCE --interrupted(AMBUSHED)----> COMBAT
  ADVANCE --failed(UNITS_LOST)-------> RETREAT

  COMBAT --done(ENEMY_CLEARED)-------> ADVANCE   # 环：回到前进
  COMBAT --failed(UNITS_LOST)--------> RETREAT

  RETREAT --done(SAFE)---------------> HOLD
```

- COMBAT → ADVANCE 形成环。
- 每次回到 ADVANCE，`attempt += 1`，内部 DAG 从第一步重新开始。
- `checkpoint` 等需要跨 attempt 保留的变量可声明为 `persistent`。
- **两层路由优先级**：step 以某原因退出时，先在当前策略内部的尝试 DAG 中匹配该原因边；内部没有匹配边，才查策略层转移边。两层都无匹配则 FAILED。

### 4. 环的约束

不是任意环图，而是有界环：

- 每个环必须至少有一条非环出口（到达 HOLD/RETREAT/FAILED 等）。
- 策略边可以声明 `max_iterations`。
- 系统全局默认上限（例如 100 次策略转移）防止意外死循环。
- 超过上限：退出原因 `LOOP_LIMIT`，模块 FAILED 并告警。
- 环只允许出现在策略层；策略内部的 step DAG 禁止回边。

### 5. UI 展示

```text
当前策略：ADVANCE   attempt: 3
  ✓ move_with_escort
  → confirm_arrival

策略转移历史：
  ADVANCE#1 --AMBUSHED--> COMBAT#1
  COMBAT#1 --ENEMY_CLEARED--> ADVANCE#2
  ADVANCE#2 --AMBUSHED--> COMBAT#2
  COMBAT#2 --ENEMY_CLEARED--> ADVANCE#3
```

- 策略层显示为模式节点和转移边，环可见。
- 尝试层显示为当前策略内部的 DAG 进度。
- 不在 UI 中展开无限长的历史，只保留最近 K 次转移，完整历史进 trace。

### 6. 为什么不直接用单层环图

- 单层环图会同时承担“战术流程”和“策略切换”，节点职责混乱。
- 环重入时，不知道是“继续原 step”还是“开始新一次尝试”。
- 并行 join 与回边混在一起，状态迁移难以验证。

两层模型把复杂度切成两块：

```text
策略层：我在执行哪个大策略，可以循环。
尝试层：这次策略内部完成到哪一步，不循环。
```

## 反例（明确禁止）

- 策略内部 step DAG 出现回边。
- 策略图无限环，没有非环出口。
- 回到某策略时继续旧 step 的中间状态而不是开始新 attempt。
- `if` 分支顺序不固定或随机。
- 任意单层环图，不区分策略与尝试。

## 验收标准

1. 步坦推进遇敌：ADVANCE --AMBUSHED--> COMBAT --ENEMY_CLEARED--> ADVANCE 能循环，且 attempt 递增。
2. 每次重入 ADVANCE，内部 DAG 从第一步开始；persistent 变量按声明保留。
3. 策略图存在至少一条非环出口；无出口的环在入库时被拒绝。
4. 策略转移达到上限时以 LOOP_LIMIT 失败，不无限运行。
5. UI 能展示当前策略、内部 DAG 进度、最近转移历史。
