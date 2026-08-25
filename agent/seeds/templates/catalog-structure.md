# catalog/ 目录结构规划

> 三族数据手册的组织方式：每族一个文件夹，文件夹内按数据类型分文件。
> 每个文件的格式约定在下方逐个定义。implementation-notes.md 只引用本文档，不重复列数据。

---

## 目录结构

```
catalog/
  terran/                 人族（我方主力，数据最完整）
    units.md              单位数据
    buildings.md          建筑数据
    addons.md             挂件数据
    upgrades.md           升级数据
    abilities.md          单位技能数据
    matchups.md           对抗其他族的策略要点
  zerg/                   虫族（参考级，了解对手）
    units.md
    buildings.md
    upgrades.md
    abilities.md
    matchups.md
  protoss/                神族（参考级，了解对手）
    units.md
    buildings.md
    upgrades.md
    abilities.md
    matchups.md
```

---

## 分文件的理由

| 文件 | 为什么单独 | 读取场景 |
|---|---|---|
| units.md | 单位数量多（人族16个），每行字段多 | 写规划时查某个单位造价/前置；识敌时查对手单位 |
| buildings.md | 建筑有挂件/前置关系，和生产链路紧密相关 | 写规划时查前置链；判断对手科技路线 |
| addons.md | 挂件决定产位类型（普通/科技），和产能计算直接相关 | 判断产线能产什么；写规划时决定挂什么 |
| upgrades.md | 升级有前置链（1→2→3），容易漏 | 写规划时查升级前置；健康检查 prereq_missing |
| abilities.md | 技能影响战术判断（兴奋剂/架炮/隐身等），但不影响生产规划 | 商量战术时参考；对局中判断威胁 |
| matchups.md | 对抗要点不是基本数据，是经验总结 | 商量战术时参考 |

### 人族 vs 虫族/神族的详细程度差异

| 数据类型 | 人族 | 虫族/神族 |
|---|---|---|
| units.md | 完整：11个字段 | 参考：9个字段（去掉 addon_req，虫族无挂件；神族简化） |
| buildings.md | 完整：11个字段 | 参考：7个字段（去掉挂件相关） |
| addons.md | 完整：人族8个挂件 | 不适用（虫族无挂件；神族无挂件） |
| upgrades.md | 完整：28个升级 + 前置链 | 参考：只列关键升级（影响对局判断的） |
| abilities.md | 完整 | 参考：只列威胁技能 |
| matchups.md | 有 | 有 |

---

## 每个文件的格式约定

### 1. units.md（单位数据）

#### 人族格式（完整级，11字段）

```markdown
# 人族单位数据

| type | name_zh | category | 矿物 | 气体 | 补给 | 建造时间(s) | 产出建筑 | 前置 | 挂件要求 | 说明 |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| terran/scv | SCV | unit | 50 | 0 | 1 | 12 | terran/commandcenter | — | none | 采集工人 |
| terran/marine | 机枪兵 | unit | 50 | 0 | 1 | 18 | terran/barracks | — | none | 反应堆双倍出兵 |
| terran/marauder | 劫掠者 | unit | 100 | 25 | 2 | 22.5 | terran/barracks | — | techlab | 反装甲 |
| terran/reaper | 死神 | unit | 50 | 50 | 1 | 31 | terran/barracks | — | techlab | 跳跃 |
| terran/ghost | 幽灵 | unit | 200 | 100 | 2 | 33 | terran/barracks | terran/ghostacademy | techlab | 隐身/核弹 |
| terran/hellion | 恶火 | unit | 100 | 0 | 2 | 16 | terran/factory | — | none | 可变形为地狱蝠 |
| terran/widowmine | 寡妇雷 | unit | 75 | 25 | 2 | 27 | terran/factory | — | none | 隐埋 |
| terran/cyclone | 飓风 | unit | 150 | 100 | 3 | 38 | terran/factory | — | techlab | 锁定 |
| terran/siegetank | 攻城坦克 | unit | 150 | 125 | 3 | 45 | terran/factory | terran/factorytechlab | techlab | 可架炮 |
| terran/thor | 雷神 | unit | 300 | 200 | 6 | 51 | terran/factory | terran/armory | techlab | 对空/对地 |
| terran/viking | 维京战机 | unit | 150 | 75 | 2 | 30 | terran/starport | — | none | 可变形为地面 |
| terran/medivac | 医疗运输机 | unit | 100 | 100 | 2 | 30 | terran/starport | — | none | 运输+治疗 |
| terran/liberator | 解放者 | unit | 125 | 75 | 2 | 35 | terran/starport | — | none | 可部署防空域 |
| terran/raven | 鸦 | unit | 100 | 200 | 2 | 43 | terran/starport | — | techlab | 侦察/干扰 |
| terran/banshee | 女妖 | unit | 150 | 100 | 3 | 40 | terran/starport | — | techlab | 隐身 |
| terran/battlecruiser | 战列巡洋舰 | unit | 400 | 300 | 6 | 64 | terran/starport | terran/fusioncore | techlab | 战术折跃 |
```

