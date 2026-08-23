# Driver Spike 结论（P1 / M0.5 / D5）

> 来源：`modules/driver/spike.py` 跑 LadderMap（破晓黎明 - 天梯版），realtime=False，game_time_limit=120。
> 日志：`docs/driver_spike.log`。
> 环境：Python 3.12 + burnysc2 7.3.0（import 名 `sc2`），SC2 Base97579。

## 已答（D5）

### 1. SC2 启动 + bot 跑通 ✓
`run_game(realtime=False, game_time_limit=120)` 到时返回 `Result.Tie`，进程自动清理。

### 2. RawGameState 形态（burnysc2 7.x BotAI 暴露）
- **单位集合**：`self.units`（己方机动单位）、`self.workers`、`self.townhalls`、`self.enemy_units`（可见敌方）、`self.all_units`（**含矿脉/气井/doodad 等中性对象**，开局 136 个）。→ 矿脉位置可从 `all_units` 按 type 过滤取（map 模块的 resource 节点来源）。
- **Unit 属性**：`tag`、`type_id`、`position`(Point2 浮点)、`alliance`(int: 1=self, 3=enemy)、`health`/`health_max`、`shield`、`energy`、`build_progress`、`orders`、`facing`、`buffs`、`is_carrying_minerals/vespene`。`cargo_space_used` 缺（运输机装载需另查）。
  - **注意命名差异**：burnysc2 用 `health`（非 `hp`）；owner 用 `alliance`（int，非 enum）。
- **Order**：`order.ability`（`AbilityData(name=Gather/Move/...)`）、`order.target_world_space_pos`（Point2|None）。
- **resources**：`self.minerals/vespene/supply_used/supply_cap` ✓。
- **creep/visibility**：`self.state.creep` / `self.state.visibility`，`PixelMap`，**dims = map_size**（176×160），与地图格点对齐。
- **map**：`self.game_info.map_size`=(176,160)；`self.game_info.map_name`。

### 3. 坐标原点 = 左下原点 ✓
start_location=(48.5, 28.5) 落在 176×160 的左下区域；Point2 浮点，x 右 y 上。→ **burnysc2 原生 position 与我们约定一致，world 模块对 position 的坐标转换 = no-op**。

### 4. CommandPolicy(move)：持久、不需每帧重发、到位清空 ✓
spike iter1 发 move→CC，iter2–5 orders 持续 `Move`（**不重发**），iter6 到位清空（game_time≈1.07）。→ 执行器去重：相同 `(slot,type,move,params)` 不重发；到位/被打断/参数变才重发。

### 5. 步进节拍 ✓
`on_step(iteration)` 每步一调；`self.time` = 绝对游戏秒；~0.179 游戏秒/步（≈4 帧/步）。→ **Operation 下一 step 生效**已验：iter1 发 move，iter2 起 orders 出现。

### 6. auto-order ✓
开局 SCV 自带 `Gather` order（自动采矿）。→ auto-order 白名单含 `Gather`；**用户接管识别** = order 从 auto-order 变为用户命令。

## CommandPolicy 表（部分；其余待后续 spike）

| action | 持续性 | 每帧重发 | 清空时机 | 来源 |
|---|---|---|---|---|
| move | 持久 | 不需要 | 到位（~4-5 步）| spike ✓ |
| attack_move / hold_position / follow / patrol / stop | TBD | TBD | TBD | 待后续 spike |

## 待后续 spike（剩余 D5）

- attack_move / hold_position / follow / patrol / stop 的持续性与重发后果。
- size 2/3/5 建筑坐标转换（建一个建筑，比对 placement grid 与世界坐标）。
- user-takeover 识别（手动操作一个单位，看 order 变化）。
- creep/visibility PixelMap 的 `[x,y]` 索引方向（是否需翻转）。

## 对架构的影响

- **world 模块 V1 几乎是 identity**：position 原点一致、creep/visibility dims 对齐 → V1 world 只需透传 + 按 type 从 `all_units` 过滤出矿脉等中性对象；规则层（power/addon）D11 再插。
- **RawGameState 字段名对齐 burnysc2**：`unit.health`（非 `hp`）、`alliance`（非 `owner`）、`orders[i].ability.name + target_world_space_pos`。P0-影响边界 D1 的字段名据此修正。
- **执行器去重**：move 持久 → 去重按 `(slot,type,action,params)`；到位/被打断/参数变才重发（验证 spec-003 §2.1）。
- **Operation 下一 step 生效**已验（iter1 发 → iter2 见）✓。

## SC2GamePort 集成 check（真 driver，`run_driver_check.py`）

Round 1（step 0 抽 RawGameState）✓：
- counts: all=136 own=9 enemy=127 neutral=0；CC/SCV 属性全对（health/position/orders(Gather)/facing/buffs）。
- resources/minerals/supply ✓；map_size=(176,160)。
- **creep/visibility PixelMap 数据**：经 `data_numpy.tobytes()` 取（176×160=28160 bytes）✓（早先 `.data` attr 为空已修）。
- 坐标左下原点 ✓（CC (48.5,28.5) 落左下）。

Round 2（step 1 submit move → step2+ 跟踪）✓：
- step2-8 orders 持续 `Move`（位置向 target 变化，不重发）→ step9 到位清空 → **op 生效且 state 反映 ✓**。
- 验证 driver 端到端：`submit_operations` → op_queue → `_apply_op`（`self.do`）→ SC2 下一 step 执行 → RawGameState 反映。

### 新发现（D5 follow-up）

- **alliance=3 二义**：`MINERALFIELD`（中性矿脉）alliance=3，与 enemy 同值；burnysc2 `self.enemy_units`=0（正确排除矿脉）。→ **world/catalog 不能单靠 alliance 映射 owner**：矿脉/气井/装饰物按 TYPE 判 neutral，enemy 用 burnysc2 `enemy_units` 或按 type 排除资源类。决策留 world 模块。
- **build/train 的 `params["type"]`** 是稳定 ID 字符串，driver 需稳定 ID → burnysc2 `UnitTypeId` 映射（catalog 建后补）。当前 move/attack_move/hold/stop/follow/focus_fire 已通；build/train 待映射。
