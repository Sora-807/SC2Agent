# 步坦协同升级计划（可直接指导执行的实施顺序）

> 目标：把「20 步兵 + 4 坦克蛙跳协同推进」从离线示范升级为**真机可跑**的完整能力。
> 基线：271 测试全绿；macro_push 真机全链已打通（docs/full_flow.log）；步坦协同离线示范已可编译（docs/tank_marine_push.yaml）。
> 每项任务完成后必须：`uv run python -m pytest tests -q` 全绿，再进入下一项。

## 0. 验收图景（最终验收标准）

真机一局：单矿满 SCV → 补给/兵营/反应堆 → 工厂 + 科技挂件 → 20 步兵 + 4 坦克 →
flow 走循环蛙跳（坦克推进到枪兵身边架起 → 枪兵推进到坦克架起射程 80% 覆盖内 → 清理 →
威胁小再推进）→ 攻到敌方主矿 → `engine_done=True`、`dropped=0`。

日志证据（tick 行）：组心距敌方主矿递减、架起订单（SIEGEMODE）出现、停等 step 出现、
坦克实体先为 SIEGETANK 后变 SIEGETANKSIEGED。

## 1. 任务总览与依赖顺序

| 顺序 | 任务 | 依赖 | 产出 |
|---|---|---|---|
| T1 | siege/unsiege 动作落地（driver） | 无 | OP_CATALOG + 翻译 + 契约/翻译测试 |
| T2 | catalog 数据扩展（坦克/工厂/挂件 + 射程 + 形态变体字段） | 无 | terran.json + CatalogEntry 字段 + 校验 |
| T3 | 形态变体归一化（SIEGETANKSIEGED 计数） | T2 | flow 按 variants 匹配类型 |
| T4 | point_toward 空间函数 + 动态参数去重量化 | 无 | 谓词目录 + engine 求值 + 去重测试 |
| T5 | 循环版步坦协同 flow 重构 + 离线确定性测试 | T1+T2+T3+T4 | 新 yaml + 测试 |
| T6 | 真机生产链前置（工厂放置位 + 坦克生产） | T2 | 位置扫描 + 生产检查脚本 |
| T7 | 真机全链验证（步坦协同 complete） | T5+T6 | run_tank_marine_push.py + 日志证据 |
| T8（可选） | timer_elapsed 做实（start/stop_timer + 谓词） | 无 | 计时器运行时 + 测试 |
| T9（可选） | 威胁谓词细化（engaged/under_attack/nearest_units） | 无 | 谓词目录扩展 + 测试 |

执行顺序：**T1 → T2 → T3 → T4 → T5 → T6 → T7**，T8/T9 按需穿插（不阻塞主线）。

## 2. 全局红线（每项任务都受约束）

1. 分层依赖（tests/architecture/test_imports.py 锁死）：game 零 sc2；driver 只 game+sc2；
   flow 只 game+tactical_map（game.catalog 属于 game 包，flow 可引用）；world 不碰 driver/flow。
2. 数据驱动：数值/词表进 `modules/game/data/terran.json`；结构性闭集进枚举；
   加载边界校验在 catalog.register 内 fail-fast。
3. 新动作必须三处同步（测试 `test_translators_and_unimplemented_partition_catalog` 锁死）：
   OP_CATALOG + driver TRANSLATORS（或 UNIMPLEMENTED_ACTIONS 带原因）。
4. 真机数值不信 wiki：build_time/射程以 burnysc2 game_data 或真机实测为准
   （历史教训：反应堆 build_time 25→36 真机锁定）。
5. 真机踩坑清单（每一项任务的实现都要对照）：
   - 同帧同单位命令被 burnysc2 去重丢单 → `_frame_busy` 跨队列去重（已实现，新路径沿用）；
   - 训练队列满静默拒单 → 产出建筑按空槽选（已实现）；
   - 在途建造按位置确认（已实现）；挂件只选完全空闲母建筑、重发才计次（已实现）；
   - 挂件命令用通用能力 + 父专属实体类型（已锁定）。

---