#### 字段说明

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `type` | str | 系统内标识，用于生产规划/observe/simulate_plan | `terran/siegetank` |
| `name_zh` | str | 中文名，人类可读 | 攻城坦克 |
| `category` | enum | 固定 `unit` | `unit` |
| `矿物` | int | 矿物花费 | 150 |
| `气体` | int | 气体花费 | 125 |
| `补给` | int | 占用补给数 | 3 |
| `建造时间(s)` | float | 训练时间，秒 | 45 |
| `产出建筑` | str | 在哪个建筑训练 | `terran/factory` |
| `前置` | str | 额外前置（建筑/升级），多个用逗号 | `terran/factorytechlab` |
| `挂件要求` | enum | `none`/`techlab`/`reactor` | `techlab` |
| `说明` | str | 简短描述 | 可架炮 |

#### 虫族/神族格式（参考级，9字段）

去掉 `category`（固定 unit）和 `挂件要求`（虫族无挂件，神族无挂件）：

```markdown
# 虫族单位数据

| type | name_zh | 矿物 | 气体 | 补给 | 建造时间(s) | 产出建筑 | 前置 | 说明 |
|---|---|---:|---:|---:|---:|---|---|---|
| zerg/drone | 工蜂 | 50 | 0 | 1 | 9 | zerg/hatchery | — | 采集工人 |
| zerg/zergling | 跳虫 | 25 | 0 | 0.5 | 17 | zerg/spawningpool | zerg/spawningpool | 廉价快速近战 |
...
```

---

### 2. buildings.md（建筑数据）

#### 人族格式（完整级，11字段）

```markdown
# 人族建筑数据

| type | name_zh | category | 矿物 | 气体 | 补给 | 建造时间(s) | 产出建筑 | 前置 | 挂件要求 | 说明 |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| terran/commandcenter | 指挥中心 | building | 400 | 0 | +10 | 71 | — | — | none | 主基地；可升级 |
| terran/orbitalcommand | 轨道空降 | building | 150 | 0 | — | 25 | terran/commandcenter | — | none | 扫描/MULE |
| terran/planetaryfortress | 行星要塞 | building | 150 | 150 | — | 50 | terran/commandcenter | — | none | 防御炮塔 |
| terran/supplydepot | 补给站 | building | 100 | 0 | +8 | 21 | — | — | none | 可升降 |
| terran/refinery | 精炼厂 | building | 75 | 0 | — | 21 | — | terran/commandcenter | none | 建在气矿上 |
| terran/barracks | 兵营 | building | 150 | 0 | — | 46 | — | terran/supplydepot | none | 产步兵 |
| terran/factory | 工厂 | building | 150 | 100 | — | 46 | — | terran/barracks | none | 产装甲 |
| terran/starport | 星港 | building | 150 | 100 | — | 46 | — | terran/factory | none | 产空军 |
| terran/engineeringbay | 工程站 | building | 125 | 0 | — | 25 | — | — | none | 步兵攻防升级 |
| terran/armory | 军械库 | building | 125 | 100 | — | 46 | — | terran/factory | none | 装甲/空军攻防升级 |
| terran/ghostacademy | 幽灵学院 | building | 150 | 50 | — | 40 | — | terran/barracks | none | 解锁幽灵 |
| terran/fusioncore | 聚变核心 | building | 150 | 150 | — | 46 | — | terran/starport | none | 解锁战列巡洋舰 |
| terran/bunker | 地堡 | building | 100 | 0 | — | 30 | — | terran/barracks | none | 步兵驻扎 |
| terran/sensortower | 感应塔 | building | 125 | 100 | — | 25 | — | terran/engineeringbay | none | 侦测 |
| terran/missileturret | 导弹塔 | building | 100 | 0 | — | 18 | — | terran/engineeringbay | none | 防空 |
```

