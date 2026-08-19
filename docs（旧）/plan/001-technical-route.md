# 技术路线

- 状态：v0.1
- 依据：ADR-0001~0014、ADR-0022~0028、`docs/spec/001~006`

## 1. 总原则

1. **新项目不 import 旧项目任何模块**（ADR-0001 红线）。
2. 旧项目只允许“人工参考”，禁止复制后在新代码里 `import sc2_agent`：
   - driver 参考：`C:\dev\project\sc2_agent\src\driver\sc2_driver.py / sc2_bridge.py / sc2_game.py`
   - 生产建造数据参考：`C:\dev\project\sc2_agent\docs\SC2生产建造API参考.md`
   - 经验教训参考：旧 `docs\经验之谈.md`（观察要填全、simulate 必须接实时状态、审批收窄）。
3. V1 范围：**Terran 完整实现；Protoss/Zerg 只用 JSON schema 和测试夹具锁接口**。
4. V1 战斗模型以 `docs/spec/001~006` 为唯一实现依据；旧 GuardScript/泳道/两层图不实现。
5. 每个里程碑以 contract test + import 检查 + 脚本化场景为退出门禁。

## 2. 环境与依赖基线

- Python 3.12（burnysc2 兼容性在 M0.5 spike 首日确认；如 v7 仅支持更早版本，以 spike 结论锁定）。
- pip 使用清华源：

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <package>
```

或项目级配置 `pip.ini` / `pyproject` 内 `index-url`。

- 核心依赖候选：
  - `burnysc2`（唯一允许 import SC2 的包，仅 driver 依赖）
  - `pydantic` 或 dataclass + jsonschema（schema 校验；最终按 M1 决定）
  - `pytest`、`pytest-asyncio`
  - `httpx` / `fastapi`（M9 才引入，不在 M0 强依赖）
- 仓库布局沿用 ADR-0001 模块名：`game / mechanics / tactical_map / constraint / planner / flow / runtime / driver / agent / api / browser`。

## 3. 里程碑路线

```text
M0   工程骨架与依赖红线
M0.5 driver spike（burnysc2 实测，flow 执行器的硬前置）
M1   game：数据目录 + 注册表 + GameState + 事件 + 坐标
M2   mechanics：Terran 生产/建造/科技机制
M3   tactical_map：坐标、区域、空间查询
M4   constraint：当前可做性校验
M5   planner：确定性经济/生产投影
M6   flow：spec 001~005 的编译、schema、策略/步骤模型
M6.5 runtime：Group 分配、Strategy 执行、状态 store、事件、会话
M7   agent：no_think + intent router + patch validator
M8   driver：GamePort 薄适配 + CommandPolicy
M9   api + browser：live 驾驶舱，UI 最后实现
```

关键依赖：

```text
M0 → M0.5（spike 不依赖 game 正式模型，但坐标结论回流 M1）
M0 → M1 → M2/M3 → M4 → M5 → M6 → M6.5 → M7/M8 → M9
M6 可在 M1 类型 ID 冻结后开始，但 M6.5 依赖 M6 + M3 空间工具
M8 依赖 M0.5 CommandPolicy + M1 GameState
M7 依赖 M6（schema/patch）+ M6.5 RuntimeFacade
```

## 4. 里程碑细化

### M0 工程骨架与依赖红线

- 目标：新目录可运行测试，依赖方向被测试锁死。
- 输出：
  - `pyproject.toml`、venv 说明、清华源配置；
  - `tests/architecture/test_imports.py`：禁止 game→burnysc2、driver→flow/runtime/agent、flow→runtime、runtime→driver；
  - 空模块骨架 + 端口 stub。
- 退出：`pytest` 通过；CI 本地可跑。

### M0.5 driver spike

- 目标：实测 burnysc2 v7 的步进、命令、坐标、orders 事实；不写正式 driver。
- 参考旧文件：`sc2_driver.py`（BotAI 生命周期、build_full_grid、probe 对齐）、`sc2_bridge.py`（枚举映射、orders 解析）、`sc2_game.py`（build/train/ability 调用、TL→世界中心）。
- 必须回答：
  1. `step()` 与 `on_step` 的时序；Intent 下一 step 生效语义；
  2. `move / attack_move / hold_position / follow / patrol / stop` 的命令持续性与重发后果 → 输出 **CommandPolicy 表**；
  3. orders 全队列、auto-order 白名单、用户接管可识别性；
  4. size 2/3/5 建筑坐标转换 fixture（参考旧 probe 的 `tlbr_to_center` 经验，但新项目用左下原点语义）；
  5. creep/visibility 原始数据形态；
  6. Python 版本 / burnysc2 版本兼容性。
- 输出：`docs/driver_spike.md` + CommandPolicy fixture + 坐标 fixture。
- 退出：Fixture 全绿；CommandPolicy 覆盖 spec-003 动作目录里每个 V1 动作。

### M1 game

- 目标：数据目录、稳定类型 ID、只读注册表、GameState、GameEvent/Intent、坐标约定。
- 输入：
  - ADR-0002、ADR-0027；
  - 旧 `SC2生产建造API参考.md` 作为 `sc2_name` 映射与机制字段来源（造价/时间列只作参考，最终以 burnysc2 `game_data` 实测为准）。
- 输出：`game/catalog/terran.json` + schema；`GameCatalog`；`GameState/RawGameState`；坐标类型。
- 退出：ADR-0002 验收 1~7；import 检查零 burnysc2。

### M2 mechanics Terran

- 目标：纯函数生产/建造/科技机制：`LayerComputer`、前置/挂件/电源/供给、建造时间与费用。
- 输入：旧 API 参考的 AbilityId / TRAIN_INFO / RESEARCH_INFO 约定；ADR-0001 边界。
- 退出：三族接口可用 Terran 实现全通过；无种族硬编码泄漏进 planner/game。

### M3 tactical_map

- 目标：左下原点坐标、区域、footprint 查询、空间工具（`region_center/group_center/distance/nearest_units/cluster_centers`）。
- 退出：spec-003 空间工具 seq 缓存与空 group 语义；size 2/3/5 fixture。

### M4 constraint

- 目标：当前帧“能不能做”：资源/前置/供给/放置/重叠。
- 退出：与 M2 同一规则源；不计算未来；不依赖 SC2。

### M5 planner

- 目标：确定性经济/生产投影，1 秒步长，Projection 查询 API。
- 输入：ADR-0003；production policies（沿用 ADR-0004，待独立 schema）。
- 退出：手算固定场景一致；缺字段报 `INCOMPLETE_INITIAL_STATE`；三族插件可替换。

### M6 flow 编译与策略模型

- 目标：实现 spec-001~005 的 schema、compiler、FlowIR。
- 输出：
  - Group/Strategy/Step manifest schema；
  - compiler：槽位校验、原子目录、`(slot,type)` 粒度、边路由、环出口、`exit_step` 必须可匹配；
  - FlowIR canonical hash。
- 退出：spec-003 验收 1~6；spec-004 验收 1~2；同一输入编译两次 hash 一致。

### M6.5 runtime

- 目标：TaskAllocator（spec-006）、Group 生命周期、Strategy 执行器、StateStore、flow_events、GameSessionController、ProjectionMonitor。
- 退出：
  - spec-006 十个 fixture 全绿；
  - sticky lease 验收；
  - 状态恢复：骨架恢复 + 派生重算；
  - FakeGamePort 确定性回放。

### M7 agent

- 目标：no_think router：`FastRuleTable + NoThinkLLMParser + PatchValidator`；patch 操作面按 spec-005。
- 退出：ADR-0005 验收 1~7；不支持指令明确拒绝；所有成功 patch 产出合法 assembly。

### M8 driver

- 目标：GamePort 薄适配；应用 CommandPolicy；raw→enriched 两段。
- 退出：ADR-0026 验收 1~5；driver 不含业务规则；下一 step 生效语义。

### M9 api + browser

- 目标：live 驾驶舱（概览/地图/生产队列/Flow 状态图），只读展示 + 对话快捷指令；离线规划/复盘放后续。
- 退出：ADR-0022/0023 的 V1 子集；browser 无游戏规则实现。

## 5. 进程与部署路线

- 端口契约保持 ADR-0025/0026：driver 实现 GamePort，runtime 通过端口接入。
- **M0.5 spike 决定**：V1 先同进程（Burnysc2 主循环内直接调用端口）还是直接 IPC。无论哪种，runtime/driver 之间只允许通过 `submit_intents / on_game_state / events` 交互，不允许跨层 import。
- M9 前不做语音、不做大规模复盘优化。

## 6. 数据与知识复用清单

| 旧项目内容 | 复用方式 | 新项目落点 |
|---|---|---|
| `sc2_driver.py` | 参考 BotAI 生命周期、probe/diag 方法、TagMapper 思路 | M0.5 / M8 |
| `sc2_bridge.py` | 参考枚举映射与 orders 解析字段 | M1 catalog / M8 |
| `sc2_game.py` | 参考 build/train/research/ability 调用与 can_place 兜底 | M8 |
| `SC2生产建造API参考.md` | 提取三族生产/建筑/科技表 → `sc2_name`、ability、机制 | M1/M2 |
| `经验之谈.md` | 观察填全、simulate 接实时、审批收窄 | M5/M7/M9 |

旧项目其他模块（flow 文本 DSL、solver、sc2agent 工具）**不参考**，避免把旧架构惯性带进来。
