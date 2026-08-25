# Flow 契约（spec）

> 状态：v0.2，**已实施**（flow 引擎按此编码，tests/flow 是验收面）。
> ⚠️ 六份写于 2026-08-23，早于 ADR-0031/0032/0033：模板展开（imports/_lib.yaml）与
> 队列执行账本（uid+四值 status）未入 spec，以后两者为准；005 未覆盖编译期展开。
> 修订时同步 `docs/DOCS.md` 的状态行。

本目录把最近讨论收敛后的模型落成六份契约：

| 文件 | 内容 |
|---|---|
| `001-group-schema.md` | Group：兵权租约的配置、补兵、拆分、释放 |
| `002-strategy-schema.md` | Strategy：步骤图、group 槽位、参数、边、退出策略 |
| `003-step-schema-and-atom-catalog.md` | Step：每帧分支表；原子目录（条件/动作/筛选/空间工具） |
| `004-flowir-exitrecord-runtime-state.md` | FlowIR 编译产物、ExitRecord、运行时状态对象 |
| `005-flow-assembly-schema.md` | Flow 装配层：production_sequence、groups、bindings、patch 操作 |
| `006-allocation-algorithm.md` | Group 创建/补兵/拆分时的确定性分配算法 |

## 统一术语

| 术语 | 定义 |
|---|---|
| Group | 兵权租约。创建后长期持有，直到显式释放、单位死亡或用户接管 |
| Strategy | 行为策略。内部是一张允许有界环的步骤图 |
| Step | 策略内的步骤。每帧求值的扁平 if/elif/else 分支表 |
| 原子 | 条件原子、动作原子、筛选原子、空间工具。只增不改 |
| Flow 装配层 | group 创建/拆分/释放、group ↔ strategy 绑定、生产序列、信号 |
| FlowIR | Strategy + 绑定 + 参数编译后的运行时格式 |
| ExitRecord | step / strategy 退出时产生的结构化记录 |

## 已确认的核心规则

1. 兵权属于 Group，不属于 Strategy，也不属于 Step。
2. Strategy 只声明 `group_slots` 名字，不声明 required / min / max；未绑定槽位 = 空 group，对空 group 发动作 = no-op。
3. 兵是否够、何时等待、何时失败，全部由 Strategy 内部 Step 的 `group_count` 分支判断。
4. Strategy 不能创建、拆分、释放 Group；只能消费槽位。拆分/释放必须由 Flow 装配层显式执行。
5. 动作最小粒度是 `(group 槽位, 兵种类型)`；同一 Group 内同兵种不可拆成“2 个做 A、3 个做 B”。
6. Step 是扁平分支表：每帧第一个命中的分支执行后本帧结束；V1 不允许 if 嵌套。
7. `exit_step` 只在 Strategy 内部按 reason 边路由；`exit_strategy` 才结束 Strategy 并触发 group 处置。
8. Strategy 内部步骤图允许有界环；环必须有非环出口，并有 step 转移上限。
9. V1 装配层没有 signals；生产单位进入 free 池，由 Group 自动补兵，Strategy 首步等待 `group_count`。

## 与 docs/adr 的关系

- 本目录未确认前是**草案**。
- 确认后，旧 ADR 中与本目录冲突的部分（尤其是 0011/0018/0019/0020/0021 的编排模型）应标记废弃或改为指向本目录。
- 在本目录正式确认前，如与 ADR 冲突，以评审结论为准，不得在代码中同时实现两套语义。
