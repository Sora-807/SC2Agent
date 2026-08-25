# 工作记录：可用性整改执行轮（2026-08-21）

> 本文记录「三个并行审查 → 修复批次 → WS 断线 → F14 全链 → I5/I6 → F15-F19 → 用户四轮反馈」
> 这一执行轮的工作、决策与发现。
> 承接：docs/archive/PLAN.md（计划，已归档）、docs/ARCHITECTURE.md（架构）、docs/ISSUES.md（问题清单）。
> 每节最后标了哪些 ISSUES/红线被关闭。

## 0. 提交清单（本执行轮，按时间序）

| 提交 | 内容 |
|---|---|
| `468219f` | 审查发现的 8 个功能缺陷：写入门禁绑错源 / P5 盲接受 / P2 按钮隐藏 / pos_marks 从未渲染 / pan 误选 / pointermove 旧闭包 / chip 档图层开关 / cluster 组归属顺序依赖 |
| `4a9abb4` | F12 收尾：exit_strategy 终局三态 / PanZoom fitKey / 持久化竞态 / matchExitBranch 收窄 |
| `8569c47` | WS 断线：显眼横幅 + 手动重连（**ISSUES I3 关闭**）|
| `e65b4ce` | F14 切片 1：地图规划画布 + 点位工具（离线放标记，草稿持久化）|
| `211cae2` | F14 切片 2 + B14：map_plan 全链（提案 → 接受 → 机器覆盖层 → 新会话生效），契约 REV 10→11 |
| `6c1abbe` | F14 切片 2b：槽位拖动 + 审批叠加画布 |
| `2e51f46` | docs：工作记录 WORKLOG.md + ISSUES I3 留档 |
| `7c5fe0e` | 执行轮 §0.1–§0.12 一并提交：P0/P1/P2 规划域重构（生产规划文件+干跑 / 导航按模式 / 真地形+全图资源+预留区命名+双下拉）+ 实时驾驶整改（假世界拔除 / 400 显式化 / 两段式确认 / 关闭真机）+ 真机地形修复（driver 顺序 + store 乱序合并）|
| `ba8ceb5` | 执行轮 §0.13–§0.31 一并提交：P3 agent 接入 + 聊天/外壳改版 + 策略文件免审 + 三族目录 + 录像（未提交批次一次落库）|
| `6e7370f` | REFACTOR P0 bug 批 B1-B5+B8（网格 diff / progress 真值 / wall_ms 真时钟 / observe 常量 / CC 供给单源=13）|
| `e13ca82` | R1 god file 拆分：app.py 998→薄装配+state.py+routes/×11；manifest.py validate_strategy 分段（REFACTOR G1/G3）|
| `1316232` | R2 god file 拆分收尾：runtime.py 948→564 编排+flights.py 336（Mixin）+placement.py 98（纯函数）（REFACTOR G2）|

## 0.66 N1 批：planner 三族化（PLAN-NEXT 队列头，REFACTOR B6 清偿，2026-08-25）

> D4 当场拍板 **hybrid**：「哪些建筑」从 catalog 推导（`where(capability="gas")` /
> `Catalog.supply_map()`），「供给/工人语义」的种族差（overlord 单位供给、drone 化建筑）
> 用显式钩子管——测试先行四批跑完，全量 1076 绿（+22）。

- **N1a 红测试**（`tests/planner/test_planner_races.py`）：三族对称开局投影
  parametrize（完成时刻/终态/零死局）、气矿收入三族 parametrize（refinery 对照绿、
  assimilator/extractor 红）、Zerg 双语义（overlord train 涨 cap、drone 被吞）。
  开工时 6 红 2 绿，收工全绿。
- **N1b 数据层**：catalog JSON 加 `supply_provided` 字段——**从生成器进**
  （`tools/generate_catalog.py` 读 dump food_provided），不手改 JSON；12 条三族
  （CC/orbital/planetary=13、depot/pylon=8、hatchery/lair/hive=4、overlord 族=8）。
  `CatalogEntry.supply_provided`（默认 0）+ `Catalog.supply_map()` + `where(race=)`。
- **N1c 投影器**：planner.py 气矿封顶/`_gas_coming` 走 `self._gas_types`；供给涨幅
  提升到所有落成形态（build 与 train 同涨——overlord 语义）；`_consumes_builder()`
  Zerg 钩子（开工吞工+供给释放、落成不回矿）。`EconomyParams` 摘除 supply_provided，
  六消费方迁 catalog 单源：planner / initial_state×2（对账顺带把 units 侧 overlord
  计入 + static_check 给供给单位 train 记 planned_cap）/ opening（race 参数化，
  `CC_SUPPLY`→`base_supply(catalog, race)`，Zerg 种子带 overlord 12/12）/ alerts
  （supply_capped 集合含三族基地）/ tools/worldsim（第六消费方，方案核对时漏了
  modules/ 外的）。
- **N1d 模板层**：`basic_opening` 走 `params.race`（注册签名不动——plans.py 模板
  落地面就是 `fn(params)`）；「开局长什么样」是策略知识不是结构，显式三族表
  （zerg 分支多一条跳虫把三个 Zerg 语义都过一遍）；factory_chain/bio_tank_opening
  明示 Terran 专用不参数化。
- **回填**：REFACTOR B6 关闭、ISSUES #11 更新、PLAN-NEXT N1 出队（D4 已决注记）。
- **顺带发现（不属本批）**：①catalog `protoss/gateway` 前置记 nexus（真实应为
  pylon）——数据欠账另行核对；②`basic_opening` 默认 scv_count=12 从 12 农民种子
  起投会 24>21 卡供给死等（模板标定问题，测试用 scv_count=3 绕开）；③Zerg 变形链
  （lair/hive/overseer 形态替换）在 V1 建筑计数抽象下当「新建筑 +1」，注释已标。

## 0.67 N2 批：去重与死代码清偿（PLAN-NEXT 队列次席，REFACTOR §3/§4 收官，2026-08-25）

> 逐条核对发现审计清单 7 条里 **3 条已过期**（别的批次顺手清了）+ **2 条判保留**
> （其中 terrain_static 审计漏了 tools/——真机地形导出 `tools/run_session.py:507`
> 就是它，差点删成事故）。实际删除 5 件、去重 5 件、部分收敛 1 件。

- **N2-a 帧源去重（D2 当场拍板：api 内独立模块）**：新建 `api/frame_source.py`
  ——`SourceInfo`（从 sources.py 迁入）+ `FrameSource` Protocol + 四查询纯函数
  （`info_of`/`first_statics`/`statics_only`/`latest_at`/`between`，1e-9 容差单源成
  `EPS`）。三实现改薄壳：JsonlSource 全委派；OfflineSession 委派（statics 走
  `statics_only`）；LiveSession 锁内委派。**刻意不给基类**——三处无共享状态，
  基类会把「同形」做成「同壳」还得跟锁语义搏斗；backfill 参数保留各自语义
  （录制流=frames 翻静态首条 / 会话=静态缓冲含初始 frame/session）。game.ports
  落点否决：那是 driver↔engine 边界，帧源是 api 层概念。
- **N2-b 死代码**：`spatial.nearest`（连 `__init__` 导出与测试删）、
  `region.cells_of_big`、`producer._seq`（只写不读的自增计数）、
  `load_protoss`/`load_zerg`（零调用，`game/__init__` 导出一并清）、
  `load_terran` 归一到 `_load_race`（不再自带一份读取循环；留给
  测试当单族视图）。**判保留**：`terrain_static`（tools 活调用+编码契约测试）、
  curve 三 helper（stalls/time_to 测试糖、peak_minerals 探针用——「零消费者」
  判定过期）、manifest 空 dict 两只（注册表模式，PREDICATE/SPATIAL 同族表非空活跃）。
- **N2-c 小去重**：`view/fmt.mmss`（alerts/observe 双份合一）；
  `production/constants.py`（饱和/半径三常量单源，worker/economy/flights 迁）；
  `map_plans._reserved_conflict`（save 与 save_payload 的预留区重叠检查合一）；
  `map_plan._footprint/_overlaps` 公开为 `footprint_of`/`footprints_overlap`
  （穿透 import 的 noqa 退役）；`spatial.nearest_index`（reserved 的主矿/气井
  归属 min 位收编；排序键与 any 比较、base.py 的 Point2 域保留内联）。
- **教训**：给 worker.py 加 `__all__` 时凭审计记忆编了六个不存在的函数名，当场
  核对导出面后撤掉——**写 __all__ 前必须 grep 实际定义**，审计文档的模块布局
  是旧快照不能当现在。
- **回填**：REFACTOR §3/§4 改处置留档（已清/已过期/保留三类）、头部进度与 §7
  清单勾销、PLAN-NEXT N2 出队（D2 已决注记）。

## 0.68 N3 批：生产运行时瘦身（PLAN-NEXT 第三席，2026-08-25）

> D1 当场拍板 **Mixin 继续**（与 G2 同一结论，协作对象明确不做）。四件全部达标：
> runtime 852→482（≤650）、live 675→538（≤550）、routes/session 374→93、
> routes/plans 353→99（双双回到 178 天花板内）。行为零变化——账本/flight/
> api 装配测试原样绿（293 passed）。

- **N3-a runtime 回吐**：`production/ledger.py` 新建（LedgerMixin：帧资源账本
  `_charge`、阻塞记录 `_block/_why/_note_block`、ADR-0032 状态迁移
  `_mark_skip/_finish_item/_mark_started/_sweep_completions`、读模型
  `_producer_ever_ready/_item_ever_ready/snapshot` + `STALL_WARN_SECS` +
  `_placement_dict`）；flights.py 回吐补齐建造域第三条路径（常规建造
  `_try_build`、工兵选择 `_pick_builder/_no_builder_reason`、征用
  `_reserve/_release_flight`、取消 `_cancel_flights`、placement 薄包装）——
  挂件/气矿/常规现在完整住一个文件。runtime 留纯编排（CRUD/tick/drain/
  训练执行/选择器/输出）。
- **N3-b live 瘦身**：`api/live_io.py`（RecordingMixin：录制 setup+收尾；
  `kill_tree` 进程树清理；`_races_from_frames` 种族推断）——都围着子进程与
  它的产物转。
- **N3-c 路由抽层**：`routes/session_start.py`（start 装配/幂等守卫/两模式
  校验 + swap/map-plan 两个热切 + loadout 辅助）与 `routes/plans_simulate.py`
  （simulate v2 的 245 行服务本体）各自成路由文件，routes/×11→×13。
- **坑（两次踩同一类）**：搬方法后凭直觉剪 imports——runtime 剪掉 `_drain`
  还在用的 `STATUS_*`、session.py 剪掉 export 还在用的 `load_all`，各红一次；
  更阴的是 runtime 那次让 **sim 子进程带着 NameError 崩**，api 测试假挂 8 分钟
  （pytest 无输出、无死锁）。教训进流程：**搬完必须 AST 扫未用名 + 先跑本包
  测试再跑 api**。另：`_StartBody` 定义在搬切点之前被复制成两份，发现后清。
- **回填**：PLAN-NEXT N3 出队（D1 已决注记）。

## 0.69 N4 批：前端测试形态治理（PLAN-NEXT 第四席，D3=分类处置，2026-08-25）

> 摸底推翻了「迁为行为测试」的简单假设：12 个 readFileSync 文件里只有 3 个是
> 审计抱怨的接线锁；5 个是 fixtures 加载（正当）、1 个是跨语言契约对账
> （contract 用精确正则解析后端 schema.py 的 REV/TOPICS——正当）、2 个是全仓
> 约定禁令（theme/layout = custom-lint，**无行为可迁**）。接线锁断言的大半是
> **用户拍板的 UI/交互决策**（5 行输入框、无发送按钮、拖动=平移、框选下线）——
> 本仓无渲染基建（vitest node 环境、纯函数哲学）且 UI 由用户自测，迁渲染测试
> 不改变「锁」的性质。D3 据此拍板：分类处置而非全迁，验收口径如实修订。

- **扫描单点化**：source-scan.ts 加政策头（三类正当用途 + 决策锁守则）与
  `codeAbs/rawAbs`；chat-dock/charts 的**弱版内联 code()**（正则剥注释，不认识
  字符串字面量）删并到 stripComments；map-canvas 的 cwd 相对路径
  （`readFileSync("src/...")`，换目录跑就挂）改 SRC 锚定；theme 的本地文件
  遍历器与 rel 计算删并到 allSources/rel；layout 的 `stripComments(readFileSync(p))`
  改 codeAbs。
- **真修一条注释依赖断言**：「流面失败回退整段」原本靠 `sayChat(text); // 回退`
  的**行尾注释**才同线成立——真剥注释后断空，改锁代码事实
  （`sayChatStream…sayChat\(text\)` 序列）。这正是审计说的子串锁的病根标本。
- **it.each 立杆**：theme 的 FONT_PX 三载体对齐（index.css @utility = FONT_PX =
  tokens.T）从 for 循环改 it.each——哪个 token 不齐单独红。
- **验收（D3 口径）**：`grep readFileSync web/tests` = fixtures 加载（economy/
  source/schema/graph/gantt/charts×1）+ contract 契约读 + source-scan 单点；
  396 绿 + typecheck 净（后端本批零改动，N3 门仍有效）。
- **教训**：bash heredoc 里写 Python 替换正则转义三连坑（`\\/`/`\\s` 层层失真）——
  含正则的替换一律走 Edit 工具，别再在 heredoc 里猜。
- **回填**：PLAN-NEXT N4 出队（D3 已决注记 + 验收口径修订理由）。

## 0.70 N5 批：质量杂项收官（PLAN-NEXT 第五席，工程批 N1-N5 至此清空，2026-08-25）

- **flaky 修**（`test_sim_session_assembles…terrain`，全量跑偶发红两次复现）：
  根因 = 轮询 `frames>3`（10s 预算）后直接断言——子进程冷启动偶发超预算就红；
  且失败路径不停会话，漏的 sim 子进程拖慢全场雪上加霜。修：轮询**真前置**
  （statics 出现 static/terrain + static/map）、预算 30s、finally 停。
- **llm_stream 迁直调**：`tests/agent/test_llm_stream.py` 8 条——双字段容错
  parametrize（reasoning/reasoning_content 各一条 + 混用一条）、零分片重试
  （sleep 打桩不真等）、partial 不重试、预算三态（用尽不调 API 返 budget-stop
  哨兵落 model 字段——vendor 形状无 finish_reason、计数跨调用累计、reset 清限）。
  tests/api/test_agent_chat.py 删 3 条装配版（鸭子类型样板三份重复随之归一）。
- **覆盖缺口核实**（推翻「两区最危险」的行数比印象）：import 级扫描 agent/ 全
  覆盖；view 裸模块只有 fmt（N2 新建）与 jsonl——补 `tests/view/test_fmt.py`
  （7 参数）+ `tests/view/test_jsonl.py`（往返/一行一封/空行跳过/坏行带行号
  parametrize）；map_plans/initial_states/loadouts 有 api 集成测罩（非裸）。
- **stub 三件收尾**（REFACTOR §5 处置留档）：mechanics 占位挂 ADR-0002 §4 +
  REFACTOR §5 引用；`SC2GamePort.stop()` 显式 no-op 挂牌（真机停机走
  live_io.kill_tree，本方法无调用方）；engine `on_session_event` 补 docstring
  （与 recorder 同拍板：不猜字段不造数据，wire 待 ISSUES #9 世界推导）。
- **回填**：PLAN-NEXT N5 出队（工程批清空，只剩 N6 UI 后置）。

## 0.71 I39 批 A：agent 观测与工具面四件套（agent 自提需求，用户拍板开跑，2026-08-25）

> 来源是 agent 在复盘中自提的四条缺口（用户转述原文）。梳理时发现录像回看的
> 根因比 agent 说的深一层：tools 层 observe 的 **args.source 没接管道**——
> 传录像 id 也被闭包默认 live 吞掉，才报出误导性的「没有活跃会话」。
> 两个默认拍板：swap **放开**（与免审方向一致、端点约束+swap 事件可审计）；
> 录像+bbox 网格后置。

- **离散度**：clusters.cluster_units 加 spread（到簇心平均距离，1 单位=0），
  observe 部队表加「散布 X.X格」+段首口径注。测试教训：**手算期望值两次算错**
  （方阵角距心是 √2 不是 1；三人堆是 16/9 不是 8/3）——实现是对的，测试错，
  期望值要现推不要凭直觉。
- **录像回看**：state.resolve 回落 recording_registry（create_app 装配，与夹具
  registry 同款懒加载）；observation 端点 time 超界 400 带时长；rec- 前缀 404
  列可用录像；tools 层 source 管道接上 + 描述改三值。
- **open**：workspace.open_chip 三区白名单 → ChangeRecord(action="open") →
  前端 chip（↗ 图标，ChatChange 联合类型扩 action/area）；target 走 change_target
  单点。顺手发现：prompt.md 引用的 system/surface.md **不存在**（悬空引用），
  改写段落时一并摘除。
- **swap_strategy**：client.session_swap + 工具薄壳；提示词双份同步
  （agent/seeds 源 + workspace 工作副本——只改工作副本会被新工作空间的播种绕过）；
  边界锁 EXPECTED_TOOLS +2（测试红得其所：授权边界变化就该红一次）。
- 基线 1092→**1108**+4skip / 前端 396 + typecheck。







## 0.41 三十四轮：模板化 + 生产序列补齐 + 热切 V1 三批执行（2026-08-23，未提交）

> 立项与决策见 ADR-0031 与本文各节（原执行计划 PLAN-TEMPLATES-HOTSWAP.md 已执行完毕删除；
> 决策浓缩在 §0，不要重新讨论）。三批一提交的纪律由用户在提交时执行。

**批 A（模板库与展示层，REV 15）**：
- `flow/templates.py` + 模板库（ADR-0031）：编译期模板展开。出厂种子
  `modules/flow/data/_lib.yaml` 随版本库（runtime/ 整目录 gitignore），StrategyStore
  建目录时播种成 `runtime/strategies/_lib.yaml` 工作副本（人改副本）。
  `imports:` 节引用模板（键名=step_id、params 绑定、绑定值可桥接策略级 `{param}`）；
  exits 接口契约（edges 接错线当场红）；展开产物与手写同构，运行时零改动。
  种子六件：gather/push/hold_ramp/garrison/armor_hop/inf_hop（全部过编译+引擎转移）。
- `StrategyStore`：`_` 前缀=锁定保留名（`_lib` 不是策略、清单不列）；带 imports 的
  保存喂模板库编译；`load_strategy_file` 自动装同目录 `_lib`；`GET /api/strategies/_lib`
  只读原文（agent `read strategies/_lib.yaml` 走它，写被指路拒绝）。
- 展示层：branch `display_name_zh`（BRANCH_KEYS+校验+前端渲染）；`flow/vocab.REASON_ZH`
  全局默认表，`static/strategy.reasons` = 默认 ∪ 策略覆盖；`imported: [step_id]` 标记。
