# REFACTOR — modules/ 代码债审计与重构蓝图

> 来源：2026-08-23 对 `modules/` 全 67 文件 / ~1 万行的只读审计（4 个 agent 分块深读）。
> 这份文档持有**全部 file:line 细节与拆分蓝图**；ISSUES.md 的 I13 是它的一句话索引。
> 性质判定：工程**底子过硬但已"长过头"，不整洁**。分层、docstring、stub 诚实都是真功夫；
> 债集中在 god files、累积死代码、和几个会让 UI/agent 拿到错数据的真实 bug。

---

## §0 一句话结论

不是"乱"，是"好底子 + 快速长胖"——分层干净（`game` 真叶子、`production` 不 import `api`、
`driver` 不碰 flow/view 业务逻辑、无循环依赖）、docstring 普遍解释**为什么**、stub 都老老实实
标"待建"。但 god files 已到"动一下碰 900 行闭包、下一个人不敢重构"的程度，且**真实 bug
藏在体量里**——优先级是 **先修 bug（§1）→ 拆 god file（§2）→ 清死代码（§3）→ 去重（§4）**。

> **进度（2026-08-23）**：§1 全修 + §2 三个 god file 全拆（WORKLOG §0.32-0.34）。
> §3 死代码 / §4 去重已清偿（2026-08-25 N2 批：逐条核对——部分已过期、三条标保留理由，见两表处置留档）。
> B6 已关（2026-08-25 N1 批：planner 三族化）；B7 已关（2026-08-23 §0.41：live/offline 命令返回统一 {queue/task, items, accepted_seq}，QUEUE_OPS 三处共用一份）。

---

## §1 真实 bug（P0/P1，会出错误数据——优先修）

> **2026-08-23 进展（WORKLOG §0.32）：B1/B2/B3/B4/B5/B8 已修**，下表留档 + 标注修法。
> B6 已关（2026-08-25）/ B7 已关（§0.41）。

