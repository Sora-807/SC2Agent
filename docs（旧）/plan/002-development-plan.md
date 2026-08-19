# 开发计划

- 状态：v0.1
- 假定：单人开发 + 可随时真机验证；工作量单位为“人日”，不含等待审批与返工缓冲。

## 1. 总览

| 里程碑 | 内容 | 估算 | 关键风险 |
|---|---|---|---|
| M0 | 工程骨架、依赖红线 | 1–2 | 低 |
| M0.5 | driver spike | 3–5 | 高：burnysc2 时序/命令事实 |
| M1 | game 数据与状态 | 3–4 | 中：schema 一次定型 |
| M2 | mechanics Terran | 2–3 | 中：三族接口抽象 |
| M3 | tactical_map | 3–4 | 中：坐标与区域 |
| M4 | constraint | 2 | 低 |
| M5 | planner | 4–6 | 高：生产机制覆盖 |
| M6 | flow 编译与策略模型 | 6–9 | 高：编译规则闭环 |
| M6.5 | runtime | 6–9 | 高：分配器与状态恢复 |
| M7 | agent router | 3–5 | 中：LLM 输出约束 |
| M8 | driver 适配 | 3–5 | 高：依赖 spike 结论 |
| M9 | api + browser live | 5–8 | 中：前端范围 |
| 合计 | | 41–62 人日 | 建议排 3 个月并留 30% 缓冲 |

关键路径：`M0 → M0.5 → M1 → M5 → M6 → M6.5 → M8 → M9`。
M2/M3/M4 可与 M5 之前并行推进；M7 在 M6.5 facade 可用后开始。

## 2. 里程碑任务清单

### M0 工程骨架（1–2d）

- [ ] `pyproject.toml`、`pytest` 配置、`pip` 清华源说明
- [ ] 模块空目录 + 端口 stub
- [ ] 依赖检查测试（ADR-0001 验收 1）
- [ ] `docs/plan` 与本计划登记
- 退出：`pytest` 绿；任何非法 import 测试失败。

### M0.5 driver spike（3–5d）

- [ ] 最小 burnysc2 本地脚本（参考旧 driver，不 import）
- [ ] step/on_step 时序、Intent 生效窗口
- [ ] 动作命令持续性/重发/stop 实测
- [ ] orders 队列、auto-order 白名单、用户接管探测
- [ ] size 2/3/5 坐标 fixture（左下原点语义）
- [ ] creep/visibility 原始数据
- [ ] 输出 `docs/driver_spike.md` + CommandPolicy fixture
- 退出：CommandPolicy 覆盖 spec-003 V1 动作；spike 结论进入 M1 GameState 完整性清单。

### M1 game（3–4d）

- [ ] catalog schema + `terran.json`（稳定 ID + sc2_name + zh 字段）
- [ ] `GameCatalog` 只读注册表 + 查询接口
- [ ] `RawGameState` / `GameState` / `GridPos` / `Point2`
- [ ] `GameEvent` / `Intent` 稳定类型
- [ ] 坐标与 placement 语义（ADR-0027）
- [ ] GameState 完整性清单（哪些字段缺失 → INCOMPLETE_INITIAL_STATE）
- 退出：ADR-0002 验收 1–7。

### M2 mechanics Terran（2–3d）

- [ ] race 插件接口（build/train/research/expansion）
- [ ] `LayerComputer`（power / addon_attachment 等确定性层）
- [ ] Terran 实现：补给、挂件、建造前置、训练前置
- [ ] 旧 API 参考数据校验（枚举名不一致点：SHIELDWALL 等）
- 退出：Terran 插件全测试；P/Z 夹具接口锁定。

### M3 tactical_map（3–4d）

- [ ] L0/L1 地图模型（V1 用 fixture 地图）
- [ ] 区域、footprint、重叠查询
- [ ] spec-003 空间工具 + seq 缓存
- 退出：spec-003 空间工具验收；size 2/3/5 查询正确。

### M4 constraint（2d）

- [ ] 规则组合器：game/mechanics 得知需求，tactical_map 查覆盖
- [ ] 放置/资源/前置/供给校验
- 退出：ADR-0001 验收 3；与 mechanics 单点规则源。

### M5 planner（4–6d）