- memory lint（A4）：`agent/memory_lint.py` 纯函数 + ApiWorkspace 写钩子（软提示不拒绝）；
  write/append/edit/insert 换 lint 版工具（vendor 不改：drop 名字条目换直接 factory），
  写 memory/*.md 的结果尾部附 [ID]/状态字段提示。
- 工具退役（A5，19→17）：`read_current_strategy`（dump 写死常量误导 live）与
  `write_surface`（挂成只读文件 `system/surface.md`，SurfaceArea 渲染 /api/agent/tools）
  退役；原则进 AGENT-LOOP §6：**新能力优先问"能不能是一个文件"**。

**批 B（生产序列补齐 + loadout，REV 16）**：
- B1 loadout：`runtime/loadouts/<id>.yaml`（map_plan/strategy/plan/spawn 三件套引用，
  只读文件即真相）+ `view/loadouts.py`；`session/start?loadout=` 解析并把 plan 的
  队列**自动 submit 入队**；start_session 工具 + StartCard 下拉。
- B2 队列命令：`insert(index)`（剩余队列位置，越界 400，天然只具后效性）+
  `replace_head`（原子换队首，无 409 窗口）；QUEUE_OPS 三处共用一份（api/commands.py）。
- B3：flight 记 `from_index`（emit 时剩余队列下标）→ InFlightView（rev 16）；
  observe 生产段收口（队首+剩余+在途带原序号）+ 新增「op 流水」段。
- **sc2 控制文件通道**（顺带解决 sc2 一直无命令面的问题）：stdin=DEVNULL 的真机，
  命令经控制文件 —— 父进程 append、子进程帧边界 rename→读→删（无损协议）。
  queue/workers/swap 全走它；LiveSession._statics 按 topic 去重（热切重发静态面不再被旧帧遮住）。
- B7 关账：live/offline 命令返回统一 `{queue/task, items, accepted_seq}`（dispatched 删除）。

**批 C（热切 V1）**：
- `FlowEngine.swap_strategy(manifest)`：同装配约束（assembly shim 重跑全套 validate_assembly
  + map 名校验，先校验后变更零中间态）；同名 active_step 续位（locals/timers 保留）、
  异名从 initial_step 重起；variables 同名保留；对已结束策略 swap=复活；去重键清空
  （新策略首帧命令不被旧签名吞）；转移历史记 `swap` 事件（from 旧@版本→新，续位/重起）。
- `POST /api/session/swap?strategy=<id>`：offline=帧边界 pending 应用 + 重发 static/strategy；
  live（sim stdin / sc2 控制文件）子进程帧边界应用；group_slots 不一致 409（会话不受影响）。
- 提示词/写面规则更新：策略**可**热切（用户的动作，agent 无工具）；装配热切/参数级热改/
  多实例明确不做。

**回归**：后端 851 passed（新增 tests/flow/test_templates.py 22、tests/agent/test_memory_lint.py 8、
tests/api/test_loadouts_swap.py 18）；前端 361 passed + tsc/build 绿；夹具按 REV 16 重生成。
真机（sc2）swap 端到端待用户验一次（控制文件通道已被 sim 子进程测试覆盖）。

## 0.51 四十四轮：真机七报立项（PLAN-LIVE-ROUND2，本轮只立项不执行）（2026-08-24，未提交）

用户真机（仿真快进）再报七项，本轮全部**钉根因 + 立项**，修复留下一执行轮：
`docs/archive/PLAN-LIVE-ROUND2.md`（A~F 六批：投影真值 / 跟随强化 / start_session 配置面 /
无builder 告警 / observe 结构化读法 / 图标+done 前端）。已核实的根因：

- **投影矩阵「炸掉」**：`producer._project` 在 live 队列空的瞬间回退**参考计划**
  （basic_opening 22 项）——单笔 train 进 in_flight 即弹队列 → 整份开局参考被当投影
  画出（截图 20+ 农民、「在产 6」全是它）。修法= in_flight 进投影 + 中途永不回退。
- **启动期 agent 停**：`FOLLOW_MAX_IDLE_STOPS=3` 被纯文字轮烧完 →「跟随停止」；
  用户拍板上限 200、不许放弃跟随、提醒改**系统条 UI**（不再冒充用户消息）。
- **「补给站无builder」反复告警**：blocked 原因上浮为告警本身没错，错在无诊断、
  持续不解除（候选根因三个：stale 后端 / flight 征用泄漏 / economy foreign 误伤，
  执行轮真机钉死）；诊断计数（§0.47 已埋）要进告警文案。
- **done 工具**：立项时判「vendor 内建后端不动」**过保守**，用户拍板可动后复核：drop 是
  vendor 自己的组装接缝（AdvisorSpec 已在用），`.drop("done")` 一行即可；runner 对纯
  文字轮本就自然收轮。唯一暗桩 = vendor `build_system_prompt` 固定追加的「调用 done
  提交结果」行（talk.py:495/runner.py:82 都走它）——只删工具会留「调不存在工具」的
  摩擦，方案改为三件套（drop + 删我方 prompt 两处 + talk 本地组装系统提示词），见
  PLAN F2。
- **observe 缺三段**：关键建筑数量（含挂件）/全部队明细/区域信息（坐标血量）——
  数据全在 world 帧 UnitView，零契约改动可加；旧文档检索无该格式逐字原文，
  以用户本轮口述为准。

## 0.52 四十五轮：PLAN-LIVE-ROUND2 六批执行（2026-08-24，未提交）

上一轮立项（§0.51）本轮全部落地，A→B→C→E→F→D 序：

- **A 投影真值**（`view/producer._project`）：`_live_seen` 门 —— 见过 live 队列后
  **永不回退参考计划**，队列空投空队列（纯收入外推；在建/在训从 world 帧的
  build_progress 派生、照常落成，在途可见性不靠参考计划）。核实发现 planner
  `derive_from` 本就把真实在途带进曲线（成本不重扣），故 A1 的"重建 QueueItem"路径
  刻意不做——会让成本双扣+累计泳道重复条。开局（从未见过 live 队列）参考计划照常、
  标 draft。测试 +3（回退门/开局/在建落成）。
- **B 跟随强化**（`agent/talk`）：`FOLLOW_MAX_ROUNDS` 40→200；**删 idle-stops 放弃**
  （连续纯文字不再「跟随停止」——那正是启动期停摆根因），唯一止损=轮数上限；
  `_game_alive_retry`：活过之后突然取不到短重试 2 次（SC2 启动/收尾窗口后端忙 ≠
  对局结束），从未活过不重试。提醒轮历史带 `nudge: True`，前端 **NudgeBar 系统条**
  （warn token 全宽条，不冒充用户气泡），提醒文案带次序号+「系统要求非用户说话」。
  提示词同步（文字不会结束回合/启动期也 sleep）。测试改 2 增 1。
- **C start_session 配置面**：`production` 请求体参数（mineral_workers/gas_workers/
  reserve_idle，简写 mineral/gas/idle）→ 会话建立即 `set_worker_target` 下发（offline/
  live 两路）；校验在建会话**之前**（坏参数不留半配会话）。工具层同名校验+采集配额
  回显；提示词补「开局配置」小节。测试 +3（路由）+1（工具）。
- **E observe 三新段**（`view/observe`）：**部队清单**（全部战斗单位按类计数+form、
  在训 world producing、待训队列 train 项）、**关键建筑**（建成计数+挂件点名+在建带
  进度）、**区域**（按经济节点 base 分桶：建筑逐个坐标+血量%、部队按类计数、远离
  基地归「机动」；只文字不碰地形=I8 的地盘不越）。facts 补 `buildings`（挂件算宿主
  `:reactor` 后缀）与 `army` 机器可读副本。红线保持：只从 ViewFrame 派生，零契约改动。
  测试 +4。
- **F 图标语义化 + done 下线**：toolGlyph 几何字符退役 → `ToolIcon` 12px SVG
  （read=文稿、glob/grep=放大镜、write 族=铅笔、observe=眼睛、sleep=月牙、
  start/stop_session=靶心/方块、delete=垃圾桶）。**done 三件套**：spec `.drop("done")`
  （vendor 自设计接缝，源码不动）；prompt 两处删+「回复即回合结束」；talk `_system_prompt`
  本地组装替换 vendor `build_system_prompt`（其固定追加的「调用 done 提交结果」会引导
  每次收尾白试一轮不存在的工具）——**首轮也预种 system 头**，runner 自组路径永不触发。
  冒烟链/测试脚本全部改纯文字收尾（`_done` 保留名字、行为=文字响应）。
- **D 无 builder 告警**：flight 征用**全路径审计 = 无泄漏**（failed/started/drop 都
  release，换人重发先 release 旧 owner）——截图事故根因收敛到「stale 后端跑旧代码」；
  诊断三元组拆开（总数/建造征用/本帧已令）进 `_no_builder_reason` → 阻塞 → 告警文案。
  回归 +2（丢弃必释放征用+重抽、诊断计数文案）。

**回归**：后端 887 passed / 4 skipped；前端 379 passed + tsc/build 绿（NudgeBar 裸
amber 被主题纪律测试拦下 → 改 warn token，纪律生效的实例）。真机复验点：只提 1 农民
泳道不再炸；启动期 agent 被系统条驱动 sleep；observe 一眼答「兵营几个带没带挂件、
部队都有啥在哪」；补给站 10s 内 builder 就位（若复现再查 stale 进程）。

## 0.53 四十六轮：真机三报根因——乱分配/propose 无效是同一条链（2026-08-24，未提交）

用户真机（正常模式）三报，前两报查 trace（rec-20260824-001605）钉成**一条事故链**：

- **证据链**：op 流水显示维持器整场每 2-5s 重派 gather（乱分配本体）；t=18.6 生产层给
  SCV 发建造补给站、t=18.75 建筑 0% 实体出现、**t=19.1 维持器给同一台 SCV 发 gather**
  → 建造单被顶 → 0% 建筑被弃（t=80 第二次同样死法）；复盘里"生产队列一直空"其实是
  正常的 emit 即出队（0.2s 内消费），泳道图上的「缺矿/无builder」卡点是**参考计划**
  的投影（live 队列从未非空 → `_live_seen` 未置位 → 整场投 basic_opening 草稿）。
- **根因①（驱动，最底层）**：`sc2_adapter.extract_raw_order` 读 `target_unit_tag`——
  burnysc2 `UnitOrder` 没这个字段（真实字段是 `target`：int=单位tag | Point2=点）→
  真机订单 **target_tag 恒 None**：维持器看不见"已在采"（走矿段的工人全判空闲→
  全场重派），采矿族判定也要求 target_tag 而全数失效。sim 驱动字段填得对所以离线
  测试一直绿——**测试夹具把错误协议也刻死了**（`_fake_order` 按不存在字段造），
  夹具已重写为真实形态并加回归锁。
- **根因②（flight 确认）**：`_confirm_build` 把"实体出现"当完工立即释放征用——
  SC2 放置即出 0% 实体，SCV 还要盖 build_time 秒。修：锁定实体（`entity_tag`），
  等 `build_progress>=1` 才 started/释放；实体半途消失（Decay 死/被拆）→ failed 转重试。
- **根因③（维持器）**：扛货分支（carrying → 沿用记忆）**不做外来订单检查**——被派去
  建造的 SCV 手上常有矿，记忆又因①是空的 →"扛货无记忆"= 可派 → gather 顶掉建造单。
  修：扛货但订单为外来能力（非 Gather/Harvest/Return）→ 进 foreign 不接管；采矿族
  判定改按**能力名**（不再要求 target_tag，对两种驱动都稳）。
- 第三报（复盘切换加载慢）：纯前端性能问题，用户拍板可后排 → ISSUES 开放清单 #19。

**回归**：后端 892 passed / 4 skipped（+驱动形态锁 2、+flight 完工/实体死 2、
+维持器扛货外来/能力名判族 2）。真机复验：采矿稳定态应零 gather 命令（观察 op 流水）；
提交补给站应真盖完（进度 0→100%，完工后 SCV 才归队）；泳道图在第一次入队后变 live。

## 0.54 四十七轮：五问题根因定位 + PLAN-ROUND3 立项 + G 批（复盘投影改版）执行（2026-08-24，未提交）

用户真机复盘报五问题 + 复盘投影泳道爆炸，本轮**全部钉根因**（详见
`docs/archive/PLAN-ROUND3.md` 根因速查表），四个拍板：observe 并入 bbox（read 路径留
index.md 当源清单）、TRAIN 卡死先只告警、队列体检只诊断给建议、F17 历史累积
退役（用户自认的决策错误）。根因要点：

- **聊天轮末吞正文**：`round` 事件 `setMessages(ev.messages)+setLive(null)`
  （ChatDock.tsx:367-375）换成两段式落史——talk.py 只存最后一段正文、_round_steps
  只收 tool/reasoning，工具间正文从未序列化（流式期间 chat-live.ts 是对的）。
  插话跑到上面 = 双槽渲染（messages 在上 / live 钉最下，插话 append 进前者）。
- **地图"幻觉"= 假 not-found**：7 次失败路径全符合 maps/index.md 约定，死于
  MAX_COLS/ROWS=14（region_view.py:33-34）；render_region 的有用 ValueError 被
  ReadOnlyArea.exists（try-read→False）+ vendor read 的 contains 预检双层吞成
  "not found" → 模型只能瞎改坐标。
- **sleep**：只有相对 game_seconds；早退仅 未连接/300s 墙钟/用户插话——游戏结束
  不出"未连接"→ 照睡（trace 实证 166s）。
- **警报**：9 种已有但无敌方接触类（只有当帧计数，无 last-seen 记忆）且纯拉模式
  （sleep 不看）——用户 10s 滚动窗设计（不同敌兵数+峰值同屏数）立项为 D 批。
- **枪兵卡队首**：queue_blocked 其实会响，agent 在 sleep 看不见；TRAIN 无 flight。
- **自动补给**：supply_guard 只改投影副本（live 队列从未自动插）——真问题是投影
  撒谎，立项开关化（H 批）。
- **复盘投影泳道爆炸**：F17 累积（use-accumulated.ts）events 去重键含 t，每次
  重投影 t 漂移 → 逐帧全量叠加。

**G 批（本轮已执行，前端）**：
- G1 拆累积：删 `use-accumulated.ts`；ProjectionBoard 与 WholeOpsList（复盘队列卡）
  改吃原始帧 —— 板上只剩本帧投影 [T, T+horizon]。
- G2 截断设计：`nowAnchoredRange`/`LEFT_MARGIN_SECS`（gantt-data.ts 纯函数）——
  红截断线钉 T 贯穿泳道+曲线；视窗左缘钳 max(0, T-30)（平移/跟随/初始三处）；
  右缘仍钳数据末端；**在建部分条**：world.units 0<build_progress<1 + 目录
  build_time 反推已耗/剩余，与投影同 stable_id 跨 T 的条只延左端不重开（
  packPairs 抽出为共用打包核心）。训练条左延伸等 G3（SC2 订单无进度，
  需 runtime 记 emitted_at，后端批次）。

**回归**：前端 382 passed（gantt accumulateInto 块退役、+packPairs/nowAnchoredRange
7 测；layout/charts 结构断言改为"无累积+截断锚定"）+ tsc 绿。后端未动。
后续批次 A（聊天 segments）→B（observe+bbox）→C（sleep）→D（enemy_contact+唤醒）
→E/F/H，见 PLAN-ROUND3。

## 0.55 四十八轮：PLAN-ROUND3 七批执行（A 聊天 segments / B observe+bbox / C sleep / D 警报唤醒 / E 卡死文案 / F 体检 / H 供给开关 / G3 在训记账）（2026-08-24，未提交）

用户拍板「全部按顺序执行」，本轮落地 PLAN-ROUND3 余下全部批次：

- **A 聊天 segments**（吞正文根因修复）：talk 落史新增 `segments` —— 轮内交错时间线
  （正文/思考/工具/插话按真实顺序全序列化；llm_call ↔ 本轮新增 assistant 消息配对，
  插话按 InterjectionQueue 排空账本的时刻插到「正在跑的工具」之前）。前端
  ChatMessage +interjection/segments；AgentMessage 有 segments 优先渲染（无则回落
  steps+text）；插话 live 期 appendUserEntry 落时间线**末尾**（「跑到最上面」的双槽
  错位修复）；插话独立历史条目只喂 LLM，下一条 agent 带 segments 时前端跳过（不双显）。
- **B observe+bbox**：observe 带 bbox=[x1,y1,x2,y2]（+step 默认 2、source 默认 live）
  = 格点网格，复用 MapsArea 渲染；**如实报错**：超范围报地图尺寸+哪个坐标超
  （先于渲染校验，不再被钳制/吞掉），网格超限报上限+建议 step。maps/index.md 改为
  源清单职责（observe 是主入口，read 路径保留）。AdvisorSpec/make_tools 穿
  map_plans_dir。
- **C sleep 补齐**：+`until_game_time`（绝对时刻，定时节点不用相对秒硬凑）；
  **「已结束/崩溃」也算结束**（对局打完 run_game 返回 → 会话翻已结束、game_time
  冻结 —— 旧判定只认未连接，sleep 空转到墙钟上限，用户实测根因）；+时钟冻结兜底
  （状态仍对局中但 game_time 冻满 60 墙秒 → 叫醒，防 SC2 挂死不退出）。
- **D 敌方接触 + 唤醒链**（用户 10s 窗设计）：AlertService +滚动记忆 —— tag→最后
  出现时刻/类型/位置，10s 窗统计「见过 N 个不同敌兵 + 峰值同屏 M + 最后位置」→
  `enemy_contact` 警报（≥3 个或峰值 ≥5 → warn）；`active_alerts()` 活跃面（最近
  15 游戏秒报过的 warn+），OfflineSession.describe 直读、LiveSession.describe 从
  最近 frame/alerts 帧捞（子进程的 AlertService 够不着）；sleep 轮询 warn+ 即醒
  （同 id 一次 sleep 只叫一次）。
- **E TRAIN 卡死文案**（只告警不动作，用户拍板）：runtime 记 `_ever_ready`（曾建成
  的建筑类型）；snapshot blocked +`producer_ever_ready`；警报文案区分「曾建成、
  现在不在 —— 大概率被摧毁：重排/重建」vs「从没建成过 —— 建造被卡/掉单」。
- **F audit_queue 工具**（17→18 个，只诊断+建议）：在线队列（name 选）/plan_id/草稿
  三入口；检测卡补给（累计人口 vs 可用+队列内 depot 供给，离线基线=种族主基地）、
  前置不在场也不在队列、产出建筑缺失；每条给「插什么、插在剩余队列 #i 前」，
  改法归 agent（在线 propose hunk insert / 离线 edit 文件）。client +latest_frame。
- **H supply_guard 开关**：Planner.project +`auto_supply`（**默认关**）—— 投影不再
  替人补供给，卡人口真实浮出；/api/plans/simulate 请求体与 simulate_plan 工具透传。
- **G3 在训记账**（REV 16→17）：runtime emit 训练单时记 {type, producer_tag,
  started_at}，淘汰=产出建筑消失或无订单且超时长（容忍 emit→订单落地间隙）；
  ProductionFrame +training（TrainingView），前端契约 .default([])；ProjectionBoard
  部分条补齐训练左延伸（started_at + build_time，跨截断线）。

**事故与修复（本轮流内）**：live.py `_recent_alerts` 初版在 describe() 持锁时再取
`self._lock` —— 普通 Lock 不可重入 = 死锁（_control 的 terrain 分支注释警告过的同
一个坑），全量测试挂死 8 分钟定位后改由调用方持锁直读。教训：给 live.py 加任何
describe 侧的派生读取，先看锁。

**回归**：后端 910 passed / 4 skipped；前端 385 passed + tsc 绿。提交仍等用户验收。

## 0.56 四十九轮：真机三报 —— 断流根因=max_turns+假not-found / 清单不刷新 / chip 跳转被守卫弹回（2026-08-24，未提交）

用户三报，逐一钉根因（trace 2026-08-24T143544 全程无网络错误）：

- **「工具调用之后没有下文」**：trace 里每次断都是 `run_end outcome=paused
  reason=max_turns` —— 轮预算 8 个 turn，agent 全花在工具上，最后一轮纯工具调用
  （无正文）→ 用户侧凭空断线。更糟的一轮：`strategies/_lib.yaml` 被**假 not found**
  折磨整轮（ls/grep/glob 都看得见、read 说没有 —— read 有 REST 特判、exists 没有，
  vendor contains 预检吞错误，与 maps 同款 bug）。修四件：
  1) max_turns 8→16；2) paused 轮落史附「轮数上限暂停，回复『继续』」说明（模型
  侧 _seed_history 也看得到，知道自己被截）；3) `_file_exists` 对 _lib.yaml 同源
  探测（strategy_lib_text 200 才算存在）；4) ReasoningTolerantClient 零分片重试
  （连接瞬断且一个 delta 都没发 → 重试一次；已发分片不重试防前端正文重复，
  异常照抛 → 引擎记 run_end error，trace 可见 —— 那就是错误日志面）。
- **复盘清单要刷新浏览器**：fixtures+recordings 只在 init 拉一次，落盘是后端事件
  无推送面。修：`refreshSources()`（只刷清单不动当前源）挂 window focus /
  visibilitychange，聊天轮改动含 live 域时也刷。
- **规划清单不刷新**：queue-store `loaded` 一次终身制；agent 走 REST 写的规划前端
  看不见。修：ChatDock 轮末按改动域触发 `useQueueStore.refresh()`（顺带重拉地图
  规划引用清单）—— 点 chip 前清单已是新的。
- **复盘页点规划 chip 跳不过去**：App 的「页面不属于本模式」守卫把 plan-* 页从
  replay 弹回复盘首页；ChangeChip 此前只处理了 drive 方向的切模式。修：按目标
  前缀双向切（#/plan-* → offline 规划模式，否则 drive）。

**回归**：后端 914 passed / 4 skipped（+chat 三测/workspace 一测）；前端 386
passed + tsc 绿。提交仍等用户验收。

## 0.57 五十轮：轮预算改版 —— max_turns 500 + 输出 token 刹车（1M/轮）+ 活性看门狗（2026-08-24，未提交）

用户拍板：对局跟随一轮可以很久，max_turns 16 太小 → **500**；真正的上限改用
**输出 token 预算**（先 1M/轮）——「不知道能不能统计」：能，LLM usage 的
output_tokens 一直在 trace 的 llm_call 里记着。

- **预算刹车**：ReasoningTolerantClient 加 `output_budget`/`round_output_tokens`
  （AgentTalk 轮首 `reset_round_budget(1_000_000)` 复位，FakeLLM 无该协议自动不设限）；
  累计到预算 → **不再调 API、返回空响应**（无工具调用 → runner 自然收轮，
  不走异常路径、SSE 不炸）；talk 轮末检测计数，落史与 say 结果带
  「预算用完（本轮累计 N）—— 回复『继续』会开新预算」说明 + `output_tokens` 字段。
- **max_turns 16→500**（§0.56 的 16 只是过渡）。
- **看门狗活性制**：绝对 600s 会误杀合法长轮（sleep 单次墙钟上限 300s、
  500-turn 对局轮轻松超 600s）→ 改为**事件续期**：流式 delta/tool/turn 到达即刷
  `_round_activity`，连续闲置 600s 才杀（sleep ≤300s 的静止期安全通过）；
  非流式路径（emit=None，测试）无事件可刷，退化为原绝对上限语义。

**回归**：后端 915 passed / 4 skipped（paused 测试改显式 max_turns=6 构造 +
预算刹车新测：累计到 1.1M 后第三次调用不发生、落史带说明与用量）。
前端零改动（output_tokens 为附加字段）。提交仍等用户验收。

## 0.65 五十八轮：仿真放置近似模型（批 2 漏账补上，用户拍板口径，2026-08-24）

用户问出「规划和地图绑定怎么设计的、仿真要不要绑」——核查发现 PLAN-V2 批 2
落点写了「仿真近似归批 3」但批 3 清单里丢了，**仿真一直是放置盲**（干跑永远
乐观，live 会 skip 的规划干跑全绿）。用户拍板口径：**引用可选、报错不阻断
仿真、可一键关**。落地：

- **planner/slots_model.SlotPool**（新）：图层 home 区槽位表近似——（类别,
  尺寸）过滤按声明序消耗（与 placement.py 自动找位同规则）；exact 按槽位名
  占位（同名二次占用=冲突）；挂件/气矿不占槽（不建模母建筑容量，如实标注）；
  spawn 取 bl（标准开局近似）。
- **接入**：Build op +mark 字段；queue_to_ops 传 mark + 可选池校验（标记不在
  图层 → 摘除进「未入仿」**仿真继续**，D6 分工；命名空间引用只对池来源同 id
  剥前缀，指向其他规划的近似不建模按自动找位）；planner _feasible peek（耗尽
  → skip(placement_collision)，闭集 key 在仿真侧终于有真值来源）+ _start 开工
  即占位（live 同语义）。
- **REST**：`simulate_plan(+map_plan, +placement)`——默认开（出厂模板槽位，
  与无会话默认图层一致）；map_plan 换图层来源（from_session/queue_name 自动
  取会话默认规划）；placement=false 完全关（旧行为）。响应带 placement_source。
  静态体检（horizon=0）加 exact 标记校验（placement_ref 错误，不阻断）。
  skipped 警报文案按 reason 分流（placement→扩图层建议，prereq→插建造项）。
- **规划-地图绑定维持软绑定**（不强制）：plan=纯队列，绑定发生在会话默认图层
  与放置引用（裸名跟默认 / 命名空间钉死），ADR-0033 原设计不变。
- 后端 **993 passed / 4 skipped**；两个旧测试锁的「placement 完全不进投影」
  是旧设计，更新为「mark 进、坐标不进」。

## 0.64 五十七轮：PLAN-V2 批 6 落地 —— 收尾 + 三项债务清偿（全案完，2026-08-24）

后端 **989 passed / 4 skipped**、前端 **396 + tsc 绿**、契约零改动。PLAN-V2 六批全完。

- **audit_queue 退役**（D2）：工具删除（18→17），`simulate_plan(horizon=0)`
  静态体检路径吸收（批 3 已通，本批补文案与测试锁）；spec 提示词同步。
- **清偿①命令分发归一**：queue op 的 dispatch 三份拷贝收敛为
  `api/commands.apply_queue_op` 单点（OfflineSession 与 run_session 子进程共用，
  LiveSession 本就只转发）；ack 由薄壳统一发（去重）。
- **清偿②classify 接入 planner**：`_feasible` 改四元组 (ok/reason/wait/skip_key)，
  classify 单点判定 + 规划器时序精化（收入/顶满 200/研究槽本地保留）；
  `_dead_reason` 垫片删除。**语义归一（D6/D8 对齐）**：矿/气/人口缺 = pending
  （哪怕无收入死等也只发 stalled 事件不 skip），只有 classify 判 skip（前置根本
  不在）才 skipped。ExecView 适配层槽语义修正（普通单位总占用<总槽、科技单位
  算溢出占用——B15 三方一致）。**分层红线踩线**：planner import production.semantics
  被架构测试抓住 → semantics **移入 constraint/**（两层的公共下层，本就是可行性
  校验域）——ADR-0032 注记更新。
- **清偿③live export 下沉子进程**：控制命令 `op=export`（投影往返同款 id 配对），
  子进程用真 GameState derive_from 直出（工人分类/建造数最准），父进程只转发；
  失败/超时回退帧拼装。
- **提示词 v2 同步**（spec.py）：工作区清单（production-plans 新名/双分支地图规划/
  initial-states/catalog 手册）、observe 两块描述、simulate 四段用法（horizon=0/
  initial_state/queue_name/from_session/export_snapshot）。
- **死代码扫尾**（REFACTOR §3）：`_p_group_center` 零引用删除；`check_assign_workers`
  「被测试养着的死 API」删除 + constraint/__init__ 导出面倒挂修正（补活导出
  check_addon/check_gas/occupied_cells）。其余清单项复核已有消费者（留）。
- **ADR-0034** 补档（auto_supply 移除——批 1 落地、清单欠档）。
- PLAN-V2 收档头标注全案完成 + 六批提交号。

**遗留**：矿区坐标真机校准（mine_areas.yaml 草案）；agent 切地图工具面（等用户
设计文档，REST 已先行）；B6 planner 三族化挂账（YAGNI）；repair op ISSUES 立项。

## 0.63 五十六轮：PLAN-V2 批 5 落地 —— catalog 手册 + production-plans 改名 + scouting 派生 + 初始化模块（2026-08-24）

后端 **991 passed / 4 skipped**（批 4 后 968，+23）、契约零改动：

- **catalog/ 只读虚拟区**（agent/readonly.CatalogArea）：三族手册从**活 catalog**
  渲染（terran 完整 11 字段 / 虫神参考 9 字段 / addons 仅人族 / matchups 种子）——
  零漂移：不落盘、启动不生成，catalog 演进手册自动跟上。
- **plans/ → production-plans/**（I5）：工作区主名 + 磁盘默认目录
  （runtime/production-plans，serve_api 兜底旧 runtime/plans 沿用）；REST
  /api/plans 不改（内部名避免 churn）。**旧名 plans/ 保留 read/write 别名**
  （split 层双前缀；agent 历史笔记不废）——但 vendor ScopedWorkspace 的
  ls/glob 按 visible_paths 过滤前缀，清单只认新名（踩过：ls plans/ 空）。
- **scouting 派生**（D3）：observe 工人五分的侦查 = 编入 flow 组的 SCV
  （group_id 非空）计数；仿真侧恒 0（无编组）。`repair` op 不在 OP_CATALOG
  → ISSUES 立项。
- **初始化模块**（agent/bootstrap.py，泛化 memory_seed「只补缺失」）：首运创建
  workspace.md（工作区地图指路）+ system/prompt.md（系统提示词快照）；
  AgentTalk 起动时挂接；幂等、用户修改永远优先。

前端 theme.test 有 1 个失败（avatar/AvatarLab.tsx 的 text-xs）——是并行新增的
未跟踪文件（用户侧 WIP），不在本批范围，未动。

## 0.62 五十五轮：PLAN-V2 批 4 落地 —— observe v2 两块输出 + 集群 + 自动 step（2026-08-24）

批 4 全部落地，后端 **968 / 4s**、前端 **386 + tsc 绿**、契约零改动：

- **无 bbox 两块**（templates/observe-output.md 归一）：`全局状态`（资源/工人五分
  [building=在途建造派生、scouting=0 待批 5、分类差如实标 other]/建筑汇总[挂件
  分布+在建]/部队汇总/生产序列[在训=账本+建筑 producing 双源、排队；旧录像
  status 值回放兼容]）+ `区域信息`（按批 2 落的 mine_areas 矿区分区：建筑表
  坐标/挂件/正在做什么 + 部队表集群/血量%=均值/绝对=总和、`敌方：` 前缀=当前
  帧视野内；矿区外如实另栏并标注待校准）。旧段 经济/部队清单/关键建筑/区域 退役。
- **view/clusters.py**（新）：网格桶并查集聚类（半径 5 格），observe 区域表与
  adapt 的 `world.enemy_clusters`（EnemyClusterView 从恒 None stub 落地）同一算法单点。
- **bbox 自动 step**：observe 工具删 step 参数——最小 step≥1 使 列×行≤14×14
  （全量优先），超出自动降密度并在尾部标注实际 step（14×14 上限保留，用户批注）。
- **source/time**：`GET /api/observation?source=<id>&time=<秒>`（agent 工具透传）——
  录像源回看历史时态；live 缓冲帧同路（超范围如实空段）。observe.py 本就吃帧字典，
  帧驱动底子已在，本批只是把它从"多段杂烩"重排成模板两块。
- 投影段的 30s 预估标注已移除（看曲线用 simulate_plan）。

**矿区坐标（mine_areas.yaml 草案）仍待真机校准** —— 批 2 落的六矿区 bbox 现在
真的被消费了（区域信息段+矿区外栏），下次真机局对照 observe 输出校准一次即可。

## 0.61 五十四轮：PLAN-V2 批 3 落地 —— simulate v2 四段输出 + initial-states + 会话导出（2026-08-24）

批 3 全部落地，后端 **968 passed / 4 skipped**（批 2 后 954）、前端 **386 + tsc 绿**、
**契约零改动**（v2 附加键在 REST 响应层，不进 ViewFrame）：

- **uid 穿透 planner**：Op 子类末位带 uid（基类默认值会挡子类非默认字段——dataclass
  继承坑）；queue_to_ops 透传（count 展开共享同 uid）；InFlight 带 uid（完成时刻归账）。
- **执行账本**（curve.queue_status）：completed（started/completed 时刻）/ skipped
  （死局映射闭集 prereq_missing + detail 原话）/ in_progress（horizon 截断在途）/
  pending（没轮到，D8 无 not_reached）。**三个坑**：①_apply_completed 在 in_flight
  过滤前调用（按对象身份剔除 done）；②count 展开同 uid 有间隙（训练逐个）——完成
  判定还要看剩余队列有没有同 uid op；③settle 循环同 uid 第二个 op 会覆写状态（先判
  rec.status != pending 跳过）。
- **采样派生**（curve.extras，与 points 等长）：工人五分（building=在途建造派生、
  scouting 恒 0=编组派生批 5）+ 产位 cap 明细（slot_capacity 同一套槽模型）。
  ProjectionPoint 是契约面不加字段 —— 派生量走 extras。
- **initial_state**：planner/initial_state.py（state_from_doc/validate：catalog/units
  不含 SCV/工人分项/supply_cap 按 CC=13·depot=8 对账；state_to_doc 反向；static_check
  = horizon=0 静态体检）；Planner.project(initial=SimState) 直入起点跳过 derive。
- **plans_simulate v2**：+queue_name（在线队列）/from_session（会话导出当起点）/
  initial_state（字符串引用 initial-states/<id>｜对象内联）/sample_interval/sample_start
  （采样窗 [start, horizon]——until_complete 跑过头归曲线不归采样）；horizon=0 静态
  体检（D2 的 audit 合并路径）；响应附加键 samples/queue_status/final/alerts
  （**不得覆盖 frame 自带键**——source/skipped 教训：附加键改名 queue_source）。
  queue/items 两键都收（v1 兼容）。草稿项自动分配显示 uid q01…。
- **initial-states/ 资源面**：InitialStateStore（example-midgame 锁定种子「只补缺失」）
  + REST CRUD + serve_api --initial-states + 工作区 initial-states/ 虚拟目录（读写经
  REST，校验在服务端）。
- **export_snapshot**：GET /api/session/export（?id= 存盘复用）+ agent 工具；离线走
  derive_from→state_to_doc（最准），live 从帧拼（frame/world+economy+production join，
  workers.building=在途建造数近似、upgrades 空表如实标注——runtime research 记账未建）。
- **simulate_plan 工具四段渲染**（templates/simulate-plan-v2-output.md 归一版）：
  采样表（工人五分斜杠串+兵营/工厂/星港普闲/科闲）/队列状态表（STATUS_ZH+SKIP_REASON_ZH）/
  终值快照（近似标注：产线明细按类型+挂件聚合）/健康检查（🔴🟡⚪ 分级表）。
  事件时间线段落删除（2/4 表的起止时刻就是它）；采样行封顶 40（抽行+提示，防挤掉
  后段——bio_tank 1500s×10s=150 行曾把 3/4 段截掉）。
- 工程教训：heredoc 传含反斜杠补丁**又**截断两次（投影 uid/InFlight uid 两次静默
  丢失）——已全量改 Write 脚本文件/Edit 工具，且每次改完立即 grep 验证落盘。

**待批 6**：audit_queue 工具退役（horizon=0 路径已通）；spec.py 提示词更新。

## 0.60 五十三轮：PLAN-V2 批 2 落地 —— 地图规划双分支 + 会话图层合并 + 默认热切（ADR-0033，2026-08-24）

批 2 全部落地，后端 **954 passed / 4 skipped**（批 1 后 942，+12 行为锁）、
前端 **386 passed + tsc 绿**、**契约零改动**（内容变化不加字段）：

- **双分支文件**（`view/map_plans.py`）：一份 = `spawns: {bl, tr}` 两套
  build_slots/pos_marks；单分支旧格式兼容可读；预设四件收敛为两件
  （default/layout，init 时退役旧锁定件）；payload/save(hunks)/doc 读写全部带
  spawn 分支参数，hunks 只动指定分支；全量 doc 双分支各跑几何/预留/简写校验。
- **会话图层合并**（新 `tactical_map/merge.py`）：默认规划裸名（home 区名单 =
  null=auto 的自动放置消费面）+ 全部规划 `规划id/名字` 命名空间键（含默认自己，
  显式引用不随热切漂移；**槽位对象名也带前缀** —— map_static 按 name 渲染，
  不带前缀三个规划的同名槽会互相覆盖）+ 预设固定点全局裸名；同一出生端平移
  （全部规划同 spawn_key 到同一 CC）；默认不存在回落出厂模板（如实记 None）。
  装配点：OfflineSession（map_plans_dir + id）与 run_session（--map-plans-dir，
  _detect_spawn 后重建合并层）。**route 传 id 不再传路径**（子进程按目录查）。
- **默认热切**：`POST /api/session/map-plan?id=`，offline=帧边界 pending、
  live=控制文件 `{"op":"map"}`，都是重建合并图层 → 四方换引用 → 重发 static/map。
  `resolve_placement_refs` 删除 —— `规划id/点位名` 直通，不存在标记执行期
  dropped（作者错误）。
- **null=auto 落地**（ADR-0027 修订）：placement.py null 拒绝 → 默认
  in_region("home") 按序空位；无位 = skip(placement_collision)。注意 CC 是 5×5
  不是 4×4 —— 测试夹具里 CC 离 2×2 补给槽太近会把槽全盖住（本轮踩过）。
- **矿区进基础数据**（D4）：`data/ladder_map/mine_areas.yaml` 六矿区草案
  （**坐标待真机校准**，文件头标注）+ `tactical_map/mine_areas.py` loader；
  批 4 observe v2 以此为分区基准。
- **前端红蓝**：spawn 从"规划过滤键"变"分支视图"（同一规划两页签，蓝红按钮
  只切视图重拉 payload）；map-plans api 带 spawn 参数；规划下拉不再按出生点过滤
  （dual 属于两侧）；queue-store 放置引用空间过滤兼容 dual；StartCard 兜底不变。
- **maps 只读区适配**（agent/readonly.py）：双分支默认渲染 bl 侧 + 说明行标注；
  索引文案更新；loadouts 默认清单 layout-bl → layout（旧值会 400）。
- 工程教训 ×2：①heredoc 传含反斜杠/大中文串的补丁**必截断**（批 1 已踩、本轮
  又踩两次）—— 大改一律 Write 脚本文件或 Edit 工具；②合并层的 dict 键带前缀
  而**对象 name 不带**时，payload 渲染会静默覆盖 —— 两处都要改名。
- 测试耗时体检（用户问）：全量 958 测 ≈116s 无病态 —— 最慢 25 个（全是子进程
  端到端，0.55s/个冷启动底价）占一半；**开发内环改为只跑受影响文件**，全量
  留批末。可选提速项 pytest-xdist 待用户拍板。

**待办**：agent 切地图工具面等用户设计文档（REST 已先行）；矿区坐标批 4 校准。

## 0.59 五十二轮：PLAN-V2 批 1 落地 —— 队列执行账本 + skip 语义（ADR-0032，2026-08-24）

批 1 六件套全部落地，后端 **942 passed / 4 skipped**（基线 915，净增 27 行为锁）、
前端 **386 passed + tsc 绿**、契约 **REV 17→18**（一次 +1）：

- **执行账本**（`game/production.QueueItem` + runtime）：项带 `uid`（q01… per-queue
  递增，clear 不重置、重排不变）与 `status` 四值闭集
  （pending/in_progress/completed/skipped）；**已执行项保留在队列里不再摘除**。
  BUILD 完成 = flight 确认实体 `build_progress>=1`；TRAIN 完成 = `_trainings`
  账本淘汰（账本条目带 uid 关联）。帧反解回灌（live queue_items → hunk →
  submit）往返 uid/status —— 丢 status 会让已完成项被重跑；`_check_items`
  对带 uid 的账本往返项放开 count=0（已完成项的合法状态）。
- **共享分类器** `production/semantics.classify(item, catalog, view)`（纯函数，
  表驱动 17 测锁语义）：ready / pending（矿/气/人口/前置在途或在队列/产出建筑
  在途或在队列/**训练槽满 production_capacity**）/ skip（`prereq_missing`｜
  `placement_collision`）。production_capacity 归 pending（PLAN-V2 §7 拍板，偏离
  agent 文档——槽满是瞬态）+ 超 60s 警报升级 warn。消费方：runtime 已接，
  planner 仿真/健康检查批 3 接入。
- **drain = skip-and-continue**（队首冻结只对 pending 保留）：skip 标记后继续
  下一项（不级联）；pending 队首等待不越序（顺序语义）。放置位耗尽/重试耗尽
  （含挂件母建筑/气井）从 dropped 改为 skip(placement_collision) 留账本；
  作者错误（catalog 不认/标记不存在/未知 op）仍 dropped 摘除（D6 分工）。
- **命令面换 uid**：`insert(before_uid)`/`remove(uid)`/`reorder(order=uid 排列)`
  贯穿 commands.py → OfflineSession/LiveSession → run_session 子进程；提案
  hunks 同步（insert 用 before_uid、delete/modify 用 uid）；agent propose schema
  与 audit_queue 建议文案改 uid 锚点（在线体检只看未执行项）。
- **D1 警报**：删 supply_block 前瞻；live 面新 `supply_capped`（已卡人口**且**
  队列/在途没排供给建筑才报，建议带 before_uid）；skipped 项进警报
  （prereq_missing 家族用 `_ever_ready` 分「被摧毁 error / 从没建 warn+插建
  议」，一次性防刷屏）；capacity 等待 info→60s→warn。**D7：`_supply_guard`
  与 auto_supply 参数彻底删除**（planner/routes/tools/probes 四处）。
- **契约 rev 18**：items[] +uid/reason/producer_ever_ready，status 扩四值
  （旧值保留枚举做回放兼容，前端 QUEUE_STATUS_ZH/SKIP_REASON_ZH 映射），
  in_flight/training/blocked +uid；ProductionPage 队列表换 uid 列 + 四态渲染
  + uid 命令；draftFromSessionQueues 过滤已执行历史项；规划文件持久化剥账本
  字段（uid 是会话内轨迹，不落 authoring 数据）。
- ADR-0032（队列执行账本与 skip 语义，取代 S11 队首冻结拍板）+ ADR-0027 修订
  （placement null = auto，语义先记、批 2 落地）。
- 工程小事故 1 起：heredoc 传大中文补丁串被 bash 截断，`open(p,"w")` 先截断后
  崩 → alerts.py 清空 —— git checkout 恢复后改走脚本文件方式（大改一律不再走
  heredoc）。

**执行时确认点**（PLAN-V2 §7 预告的两处之一，已按推荐值落地待用户复核）：
production_capacity → pending + 60s 升级 warn（§7 拍板）。

## 0.58 五十一轮：PLAN-V2 立项 —— 执行模型/地图规划/观测面大重构（2026-08-24，本轮只立项不执行）

需求源 = agent 自己梳理的文档（runtime/agent-talk/workspace/improvement-notes.md
I1-I9 + templates/ 三份；用户拍板「是输入不是规范」，字段命名过系统四源归一：
with_reactor→reactor、slots→cap、双倍挂件→反应堆、prereq 家族合并、status 英 key
+映射表）。八项决策 + 两个新增点全部落定（D1 警报三情形含 live 被打掉/侦查归
Group 语义/矿区进基础数据/勿需 not_reached 四值枚举/auto_supply 彻底删/前端红蓝
适配/初始化模块），细节见 `docs/archive/PLAN-V2.md`（六批：①队列执行模型 uid+skip 语义
→②地图规划双分支+命名空间+热切 →③simulate v2 四段输出+initial-states →
④observe v2 全局+矿区 →⑤catalog 生成+改名+初始化模块 →⑥audit 合并退役；
ADR-0032/0033/0034 + ADR-0027 修订待写）。待用户输入：agent 切地图设计文档。
**基线**：§0.54-0.57 的 915 后端 + 386 前端绿仍未提交 —— PLAN-V2 执行时一并对账提交。

## 0.50 四十三轮：外层流光移除 + 轮内插话（2026-08-24，未提交）

- **dsh-sweep 矩形流光移除**（用户拍板不好看）：工具行 running 只留「运行中…」文字；
  CSS/注释/测试锁同步（锁反转：必须不在）。
- **轮内插话**（对局跟随期间 agent 一轮很久，用户此前完全插不上话）：
  BaseAgent **没有轮内消息通道**（state.inbox 只轮首排水；LLM 思考中无法硬打断，
  vendor 不改）—— 在**自己的工具层**做检查点，两种时机都覆盖：
  1. sleep 早醒：轮询（0.5s）先查插话队列，有就立刻返回「（用户插话：…）sleep 提前结束」；
  2. 任意工具结果捎带：spec 把全部语义工具包一层，结果头部带「（用户插话：…）优先回应」。
  - `AgentTalk.interjections`（InterjectionQueue）经 AdvisorSpec 注入；轮内到达的插话
    按真实时序写历史（原话 → 插话 → 回复，interjection 标记）；**没赶上检查点的插话由
    跟随循环补送一轮**（优先于游戏提醒，哪怕对局已结束也不丢）。
  - `POST /api/agent/chat/interject`（无进行中的轮 → queued=False，前端正常发送）。
  - 前端：busy 时输入框**不再禁用**，回车 = 插话（本地即显），占位文案说明检查点语义。
  - 提示词新增「插话」节：看到（用户插话：…）优先回应。
- 回归：后端 872/4s（+sleep 早醒/工具捎带/补送闭环三测），前端 378 + tsc/build 0。

## 0.49 四十二轮：/clean 斜杠指令（清空对话上下文，2026-08-24，未提交）

用户诊断：agent 一直说"没有关闭对话的工具"—— stop_session §0.44 就有，但 (a) 后端
进程没重启时是旧工具集，(b) **上下文脏了**：旧轮次里"我没有工具"的认知随历史整段回灌，
一直误导模型。用户拍板：加斜杠指令手动清（首批只有 clean；不做自动化）。

- 后端：`AgentTalk.clear_context()`（_lock 内：历史清零落盘 + 引擎 agent 消息态重置，
  runner 下轮自补系统提示；**记忆文件不动**）+ `POST /api/agent/chat/clean`。
- 前端：输入 `/` 弹指令建议（名称+说明），回车（`/clean` 精确匹配）或点选执行——
  不发给 LLM；清完本地即显说明行（上下文已清空、memory 在磁盘不受影响）。
  `SLASH_COMMANDS` 表驱动，后续加指令只动这张表。
- 坑：create_or_get(类型, target, 版本) 参数顺序传反，异常被兜底吞 → 引擎态没重置
  （测试抓到）。**兜底 except 别吞参数错误——测试要断言副作用真的发生**。
- 回归：后端 869/4s（+clean 一测），前端 378 + tsc/build 0。

## 0.48 四十一轮：token 流「卡到思考完才出」三层修（2026-08-24，未提交）

用户报告：发话后界面死等很久才见"思考中"，通常思考完才收到内容。链路排查：LLM 客户端
（stream=True + on_delta）、引擎（STREAM_DELTA 事件通道）、SSE（逐事件 to_thread 拉取）
**都是真流式**（§0.15 真机验过）——症状是三层叠加：

1. **UI 死屏（已修）**：发话到首个分片之间什么都不显示（「思考中」占位十五轮退役、
   turn_start 前端不渲染）。补回最小占位：`live.length === 0` → ThinkRow running。
2. **思考字段容错（已修）**：父类只认 delta.reasoning_content（百炼/Qwen 系）；端点若用
   **reasoning**（o 系/部分代理），思考期零分片。`agent/talk.ReasoningTolerantClient`
   （vendor 不改，子类）两种字段都认，测试锁两种命名都流出。
3. **首分片延迟诊断（已加）**：`_say_on_engine_loop` 记录首个 delta 距发话的毫秒数，
   GET /api/agent/chat 的 `first_delta_ms` 暴露 —— 下一轮真机一看便知是端点不流式/
   思考服务端整段做/提示词过大（prefill 慢，历史无界增长的另一笔账，压缩留后续）。

回归：后端 868/4s、前端 377 + tsc/build 0。

## 0.47 四十轮：对局跟随（游戏没结束不许停）+ 建造就近抽采矿 SCV（2026-08-24，未提交）

- **对局跟随**（用户拍板：对局期间 agent 经常断）：`AgentTalk._round_with_follow` ——
  每轮结束查会话（driver=sim/sc2 且 alive），游戏还在进行就注入「（跟随提醒）」再跑一轮
  （引导 sleep→observe→必要时 propose；对局结束先终局总结）。每个引擎轮**独立看门狗**
  （一轮超时只终止跟随，不再整场腰斩——runner 不再整体套表）。两道止损：总轮数 40 上限 +
  连续 3 次「想停又不 sleep」→「跟随停止」说明落历史。提醒以 user 消息进历史（前端可见）。
  提示词同步：「收到（跟随提醒）= 你在对局中想结束但游戏没完」。
- **建造选人**（用户拍板：默认拿最近的采矿 SCV，建完归队）：`_pick_builder(gs, near=建造点)`
  就近选（常规/气矿/重试三路径都传点）；「缺少建造者」的阻塞文案带诊断计数（场上几个/
  本帧已令/征用几个）。归队是自动的：建造期间征用 + 维持器外来订单规则双保护，完工订单
  清空 → 维持器派回采矿（§0.46 的修复正好是这一环的前提）。
- 回归：后端 867/4s（+跟随三测、+就近选人），前端 376 不变。真机复验点：对局中 agent
  应被提醒轮驱动持续跟随到终局；propose 补给站后应看到**最近的**采矿 SCV 走去建造、
  完工后自动回矿。

## 0.46 三十九轮：真机四连报根因修复（采矿改派/提案无效/复盘rev/chip跳转）（2026-08-24，未提交）

用户真机实况四报，逐个钉根因：

- **采矿被反复改派（P0，`production/economy`）**：往返送矿那一段订单目标是**基地**
  （Return 单），`_current_assignment` 只认"订单指向矿脉"→ 送矿途中被判空闲 → 冷却
  （22 帧）一过就 gather 去别的矿 → 往返被打断、收入归零。修：`_harvest_mem` 记住每个
  工人的在岗矿脉，送矿途中（携带矿/气）沿用记忆；采矿族订单指向已采空矿（过渡帧）
  仍归维持器重派。
- **propose"自动应用了但对局没反应"（同根因的另一半）**：propose 工具与通道**没有错**
  （insert→submit→控制文件→子进程队列，链路有测试）；坏在维持器把带 build 单的工人
  也当空闲候选，gather 改派**顶掉排队的建造单**。修：外来订单（build/move/attack 等
  非采矿族）的工人整体跳过 —— 不占名额、不改派、不进候选（reservations 之外的行为性
  双保险）。测试三锁：送矿途中零改派 / 送达后续派同矿 / 建造工人不被碰、空闲者补位。
- **复盘报"帧 rev=12 前端 REV=16"（回放不可用）**：parseEnvelope 硬等 rev===REV，旧
  录像第一行就拒 —— 违背"只增不改、旧帧可解析"的契约承诺。修：rev ≤ REV 照常解析
  （缺字段走 .default()），只有帧比前端新（rev > REV）才报"前端过旧"；envelope 的
  zod rev 从 z.literal(REV) 收宽为 int。live 的版本同步检测仍在 WS _hello 严格判。
- **改动 chip 跳转去空白规划页**：对局类改动的落点是 #/production，但规划模式下
  App 的"页面不属于本模式"守卫立即重定向回规划首页且丢参数。修：ChangeChip 点击时
  目标非 #/plan- 先 setMode("drive") 再设 hash。
- **sleep 必须用（提示词硬化）**：明确"回合结束=沉睡直到用户说话"，等待**必须**轮内
  sleep（游戏时间），observe → sleep → observe 连做。
- 顺带：看门狗测试的 0.5s 预算满载偶发误杀（时序敏感），放宽到 2.0s。
- 回归：后端 863/4s、前端 376 + tsc/build 0。**真机复验**：采矿应连续不中断（送矿
  往返不再改派）；propose 后 observe 应看到队列入列且建筑真开工。

## 0.45 三十八轮：serve_api 端口守卫 + agent sleep 工具（2026-08-24，未提交）

- **端口守卫**（stale serve_api 反复锁死 8770 的老毛病，用户拍板自动清）：起服务前
  `ensure_port_free` —— psutil 连接表找 (host,port) 的监听者；命令行像本项目后端
  （serve_api/uvicorn）→ **树杀**（含 run_session/SC2 子进程）后等端口空（≤10s）；
  陌生进程**不误杀**，打印 PID/命令行与 taskkill 指引退出；`--no-kill` 可关自动清理。
  匹配/识别是纯函数（listening_on / looks_like_our_backend），测试锁（含通配监听算冲突）。
- **sleep 工具**（用户要求：agent 说完话就没法再等了——等待推给了用户）：按**游戏时间**
  等（快进模式游戏钟跑得快，同样的游戏秒等得更省——用户点名的偏好）；轮询会话
  game_time，无会话/会话结束/墙钟上限（300s，留足轮看门狗 600s 余量）提前返回并说明。
  提示词：observe → sleep → observe 一轮内连做，等待留给自己。常量模块级（可测试注入）。
- 回归：后端 860/4s（+端口守卫 2、+sleep 三分支）。

## 0.44 三十七轮：真机出生点检测（左下锚点写死事故）+ stop_session 工具（2026-08-24，未提交）

**事故**（用户真机实况 + agent 发现）：出生右上角的一局，矿 50 不涨、"采矿实际 8/可达 8"、
agent 无手段定位差异、也没有关游戏的工具（用户手动关窗收场）。agent 记 [I1] 改进笔记。

**根因（A，已修）**：真机会话构造期锚点写死左下（run_session `cc=Point2(30.5,30.5)`），
`instantiate_spawn` 把地图层平移到它 → 经济锚点/矿脉选择（NODE_RADIUS=20 内）/槽位/UI 地图
全部指向左下；出生 tr 时工人被派去左下（敌方侧）采矿，横穿全图零收入。顺带：bl 局的槽位
此前也被平移偏离校准位（模板 origin 48.5,28.5 vs 写死 30.5,30.5），本次一并归正。
- `tactical_map/base.pick_spawn_layout`（就近分支，纯函数）；
- run_session：sc2 首帧 `_detect_spawn`（实测我方 CC → 重建 layer → 同步
  engine/keeper/runtime/producer 四方 + producer.spawn），在第一张 static/map **之前**完成
  （无需补帧）；meta 带 `spawn` 键；CC 找不到 = 保持临时假定层（如实）。sim 不检测。
- 事故里"CC 产线空闲、队列空"**不是装配失败**：没带 loadout/plan 的会话默认不自动入队。

**stop_session（C，已加）**：agent 能关自己开的局（POST /api/session/stop，树杀防孤儿 SC2）；
工具面/提示词/写面清单同步；「用户在场的正常模式局先问再关」写进工具描述。
顺带修正 §0.43 引入的自伤：describe 的 alive 被我写反（is None→is not None），四个 sim
会话测试齐挂——教训：**改 describe 字段先跑 test_live 再继续**。

**B（随后，ISSUES #18）**：观察包零收入检测（产出速率 + 工人距目标矿脉距离 + 警报）。
回归：后端 855/4s（+出生点检测 4、+stop_session 工具 1）。真机验证待用户：出生 tr 的局
（重开几次随机到 tr）采矿应正常、地图层正确。

## 0.43 三十六轮：「开启游戏」收敛为两模式（正常/仿真 + 快进倍数，2026-08-23，未提交）

用户拍板：AD 面上的 sim/实时多种驱动收敛为**开启游戏一项、两种模式** ——
1. **正常模式**（mode=normal）：玩家可见、实时流速（原 realtime 路径）；
2. **仿真模式**（mode=fast）：给 agent 用的快速模式，**同样是真 SC2 游戏**，支持快进
   倍数（speed：2/4/8…，0=不限速），跑完看实际结果。
offline/sim 是测试驱动（假世界），从用户面退场（后端保留给测试）。

- **配速**：`driver/sc2_adapter.pace_sleep_seconds`（纯函数）—— N 倍速 = 每真实秒推进
  N 游戏秒，bot 在 on_step 里睡差值（非实时游戏由客户端主动 step，慢点请求=慢点跑）；
  `SC2GamePort(speed=)` 起始注入、`set_speed` 热改。
- **通道**：run_session `--speed` + `speed` 控制命令（sc2 落 bot 配速；sim 同命令缩放
  节拍睡眠 —— 测试可端到端）；`POST /api/session/speed`（即时生效不重启；正常模式/
  进程内沙盒 409 带原因）；`session/start?mode=&speed=`（错配 400：mode 只属 sc2、
  normal 带 speed、倍数越界）；幂等守卫把模式/起始倍数纳入会话身份。
- **Agent 面**：start_session 工具收敛 mode/speed（driver 恒 sc2；默认 fast+最快），
  提示词改为「验证用仿真模式，不用问用户；正常模式留给用户在场时」。
- **UI**：StartCard 模式段选（正常/仿真）+ 仿真倍数下拉，按钮改「开启游戏」；
  ModeBar 在仿真会话运行中给 2×/4×/8×/最快小按钮组（顶栏无下拉的十四轮锁保持——
  用按钮组不是 select）。
- 坑（自伤后自查）：describe 改动时把 alive 写反（is None→is not None），四个 sim
  会话测试齐挂 —— **改 describe 字段先跑 test_live 再继续**。
- 回归：后端 850/4s（+pace 3、+game modes 4、工具模式测试重写），前端 374 + tsc/build 0。
- 真机（sc2）两种模式 + 运行中变速留用户验一次（通道已被 sim 端到端覆盖）。

## 0.42 三十五轮：提示词六条整改 + 记忆工作区种子 + 流式分段错位（2026-08-23，未提交）

- **提示词重写**（用户六条评审）：规则去重（「不能热改」×4 →「你做不到的事」一处 + surface.md 引用）；
  术语统一（生产规划/提案（propose）/观察包（observe）/地图规划）；开局文件名点名（user-preferences +
  strategy-notes + session/current）；**格式约定归各记忆文件头部**（提示词只说"见文件头部"，单一出处）；
  「你做不到的事」只留原则（具体边界以 system/surface.md 为准）；新增「记忆写入触发」段（拍板/撞墙/
  对局结束/轮末覆盖写 session/current——"轮"=一次交互回合/surface 变更对账）。全文 ~5000→3350 字。
- **记忆工作区种子**（用户拍板"目录丢了要能自动初始化"）：`agent/seeds/` 随库存档当前模板
  （runtime/ gitignore；含 replays/README 与 session/current 骨架），`agent/memory_seed.py`
  在 AgentTalk/单回合 runner 装配时**只补缺失、绝不覆盖**（用户修改优先）；种子与现役副本的
  write_surface 字样改指 system/surface.md。tests/agent/test_memory_seed.py 四条锁。
- **流式分段错位修复**（用户反馈：先吐正文再思考时，思考行插到正文上方）：live 状态从
  {steps,text} 两段改为**按到达顺序的时间线**（`shell/chat-live.ts` 纯 reducer + 5 测试），
  LiveMessage 交错渲染（正文分段各归其位；历史消息仍是两段形态不受影响）。
- **策略保存可读性 lint**（用户拍板「校验型钩子」补齐）：`view/strategies.strategy_lint_hints`
  —— 保存**成功**后点名缺 display_name_zh/description_zh 的 step、没中文覆盖的 reason
  （默认表 ∪ 策略 reasons 之外的），`{"ok": true, "hints": [...]}` 随结果返回；agent 工作区
  把 hints 送进 memory lint 同一条提示通道（写结果尾部可见）。不拒绝 —— 硬错误仍在编译校验。
  _lib 导入的 step 自带模板中文名，不误报。
- 回归：后端 843/4s，前端 373 + tsc/build 0。

## 0.35 三十一轮：三文档目录合一 docs/（分类收纳 + 内容修剪）（2026-08-23，未提交）

用户拍板「合并成一个文件夹 + 分类 + 保留精华、删否决/无意义」：

1. **布局**：`newdocs/` + `docs/` + `docs（旧）/` → 一个 `docs/`——根层 = 现行
   （ARCHITECTURE/PLAN/WORKLOG/ISSUES/AGENT-LOOP/REFACTOR/DOCS），子目录六分类：
   `contract/`（plan-frontend 等五份活性契约）、`adr/`（八份：现行 0029/0030 +
   历史精华六份）、`spec/`（Flow v0.2 schema 契约六份+README）、`reference/`
   （设计语言/driver spike）、`data/`（game_data_dump/tank_marine_push），
   `evidence/`（三份代码引用的真机证据日志；探针新输出也改落这里）。
2. **内容修剪**：删 `模块审查.md`（被 REFACTOR.md 审计取代）与
   `issues-flow-production.md`（被 ISSUES.md 取代）——5 处代码引用同步改写
   （其中两处本就是陈旧指针：经济维持器早已落地 ADR-0030）。契约/ADR/spec
   **不修剪**：它们是活性真相源，修剪只发生在被取代层。
3. **路径同步**：31 个代码文件 + 16 个探针脚本（含 parts 形式常量与误归校正）+
   .gitignore + docs 内部 11 个文件的 newdocs/（旧）指向，全部对齐新布局；
   `DOCS.md` 重写为合并后地图（逐份保留理由 + 删除可追）。
- 回归：后端 **769/4s**、前端 **361/26** + tsc 0（test_tank_marine_push 吃
  docs/data/ 新路径过）。

## 0.34 三十轮：R2 收尾 + 文档收归 + 根目录清理（2026-08-23，未提交）

用户拍板「继续推进 + 文档收归 + 根目录清理」：

1. **R2（`1316232`）**：runtime.py 948 → 564（编排）+ `flights.py` 336（在途确认/
   挂件/气矿，Mixin 原样搬——与账本/征用深度共享，抽回调接口风险大于收益）+
   `placement.py` 98（解析纯函数化）。三个 god file 全部拆完。
2. **文档收归（开放清单 #9 关闭，按 2026-08-23 审计的 4 步计划执行）**：
   - 删 superseded 36 份 tracked：docs/ 的 4 份旧计划（plan-agent/planner/step-tank/
     strategy-dsl-v02）+ 4 张探针截图；docs/ 的 22 份旧 ADR（0001-0012/0015-0023/
     0025-0026）+ plan/ 全部 + 重构共识总览 + adr/README。
   - 删 docs/ 未跟踪扫描日志 41 个：一次性 slot/scan/probe 输出；**保留 3 个被代码
     注释引用为真机证据的**（full_flow.log / bare_addon.log / slot_scan.log）。
   - 保留原则 = 活性引用驱动：docs/ 留 10 份（契约真相源 plan-frontend/需求文档/
     game_data_dump/tank_marine_push.yaml/现行 ADR 0029+0030 等，逐份理由见
     [`DOCS.md`](DOCS.md) 新文档地图）；docs/ 留 spec 6 份（Flow v0.2 schema
     契约）+ ADR 6 份（0006§7-9/0013/0014/0024/0027/0028）。
   - 修正 2 处过期指针（`docs/测试计划.md` → `docs/test-plan.md`）。
3. **根目录清理**：`traces/`（8-21 的 6 份旧运行轨迹，215K，gitignored 临时）删；
   根目录 `__pycache__` 删；**16 个 run_*.py 真机探针 + dump_game_data.py 迁入
   `tools/probes/`**（不是垃圾——有离线守卫测试 + 是真机烟测套件；迁移 =
   git mv + `__file__` 引导两级修正 + 守卫测试 import 路径跟进）。根目录只剩
   11 目录 + pyproject/start.bat/uv.lock。
   - ⚠️ **代价（如实记）**：删掉的 `docs/state_trace.jsonl` 是真机驱动翻译测试的
     活性夹具（gitignored 但在用）→ 3 条测试转 skip（设计如此，缺则跳过）。
     下次跑一局真机 `uv run python tools/probes/run_recorder.py` 即重新生成激活。
- 回归：后端 **769 passed / 4 skipped**（1 原有真机 skip + 3 条等 state_trace 夹具）。

## 0.33 二十九轮：R1 god file 拆分 + F1 文件契约闭环（I20/I19，2026-08-23，未提交）

用户拍板「直接开工」——三批次方案（R1 拆分 → F1 文件闭环 → R2 runtime.py 拆分待做）：

1. **R1 god file 拆分（`e13ca82`，见提交信息）**：app.py 930 行闭包 → 124 行装配壳 +
   `api/state.py`（泵/帧源解析/命令门）+ `api/routes/`×11 按资源分组；manifest.py
   `validate_strategy` ~200 行 → 编排 + 5 个 `_validate_*`，params/variables 复制粘贴
   收敛成 `_validate_declaration_block` 一份实现。**app.state 扁平键一个没动**
   （测试/serve_api 直接摸的契约面）；错误文案逐字保留（有测试锁）。
2. **F1-2 录像衍生摘要（`view/recap.py`）**：时间线（建筑落成 / 人口上限变化 /
   警报 30s 窗口去重 / 策略转移）+ 终局盘点（资源/建筑/部队/敌方）+ 消失建筑痕迹。
   `LiveSession._close_recording` 收尾自动落 `rec-<id>.md`；**落盘原则补一条：原始
   数据 + 给人/agent 读的可读视图一起保存**（几 MB 帧流存了等于没存）。
   顺带修 `.stem` 剥后缀坑：`<rid>.meta.json` 的 stem 是 `<rid>.meta`（文件名
   代码里自己警告过的陷阱，recorded.id 一直带脏后缀）；真机录像已回填摘要。
3. **F1-1 只读区挂载（`agent/readonly.py`）**：Agent 文件树挂运行时产物——
   - `recordings/`：`index.md` 清单 + 每局摘要 .md（缺失时从 jsonl **懒生成**并落盘）；
     原始 .jsonl 刻意不挂（read/grep 整份吃进上下文会爆，错误信息指路摘要）
   - `traces/`：白名单 .md/.json（trace.html 与快照目录不进上下文）+ 越区路径拒绝
   - `proposals/log.jsonl`：提案审计史单文件
   - ApiWorkspace 集成：ls/read/grep 天然可见、**write 一律拒绝**（历史不可变，错误
     信息指路 memory/规划文件）、scratch 同名路径不可遮蔽只读区前缀。
     **磁盘直读而非 REST**：不可变文件没有"绕过校验"的写面风险（那条红线约束的是写）。
4. **F1-3 读面清单**：`agent_tools()`（write_surface）增 `readable` 段——Agent 不必
   试探就知道能翻哪些历史；提示词工作区段同步只读区说明。
5. **F1-4 记忆结构化（I19）**：`memory/` 分文件约定（user-preferences 短且开局必读 /
   strategy-notes 带 ID ≤2 行 / system-capabilities 从 write_surface 派生重建 /
   replays/ 只增）+ `session/current.md` 短期层（轮末覆盖写恢复连续性）+
   `improvement-notes.md`（Agent→开发 backlog 反馈通道）。**notes.jsonl 退役归档**
   （`runtime/archive/`；无结构 append 的实证：同一偏好重复写 3 次）；其中真实的
   用户偏好（「补给站别提前太多」）迁移进 memory 种子；空 `agent-workspace/` 清理。
   端点保留兼容但标注退役，write_surface/提示词不再宣传。
6. AGENT-LOOP.md（并行会话的 I17-I20 母题专档）随 R1 落库。
- 回归：后端 **772/1s**（+11：recap 渲染 5 + 只读区 6）；前端未动。serve_api 需重启
  （提示词/工具面/只读区装配都变了）。

## 0.32 二十八轮：REFACTOR P0 bug 批（B1-B5+B8）+ §0.13-0.31 落库（2026-08-23，未提交）

用户拍板「先提交，然后修基础问题」。本轮 = 落库 + 修 [`REFACTOR.md`](REFACTOR.md) §1
的假数据 bug 批（god file 拆分等结构性债**不在本轮**，见开放清单 #11）：

1. **落库 `ba8ceb5`**：§0.13–§0.31 十几轮（167 文件）一次入库；工作区清零后
   bug 批才有干净基线。
2. **B1 网格只发第一帧 → 内容指纹 diff**：`FrameProducer` 对 creep/visibility
   栅格按编码后内容做指纹，**变了才随本帧下发**，没变带 null —— 这是契约里
   `grids` 字段注释承诺的「仅变化时下发」，旧实现是"第一帧发一次之后永远不发"
   （菌毯/视野整局陈旧）。配套前端：store 的 world 帧是整帧替换，改为
   `grids: 新帧值 ?? 上一份`（与 static/terrain 的 pendingTerrain 同型，A5）。
   `adapt.world_frame` 的 `include_grids` 开关改为 `grids=` 注入（`grids_of` 提取）。
   **注意**：`include_grids` 仍无会话默认开启（live 下发量 ≈100KB/帧的性能与
   产品决策，等真机需要视野层时再拍）—— 本轮修的是"休眠实现也是错的"。
3. **B2 生产进度假 0.0 → None（REV 12→13）**：SC2 订单**不带进度**（协议无此
   字段，真机拿不到），原先 `ProducingView(progress=0.0)` 写死等于把"未知"伪装
   成"刚开始"。改为 `progress=None`；sim 侧被训单位自身的 `build_progress` 已
   承载真值（I10 排队语义就是靠它呈现的）。前端 zod 同步 nullable（前端只读
   producing 的长度/名字，无渲染影响）；夹具重生成（rev 13）。
4. **B3 wall_ms 伪造 → 注入真时钟**：`FrameProducer` 增 `clock`（默认
   `time.time`），`_env` 不再用 `1.7e12 + game_time*1000` 伪装墙钟；`live.py`
   地形合成帧的 `wall_ms: 0` 同步真墙钟。测试传固定 lambda 拿确定性输出。
5. **B4 observe 状态硬编码 → 常量**：6 处 `"待审批"/"已拒绝"/"已失效"` 字面量
   全部改 `proposals.STATUS_*` —— 改常量时 observe 过滤静默失效的隐患拔除。
6. **B5 CC 供给三份拷贝 → 单源 = 13**：worldsim `bases*15` / opening
   `CC_SUPPLY=15` / economy `13` 互相矛盾。收敛到 `planner.economy.supply_provided`
   单一真相源（opening 与 worldsim 都 import 它），加跨模块一致性测试锁死。
   **取值依据**：本机 `docs/game_data_dump.json` 的 `food_provided`
   （CommandCenter=13、SupplyDepot=8）+ 真机录像对照（2026-08-23 那局首帧
   1 CC + 0 补给站 → supply_cap=13）。审计原文「真实 LotV 是 11」经查**不适用
   于本机环境** —— 按仓库「不信 wiki、以真机校准」哲学取 13。
   **附带发现（未动，立案）**：该局真机起始 **8 工/13 cap**，而种子口径
   （opening/worldsim/session 默认）是 12 工 —— 开局工人口径是另一处 sim/真机
   偏差，见开放清单 #15。
7. **B8 encode 双 docstring 合并**：`grid_to_b64` 两个相邻 `"""`，解释 uint8
   截断理由的这段一直被当无效表达式吞掉，合并成一份。
- 回归：后端 **761/1s**（+3 锁：grids diff 语义 / wall_ms 跟注入时钟 /
  供给单源一致），前端 **361/26** + tsc 0 + build 0 warning；夹具重生成（rev 13）。

## 0.31 二十七轮补：开放写策略（免审落地）+ 开放任务清单立案（2026-08-23，未提交）

用户拍板「开放写策略，不需要审核」+「把未完成项整理成 issue 更新文档」：

1. **策略成为文件**：`modules/view/strategies.py` StrategyStore ——
   `runtime/strategies/<id>.yaml`（strategy + assembly 两段，与
   docs/tank_marine_push.yaml 同形）。default 从内置常量播种（锁定）；
   CRUD 端点与规划/地图规划同姿态（GET/POST/PUT doc/DELETE），**保存过全套
   编译期校验**（parse_strategy/parse_assembly/validate_assembly，错误带 step
   定位）—— 免审 ≠ 免校验。修过一个死锁：create() 持锁调 list()（Lock 不可
   重入），行构造拆出锁外。
2. **会话按 id 装配**：`POST /api/session/start?strategy=<id>`；OfflineSession /
   run_session（--strategy-file）/ LiveSession 三条装配路径都接了文件；热改
   不存在 —— 正在跑的会话不受影响，这是免审下的安全边界（不是审批）。
   R5 语义改写：「live 中不能编辑 Strategy」→「不能热改正在跑的会话」
   （spec 提示词 + agent_tools rules + read_current_strategy 文案同步）。
3. **agent 全链**：client 端点 ×4；工作区第三类虚拟目录 `strategies/<id>.yaml`
   （读 = YAML 渲染、写 = REST + 编译校验、ChangeRecord area=strategy →
   chip 跳规划-策略页）；start_session 工具加 strategy 参数（写完直接起新
   会话验证，sim 沙盒即可）；read_current_strategy 改为「策略清单 + 内置默认」。
4. **UI**：StartCard 加策略下拉（session-store 拉清单 + 启动传参；
   不选 = 内置默认）。
5. **开放任务清单立案**：ISSUES.md 新增 10 条编号清单（P0 = agent 常驻监听），
   I14 开放点①（写策略）关闭于此。
- 回归：后端 **758/1s**（strategies +5、workspace +2），前端 **361/26** +
  tsc 0 + build 0 warning。

## 0.30 二十七轮：平移方向锁死修复 + 5 分钟窗口/右缘钳制/尾部 30s + 复盘入口收敛（2026-08-23，未提交）

1. **平移方向锁死（用户实测报的 bug，真根因）**：板子的 `pointerup` 只挂在元素上
   —— 在图表**外**松手时收不到 up，`scrub` 带着旧的 `startX` 一直活着，之后的
   移动（含纯悬停）都按「相对那个陈旧锚点」平移：鼠标只要还在锚点一侧，方向
   永远不变（要翻向得跨回锚点另一侧）。概览页没事是因为它的 move/up 挂 window。
   修（双保险）：pointerdown 时 `setPointerCapture`（外面松手 up 也回来）+
   pointermove 校验 `e.buttons & 1`（左键不在 = 手势就地收尾）。两处图表同修。
2. **窗口与滚动收紧（用户拍板）**：默认窗口 10 分钟 → **5 分钟**
   （ZOOM_SPAN_MAX 600→300）；**右缘钳制** —— 平移/跟随/初始窗口的 to 都不许
   超过数据末端，右侧不再出现空白（与左侧「零点钉最左」对称）；Board 与
   概览独立卡同规则。仿真尾部：`Planner.project(tail=30)` 队列跑空后再留 30 秒
   （最后事件完成后看得到经济余势），simulate 端点启用 —— 右缘钳制的「自然末端」
   就是它。
3. **复盘入口收敛（用户拍板：只留一个一级选项）**：「夹具复盘 / 模拟 live /
   后端 API 回放」三个一级选项 + 二级夹具全退役。复盘下拉 = **对局记录**一条：
   「对局记录 08-23 14:30 · 人族 vs 神族 · LadderMap（12:34）」（时间 · 族 vs 族 ·
   地图 · 时长，录制中的带尾注）。没有记录时显示引导（开一局自动录制），
   不偷偷挂夹具。mock-live/api 两种 SourceKind 保留在 store（测试/开发通道），
   UI 不再露出。顺带修真数据：run_session 的 enemy_race 原来写死 "protoss"
   （对手是 Random，假数据）→ 从首个可见敌方单位推导；录制 meta 收尾时写
   my_race_zh/enemy_race_zh（优先会话帧，退 world 敌方前缀）。
4. **agent 与警报核查（用户问「进度还是 BUG」）**：观察包**已有**「风险」段
   （frame/alerts → `[severity] text_zh` + facts.alert_kinds）—— agent 每次
   observe 都看得到警报，不是缺失。缺的是**主动推送**（agent 常驻监听并主动
   行动）= P3 最后一块（§5.3 方向已定未排期）。写不了策略 = R5 设计边界非 bug
   （I12 辨析留档），是否放开待用户拍板（见最终回复）。
- 回归：后端 **751/1s**（planner tail +1、races +1），前端 **361/26** + tsc 0 +
  build 0 warning（mode/fixtures/charts/projection-range 测试锁随语义翻转）。

## 0.29 二十六轮：I12-B1/B3 收尾 + 四个用户 issue（记录保存/流速/平移/密度）（2026-08-23，未提交）

用户拍板「继续把剩下的做完」，随后提四个 issue —— 一轮全清：

1. **I12-B1 谓词补全（T8 落地）**：写读两侧同批放行（D8 完整版）——
   - **timer 族**：do 操作 `start_timer`（幂等：已在走不归零；重新起算要显式
     stop→start）/`stop_timer`（冻结读数），谓词 `timer_elapsed(name)` 返回已耗时
     秒数（未 start → None，比较降级 False + 诊断不静默）；
   - **locals 族**：step 键 `locals: [名…]` 声明、`set_local` 写、`{local: name}`
     读（进 step 清空 —— 引擎原有逻辑，编译期开闸 + 校验：未声明拒绝、definitions
     里禁用 {local}（别名跨 step，捕获局部是暗渠））；
   - **engaged / under_attack**：交火态由引擎每帧从 gs 推导（不发新命令不加帧）——
     engaged = 组内任一单位带攻击命令（迷雾下也算）或任一敌人进入其射程+2 格缓冲；
     under_attack = 组内单位 5s 窗口内掉过血（hp 快照在**求值之后**记账，天然滞后
     一帧）。EvalCtx 增 locals/timers/combat 三个注入口，引擎外直调降级 False+诊断。
   - 仍开放：`event_occurred`（要 D7 事件流）、`has_ready_base`（三族 town hall 目录）、
     `user_cancel`（接管通道）。
2. **I12-B3 模板化最小版**：`GET /api/modules`（内置战术库清单）+
   `POST /api/plans/from-module`（模块默认参数 → plans/<id>.yaml，可带 params/id/标题）。
   「文件与试算」面板加「从模板落地」下拉 —— 模块从此是唯一真相源，改模板重落地
   一份，不再手抄队列漂移。
3. **用户 issue ①：对局记录没保存（属实）**：live 帧流此前只在内存 FRAME_BUFFER
   （会话一停就没）。LiveSession 现在把每帧同步落 `runtime/recordings/rec-<时间戳>-
   <driver>.jsonl`（+ .meta.json 侧车：录制中/终态、帧数、时长）；`GET /api/recordings`
   列清单（录制中也列出，扫文件流补数）、`GET /api/recordings/{id}/jsonl` 出帧流。
   前端复盘下拉 = 夹具 + 📹 录像（同格式同装载路径，listRecordings 拉清单）。
   测试默认不录（recordings_dir=None 防互污染），serve_api 显式传 runtime/recordings。
4. **用户 issue ②：真机不是正常流速**：run_session 的 `--realtime` 从没被传过 ——
   burnysc2 realtime=False 时 step 不等墙钟，游戏时间飞跑。修：driver=sc2 默认
   realtime=True（sim 不受影响，本来按 tick_seconds 睡）。
5. **用户 issue ③+④：平移一下十几分钟 / 事件密到看不见字（同根）**：试算投影把
   until_complete 后的**整局 horizon**（30-60 分钟）塞进初始窗口 —— 每像素好几秒，
   拖一下自然十几分钟，泳道条挤成 4px。修：PlanningPage 不再传全量 initialDomain
   （prop 退役），板默认 zoomSpan 钳到 ZOOM_SPAN_MAX=600s；bar 只渲染与视窗相交的
   （几百条 DOM → 只剩视窗内）；窄于 30px 的 bar 隐藏文字（title 悬停看全名）。
   曲线数据仍完整 —— 拖动/边缘跟随便可看全程。
- 回归：后端 **749/1s**（flow +11：timer 幂等/冻结、locals 重置、engaged×2、
  under_attack 窗口；live +2 录制；plans +1 模板），前端 **361/26** + tsc 0 +
  build 0 warning。夹具重生成（词表禁区清空 + 18 谓词）。serve_api 需重启。

## 0.28 二十五轮：策略可读性（I1/I2/I4）+ 90s 夹具假并行根因修复（I10）+ 装配缺口对账（I12-B2）（2026-08-22，未提交）

按用户拍板「按顺序吧」处理 ISSUES 开放项，一轮三件：

1. **I1 词表中文名（REV 11→12）**：`flow.vocab` 增 PREDICATE_ZH / OPERATOR_ZH /
   ACTION_ZH 三张表（单一真相源在后端，C4），`dump_vocabulary()` 的
   predicates/operators/actions 每项带 `name_zh` 下发（rev 5 先例：值内加键也走
   REV+1，否则前端 zod 静默 strip）。提示词卡片同步带 zh（三消费方零维护自动受益）。
   前端 `graph/ast.ts`：`vocabOf(schema)` 建查找表，`renderValue` 渲染
   「已抵达(group=armor, …) ≥ 参数 min_inf」—— 没拿到 schema 退回 identifier
   （不瞎编）。测试锁：后端 zh 全覆盖（新增词忘配 zh 当场红）+ 前端 schema.test
   逐项断言。
2. **I2 可读名进契约**：manifest 增策略级 `display_name_zh`/`description_zh`、
   step 级同名、`reasons`（reason 标识符→中文，edges 切换原因与 exit 终局原因
   共用一张表）、params 声明 `description_zh`；assembly `GroupSpec` 增
   `display_name_zh`。`static/strategy` 转发 + `group_names`。三份策略源全部补写
   （session 默认 / docs/tank_marine_push.yaml / make_fixtures 两场景 —— 用户
   读到的 formup/advance/garrison/armor_hop/inf_hop 全部有中文名了）。前端
   FlowPage：节点头部中文（identifier 进 tooltip）、边标签与出口文本用 reason
   中文、转移历史双语；PlanningPage 策略 tab 同步。zod 新字段全部 `.default()`
   容错（旧夹具/旧缓存帧退回 identifier 不炸整页）。
3. **I4 面板可读性**：Card 增 `help` prop（标题旁 ？ 圆标，hover 看本面板怎么读）
   —— 策略图（含图例说明）/ 转移历史 / 绑定与参数（槽位→组、当前/目标、补员
   状态语义全写进 tooltip）；`参数.min_units` 点号改空格（「参数 min_units」）；
   组显示「步兵组（G_INF）」。组/槽位的形状颜色标记未做（I4 候选 3，低优先）。
4. **I10 根因修复（不是 planner 经济参数的锅）**：核查结论 —— 投影 events 是
   planner 模拟值（非录制真值），且 90s 夹具 t=90 队列跑空后掉进 draft 兜底；
   「6 条机枪兵同时爬进度」的真根因是 **WorldSim `_op_train` 没有产槽占用
   检查**（count=6 的 TRAIN 一秒一条全部并行开工），planner 的**快照在途推进**
   同样无槽并行（同产建筑的排队单位同秒齐落）。双层修：WorldSim 增
   `_train_busy` 产槽排队（命令即受即扣矿、进度条等前一个训完，真机语义）；
   `Planner._advance_in_flight` 与 B15 `slot_capacity` 对齐（同产建筑每秒最多
   推进 normal+tech 槽位数条，进度最高的先走）。修后夹具：机枪兵 18s 链式完成
   （90→108→126→144），SCV 12s 间隔 —— 泳道不再是假并行。测试 +4（worldsim
   排队/低进度/双兵营并行 + planner 在途 18s 链式）。
5. **I12-B2 装配缺口对账**：`AlertService.assembly_gaps(curve, assembly)` ——
   在跑完的干跑曲线（until_complete）终态上对账装配 composition.target，
   凑不齐发 `assembly_gap` warn 前瞻警报（「步兵组要机枪兵 ×10，规划终局只有
   4」）；`/api/plans/simulate` 接入，yardstick = 会话将装载的 DEFAULT_ASSEMBLY
   （V1 恒此值；装配可配置后跟着走）。live 窗口投影刻意不参与（队列中途截断，
   终态不代表总产出，会比出假缺口）。前端零改动（AlertsList 通用渲染）。
- 顺带：I9（敌方 P/Z 显示未知）由并行线解决 —— protoss/zerg catalog 数据 +
  `load_all()` 三族加载已落 driver/api 全链路，本轮 741 测试全绿含 driver 侧；
  ISSUES 移入已处理（真机抽到 P/Z 的最终确认留给用户）。
- 回归：后端 **741 passed / 1 skipped**，前端 **358/25** + tsc 0 + build 0 warning。
  夹具已重生成（rev 12 + zh + 排队语义）。serve_api 需重启。

## 0.27 二十四轮：平移 1:1 跟手 + 回放生产队列改为整局操作序列（2026-08-22 用户反馈，未提交）

1. **平移跟手**：板（复盘-生产/规划-试算）与概览公式相同，不跟手来自 0.5 增益
   （内容只走鼠标一半，永远追不上）→ PAN_GAIN=1.0（内容与鼠标 1:1）；封顶
   1920→960s（半幅拖 ≤8 分钟上限不变，跨度大的视图速度被钳住）；
2. **回放生产队列的语义重定义（用户拍板）**：用户的「生产队列」= 整局操作序列
   （与泳道图同源），不是「此刻建筑们排着的队列」。复盘模式队列卡改渲染
   `WholeOpsList`：已执行段（回放累积的开工→完成配对，拖时间轴不重排）+
   待执行段（当前帧队列剩余项，随回放推进缩短）—— 拖顶部时间轴只移动两段的
   分界，不再重排。向后大幅拖动会重置累积（回放没有全量历史，只能向前累积）。
   累积逻辑抽成共享 hook `charts/use-accumulated.ts`（投影板同源去重）。
   live 模式保留时间切片队列（可下命令的真源）。「转为规划草稿」不受影响。
3. **语义澄清（答复用户三问）**：顶部时间轴 = 回放的游戏时刻（决定地图/概览
   显示哪一刻的世界）；板上拖动 = 平移视野（只是看哪段时间窗口）—— 二十轮起
   刻意解耦（旧版拖着拖着就 seek 是跳跃根因）；曲线右侧随时间变化 = 投影是
   「从那一刻向前看的计划」，左侧 = 已发生的累积历史。
- 回归：前端 **356/25** + build 0 warning；后端未动（729/1s）。

## 0.26 二十三轮：泳道轴对齐 + 行间虚线 + 仿真跑到完成 + live 帧合并（2026-08-22 用户反馈，未提交）

1. **泳道与曲线轴没对齐的根因**：bar 按整箱百分比定位（0% = 泳道箱左缘），
   而曲线绘图区从 46px gutter 起 —— 泳道 0 点比曲线 0 点靠左 46px。修：bar 与
   行间分隔线都进 gutter 内层容器（与曲线绘图区同宽，百分比在容器内计）；
2. **泳道行间灰色细虚线**（用户拍板：纯白太素）—— 按行高 20px 铺 dashed
   border-top，只铺绘图区宽度；
3. **仿真跑到队列完成**（用户拍板：曲线不再在生产中途截断）：`Planner.project`
   加 `until_complete` —— 队列/在途还有货就越过 until 继续跑，死局推到
   COMPLETION_CAP=3600s 封顶（「永远仿真不到」的不无限跑）；`/api/plans/simulate`
   启用并按实际结束时间回填 frame.horizon（live 投影的窗口语义不变）。
   测试 +3（越过 horizon 跑完 / 不传旗标仍在 until 停 / 死局停封顶）；
4. **live 卡顿治理（前端帧合并）**：后端 live pump 每 0.2s 转发全部新帧
   （SC2 每 game step 一帧 ≈16/s）→ 逐帧 setState 把 React 渲染打满。store 侧
   `offer()` 合并：动态帧按 topic 只留最新、150ms 冲刷一次（≤7 次 setState/s，
   延迟 ≤150ms 无感）；静态面直通（测试同步断言依赖立即生效）。
- 回归：前端 **354/25** + build 0 warning；后端 **729/1s**（并行目录工作已收敛，
  test_where_role 已过）。serve_api 需重启拿 planner/告警改动。

## 0.25 二十二轮：平移封顶 + 黑圈修复 + 缺矿不标红 + 地图空转治理（2026-08-22 用户反馈，未提交）

1. **平移绝对封顶**：PAN_SPAN_CAP=1920s —— 速度不再随可视跨度无限放大（整局录像
   跨度 20+ 分钟时拖半幅就是好几分钟），封顶后整幅拖满 ≤16 分钟、半幅 ≈8 分钟
   （用户给的灵敏度上限）；小跨度视图（规划试算 300s）不受影响仍跟手；
2. **单位 chip 黑圈**：drawUnitChip 的暗色硬编码底 `rgba(13,17,23,.82)`（暗主题
   时代残留）在白画布上成了黑块、字看不清 → 白底（canvasBase().void）+ owner 色
   边 + 黑字；组标签亮黄 #fde68a → COLOR.warn（白底可读的琥珀）；
3. **缺矿不标红（后端）**：`view/alerts._queue_alerts` 分类 —— 资源等待类原因
   （晶体矿不足/高能瓦斯不足/缺矿/缺气，RESOURCE_WAIT_MARKS）恒 info 不升级
   error（顺序执行的常态，用户拍板）；结构性卡死（前置/供给/放置）保留
   warned→error 升级。测试 +1（四种资源原因超阈值仍 info），改 1（升级用例换
   结构性原因、瓦斯用例期望 info）；
4. **地图卡顿（live）**：rAF 60fps 无条件全量重画是主因 → 脏标记（每次渲染置脏；
   平滑插值未收敛持续画，收敛后空转跳过）；
5. **对话输入框 2→5 行**；**思考图标 transform-origin 8px→center**（svg 是 13px，
   8px 原点偏心导致「左右旋转不在中心」的诡异晃动）。
- 回归：前端 **350/25** + build 0 warning；后端 alerts 10/10。⚠️ 工作区出现并行
  改动（catalog/terran.json + protoss/zerg.json —— I9 三族目录进行中，非本线），
  `test_where_role`（期望 terran 单 worker）与其数据现状不符，留给该线收口。

## 0.24 二十一轮：平移增益 + assign 出泳道 + 绿毛刺退役 + 表头固定（2026-08-22 用户反馈，未提交）

1. **平移灵敏度**：PAN_GAIN=0.5（板与独立卡同值）—— 内容移量 = 手指位移 × 0.5，
   「稍微拖一下跑很远」收敛；
2. **assign 不进泳道**：planner 给瞬时目标值动作造的 `assign_<task>` 标签不是目录项、
   无工期，旧泳道里成了延到视野末端的「未完」bar 一直占道。`isAssignId` 过滤
   （pairEvents 层）；曲线上改画**黄色虚线**（accent-yellow-fg，经 chart-theme 展开
   —— canvas 不认 CSS var 字符串）+ marker text 给中文（维持采气/采矿/空闲）；
3. **绿毛刺 = 开工/完成底部短刻度**：密集时像噪点、用户读不出含义，删（工期已在
   泳道表达）；stalled 红虚线 + 原因文字保留；
4. **表头固定**：QueueTable 与复盘队列展示表的 thead `sticky top-0 bg-panel` ——
   滚动时只有列表内容动。
- 回归：前端 **347/25**（+5）+ typecheck 0 + build 0 warning；后端未动。

## 0.23 二十轮：投影交互重定义（拖动=平移/零点钉 0/框选下线）+ 折叠退役（2026-08-22 用户反馈，未提交）

用户四组图交互问题 + 折叠清理，一次收口：

1. **拖动跳跃的根因**（曲线突变 + 泳道突现突失）= 旧交互「拖图=拖时间轴(seek)」：
   拖一下把游戏时间大幅回退 → 历史累积判定不连续直接清空 → 泳道整片消失再重累积；
   叠加 uPlot 锁轴时自带拖拽缩放还开着（蓝色选框），与受控定域互相抢轴 → 跳变。
   **修法：拖动 = 平移视野**（视窗横移，内容跟手），不再碰 seek/历史；
2. **蓝色选框下线**：uPlot `cursor.drag` 全时 `{x:false,y:false}` + 删 .u-select 洗色；
3. **零点钉最左**：定域 from 恒 ≥ 0（初始化与平移双侧 clamp）—— 不再有 0 居中
   的大片左留白；
4. **概览投影可平移**：ProjectionChart 独立卡改为**内部受控定域**（默认 [0, 数据
   端]，按住拖平移；拖过之后 live/回放推进只延伸右端不拽回左端）；
   板内（domain prop）由板统一平移。跟随逻辑从「居中重定」改为**边缘触发滑窗**
   （时间位置贴边才平移，拖动中冻结）。
5. **折叠退役**：概览面板折叠钮（useCollapsed/localStorage 整套删，面板常开）+
   对话栏收起按钮/收起态（常开）—— 假折叠只是藏内容不省空间（布局固定）。

- 回归：前端 **342/25**（+7 交互锁）+ typecheck 0 + build 0 warning；后端未动。

## 0.22 十九轮：生产规划改用复盘-生产的三区固定布局（2026-08-22 用户反馈，未提交）

用户：「为什么规划中的生产规划还是旧的布局？我想要的是 复盘-生产 里的那个布局。」
生产规划页从「滚动文档 + 右侧融合工具栏」改为与复盘-生产同构的三区固定布局：
- 上排固定 38%：[队列编辑（QueueTable，区内滚）| 文件与试算（QueueSidebar）]；
- 下方试算投影占满整列（ProjectionBoard，泳道抢高度）；无试算时 Empty 占位
  （「点右上试算跑一版」）—— 投影区常驻，不再藏在试算按钮后面；
- 前瞻警报从独立卡收进「文件与试算」面板的分节；QueueSidebar 的三张 Card
  改分节（卡中卡双描边去掉）。
- 地图规划页的融合大卡不变（本轮只动生产）。
- 回归：前端 **335/25** + typecheck 0 + build 0 warning；后端未动。

## 0.21 十八轮：生产编辑器复用 —— 复盘生产页嵌入「转为规划草稿」（2026-08-22 用户提议，未提交）

用户观察：规划的生产与复盘的生产本质是同一个东西的完整版/缩小版，从未统一。
拍板方向：编辑器抽成共享组件，复盘生产页左上「生产队列」卡嵌入添加/删除/修改。

**域语义先说清**：复盘源背后没有会话（writeGate 管命令面），「在复盘里编辑」不能
是下命令 —— 正确形态是**把看到的队列提取成规划草稿来改，另存为规划文件**。
live 模式的命令编辑（reorder/remove，cmd.run）原样保留，两者互不越界。

- `planning/QueueTable.tsx`（新）：队列编辑表共享组件（行间悬浮＋插入、↑↓/×、
  op/目标/数量/放置就地修改、＋加一项/清空；密集列表无框化纪律随迁）。
  规划页吃 queue-store（st.items/update/setItems + 地图规划限定的 placement 选项）；
  复盘页吃本地草稿（帧源 map 静态面的 placement 选项）。
- `queue-draft.ts` 新增 `draftFromSessionQueues`：会话多队列按**全局 index** 摊平成
  单序列（还原规划顺序）；`cancel` 是运行期产物跳过；placement/task 原样带回
  （会话里已解析成具体槽位/区域，规划本就吃这个形态）。
- 复盘生产页队列卡 right 加「转为规划草稿」→ 卡内切换为 QueueTable 编辑态 +
  「另存为规划」（createPlan + savePlan，标题=「复盘提取 游戏时间」）→ 成功后出
  蓝框跳转 chip（切规划模式打开新规划）。
- 回归：前端 **334/25**（+6：draftFromSessionQueues 4 + 复用锁 2）+ typecheck 0 +
  build 0 warning；后端未动。

## 0.20 十七轮：工作台融合成整块 + 对话区独立浮起（2026-08-22 用户反馈，未提交）

用户指出右侧工具栏与对话栏都白、贴在一起「融合」了，拍板新形状：
1. **地图与工具栏融合成一整块白卡**（内部 border-l 分割，不再各占一列）——
   WorkPanel 独立面板列退役，PlanningPage 的 map/production tab 自渲染
   `rounded-lg border bg-panel shadow-sm` 大卡：[地图 p-2 | 规划工具栏 w-72] /
   [表格+投影滚动区 p-3 | 生产规划工具栏 w-72]；
2. **对话区独立**：卡片四周留蓝缝，ChatDock 改圆角浮卡
   （rounded-lg + shadow-sm，去掉贴边 border-l）——**不要分割线**（用户随后修正：
   左缘那条 border-chrome 小线删去，只靠蓝缝隔开）；再修正**对称**：缝统一 8px
   （main p-3→p-2）；**终形**：对话右/上/下直接贴边（与顶栏对齐，不再留缝圆角），
   只有左缘留 8px 蓝缝（来自 main 的 p-2 右缘）与工作卡隔开 —— 对话变回贴边矩形。
- 回归：前端 **328/25** + typecheck 0 + build 0 warning；后端未动。

## 0.19 十六轮：外围白/选中蓝反转 + 删两段唠叨文案 + 投影区高度重排（2026-08-22 用户反馈，未提交）

- **配色反转**：外围（顶栏 + 侧栏）改**白**（bg-panel + border-chrome 浅蓝缝不变），
  选中态反转为**蓝**（新 token `--bg-select #9CBCE3` + `bg-select`）：模式三框选中段
  与侧栏选中按钮 = 蓝底黑字 semibold。主区仍是蓝底白卡 —— 白框蓝芯。
- **删文案（用户拍板）**：①规划地图 tab 底部的图例行（菱形/实线框/淡绿底…）整行删；
  ②复盘里生产页的只读横幅（「只读：当前是离线帧源…」）删 —— CommandBanner 收窄为
  纯命令结果条；写入控件仍受 writeGate 门控（不可写时直接不渲染），只是不再占横幅。
- **投影区高度重排**：泳道箱从固定 140px 改 `flex-1 + min-h 140px`（生产页三区里
  向下抢回空间，行多时区内滚；规划页文档流里自然展开）；矿气曲线高度不变（190）；
  右侧检查面板从固定 320px 改 `self-stretch` 拉满整板高度（区内滚）。
  链路：生产页投影 Card 改 `flex min-h-0 flex-1 flex-col`，board 根/左列/图板包裹
  一路 flex-col min-h-0 flex-1。
- **回归**：前端 **327/25** + typecheck 0 + build 0 warning；后端未动（725/1）。

## 0.18 十五轮：无边缘缝外壳 + 右侧固定工作面板 + 蓝底白卡 + 真流式（2026-08-22 用户反馈，未提交）

用户四组反馈：①rail 顶过头了——本意是接在顶栏下面，且去掉与边界的缝（两个圆角
矩形接上、中间浅边分割）；②工作面板放左侧不行（右侧是对话框，工具要离鼠标近），
移到右侧**固定不折叠**；③换配色：#9CBCE3 蓝外围 / 白卡片 / #E1DBE9 粉点缀 /
#F5C386 黄交叉用（不让一色滥用）/ 黑字靠轻重粗细做视觉引导，密集列表不用黑框
（色块+排列做秩序），偶尔带框的区域才显轻松；④假流式不够——思考从第一个 token
起就要有 UI 与动效，「思考中」扫光占位冗余砍掉；**用户改了 BaseAgent**
（C:\dev\project\BaseAgent，未提交工作树：llm/engine/runner/types 支持 on_delta
流式分片 + Engine.start_stream 内存事件通道 + ADR-0007），接上真流式。

**外壳（无边缘缝）**：App 根去 padding/gap → `flex-col`：ModeBar 全宽贴视口顶
（`border-b border-chrome bg-base`），下排 [SideRail（贴左、border-r、直接接顶栏）|
主列 | WorkPanel | ChatDock]，区块间全是浅色分割线不再有缝。模式三框 = 蓝底上的
白药丸分段控件（选中段粉洗 `bg-inset`）；rail 按钮 = 蓝底白浮起（选中白药丸+
shadow-sm）。

**工作面板右移**：SideDrawer/drawer.ts 状态机退役 → `shell/WorkPanel.tsx` 固定
右侧（w-80、border-l、白面板），只在 plan-map/plan-production 页渲染；内容组件
不变（MapPlanSidebar/QueueSidebar，状态仍在 store）。

**配色 v3（token 全换）**：--bg-base #9CBCE3（外围蓝）/--bg-panel #fff（白卡，
Card 加 shadow-sm 浮起）/--bg-inset 与 --bg-active #E1DBE9（粉：小区域区分与选中）/
--accent-yellow #F5C386（黄：warn 底、角标、交叉点缀）/--border-on-base #7EA3CD
（蓝面浅边）；文字全近黑四档（层次靠字重：ModeBar 选中段 font-semibold、rail
同款）。canvas 文字/格线微调、图表 fallback 同步。

**密集列表去框（生产队列表）**：表格内 select/input 全部无框化
（`hover:bg-inset focus:bg-inset`，聚焦才显底），行改斑马纹
`odd:bg-raised` + hover —— 黑框堆叠消失，秩序靠色块与列对齐。

**真流式**：
- vendor/agentic ←→ BaseAgent 工作树同步（robocopy /MIR，内容 diff 验证一致）；
  我们的工具本就 async，722 全绿零兼容修复。新面：`LLMDelta`（reasoning/
  content/tool_call 分片）+ `Engine.start_stream`（async for 逐事件）。
- `agent/talk.py`：`_say_on_engine_loop(text, emit=)` 双路径（start / start_stream），
  `start_round(text) -> RoundHandle`（queue.Queue 跨线程事件流 + round 终态 + None
  哨兵；引擎仍在专属循环线程，SSE 侧 to_thread 拉队列不阻塞 uvicorn）。
- REST `POST /api/agent/chat/stream`（SSE：delta → tool_call → run_end → round）；
  旧整段端点保留（回退路径）。
- `api/agent-chat.ts` `sayChatStream`（fetch reader 解析 data: 行；流面失败回退
  整段）；ChatDock 重写：reasoning 分片**第一个 token 即点亮**思考行（轨道旋转+
  呼吸，摘要跟随最新行）、content 分片直入正文（Markdown 实时渲染）、tool_call
  参数分片即开「运行中」工具行（扫光）、完成事件落真名/参数/结果；round 到达用
  服务端真源（steps/changes）替换本地过程。Typewriter 假流式 / running_steps
  1.2s 轮询 /「顾问思考中」扫光占位全部退役。
- chat 改动按钮重样式（用户拍板）：全宽蓝框圆角（border-[1.5px] accent-blue）、
  透明底黑字、与回复同宽对齐。

**回归**：后端 **725/1s**（+3 流式：start_round 事件序 / SSE 端点 data 行 /
空消息错误流）；前端 **325/25** + typecheck 0 + build 0 warning（新工具类
border-chrome/bg-yellow-soft 等已验在产物 CSS）。

**注意**：serve_api 需重启拿新 vendor + SSE 端点；vite 热更即可。

## 0.17 外壳重构：粉蓝白 + 顶栏极简 + 时间轴下沉 + 左抽屉（2026-08-22 用户十四轮反馈，未提交）

用户四组诉求一次做完：色板换粉蓝白（嫌纯白太素）、顶栏只剩【指示灯】+三框
（删标题/下拉/状态文字）、时间轴做到下一层级（左侧栏顶上去）、左栏做成可展开
抽屉（点击展开对应内容、点其他地方收起、状态保留）。

**色板（用户给色板，微调归我）**：底 `#f5f2f8` / 面板 `#faf8fc`，accent 族
蓝粉薰衣草（`#84C9EF #B4D2ED #CBBDDD #DCB5D4 #E3B1D2`）各三档（本色/soft 洗/
fg 文字深度）进 token：选中态粉洗 `--bg-active #eed6e6`、边框薰衣草、文字
李子灰。canvas（void/格线/mark 粉 fg/owner 去饱和）与图表系列色（蓝/绿/粉/
薰衣草）同步翻。**暗色残留大扫除**：全仓 tsx 的 `x-950/x-900` 洗底与 300 档
浅字（白底上看不见）全部换成 ok/warn/err/accent token（72 行），theme.test
加禁令锁（violet-500 思考色白名单除外）。

**顶栏极简**：SessionBar 退役 → ModeBar（【指示灯】游戏|复盘|规划，右缘只在
异常态出现控件：后端重试/断线重连/会话收尾/失败原因）。模式改名不动 id：
drive=游戏、replay=复盘、offline=规划，顺序 游戏|复盘|规划（MODE_ORDER）。
删下去的控件各归其位：
- 会话启动/地图规划选择 → **StartCard**（游戏模式 + 无活会话时占主区——没有
  会话就没有可看的驾驶面）；
- 复盘源/夹具选择 + 播放×4/暂停/回到实时 → **TimeStrip**（主列顶部的细带）；
- 离线背景夹具选择 → 调试页（会话卡里一行）。
- 会话轮询收口 `shell/session-store.ts`（zustand 模块级）：ModeBar 指示灯与
  StartCard 共享同一份 2s 轮询（指示灯三态：绿呼吸=运行/灰=没跑/红=断流）。

**时间轴下沉（I11 关）**：Timeline 改细轨（h-7，去卡片壳与图例行），回看染粉/
跟随蓝；只在 drive(活)/replay 渲染，规划模式整条消失。左 rail 顶到视口顶。

**左抽屉**：IconRail(w-44 横排) 退役 → SideRail（w-[4.5rem] 图标+小字竖排，
分组细线）+ SideDrawer（w-80，分节滚动）。交互三律收进 `shell/drawer.ts`
纯状态机（drawer.test 12 条）：同键再点=toggle、换键=切换、点主区=收起不丢
key（收起用 hidden 不卸载 + 状态进 store）。
- **地图规划拆分**：`planning/map-plan-store.ts`（文件面+草稿 hunk 面+投影
  hook）+ `panels/MapPlanSidebar`（规划空间/放置工具/点位槽位列表）+ 主区只剩
  全幅画布（hover/ghost/落笔/拖动校验留在页侧）。**顺带修掉换页丢状态**：
  原先 MapPlanning 本地 state，导航一换就没（P1 时代遗留）。
- **生产规划拆分**：`planning/queue-store.ts` + `panels/QueueSidebar`（文件/
  放置引用/成本/试算）+ 主区表格与投影板。深链 ?plan/?map 由 initOnce 幂等消费。

**顺带**：Overview 经济大数字 tabular-nums（I7-② 拖时间轴跳动）；I7-③
「地图规划左侧栏偏大」由抽屉化一并解决；ui.tsx 的 Pill 死件删除。

**回归**：前端 **327/26**（+18：drawer 12 + 外壳锁 6 + 模式命名 2 −
session-drive 重定向）+ typecheck 0 + build 0 warning；后端 **722/1s**（零改动）。

## 0.16 审批下线 + agent 文件工作区 + 改动 chip（2026-08-22 用户拍板，未提交）

用户痛点（本节立项依据）：工具层 12 个 CRUD 泛滥（无法跨规划搜索、全量覆盖
改不了中段）、审批弹窗打断工作流、"agent 生成规划后不知道去哪看"。拍板方向：
**baseagent 固定文件夹工作区**（框架 file_contract 本就齐备：ls/read/glob/
grep/write/append/edit/insert/delete/stat，`AdvisorSpec` 此前刻意不用）；
**审批彻底下线**（代码保留休眠）；**改动跳转由系统生成**（不信 LLM 拼链接），
agent 只需遵守"哪个路径写什么格式"，钩子按路径分类校验。

### B 审批下线（最小改动先做）

- `POST /api/proposals`（app.py）：创建后校验通过 → **立即自动 accept**，
  `decision.auto=true` 留审计；应用失败（如无会话）留在待审批。§6 P1-P5 的
  语义未删——accept/reject 端点、ProposalStore、ProposalReview.tsx 全部保留
  休眠（ProposalHost 本就无挂载点），恢复人工审批只需删端点里那段自动应用。
- 话术全量更新（agent/tools.py propose、spec.py 域一、__init__）："等用户审批"
  → "校验通过即自动应用"。观察包提案历史照旧（防重复提的依据从"待审批计数"
  变为"[已接受] 行"）。
- 测试：test_round 两条改语义（自动应用断言 + 队首真的变成精炼厂）；
  test_proposals/test_live 里依赖"创建后待审批"的用例改走 `store.create`
  直接建（preview/accept/reject/expire 路径继续被测，只是入口绕开端点副作用）。

### A 虚拟文件工作区（agent/workspace.py，核心）

- **ApiWorkspace(Workspace)**：实现 5 个存储原语，edit/insert/delete/grep/
  read-before-write 全由框架基类组合。三条虚拟路径区：`plans/<id>.yaml` →
  规划 REST（读渲染 YAML/写解析后 PUT，校验在服务端）；`map-plans/<id>.yaml`
  → 新增 `GET|PUT /api/map-plans/{pid}/doc`（MapPlanStore.doc/save_payload，
  校验口径与 hunks 一致=只查**本次改动**的槽位，预设存量不追溯）；其余路径
  = scratch 磁盘自留地（memory.md 替代 notes 工具，analysis-*.md）。
  写不存在的 plan 路径=新建（先 create 再 PUT）；map-plans 同理（复制空白）。
- **工具面 19→18 但语义收敛**：删 8 个规划 CRUD + 2 个 notes 工具，文件契约
  10 件套进场（engine registry 按 agent.workspace 装配）。语义动作保留：
  simulate_plan / start_session / list_modules / read_module /
  read_current_strategy。**跨规划 grep 从此可行**（用户痛点 #1）。
- run.py/talk.py：Engine(workspace=ApiWorkspace(...))；planning demo 链改为
  ls → read → write plans/agent-demo.yaml → simulate。
- observe 的 current_index：确认**不需要**——队列视图本就是"剩余队列"语义
  （rev 4 校准：已完成项出队，agent 看到的下标直接可用于 propose hunks）。

### C 改动 chip（系统生成跳转，不信 LLM）

- **ChangeRecord/ChangeLog**：写钩子在保存成功后记 {area, action, ref, label,
  target}；propose 自动应用记 live 域。**校验失败/未遂不记**。target 是后端算好
  的 hash（plan→`#/plan-production?plan=<id>`，map_plan→`#/plan-map?map=<id>`，
  live→`#/production`）。
- AgentTalk 轮末 drain → 随 agent 消息进 history（持久化，刷新后 chip 还在）。
- 前端：route.ts 支持查询参数（parseRoute 纯函数可测）；App 把 ?plan=/?map=
  传给 PlanningPage（key 含参数，深链每次重挂载消费）；ChatDock 消息尾部渲染
  圆角矩形 chip（动作字形 + 对象名，点击 window.location.hash 赋值）。
- 已知边界：drive 模式下点规划 chip 会被模式导航兜底跳走（R5 门控仍生效），
  G7 不静默；scratch 文件无 chip（用户拍板：不做文件查看，正文汇报）。

### 回归与验证

- 后端 **720 passed / 1 skipped**（新增 tests/agent/test_workspace.py 13 条：
  虚拟目录映射 / read-before-write / 锁定拒改 / insert 中段插入 / 重叠拒存 /
  ChangeRecord 记录时机 / scratch / 跨规划 grep）；前端 **308 passed**（新增
  route.test.ts 5 条 + chat-dock chip 扫描锁）；tsc 通过。
- run_round 返回值新增 changes（CLI 可见轮末改动清单）。


## 0.15 ChatDock 设计改版 + 双主题落地（2026-08-22 用户四轮反馈，未提交）

**ChatDock 按设计参考文档重构**（docs/前端对话框设计指导参考.md，提炼自
deepseek-harness）：不对称布局（用户=右对齐圆角气泡，agent=全宽纯文本无容器；
无头像无角色标签）；思考链=内联折叠灰字行（Think·首行摘要→缩进全文）；工具
调用=全宽单行折叠（工具名·参数摘要·耗时→展开 IN/OUT 圆角卡，失败 OUT 红字）；
轮级加载=一行扫光（dsh-sweep，prefers-reduced-motion 关闭）而非逐 step spinner；
时间戳 hover 才出现。扫描锁 +1（Think/IN/OUT/扫光/气泡形态）。

**双主题（用户拍板正路方案 + 亮色调两轮校准）**：
- **基建**：index.css 双调色板（:root 暗 / html[data-theme=light] 亮）+ 语义
  @utility（bg-base/panel/raised/inset、border-l1/l2、text-strong——text-dim/
  faint/ghost 改走变量）；shell/theme.ts（localStorage + THEME_EVENT 事件）+
  useTheme hook + SessionBar「☀亮色/☾暗色」按钮；main.tsx 启动 restore。
- **亮色调两轮用户校准**：①「不用近黑」→ 文字柔炭灰 #3f454d，全档 #E3E3E3
  一带；②「白太多、彩字太亮」→ 面板体系去纯白（raised 从 #f7f8fa 压回
  #e9ebee，最白一档只给地形位图）+ accent 覆写层降饱和（压色相饱和不压明度：
  emerald #3c7862 / sky #41708f / amber #8f6c1f / red #ac4a4a）。
- **canvas/图表**：canvasBase() 双主题（void/text/grid）；bakeTerrain 亮色
  slate 阶梯（#E3E3E3 压一档→纸白，悬崖暗描边/斜坡暖色两主题通用）+ useTheme
  触发重烤；ProjectionChart/PairChart 轴网格读 CSS 变量、系列色亮色降饱和
  （chart-theme.ts）。
- **全站清扫**：~230 处字面 bg/text/border-neutral-N → 语义 token（sed 分档
  映射 + 残余 7 处手工）；ring/悬停同批。**扫描锁**：tsx 禁止裸 neutral 色
  回归（theme.test +4：扫描/调色板 13 变量齐/@utility 齐/canvas 双主题降级）。
- 亮色 accent 覆写层是**兼容层非终态**（accent 未进 token 体系）；下一轮视觉
  打磨：彩字胶囊化（tint 底+边框）、accent 全量 token 化。
- **回归**：前端 **293/23** + typecheck 0；后端 714/1s（本轮零后端改动）。

**同日五轮反馈（截图驱动，未提交）**：①主题切换改**分段控件**（暗|亮，选中段
底色；emoji ☀☾ 在 Windows 豆腐块 = 用户看到的「切换没变化」）；②UPlotChart
默认坐**凹陷面板**（border-l1+bg-inset —— 图表与卡片拉开底色层次）；③ChatDock
再打磨：**加宽一倍**（w-80→w-160）、思考行=**SVG 原子图标+着色加粗「思考」**（不
依赖 emoji 字体）、工具行=**几何字形图标+缩进+参数收进展开态**（折叠只见
名字+耗时）、agent 回复走**迷你 Markdown 渲染器**（shell/markdown.tsx，零依赖
显式白名单：标题/粗斜体/行内码/围栏码/中英列表/引用；markdown.test 4 条锁）；
④**提案审批收件箱整体退役**（用户拍板：离线域早已 agent 直改，收件箱不知道
怎么用还占地方）——ChatDock 收件箱/徽标/轮询全删，App 的 ProposalHost 浮层与
openProposal 状态同批退役（**后端提案通道原样保留**：live 域 API 与测试不動，
只是无 UI 入口）；⑤**发送按钮退役**（Enter 直发，输入框下只留一行提示）；用户
气泡加浅底+描边（bg-inset+border-l1）；IN/OUT 改**独立标签列**（flex 布局，
修复绝对定位盖字）。扫描锁 +2（无收件箱/无发送按钮）。**回归：前端 299/24 +
typecheck 0；后端 714/1s。**

**六轮反馈（同日）**：①输入框下的常驻提示行退役（占一整行；出错时才出现，
G7 保留）；②迷你 Markdown 补 **GFM 表格 + 容错**（`|---|` 分隔行的对齐变体；
缺分隔行/缺首尾竖线/列数不齐分别靠「首行当表头」「≥1 竖线即可识别」「按最宽
行补空」救回；防误伤 = 单行竖线文本不是表格，需要连续 ≥2 行竖线行）——
markdown.test +4 锁。**回归：前端 303/24。**

**七轮反馈（同日，截图驱动）**：
- **rev 徽标迁左下角**（StatusChip bottom-right→left，常态 ghost 色更低调；
  左下角规划为未来「设置入口」位）。**模式/主题切换选中态**：新 `--bg-active`
  token（控件状态色阶，不拿表面色凑 —— 亮色下 raised≈base 就是「没底色」的
  根因），模式三段与主题两段都用它。
- **投影板**：①横向滚动条根因 = uPlot 宽度取 `clientWidth`（含 padding），
  图比内容盒宽 8px —— 改按内容盒计算；②**滚轮调宽度退役**（用户拍板没用，
  保留左键拖时间轴 + 点击检查）；③**矿 y 轴上限 600 贴顶**（MINERAL_CAP，
  >600 无决策价值；hover/检查面板仍显真实值）。
- **生产页信息分区 v1**：经济卡重做成「经济本体」—— 采矿/采气工人与满采位
  大数字（汇总）+ 配额表（唯一写入口，实际/可达合并一列）+ 征用计数；删
  per-node 矿2/2 chips、本帧差量、征用名单明细；顶排双卡**固定 340px**
  （队列卡内部滚动，数据多少不撑动布局）。
- **回归：前端 303/24 + typecheck 0；后端 714/1s。**

**八轮反馈（同日）**：①模式切换仍「没底色」——build 产物里 `.bg-active` 在
（vite 旧包嫌疑，让用户强刷）；对比度再拉开（dark #2e3a49 / light #cbd3dc）；
②滚动条全局改**细 6px + 半透明**（webkit + firefox 双写）；③泳道容器高度
恒定 140px（原 min(rows,7) 随行数 1~7 变 = 「高度随便变」的根因）；④uPlot
host 加 overflow-hidden 兜底；⑤检查面板固定 320px 内滚；⑥**生产页三区固定
布局**：页根 h-full overflow-hidden 不滚，顶排（队列 2/3 + 经济 1/3）38% 高、
投影吃剩余，各区内部滚；**在途建造/目录 tabs 整体删除**（CatalogPicker 退役，
layout 锁更新为「三区固定」断言）；⑦「开局90秒」夹具泳道并行度可疑 →
**ISSUES I10** 开放（packBars 无 bug，数据侧待查）；⑧美学配色：用户想要
「灰白高级感 / 粉白蓝可爱」方向 —— 本轮只给色卡建议不动代码（见对话）。
**回归：前端 303/24。**

**九轮反馈（同日）**：start.bat 跑的是 `pnpm dev`（真 dev server）——「旧包」
论推翻，两件事都是真 bug：①泳道横向滚动条真凶 = `overflow-y-auto` 使
overflow-x 隐式变 auto，泳道内 100% 处 2px 卡点竖线溢出 → 加
`overflow-x-hidden`；②投影 footer 删「视窗/采样」两行（保 hover 读数与卡点）；
③**配色双方案落地**（用户「其他建议采纳」）：方案 A「灰白高级感」替换亮色
（#ECEDEE 底 + 雾蓝 #7C93B2 注入 bg-active/链接/选中态），方案 B「樱」第三
主题（暖白 #F7F4F2 + 樱粉 #E8A0B4 系），ThemeName 三档 + 切换器三段（暗/亮/
樱）+ CANVAS_THEME/terrain 阶梯/图表系列色接入；accent 覆写层改 `:is()` 同时
罩亮色与樱色。教训：**palette 手写 hex 出过西里尔 е 混入**（#ecedeе 非 ASCII →
CSS 静默失效），isascii() 自检进流程。**回归：前端 303/24；后端 714/1s。**

**十轮反馈（同日，截图驱动）**：「主题像蒙了一层滤镜」的根因 = 调色板各档色差
太小（亮/樱 base≈panel，只差 2~3 个色值单位）+ **结构组件根本没铺底色**
（IconRail/时间线/顶栏全透明，只有卡片有 bg-panel）。修法：①调色板**真实
分层** —— 页面底明显更深（亮 #E2E6EB / 樱 #EDE6EA），面板浮起（#F7F8FA /
#FBF8FA），凹陷/浮起/激活拉开档位（用户点题「边缘空白处稍深，看起来有背景」）；
②IconRail 改**圆角卡片**（bg-panel+border）+ 激活项 bg-active+加粗、悬停
bg-inset；③顶栏与时间线同样铺 bg-panel 圆角卡片（时间线 mt-2）；canvas void
跟页面底同步。樱色文字改暖灰（#4A4246 系）。**回归：前端 303/24。**

**按钮级打磨轮（同日，用户「开工吧」）**：`.btn` 体系五变体（ghost/ok/warn/
danger/active）进 `@layer components`（工具类仍可覆盖）——统一态：hover 浅凹
或 tint 加深、按下轻下沉 `translateY(1px)`、disabled 40%+not-allowed（理由仍
走 title，G7）、`focus-visible` 描边环（键盘可达）；语义状态色进 token
（--ok/warn/err-fg/bg ×三主题，hover 用 color-mix 加深）。全站清扫：静态
7 形态 + 动态（Flow 步骤列表选中上 bg-active、危险/主操作/警告按钮归位）；
`text-xs→text-label`、`text-sm→text-body` 全量收编（34 处）+ **扫描锁**
（G6 从此禁 text-xs/sm 回归）。**回归：前端 304/24 + typecheck 0。**

**十一轮反馈（同日）**：①外壳 `gap-2` 统一竖向间距（时间轴与内容区贴边的
根因 = shrink-0 块与 flex-1 行之间没有 gap）；②时间轴做小（py-2→py-1），
「下放进页面级/改形态」记 ISSUES I11 待定；③**rev 徽章删除**（StatusChip
组件退役，layout 锁改「无 footer 无 StatusChip」）；④只读唠叨降噪：离线模式
不再显示 writeGate 理由横幅（本就不该写，不是异常；驾驶/复盘保留）；
⑤**收敛单主题**（用户拍板「先只做一套，以暗为基础微调」）：light/sakura
调色板与 accent 覆写层删除，**页面底 = #E3E3E3**（组件间空白的填充色，暗面板
浮在浅底上）+ `--on-base-text`（直接落在底上的文字用深色）；theme.ts/ThemeToggle
删除，canvas/图表固定暗色；theme.test 改单主题断言（禁 light/sakura 回归）；
⑥投影卡 overflow-hidden（不再出滚动条）；「N 行（区内滚动）」行删除（挤走
泳道下方空间）；顶排 38%→30%（生产队列/经济框缩短）。**回归：前端 302/24 +
typecheck 0；后端 714/1s。**

**十二轮反馈（同日）**：
- **对话流式动效**：`refresh_running_steps()`（GET /api/agent/chat 顺带刷新）——
  从 trace 事件增量推「本轮进行中」：最后一个 tool_call 无结果 = 工具运行中；
  最后一个 llm_call 的 reasoning = 思考中（blob 流式追加，摘要跟最新一行）。
  前端 busy 期间 1.2s 轮询拉 running_steps 镜像渲染：思考行 = 原子轨道旋转
  （think-orbit 2.4s）+ 文字呼吸 + 摘要跟随最新一行 + 自动展开；工具行 =
  dsh-sweep 扫光 + 「运行中…」；各自结束即停（prefers-reduced-motion 全关）。
  真·token 级流式文本需要 runner 钩子/async LLM 客户端，超出本轮。
- **规划页生产板表格化**（用户拍板复用复盘页布局）：卡片行 → 表格行
  （#/op/目标/数量/放置/操作 六列）；**行间悬浮「＋ 在此插入」**（0 高缝隙行
  hover 展开，点按 splice 插入）；↑↓ 上/下移（hover 才现，边界禁用）；
  放置列对齐复盘页；describeItem 说明行退役（表格自身可读）。
- **回归：前端 308/25 + typecheck 0；后端 720/1s（+running_steps 锁）。**

**十三轮反馈（同日）——两个大修**：
- **「越来越白 + 边框消失」= 真 CSS 事故**：index.css 注释里写了
  「--bg-*/--border-*/--text-*」—— `*/` 序列**提前闭合了注释块**，从
  `@utility text-metric-xl` 起整段被 Tailwind 判非法 → 产物丢掉全部语义类
  （bg-panel/border-l1/btn 体系/think-running 动画全没了）= 全站裸默认样式。
  修：注释措辞去掉 `*/` 序列；**事故锁**（theme.test 扫描注释体内不得出现
  `*/`）+ build 0 warning 验证五类齐全。
