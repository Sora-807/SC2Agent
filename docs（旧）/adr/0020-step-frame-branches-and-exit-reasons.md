# ADR-0020 Step = 每帧分支表 + 退出原因路由
> **本文件已由 ADR-0028 限定/替代，有效范围以 `docs/spec/` 为准。**

- 状态：已确认草案
- 替代：无；`if/elif/else` 写法以 ADR-0021 为准
- 范围：step 内部语义；遭遇战转移；退出后如何路由到其他策略

## 背景

需要澄清两件事：

1. `do / until` 到底怎么执行？
2. step 结束时如果只返回成功/失败，无法表达“遭遇敌人”“兵力损失”“用户取消”等不同去向。

核心结论：**游戏本身就是循环。每帧我们只做一件事：按顺序评估这个 step 的分支表，决定本帧发什么命令、是否退出、退出原因是什么。**

## 决定

### 1. Step 是一个每帧求值的分支表

> 本节示例保留 `when` 伪代码以解释语义；正式作者写法采用 ADR-0021 的 `if / elif / else`。

```text
step:
  mode: advance          # step 内部模式变量，初始值

  every_frame:
    - when: arrived(tanks, target, 5)
      exit: done, reason=ARRIVED

    - when: enemy_count_near(tanks, 10) > 0
      set mode: combat
      do: tanks.focus_fire(enemy)

    - when: mode == combat and enemy_count_near(tanks, 10) == 0
      set mode: advance

    - when: mode == advance and distance_between(tanks, infantry) > threshold
      do: tanks.hold()
          infantry.move(target)

    - default:
      do: tanks.move(target)
          infantry.move(target)
```

执行语义：

- 每帧从第一条开始判断。
- 第一个为真的分支生效，然后本帧结束。
- `exit` 分支会终止 step。
- 非 `exit` 分支只改变本帧动作或内部 `mode`，step 下一帧继续。
- 没有分支命中时，执行 `default`；没有 default 就保持当前动作。
- 游戏循环就是循环；没有 while。

所以“步坦协同”不是写一个循环，而是写几行每帧判断：

```text
还没到？继续走。
前后距离太远？前面的停。
没有异常？都走。
```

### 2. Step 内部 mode

- `mode` 是 step 声明的小型状态变量，用于处理“暂时切换行为，之后还要回到原目标”。
- 例如行进中遇敌：`advance → combat → advance`。
- 这种内部 mode 切换不算 DAG 环，外部图仍无环。
- 复杂可复用的分支表可以离线封装为 behavior primitive，例如 `escort_advance`、`advance_with_ambush_response`。

### 3. Step 退出原因

每个 step 退出时产生结构化记录：

```text
ExitRecord(
    kind,       # done | interrupted | failed | cancelled
    reason,     # ARRIVED / AMBUSHED / TARGET_CLEAR / UNITS_LOST / TIMEOUT / USER_CANCEL ...
    payload,    # 相关单位、位置、事件
    seq,        # 当时的 GameState seq
)
```

V1 初始原因集合：

```text
done:
  ARRIVED
  TARGET_CLEAR
  SIGNAL_RECEIVED

interrupted:
  AMBUSHED
  TIMEOUT
  USER_CANCEL
  MANUAL_RETASK

failed:
  UNITS_LOST
  ALLOCATION_FAILED
  INVALID_TARGET
```

### 4. DAG 按退出原因路由

DAG 边默认只在 `done` 时激活；也可以标注特定原因：

```text
edges:
  S_advance --done(ARRIVED)----------> S_hold
  S_advance --interrupted(AMBUSHED)--> S_focus_fire
  S_advance --failed(UNITS_LOST)-----> S_retreat
```

- 一个 step 可以有多个出边，按原因匹配。
- **路由优先级**：step 退出后，先在当前策略的尝试 DAG 内匹配原因边；匹配不到，再上升到策略层匹配转移边（见 ADR-0021）。两层都无匹配时，模块进入 FAILED 并保留 ExitRecord。
- 同一层内多条边匹配时，按声明顺序取第一条。
- 这样 step 结束后可以路由到指定决策，而不是只能成功/失败。

### 5. 什么时候不退出，什么时候退出

| 情况 | 处理 |
|---|---|
| 步坦协同中前后距离太远 | 不退出；step 内分支让前方停下 |
| 行进中遭遇小股敌人 | 不退出；step 内 mode 切到 combat，清除后回 advance |
| 遭遇大股敌人，需要改变整个计划 | 退出，reason=AMBUSHED，路由到防守/撤退 step |
| 部队损失到无法完成原目标 | 退出，reason=UNITS_LOST，路由到重组/撤退 step |
| 到达目标 | 退出，reason=ARRIVED，进入下一目标 |

判断标准：**如果处理完还要继续原目标，就留在 step 内；如果整个目标变了，就带 reason 退出并路由。**

### 6. 回到原 step 的问题

**同一策略内部的 step DAG 不允许回边。** 策略层之间的环由 ADR-0021 表达。

需要“继续原目标”时：

1. 首选：不要退出原 step，用内部 mode 处理（如遇敌后继续前进）。
2. 次选：路由到另一个 step，由模块作者显式写成继续节点，例如 `S_resume_advance`。
3. 策略层：从 COMBAT 等策略回到 ADVANCE 时，按 ADR-0021 开始新 attempt，不是同一 DAG 回边。

这样 step DAG 始终无环，UI 仍然简单。

### 7. 与原子判定目录的关系

- 分支表里的 `when` 全部来自 ADR-0018 的原子判定目录。
- 允许 `and / or / not` 组合。
- 分支按声明顺序短路，保证确定性。
- `set mode` 是 step 内部唯一允许的状态写操作。

## 反例（明确禁止）

- 在 step 里写任意循环或任意 Python。
- 分支顺序不固定或随机。
- step 退出只返回 true/false，不返回原因。
- 遭遇战明明要回到原目标，却退出 step 再从外部绕回来。
- 同一策略内部的 step DAG 出现回边（策略层环见 ADR-0021）。

## 验收标准

1. 用分支表表达步坦协同：未到达时继续，间距过大时前方停，距离恢复后继续。
2. 行进中遇敌时，mode 切到 combat；敌清后回到 advance，DAG 不发生回边。
3. S_advance 因 AMBUSHED 退出时，能路由到 S_focus_fire 或 S_retreat。
4. 同一帧内分支按声明顺序短路，行为确定。
5. UI 能显示当前 step、mode、本帧命中的分支、退出原因。