#### 字段说明

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `type` | str | 系统内标识 | `terran/barracks` |
| `name_zh` | str | 中文名 | 兵营 |
| `category` | enum | 固定 `building` | `building` |
| `矿物` | int | 矿物花费 | 150 |
| `气体` | int | 气体花费 | 0 |
| `补给` | int | 提供补给数（`+N`）或不占补给（`—`） | `+8` 或 `—` |
| `建造时间(s)` | float | 建造时间，秒 | 46 |
| `产出建筑` | str | 建筑由 SCV 建造，填 `—`；升级形态填原建筑 | `—` |
| `前置` | str | 前置建筑，多个用逗号 | `terran/supplydepot` |
| `挂件要求` | enum | `none`（建筑本身可挂挂件，但建造时不需要挂件） | `none` |
| `说明` | str | 简短描述 | 产步兵 |

#### 虫族/神族格式（参考级，7字段）

去掉 `category`、`挂件要求`、`产出建筑`（虫族建筑由工蜂变异，神族由探针折跃，不需要此字段）：

```markdown
# 虫族建筑数据

| type | name_zh | 矿物 | 气体 | 建造时间(s) | 前置 | 说明 |
|---|---|---:|---:|---:|---|---|
| zerg/hatchery | 孵化场 | 300 | 0 | 39 | — | 主基地（可升级） |
| zerg/lair | 虫穴 | 150 | 100 | 50 | zerg/spawningpool | 二本 |
...
```

---

### 3. addons.md（挂件数据）

#### 人族格式（完整级，11字段）

```markdown
# 人族挂件数据

| type | name_zh | category | 矿物 | 气体 | 补给 | 建造时间(s) | 产出建筑 | 前置 | 挂件要求 | 说明 |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| terran/reactor | 反应堆 | addon | 50 | 50 | — | 25 | — | 依附建筑 | none | 双倍出兵（2普通产位） |
| terran/techlab | 科技实验室 | addon | 50 | 25 | — | 25 | — | 依附建筑 | none | 解锁高级单位/升级（1科技产位） |
| terran/barrackstechlab | 兵营科技实验室 | addon | 50 | 25 | — | 25 | terran/barracks | — | none | 同 techlab，绑定兵营 |
| terran/barracksreactor | 兵营反应堆 | addon | 50 | 50 | — | 25 | terran/barracks | — | none | 同 reactor，绑定兵营 |
| terran/factorytechlab | 工厂科技实验室 | addon | 50 | 25 | — | 25 | terran/factory | — | none | 同 techlab，绑定工厂 |
| terran/factoryreactor | 工厂反应堆 | addon | 50 | 50 | — | 25 | terran/factory | — | none | 同 reactor，绑定工厂 |
| terran/starporttechlab | 星港科技实验室 | addon | 50 | 25 | — | 25 | terran/starport | — | none | 同 techlab，绑定星港 |
| terran/starportreactor | 星港反应堆 | addon | 50 | 50 | — | 25 | terran/starport | — | none | 同 reactor，绑定星港 |
```

#### 字段说明