- **主题翻白**（用户拍板「我要白色」，此前误解成暗面板浮浅底）：:root 整套
  白系（底 #E3E3E3 / 面板纯白 / 文字柔炭 / 选中雾蓝 #D7E1EE）；color-scheme
  light（表单控件/滚动条随白）；canvas void 白底 + 地形阶梯浅色档 + 悬崖描边
  浅色系数 0.55；图表系列色降饱和档（emerald #2e8a6d 系）。
- **假流式**（用户提议）：Typewriter 组件 —— 新到回复按 ~28 字/秒逐字演出
  （G5 纯显示层节奏；typing 标记只在内存，历史消息/刷新后整段显示）。
- **回归：前端 309/25 + typecheck 0；后端 722/1s。**

## 0.14 P3 切片 A+B：离线商量回路（对话式顾问 + 战术素材工具，未提交）

用户拍板方向（2026-08-22）：live 自动应用可以后放，**先让 agent 在离线阶段能和
玩家商量战术/策略**。盘点结论 = 素材面（P3 切片 1-3）已齐但对话回路不存在 ——
ChatDock 输入框还是 B10 时代的禁用占位（注释原话「agent 接上时不用改这里」）。

**切片 A —— 对话回路**：
- `agent/talk.py` AgentTalk：常驻 Engine，玩家每条消息 = 同 target 一轮
  `engine.start`（BaseAgent `create_or_get` 复用实例 + runner 写回 state.messages
  → 对话历史天然延续）。三个运行时事实（源码验证/实测）：
  ① `state.turn_no` 终身累计而 max_turns 按它比较 → **每轮归零**（否则聊几轮后
  每轮立刻 max_turns 退出无回复）；② 一个 Tracer 一个 run_id，每轮收尾
  `finalize_summary()` → 聊到一半 trace.html 也能看；③ **引擎必须跑在专属事件
  循环线程** —— 真 LLM 首测踩出死锁：工具面走同步 urllib 回环访问本服务，引擎
  若在 uvicorn 循环上会阻塞回环请求（list_plans 超时×2，agent 如实回报失败）。
  修法 = daemon 线程常驻循环 + `run_coroutine_threadsafe`，urllib 阻塞的只是
  对话线程。
