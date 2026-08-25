# PLAN-ROUND3：复盘投影改版 + 观测/警报/睡眠批次

2026-08-24 真机复盘后用户报五问题 + 复盘投影堆叠，根因全部定位（见会话），
本文是执行计划。拍板记录：

- 聊天两 bug 修法无分歧（segments 落史 + 插话进 live 时间线）。
- observe 加 bbox 读格点；`maps/<源>/<bbox>.md` 的 read 路径**保留 index.md 当源清单**，
  格点网格主入口改 observe（用户拍板"同意"）。
- TRAIN 卡死：**先只告警不动作**（severity 台阶升级，不自动丢弃/重排）。
- 队列体检：**只诊断报告 + 改进建议**，让 agent 按建议手动插入。
- 复盘投影：F17 历史累积是"决策错误"，**改回去**（用户拍板）。

## 根因速查

| # | 现象 | 根因 |
|---|---|---|
| 1a | 轮完成瞬间吞正文 | `round` 事件 `setMessages(ev.messages)+setLive(null)`（ChatDock.tsx:367-375）换成两段式落史；talk.py:508-510 只存最后一段正文、_round_steps 只收 tool/reasoning，中间正文从未序列化 |
| 1b | 插话跑到最上面 | 双槽渲染：messages 在上 / live 钉最下；插话路径 append 进 messages 不动 live（ChatDock.tsx:347-360） |
| 2 | maps 路径"幻觉" | 假 not-found：7 次失败路径全符合约定，死于 MAX_COLS/ROWS=14（region_view.py:33-34）；有用 ValueError 被 ReadOnlyArea.exists（readonly.py:42-47）+ vendor contains 预检（tools.py:91-92）吞成 "not found" |
| 3 | sleep 不醒 | tools.py:291-337 只有相对 game_seconds；早退仅 未连接/300s 墙钟/用户插话；游戏结束不出"未连接" |
| 4 | 无警报感 | AlertService 9 种已有但 (a) 无敌方接触类（只有当帧计数）(b) 纯拉模式，sleep 不看 |
| 5 | 枪兵卡队首 | queue_blocked 会响（alerts.py:56-86）但 agent 在 sleep 看不见；TRAIN 无 flight/超时（BUILD 有 flights.py:40-145） |
| 6 | 自动补给疑虑 | _supply_guard（planner.py:111-142）只改投影副本，live 队列从未自动插；真问题是投影撒谎 |
| 7 | 复盘投影泳道爆炸 | F17 累积（use-accumulated.ts）：events 去重键含 t，每次重投影 t 漂移 → 逐帧全量叠加 |

## 批次

### G（本批执行）：复盘投影改版
- G1 拆累积：删 `web/src/charts/use-accumulated.ts`；ProjectionBoard 与
  WholeOpsList（复盘队列卡）改吃原始帧 —— 板上只剩 [T, T+horizon]。
- G2 截断设计（用户 2026-08-24 拍板）：
  - T = 当前帧 based_on_game_time；视窗左缘钳 max(0, T-30)（30s 给在产条显名字）；
  - 红截断线钉 T，贯穿泳道+曲线；左侧除部分条外为空；
  - 在建部分条：world.units 0<build_progress<1 + catalog.build_time →
    from = T - bt·bp，to = T + bt·(1-bp)，与投影条同 stable_id 且跨 T 的合并左端。
- G3（后端小改，下批）：训练订单 SC2 无进度（rev13 收窄 None）→ runtime 在
  `_try_train` emit 时记 emitted 游戏时间，snapshot 暴露 in-flight 训练表，
  前端 join 画训练部分条。契约 REV+1。

### A：聊天 segments（前后端契约）
- talk.py 落史消息改 segments 模型（text/tool/reasoning 交错全序列化）；
  ChatMessage 契约加 segments；AgentMessage 按 segments 渲染；
  插话消息插进 live 时间线（或 round 落史时保序）。

### B：observe + bbox
- observe 工具加可选 bbox（左下/右上）+ step + source（默认 live 会话规划）；
  复用 render_region；错误如实：超范围报"地图 176×160，x2=… 超出"，
  超限报上限+建议 step（把 render_region 的 ValueError 送到模型面前，
  不再被 exists 吞掉）。maps/index.md 保留源清单职责，格点主入口改 observe。

### C：sleep 补齐
- 加 `until_game_time`（绝对游戏时间）参数；
- 轮询加对局结束判定（driver result/over）→ 提前醒并告知。

### D：敌方接触警报 + 唤醒链
- producer 每帧维护 per-tag last_seen → 滚动 10s 窗 → `enemy_contact` 警报
  （窗内不同敌兵数 + 峰值同屏数 + 最后出现位置）；
- sleep 轮询加"warn 及以上警报即醒"（与用户插话同通道）。

### E：TRAIN 卡死告警升级（只告警不动作）
- blocked 持续时间分级抬 severity（如 30s warn → 120s error）；
  AlertView 文案区分"产出建筑被毁"与"未建"。

### F：队列体检（只诊断）
- 新工具/操作：对在线/离线队列跑检测（卡补给/卡科技/前置顺序错误），
  返回改进建议（插 depot 位置/重排 diff），agent 手动执行插入；
  检测同时挂 supply_block/tech_block 警报。

### H：supply_guard 显式开关
- simulate_plan 加 auto_supply 参数（默认关）；live 前瞻投影不再自动插 depot
  —— 卡人口真实浮出，配合 F 的体检建议。

执行顺序：G（已完成部分见 worklog）→ A → B → C → D → E/F/H。
提交策略：全部完成前不提交，等用户验收。
