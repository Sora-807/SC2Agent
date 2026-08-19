# ADR-0002 game：数据目录、类型注册表、GameState 与事件

- 状态：已确认草案
- 范围：`game` 模块，重构的地基层

## 背景

旧 `game` 的模型同时承担了三件事：Terran 数据表、通用状态模型、种族规则。具体问题：

- `UnitView` 上直接长着 Terran 字段：`has_techlab`、`has_reactor`。
- `BuildingDefinition` 用一张大表混合三族特性：`addon_parent`、`requires_power`、`morph_from`。
- 下游通过 `type_name == "MARINE"` 这类魔法字符串做业务判断。
- 数据写在 Python 代码里，数值变动需要改代码。
- `GameState` 与 `MapGrid` 互相耦合，状态是否完整没有明确概念。

## 决定

### 1. 稳定类型 ID + 自动注册表

代码逻辑不依赖 SC2 枚举名，也不使用 `"MARINE"` 这类字符串作为判断依据。每个类型使用稳定 ID：

```text
terran/marine
terran/scv
protoss/zealot
zerg/zergling
```

`sc2_name` 仅是数据目录中的一个普通字段，供 driver 映射到 burnysc2 枚举，不得进入 game 核心查询逻辑。

### 2. 数据目录与类型注册表

- 数据文件：`game/catalog/terran.json`、`protoss.json`、`zerg.json`。
- 加载后生成只读 `GameCatalog`。
- 查询通过角色和能力，而不是具体类型名：

```text
reg.units.where(role="worker")
reg.units.where(kind="army")
reg.units.where(capability="build")
reg.units.where(capability="gather_gas")
reg.unit("terran/marine")
```

- 新增、删除一个单位只修改对应 JSON；数值调整只修改对应 JSON。
- 数据目录带 schema 与版本号，加载失败必须报错，不允许静默使用旧数据。
- 每个类型带中文本地化字段：`display_name_zh`、`description_zh`、`aliases_zh`。展示层与 agent 观察层通过这些字段取得中文文本。
- FlowSpec、FlowIR、planner、constraint 内部只使用稳定类型 ID；中文别名只用于输入解析与展示，不进入运行时语义。
- 中文本地化字段属于数据目录，修改名称/别名不触发任何模块代码改动。
- 中文别名解析冲突策略：一个别名命中多个类型或一个类型有歧义时，不硬猜，返回澄清请求；该规则由 router 层执行。

### 3. 通用状态模型

`GameState` 表示任意时刻的一帧完整游戏快照，其设计原则：

- 状态中不出现 Terran 专属固定字段。
- 单位实例只保存实例数据，类型属性通过 `type_ref` 查注册表获得：

```text
UnitInstance(
    id,
    type_ref,       # 指向注册表
    owner,
    pos,
    hp,
    progress,
    production,     # 生产状态：队列、挂件等按 race 机制解释
    flags,          # 少量结构化运行时标记
)
```

- `GameState` 是 planner 的唯一初始输入。字段缺失时 planner 必须报“初始状态不完整”，不得猜测。
- 地图数据与状态解耦：`GameState` 可包含地图快照或地图引用，但核心状态模型不依赖地图实现细节。
- `GameState.map_layers` 统一承载地图二维属性层，如 `creep`、`power`、`addon_attachment`、`visibility`。driver 只填动态原始层；runtime.world 或离线导入工具调用 `mechanics.LayerComputer` 补齐确定性层后，才把完整 GameState 交给核心消费者。

### 4. 种族机制不放在 game

三族生产机制差异（SCV 建造、折跃、幼虫/虫卵等）放在 `mechanics` 模块。`game` 只回答：

- 这个类型是什么。
- 它属于什么角色、有什么能力。
- 它需要什么前置。
- 它的成本、补给、时间等静态数据是什么。

`game` 不回答“Terran 怎么造建筑”这种机制问题。

### 5. 事件与意图

- `game` 定义 `GameEvent` 和 `Intent` 的稳定类型。
- `Intent` 是上层表达“想做什么”的语义对象，如训练、建造、移动、攻击、采集。
- `Intent` 不绑定 SC2 API，具体转换由 driver 完成。

### 6. 坐标约定

- 项目内部统一采用**左下角为 (0,0)，x 向右递增，y 向上递增**的坐标。
- 建筑、区域、预留区使用整数格点 `GridPos`；单位位置使用浮点世界坐标 `Point2`，不做取整存储。
- 建筑 placement 使用 TL+BR+size 双角点表示；细节见 ADR-0027。
- `game`、`tactical_map`、`constraint`、`planner`、`browser` 全部遵循该约定。
- SC2 世界坐标、图像像素坐标、旧项目坐标等转换只允许发生在 driver 或 browser 的薄适配层，不得渗透进核心模块。

## 边界

- `game` 不 import burnysc2。
- `game` 不包含 agent 决策。
- `game` 不负责未来模拟。
- `game` 不负责空间查询。

## 反例（明确禁止）

- 下游写 `unit.type_name == "SCV"` 来找农民。
- 在 `GameState` 上新增 `has_techlab` 这类固定字段。
- 在 Python 代码里维护第二份单位数据表。
- 单位数据 JSON 缺失字段时给默认值并继续运行。
- planner 从 `game` 拿到不完整状态后自行补默认开局。

## 验收标准

1. 三族数据目录全部能通过 schema 校验并加载。
2. 修改一个单位造价：只改 JSON，所有测试通过。
3. 增加/删除一个测试单位：不修改任何下游代码，下游通过注册表查询得到正确结果。
4. `game` 模块的 import 检查证明零 burnysc2 依赖。
5. 不存在下游按具体单位名分支的业务逻辑（数据映射和测试夹具除外）。
6. 每个类型都能取得中文名称、中文描述和中文别名；修改别名只改数据目录。
7. 核心模块中出现的坐标均遵循左下角原点约定，坐标转换代码只存在于 driver/browser 适配层。