## T1：siege/unsiege 动作落地（driver 层）

**目标**：flow 能对坦克组下发架起/收起；编译期词表 + 翻译 + 契约三层测试齐。

**已核实事实**：burnysc2 无裸 SIEGEMODE/UNSIEGE 枚举，可用名 =
`AbilityId.SIEGEMODE_SIEGEMODE`(388) / `AbilityId.UNSIEGE_UNSIEGE`(390)；
`UnitTypeId.SIEGETANK`(33) 架起后实体变 `SIEGETANKSIEGED`(32)（T3 处理计数）。

**改动**：
1. `modules/game/operation.py` OP_CATALOG 加：
   `"siege": []`、`"unsiege": []`（无参数，group 级批量）。
2. `modules/driver/sc2_adapter.py` 加 `_t_siege`/`_t_unsiege`：
   `return [u(AbilityId.SIEGEMODE_SIEGEMODE) for u in _units(op, find_unit)]`（unsiege 同理）；
   注册进 TRANSLATORS。
3. 注释注明：架起后实体 type_id 变 SIEGETANKSIEGED（真机观测预期，给 T3 证据）。

**测试**：
- `tests/driver/test_translate.py`：siege/unsiege 各一条（FakeUnit `__call__` 断言能力枚举）；
  清单对齐测试自动覆盖新动作。
- `tests/game/test_ops.py`：OP_CATALOG 含 siege/unsiege 且无必需参数。

**真机探针（可选但推荐）**：仿 `run_bare_addon.py` 写 `run_siege_probe.py`：
造 1 坦克 → 发 siege → 每 10 帧记 orders + type_id（预期订单出现、type_id 变 SIEGETANKSIEGED）；
再发 unsiege 回变。证据存 `docs/siege_probe.log`，直接喂给 T3 的实现与注释。

---

## T2：catalog 数据扩展（坦克生产链 + 射程 + 形态变体）

**目标**：catalog 能描述坦克（含架起形态/射程）与工厂生产链；数据加载校验 fail-fast。

**改动**：
1. `modules/game/catalog.py` CatalogEntry 加三个字段（默认值放最后）：
   - `attack_range: float | None = None`   # 常规射程（坦克未架起 7）
   - `siege_range: float | None = None`    # 架起射程（坦克 13；架起门用 0.8×13=10.4）
   - `variants: tuple[str, ...] = ()`      # 形态变体实体名（SIEGETANK → ("SIEGETANKSIEGED",)）
2. `modules/game/data/terran.json` 加三条（数值先按 SC2 标准填，T6 真机实测校正）：
   - `terran/factory`：FACTORY，building，capabilities ["train"]，size 3，cost 150/100，
     build_time 43，prerequisites ["terran/barracks"]；
   - `terran/factorytechlab`：FACTORYTECHLAB，building，capabilities ["addon"]，size 2，
     build_ability "BUILD_TECHLAB"、build_order_name "Techlab"、produced_by "terran/factory"、
     prerequisites ["terran/factory"]；
   - `terran/siegetank`：SIEGETANK，combat，capabilities ["attack","move"]，
     cost 150/125 supply 2，build_time 32，produced_by "terran/factory"，
     prerequisites ["terran/factorytechlab"]，attack_range 7、siege_range 13，
     variants ["SIEGETANKSIEGED"]。
3. 校验：register() 对 attack_range/siege_range 非负数；variants 列表化。

**测试**：`tests/game/test_catalog.py`：新条目字段断言（含 variants/双射程）；
非法射程（负数）加载报错。

---

## T3：形态变体归一化（架起坦克仍计入组）

**目标**：坦克架起后（type_name=SIEGETANKSIEGED）flow 的 group_count/group_action/Allocator 仍把它算作 SIEGETANK 组成员。

**背景**：真机架起后实体类型名变化（T1 探针证据）；不归一化的话 formup 的
`group_count(armor, SIEGETANK)` 在第一次架起后归零，策略瞬间失联。

