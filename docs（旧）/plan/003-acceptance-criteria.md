# 验收标准

- 状态：v0.1
- 用途：每个里程碑的退出条件，以及项目级最终验收。
- 规则：任何一条标 `[BLOCK]` 的不通过，不得进入下一里程碑；标 `[SOFT]` 的可记录为技术债并在下个迭代修复。

## 1. 全局红线（贯穿所有里程碑）

| ID | 标准 | 级别 |
|---|---|---|
| G1 | 新项目不 import 旧项目任何模块；存在自动化检查 | BLOCK |
| G2 | `game` 不 import burnysc2；`driver` 不 import flow/runtime/planner/agent；`flow` 不 import runtime；`runtime` 不 import driver | BLOCK |
| G3 | live 中不能创建/编辑 Strategy/模块；只能 group 操作 + attach/switch/detach + 改参数 | BLOCK |
| G4 | 任何 Flow 提交必须 validate + compile +（含生产变更时）simulate | BLOCK |
| G5 | 用户操作优先于 flow；user_leased 单位不可被 Group 分配 | BLOCK |
| G6 | 配置错误可降级告警，不崩溃、不无限重试 | BLOCK |
| G7 | 相同输入、相同 GameState/事件序列下，分配、分支、Intent、状态迁移完全确定 | BLOCK |
| G8 | 旧观察不得作为当前行动依据；ObservationPacket 替换而非追加 | BLOCK |
| G9 | 不模拟战斗结果；combat 策略不进 ProjectionSpec | BLOCK |
| G10 | Strategy 内部不能 create/split/release group；动作粒度必须是 `(slot, type)` | BLOCK |

## 2. 里程碑验收

### M0 工程骨架

| ID | 标准 |
|---|---|
| M0-1 | `pytest` 在干净环境可运行 |
| M0-2 | 依赖检查测试覆盖 G1/G2 所有方向 |
| M0-3 | 新增一个非法 import 能使对应测试失败 |

### M0.5 driver spike

| ID | 标准 |
|---|---|
| M0.5-1 | `docs/driver_spike.md` 覆盖 step 时序、命令行为、orders、坐标、creep/visibility |
| M0.5-2 | CommandPolicy 覆盖 spec-003 全部 V1 动作原子，每条有实测结论 |
| M0.5-3 | size 2/3/5 坐标 fixture 与 SC2 `can_place` 实测一致 |
| M0.5-4 | auto-order 白名单初版列出：建完回采、自动索敌/追击、集结跟随、自动修理 |
| M0.5-5 | 用户接管识别结论明确：可识别到什么程度、fallback 是什么 |
| M0.5-6 | Python/burnysc2 版本组合确定并写入 `pyproject.toml` |

### M1 game

| ID | 标准 |
|---|---|
| M1-1 | terran.json 通过 schema 校验；修改造价只改 JSON，全部测试通过 |
| M1-2 | 增加/删除测试单位不需要改下游代码 |
| M1-3 | `reg.units.where(role/capability/kind)` 查询正确 |
| M1-4 | 每个类型有 display_name_zh / description_zh / aliases_zh |
| M1-5 | `GameState` 无种族专属字段；`RawGameState` 与 enriched `GameState` 类型可区分 |
| M1-6 | 坐标转换代码只存在于 driver/browser 薄适配层；核心模块全左下原点 |
| M1-7 | GameState 完整性清单可执行：缺字段报 `INCOMPLETE_INITIAL_STATE` 且指出字段名 |

### M2 mechanics

| ID | 标准 |
|---|---|
| M2-1 | Terran build/train/research 机制纯函数测试通过 |
| M2-2 | `LayerComputer` 在线/离线同结果 |
| M2-3 | P/Z 插件接口用夹具锁定，替换插件不破坏 planner |
| M2-4 | 旧 API 参考中的枚举坑（SHIELDWALL 等）在 catalog 中修正并有回归测试 |

### M3 tactical_map

| ID | 标准 |
|---|---|
| M3-1 | region/footprint/overlap 查询通过 size 2/3/5 fixture |
| M3-2 | spec-003 空间工具全部实现，seq 缓存语义正确 |
| M3-3 | 空 group 查询结果符合 spec-003（count=0，中心相关谓词 false） |
| M3-4 | 同一 GameState seq 内重复查询结果一致，新 seq 后重算 |

### M4 constraint

| ID | 标准 |
|---|---|
| M4-1 | 资源/前置/供给/放置/重叠校验与 mechanics/tactical_map 同源 |
| M4-2 | constraint 不含未来模拟、不含 SC2 依赖 |
| M4-3 | 非法放置返回结构化错误（含原因与位置），可被 browser/agent 消费 |

### M5 planner

| ID | 标准 |
|---|---|
| M5-1 | 相同输入重复模拟结果完全一致 |
| M5-2 | 任意完整 live GameState 可作起点 |
| M5-3 | 缺字段报 `INCOMPLETE_INITIAL_STATE` 且指出字段 |
| M5-4 | planner 不含种族硬编码；Terran/P/Z mechanics 可替换 |
| M5-5 | 固定小场景与手算一致（补给站 + SCV、速开二矿、两船兵生产） |
| M5-6 | `when_affordable(after=t)` 不早于 t；after 缺省 = 初始 GameState.time |
| M5-7 | Projection 是唯一预测源：UI/agent/alerts 不另算 |

### M6 flow 编译与策略模型