| 字段 | 说明 |
|---|---|
| `产出建筑` | 该挂件依附于哪个建筑（`terran/barracks` 等）；通用 `reactor`/`techlab` 填 `—` |
| `挂件要求` | 固定 `none`（挂件本身不需要再挂挂件） |
| 虫族/神族 | 不适用，无 addons.md 文件 |

---

### 4. upgrades.md（升级数据）

#### 人族格式（完整级，11字段）

```markdown
# 人族升级数据

| type | name_zh | category | 矿物 | 气体 | 补给 | 建造时间(s) | 产出建筑 | 前置 | 挂件要求 | 说明 |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|
| terran/infantryweapons1 | 步兵武器1 | upgrade | 100 | 100 | — | 114 | terran/engineeringbay | — | none | 攻击+1 |
| terran/infantryweapons2 | 步兵武器2 | upgrade | 175 | 175 | — | 151 | terran/engineeringbay | terran/infantryweapons1, terran/armory | none | 攻击+2 |
| terran/infantryweapons3 | 步兵武器3 | upgrade | 275 | 275 | — | 188 | terran/engineeringbay | terran/infantryweapons2 | none | 攻击+3 |
| terran/infantryarmor1 | 步兵护甲1 | upgrade | 100 | 100 | — | 114 | terran/engineeringbay | — | none | 护甲+1 |
| terran/infantryarmor2 | 步兵护甲2 | upgrade | 175 | 175 | — | 151 | terran/engineeringbay | terran/infantryarmor1, terran/armory | none | 护甲+2 |
| terran/infantryarmor3 | 步兵护甲3 | upgrade | 275 | 275 | — | 188 | terran/engineeringbay | terran/infantryarmor2 | none | 护甲+3 |
| terran/vehicleweapons1 | 装甲武器1 | upgrade | 100 | 100 | — | 75 | terran/armory | — | none | 装甲攻击+1 |
| terran/vehicleweapons2 | 装甲武器2 | upgrade | 175 | 175 | — | 90 | terran/armory | terran/vehicleweapons1 | none | 攻击+2 |
| terran/vehicleweapons3 | 装甲武器3 | upgrade | 275 | 275 | — | 105 | terran/armory | terran/vehicleweapons2 | none | 攻击+3 |
| terran/shipweapons1 | 空军武器1 | upgrade | 100 | 100 | — | 75 | terran/armory | — | none | 空军攻击+1 |
| terran/shipweapons2 | 空军武器2 | upgrade | 175 | 175 | — | 90 | terran/armory | terran/shipweapons1 | none | 攻击+2 |
| terran/shipweapons3 | 空军武器3 | upgrade | 275 | 275 | — | 105 | terran/armory | terran/shipweapons2 | none | 攻击+3 |
| terran/vehicleplating1 | 装甲护甲1 | upgrade | 100 | 100 | — | 75 | terran/armory | — | none | 装甲护甲+1 |
| terran/vehicleplating2 | 装甲护甲2 | upgrade | 175 | 175 | — | 90 | terran/armory | terran/vehicleplating1 | none | 护甲+2 |
| terran/vehicleplating3 | 装甲护甲3 | upgrade | 275 | 275 | — | 105 | terran/armory | terran/vehicleplating2 | none | 护甲+3 |
| terran/shipplating1 | 空军护甲1 | upgrade | 100 | 100 | — | 75 | terran/armory | — | none | 空军护甲+1 |
| terran/shipplating2 | 空军护甲2 | upgrade | 175 | 175 | — | 90 | terran/armory | terran/shipplating1 | none | 护甲+2 |
| terran/shipplating3 | 空军护甲3 | upgrade | 275 | 275 | — | 105 | terran/armory | terran/shipplating2 | none | 护甲+3 |
| terran/combatshield | 战斗盾牌 | upgrade | 100 | 100 | — | 110 | terran/barrackstechlab | — | techlab | 机枪兵血量+10 |
| terran/concussiveshells | 震撼弹 | upgrade | 50 | 50 | — | 60 | terran/barrackstechlab | — | techlab | 劫掠者减速 |
| terran/stimpack | 兴奋剂 | upgrade | 100 | 100 | — | 114 | terran/barrackstechlab | — | techlab | 机枪兵/劫掠兴奋剂 |
| terran/punishergrenades | 惩罚手雷 | upgrade | 100 | 100 | — | 80 | terran/factorytechlab | — | techlab | 飓风技能 |
| terran/drillingclaws | 钻地爪 | upgrade | 75 | 75 | — | 79 | terran/factorytechlab | terran/armory | techlab | 恶火升级 |
| terran/hisecautotracker | 自动追踪 | upgrade | 100 | 100 | — | 79 | terran/factorytechlab | — | techlab | 寡妇雷锁定 |
| terran/personalcloaking | 个人隐身 | upgrade | 100 | 100 | — | 120 | terran/ghostacademy | — | none | 幽灵隐身 |
| terran/bansheecloaking | 女妖隐身 | upgrade | 100 | 100 | — | 110 | terran/starporttechlab | — | techlab | 女妖隐身 |
| terran/morphhellbat | 地狱蝠变形 | upgrade | 150 | 150 | — | 60 | terran/factorytechlab | terran/armory | techlab | 恶火变地狱蝠 |
| terran/advancedballistics | 高级弹道学 | upgrade | 150 | 150 | — | 79 | terran/starporttechlab | terran/fusioncore | techlab | 维京射程+3 |
```

