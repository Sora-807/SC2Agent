# 策略 DSL v0.2 —— 清死字段 + 语义对齐 + 语法加固计划

> 目标:消灭"声明了但静默不生效"的语法面,补齐编译期校验漏网项,统一单位命名到 stable id。
> 基线:全部测试绿;flow(战斗)端到端真机验通;生产运行时真机验通(tank 全链)。
> 每项任务完成后必须:`uv run python -m pytest tests -q` 全绿,再进入下一项(仓库惯例)。
> 来源:需求文档 v0.1(红线 R1-R8)、P0-影响边界、ADR-0027/0029、模块审查结论。

## 0. 本轮范围(用户已确认)

- ✅ AST 加固 + 语法糖(命名参数、defs 别名、编译期 arity/type 校验、YAML bool 陷阱拦截)
- ✅ 单位命名统一到 stable id(flow 内部一套词汇)
- ✅ 清死字段 + 语义对齐(删/实现死配置,定 blocked 语义,补编译校验)
- ❌ 不做:表达式字符串 DSL、生产 authoring 统一(一份 YAML 驱动 planner+runtime)、
      计时器做实(T8)、step 内直接写 next(替代 edges)
- ❌ 不做:OP_CATALOG 增删(原子目录只增不改红线不动)、Operation 形态改动

## 0.5 评审结论与补漏（复核：基线核过 + 两项已落地）

**结论：计划方向正确，顺序也对（先清 IR，再谈表层语法），可以执行。**
事实前提逐条核过全部成立：基线 `309 passed`；`on_exit` 只在 manifest 出现、引擎从不读；
`ActionRequest` 只定义+导出、零实例化；`QueueItem.when` 无消费方；`allocator.refresh` 只用 `target`
（`min`/`max` 从未参与）；`runtime._drain` Phase 2 确实贪心 `i+=1`，与本模块 docstring 第 3 条相反，
未知 op 确实静默跳过。

为什么这轮值得先做（对后续 LLM 产脚本的意义）：
- `PREDICATE_SIGNATURES` 是**未来提示词自动生成 / 表达式解析器 / 可视化编辑器共用的同一张表**；
  先清 IR 再做投影，顺序对。反过来先做 DSL，会把 on_exit/位置参数/静默 timer 烧进表层语法和提示词。
- 命名参数 token 实测：简单节点 `arrived` 48→63 字符（+31%），深嵌套节点配 `{ref}` 155→82（**−47%**），
  整体持平甚至更省，同时把"位置参数写错顺序"（LLM 高频错误、当前校验器**完全抓不到**）变成编译错误。

### 补漏项（H=洞，J=判断调整，A=建议加）

| # | 项 | 状态 |
|---|---|---|
| H1 | T4 队首 block 缺失速可观测性（永久阻塞静默冻结整队，违反红线 #4；live agent 也瞎） | **已落地** |
| H2 | T3.4 `min` 滞回的 `min=0`/省略边界会把补兵整个关掉（样例 min=target=max=20 掩盖） | **已落地**（T3：省略=target 下限，0=只在空组补） |
| H3 | T2c 的 bool 陷阱只查 `op`，漏了 `step_id/kind/reason/group_slot/type/action_atom/edges.from,to/...` | **已落地**（T2：`_check_identifier` 覆盖全部标识符字段） |
| H4 | 缺 ADR-0021 明文要求的图校验（不可达 step + 环出口）；含"环出口可以是 exit_strategy"的坑 | **已落地** |
| H5 | T1/T5 漏了 `run_*.py`（catalog 必传会让 run_flow_arrived/run_flow_slice 构造期 ValueError） | **已落地**（T1/T2/T3 一起迁移 + 离线编译验证） |
| H6 | D9 的 "None→False" 是静默降级：`not (a>b)` 会翻成 True，`!=` 遇 None 也返 False | **已落地**（T2：逐运算符定义 + `eval_diagnostics` 留痕） |
| J1 | D5 删 `on_exit` 同意，但要落 backlog（多实例/hot-swap 轮恢复），别变成"谁也说不清为什么没有的字段" | **已落地**（T3：顶层键白名单 + `REMOVED_KEYS` 墓碑，写了就报错并说明去哪了） |
| J2 | 运算符保留 args 同意，但顺手把 arity 写进签名表（当前 `{op: and, args:[x]}`、`{op: not, args:[a,b]}` 都不报） | **已落地**（T2：`OPERATOR_ARITY`） |
| J3 | `point_toward` 参数名 `from` 是 Python 关键字（生成 TypedDict/解析器/提示词都会绊）→ 改 `origin` | **已落地**（T2） |
| A1 | T2 收尾加 `flow/vocab.py: dump_vocabulary()`（签名表+OP_CATALOG+UNIMPLEMENTED 导出机器可读词表） | **已落地**（T2：+ `render_prompt_card()` 2.5KB 卡片 + 漂移守卫测试） |

