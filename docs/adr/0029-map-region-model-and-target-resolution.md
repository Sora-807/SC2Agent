# ADR-0029 区域模型与目标解析（RegionLayer × TargetResolver）

- 状态：已确认（grill 会话 2026-08-19）
- 范围：tactical_map 的区域数据结构、双层分区语义、map 名 → 坐标的解析契约
- 关联：ADR-0002 §3/§6、ADR-0008 §3/§4、ADR-0027、docs/P0-影响边界.md D1/D2、spec-003 §2.1/§4.4
- 取代：ADR-0008 §3 的「V1 自动生成基础区域」（改 V1 手工 authoring，自动划分后置，见 D3）

## 背景

1. game.operation 的 params 允许 map 名（region/point），但「名字 → 坐标」没有单一权威实现：谓词侧有一条 _resolve_target 雏形，动作侧透传到 driver（driver 遇字符串直接失败）——中间缺一层目标解析。
2. ADR-0008 §3 只定了「区域」概念，没定数据形态。重叠方框会导致一波兵在两个区域同时被汇报；矩形联合贴不齐地形（斜坡、台地）。

## 决定

### D1 目标解析契约（TargetResolver）

1. **三层职责**：game 管词汇（OP_CATALOG 的参数类型）；tactical_map 管语义（名字 → 坐标，纯函数，离线在线同结果）；engine 管时机（emit Operation 之前解析完毕）。
2. **Operation.params 到达 driver 时必须是纯数值**（[x,y]/tag）；driver 永不接收 map 名（红线 R2：driver 零业务规则）。
3. **去重在解析之后**（spec-003 §2.1：「以解析后的目标值参与去重比较」）。
4. 动态目标（group_center / nearest_enemy）由 engine 先求值成 Point2/tag；Resolver 只处理静态名与字面量。
5. build 的 placement 走 ADR-0027：名字 → BuildSlot → TL+BR+size。
6. 编译期校验引用名存在（R6，flow 编译器 P3 后补）；运行期未知名原样透传 → driver 应用时静默失败（现有 D6/V1 降级路径，不崩游戏）。

### D2 区域模型：一层几何，两层语义

1. **几何只有一层 leaf 分区**（每个格点至多属于一个 leaf）；大区是 leaf 的分组（父指针），不重复存几何——两层分区各存一份必然漂移。
2. **大区层 = 全图强制分区**（每格恰属一个大区，观察层无死角）；leaf 小区稀疏（斜坡/矿区/路口等战术要点），未覆盖格点属大区默认域。
3. 归属查询 region_at(pos) → (big_id, leaf_id | None)，每层无重叠 → 一个单位/一波兵永远只报一个区域（结构上消灭双报）。
4. 区域形状 = 格点集（矩形是退化特例）；加载校验：leaf 4-连通（无洞）、leaf ⊆ parent、每层 cell 唯一、大区全覆盖。
5. **anchor = 作者标注的语义中心/移动目标点**（不用质心——凹区域质心可能出界/卡墙/落洞）；leaf/big/pos_mark 名字全局唯一。
6. **命名 = 跨图统一语义槽**：stable id 如 main_base / main_ramp / natural / choke，flow 文件可跨图复用；每张图按地形把格点分给这些槽位。中文名/别名只用于展示与输入解析，歧义澄清走 router（ADR-0002 §2）。
7. 归属判定：单位 = (int(x), int(y)) 所在格；建筑按 footprint 闭区间（ADR-0027），跨区以 TL 归属，放置校验要求整 footprint 同区。
8. **V1 区域静态**：菌毯推进/矿区枯竭不改变分区；自动矿区划分后置（需求文档 S9）。
9. BuildSlot 改 **TL+BR+size**（br = tl + size - 1）；偶数尺寸的世界坐标换算由 driver 统一实现（ADR-0027 §3），tactical_map 不做奇偶特判。

### D3 Authoring 形态（L1 版本化数据）

- 调色板 PNG（indexed 8-bit，palette 索引 = 区域 key，0 保留 = 无）画格 + YAML 元数据（palette key → stable id、anchor、父子关系、中文名、build_slots 引用）。
- 全图数据属 L1 版本化静态数据，不进代码（ADR-0008）；V1 手工画 LadderMap，自动生成/人工精修管线后置。
- 加载即校验（D2 全部规则），失败报错降级告警（R7），不崩游戏。

## 反例

- Operation 带 map 名进 driver；去重发生在解析之前。
- 大区/小区各存一份几何导致两层对不上。
- 区域只存质心、运行时再猜 footprint；重叠区域双报。
- 在 flow/agent 提示词里写死「主矿在哪」（ADR-0008 反例）。

## 验收标准

1. resolve_action_params 把 move_to(position: main_base) 解析成 [x,y]，driver 只见数值。
2. region_at 对任意格点返回唯一 (big, leaf)。
3. 不连通/有洞/越出 parent/重名/未覆盖的布局加载报错。
4. 同一 flow 文件可在不同地图上运行（名字是语义槽，几何换图）。
