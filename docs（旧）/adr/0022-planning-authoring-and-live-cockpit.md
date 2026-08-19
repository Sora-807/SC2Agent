# ADR-0022 地图规划 + 生产规划 → Flow；live 驾驶舱只观察与投影

- 状态：已确认草案
- 替代：0007；页面信息架构与时间线见 ADR-0023
- 范围：browser 功能边界、两个规划工具与 Flow 的关系、live 展示

## 背景

之前 ADR-0007 把“实时地图”和“实时生产投影”当作主要视图，并把离线规划与 live 叠加。重新讨论后修正：

- 准确名称是：**地图规划**、**生产规划**。
- 两者主要是**离线协作画板**，用于玩家和 agent 商量方案。
- 地图规划 + 生产规划 + 战斗模块选择，共同产出 Flow。
- live 是另一个独立驾驶舱，只观察和预测，不与离线画板叠加。

## 决定

### 1. 两个规划工具是离线 Flow 生产工具

```text
地图规划 ─┐
          ├─→ FlowSpec（production_sequence + combat_overlays + triggers）
生产规划 ─┘
```

- 地图规划产出 `map_plan`：区域、建筑摆放区、预留区、标记点。
- 生产规划产出 `production_sequence`：生产模块与参数。
- 玩家/agent 再选择 combat overlays 和 trigger，装配成 FlowSpec。
- 生成后的 Flow 必须经过 validate + simulate，再进入版本树/待确认流程。

### 2. 地图规划画板

离线为主，live 不使用其编辑能力。

功能：

- 选择地图、红/蓝方视角；
- 从默认模板复制：地形引用、区域划分、开局信息；
- 画区域、预留区、建筑位置、标记点；
- 与 agent 对话协商，agent 可提交草稿，玩家审批；
- 产出的区域和预留区供 tactical_map 与 flow 摆放建筑时使用；
- 图层：底图/区域/用户规划/agent 草稿/版本对比；不默认叠加 live。

### 3. 生产规划画板

- 编辑生产阶段卡片：建造、训练、科技、扩张、农民/采气策略；
- Gantt + 资源曲线 + 约束亮/灰 + 终态快照；
- 以任意 GameState 起点做 simulate，支持离线开局模板和从对局快照导入；
- 输出 production_sequence 模块和参数；
- 保存为 plan，可与地图规划、combat overlays 一起生成 Flow。

### 4. 如何生成 Flow

```text
1. 玩家在地图规划中划定区域与预留
2. 玩家/agent 在生产规划中排 production_sequence
3. 玩家/agent 从模块库选择 combat_overlays，如 bio_push、hold_ramp
4. 设置 triggers，例如 m1.ready → m2.start
5. 系统组装 FlowSpec
6. validate + simulate
7. 生成/保存/提交
```

地图规划主要影响：

- 建筑摆放区域；
- 防守/集结/包抄的坐标与区域；
- selector 的 region/nearest_to 参数。

生产规划主要影响：

- 先开矿还是先兵营；
- 出什么兵、多少兵；
- 何时扩张、何时升级。

### 5. live 驾驶舱独立存在

live 视图与离线规划**不叠加**：

| 视图 | 内容 | 可编辑 |
|---|---|---|
| 实时地图 | 真实建筑/单位/敌方聚类/区域观察/look | 否 |
| 生产投影 | 当前 production flow + 最新 GameState 的未来预测 | 否 |
| 策略图状态 | 当前策略、attempt、step、退出原因、转移历史 | 否 |
| 对话/快捷指令 | 聊天、dispatch、模块插拔、改参数 | 是 |

- live 中需要修改方案时，通过对话/快捷指令做 FlowSpec patch，不直接拖动画板。
- 离线画板可以在另一个页面打开，但不会盖在 live 画面上。

### 6. 有环策略图不会增加生产投影难度

关键边界：

- planner 只投影 `production_sequence`，**不投影 combat 策略图**。
- combat 策略图可以有环，但它不进入 ProjectionSpec。
- production_sequence 在 V1 内保持无环：生产阶段按顺序执行，一次一个 active。
- 生产阶段的转移条件只允许经济/世界/生产信号，不允许依赖战斗结果。
- 战斗造成的损失通过“最新 GameState 重算投影”体现，而不是模拟战斗过程。

live 生产投影方式：

```text
每 3~5 秒：
  simulate(latest GameState, current production_sequence, horizon=60~120s)
  → 预测资源/建筑/单位/补给/预警
```

- 这是滚动投影，不尝试把 combat 策略环纳入经济模拟。
- 如果敌情导致生产计划改变，重新从新 GameState 与新 production_sequence 模拟。
- 离线生产规划是 DAG，模拟复杂度可控。

### 7. 浏览器页面结构

第一层级和选项卡以 ADR-0023 为准：

```text
实时游戏：概览 / 地图 / 生产队列 / Flow 状态图
复盘：历史对局 + 实时游戏同布局 + 时间线
规划：地图规划 / 生产规划 / Flow 规划
```

### 8. 与旧 ADR-0007 的差异

- 旧：live 地图 + live 生产投影是主视图，规划与 live 可叠加。
- 新：地图规划/生产规划是离线创作工具；live 是独立驾驶舱，不叠加。
- 旧：实时生产投影独立成为功能。
- 新：生产投影是 live 驾驶舱内的只读监视器，其模型来自当前 production_sequence。

## 反例（明确禁止）

- 把 live 数据直接画进离线规划画布并默认叠加。
- 在 live 驾驶舱里编辑地图区域或生产阶段。
- 用 combat 策略图来生成经济投影。
- 让生产阶段转移依赖战斗结果。
- 前端自己实现 validate/simulate。

## 验收标准

1. 玩家能在地图规划 + 生产规划 + 模块选择后生成合法 FlowSpec。
2. 生成的 FlowSpec 通过 validate 和 simulate。
3. live 实时地图与离线地图规划互不覆盖。
4. live 生产投影只基于当前 production_sequence 和最新 GameState，滚动更新。
5. combat 策略图含环时，生产投影仍能正常计算。
6. 浏览器中不存在游戏规则或模拟器的第二份实现。