### 实施中发现的修正（比原判断更好的答案）

1. **原提"`loop_limits` 升级为必填"改成"引擎全局默认上限"**。ADR-0021 §4 第 3 条本来就写的是
   "系统全局默认上限防意外死循环"。必填会让 `test_bio_push` 等无 loop_limits 的合法环形策略全部报错；
   改为 `engine.DEFAULT_MAX_STEP_TRANSITIONS = 200` 兜底 + 编译期只校验键白名单/正整数，零 authoring churn，
   且"没有任何配置能让引擎无限转移"这条保证更强。
2. **环出口判定必须把 `exit_strategy` 算作出口**。`docs/tank_marine_push.yaml` 的蛙跳环
   `{tank_hop, siege_gate, inf_hop, threat_gate, inf_wait}` **没有任何出边**，靠 `exit_strategy ARRIVED` 出环 ——
   朴素实现"环必须有出边"会拒绝真机验过的样例。
3. **环校验的健全性依赖新增的"死边"校验**：否则一条没有任何 `exit_step` 会走的边就能假冒"出环 edge"。
4. **队首 block 的两个真实回归点**（T6 前必须知道）：
   - `run_tank_marine_push.py` 的 `army` 队列把 marine(兵营槽) 与 siegetank(工厂槽) 混排 ——
     block 语义下坦克要等 20 机枪训完。**已按脚本自己的"分队列表达并行"原则拆成 `army_rax`/`army_fac`**。
   - `assign_workers` **不能**豁免队首门控：`build refinery → assign_workers(gas)` 的顺序是有意的，
     越过阻塞队首会让 WorkerAllocator 找不到气矿槽、意图静默蒸发（已加反例守卫测试）。
5. **`run_full_flow.py` 已经是坏的**（a41abc9 并行建造重构遗留）：`_build_flight`→`_build_flights` 失效引用，
   首个 tick 日志就 AttributeError，T6 冒烟必崩。本轮顺手修了（含 `run_tank_production_check.py`）。

### 建议执行顺序（T4 与 flow 侧零耦合，且是唯一引入新故障模式的改动，单独提交单独验）

```
T4(+H1) → 真机验 → T1 → T2(+H3/H6/J2/J3/A1) → T3(+H2) → T5(+H5) → T6
  ✅已做                                                    ↑ H4 已独立落地（不依赖 T1/T2）
```

## 1. 决策记录(每项一句话理由)