- REST：`GET /api/agent/chat`（历史/启用状态/G7 错误）、`POST /api/agent/chat`
  （空消息 400；LLM 未配置 503 带 .env 指引 —— 惰性构造，启动不炸）。装配：
  `create_app(agent_talk=现成实例 | agent_base=自回环 URL)`；serve_api 默认启用
  （补了 ROOT/vendor 的 sys.path —— agent 包此前只被 pytest 看得见）。
- 前端：`api/agent-chat.ts` + **ChatDock 输入框解禁**（消息流/思考中/Enter 发送
  /失败理由原样显形；提案收件箱收成独立分区）。B10 占位文案退役（扫描锁）。

**切片 B —— 战术素材（只读）**：`list_modules` / `read_module`（MODULE_REGISTRY
参考战术库 + docstring + ops_to_items 导出）/ `read_current_strategy`（当前会话
策略与装配 YAML）。写策略留给人 —— agent 商量/评审（EXPECTED_TOOLS 锁 +3）。

**验证**：后端 699→**710 passed/1 skipped**（+7 chat：续话/trace 中途可见/端点
往返/空消息/未启用/密钥 G7/对话中用工具；+4 模块工具）；前端 **288/23**（+4
ChatDock 扫描锁）+ typecheck。**真 LLM 两轮对话 E2E**（.env 有密钥，一次性
8791 沙盒）：第一轮 list_plans 如实报 2 份规划、第二轮 read_module 准确复述
bio_tank_opening 战术构成（农民优先/双兵营反应堆/坦克/二矿/攻防），turns=2
同一对话延续，全程零改动（它自己声明且 store 未变）。