| # | 位置 | 问题 | 级别 |
|---|---|---|---|
| B1 | `view/producer.py:112-117` | 网格**只第一帧发、之后永不刷新**。`_grids_sent` 置一次永久 True；注释自承"真实现要 diff，先保守"。creep/可见性整局是第一帧陈旧值，UI 显示假数据。**已修**：内容指纹 diff（变了才发，前端 store 保留上一份）；`include_grids` 默认仍关（下发量决策）。 | P0 |
| B2 | `view/adapt.py:267` | 生产进度永远 `0.0`。`ProducingView(progress=0.0)` 写死，schema 字段在、UI 永远画 0% 进度条。**已修**：SC2 订单不带进度（协议无此字段）→ `progress=None`（rev 13），sim 侧被训单位 `build_progress` 已是真值。 | P0 |
| B3 | `view/producer.py:206` | `wall_ms` 伪造：`1.7e12 + game_time*1000` 而非真实时钟。名/类型都标 wall-clock ms，任何算延迟的诊断是假的。`view/port.py:60` 已有 `clock` 注入机制，没用上。**已修**：`FrameProducer.clock`（默认 `time.time`）+ live.py 地形帧同步真墙钟。 | P1 |
| B4 | `view/observe.py:99,101,223,232,238,242` | 提案状态字符串硬编码（`"待审批"/"已拒绝"/"已失效"`），而 `view/proposals.py:35` 有同值 `STATUS_*` 常量。改常量时 observe 过滤**静默失效**，agent 拿到错的 pending 列表——直接违反全仓"不静默"红线。**已修**：6 处全换 `STATUS_*`。 | P1 |
| B5 | `planner/opening.py:17` vs `planner/economy.py:24` | 指挥中心供给**自相矛盾**：种子态设 `supply_cap=15`，模拟建 CC 加 13，真实 LotV 是 11。早期人口节奏前后不一致。**已修**：三份拷贝（含 worldsim `bases*15`）收敛到 `economy.supply_provided` 单源 = **13** —— 审计原文"真实 LotV 是 11"经查不适用于本机：本机 game_data_dump `food_provided=13` + 真机录像首帧 1CC/0depot → cap 13。附带发现真机起始 8 工 vs 种子 12 工（ISSUES.md 开放清单「开局工人口径」项）。 | P1 |
| B6 | `planner/planner.py`（气矿封顶/`_gas_coming`）+ `planner/economy.py`（supply_provided 单族） + `planner/module_defs.py` | **三族 catalog 扩到 174 条了，planner 还是只认 Terran**。气矿写死 `terran/refinery`、`supply_provided` 只列 Terran、module_defs 全 terran/*。I9 只解了数据层，投影器消费不了非人族（sim_state 的 REFINERY 检测已先期清掉）。**已修（2026-08-25 N1 批，PLAN-NEXT / WORKLOG §0.66）**：D4 拍板 hybrid —— 气矿集合 `where(capability="gas")`、供给表 `Catalog.supply_map()`（数据=本机 dump food_provided 三族 12 条）推导；Zerg 语义显式钩子（overlord train 落成涨供给、drone 开工被吞不回矿）；`supply_provided` 从 economy 迁 catalog，六消费方（planner/initial_state/opening/alerts/worldsim）收敛单源；`basic_opening` 模块 params.race 参数化。验收 = `tests/planner/test_planner_races.py` 三族开局投影 parametrize 绿。 | P1 |
| B7 | `api/session.py:312` vs `api/live.py:340` | 命令返回 shape 不一致：`queue_op` 一个返回 `items` 一个返回 `dispatched`，`set_worker_target` 同样只在 live 多 `dispatched`。`CommandResult.detail: dict[str,Any]` 兜不住。**已修（§0.41）**：统一 `{queue/task, items, accepted_seq}`。 | P1 |
| B8 | `view/encode.py:21-26` | 双 docstring：两个相邻 `"""`，Python 只留第一个当 `__doc__`，更长那个解释（值裁剪/uint8 截断理由）被当无效表达式吞掉。**已修**：合并成一份。 | P2 |

**修法要点**：B1/B2 先修（UI 直接假数据）；B4 改成 `from view.proposals import STATUS_*`；
B5/B6 要么把 planner 改 race-agnostic（从 catalog capabilities 推导气矿/人口建筑 + 中央化
供给常量），要么老实标"投影仅 Terran"——现在数据多族但 sim 只认 Terran 是隐性矛盾。

---

## §2 God files（最大结构性债）

> **2026-08-23 进展（WORKLOG §0.33 / `e13ca82`）：G1、G3 已拆完**；G2（runtime.py）待做。
> G1 拆后：`app.py` 124 行装配壳 + `api/state.py`（87 行共享辅助）+ `api/routes/`×11
> （23~178 行/文件）；`app.state` 扁平键原样保留（测试/serve_api 的契约面没动）。

### G1 `api/app.py`（998 行）——单 930 行 `create_app` 闭包 ——**已拆（§0.33）**
塞了 ~30 个路由 handler + WS pump + store 装配全在一个闭包里。
- **WS pump** `app.py:872-992`（~120 行）含 30 行嵌套 `pump()` + 内联 seek/play/pause 消息循环 + 一段 static-frame 投递 workaround（916-953）。→ 已随整个 WS handler 落 `routes/frames_ws.py`（每连接 nonlocal 状态与 receive 循环共享，抽类反而搬两遍）。
- **业务策略混进传输层** `app.py:529-546`：提案"校验通过即自动应用"的策略写在 HTTP handler 里，应进 `ProposalStore.create` 或独立 policy 模块。→ 落 `routes/proposals.py`（独立文件 + 恢复审批的删除点注释；进 Store 的建议保留待议）。
- **模块级副作用** `app.py:999` `app = create_app()`——import 即建全 app + 扫 `DEFAULT_FRAME_DIR`。→ 保留（uvicorn `api.app:app` 部署面，改动收益低）。
- **漏的再导出** `app.py:40` 穿透 `api.session` 拿 `parse_assembly`（session 自己 import 自 flow.manifest 但没声明导出）。→ 已随拆分消除（plans 路由直接 import）。
- **拆分蓝图（已执行）**：`api/routes/{meta,sources,session,commands,agent,proposals,plans,recordings,strategies,map_plans,frames_ws}.py` + `api/state.py`。

### G2 `production/runtime.py`（948 行）——单 ~890 行 `ProductionRuntime` 类 ——**已拆（§0.34，`1316232`）**
混了队列 CRUD + 资源账本 + 阻塞/停滞可观测 + 读模型快照 + 排空调度 + **~340 行建造飞行
状态机** + 放置解析。
- **拆分结果（已执行）**：`runtime.py` 564（编排：队列 CRUD/账本/阻塞/snapshot/drain/
  单项执行/选择器/输出征用）+ `flights.py` 336（在途确认/重试/挂件/气矿 —— **Mixin 原样
  搬**：与账本/征用深度共享，抽协作对象要把共享状态设计成回调接口，行为最关键文件上
  的手术风险大于收益；纯化留给未来）+ `placement.py` 98（解析**纯函数化**：
  region_layer/catalog/in_flight 显式入参）。行为与错误文案逐字保留。
- ~~**拆分蓝图**：`queue.py`（CRUD）/ `drain.py`（调度 + 账本）/ `build_flights.py`（confirm/retry 状态机，可独立测）/ `placement.py`（解析器）；`runtime.py` 留薄编排。~~（queue/drain
  进一步拆分会把内聚的 drain 逻辑打碎，未采纳；其余按蓝图执行。）

### G3 `flow/manifest.py`（735 行）——`validate_strategy` ~200 行 ——**已拆（§0.33）**
- `validate_strategy` `manifest.py:452-650` 单函数 ~200 行。→ 编排 + `_validate_readability`/`_validate_declaration_block`/`_validate_edges`/`_validate_do_op`/`_validate_steps`，错误文案逐字保留（有测试锁）。
- params/variables 校验**复制粘贴** `manifest.py:482-508`，docstring 自承"此前只查 params，不一致"。→ `_validate_declaration_block` 一份实现两个名字。
- ~~**拆分蓝图**：抽 `_validate_declaration_block(name, decls, allowed_keys)` 调两次；拆 `_validate_params_variables`/`_validate_steps_branches`/`_validate_edges`/`_validate_do_ops`。~~ 已按此执行。

---

## §3 死代码清单（2026-08-25 N2 批清偿完毕——下表改为处置留档）

> 反直觉点：这套代码对**语义死路**（不可达 step、死边）抓得严，对**词法死代码**几乎没清理。
> N2 批（WORKLOG §0.67）逐条核对后清偿：部分条目在审计后已被别的批次顺手清掉（标「已过期」）。

| 位置 | 处置 |
|---|---|
| `flow/predicates.py` `_p_group_center` | **已过期**：审计后已清，仅存 `_p_group_center_in_region` 且被 dispatcher 调用 |
| `tactical_map/spatial.py` `nearest` + `region.py` `cells_of_big` | **已清（N2）**：nearest 连 `__init__` 导出与测试一起删；cells_of_big 死方法删（validate_layer 本就内联重算） |
| `constraint/checks.py` `check_assign_workers` + `__init__` 导出倒挂 | **已过期**：此前已清——死 API 删除，`__init__` 已导出真正被 runtime 用的 `check_addon`/`check_gas`/`occupied_cells` |
| `view/producer.py` `_seq` / `view/statics.py` `terrain_static` / `view/schema.py` `EnemyClusterView` | **_seq 已清（N2）**；**terrain_static 保留**——审计漏了 tools/：`tools/run_session.py:507` 真机地形导出就是 `to_json(terrain_static(info))`，test_terrain.py 锁编码与 null 语义；**EnemyClusterView 已过期**（PLAN-V2 批 4 落地 clusters.py，adapt 不再恒 None） |
| `game/__init__.py` 单族 loader | **已清（N2）**：`load_protoss`/`load_zerg` 零调用删除；`load_terran` 留作测试的单族视图并归一到 `_load_race`（不再自带一份读取循环） |
| `planner/curve.py` `time_to`/`peak_minerals`/`stalls` | **保留**：「零消费者」判定已过期——stalls/time_to 是投影断言糖（tests/planner 多处），peak_minerals 被 `tools/probes/run_sim.py` 消费 |
| `flow/manifest.py` `UNIMPLEMENTED_DO_OPS`/`UNIMPLEMENTED_STEP_KEYS` 空 dict | **保留**：注册表模式不是词法死代码——同族的 PREDICATE/SPATIAL 两表非空且在校验里活跃，空表 = 「当前声明的都已实现」；删掉破坏三表对称的校验框架 |

---

## §4 重复 / 冗余（2026-08-25 N2 批清偿完毕——下表改为处置留档）

| 重复 | 处置 |
|---|---|
| **帧源接口三份复制**（sources/session/live） | **已清（N2，D2 拍板：api 内独立模块）**：`api/frame_source.py` = `SourceInfo` + `FrameSource` Protocol + 四查询纯函数（`info_of`/`first_statics`/`statics_only`/`latest_at`/`between`，EPS 单源）。三实现改薄壳（live 只包一层锁）。**刻意不给基类**：三处没有共享状态，「同形」做成「同壳」还得跟锁语义搏斗——纯函数 + 薄壳保留各自的存储形态（录制流=全量列表 / 会话=动态+静态缓冲）与并发差异 |
| `_mmss` 逐字节重复（alerts/observe） | **已清（N2）**：`view/fmt.mmss` 单源，两处 import |
| reserved-box 重叠检查写两遍（map_plans save vs save_payload） | **已清（N2）**：`_reserved_conflict(name, entry, reserved)` 单槽位 helper 共用 |
| 常量重复（production worker/economy 的饱和/半径三常量） | **已清（N2）**：`production/constants.py` 单源；worker/economy 导入，flights 的 `NODE_RADIUS` 改从 constants 取 |
| "按平方距离找最近 base"（reserved 多处 + base.py） | **部分收敛（N2）**：`spatial.nearest_index(points, target)` 收编 min 型两处（主矿归属/气井归属）；exps 排序键与 others-any 比较是「全序/比较」形态、base.py 是 Point2/SpawnLayout 域——保留内联 |
| `map_plans.py` 穿透 import 私有（`_footprint`/`_overlaps`） | **已清（N2）**：公开为 `footprint_of`/`footprints_overlap`（map_plan 仍是几何单点；公式源头在 tactical_map.placement，docstring 标注） |

---

## §5 空 / stub 模块（2026-08-25 N5 批收尾完毕——下表为处置留档）

| 位置 | 状态 | 处置 |
|---|---|---|
| `modules/mechanics/__init__.py` | 3 行 docstring 占位（D11 power/addon 机制待建）| **已挂账（N5）**：docstring 加 ADR-0002 §4（种族机制归层的约定）与本表引用；启用前调用层最小近似的先例（planner Zerg 钩子）已标注 |
| `modules/world/__init__.py` | docstring 头；`adapter.py` 是真 V1 实现 | 非 stub，不动 |
| `driver/sc2_adapter.py` `SC2GamePort.stop()` | **唯一真正的假实现**（`pass`）| **显式 no-op（N5）**：docstring 如实挂牌——真机停机链路在进程树清理（api/live_io.kill_tree），本方法无调用方、只为协议形状；真实现（surrender/leave）挂账本表 |
| `flow/engine.py` + `view/recorder.py` `on_session_event` | bare no-op（D7 事件流未定）| **已加注（N5）**：engine 侧补 docstring（与 recorder 同一拍板：不猜字段、不静默造数据），wire 待事件源落地（ISSUES 开放清单 #9 世界推导路线） |

---

## §6 长函数（>60 行，顺手在拆 god file 时收掉）

- `view/map_plan.py:72-194` `apply_map_overrides`（123 行，7 分支 if/elif over hunk kind）→ 分发表 `{kind: handler}`。
- `flow/predicates.py:226-264` `_call_predicate`（17 分支 if/elif）→ 派发表（签名已在 `PREDICATE_SIGNATURES`，顺带让 `_p_group_center` 这类死 wrapper 不可能再出现）。
- `view/adapt.py:311-379` `flow_frame`（69 行）/ `495-568` `economy_frame`（74 行）→ 抽 `_strategy_view`/`_economy_tasks`。
- `planner/planner.py:177-266` `_feasible`（~90 行，Build/Train/Research 三分支重复"缺矿/缺气/前置没"）→ 抽 `_check_cost_and_prereqs`。
- `production/economy.py:250-313` `_plan`（~63 行）→ 抽候选排序 helper。
- `tactical_map/region.py:241-349` `load_region_layer`（~108 行）→ 拆 `_load_grids`/`_load_regions`/`_load_marks_slots`。
- `view/strategies.py:197-210` `_compile_errors`（三个近乎相同的 try/except）→ 循环 + 单 handler。

---

## §7 优先级与拆分顺序

1. ~~**P0 修 bug**（§1 B1/B2/B4/B5）~~ —— **已完成（2026-08-23，WORKLOG §0.32，
   含 B3/B8）**：producer 网格 diff、progress=None、wall_ms 真时钟、observe 常量化、
   CC 供给单源=13。
2. ~~**P1 god file 拆分**（§2 G1/G2/G3）~~ —— **全部完成（G1/G3 §0.33；G2 §0.34）**。
3. ~~**P1 三族只到 catalog 没到 planner**（§1 B6）~~ —— **已完成（2026-08-25 N1 批，
   WORKLOG §0.66）**：catalog 推导 + Zerg 语义钩子（D4 hybrid），三族开局投影测试绿。
4. ~~**P2 死代码清理**（§3）~~ —— **已完成（2026-08-25 N2 批）**：三条已过期、四条删除、
   两条标保留理由（terrain_static 有 tools/ 活调用、curve helper 被测试/探针消费）。
5. ~~**P2 去重**（§4）~~ —— **已完成（2026-08-25 N2 批）**：FrameSource 纯函数收敛 + 四项小去重
   （最近 base 距离部分收敛，排序/比较形态保留内联）。
6. **stub 收尾**（§5）——给空 stub 加 issue 引用，别让 `mechanics`/`on_session_event` 无声腐烂。

---

## 附：模块现状地图

| 模块 | py 文件 | 总行 | 状态 |
|---|---|---|---|
| `game/` | 8 | ~790 | 全实现，真叶子，零 TODO——最干净 |
| `planner/` | 9 | ~870 | Terran-only 隐性矛盾（B6）+ `planner.py` 379 行偏重 |
| `flow/` | 6 | ~1700 | `manifest.py` 735 god file（G3）；vocab 是真单一真相源 |
| `tactical_map/` | 8 | ~970 | 几个死方法 + 距离写法三重复制 |
| `constraint/` | 2 | ~165 | 小，但 `__init__` 公开面倒挂 |
| `view/` | 17 | ~3500 | 最大模块；`producer.py` 三连 bug（B1/B3 + 死 `_seq`）；`schema.py` 766 非债（40 个 DTO + REV）|
| `api/` | 6 | ~2000 | `app.py` 998 god file（G1）|
| `production/` | 6 | ~1500 | G2 已拆：runtime.py 564 编排 + flights.py 336 + placement.py 98 |
| `driver/` | 4 | ~650 | 唯一假实现 `stop()`；其余干净 |
| `world/` | 2 | ~92 | adapter 真 V1 实现，非 stub |
| `mechanics/` | 1 | 3 | **空模块**占位 |
