# ADR-0006 单位所有权与域仲裁

- 状态：已确认草案
- 范围：用户操作与 flow 的冲突处理；单位池、工人配额、建造调度

## 背景

需要明确回答：用户操作与 flow 发生冲突时怎么办？

旧项目没有显式机制，flow selector 直接扫描全局单位列表，无法区分“用户正在操控的单位”和“agent 可以调用的单位”，抢单位、抢位置的风险只能靠运气避免。

## 决定

### 1. 三层域仲裁器

| 域 | 仲裁器 | 管理对象 |
|---|---|---|
| 建造生产 | ConstructionScheduler | 全局 BuildRequest 队列与建筑位置 reservation |
| 工人 | WorkerManager | 采矿/采气/侦察/建造/修理配额 |
| 战斗单位 | UnitPool | free / user_leased / flow_leased 租约池 |

### 2. ConstructionScheduler：一个全局生产槽位

- 所有 flow 与用户提交的建造请求进入同一个队列。
- 由统一调度器决定谁来建造、何时建造、建在哪里。
- 建筑位置通过 `tactical_map.find_placement` 查询，自动排除真实建筑、用户预留区、flow 预留区。
- 多个 flow 可同时提交请求，但执行由调度器仲裁。

### 3. WorkerManager：配额池，不是整池征用

SCV/Probe/Drone 不能整体归某个 flow，因为工人同时承担采矿、采气、侦察、建造、修理。按配额管理：

```text
mineral_workers
gas_workers        # 每精炼厂 0~3
build_reserve      # 有 BuildRequest 时动态占 1
repair_reserve
scout_quota
```

- 经济模块调整长期配额。
- 侦察 flow 只能申请 `scout_quota`。
- 建造调度器从 `build_reserve` 借工人，完成归还。
- 申请不到配额的 flow 排队或报警，不允许直接抓走矿区工人。

### 4. UnitPool：战斗单位三态池

```text
free         → 所有 flow selector 可筛选
user_leased  → 用户直接操控中，任何 flow 不可选
flow_leased  → 某 flow 已征用，其他 flow 不可选
```

- 每个 flow 必须通过 selector 从 `free` 池筛单位，不允许扫描全局单位列表。
- 多 flow 同时申请同一批 free 单位时，优先级：

```text
user > safety > tactical > strategy
```

- 高优先级不抢低优先级已经到手的单位，只影响同时申请 free 单位时的分配顺序。
- flow 缺兵时等待或发出提示，不抢用户的兵。

### 5. 租约生命周期

```text
free → leased(controller, reason, policy) → free
```

释放策略：

| 策略 | 说明 |
|---|---|
| `complete + idle_grace` | 当前指令完成，空闲 1 秒无新指令即归还 |
| `defense_ttl` | 防守指令：前 10 分钟有效期 30 秒，之后 10 秒 |
| `until_signal` | 直到 flow 完成，或用户显式“交还/清空战术” |
| `fixed_ttl` | 固定时长，例如侦察 N 秒 |

防守 TTL 的 V1 定义：

```text
defense_ttl(game_time):
    return early_ttl if game_time < split_time else late_ttl
# 默认参数：early_ttl=30.0, late_ttl=10.0, split_time=600.0
```

- 阈值必须参数化，不允许散落魔法数。
- 它只用于被识别为防守性质的用户指令（如 hold position、守斜坡、攻击到防守点）。普通移动/攻击仍使用“指令完成 + 空闲 1 秒归还”。

### 6. 不做 V1 全局战力仲裁

不实现“兵少时以用户为准，兵多时以 flow 为准”的全局切换。理由：

- 会造成单位在用户与 flow 之间来回易主，行为难以解释。
- V1 只需要保证：用户实际操控的单位进入 `user_leased`，剩余 free 单位继续供 flow 使用。
- 如果未来需要限制用户租借规模，再增加 `user_quota` 配置，另开 ADR。

### 7. 用户操作识别（SC2 现实限制）

raw SC2 observation 没有可靠的 `issued_by = user` 字段。采用分层策略：

1. **命令记账**：driver 记录自己发出的每条 `apply(intent)`，建立预期 order 指纹。
2. **自动 order 白名单**：SCV 建完自动回采、自动索敌/追击、集结跟随、工人自动修理等游戏自身产生的 order 属于可解释自动 order，不判定为用户接管。
3. **order 推断**：每帧比对单位当前 orders：
   - 与预期 order 或自动 order 白名单匹配 → 不标记用户接管。
   - 出现无法解释的新 order → 推定用户接管，标记 `user_leased`。
4. **安全 fallback（V1 默认）**：无法可靠判断时，按“当前 order 完成 + 空闲 1 秒”归还 free。

最坏代价是 flow 晚约 1 秒拿到单位，可接受，远好过抢兵。

### 8. driver 探测任务

在 driver 阶段正式实现前，先探测 burnysc2 可提供的字段：

```text
unit.orders 全队列
order.ability / target / progress
能否感知 selection 或 UI 命令
自己 apply 的命令与 orders 的对应关系
自动 order 白名单：建完回采、自动索敌/追击、集结跟随、自动修理
```

在探测结论出来前，所有权设计不依赖“完美识别用户操作”。

### 9. 游戏内直接鼠标操控的边界

第一代明确假设：浏览器、语音、dispatch 进入系统的用户操作，系统能看到 owner。用户在游戏客户端内直接鼠标框选的操作只能 best-effort 推断；无法可靠识别时走安全 fallback。该假设需在实现前再次确认。

## 反例（明确禁止）

- flow selector 扫描全局单位列表并点名单位。
- 用户正在操控的单位被 flow 直接抢走。
- 高优先级 flow 抢夺低优先级 flow 已征用单位。
- 建造调度绕过 tactical_map 自己计算重叠。
- 在“无法识别用户操作”时让单位永久属于某一方。

## 验收标准

1. 可识别场景：用户租借 4 个枪兵后，剩余 free 枪兵仍可被 flow 正常使用。
2. flow 缺兵时等待/提示，不抢 user_leased。
3. 两个 flow 同时申请同一批 free 单位时，分配结果确定且符合优先级。
4. 可识别场景：普通指令完成后约 1 秒归还；防守指令按 30/10 秒 TTL 归还。
5. 不可识别场景：走 fallback“当前 order 完成 + 空闲 1 秒归还”，验收标准为不永久占用、不抢用户正在执行的单位。
6. 建筑位置查询自动排除用户预留与 flow 预留。
