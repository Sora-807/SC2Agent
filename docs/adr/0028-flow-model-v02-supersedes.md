# ADR-0028 Flow 语义模型 v0.2：以 spec 为准，废弃旧编排模型

- 状态：已确认草案
- 日期：待最终确认时补齐
- 替代：ADR-0011 / 0012 / 0017 / 0018 / 0019 / 0020 / 0021 中与本文冲突的部分
- 范围：combat / 行为策略的最终语义模型；Flow 装配层

## 背景

讨论确认了新的 Flow 语义模型：

- 兵权属于 **Group**，不属于 Strategy / Step；
- **Strategy** 内部是允许有界环的步骤图；
- **Step** 是每帧求值的扁平 if/elif/else 分支表；
- 外层装配层没有行为图，只有 production_sequence + groups + bindings；
- 生产与战斗的衔接不靠 signals，而靠“Group 自动补兵 + Strategy 首步等待 group_count”。

旧 ADR 0018/0019/0020/0021 的 GuardScript、泳道、策略层+尝试层、do/until 编排等术语已不再使用，继续保留会导致实现按错误模型编码。

## 决定

1. **有效模型文档**为 `docs/spec/` 下六份契约：
   - `001-group-schema.md`
   - `002-strategy-schema.md`
   - `003-step-schema-and-atom-catalog.md`
   - `004-flowir-exitrecord-runtime-state.md`
   - `005-flow-assembly-schema.md`
   - `006-allocation-algorithm.md`
2. 旧 ADR 处理：

| ADR | 处理 |
|---|---|
| 0011 | **仅保留思想**：选择/执行分离、pool_key/share、sticky lease、user 优先；具体原子目录与分配算法以 spec-003 / spec-006 为准 |
| 0012 | **生产模块示例仍可参考**；combat 部分（activation: m1.ready、combat_overlays）作废，以 spec-002 / 005 为准 |
| 0013 | **生产模块实例状态与热切换继续有效**；combat 状态以 spec-002 / 004 为准 |
| 0014 | **生产序列编辑矩阵继续有效**；combat 编辑语义以 spec-005 为准 |
| 0017 | **UI 概念由后续 browser spec 重写**；旧 lane/attempt 术语不再作为实现依据 |
| 0018 | **废弃**。原子判定目录以 spec-003 为准 |
| 0019 | **废弃**。步骤图模型以 spec-002 为准 |
| 0020 | **退出原因思想保留**，但目录与语法以 spec-003 / 004 为准；其余作废 |
| 0021 | **if 分支表与有界环思想保留**，但“策略层+尝试层”两层模型不再使用；单层 Strategy 步骤图以 spec-002 为准 |

3. 核心规则：

- Group 是兵权租约，只由装配层创建/补兵/拆分/释放；
- Strategy 只声明 group_slots，不声明 required/min/max；
- 未绑定槽位 = 空 group；空 group 动作 no-op；
- 兵够不够全部由 step 内 `group_count` 判断；
- 动作粒度 = `(group 槽位, 兵种类型)`；
- `exit_step` 只内部路由，`exit_strategy` 才结束 Strategy；
- Strategy 内步骤图允许有界环，必须有非环出口与转移上限；
- V1 装配层**没有 signals**，生产单位经 free 池由 Group 自动补兵接收。

## 边界

- production 模块的 policies 模型继续沿用 ADR-0003 / 0004，直到独立 production manifest schema 落地。
- ADR-0001 模块边界、0002 game 数据、0006 单位所有权、0008~0010、0022~0027 继续有效；其中 0024 的恢复权威源按下述修正执行。

## 修正：历史恢复权威源

- **live 恢复权威**：`FlowRuntimeStateStore + flow_events 重放 + 最新 GameState 重算派生进度`；
- **历史回放权威**：`runtime snapshot + flow_events`；
- snapshot 不承担 live 崩溃恢复的权威职责；ADR-0024 后续按此修订。

## 反例（明确禁止）

- 继续按 0018 泳道 / 0019 do-until / 0021 两层图实现 combat 模块。
- 在 Strategy manifest 中写 `m1.ready` 或任何 Flow 实例 ID。
- 在装配层加入 signals / combat_overlays / triggers 作为 V1 行为机制。
- 同一份代码同时实现 spec 模型与旧编排模型。
- Strategy 内部创建、拆分、释放 Group。

## 验收标准

1. 所有 combat 模块 manifest 都通过 spec-001~006 的 schema 校验。
2. 编译器中不存在 GuardScript / lane / do-until / StrategyGraph+attempt 两层模型代码路径。
3. 两船兵前压场景完全由 production_sequence + G1 自动补兵 + bio_push 首步等待表达，不含 signals。
4. 旧 ADR 冲突文档已加顶部“已被 ADR-0028 替代”横幅或移入 archive。
5. 六份 spec 与 ADR-0028 的术语在代码与测试中一致。