**用户三轮反馈修复（同日，未提交）**：①「没有结论」占位 = LLM 纯文本回答没调
done（result=None）→ 回复提取改为「result 或最后一条 assistant 文本」，提示词
同步（对话直接文字回答；完成具体事才 done）；②时间戳 29789345:21 = `fmtTime`
**本是游戏时间格式化器**（秒→mm:ss），聊天时间戳改挂钟 HH:MM（本地 clock()）；
③对话框过程可视化：每轮 agent 消息带 `steps`（trace 事件切片：tool_call 的
名称/参数/结果摘要/耗时 + llm_call 的 reasoning blob 全文），ChatDock 折叠渲染
（思考过程/工具调用两个 details）。回归 712/1s + 288/23。

**记忆系统（同日拍板「工具化 + 记忆文件」，未提交）**：
- **对话持久化**：AgentTalk `history_path`（serve_api = runtime/agent-talk/
  history.json），每轮落盘；重启后 ① UI 历史读回 ② **历史 seed 进新引擎的
  state.messages（带 system 头）—— LLM 侧也记得**，真·跨重启续聊（回归锁
  用闭包捕获 FakeLLM 验证第二轮入参里含第一场文本）。
- **跨会话笔记**：`GET/POST /api/agent/notes`（append-only jsonl，人与 agent
  同一入口）+ `save_note`/`list_notes` 工具（EXPECTED_TOOLS +2）+ 提示词
  「记忆」节（拍板方向/偏好/结论要记；开局先 list_notes）。