| # | 决策 | 理由 |
|---|---|---|
| D1 | flow 类型引用全部 stable id;FlowEngine 构造**必传 catalog**(None 报错) | 消灭双词汇;catalog 属 game 包,flow 可引用(架构红线允许);归一化路径简化 |
| D2 | 谓词/空间工具改**命名参数**;比较/逻辑保留 args(运算符自然位置) | 位置参数不可自解释;运算符是前缀序,保留 args 代价为零 |
| D3 | 新增 `definitions` 别名节,when/do-params 可 `{ref: name}` | 消灭 4 处重复的 point_toward 表达式与双份 READY 条件 |
| D4 | 编译期查:谓词 arity+参数名、group_action 缺 type、param/var 引用、重复 step_id、多实例、composition min≤target≤max、on_exit/loop_limits 取值、timer 写操作 | 运行期 TypeError/KeyError 是 R7 红线违背,全部提前到编译期 |
| D5 | **删除** on_exit(单实例引擎下 release 无可观察效果)、QueueItem.when(条件项等 authoring 统一轮)、ActionRequest(无人实例化)、live_editable 示例(无消费) | 死字段是"虚假承诺"语法,最坏;实现留到有消费方的轮次 |
| D6 | composition.min **实现** S3 滞回(低于 min 补到 target,never 超过 max) | 字段语义清晰、改动小;S3 承诺兑现 |
| D7 | 生产队列恢复**队首门控(block)**语义(与模块 docstring/P0/S11 一致);未知 op 不再静默跳过,记入 dropped | 乱序执行破坏 build order 语义且跨帧超支;若 T6 真机吞吐受损,后续轮加 per-queue 开关 |
| D8 | timer:start_timer/stop_timer 与 timer_elapsed 一样**编译期拒绝**(进 UNIMPLEMENTED_DO_OPS) | 写被允许/读被拒绝的不对称 = 静默无效;T8 实现时一起放回 |
| D9 | and/or 短路求值;比较运算遇 None 操作数返回 False | 空组 group_center=None 参与比较不再崩(R7);短路消除多余求值 |

## 2. 任务总览与依赖顺序

| 顺序 | 任务 | 依赖 | 产出 |
|---|---|---|---|
| T1 ✅ | 词汇统一:flow 全 stable id + catalog 必传 | 无 | allocator/predicates/engine 匹配重写 + 全部 flow 测试迁移 + run_*.py |
| T2 ✅ | 语法层加固+糖:签名表/命名参数/defs/校验补全 + A1 词表导出 | T1 | predicates 签名表 + manifest 校验 + engine 求值 + 26 个新测试 |
| T3 ✅ | 死字段清理:删 on_exit/QueueItem.when/ActionRequest,min 滞回 + 顶层键墓碑 | T1 | 三处删除 + allocator 滞回 + 测试 |
| T4 ✅ | 生产语义对齐:队首 block + 阻塞可观测(H1) + 未知 op 记 dropped | 无(与 flow 侧零耦合) | runtime 改动 + 测试 + docstring 对齐 + run_*.py 日志 |
| H4 ✅ | 图级编译校验(不可达 step/环出口/死边)+ 全局转移上限兜底 | 无 | manifest/engine 改动 + 10 个新测试 |
| T5 ✅ | 样例与文档迁移:tank_marine_push.yaml(+definitions)、P0/需求文档/test-plan | T2+T3 | 新语法样例 + 文档同步 |
| T6 | 全量回归 + 真机冒烟(run_full_flow / run_tank_marine_push) | T1-T5 | pytest 全绿 + 真机日志证据（真机需人执行）|

执行顺序:**T4(+H1) ✅ → H4 ✅ → T1 ✅ → T2(+H3/H6/J2/J3/A1) ✅ → T3(+H2) ✅ → T5(+H5) ✅ → T6（离线部分 ✅，真机待人执行）**。
T4 与 flow 侧零耦合,且是本轮唯一引入**新故障模式(失速)**的改动 —— 单独提交、单独回归、单独真机验,
别和 T1/T2 的大规模机械迁移混在一个提交里(混了以后真机出问题分不清是词汇迁移还是队列语义)。

## 3. 全局红线(每项任务都受约束)

1. 分层依赖(tests/architecture/test_imports.py 锁死):flow 只 game+tactical_map;game 零 sc2;driver 零业务规则。
2. OP_CATALOG 原子目录只增不改;本轮不动。
3. 编译期拒绝优先于运行期降级:flow 配置错误**必须**编译失败(R6),生产队列 op 才允许 drain 期降级(R7)。
4. 不静默:任何被忽略的配置都要么删掉,要么报错,要么实现。
5. 真机数值不信 wiki;本次不新增魔法数(现有真机常数如 90 帧/retries=6 维持原值,不趁机改)。
   例外(已用):`STALL_WARN_SECS=30`、`DEFAULT_MAX_STEP_TRANSITIONS=200` —— 纯诊断/兜底阈值,
   只影响"是否报告"与"无限循环兜底",不参与任何正常执行决策。
