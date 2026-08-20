# 规划器（Planner）设计计划 —— 生产投影（对齐需求文档 v0.1）

> 来源：对齐 `docs/需求文档-v0.1.md`（S5 生产投影 + 模块表 + R4 + P5）与 `docs/P0-影响边界.md`（production_sequence 草稿 + 生产 action 原子）。
> 目标：建 `planner` 模块——`planner(GameState, production_sequence) → 投影曲线`，逐帧/逐秒顺序模拟 production_sequence 的消费（可行性门控，constraint+mechanics），输出未来资源/单位曲线。
> 性质：**无状态、不依赖 flow 运行期、不模拟战斗（R4）、planner 是唯一预测源**；用于 UI/agent/alerts 看未来（不挡 live 执行行）。
> **不是活控制器**：不发 QueueItem、不接 runtime、不接管 live 生产执行（那是 runtime S11 的活）。
> 基线：292 测试全绿；catalog（cost/build_time/supply/prereq/produced_by）+ constraint.checks 已建；mechanics/ 与 planner/ 为空占位。

## 0. 验收图景（最终验收）

离线：`run_sim.py` 吃一个 production_sequence（如 `docs/buildorder_tank.yaml`）+ 一个起始 GameState 快照 →
输出 投影曲线（逐秒：矿/气/supply/单位/建筑）+ 事件（"工厂 t=43 完成"、"坦克×4 t=…完成"、"t=X 卡：缺气"）。
- 能回答："这个 production_sequence 几秒凑齐 20 步兵+4 坦克？中间堆矿/断气/卡补给吗？"
- 能比较两个 production_sequence（A 比 B 快 N 秒）。
- 仿真矿/气曲线与真机 log（`docs/full_flow_tank.log` 的 tick 矿/气）吻合（收入速率校准）。
- 选出的 production_sequence 塞进 run 脚本当 macro 队列 → 真机 20+4 / 气满采 / 不堆矿 / dropped=0。

## 1. 契约（对齐规格）

- **签名**：`planner(game_state: GameState, production_sequence: list[ProductionModuleInstance]) → ProjectionCurve`
- **ProductionModuleInstance**（P0 L127）：`{instance_id, module_ref, version, params}` —— production_sequence 是**可复用参数化模块的实例列表**，不是裸 op。
- **生产 action 原子**（P0 L93-102）：`build(type,position)` / `train(type)` / `research(ability)` / `cancel(order)` / `assign_workers(task,count)` / `morph(type)`（morph 三族预留）。
- **模拟**：逐帧/逐秒顺序模拟 production_sequence 的消费；可行性门控用 constraint+mechanics。
- **性质**：无状态、不依赖 flow 运行期、不模拟战斗（R4）、唯一预测源。

## 2. 边界（对齐 S5/S8/S11）

| 是 | 不是 |
|---|---|
| 离线投影/预测（GameState 快照 + production_sequence → 曲线） | 活控制器（不发 QueueItem、不接 runtime、不接管生产） |
| 给 UI/agent/alerts 看未来（S5） | 替换 runtime 的 live 执行（S11：drain 队列 + constraint 门控，不需 simulate） |
| 唯一预测源（规格锁定） | 读 live GameState 之外的 live 通道 / 调 driver |

> live 执行（runtime S11）与 planner（S5 投影）是两条独立路径：runtime 边消费边按 constraint 门控执行；planner 拿同一 production_sequence + 当前 GameState **投影**出未来曲线，不参与执行。

## 3. 依赖与前置

- **依赖**：constraint（可行性：矿/气/供给/前置/放置——已建 checks.py，直读 catalog）+ mechanics（造价/时间/前置——空，**V1 可由 catalog 暂代**）+ game（catalog/GameState 类型——已建）。
- **mechanics 前置**：规格把 mechanics 列为 planner 依赖，但 V1 Terran 的造价/时间/前置已在 catalog（CatalogEntry.cost/build_time/prereq），constraint.checks 也直读 catalog（mechanics 空）。故 V1 planner 可**直读 catalog 暂代 mechanics**，mechanics 留空（与 constraint 现状一致）；三族/LayerComputer(power/addon) 时再建 mechanics。
- **不阻塞**：planner V1 只需 constraint + game（catalog），无需先建 mechanics。

## 4. 接口设计

```python
# modules/planner/curve.py
@dataclass
class ProjectionPoint:              # 逐秒快照
    t: float; minerals: float; gas: float
    supply_used: int; supply_cap: int
    mineral_workers: int; gas_workers: int
    buildings: dict[str, int]; units: dict[str, int]; in_flight: list[InFlight]

@dataclass
class ProjectionEvent:              # 完成/启动/卡
    kind: str                       # "completed" / "started" / "stalled"
    type: str | None; t: float; reason: str | None

@dataclass
class ProjectionCurve:
    points: list[ProjectionPoint]; events: list[ProjectionEvent]
    def time_to(self, predicate) -> float | None   # 凑齐 X 的时间
    def peak_minerals(self) -> float               # 峰值余矿（堆矿诊断）
    def stalls(self) -> list[ProjectionEvent]      # 卡点列表

# modules/planner/planner.py
class Planner:
    def __init__(self, catalog, constraint): ...   # V1 直读 catalog，不依赖 mechanics
    def project(self, gs: GameState, seq: list[ProductionModuleInstance],
               until: float) -> ProjectionCurve: ...
```

## 5. 仿真循环（每秒一帧）