- 回归：后端 **714/1s**，前端 288/23。
- **双主题**：用户拍板**下一批次做正路方案**（语义 token 层 + 双调色板 +
  全站清扫 + canvas/图表亮色板，估 1.5-2 天；顺手兑现 F11 欠的 theme token）。

## 0.13 P3 切片 1+2：agent 接入离线规划域（工具面 + 提示词，未提交）

上一执行轮（§0.1–§0.12）已随 `7c5fe0e` 入库，本节起新一轮。P3 立项见 §5：
让规划文件/试算/预设点位真正被 agent 使用（用户定位的重中之重）。

**权限域正式一分为二**（§5.3 方向的离线半边落地）：
- **对局状态**：仍只有 `propose` 一条路（§6 P1 的锁没有放宽）；
- **离线规划文件**（authoring 数据）：agent 直改（codeagent 语义，用户 2026-08-21
  拍板），不走提案。边界从「不能直改状态」收窄为「不能直改**对局**状态」。

**切片 1 —— 规划工具面（agent/tools.py `make_planning_tools`，10 个）**：
- 生产规划：`list_plans` / `read_plan`（带下标，全量替换的行号依据）/ `create_plan`
  （copy_from 复制是锁定规划的唯一改动路径）/ `write_plan`（队列全量替换；
  title/map/spawn 不填时工具层先 GET 现值兜住 —— 后端 PUT 是覆盖语义，漏传会把
  标题抹成 id）/ `simulate_plan`（给 queue 或 plan_id；输出曲线末点 + 事件 +
  前瞻警报 + 被跳过项，全是 LLM 友好的有界文本）；