6. 每个任务结束 pytest 全绿;样例 yaml 的离线确定性测试(tests/flow/test_tank_marine_push.py)始终过。

---

## T1:词汇统一(flow 全 stable id + catalog 必传)

**目标**:flow 的 authoring 面只剩一套单位词汇 = catalog stable id("terran/marine");
引擎侧经 catalog 把 gs 的 burnysc2 实体名翻译回 stable id 匹配。

**改动**:
1. `modules/flow/engine.py`:`catalog=None` → 构造期 `None` 直接 `ValueError`(报错信息说明
   flow 需 catalog 翻译 stable id)。
2. `modules/flow/allocator.py`:
   - composition 键 = stable id(不再容忍 burnysc2 名)。
   - 匹配改为:对每个 stable_id 取 `catalog.by_stable_id(id).burnysc2_name`,
     候选单位 `normalize_burnysc2_name(u.type_name) == burnysc2_name`(变体归一保留,T3 语义不变)。
   - `count/expand` 的 type 参数 = stable id,直接查 `leased_by_type` 键(删除"输入归一"路径)。
3. `modules/flow/predicates.py`:`_normalize_type` 只剩"单位侧变体归一"(删掉把 stable id
   当输入再归一的兼容路径);`_p_unit_count`/`_p_has_building` 的 type 参数按 stable id 处理
   (同样走 `by_stable_id → burnysc2_name` 匹配)。
4. 全部 flow 测试迁移:composition/group_action.type/谓词 type 参数改 stable id;
   每个 `FlowEngine(...)` 构造补 `catalog=load_terran()`;`test_variants.py` 的"无 catalog 回归基线"
   测试删除(语义被 D1 取代),保留变体归一测试(改为断言必传 catalog 的报错)。

**测试**:
- allocator:stable id 匹配(含 SIEGETANKSIEGED 变体归一)、无 catalog 构造报错。
- 既有测试全部迁移后绿(机械改,行为不变)。

**验收**:pytest 全绿;grep flow/ 下无裸 "MARINE"/"SIEGETANK" 等 burnysc2 名出现在
authoring 字符串里(除 test 构造的 Unit.type_name 与归一化常量)。

---

## T2:语法层加固 + 糖(签名表/命名参数/defs/校验)

**目标**:when/do 语法从"位置参数 dict"升级为"命名参数 + 别名",编译期抓所有
arity/类型/引用错误;运行时不再 TypeError/KeyError(R7)。

### T2a 谓词签名表 + 命名参数

`modules/flow/predicates.py` 新增签名表(单一权威源,validate 与 eval 共用):

```python
PREDICATE_SIGNATURES: dict[str, tuple[tuple[str, bool], ...]] = {
    "group_count": (("group", True), ("type", False)),
    "arrived": (("group", True), ("target", True), ("radius", True)),
    "game_time": (), "strategy_elapsed": (), "step_elapsed": (),
    "distance_between": (("a", True), ("b", True)),
    "enemy_count_near": (("anchor", True), ("radius", True)),
    "group_hp_ratio": (("group", True),),
    "group_center": (("group", True),),
    "region_center": (("name", True),),
    "unit_count": (("type", True),),
    "group_center_in_region": (("group", True), ("region", True)),
    "enemy_visible_in": (("region", True),),
    "has_building": (("type", True), ("region", False), ("ready", False)),
    "point_toward": (("from", True), ("toward", True), ("dist", True)),
}
```

- 谓词/空间工具节点用**命名参数**:`{op: arrived, group: inf, target: {param: target}, radius: 8.0}`;
  不再接受 `args`(compile error 提示迁移写法)。
- 比较(`>=` 等,恰好 2 参数)与逻辑(`and`/`or` 2+ 参数、`not` 恰好 1)保留 `args`。
- `eval_when`/`_eval_value` 按签名表取参;and/or 短路、not 取一。
- 比较运算 `None` 安全:任一操作数为 None → False(空组/未解析点不再崩)。

### T2b defs 别名节

- StrategyManifest 新增 `definitions: dict`(可选节)。
- 语法:`definitions: {front_point: {op: point_toward, from: ..., toward: ..., dist: ...}}`;
  when 与 do-params 的值树中可写 `{ref: front_point}`。