- [ ] 1s 步长确定性模拟内核
- [ ] production policies 子集：keep_workers / build_when / train_while_affordable / expand_when / ensure_production
- [ ] Projection 查询 API + 绝对游戏时间锚点
- [ ] 不完整状态报错
- 退出：ADR-0003 验收 1–6；手算 fixture。

### M6 flow 编译与策略模型（6–9d）

- [ ] spec-001/002/003 schema（JSON schema）
- [ ] compiler：槽位、原子目录、粒度、边、环出口
- [ ] FlowIR 生成 + canonical hash
- [ ] Step 分支表解释器（纯逻辑，不接 driver）
- [ ] V1 策略库：`bio_push`、`hold_ramp`（离线 authoring）
- 退出：spec-003 验收 1–6；spec-004 验收 1–2；同一 manifest 编译两次等价。

### M6.5 runtime（6–9d）

- [ ] UnitPool / TaskAllocator（spec-006 两阶段算法）
- [ ] Group 生命周期：create/refill/split/release
- [ ] Strategy 执行器 + 动作去重 + StepState/StrategyState
- [ ] FlowRuntimeStateStore + flow_events journal
- [ ] GameSessionController（ADR-0010）
- [ ] ProjectionMonitor（3s 滚动投影）
- [ ] FakeGamePort 确定性回放
- 退出：spec-006 十个 fixture；ADR-0013 生产实例恢复语义；ADR-0025 验收。

### M7 agent（3–5d）

- [ ] FastRuleTable 高频指令
- [ ] NoThinkLLMParser：单次、无工具、无推理链
- [ ] PatchValidator + assembly patch（spec-005）
- [ ] 澄清/拒绝路径
- 退出：ADR-0005 验收 1–7；错误 patch 零提交。

### M8 driver（3–5d）

- [ ] GamePort 实现：start/stop/submit_intents/events/read(offline)
- [ ] raw GameState 读取 + 类型映射
- [ ] CommandPolicy 应用 + Intent 去重
- [ ] enriched 由 runtime.world 补齐
- 退出：ADR-0026 验收 1–5。

### M9 api + browser live（5–8d）

- [ ] RuntimeFacade HTTP API
- [ ] live 驾驶舱：概览/地图/生产队列/Flow 状态图
- [ ] 对话快捷指令 + 只读展示
- [ ] 状态轮询 1s / 投影 3s / 快照 5s
- 退出：ADR-0022 验收；browser 无游戏规则。

## 3. 每周节奏建议

```text
W1  M0 + M0.5
W2  M1 + M2
W3  M3 + M4
W4  M5
W5  M6
W6  M6.5（分配器）
W7  M6.5（执行器/状态）+ M7 开工
W8  M7 + M8
W9  M8 + M9
W10 端到端联调与验收补课
```

该节奏偏理想，实际以 M0.5 和 M6.5 的返工为准，预留 W11–W12 缓冲。

## 4. 风险与对策

| 风险 | 触发信号 | 对策 |
|---|---|---|
| burnysc2 命令事实与 spec 动作目录不符 | M0.5 spike fixture 失败 | 冻结 CommandPolicy；未验证原子禁入策略库 |
| 分配算法实现复杂 | spec-006 fixture 过不去 | 先实现 Phase 1 保底路径，但接口保持两阶段 |
| LLM parser 不稳定 | patch 通过率低 | 收紧 schema；规则表优先；增加 Golden set |
| 状态恢复跨进程复杂 | M6.5 恢复测试失败 | V1 先同进程端口，进程拆分后移 |
| UI 范围过大 | M9 进度落后 | 先概览+地图，规划/复盘后置 |
| 旧模型回潮 | 代码出现 lane/do-until | ADR-0028 + 架构评审门禁 |

## 5. 依赖旧项目的执行纪律

- 只读参考，不在新代码中 import；
- 复制片段必须改成新项目类型与命名，并注明来源文件；
- M0.5/M8 评审时对照旧 driver 的已知坑：`SHIELDWALL`、addon 用母建筑、gas 建筑 target 是 geyser unit、`can_place_single` 失败要记失败位置；
- 旧 API 参考中造价/时间列不是权威，M2 必须用 burnysc2 `game_data` 实测校准。
