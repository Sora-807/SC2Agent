# ADR-0009 观察新鲜度与上下文失效策略

- 状态：已确认草案
- 范围：agent 上下文管理、观察数据生命周期、look 等缓存结果的过期策略

## 背景

游戏信息量很大。旧项目曾出现过 agent 误用过时观察、重复读旧文件、look 结果跨轮残留等问题。需要明确：

- 哪些数据是“当前事实”，新情报到来后旧数据必须作废。
- 哪些数据是“历史记录”，只能回看，不能作为行动依据。
- 哪些数据是“持久工作记忆”，不能被新鲜度策略误删。

## 决定

### 1. 三种数据生命周期

| 类型 | 生命周期 | 例子 |
|---|---|---|
| 临时观察 | 一个 trigger 周期内有效，新观察生成即作废旧观察 | GameState 摘要、区域汇总、30 秒投影 |
| 缓存查询结果 | 带时间戳与序列号，新观察产生后标记 stale | look 快照、小区域详情 |
| 持久工作记忆 | 跨轮、跨局保留，直到显式改写或按记忆策略压缩 | 当前计划、模块选择、失败原因、用户偏好 |

### 2. ObservationPacket 替代“追加旧观察”

每次 trigger 生成新的 `ObservationPacket`：

```text
ObservationPacket(
    seq,                # 单调递增序号
    game_time,
    tick,
    generated_at,
    supersedes,         # 指向前一个 seq
    sections,           # 当前状态、区域信息、30 秒投影等
)
```

- 新 packet 生成后，前一个 packet 立即标记为 superseded。
- LLM prompt 只注入当前 packet 全文。
- 旧 packet 只保留在 trace 中，不自动再进入 prompt。
- 规则是“替换”，不是“追加”。

### 3. 失效时机

以下任一条件发生时，旧游戏数据上下文作废：

- 新 `GameState` 到达，且 `tick > 当前 packet.tick`。
- 新 `ObservationPacket` 生成完成。
- 当前 trigger 结束：本轮 scratch 观察从工作上下文中清除。
- 对局结束：live 观察全部作废，仅保留 trace 与复盘材料。

### 4. look 等查询结果的过期规则

- `look` 结果属于某个 `ObservationPacket` 的 `seq`。
- 当更新 seq 的 packet 生成后，旧 look 结果标记为 stale。
- stale 结果仍可回看，但必须带 `历史数据` 标记。
- 本轮内、同 seq 下的 look 结果仍可引用。
- 历史 look 只通过 trace/history 工具查看，不注入当前 prompt。

### 5. 历史数据与当前事实的界面

- 所有数据对象带 `seq`、`game_time`、`stale` 状态。
- 当前 prompt 顶部声明：仅以 `seq = current` 的数据作为行动依据。
- 引用旧 seq 数据时，必须显式说明“这是 seq N 的历史快照”。
- alert 是事件流，不是当前状态；只用于说明“发生了什么”，不能替代最新 GameState。

### 6. 持久工作记忆不受影响

- 工作笔记、当前 FlowSpec、模块选择理由、失败原因、用户偏好等持久数据不因观察失效而删除。
- 它们有独立版本与时间戳，按 ADR-0005/0006 的规则更新。

## 反例（明确禁止）

- 每轮把旧观察继续追加进 prompt，导致上下文无限膨胀。
- look 结果跨 seq 存活且无 stale 标记。
- 把历史 GameState 当作当前 GameState 交给 planner。
- 因为“作废旧观察”而删除工作笔记和持久计划。
- 用 alert 历史代替最新资源/单位状态。

## 验收标准

1. 第 N+1 次 trigger 后，prompt 中只包含 seq=N+1 的当前观察，不包含 seq=N 的全文。
2. 旧观察可从 trace 中按 seq 回看，并带有历史标记。
3. look 快照在新 seq 生成后标记为 stale，当前 prompt 不注入。
4. 持久工作记忆跨多个 trigger 存活。
5. 对局结束后 live 观察全部失效，复盘材料仍可访问。