- validate:ref 必须已声明、defs 内节点词表同 when AST、**禁自引用/环**(递归栈检测)、
  defs 未使用允许(不强制)。
- eval:when/参数求值遇 `{ref}` 展开求值(纯替换语义,无运行时变量捕获)。

### T2c 编译期校验补全(manifest.validate_strategy/validate_assembly)

新增校验(全部带 step/branch 定位报错):
1. 谓词 arity+参数名(按签名表;未知参数名报错;多余参数报错)。
2. `group_action` 缺 `type` 报错;type 形如 `race/name` 两段(词汇统一)。
3. do 的 params 值树内 `{param}/{var}/{ref}` 引用存在性(此前只查 when)。
4. YAML bool 陷阱:op 值非 str(bool 进来)→ 报错"op 名 'on' 被 YAML 解析成布尔,请加引号"。
   **H3 补漏**:同一检查必须覆盖所有标识符字段 —— `step_id / kind / reason / group_slot / type /
   action_atom / group_id / instance_id / strategy_ref / edges.from,to / param 名 / var 名 / region 名`。
   实测 PyYAML:`on/no/off/yes → bool`、`~ → None`、`12:30 → 750`(六十进制)、`1_000 → 1000`、
   Tab 缩进 → ScannerError;且 `{op: >=}` 不加引号 **ScannerError(硬失败)** 而 `{op: <}` 不加引号
   **正常解析(软通过)** —— 这种不一致会把 LLM 训成惯性犯错者。最阴的是 `reason: NO → False` 而
   edges 那侧写 `"NO"` 字符串,两边不等 → 报"无匹配 edge",把人指向完全错误的方向。
   零成本收益:机器路径改发 **JSON**(实测 `yaml.safe_load` 直接吃紧凑 JSON),隐式类型陷阱一次消失,
   还能上 JSON Schema 约束解码;YAML 只留给人读。
5. 重复 step_id(parse 时 dict 覆盖静默丢)→ 报错。
6. `validate_assembly`:>1 strategy_instances → 报错(V1 单实例;见决策 D1 语境)。
7. composition:`min <= target <= max`、值非负整数、键为两段式 stable id。
8. timer:`start_timer/stop_timer` 从 DO_OPS 移入 `UNIMPLEMENTED_DO_OPS`(带原因,
   compile 拒绝,与 timer_elapsed 对称)。
9. ✅(H4 随手落地)`loop_limits` 键白名单 + **正整数**(bool 显式拒);未声明时由
   `engine.DEFAULT_MAX_STEP_TRANSITIONS` 兜底,不再"未声明 = 无上限"。
   params 项只允许 `type/default`
   (live_editable 从示例删除,多余键告警);param 声明 type 白名单(int/float/point/bool/str)。
10. 引用类型检查(尽力而为):`{param: x}` 用在谓词数值参数位时,若声明 type 可静态判定
    (int/float)则校验;point 参数位要求声明 point 或任意。

### T2c+ 评审补漏（H4 已落地，其余待做）

