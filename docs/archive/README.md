# archive/ —— 已完成的计划原文

四份计划的执行史实况在 `docs/WORKLOG.md`（§0.x 每轮），落地决策在 `docs/adr/`；
这里只保留**计划原文**，价值是 file:line 级的根因证据与当时的取舍记录。

| 文件 | 是什么 | 去向 |
|---|---|---|
| `PLAN.md` | 首批计划（F/P 批次） | 已由 PLAN-V2 / PLAN-LIVE-ROUND2 接续完成；F10-F14 根因诊断的 file:line 证据库 |
| `PLAN-V2.md` | V2 六批重设计 | 全部执行完毕，真源已迁 WORKLOG/ADR |
| `PLAN-LIVE-ROUND2.md` | 真机第二轮六批 | 全部执行完毕（偏差两处已在文内注明） |
| `PLAN-ROUND3.md` | 第三轮 A-H 批 | 大部分落地；**H 批（supply_guard 显式开关）已被 ADR-0034 推翻**（彻底删除 auto-supply，诊断取代掩盖） |

历史可追 `git log --follow -- docs/archive/<file>`。