#### 字段说明

| 字段 | 说明 |
|---|---|
| `category` | 固定 `upgrade` |
| `补给` | 固定 `—`（升级不占补给） |
| `产出建筑` | 在哪个建筑研发；升级通常需要挂件（`techlab`） |
| `前置` | 前置升级或建筑，多个用逗号；如 `terran/infantryweapons1, terran/armory` |
| `挂件要求` | `techlab` = 需要科技实验室；`none` = 不需要（如幽灵学院本身就能研发） |

#### 虫族/神族格式（参考级，7字段）

```markdown
# 虫族升级数据

| type | name_zh | 矿物 | 气体 | 建造时间(s) | 前置 | 说明 |
|---|---|---:|---:|---:|---|---|
| zerg/adrenalglands | 肾上腺素 | 200 | 200 | 79 | zerg/hive | 跳虫攻速 |
| zerg/chitinousplating | 几丁质甲壳 | 150 | 150 | 79 | zerg/hive | 雷兽护甲+2 |
| zerg/burrowcharge | 冲锋 | 150 | 150 | 71 | zerg/lair | 雷兽冲锋 |
...
```

---

### 5. abilities.md（技能数据）

#### 格式（三族统一，6字段）

```markdown
# 人族单位技能数据

| 兵种 | 技能名_zh | 热键 | 消耗 | 冷却(s) | 说明 |
|---|---|---|---|---:|---|
| terran/marine | 兴奋剂 | T | 自身10血 | 0 | 攻击+移动速度，持续10s |
| terran/marauder | 兴奋剂 | T | 自身10血 | 0 | 同上 |
| terran/ghost | 电磁脉冲 | E | 75气 | 0 | 破坏护盾+减速能量 |
| terran/ghost | 隐身 | G | 持续耗气 | 0 | 隐身 |
| terran/siegetank | 架炮 | E | 无 | 0 | 切换攻城模式 |
| terran/thor | 高爆弹幕 | E | 无 | 0 | 对空溅射 |
| terran/viking | 变形 | E | 无 | 0 | 空地切换 |
| terran/banshee | 隐身 | G | 持续耗气 | 0 | 隐身 |
| terran/battlecruiser | 战术折跃 | T | 无 | 0 | 瞬移到目标位置 |
| terran/raven | 干扰矩阵 | R | 75气 | 0 | 目标无法攻击 |
```

#### 字段说明

| 字段 | 说明 |
|---|---|
| `兵种` | 哪个单位有此技能 |
| `技能名_zh` | 中文名 |
| `热键` | 游戏内热键 |
| `消耗` | 耗气/耗血/无 |
| `冷却(s)` | 冷却时间秒数，0=无冷却或持续 |
| `说明` | 简短描述 |