**改动**：
1. `modules/flow/engine.py` FlowEngine.__init__ 加可选参数 `catalog=None`（默认不归一化，兼容现有测试）。
2. `modules/flow/predicates.py` 与 Allocator 的类型匹配点统一走 helper：
   `_normalize_type(ctx, type_name)`：无 catalog 原样返回；有 catalog 时查
   `by_burnysc2_name(type_name)` 的 variants 反向表，把变体名归一到主名。
   覆盖点：group_count（含带 type 参数）、group_action 的 expand、group_hp_ratio/arrived 等间接计数。
3. run 侧（T7 脚本）构造 FlowEngine 时传 `catalog=load_terran()`。

**测试**：`tests/flow/test_engine.py`（或新建 test_variants.py）：
组里 4 单位 type_name 为 SIEGETANKSIEGED 时，`group_count(armor, SIEGETANK) == 4`；
不传 catalog 时行为不变（回归）。

---

## T4：point_toward 空间函数 + 动态参数去重量化

**目标**：flow 能表达「从坦克组心朝目标方向延伸 d 距离的推进点」；动态点参数不引发命令风暴。

**语义**：`point_toward(from, toward, dist)`：from/toward 为点值（字面坐标、点位名、
`{op: group_center, args: [slot]}` 嵌套节点、param），返回 from + normalize(toward-from)×dist；
from==toward 或 dist<=0 → 返回 from。

**改动**：
1. `modules/flow/predicates.py`：新增 `KNOWN_SPATIAL_OPS = frozenset({"point_toward"})`；
   eval_when 加分支；manifest `_validate_when_node` 认它（把 UNIMPLEMENTED_SPATIAL_OPS 里的判断改为先查已实现集合）。
2. `modules/flow/engine.py` `_eval_value` 加分支：递归求值 args 后调用 point_toward。
3. **去重量化**（真机教训：同单位连续命令打断移动）：`_emit_group_action` 在
   `resolve_action_params` 之后、`json.dumps` 之前，对 params 中 POINT/POINTS 参数做
   `round(x, 0)`（量化到整格）再进去重键——动态点组心微移 <0.5 格不重发，真移动才重发。

**测试**：
- `tests/flow/test_predicates.py`：方向/比例/边界（重合、dist=0、负 dist）。
- `tests/flow/test_engine.py`：动作参数含 point_toward 时求值正确；组心移动 0.4 不重发、
  移动 ≥1 格重发（去重量化）。

---

## T5：循环版步坦协同 flow 重构 + 离线确定性测试

**目标**：用 T1/T3/T4 把 docs/tank_marine_push.yaml 从「3 跳手写 + hold 占位」升级为
**单循环跳对**（自动蛙跳到目标），离线测试全节奏通过。

**策略结构（循环 4 步）**：

```
formup ─(20 步兵+4 坦克)─▶ tank_hop ─▶ siege_gate ─▶ inf_hop ─▶ threat_gate ─┐
                            坦克 move       距目标≤0.8×13   步兵推到覆盖前沿    └─(威胁小 CLEARED)─┘
                         到步兵旁(组心)     才架起(siege)
        └────── 任意时刻 arrived(inf, target, 8) → exit_strategy ARRIVED（done）──────┘
```

关键表达式：
- tank_hop：`move_to {op: group_center, args: [inf]}`（坦克推进到枪兵身边）；
  `arrived(armor, {op: group_center, args: [inf]}, 3)` → IN_PLACE。
- siege_gate：`arrived(armor, {param: target}, {param: tank_cover})`（距目标 ≤ 10.4 才架起）
  → `group_action siege`（T1）→ SIEGED。
- inf_hop：步兵 `attack_move_to {op: point_toward, args: [{op: group_center, args: [armor]}, {param: target}, {param: tank_cover}]}`
  （=坦克覆盖内、朝敌方向的前沿点，满足「步兵不超出坦克射程」）；
  脱队门 `distance_between(inf, armor) > inf_hold_dist` → STRAGGLED → inf_wait（hold + step_elapsed ≥ hold_secs）→ RESUME；
  `arrived(inf, 前沿点, 3)` → IN_PLACE。
