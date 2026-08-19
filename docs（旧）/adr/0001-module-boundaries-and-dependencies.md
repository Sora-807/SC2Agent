# ADR-0001 模块边界与依赖方向

- 状态：已确认草案
- 日期：待最终确认时补齐
- 范围：整个 sc2_agent_next 项目的顶层结构

## 背景

旧项目 `sc2_agent` 的核心问题不是某个模块写错，而是模块职责互相穿透：

- `game` 的数据模型混入了大量 Terran 专属字段。
- `driver` 同时承担读取状态、动作映射、flow 执行、agent 接入。
- `solver` 与 `planner` 两套引擎并存，且模拟器内部写死 Terran 默认开局。
- `flow` 在运行期反复解析文本，并把战术状态放在模块级全局变量中。
- `browser` 内重复实现了一部分游戏规则（如建筑可放置判断）。

推倒重来时，第一优先事项不是重写功能，而是先固定模块边界和依赖方向。

## 决定

1. 新项目独立于旧仓库，命名为 `sc2_agent_next`，新代码不 import 旧项目任何模块。
2. 采用以下模块划分：

| 模块 | 唯一职责 | 禁止事项 |
|---|---|---|
| `game` | 数据目录、类型注册表、GameState、事件定义 | 不依赖 burnysc2；不含种族机制逻辑 |
| `mechanics` | 三族生产/建造/科技机制插件 | 不定义 GameState；不包含 agent 决策 |
| `tactical_map` | 空间查询、地形/资源/区域/预留、live 与离线共用的地图模型 | 不直接发操作；不承载经济模拟 |
| `constraint` | 结构化约束校验 | 不自己计算地图距离和放置重叠；不模拟未来 |
| `planner` | 经济/生产确定性投影模拟器 | 不模拟战斗；不读取 SC2；不定义默认开局 |
| `flow` | FlowSpec 编辑模型、FlowIR、模块库、编译与校验 | 运行期不解析文本；不持有全局战术状态 |
| `runtime` | 协调所有运行时组件：分配、执行、状态、投影、历史、会话，见 ADR-0025 | 不 import driver；不包含业务规则；不直接依赖 agent |
| `driver` | 纯 SC2 适配器：实现 GamePort（read / apply / events / session） | 不 import flow/runtime/planner/agent；不维护第二份单位枚举 |
| `agent` | 意图路由、模块装配、快慢思考策略 | 不绕过 validate/simulate 提交；live 中不创建新模块 |
| `api` | HTTP/进程桥接 | 不包含游戏规则 |
| `browser` | 展示与交互 | 不实现游戏规则、不重复约束逻辑 |

3. 依赖方向严格单向：

```text
game
 ↑
tactical_map / mechanics
 ↑
constraint
 ↑
planner
 ↑
flow
 ↑
runtime
 ↑
agent / api / browser
```

其中 `driver` 只依赖 `game`，实现 `GamePort`；runtime 通过 GamePort 接入 driver，不直接依赖 driver。

4. 模块之间只依赖稳定接口：
   - 数据变化、参数变化、算法改进只允许改单点。
   - 跨模块语义只能增量添加，不允许隐式穿透或绕过接口。
   - 每个模块提供 contract tests，接口破坏必须被测试立即发现。

5. 空间层与规则判定边界：
   - `game` 数据目录声明哪些建筑/单位提供什么层、需要什么层、半径多少。
   - `driver` 只填充 SC2 可直接读取的动态原始层：creep、visibility 等；不计算 power/addon 等规则层。
   - `mechanics` 提供纯函数 `LayerComputer(buildings, catalog, dynamic_layers) -> map_layers`，计算 power、addon_attachment 等确定性层。
   - live 下由 runtime/world 调用 LayerComputer，将 driver 的 raw GameState 补成完整 GameState；离线地图规划/导入工具直接调用同一个 LayerComputer 填充 GameState.map_layers，保证在线/离线一致。
   - `tactical_map` 只查询 `GameState.map_layers`，不长期维护或推算这些层。
   - `constraint` 组合：先通过 game 数据/mechanics 得知“需要什么层”，再向 tactical_map 查询“该位置是否覆盖”。
   - 禁止 constraint 自己计算供能范围，禁止 driver 写业务规则，禁止 tactical_map 自己决定游戏规则，禁止独立服务模拟菌毯蔓延。

## 反例（明确禁止）

- 在 `driver` 里写 `if unit_type == "MARINE"` 这种业务判断。
- 在 `planner` 里写死 `COMMANDCENTER`、`ORBITALCOMMAND`、`SCV` 等种族单位名。
- 在 `game` 里 import `sc2` 或 burnysc2。
- 在 `browser` 里重写一份 `canPlace` / 约束规则。
- 在 `flow` 执行器里使用模块级可变全局变量保存战术状态。
- 为了图快，让 `agent` 直接改 planner 内部状态。

## 验收标准

1. 存在依赖检查测试：任何模块 import 了禁止依赖时测试失败。
2. 修改任意一个单位的 JSON 数据，不触发其他模块代码变更。
3. 修改 `planner` 收入公式，`game`、`tactical_map`、`constraint` 不需要改动。
4. 新项目从零搭建，不引用旧项目源码。