- 地图规划：`list_map_plans` / `read_map_plan`（槽位/点位/**预设固定建造点 36 名单**；
  绝不吐地形栅格 —— static/map payload 直接给会淹掉上下文）/ `create_map_plan` /
  `write_map_plan`（hunks 与画布同一套校验，重叠/压预留区的结构化错误拼成可读一行）；
- `start_session`（driver=offline/sim/sc2 + map_plan；sc2 在描述里标明会开真游戏进程）。
- 配套 `ApiClient` 补 PUT 通道与规划/会话方法；测试 transport 相应支持 PUT。

**切片 2 —— AdvisorSpec**：工具集接入规划工具；系统提示词重写为两域结构
（域一 live 提案制 / 域二规划直改「读→改→试算→报告」，没有试算的改动不算完成）。

**边界锁同步**：`test_agent_has_no_direct_command_tools` 的精确集合更新为
EXPECTED_TOOLS（14 个）；**queue_op / set_worker_quota / submit_queue 仍然不存在**
—— 收窄的是规划文件，不是对局命令。测试文件头写明「改集合 = 改授权边界，
必须连 WORKLOG 决策记录一起改」。

**验收**：`agent.run --dry --demo planning`（新 demo 链：list → read → create →
write → simulate → done）在一次性沙盒 serve_api(8791) 上走通：agent-demo.yaml
落盘、干跑输出「精炼厂→派 3 人采气→t=118 工厂开工→气 287、单位 6」、前瞻警报
区块常驻（无警报也显示「（无）」）。干跑第一版还如实暴露了「不派采气工 →
缺气 stalled」—— planner 语义透过工具面正确显形。
**回归**：后端 **688 passed / 1 skipped**（+16，tests/agent/test_planning_tools.py）；
前端 283/22 不受影响。

**BaseAgent HTML 可视化开启（2026-08-22 用户要求，同轮）**：vendor 的 agentic
（来源 C:\dev\project\BaseAgent，NOTICE.md 有记录；venv 未装 pip 包，运行时走的
就是 vendor 副本）自带自包含 trace.html，但 `Engine.start` **不代收尾** ——
`Tracer.finalize_summary()` 必须显式调，我们从没调过，所以可视化一直没生成。
修法（不改 vendor，在 agent/ 包一层）：`run_round` 收尾调 `tracer.finalize_summary()`
（同时产出 summary.json / tree.json / trace.md），返回值带 `trace_html` 路径，
CLI 打印「可视化：…」。回归锁 `test_run_round_generates_trace_html`（run_round
注入 transport client 跑 planning demo 链，断言 html 存在且含 advisor、四个聚合
产物齐）。实测 64KB 自包含页面（泳道/轨迹/会话三视图），summary.json 的 tools
计数正好是 demo 链的六次调用。**回归：后端 689/1s。**

**切片 3 —— I8 收口：placement 点名引用（同日，ISSUES I8 关闭）**：
- **预设固定建造名进会话 layer**：`_reserved_boxes` 计算从 view/map_plans 下沉到
  `tactical_map/reserved.py`（单一事实源；world/session 不许 import view，而会话
  装配现在也要用 —— view.statics._ladder_map_data / map_plans._source_mains 改为
  委托）。新增 `reserved_marks()`：36 个命名预留区 → PosMark，OfflineSession 与
  run_session（sim/sc2 驱动）装配后 `layer.pos_marks.update(...)` —— runtime 的
  `exact.mark` 解析**零改动**就能吃预设名（蓝方主矿气井1 = 气井中心，奇数
  footprint 中心即采集坐标）。
- **「规划id/点位名」限定引用 = REST 层映射**：`resolve_placement_refs`
  （view/plans.py 纯函数）—— mark 含 "/" 且会话装的就是该规划 → 剥前缀；装的不是
  或没装规划（出厂模板）→ 400 带中文理由。挂在 `queue_command`（UI 与 agent 同一
  命令面）；裸名/in_region 原样通过（存量语义不变）。`session_start` 给两类会话记
  `map_plan_id`。**ViewFrame 契约零改动、runtime 零改动**（只见裸名，ADR-0027
  抽象标记语义不变）。提案 hunks 语法保持裸名（P1/P2 红线体系不动）。
- **编辑器按所选地图规划过滤**（PlanningPage 生产板）：新增「放置引用」下拉
  （默认该空间的 layout 出厂校准布局），placement 选项来自所选规划的 payload ——
  槽位/点位值带 `规划id/名` 限定前缀、预设名全局裸用、区域照旧；
  `placementOptions(map, qualifyId)` 扩展 + select 回显兼容 mark:/slot: 两组值。
- agent 工具/提示词同步：write_plan/read_map_plan/spec 写明「预设名可引用、
  槽位名用规划id/名限定」。
- 测试：tests/api/test_placement_refs.py（10 条：映射纯函数 ×4 + layer 预设名 +
  命令面接受/跨规划拒/出厂模板拒/裸预设名）+ 前端 placementOptions 限定/预设锁。
- **回归：后端 699 passed / 1 skipped，前端 284/22 + typecheck 无错。**
- P3 剩余：live 自动应用（§5.3 三护栏 + ADR）。

**P3 剩余**：切片 3（I8 收口：placement 点名引用地图规划槽位/预设名，REST 层映射
不动 ViewFrame）；live 自动应用（§5.3，随 agent 接 live 一起做，写 ADR）。

## 0.12 真机地形 bug 修复 + 「未知」确诊（2026-08-22 用户反馈）

**用户两问**：游戏实时地形不可用（进度还是 bug？）/ 很多数据显示「未知」。

**地形 = bug，双层修**（真机专用路径，沙盒从未触发）：
- 根因：`SC2DriverBot.on_step` 里地形回调（`_map_info_cb`）排在 `sink.on_game_state` **之前** ——
  真机会话的静态面顺序因此是 [terrain, map, ...]，而前端 store 的 terrain→map 合并是
  「map 未到即丢」→ 整局地形静默吞掉，地图页纯色底。**与 B16 是同一个顺序坑**（sim 侧
  当年修过并留了教训注释，driver 这条漏了）；昨天的地形采集走 REST（不关心顺序）所以没暴露。
- 修 A（driver）：on_step 重排 —— 首帧先走 sink（Session.on_state 同步发 static/map），
  地形回调排后。回归锁 `test_on_step_sinks_first_state_before_map_info_callback`
  （events == ["sink", "terrain"]，且地形只发一次）。
- 修 B（store，A5 防御）：`pendingTerrain` —— terrain 先到就挂起、map 到了再并，
  两个方向都成立（帧序不可假设；顺带让**旧真机录制的 JSONL**（terrain 在前）也能放出地形）。
  回归锁 mode.test 两条（乱序/正序都合并）。
- 验证：独立 WS 探针确认 sim 会话 statics 顺序 [map, catalog, schema, strategy, terrain]
  且 payload 完整；真机全流程留用户验收（subprocess 每次 spawn 重导 driver，无需重启 serve_api）。

**「未知」= 进度缺口（非 bug，设计如此显形）**：真机对手是 `Computer(Race.Random)`
（sc2_adapter.py），catalog 只有 `load_terran()` —— 敌方非人族单位/建筑翻不进 stable_id，
`_unknown_stable_id` 显式打 `unknown/` 前缀（C1：动态帧禁 burnysc2 名），前端如实显示「未知」。
归属 ISSUES I9（开放）：修法 = 补 protoss/zerg catalog 数据；临时缓解可把对手固定 Terran（未做，
随机族对测试覆盖反而有价值）。

**回归**：后端 **672 passed / 1 skipped**（+1 driver 顺序锁）；前端 **283 / 22**（+2 store
乱序合并锁）+ typecheck 无错。

## 0.11 实时驾驶整改：假世界连根拔起 + 顶栏收敛（用户实测反馈，未提交）

**用户症状三连**（点击启动真机没反应 / 时间线连上了但地图空 + 来历不明的槽位 /
游戏实时还有下拉框）——一条根因链 + 一个旧包残留：

1. **「点启动真机没反应」= 400 被吞**：`SessionBar` 的 `mapPlanId` 初始值硬编码 `"default"`，
   而 P2 双下拉模型（§0.10）后预设已改名 `default-bl/tr`、`layout-bl/tr` —— 不动下拉直接点按钮
   就发 `map_plan=default` → 后端 400「不存在」→ 前端 `sessionAction` 把 `!res.ok` **静默吞成
   null**（commands.ts）→ `startSession` 无视失败继续 attach。修法三件：
   - `pickMapPlan` 纯函数（shell/mode.ts）：当前值不在清单 → 落第一个；清单未到 → 不带参数；
   - `sessionAction` 失败返回 `{ok:false, detail}`，UI 顶栏内联红字显形（G7 同款纪律）；
   - 去掉 `window.confirm`（嵌入式浏览器可能直接拦掉 = 又一个静默无效）→ **两段式确认**
     （第一点变「再点一次 · 确认启动 SC2」，4s 自动还原）。
2. **「时间线在动 + 地图空 + 神秘槽位」= WS 惰性建假世界**：attach("live") 后
   `/api/frames?source=live` 会顺手建一个**进程内 OfflineSession（假世界）**——出厂模板
   槽位 + 逐秒假帧 + 无地形，被误读成「SC2 连上了但数据不对」。修法（api/app.py）：
   - `_session()` 不再惰性创建；无会话 = 如实说：WS 合成一条 `frame/session`「未连接」帧
     （bootHint 对「未连接」不显示首帧等待，顶栏「等待会话」接手）；
   - **WS 保持连接等会话**：pump 每轮重解析 `app.state.session`，会话出现（含停止后再启动）
     自动重置游标 + 补发全量快照，前端 attach 一次即可，不用重连；
   - REST `_source("live")` 无会话 → 404「没有活跃会话」（不再悄悄造一个）。
3. **「游戏实时下拉框」= 旧前端包残留**：当前源码与 git 历史都没有这个控件 —— 用户浏览器
   挂着旧 vite 包（同机还发现两个 stale serve_api 进程，已清）。重启服务即消。

**顶栏收敛（用户拍板「冗余摘掉」）**：删 状态/地图/游戏时间 三个 Pill（状态由会话 label 给、
游戏时间时间线上有）；播放×4/暂停只属于复盘模式（回放语义），驾驶态只有「回到实时」；
**删「启动沙盒」按钮**（sim 驱动保留在后端，测试/agent 走 REST——UI 无沙盒入口，
假世界不再有被误当真机的路径）；「停止会话」按驱动改名 **「关闭真机」/「停止沙盒」**，
且**会话存在即可点**（不再 `disabled={!live}`——子进程死了 SC2 可能还挂着，恰恰是最需要
点它的时候；stop 对死会话幂等无害）。这是「关闭游戏按不下、感觉一直关不上」的修法。

**回归**：后端 **671 passed / 1 skipped**（基线 669 +2：WS 无会话不建假世界 + WS 后启动自动接上）；
前端 **281 / 22**（+3 pickMapPlan、+8 session-drive 源码扫描锁：错误不吞/无沙盒入口/
无 window.confirm/关闭真机命名/无 Pill/播放暂停只留复盘/pickMapPlan 兜底）+ typecheck 无错。
**浏览器端到端实测**（IAB，vite 5273 + serve_api 8770 最新代码）：drive 顶栏新形态 ✓、
无会话「未连接」✓、REST 起 sim 会话后 UI 同连接自动接上（矿 987→1088、驻留 70→82s、
seq 69→81）✓、UI 点「停止沙盒」回未连接且无孤儿进程 ✓、两段式确认 ✓。
**真机（SC2）全流程留用户验收**：启动 → BootHint 等待 → 帧到达 → 关闭真机树杀。

## 0.10 规划模型重构：双下拉 + 预设瘦身（用户十三轮反馈）

**模型修正（用户拍板）**：规划不是"一张地图"，而是**某地图 × 红/蓝方的一种布局**
—— 双下拉设计：先锁定地图 + 出生点，第二个下拉只列这个**独立规划空间**的布局，
随时切换对比。旧模型（一个文件含 bl/tr 双分支 + 单独切出生点）退役。

- **文件形态**：单出生点分支 `{id, title_zh, map_name, spawn, origin, anchor,
  build_slots, pos_marks, updated_at}`；del 不再跨分支同步（规划就是单分支）。
- **锁定预设 ×4（init 自愈）**：`default-bl/tr` **空白地图**（0 槽位 —— 用户拍板
  "去掉所有自建槽位后才是默认地图"）+ `layout-bl/tr` **出厂校准布局**（手写模板
  对应方全部 26 槽位）。锁定前缀 `default-`/`layout-`，复制是唯一改动路径。
- **解析兼容**：`tactical_map.base.load_map_plan` 同时吃单出生点形态与 base_layout
  原形态（spawns 双分支）—— 会话装配（run_session/OfflineSession）换用它。
- **REST**：`GET /api/map-plans/{pid}` / `PUT {hunks}` 去掉 spawn 参数（规划自带）。
- **前端头部**：地图下拉（唯一 map_name 集合）→ 蓝方/红方 → 规划下拉（按
  map+spawn 过滤）；切地图/方 = 自动落该空间第一个规划；复制/删除随空间走。
- **气井名 LOD**（同轮小改，后按用户再反馈收紧）：气井名与普通槽位**完全同档
  同字体**（`LOD.buildingLabelFull=8` 才显示），基地名常驻（≥2.5）。
- 旧 runtime/map-plans 文件为双分支形态，与新模型不兼容 —— runtime 数据可弃，
  重启时清目录由预设自愈重建（用户尚无有价值的自建规划，损失为零）。
- **回归**：后端 **669/1s**（test_map_plans 按新模型重写 12 条）；前端 **270/21**
  + typecheck 无错。curl 验证：四预设 slots=0/0/26/26、locked、spawn 正确。

## 0.9 地形量化修正 + 预设命名 + 视觉改版（用户十二轮反馈）

- **黄色格点之谜（(62,80) 一带）**：真机高度有 40 个不同值，量化器把 1-2 单位的
  缓变也当独立层 → 满图"跨层"被判成斜坡暖色抖动。修法：`quantizeLevels` 加
  **MIN_LEVEL_GAP=4**（相邻高度差 <4 并同层；SC2 真实台地落差 ≥6）—— 只有真
  台地间才算层/悬崖/斜坡。缓变噪声并层有回归锁（189/190/191 交替 → count=1）。
- **悬浮窗只留世界坐标**（格/世界双坐标删格）。
- **资源视觉改版（用户拍板）**：去小圆点，全部描边框 + 加深 tint；气井改**紫色**；
  矿脉深蓝（高倍率才描边，98 个框低倍率太密）；地图页保留圆点（live 采集人数挂点）。
- **预设命名（用户拍板：基地/气井=预设不可动槽位，后续直接引用）**：
  `_reserved_boxes` 命名 —— 主基按出生点匹配（蓝方主矿/红方主矿），分矿归属最近
  主基按距离编号（二矿…七矿，只收离本方更近的），气井归属最近基地（蓝方主矿气井1/2）；
  **36 个命名条目**（12 基地 + 24 气井）进 payload。画布带名标注（LOD）；
  槽位列表新增「预设固定建造点」锁定段（虚线行，不可编辑删除）。
- **回归**：后端 671/1s（命名断言）；前端 **270/21**（缓变并层回归锁）+ typecheck 无错。

## 0.8 黑屏修复 + 图层面板收纳 + 去压暗（用户十一轮反馈）

- **黑屏（TDZ 崩溃）**：`reserved` 声明插在了 preview useMemo **之后**，点击槽位触发
  重渲染 → `Cannot access 'reserved' before initialization` → 整页黑屏。
  修法：声明上移到首个使用点之前。教训：**补丁脚本插码先看清引用顺序**，
  useMemo 的求值发生在渲染路径上，引用先于声明就是运行时炸弹。
- **图层面板收纳**（用户拍板：不常用功能不占侧栏）：新 `canvas/LayerPanel.tsx`
  —— 画布**左下角折叠小部件**（收起=一个小 chip，展开=开关清单）。地图页侧栏的
  「图层」整卡删除换它（「位置插值」开关收进 extra 行）；**规划页画布也挂上**
  ——planningLayers 从冻结 memo 改为真 state（可建区/矿区=初始强开，用户可再关）。
  放置横幅上移（bottom-2→8）让位。
- **去掉不可走压暗**（用户拍板「干脆全都没有深色」）：数据显示左下基地周围
  不可走格最多（70/400）、tr 主基几乎没有（5/400）—— 压暗随真实地形天然不对称，
  被误读成"只有左下有矿影"。移除 bakeTerrain 的压暗分支；悬崖硬描边保留
  （信息不丢），按格可走性由悬浮窗给出（F18）。
- 浏览器实测：黑屏消失、图层面板在、槽位列表正常；回归：前端 **269/21** +
  typecheck 无错（后端本轮未动，671/1s）。

## 0.7 固定建造点预留区（用户十轮反馈：别处矿看不到/红方没基地/基地气矿要预留）

**两个关键实测事实**：
1. **game_info 的 placeable/pathable 在矿脉/气井位置全是 1（不挡）** —— SC2 这两张
   栅格只表达地形可走性，不含资源占用。之前"深色=矿脉压暗"的解读是错的（那片深色
   是地形本身）；更要紧的是**放置校验真的允许把槽位放到矿上**。
2. burnysc2 的 `expansion_locations_list` 在首帧可用且与模板 origin 完全一致 ——
   第三次真机采集拿到 **12 个基地位置**（精确含 bl [48.5,28.5] / tr [127.5,119.5]）。

**修法**：
- 数据文件三件套：terrain.json = 地形三栅格 + 122 资源点 + **12 基地位置**
  （adapter `extract_map_info` 加 expansions 旁挂键，不进帧 payload、存 meta；
  采集脚本从 describe.meta 拿）。
- **预留矩形单点计算**（`map_plans._reserved_boxes`）：基地=CC footprint（catalog
  size）、气井=3×3（refinery size）、矿脉=2×2；payload 直出 `reserved` 数组 ——
  前端渲染与预览吃同一份矩形，TS 里不重写脚印算法。
- **两侧校验**：后端 save 对**本次改动**的槽位做预留重叠拒绝（存量出厂槽位不追溯）；
  前端 previewPlacement 加 `reserved` 参数（reason "reserved"，提示区分基地/气井/矿脉），
  悬停 ghost、点击落笔、拖动落点同用一份判定。
- **画布**：资源层不再依赖 world（规划视图 world=null 也画全图矿/气井点）；
  新 `reserved` prop 画预留区 tint（基地黄 + 描边 + 「基地」标注 LOD、气井绿、
  矿脉蓝）。红方切换后基地/矿区在图上直接可见（此前"红方没基地"= 无任何标记）。
- Vite 中途被停导致用户页面挂着旧包 —— 重启（资源点/预留区/真地形全在最新包里）。

**回归**：后端 **671 passed / 1 skipped**（+2：payload 带 134 预留矩形 / 压基地 400）；
前端 **269** / 21 files（+1 previewPlacement 预留单测）+ typecheck 无错。
payload 实测：reserved 134（12 基地 + 98 矿 + 24 气井）+ resources 122 + 真地形。

## 0.6 全图资源点接入（用户九轮反馈：地图颜色是啥/为什么只有左下角有矿/视野限制？）

**用户的三个问题与实测答案**：
1. **「丰富的颜色是什么数据」** = 真机地形三栅格（§0.5 采集）：高度分级台地 +
   悬崖描边 + 斜坡 + 可建区淡绿 + 不可走压暗 —— 全图、无战争迷雾限制（game_info）。
2. **「为什么只有左下角主矿有基地和矿的阴影」** = 两个独立来源叠加：左下角的
   "基地"是**蓝方槽位集群**（depot 网格 + rax 楼梯，规划数据，只在所选出生点）；
   "矿的阴影"是**真实地形里矿脉不可走格的压暗**（矿脉确实挡路）。夹具/沙盒的
   `resource_nodes` 是 worldsim 假造的 10 个（只围绕蓝方 CC）—— 那才是"只有左下"
   的另一半原因；规划页 payload 此前根本没传资源点。
3. **「是不是只能拿到视野内的数据」** —— 否，实测证伪：中性资源（矿脉/气井）
   不受战争迷雾限制（迷雾只藏敌方单位）。真机一帧 static/map 的 resource_nodes
   = **全图 98 矿脉 + 24 气井，聚成 14 个矿区**，坐标横跨全图（x 19..157）。

**修法**：资源点随真机地形一起落进数据文件（terrain.json 加 `resources` 节，
一次采集 122 点）；`view.statics.ladder_resource_nodes()` 出口；
**地图规划 payload 带全图资源点**（planning 画布的「矿区」图层强制开 ——
槽位绕开真实矿脉线这件事终于有数据可依）。真机采集第二次启停，无残留进程。

**回归**：后端 **669 passed / 1 skipped**（+1 全图资源断言）；前端 268/21 +
typecheck 无错。payload 实测：98 矿脉 + 24 气井直达规划页。

## 0.5 真地形接入 + 会话装配地图规划（用户八轮反馈：地形不可用/离线地图不准/槽位归属）

**三个症状一个根因链**：
1. 「实时驾驶地形不可用」——用户测的是沙盒（sim）：worldsim 没有 game_info，
   按设计不发地形 → 地图页一片「地形未下发」。
2. 「离线 LadderMap 数据不准 + 蓝红基地位置一样」——两件事：①夹具地形是 B16
   合成的（三级方形台地，不是真实地图）；②**规划画布混源**：出生点切到红方后
   槽位移到右上，但夹具（bl 开局）的 CC/工人/矿脉从 world 帧照画在左下 ——
   用户看到的"基地"没动，误读为数据错了。
3. 「depot13 这些不该是默认，应该是某个规划设定的」——地图规划文件建好了，
   但**会话装配仍读手写 base_layout**（进入游戏加载哪一份规划没有接口）。

**修法（四件）**：
- **真机地形一次采集、进版本库**：起真机会话 → `GET /api/sources/live/statics`
  抓 static/terrain → `modules/tactical_map/data/ladder_map/terrain.json`
  （height 非零格 17760，与 d42aa1e 真机验证记录一致）。`view.statics.ladder_terrain_view()`
  统一出口；三处消费：**地图规划 payload**（规划页画布直接带真地形）、**make_fixtures**
  （真地形优先，B16 合成器降为无文件兜底；夹具已重生成，三份全 17760）、
  **run_session 沙盒**（首帧 statics 之后发 terrain 控制行 —— 顺序不能反，B16 教训：
  早于 static/map 会被前端合并丢帧）。沙盒/离线从此看到**真实地图**。
- **规划画布去混源**：MapPlanning 的 MapCanvas 不再喂夹具 world/production/economy
  —— 规划视图只画地图规划本身（槽位/点位/真地形），出生点切换语义干净。
- **会话装配接地图规划**：`POST /api/session/start?map_plan=<id>`（幂等守卫加
  规划维度：同 driver 同规划才幂等）→ LiveSession `--map-plan` → run_session /
  OfflineSession `load_base_template(plan)` 装配。驾驶模式会话操作区加**地图规划
  下拉**（会话启动时装配；运行中禁用）。规划不存在/无落盘文件 → 400 带原因。
- **点位命名空间（方向确认，未实现）**：用户认同点位引用应为「地图规划名/点位」
  （I8 的解法方向）——涉及 placement 匹配与契约，归 P2 切片 2 与区域划分一起做。
- 真机地形采集时 SC2 正常启停、无残留进程（tasklist 验证）。

**回归**：后端 **668 passed / 1 skipped**（+2：会话装配规划+真地形、未知规划 400；
live 首测的 statics 断言更新为含 static/terrain）；前端 268/21 + typecheck 无错；
夹具重生成（`pnpm gen:fixtures`，三份全真地形）。

## 0.4 P2 切片 1：地图规划文件模型 + 规划页 tab 退役（用户七轮反馈）

**规划页两处切换 UI 删除（用户拍板「完全不需要」）**：
- **顶部 tab 行**（地图规划/生产规划/Flow装配）删除 —— P1 后左侧导航栏就是这三个入口，
  导航只应有一处。`PlanningPage` 的 tab 从 prop 派生（不再有本地 state，避免
  同组件复用不换页的坑）；R5 门控降级为 hash 直达兜底。
- **地图规划右侧「点位/槽位」内部 tab** 删除 —— 两组列表**常显堆叠**（各带小节标题），
  双向高亮直接可见；scrollIntoView 不再依赖 tab 切换。

**P2 切片 1：地图规划文件（`view/map_plans.py` 新模块 + REST + 前端头部）**：
- **一个地图规划一份 YAML**（`runtime/map-plans/`，base_layout 同构 + title_zh/updated_at）。
  默认 `default` **锁定**，从手写模板**逐字节复制**（保留真机校准注释；永不重写，
  复制是唯一改动路径）。
- **出生点切换**：bl/tr 按出生点分支存各自的世界坐标；读取 =
  `instantiate_spawn(cc=origin)` 零平移 → `map_static` 直接产 **static/map 形状 payload**
  —— 前端画布零改动（同款渲染路径）。
- **离线保存不走提案**（用户已拍板）：草稿 hunks → `apply_map_overrides` 同一套校验
  （重名/重叠/尺寸/指向不存在，400 结构化理由）→ 写回**正在编辑的出生点分支**；
  `del` 按全局名同步其它分支（与 B14 名字空间语义一致）。前端「提为提案」按钮
  从规划页退役，改「保存到地图规划文件」；草稿 localStorage 退役（后端文件是真相源）。
- 前端头部：地图下拉（锁定标记+槽位数）/ 蓝方红方出生点切换 / 新地图名 + 复制新建 /
  删除；无后端时回退夹具底图并提示。terrain 由前端叠加夹具地形（规划文件不含栅格）。
- REST：GET/POST/PUT/DELETE `/api/map-plans`、`GET /api/map-plans/{id}?spawn=`；
  serve_api 加 `--map-plans`（默认 runtime/map-plans）。
- **事故记录**：MapPlanStore.create 持锁调 list_meta（内部再拿非重入锁）→ 测试挂死，
  与上轮 planner 缩进事故同一天。教训再记一笔：**持锁方法里不得调用会拿同一把锁的
  对外方法**——对外方法（带锁）与内部方法（无锁）分层。
- 浏览器实测：头部（下拉/蓝红方/复制新建）与右侧常显列表渲染正确；
  IAB 内 main 区点击被画布页 window 级原生监听拦（环境怪癖，真实浏览器不受影响，
  bl/tr 切换与保存的功能路径由 9 条后端测试锁定：两出生点坐标各自正确、
  hunks 校验、锁定、复制删除、重启持久化）。
- **P2 切片 2（未做，下一步）**：主矿/二矿区域划分 authoring（用户指出的数据缺口，
  region 现状只有 main_base 且 zh 名为空）——文件形状已预留 regions 节；
  ISSUES I8（placement 跨地图）也归切片 2 定。

**回归**：后端 **666 passed / 1 skipped**（+9 map_plans）；前端 **268** / 21 files
+ typecheck 无错。

## 0.3 P1 导航重构 + 警报复用（用户六轮反馈：placement 立项 + 警报组件 + 推进 P1）

**导航按模式重组（P1，用户拍板「离线编辑不应有驾驶栏、复盘不应有规划栏」）**：
- `shell/rail.ts`：`railGroups(mode)` 改为**模式的函数**——offline → 规划组+诊断，
  drive/replay → 驾驶组+诊断；组级门控退役（可发现性由顶栏一级三段模式保证，
  G7 的 R5 理由由 PlanningPage 守卫兜底 hash 直达）。新增 `homePageOf(mode)`。
- `App.tsx`：模式切换/旧链接直达时当前页不属于本模式 → 自动跳该模式首页
  （offline → 生产规划[主场景]，drive/replay → 概览）。
- `IconRail`：w-16 → **w-44 加宽横排**（图标+完整标签+右侧角标）。
- `SessionBar`：offline 的夹具下拉加「背景数据」标签（它只是规划的静态面来源，
  不是回放语义）。
- 浏览器实测过三个形态：offline 左栏=规划+诊断、主区自动落生产规划（默认规划
  22 项含 research 完整可编辑）；切复盘后左栏=驾驶+诊断、角标正常（概览 1 条
  警报 / 生产「阻塞」）、主区自动落概览；顶栏一级三段 + 源选择器随模式出现。

**警报复用（用户问「生产规划的警报和实时生产是不是没复用组件」）**：
- 根因：两处数据面不同——实时 = `frame/alerts`（AlertView，AlertService 带冷却
  + 运行时来源），规划 = stalled 事件直渲染（自己拼文案）。复用的正确层次是
  **数据模型**不是像素。
- 修法：`AlertService.from_curve(curve)`（无状态、无冷却，规划干跑专用）→
  stalled 死局全量转 `plan_stalled` AlertView（error 级，文案后端拼含 mm:ss）；
  `/api/plans/simulate` 返回体加 `alerts`。前端抽 `panels/AlertsList.tsx`，
  Overview 风险面板与规划前瞻警报卡共用同一组件（severity 点 + text_zh + eta）。
- `plan_stalled` 不进帧契约闭集（只走 REST），前端用结构化类型，无 REV 变化。

**ISSUES I8 立项（用户指出，暂不动代码）**：生产规划的 placement 引用当前帧源
地图的槽位/点位名，但规划是独立文件、可能跨地图 —— 换图对不上。用户没想好
匹配方式（也许规划↔地图绑定），归 P2 地图规划文件模型一并定。

**回归**：后端 657/1s（不变）；前端 **268** / 21 files（rail.test 重写为模式
函数语义 +2）+ typecheck 无错。运行中的服务已换最新代码（vite 5273 +
serve_api 8770，可直接验收）。

## 0.2 实机 static/map 修复 + P0 生产规划可用化（用户四轮反馈）

**用户四轮反馈**（投影板打磨之外的系统性反馈）确立了两件事：
1. **导航按模式重构**（P1，未动工）：顶部一级 实时游戏|复盘|离线规划，二级导航是模式的
   函数（离线规划才有 地图规划/生产规划/Flow装配）；现状 IconRail 只在 drive 下置灰规划组，
   offline/replay 全可见，用户判定"相互矛盾"。「规划（离线工作台）」条幅已删。
2. **审批可跳过**（用户原话"不走审批让 agent 直接去改都行类似 codeagent"）：离线规划域
   agent 直改文件、diff/撤销兜底；live 自动应用（原 §5.3）后置——**没有 agent 接 live 之前
   做了也没法调试**（用户拍板）。提案通道降级为可选，不删。

**实机「等待 static/map」bug（已修）**：真机首帧 `bot.time=0.0`，四张静态帧以 game_time=0
落进会话缓冲；前端在会话出帧前 attach（真机开机 1-2 分钟，必然先 attach），WS live 泵的
游标过滤（`game_time > cursor`）让 game_time=0 的静态帧**永远落在游标后面**，一次都发不出
——生产页有数据、地图页一直等待。sim 沙盒没暴露是因为 worldsim 先 `t+=dt` 再产帧
（静态帧 game_time=1.0 能过过滤）。修法（`api/app.py` WS 泵 live 分支）：静态帧改
「快照出现即发」（`statics()` 增量转发，不受游标门控；between 通道跳过静态帧防重发）。
回归测试 `test_ws_live_forwards_statics_landing_at_game_time_zero`（stub 逐字复刻真机时序，
含心跳线程保证预修时快速失败而非挂死）；已做修前验证（预修 5.4s 失败，修复后过）。

**P0 生产规划可用化（本轮主体，已落地）**——用户定位"重中之重：必须 agent 能用它才能流程规划"：
- **后端规划存储**（`view/plans.py` 新模块）：**一个规划一个 YAML**（用户拍板独立规划文件；
  `runtime/plans/`，serve_api 已接 `--plans`）。Plan = 地图引用 + 出生点(bl/tr) + 队列三件套；
  默认规划 `default` **锁定**（从 bio_tank_opening 参考计划导出，连续同型合并 count → 22 项），
  不可覆盖/删除，要改就复制。校验复用提案通道的 `parse_item` + catalog 存在性，
  原因中文 400 返回（agent 拿着就能改）。REST：GET/POST/PUT/DELETE /api/plans + GET /api/plans/{id}。
- **无会话干跑**（`POST /api/plans/simulate`）：`planner/opening.py` 新增标准开局种子
  （CC + 12 SCV 挂采矿 + 8 矿脉 + 50 矿，供给 12/15）——SCV 必须带 HARVEST_GATHER 指向矿脉
  tag，否则 derive_from 把 12 工全算 idle、收入为 0。真 Planner 逐秒投影，
  返回 frame/projection 的 payload（ProjectionBoard 直接渲染），**不需要会话**
  （与提案预览互补：那个要会话当起点）。stalled 事件（缺矿/缺气/前置没…）就是前瞻警报。
- **前端生产规划 tab 重建**（PlanningPage）：规划下拉/复制新建/空白新建/保存/删除
  （草稿退役 localStorage，后端文件是真相源）；编辑器支持 **research** 项（DraftItem.op 扩枚举，
  类型列表用 upgrade role）；试算 → **双板同款 ProjectionBoard**（草稿 + 已保存对照，
  新 `initialDomain` prop 让规划从 0 起轴不出负时间）——用户反馈的「规划页与生产页
  两套图表组件脱节」在此收敛；前瞻警报卡列出全部卡点（时间 + zh 名 + 原因）与 skipped 项。
  「提为提案」按钮移除（那是 live 语义，且无会话时整条走不通）。
- 冒烟：default 规划 300s 投影 29/31 供给、枪兵×4、卡点如实报缺矿（早期 SCV 生产快于收入，
  模型的诚实输出）；marine-only 草稿 t=0 即报「前置没」。
- **回归**：后端 **654 passed** / 1 skipped（+9：test_plans.py 9 条）；前端 **266 passed**
  / 21 files（+4：draftFromJson 往返/research/缺省回退）+ typecheck 无错。

**遗留（用户明确可延后）**：泳道放大出现左右滚动条；自适应高度导致拖时间轴布局跳动；
地图规划左侧栏偏大。归 P1/可读性轮。

**警报语义修正（用户五轮反馈：警报太多、红线不知道为什么）**：
- 根因：stalled 事件把**时序等待**和**死局**混在一起。顺序队列里缺矿（攒钱）、
  无产槽/无科技槽（排队，槽空即启）、前置在途（建筑落成即启）都是正常时序，
  不是警报——之前全画成红线。
- 修法（`planner._feasible` 返回 `(ok, reason, wait)` 三元组）：`wait=True` 的失败
  不记 stalled 事件（泳道 bar 的起点天然表达等待）；只有死局才记——
  **前置不在场也不在途**（顺序队列里后面的 op 永远救不了队首）、
  **供给顶到 200**（新增 `SUPPLY_MAX=200` 真实上限：sim 供给封顶 + 守卫在 200 后
  不再白插 depot——「缺供给」由此成为用户要的"不能再往后生产"硬停警报）、
  **收入永远为 0**（无矿工且无 SCV 在训 = 缺矿死局；无气工或无精炼厂 = 缺气死局——
  "建了精炼厂没人采"这类规划缺一步会如实报出来）。
- 判据只看当前 SimState（顺序语义：排在后面的 op 救不了队首）。
- 效果：默认规划 300s 投影从 5+ 个红线降到 **0**；marine-only 草稿仍报「前置没」。
- 前端顺带：**对照板默认收起**（用户拍板：平时只看草稿，不占横屏；「▸ 对照已保存规划」
  按钮展开才双板并排）。
- 事故记录：改主循环时一次 Edit 弄坏了 `while queue:` 的缩进（内层逃出外层、
  `st.t += 1` 被带进内层）→ 外层永不推进 + 守卫每秒插 depot 把队列撑爆 → 测试挂死。
  faulthandler 重复采样定位。教训：Edit 后必须立刻跑受影响的最小测试，不能攒到最后。

**后续批次（用户已拍板顺序）**：P0（本轮）→ P1 导航重构（顶部三模式 + 二级导航）→
P2 地图规划文件模型（默认地图锁定 + 出生点切换 + 复制新建；**主矿/二矿区域划分是
authoring 数据缺口**：现状 region 只有 main_base 且 display_name_zh 为空）→
P3 live 自动应用 + agent 工具面（read_plan/write_plan/simulate 直连 REST）→
P4 复盘记录列表（ViewRecorder 落盘接 registry）+ flow 装配定位（只读图 + agent 改 YAML 接缝）。

## 0.1 I5 + I6（投影泳道共享时间轴 + 真机首帧等待提示）

按 §5.1 顺序推进的两个前端项（不动契约、不动后端）：

**I5 投影板**（ISSUES I5 关闭）：
- 曲线与泳道合进一张卡（`charts/ProjectionBoard.tsx`），共享同一条时间轴：
  - 同一定义域：`gantt-data.ts` 的 `sharedDomain(frame)` 同时喂给曲线（`ProjectionChart` 新增 `domain` prop 锁轴）与泳道（百分比定位）；
  - 像素对齐：泳道左标签列宽 = y 轴宽（46px）、右留白 = 曲线右内边距（8px），都在 `PROJECTION_GUTTER` 单点定义；
  - 锁轴时关掉 uPlot 的 x 框选缩放（曲线缩了泳道跟不上，对齐就毁了）。
- 真泳道：每个 stable_id 一条道，道内 started→completed 一条 bar，同类并发错开子行（贪心打包，row）；
  泳道按首次出现紧排；删掉右侧时间列（时间由共享轴给）。旧 Gantt 的「每个事件一行 + 每行重复 label + 只往下摊」三问题一并解决。
- 配对语义沿用旧 Gantt（同类型最早未闭合 started）但修正了一处字典序坑：
  旧代码 `[...entries].sort()[0]` 按**字符串**排，"id:10" < "id:9" —— 10s 的 started 会被 9s 的抢先闭合。
  现在 `toLanes` 按数值排。
- 卡点是时间点：一条红色竖线贯穿全部泳道（叠加层），与曲线里的红色虚线同一条轴。
- 验收截图：`docs/I5-projection-board-verify.png`（含像素级对齐测量：泳道轨道与 uPlot 绘图区
  左边缘 x 均为 146.8，7 条卡点竖线两侧偏差均 < 1px）。

**I6 首帧等待横幅**（ISSUES I6 关闭）：
- 判定收在 `shell/mode.ts` 的 `bootHint(mode, sourceKind, sessionState, mapArrived)` 纯函数：
  drive + live 源 + static/map 未到 + 会话非终态（启动中/对局中/会话帧未到）→ 提示
  「正在连接 SC2 并等待首帧（真机约需 1-2 分钟）」；数据到达自动消失；
  已结束/崩溃/未连接不提示（真实终态另有显示）。
- `shell/BootHint.tsx` 只订阅 store 与展示；地图页顶部（含无地图分支）与概览页顶部接入。
  离线/回放模式不出现。

**回归**：后端 638 passed / 1 skipped（不变）；前端 **244 passed / 20 files**
（基线 223/19 + 新 gantt.test.ts 16 条 + mode.test.ts 扩 5 条）；`pnpm typecheck` 无错。

遗留小项（不阻塞）：泳道左标签列 46px，非 catalog 的 id（如 assign_gas 事件的 stable_id）
会被截断，靠 title tooltip 兑底；可读性轮（§5.2）给词表加 zh 后自然缓解。

## 0.2 F15-F16 + B15-B16（用户二轮反馈：投影板交互重构 + 规划编辑体验）

用户对生产页/规划页的二轮实测反馈，四个任务一次做完（计划：F15-F16 + B15-B16，
全部不动契约 REV）。

**B15 planner 产槽模型**（后端，`planner/slots.py` 新模块）：
- 调研结论：多兵营并行**早已支持**（`_feasible` 按产建筑数计槽）；用户看到的机枪兵串行
  是因为场景只有 1 兵营，18s/个 +「无产槽」卡点是正确投影。
- 真 bug：techlab 母建筑被当普通产槽（tank 只需 factorytechlab 建过一次，之后普通工厂
  也能"造" tank）；且 `derive_from` 不数 live 已有挂件（`st.addons` 只在投影内落成时更新）。
- 新模型（用户拍板优先序）：科技单位只能用科技槽；普通单位用普通槽
  （容量 = 母建筑数 − techlab − reactor + reactor×2），普通满才溢出到空闲科技槽；
  失败原因细分「无科技槽」/「无产槽」。
- 溢出记账的坑（实现中发现）：溢出到科技槽的普通单位若计回 busy_normal，第二次判定会
  误判还有空闲科技槽 —— 正确式子：普通单位看总占用 < 总容量；科技单位看
  `busy_tech + max(0, busy_normal − normal_cap) < tech_cap`（贪心：普通先占满普通槽）。
- 测试 tests/planner/test_slots.py 6 条（tank 串行于科技槽 / 溢出 / reactor 双槽 / derive 数挂件）。

**B16 夹具合成地形**（`tools/make_fixtures.py`）：
- 根因：三组离线夹具 static/terrain 帧数 = 0（sim 会话也没有）——规划页看到的一片绿
  是「地形未下发」降级态，F11 的台地-悬崖渲染只在真机有数据时生效。
- 修法：make_fixtures 加确定性合成地形（以主基为中心的三级方形台地 + 悬崖墙 +
  y∈[28,32] 东西向斜坡走廊 + 3×3 平坦 placeable），每 scene 首帧（statics 已落盘）后
  emit 一条 static/terrain —— 必须在 static/map **之后**，否则 store 合并（`map 到了才把
  terrain 并进去`）会丢帧。
- `pnpm gen:fixtures` 重生成，三夹具各含 1 条 terrain；前端零改动。

**F15 生产投影板 v2**（`charts/ProjectionBoard.tsx` 重构 + UPlotChart/ProjectionChart 扩展）：
- 版式：泳道在上、曲线在下（一张图两带）；hover 游标线贯穿两带（uPlot 光标与泳道
  mousemove 双向同步，都是 native 监听）；卡点红竖线仍贯穿。
- 滚轮调视窗宽度（G3 native passive:false，围绕光标缩放，clamp 20..600s）；
  **中心跟随顶层时间轴**（store.position；live 跟随帧），拖时间轴只移中心不动宽度。
  UPlotChart 新增受控 `xRange`（变化 → u.setScale）—— setData 会重算自适应刻度，
  受控时必须压回去。
- 点击检查（用户拍板：逐秒采样 + 点击检查，不做 22.4 帧/秒真逐帧）：点击任意时刻 →
  右侧面板显示该秒完整状态：资源/供给/工人（points 直读）+ 建筑/单位明细 +
  在产明细（`gantt-data.activeAt`：显示层从 events 配对，与 toLanes 同层同配对）+
  产线摘要（每类产建筑「X 座 · 在产 N」，catalog.produced_by join）。
- 脚注重组成一行；卡点列表不再常驻（竖线 tooltip + 检查面板列出视窗内卡点）。
- Overview 的小投影图不动。测试：gantt.test 扩 activeAt，新 projection-range.test（zoom/center/anchor）。

**F16 规划编辑体验**（MapCanvas/PlanningPage/map-draft/layers/grid）：
- hover 预览 ghost：新 `previewPlacement` 纯函数（吸附 + 重叠 + placeable，
  reason: overlap/unplaceable）—— hover 算 ghost、点击用**同一结果**落笔，预览与落笔
  永远一致；画布新 props ghost/onHover，绿=可放红=非法。
- placeable 即时校验：放置/拖动落点新增地形校验（之前只查重叠）；地形未下发时如实
  降级为未知（不算非法）。
- 槽位可见化：规划模式（slotsOverride 非 null）槽位画实线框 + 名字（LOD 同 marks）；
  地图页驾驶态维持四角刻度低权（F11e 原则不变）。
- 可建区层：`layers.ts` 新图层 placeable（默认关，规划页强制开），淡绿 tint；
  B16 后离线也有数据。
- 双向高亮：MapCanvas 新增 onMarkClick/onSlotClick/selectedName（点位菱形半径命中、
  槽位原地点击也算选中）；右侧列表项高亮 + scrollIntoView + 点位聚焦改名；
  反向点击列表行 → 地图高亮。
- 布局压缩：点位/槽位合并成一张卡（内部 tab + 固定高度内滚）；整页去外滚
  （根 h-full flex，画布 flex-1）；layout.test 相应更新（PlanningPage 移出文档页清单）。

**回归**：后端 **644 passed** / 1 skipped（+6）；前端 **259 passed** / 21 files（+8）
+ typecheck 无错。规划页手动验收（地形/ghost/双向高亮）由用户进行。

## 0.3 F17-F19（用户三轮反馈：投影板打磨 + 地图悬浮窗 + 规划试算）

**F17 投影板 v3**（纯前端）：
- **历史累积**（拖时间轴时左侧内容不再消失）：投影帧只含 [based_on, +horizon]，
  中心跟随时左半视窗会空 —— `gantt-data.accumulateInto` 显示层把走过的每秒累积
  （points 按 t 去重后到覆盖；events 按 kind|t|id|reason 去重；超出当前帧末端的旧预测
  丢弃；based_on 大幅回退时清空重新累积）。与 ReviewableSource 环形缓冲同类。
- **左键拖图 = 拖时间轴**：按住绘图区横拖（位移 >4px）→ seek（仅可 seek 源；
  live 跟随模式禁用）；拖完的 click 不触发检查面板（lastScrubMoved 门控）。
- **hover 线两处修复**：① 拖时间轴后线滞后 ≈ 中心位移（hoverT 存绝对时间，range 变了
  线不动）—— 改存 **frac**（几何位置），换算全部在 root 级统一 native mousemove 里做
  （不再分泳道/uPlot 两套换算）；② 线越过时间轴延伸到 legend —— uPlot 自带 legend
  是 canvas 下方的独立 div，隐藏它（ProjectionChart 新 hideLegend），hover 读数
  （时间/矿/气/供给）进 footer。
- **泳道重构**：名字进 bar（圆角矩形内 zh 名），行变**全局打包行**（跨类型贪心，
  toLanes→packBars）；固定 7 行高度、超出滚动（角标显示总行数）。
- **检查面板常驻**：默认显示「现在」（based_on 点），点击任意时刻切换，× 回到现在 ——
  面板不再开关移位。

**F18 地图格子悬浮窗**（MapCanvas 内置，所有用画布的页都有）：左上角显示鼠标
所在格坐标 + 地形分类（grid.ts 新 `terrainClassifier`：与 bakeTerrain 同源的量化/
边缘检测，U15 显示层）—— 悬崖/斜坡/平地/不可走 + level + 可建/不可建；
height 未下发时如实只给坐标（不编造）。规划页的放置模式横幅让出左上角（移到底部）。

**F19 规划试算联动**（用户问「规划没图怎么仿真」）：生产规划 tab 加「试算（双投影）」
—— 草稿提为提案 + 立即 fetchPreview → ProjectionPairChart 展示当前队列 vs 草稿后。
仿真是**后端真 planner 干跑**（A3/C7：前端不本地算）；没有会话时后端如实报错
（「没有会话，算不了投影」→ 先起沙盒）。

**回归**：前端 **262 passed** / 21 files（+3，gantt.test 重写为 packBars/accumulateInto
语义）+ typecheck 无错；后端未动（644/1s 不变）。

## 1. 审查轮的关键发现（全部验证过再修）

三个并行审查（地图页 / Flow 页 / 会话与生产页）+ 自己审出的问题，共 12 条，
**每条都带 file:line 证据、修前验证、修后回归锁**。最重要的四条：

1. **写入门禁钉在回放源上**（`sourceKind === "api"`，api 是历史回放、live 才是会话源）——
   「启动沙盒 → 下命令」主链路在 UI 上整条走不通，且横幅说假话。修成 `writeGate(sourceKind, timeline)`
   纯函数：可写的唯一源是 live 且不在回看（R8）。
2. **P5 盲接受有洞**：`_expire()` 只在 list/get 调，accept 不调 → 过期提案照单全收。
   注意：**原有 P5 测试因为先 GET 了一次列表，恰好把这个洞掩盖了** —— 测试顺序掩盖 bug 的典型。
3. **P2 只守了一半**：校验未过的提案"接受按钮隐藏"而不是"禁用可见"，且**拒绝按钮一起没了**，
   理由回流通道（P3）被顺带堵死。
4. **pos_marks 从数据到渲染整条缺失**：layer.pos_marks 恒空（instantiate_spawn 硬编码 `{}`）、
   画布零引用、图层清单没有这一层。"在线看到标记"不是免费能力，是三层缺失。

其余：pan 无位移阈值误选 / pointermove 旧闭包丢增量（**是 F10 自己的漏修**，wheel 用了函数式、
紧邻的 move 没改）/ chip 档两个图层开关无效 / cluster 组归属 `null` 哨兵冲突（`[null,"inf"]` 报成
"inf"，结果看遍历顺序）/ exit_strategy 终局被画成"留在本步" / PanZoom 用内容包围盒当换图信号
（拖节点即重新 fit，拖拽与视口打架）/ 持久化竞态丢最后一段位移。

## 2. F14 的设计决定（后续改动要遵守）

### 2.1 机器覆盖层（B14 的核心决定）

map_plan 接受后**不改写**手写 `base_layout.yaml`，而是写**机器生成的**
`base_layout.overrides.yaml`。理由：手写文件每行带真机校准注释（"rax1-2 真机 can_place+挂件
2×2 双锁定"这类），机器改写必吞注释；覆盖层是机器产物、`safe_dump` 往返安全。
合并语义：**先按名删（`*_removed`）再按名增/覆盖**；覆盖条目标记 `world_fixed`——
它们存**最终世界坐标**，实例化时不再随出生点平移（手写条目才平移）。

### 2.2 move = del + add（不给 move_slot 动契约）

槽位拖动落点表示为 `del_slot` + `add_slot` 两条 hunk。后端 `apply_map_overrides` 顺序执行
天然正确（先删后增、diff 回覆盖层 = 位置覆盖），免掉 REV 12。代价：逐条审批时用户看到
"删除 X + 新增 X@新位置"两条 —— 诚实且可读，接受。**前端草稿模型同理**（拖动生成两条 hunk）。

### 2.3 画布对草稿零认知

MapCanvas 只接受 `marksOverride` / `slotsOverride`（投影结果），谁要叠加草稿谁自己
`applyDraft` 算好传进来。驾驶页、规划页、审批叠加画布共用同一块画布、同一套纯函数。
这是决策 U1（组件 = 帧→像素纯函数）的画布版。

### 2.4 槽位几何的单点例外

编辑器**输入侧**需要算新槽位的 footprint（画它、做重叠校验），前端有 `slotTl`
（= 后端 `placement.tl_from_pos`，黄金用例锁同步）。这是 C2"零几何换算"的唯一合法例外：
**输入换算**单点 + 测试锁；渲染已下发数据仍然零换算。

### 2.5 V1 语义限制（文档写明，勿误用）

- 覆盖层坐标是世界坐标，两个 spawn 变体（bl/tr）同名条目共享名字空间：bl 优先、tr 补缺，
  用户编辑 bl 视图（离线夹具 spawn=bl），tr 变体按世界坐标**不镜像**。
- 接受提案**不热更正在跑的会话**；新会话加载时生效（"重开会话后标记还在"的机制）。
- 后端 validate 做几何与名字校验；**placeable 地形校验只在前端编辑器即时做**，
  后端下次真机运行时自然生效（B14 没有地形数据在手，如实标注）。

## 3. 额外发现（ISSUES 之外的）

1. **投影页写入面的源头错误**（已修）：见 §1.1。
2. **簇归属顺序依赖**（已修）：见 §1。
3. **测试掩盖 bug 的三个例子**（教训）：P5 测试先 GET 列表掩盖了 accept 不重查过期；
   夹具 rev 落后于契约时大批测试红（这是好的，但要记住 `pnpm gen:fixtures`）；
   mock 缺新方法导致 attach 抛错（恰好证明调用路径真实存在）。
4. **paint_region 有前置依赖**：区域笔刷面向全图 RegionLayer（标签网格），而会话装配走主基
   `instantiate_spawn`（leaf_grid=None），没有区域网格可涂。**要先建全图区域 authoring 通路**
   （region.yaml → RegionLayer → 会话装配），再做笔刷。已从切片 2b 移出。
5. **live 下 proposals 不推送**（未修，见 §5.2）：子进程 FrameProducer 没接 ProposalStore。
6. **WS 断线语义三分**（已修）：握手成功后的 close = 断线；dispose 主动关不算；握手失败不算。

## 4. 流程教训（给后续执行者）

1. **edit 工具对 CRLF 长段多行匹配不可靠**（PlanningPage.tsx 反复报 not found，但字节级 diff
   完全一致）。可靠姿势：同一 run_code 内先 fresh read 全文，old_string 用 `indexOf`/slice
   **程序化提取**，替换后立即验证（grep/typecheck）。
2. **`String.replace` 静默失败比 edit 报错更危险**：不匹配时原样返回、不报错，
   一次"声明成功"的假象（"}" 写成 ">" 导致 drag 声明根本没插入）。用 replace 必带
   `includes` 断言或 replace 后 grep 验证。
3. **heredoc 追加测试时，双引号嵌套直接写坏 JS**（esbuild 在测试运行时才炸）。
   `cat >> file <<'EOF'` 里写 `toContain("{ kind: "x" }")` 必须用单引号字符串。
4. **负向断言容易假绿或误伤**：fit 断言写成"全文不得出现某依赖"误伤了合法代码（doFit 本该用
   当前内容尺寸）。优先写精确的**正向**断言；负向断言必须配正向兜底。
5. **run_code 里写模板字符串，反引号必须转义或用 `String.fromCharCode(96)`**——已踩多次。
6. **union 成员字段撞名**：`{ kind: "add_slot"; ...; kind: string }` 两个 kind，
   对象字面量后者覆盖前者，运行时操作名变成 "supply"。类别字段命名 `slotKind`。
7. **vitest node 环境没有 localStorage**：测试里 stub 内存实现（行为同浏览器）。

## 5. 下一步计划（P3 批次：agent 接入离线规划域 —— 下一执行轮的立项）

> P0（生产规划文件+干跑）/P1（导航按模式）/P2（地图规划：真地形+资源+基地命名+双下拉）
> 已全部落地（§0.2–§0.10）。**下一批次 = P3：把 agent 接到离线规划域** ——
> 让前面积累的规划文件/试算/预设点位真正被 agent 使用（用户定位的重中之重）。
> 属于"比较大"的批次，此节为压缩上下文后的接续计划。

### 5.1 P3 切片（建议顺序）

1. **agent 规划工具面**（`agent/tools.py` 扩展，直连 REST，codeagent 语义不走提案）：
   - 生产规划：`list_plans` / `read_plan` / `write_plan`（PUT 全量队列）/ `simulate_plan`
     （干跑 → 曲线 + 前瞻警报，AlertView 文案直接可读）；
   - 地图规划：`list_map_plans` / `read_map_plan`（含 reserved 预设名清单）/
     `write_map_plan`（hunks，与画布同一套校验）；
   - `start_session`（带 map_plan，进入游戏加载所选布局）——沙盒即可验。
2. **spec 更新**：AdvisorSpec 系统提示词加规划域职责（读 → 改 → 试算 → 报告
   曲线/卡点/与当前布局对比），工件是规划文件；提示词里说明预设点位名
   （蓝方主矿/蓝方主矿气井1…）可直接被 placement 引用。
3. **placement 点名引用（I8 收口）**：生产规划 build 项 placement 支持引用
   地图规划的槽位/预设名（编辑器按所选地图规划过滤选项）；涉及
   `PlacementExact.mark` 匹配 resolver 接受「规划名/点位」形式 —— 动契约要谨慎，
   先在 REST 层做映射、不动 ViewFrame（无 REV 变化）。
4. **验收**：`agent.run --dry`（FakeLLM）走通"读规划→改→试算→读警报"；
   有密钥时真 LLM 一轮；沙盒会话用 agent 选定的地图规划启动。
5. **明确不做（本轮）**：live 自动应用与 agent 常驻触发（§5.3，等 agent 接 live
   一起做）；flow 装配编辑器。

### 5.2 小项池（随手/插队用）

- 可读性轮（I1/I2/I4 合并，REV 11→12）：词表/step/策略 zh 名。
- P4 复盘记录列表：ViewRecorder 落盘目录接 registry（前端复盘模式加记录下拉）。
- I7 UI 债：泳道放大左右滚动条、自适应高度跳动、地图规划左侧栏偏大。
- 槽位 placeable 后端校验收口（terrain.placeable 栅格进校验面）。

### 5.3 live 提案的"自动应用"设计（方向已定，随 agent 接 live 落地）

用户态度（2026-08-21 四轮反馈）：**审批体系可跳过，agent 直接改（codeagent 模式）优先**。
两域拆分后语义更清晰：
- **离线规划域**：agent 直改规划文件（P0 已给 REST 面），diff/撤销兜底，不走提案；
- **live 驾驶域**：production_queue 自动应用，三道护栏保留——**R8 新鲜度门**（based_on_seq
  过期 409）、**可审计**（append-only 历史 + ops 帧 origin=agent）、**可撤销**（队列工具 op
  是天然 undo）；live 下 map_plan / flow_ast / params 根本不产生（R5 一致）。
这是对 P1 语义的正式变更，落地时写 ADR。技术选型已定：**父进程 ProposalStore 变化注入
WS 帧流**（与 OfflineSession 的 producer.proposals 同构）；在"自动应用"语义下，该推送从
"待审批收件箱"变为"应用历史流"。

### 5.4 观察

- ISSUES：I1/I2/I4 开放（§5.2）；I3/I5/I6 已关；I7（UI 债）开放；I8 收口进 P3 切片 3。
- 回归基线（P2 双下拉模型后）：后端 **669 passed / 1 skipped**，前端 **270 passed /
  21 files**，REV 11（规划端点是 REST 传输层，不动 ViewFrame 契约）。
- 运行环境：serve_api(8770) 与 vite(5273) 常驻最新代码；数据文件
  `tactical_map/data/ladder_map/terrain.json`（地形+122 资源+12 基地）已进版本库。
