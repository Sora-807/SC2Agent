# ADR-0003 planner：经济/生产确定性投影模拟器

- 状态：已确认草案
- 范围：`planner` 模块，替代旧 `planner` + `solver` 两套引擎

## 背景

旧项目的 `planner` 与 `solver` 是两套并存引擎：

- 两套输入：分析式 `Step` 与面板式 `SeqItem`。
- 模拟从内部写死的默认开局开始（50 矿、12 SCV、1 CC）。
- 单位、基地、补给等种族对象被硬编码。
- `simulate` 的结果只是资源曲线和卡片可造性，无法回答“何时卡人口”“何时能负担某单位”这类查询。
- 不区分经济可模拟与战斗不可模拟，agent 可能拿到看似正确但实际错误的预测。

## 决定

### 1. 定位

`planner` 是**经济与生产确定性投影模拟器**：

- 模拟资源、建筑、单位、科技、补给、产线占用随时间的变化。
- 不模拟战斗结果、单位交换、敌人行为。
- 敌情只作为外部触发输入，不进入确定性投影。

### 2. 输入

```text
Projection = simulate(initial_state, projection_spec, mechanics, horizon)
```

- `initial_state` 必须是完整的 `game.GameState`。
- 不允许 planner 提供隐藏默认开局。
- 离线场景使用“初始状态构造器”显式生成完整 GameState，再传给 planner。
- 任意时刻的 live GameState 都可以作为起点，例如游戏进行到 8:32 抓取的完整快照。
- 关键字段缺失时 planner 直接报错 `INCOMPLETE_INITIAL_STATE`，不猜测。
- `projection_spec` 来自 FlowSpec 编译（见 ADR-0004），或离线手工构造的测试计划。
- `mechanics` 是三族生产机制插件。

### 3. 推进方式

V1 使用固定 1 秒仿真步长：

```text
t = 0：加载完整 GameState
每 1 秒：
    推进收入、生产、建造、科技进度
    触发到期事件
    重新评估所有 projection policy 的条件
    满足资源/前置/容量约束时启动操作
    记录 state(t) 与 events(t)
直到 horizon 或没有可推进操作
```

固定 1 秒是 V1 的实现选择；未来可以在保持对外 1 秒采样的前提下改为内部事件跳跃优化。

### 4. 输出：可查询投影

`Projection` 不是一条曲线，而是一个可查询对象：

```text
P.state(t)                       # 绝对游戏时间 t 秒处的预测状态
P.curve(...)                     # 给 UI 画图
P.events()                       # 预测事件序列
P.first("supply_blocked", after=t0)
P.when_affordable(type, after=t0)
P.warnings(after=t0, horizon=60)
P.duration(condition, after=t0)
```

前端、agent、警报系统消费同一份 `Projection`，不允许各自另算一套。

### 4.1 时间锚点约定

`Projection` 对外统一使用**绝对游戏时间**（秒），原点来自初始 `GameState.time`：

- `P.state(t)` 中的 `t` 是绝对游戏时间。例如初始状态在 8:32（512 秒），则 `P.state(572)` 表示 60 秒后的预测状态。
- 所有 `first / when_affordable / warnings / duration` 类查询都必须显式给出 `after` 锚点；锚点缺省时取初始 `GameState.time`，且 API 文档必须写明，不允许隐式换基准。
- 如需“从某个预测状态继续问”，取该状态的时间 `t` 作为锚点：

```text
snap = P.state(t)
P.when_affordable(type, after=snap.time)
```

内部仿真可以用相对偏移秒数实现，但对外接口、日志、事件、UI 只暴露绝对游戏时间。

### 5. 与约束器/战术地图的关系

- `constraint` 回答“当前这一刻能不能做”。
- `planner` 回答“按计划执行，未来会变成什么样”。
- `tactical_map` 提供空间信息；planner 不自己计算地图距离或建筑重叠。
- planner 内部启动操作前，使用与 constraint 相同的规则来源判断可行性，保证二者一致。

### 6. 可投影边界

只有经济/生产类操作进入 projection：

| 可投影 | 不可投影 |
|---|---|
| build / train / upgrade / expand | move / patrol / attack / load / 微操 |
| 补农民、采气分配 | 依赖敌情的分支结果 |
| 固定顺序、parallel、when 资源/补给/建筑/时间条件 | 战斗胜负与单位交换 |
| 有界 loop / repeat | 敌人行为 |

不可投影部分在 Projection 中显式标注，不得静默忽略或假装已模拟。

combat 模块的策略图即使含有环，也不进入 `ProjectionSpec`。planner 只投影 `production_sequence`；production 阶段转移条件不得依赖战斗结果。战斗造成的兵力损失通过“用最新 GameState 重新滚动模拟”体现。

## 反例（明确禁止）

- `simulate()` 无参调用并使用内部默认开局。
- 在 planner 里写死 Terran 单位名或建筑名。
- 用随机数模拟战斗结果。
- GameState 缺字段时给默认值继续算。
- 给 UI 一套预测、给 agent 另一套预测。

## 验收标准

1. 相同输入与相同 mechanics 下，多次运行结果完全一致。
2. 用任意一帧完整 live GameState 作为起点可正常模拟。
3. 不完整初始状态报错，且错误明确指出缺失字段。
4. 三族 mechanics 插件可替换，planner 本身不含任何种族硬编码。
5. 预测时间点与手算的小规模固定场景一致（例如：只补补给站与农民）。
6. `when_affordable(type, after=t)` 返回的绝对时间不早于 `t`；`after` 缺省时明确等于初始 `GameState.time`。
