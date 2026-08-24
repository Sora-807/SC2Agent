# ADR-0034：auto_supply 移除 —— 诊断取代掩盖

日期：2026-08-24 ｜ 状态：已采纳（PLAN-V2 批 1 落地，D7） ｜ 影响：
planner/planner.py（_supply_guard 删除）、routes/plans.py、agent/tools.py、probes

## 背景

供给守卫（auto_supply=true 时仿真自动插 depot）是投影器对现实的**美化**：
- 卡人口这种规划缺陷被静默修掉，用户与 agent 都看不见；
- 投影与真机行为分叉（真机不会自动插 depot），投影不可信 → 警报不可信。

H 批曾把它默认关（auto_supply 默认 false），但参数还在 —— 留着一个
「打开就撒谎」的开关本身就是负债。

## 决定

**彻底删除**（PLAN-V2 D7）：`_supply_guard` 方法、`auto_supply` 参数
（planner/routes/tools/probes 四处）、工具 schema。替代 = 诊断链：
- live 面 `supply_capped` 警报（已卡人口且队列/在途没排供给建筑才报，
  建议带 before_uid）；
- 干跑面 from_curve 同 kind（队列里没排供给建筑 → 插 depot 建议）；
- `simulate_plan(horizon=0)` 静态体检的人口对账（批 6 吸收 audit_queue）。

## 后果

「一切尽可能手动」的哲学落地：系统只报问题和建议，不替人做决定。
同族先例：supply_block 前瞻警报同批删除（D1 —— 前瞻与手动哲学冲突）。