- threat_gate：`enemy_count_near(前沿点, threat_radius) < threat_limit 且 group_hp_ratio(inf) > 0.6`
  → CLEARED → 回 tank_hop（循环）；否则留在本步继续 `attack_move_to 前沿点` 清理。
- loop_limits.max_step_transitions 提到 80（循环次数上限，有界兜底）。

**测试**：重写 `tests/flow/test_tank_marine_push.py`：脚本化帧序列走 **2 轮完整循环**，
断言：组心逐步逼近 target（每轮 ≈ tank_cover 前进量）、架起在距目标 ≤10.4 时才发、
脱队停等出现、威胁大时卡在 threat_gate、威胁解除继续、最终 arrived → done；
op 断言含 siege 动作 + 动态点去重（同轮内 attack_move 不因组心微移重发）。

---

## T6：真机生产链前置（工厂放置位 + 坦克生产）

**目标**：真机能稳定产出 1 工厂 + 1 科技挂件 + 4 坦克。

**步骤**：
1. 放置位作者：`modules/tactical_map/data/ladder_map/base_layout.yaml` 加 factory slot
   （3×3，kind production）+ factory_addon 预留（2×2，kind addon，右下）；bl/tr 两变体。
   坐标经真机 can_place 扫描锁定（复用 `run_slot_scan.py` 模式，脚本改造成扫描 factory 位）。
2. 生产队列脚本 `run_tank_production_check.py`（仿 run_production_check.py）：
   队列 = 补给站×2 → SCV×12 → 补给站×6 → 精炼厂×2 → 派气工 → 兵营 → 工厂 → 科技挂件
   （addon 项，母建筑=工厂）→ 坦克×4。
3. 真机跑通证据：工厂落位 slot、FACTORYTECHLAB 实体（右下 2×2 报告位）、4 坦克训练完成；
   **顺带实测校正 T2 数值**：工厂/挂件/坦克的 build_time 与坦克双射程（架起门 10.4 的射程 13 校验）。

---

## T7：真机全链验证（步坦协同 complete）

**目标**：验收图景（§0）达成。

**步骤**：新脚本 `run_tank_marine_push.py`（run_full_flow.py 模式）：
- 生产半边：T6 队列 + 枪兵维持（补给/枪兵/坦克维持：坦克 <4 补训、步兵 <20 补训，
  维持器照抄现有门控：macro 排空 + 挂件就绪 + formup 内）。
- flow 半边：T5 循环策略 + `catalog=load_terran()`（T3 归一化）+ 区域层含 T6 新点位。
- 证据日志（tick 行扩展）：组心距敌方主矿、架起订单（坦克 orders 集合）、
  停等 step 出现、SIEGETANKSIEGED 实体计数。
- 验收：`engine_done=True`、`dropped=0`、日志证据齐全；`docs/full_flow_tank.log` 存档。

---

## T8（可选）：timer_elapsed 做实

**现状**：engine 的 start_timer/stop_timer 是 stub（空转）；UNIMPLEMENTED_PREDICATE_OPS 含 timer_elapsed。
**改动**：engine 加 `self._timers: dict[str, float]`；start_timer/stop_timer 写入/清除（key = 名，
名在 variables 声明——manifest 校验引用存在性）；predicates 加 `timer_elapsed(name)`；
停等语义可从「step_elapsed 近似」换成真计时器。测试：test_engine 计时器读写 + 谓词。

## T9（可选）：威胁谓词细化

- `nearest_units`（选择器词表 spec-003 §4.3）→ 前沿单位判定（替代组心近似）；
- `engaged`：orders 含攻击类能力或近期掉血 → 交火判定；
- `under_attack`：hp 历史差分（engine 持上一帧 hp 快照）。

---

## 3. 回归基线

- 每任务结束：`uv run python -m pytest tests -q` 全绿（当前 271）；
- 真机任务结束：log 存档 docs/ + test-plan.md 相应条目更新（✅ + 真机锁定事实）；
- git 提交粒度：一任务一提交，commit message 带「T<编号>:」前缀。