- ✅ **H4 图校验(ADR-0021 §4 + 验收 #3)**:不可达 step、环必须有出口(出环 edge **或** 环内 exit_strategy)、
  死边(没有任何 exit_step 会走的 edge —— 环出口判定的健全性依赖它)。已随本轮独立落地。
- **H6** D9 的 None 安全要逐运算符定义:比较 → False;`==`/`!=` 走正常 None 语义(`None == None` 为 True);
  注意 `not (a > b)` 会因 None 翻成 **True**,把空组误判成条件成立、触发推进/攻击这类不可逆动作。
  并且每个 `(step, branch, op)` 首次触发记一条 trace 诊断(一次性,不刷屏)——否则"不静默"红线守不住。
- **J2** 运算符 arity 进签名表:比较=2、`and/or`≥2、`not`=1。当前 `{op: and, args: [x]}` 能过、
  `{op: not, args: [a, b]}` 静默忽略第二个。
- **J3** `point_toward` 的参数名 `from` 是 Python 关键字 → 改 `origin`(现在零成本;等样例/提示词/测试铺开就不是了)。
- **A1** T2 收尾加 `modules/flow/vocab.py: dump_vocabulary() -> dict`(签名表 + OP_CATALOG +
  UNIMPLEMENTED_* → 机器可读词表)。三个零维护用途:提示词自动生成(永不与校验器漂移)、
  编译错误里的"期望参数名/可用谓词"、UI 编辑器方块目录。约 20 行,但它比语法选择更决定
  "LLM 能不能可靠写脚本"。

### T2d 既有测试迁移

- 全部 when/do 改命名参数 + stable id(机械);bio_push/tank 测试字符串同步。
- 新增测试:arity/命名参数错误 compile fail、defs 展开、环拒绝、bool 陷阱、
  比较 None 安全、短路(构造会崩的右支不崩)。

**验收**:pytest 全绿;docs/tank_marine_push.yaml 用新语法写一遍在 test_tank_marine_push.py 过
(此步用 T5 落,但 T2 内先用测试内联字符串验证新语法)。

---

## T3:死字段清理

**目标**:删除三处死配置 + 让 composition.min 生效。

1. **on_exit 删除**:manifest 字段、parse、样例 yaml、docstring;
   引擎 done 行为不变(单位 lease 保持,单实例语义下与 release 无差异 —— 决策 D5)。
2. **QueueItem.when 删除**:`game/production.py` 字段 + 注释;
   runtime 无消费方,删除即诚实;条件项等生产 authoring 统一轮以结构化 AST 条件回归。
3. **ActionRequest 删除**:`flow/manifest.py` 类 + `flow/__init__.py` 导出;
   `docs/P0-影响边界.md` 数据流描述改为"flow 引擎直接产 Operation(原 ActionRequest 展开步合入引擎)"。
4. **composition.min 实现(S3 滞回)**:⚠️ **H2 边界**:`if len(cur) < min` 在 `min` 省略或为 0 时
   永远 false → 该类型**永不补,连首次都不填**;样例 `min=target=max=20` 恰好把这个洞盖住。
   必须定死:`min` 省略时默认 = `target`;`min=0` 显式表示"空组时填一次"或"不自动填"(二选一),
   写进校验 + 测试。实现:allocator.refresh 改
   `if len(cur) < min: need = target - len(cur) else 0`;max 硬上限(`take = cands[:min(need, max-len)]`)。
   语义:数量在 [min, target) 不补(滞回,避免每死一个兵就抢一次 free 池),
   跌破 min 补回 target,永不超 max。
5. 样例 yaml 的 `live_editable` 删除(无消费;hot-edit 轮再加)。

**测试**:allocator 滞回三区间([min,target) 不补/跌破补/上限截断);
QueueItem 构造签名更新;ActionRequest 导入删除后架构测试仍绿。

**验收**:grep 确认 `on_exit`/`QueueItem(.*when|`QueueItem.when`/`ActionRequest` 零残留(文档 P0 除外);pytest 全绿。

---

## T4:生产语义对齐(队首 block + 不静默) —— ✅ 已完成（含 H1）

**目标**:runtime 行为与文档语义一致;无静默路径。

> **落地记录**：`_drain` Phase 2 改 break；每个 blocked 返回点带原因（`_block`/`_why`，直接用
> `ConstraintResult.reasons`）；`self.blocked[queue] = {item, reason, since, frames, warned}`（队首换人/通畅即清）；
> 同一队首阻塞超 `STALL_WARN_SECS = 30` 游戏秒记一条 `self.stalls`（只报一次，**不自动丢弃队首**——那会破坏
> build order）；未知 QueueOp 出队记 dropped；TRAIN 的 consumed 显式出队（原路径 count>1 会反复记 dropped）；
> run_*.py tick 日志加 `blocked=`/`stalls=`；`army` 队列按产线拆分。测试 +4，全量 323 绿。

1. `modules/production/runtime.py` `_drain` Phase 2:
   - BUILD/TRAIN `outcome == "blocked"` → `break`(队首门控,本帧不再处理该队列后续项),
     不再 `i += 1; continue`。
   - 资源预检不足同样 `break`(与门控一致)。
   - 未知 `QueueOp` → `self.dropped.append((head, f"未知 op {head.op!r}"))` + 出队(不静默)。
   - 作者错误项(drop-and-continue)行为不变:未知 type、缺 placement 仍丢弃继续 —— 这不破坏顺序语义。
2. 模块 docstring 第 3 条与代码对齐(block 语义原文已经对,代码改回)。
3. `docs/P0-影响边界.md` S11 段落无改动(本来就写队首门控)。
4. 已知风险登记(文档注释,不修):跨帧 GS 快照未更新仍可能超发,SC2 静默拒单由
   build_flights 确认+重试兜底;train 无确认(真机启发式规避,待 D6 ApplyResult)。

**测试**:tests/production/test_runtime.py 增:
队首缺矿时**后续项不执行**、下一帧矿够后队首先执行;
未知 op 进 dropped;既有 skip 语义测试改为 block 语义断言。

**验收**:pytest 全绿;runtime docstring 与代码一致。

---

## T5:样例与文档迁移（+H5：run_*.py 必须一起迁）

1. `docs/tank_marine_push.yaml`:definitions 节(front_point/ready)+ 命名参数 + stable id +
   composition min≤target≤max 合法;删除 on_exit/live_editable。
2. `tests/flow/test_bio_push.py` 内联策略改新语法(可作为"最小新语法示例")。
3. `docs/P0-影响边界.md`:ActionRequest 段落、stable id 词汇说明(flow 全 stable id)、
   composition min 滞回语义、block 语义确认。
4. `docs/需求文档-v0.1.md` S3 补滞回语义一句话;S8/S11 无改动。
5. **H5 `run_*.py` 真机脚本(原计划漏了,但 T6 要用它们冒烟)**:
   `run_flow_arrived.py:97`、`run_flow_slice.py:110` 是 `FlowEngine(..., port)` **无 catalog**,
   T1 后构造期直接 ValueError;`run_full_flow.py`/`run_siege_probe.py`/`run_tank_marine_push.py`
   各有约 10 处内联 authoring(兵种名/位置参数)。迁移量实测:**24 处 `FlowEngine(` 构造、
   tests/flow 内 124 处 burnysc2 兵种名、55 处 `"args"`**。
   （本轮已顺手修掉 a41abc9 遗留的 `_build_flight`→`_build_flights` 失效引用，否则 T6 冒烟必崩。）

**验收**:test_tank_marine_push / test_bio_push 用仓库 yaml 直接过;pytest 全绿。

---

## T6:全量回归 + 真机冒烟

1. `uv run python -m pytest tests -q` 全绿(含 architecture 分层测试)。
2. 真机冒烟(若 SC2 环境可用):`run_full_flow.py`(生产链 + block 语义)、
   `run_tank_marine_push.py`(新语法 yaml 跑真机),证据入 docs/*.log;
   观察点:dropped 不增、队首等待后恢复、循环蛙跳行为与旧版一致。
3. 若 block 语义真机吞吐明显受损:记录证据,后续轮加 per-queue `skip_blocked` 开关(本轮不修)。

---

## 7. 不做清单(防范围蔓延)

- 表达式字符串 DSL / 转译层;step 内 next 语法(替代 edges);
  （**理由留档**:表层语法值得做,但必须等词表(A1)与 IR 稳定后再做 —— 那时它只是签名表的机械投影,
  约 200 行;现在做会把死字段/位置参数/静默 timer 烧进语法与提示词。量化依据:同一份
  `tank_marine_push` 的 AST-YAML 5050 字符/91 行/296 个括号/最长单行 291 字符 vs 表层语法
  1669 字符/40 行/最长 96 —— 3.0×,且 `point_toward(...)` 现在被抄了 4 遍。）
- timer_elapsed 实现(T8);engaged/under_attack/nearest_units(T9);
- 生产 authoring 统一(planner module_defs 代码注册 → YAML);
- OP_CATALOG 与 driver 任何改动;跨帧超支根治(依赖 D6 ApplyResult);
- 现有真机魔法数(90 帧/retries=6/NODE_RADIUS)常量化重构 —— 记入 backlog,不在本轮。

---

## 8. 落地记录（本轮实际完成）

| 提交 | 内容 | 测试 |
|---|---|---|
| `12a1c79` | T4 + H1：队首门控 block + 阻塞可观测（blocked/stalls）+ 未知 op 记 dropped；run 脚本日志加 blocked/stalls；army 队列按产线拆分 | 323 |
| `2b131c1` | H4：图级校验（不可达 step / 无出口环 / 死边）+ 全局转移上限兜底 + exit_record | 319 |
| `5ad2be6` | T1：词汇统一（authoring 全 stable id、catalog 必传、单侧归一）+ 测试/样例/run 脚本迁移 | 326 |
| `8f8e8bc` | T2 + A1：签名表 + 命名参数 + arity + definitions/ref + None 安全与诊断 + 校验补全 + flow/vocab 词表导出 | 352 |
| `90bd846` | T3 + H2：死字段清理（on_exit/ActionRequest/QueueItem.when）+ 顶层键墓碑 + S3 补兵滞回 | 359 |
| 本次 | T5 + T6（离线）：样例 definitions 重构、P0/需求文档同步、run_*.py 离线守卫测试 | 363 |

基线 309 → 363（+54 测试）。全程每个任务结束 `uv run python -m pytest tests -q` 全绿。

### 样例前后对比（`docs/tank_marine_push.yaml`，去注释）

| 指标 | T2 前（位置参数） | 现在（命名参数 + definitions） |
|---|---|---|
| 字符数 | 5050 | 4871 |
| 括号 `{}[]` | 296 | 220 |
| 最长单行 | 291 | 217 |
| `point_toward(...)` 抄写 | 4 遍 | 1 处定义（`front_point`）|
| 集结条件抄写 | 2 遍 | 1 处定义（`formed`）|

命名参数本身会让单个节点变长（`arrived` 48→63 字符），但配 `{ref}` 后深嵌套节点大幅缩短
（`enemy_count_near` 那条 155→82），整体反而更短，且"参数顺序写错"从静默 bug 变成编译错误。

### T6 剩余部分（真机，需要人执行）

离线部分已完成：`pytest tests` 全绿（含架构分层测试）；`run_*.py` 新增离线守卫测试
（内联策略必须能编译 + 构造引擎、脚本引用的引擎/运行时私有属性必须存在 —— 后者正是 a41abc9 之后
`run_full_flow.py` 一直坏掉的那类漂移）。

真机冒烟命令与观察点：

```bash
uv run python run_full_flow.py          # 生产链 + 队首 block 语义
uv run python run_tank_marine_push.py   # 新语法 yaml（definitions）+ 蛙跳循环 + 拆分后的军队队列
```

观察点（证据入 `docs/*.log`）：
1. `dropped=` 不增长（未知 op / 作者错误项应为 0）。
2. `blocked=` 出现后能恢复（队首攒够矿即继续），`stalls=` 为空或只在真卡死时出现一条。
3. 蛙跳行为与旧版一致（组心距敌矿递减、siege 只在坦克入 tank_cover 那轮发）。
4. `army_rax`/`army_fac` 两条产线并行产出（拆队列后坦克不再等 20 机枪训完）。
5. 若 block 语义确实压低吞吐：记录证据，后续轮再加 per-queue `skip_blocked` 开关（本轮不修）。

### 遗留 / 交接

- `on_exit` 的恢复条件写在代码里（`flow.manifest.REMOVED_KEYS`）：多实例 / hot-swap 轮按 spec-002 放回。
- timer（`start_timer`/`stop_timer`/`timer_elapsed`）三处对称拒绝，T8 一起放回。
- 表达式字符串 DSL 仍不做：前置条件（词表 `flow.vocab` + 稳定 IR）现在才刚具备，届时它只是签名表的机械投影。
- `docs/plan-backend-view.md` 的 B1（显式读模型 `snapshot()`）本想作为 T2/T3/T4 的补充条款顺手落；
  实际落地的可观测面已覆盖它要的大半：`FlowEngine.exit_record` / `FlowEngine.eval_diagnostics` /
  `ProductionRuntime.blocked` / `ProductionRuntime.stalls`，B1 只需把它们包装成 snapshot 读模型。