#### 虫族/神族

只列**威胁技能**（需要我判断对策的）：

```markdown
# 虫族威胁技能

| 兵种 | 技能名_zh | 消耗 | 说明 |
|---|---|---|---|
| zerg/infestor | 真菌增生 | 75气 | 遁地，范围定身+伤害 |
| zerg/viper | 绑架 | 无 | 拉走我方单位 |
| zerg/baneling | 爆炸 | 自杀 | 溅射伤害 |
```

```markdown
# 神族威胁技能

| 兵种 | 技能名_zh | 消耗 | 说明 |
|---|---|---|---|
| protoss/sentry | 力场 | 50气 | 阻断地面通行 |
| protoss/sentry | 守护之盾 | 75气 | 减少远程伤害 |
| protoss/templar | 风暴 | 125气 | 范围伤害 |
| protoss/darktemplar | 永久隐身 | 无 | 需侦测 |
```

---

### 6. matchups.md（对抗要点）

#### 格式（三族各有，经验性内容）

```markdown
# 人族 vs 虫族

## 关键时间点
- 4:00 跳虫前压：需要墙+地堡
- 6:00 蟑螂一波：需要坦克或大量机枪+地堡
- 8:00 飞龙骚扰：需要导弹塔+维京/机枪
- 10:00 雷兽：需要坦克架炮+大量火力

## 兵种克制
| 我方 | 克制 | 怕 |
|---|---|---|
| 机枪兵+兴奋剂 | 跳虫、飞龙 | 蟑螂、雷兽、爆虫 |
| 攻城坦克架炮 | 蟑螂、雷兽 | 飞龙绕后 |
| 维京 | 飞龙、腐化者 | 皇后 |
| 女妖隐身 | 工蜂骚扰 | 皇后、眼虫 |

## 建筑节奏
- 对虫族必须堵口（补给站+兵营+补给站墙）
- 二矿要早（5:00 前开二矿对抗虫族经济）
- 工程站早出（步兵攻防对打虫族核心）
```

```markdown
# 人族 vs 神族

## 关键时间点
- 4:00 暗堂：需要感应塔/地堡/扫描
- 6:00 不朽者推：需要坦克+EMP
- 8:00 巨像：需要维京
- 10:00 风暴：需要分散+维京

## 兵种克制
| 我方 | 克制 | 怕 |
|---|---|---|
| 机枪兵 | 狂热者（有微操）| 不朽者、巨像、风暴 |
| 攻城坦克 | 不朽者（需EMP） | 闪烁追猎 |
| 维京 | 巨像、虚空舰 | 凤凰 |
| 幽灵+EMP | 圣堂、不朽者 | — |

## 建筑节奏
- 对神族可选不堵口（用机枪微操防守）
- 工程站早出（攻防对枪兵核心）
- 军械库中期出（装甲武器对坦克）
- 幽灵学院视对面出圣堂而定
```

---

## 数据准确度要求

| 数据 | 人族 | 虫族/神族 |
|---|---|---|
| 矿物/气体 | 精确 | 精确（判断对面花了多少） |
| 补给 | 精确 | 精确 |
| 建造时间 | 精确 | 精确（判断对面多快） |
| 前置链 | 精确 | 精确（判断对面科技路线） |
| 挂件要求 | 精确 | 不适用 |
| 技能数据 | 精确 | 参考级（只列威胁技能） |
| 对抗要点 | 经验总结 | 经验总结 |

## 维护说明

- 数据可能随游戏版本/平衡性补丁变化，需定期对账
- 人族数据需要最准确（我方主力），虫神参考级允许少量误差
- `produced_by` 和 `requires` 的区别：produced_by = 在哪个建筑产；requires = 还需要什么额外前置
- `挂件要求`：`none` = 裸建筑即可产；`techlab` = 需科技实验室；`reactor` = 需反应堆
- 建筑的 `产出建筑` 为空（由 SCV 直接建造），`前置` 列前置建筑
- 升级的 `前置` 可含逗号分隔的多个依赖（前置升级 + 前置建筑）