| ID | 标准 |
|---|---|
| M6-1 | spec-001~005 的 schema 全部可校验，错误信息指出字段 |
| M6-2 | 未知槽位/未知原子/`(slot,type)` 粒度违规/无匹配 exit_step 边 → 编译失败 |
| M6-3 | 环无出口、无转移上限 → 编译失败 |
| M6-4 | 同一 manifest + 绑定 + 参数编译两次 ir_hash 相同 |
| M6-5 | 空 group：count=0、动作 no-op、中心相关谓词 false |
| M6-6 | 每帧只命中第一个分支；无命中且无 else 不发新 Intent |
| M6-7 | 动作去重：相同 `(slot,type,action,params)` 不重发 |
| M6-8 | V1 策略库 `bio_push`、`hold_ramp` 通过离线脚本化场景测试 |

### M6.5 runtime

| ID | 标准 |
|---|---|
| M6.5-1 | spec-006 十个 fixture 全绿 |
| M6.5-2 | 已 lease 单位不参与重分配；用户接管单位不可分配 |
| M6.5-3 | Group 状态机（forming/ready/active/paused/releasing/released）全转移测试通过 |
| M6.5-4 | split 原子性：失败源 Group 逐单位不变 |
| M6.5-5 | 自动补兵 min→target 滞回，不死一个补一个 |
| M6.5-6 | 恢复：骨架状态恢复，unit_counts/center 由最新 GameState 重算 |
| M6.5-7 | FakeGamePort 回放同一脚本，分配与 Intent 逐帧一致 |
| M6.5-8 | 会话状态机与重复 start/stop 防护（ADR-0010）测试通过 |

### M7 agent

| ID | 标准 |
|---|---|
| M7-1 | FastRuleTable 覆盖的指令不经 LLM |
| M7-2 | NoThinkLLMParser 无推理链、无工具、无自由文本，单次输出 patch |
| M7-3 | 复合句/改口句 Golden set 路由正确（ADR-0012 第 6/7 节改写版） |
| M7-4 | 不合法 patch 拒绝且不部分生效 |
| M7-5 | 所有成功路由产出合法 assembly，并自动 validate/compile |
| M7-6 | 不支持指令明确拒绝；别名歧义返回澄清请求 |

### M8 driver

| ID | 标准 |
|---|---|
| M8-1 | 10 个 FakeSC2Loop step 推送 10 个 GameState |
| M8-2 | Intent 下一 step 生效，当前 step 不生效 |
| M8-3 | 游戏时间跨整秒生成 ObservationPacket；未跨秒不生成 |
| M8-4 | CommandPolicy 全动作实现且测试覆盖 |
| M8-5 | driver 无 power/addon 业务规则 |
| M8-6 | raw→enriched 两段：核心消费者只能拿到 enriched |

### M9 api + browser

| ID | 标准 |
|---|---|
| M9-1 | live 驾驶舱四选项卡可用：概览/地图/生产队列/Flow 状态图 |
| M9-2 | 地图叠加：建筑/进度/单位集群/flow group/当前战术/未来 30s 确定建筑 |
| M9-3 | 生产队列与曲线来自 planner，无第二份模拟 |
| M9-4 | Flow 状态图显示当前 strategy、step、branch、exit reason |
| M9-5 | 对话可执行 group/strategy 快捷指令，非编辑模式 |
| M9-6 | browser 源码不存在 canPlace/validate/simulate 实现 |

## 3. 端到端验收场景

| ID | 场景 | 通过标准 |
|---|---|---|
| E2E-1 | 离线：速开二矿→两船兵生产 | planner 给出 full 投影；最终两船兵数量达标；无 move/attack Intent |
| E2E-2 | 离线：bio_push 脚本化 GameState | formup 等待→FORMED→advance；遇敌 AMBUSHED→combat；清完回 advance；UNITS_LOST→retreat |
| E2E-3 | 离线：G1 补兵 | 低于 min 触发，补到 target；不抢 user_leased/其他 Group |
| E2E-4 | live 冒烟：提交 assembly 后启动 | 生产运行，G1 自动集结，满员后自动推进 |
| E2E-5 | live 改口：“先别出门，兵留家里” | detach/switch 后兵权仍在 G1，单位不丢、不重分 |
| E2E-6 | live 用户接管 | 被接管单位立即离开 G1；不抢回；剩余行为符合 group_count 分支 |
| E2E-7 | 两个 Group 竞争 | 分配符合 spec-006 fixture，结果确定，UI/日志可解释 |
| E2E-8 | 崩溃恢复（runtime 重启） | 骨架恢复 + 派生重算；计时器不漂移；不自动重开游戏 |
| E2E-9 | 历史回放 | 从快照点可还原 FlowRuntimeState；事件点显示最近快照近似值并明确标注 |

## 4. 性能与容量（SOFT 基线）

| 指标 | 基线 |
|---|---|
| 单 Step 每帧求值 + 动作去重 | < 2 ms（不含 IPC） |
| TaskAllocator 同帧 100 个请求、1000 单位 | < 50 ms |
| FlowIR 编译（20 step / 10 group） | < 1 s |
| runtime snapshot 单份序列化 | < 100 ms |
| ObservationPacket 生成（1s 节拍） | 不阻塞下一个 GameState 处理 |

> 这些是目标基线；M0.5/M6.5 实测后回填正式预算。超预算但功能正确记 SOFT，性能优化排入下一迭代。

## 5. Definition of Done（发布/里程碑完成）

1. 该里程碑所有 BLOCK 验收项通过；
2. 新增代码有 contract test；旧代码回归全绿；
3. 依赖检查通过；
4. 与文档不一致处已改文档或新增 ADR，不允许只改代码不改文档；
5. 已知问题已登记（SOFT 列表 + 负责人 + 计划迭代）；
6. 评审记录：变更范围、测试证据、风险说明。