```
project(gs, seq, until):
  st = derive_from(gs)          # 从 GameState 快照派生仿真态（矿/气/supply/单位/建筑计数 + 在途）
  ops = expand(seq)             # production_sequence → op 序列（module_ref 展开，参数化）
  queue = list(ops); traj = ProjectionCurve()
  while st.t < until:
    # 1. 收入（rate 校准自真机 log / burnysc2 game_data）
    st.minerals += st.mineral_workers * mineral_rate
    st.gas += st.gas_workers * gas_rate
    # 2. 推进在途（progress += 1；到 build_time → 落成：buildings/units+1、supply 变、产槽释放）
    for f in st.in_flight: f.progress += 1
    apply_completed(st, traj)   # 落成 + 完成事件
    # 3. 展开+消费 production_sequence（可行性门控 = constraint.checks）
    while queue and feasible(queue[0], st, constraint):
        op = queue.pop(0); start(op, st); traj.events += 启动事件
    if queue and not feasible(queue[0], st): traj.events += 卡事件(队首, 原因)
    # 4. 记点
    traj.points.append(snapshot(st)); st.t += 1
  return traj
```

- **feasible(op)**：build→constraint.check_build（矿/气/供给/前置/放置）+ 有 idle SCV 当 builder；train→check_train + 产建筑空训练槽；assign→有 idle SCV。
- **产槽模型**：兵营 1 / 工厂+techlab 1 / 反应堆 2；在训占槽、完成释放（仿真器自管）。
- **收入速率**：mineral/gas per SCV/sec 从真机 log 校准（红 #4）。

## 6. 待定（实现时定，规格 v0.1 留白）

- **production module 定义**：module_ref 怎么展开成 op 序列（模块 authoring 格式：yaml 模板？代码注册？）—— P5 实现 P2 定。
- **mechanics V1 是否薄封装 catalog**（还是 planner 直读 catalog）——倾向后者（不阻塞）。
- **ProjectionCurve 字段**够否（按验收需求补）。
- **收入速率校准**（mineral/gas per SCV/sec，非线性饱和递减是否需要）。

## 7. 模块与架构

```
modules/planner/
  __init__.py        # export Planner, ProductionModuleInstance, ProjectionCurve/Point/Event
  curve.py           # ProjectionCurve/Point/Event + 查询（time_to/peak_minerals/stalls）
  planner.py         # Planner.project（纯函数仿真：derive/收入/推进/展开+消费/门控/记点）
  module_defs.py     # production module 定义 + expand()（module_ref + params → op 序列）
run_sim.py           # CLI：吃 production_sequence yaml + GameState 快照 → 打印曲线 + 摘要
```

架构（继承 test_imports.py）：
- planner → constraint / mechanics / game（V1 实际只用 constraint+game；mechanics 空）。
- **不碰** driver/flow/production/sc2/tactical_map/world。
- 纯函数、无 IO、确定性（同输入同输出）——可单测、可复现。

## 8. 任务总览与依赖顺序

| 顺序 | 任务 | 依赖 | 产出 |
|---|---|---|---|
| P1 | 仿真态派生（GameState 快照 → SimState）+ ProjectionCurve/Point/Event 数据模型 | 无 | curve.py + 派生 + 单测 |
| P2 | production module 定义 + expand()（module_ref+params → op 序列） | P1 | module_defs.py + 单测 |
| P3 | Planner.project 主循环（收入/推进/展开+消费/可行性门控/记点+事件） | P1+P2 | planner.py + 单测 |
| P4 | run_sim.py CLI + 摘要（凑齐时间/峰值余矿/卡点） | P3 | run_sim.py |
| P5 | 收入速率校准 vs 真机 log（矿/气曲线吻合） | P3+真机log | 校准参数 + 吻合验证 |
| P6 | 用仿真器设计 20+4 production_sequence → 塞 run 脚本 → 真机验 20+4/气满采/不堆矿 | P4+P5 | docs/buildorder_tank.yaml + 真机 log |

执行顺序：**P1 → P2 → P3 → P4 → P5 → P6**。每项完成 `uv run python -m pytest tests -q` 全绿再进下一项；真机项存档 docs/ + 更新 test-plan。

## 9. 红线（对齐规格 R4/R6 + 架构）

1. 分层（test_imports.py）：planner → constraint/mechanics/game；不碰 driver/flow/production/sc2/tactical_map/world。
2. 无状态、纯函数、确定性（同输入同输出）。
3. 不模拟战斗（R4）；不挡 live（live 执行行归 runtime S11）。
4. 数值（造价/时间/收入速率）以 catalog / 真机 log / burnysc2 game_data 为准（不信 wiki）。
5. production_sequence 是 module 实例列表（可复用模板，对齐 P0 L127），不是裸 op。

## 10. 测试

- `tests/planner/test_curve.py`：仿真态派生（GameState 快照 → SimState 字段正确）+ 落成 apply（建筑/单位+1、supply 变、产槽释放）。
- `tests/planner/test_module_defs.py`：expand()——module_ref+params → op 序列正确。
- `tests/planner/test_planner.py`：project 主循环——脚本化 seq + gs → 断言曲线（"工厂 t=43 完成"、"坦克×4 t=…完成"、堆矿/卡补给事件、20+4 凑齐时间）+ 可行性门控（缺资源/前置/槽位→卡）。
- P5：仿真矿/气曲线 vs 真机 log 吻合（pytest approx）。
- P6：真机——20+4 产出、气工=6、矿不爆、dropped=0。

## 11. 边界（不做什么）

- 不做活控制：不发 QueueItem、不接 runtime、不读 live GameState 之外的 live 通道、不调 driver。
- 不做微操/战斗（归 flow）。
- V1 不做三族 mechanics / LayerComputer(power/addon)（catalog 暂代）；不做 build-order 树搜索/HTN（先线性 production_sequence 仿真）；非线性矿脉饱和递减可选（P5 偏差大再加）。
- 不替换 runtime/steward 的 live 执行机制；只提供离线投影 + 选出的静态 production_sequence。
