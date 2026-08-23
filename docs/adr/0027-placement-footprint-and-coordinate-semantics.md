# ADR-0027 建筑 placement、footprint 与坐标语义

- 状态：已确认草案
- 替代：无（细化 ADR-0002/0004/0008）
- 范围：placement 存什么、偶数尺寸建筑如何对齐、单位坐标是整数还是浮点、距离判定单位

## 背景

旧项目踩过建筑中心偏移/坐标混用的坑：2×2 与 3×3 建筑中心规则不同，导致补给站卡死和 footprint 误判。新架构必须在一开始固定坐标与 placement 语义。

## 决定

### 1. 两种坐标，严格区分

| 类型 | 表示 | 用途 |
|---|---|---|
| 建筑格点 | 整数格，左下角 (0,0)，x 右 y 上 | 建筑 TL/BR、区域、预留区、footprint |
| 单位位置 | 浮点世界坐标，左下角原点，x 右 y 上 | 单位位置、距离判定、移动目标 |

- 单位位置不做取整存储；`GridPos` 只用于建筑和区域。
- `distance_between` / `arrived` 使用浮点世界距离；radius 单位是世界距离，不是格数。
- 格 ↔ SC2 世界坐标转换只存在于 driver 的一个函数中，并通过 size 2/3/5 fixture 测试。

### 2. 建筑 placement 表示

```text
Placement(
    kind,       # explicit | region | reservation | policy
    tl,         # 整数格左上/左下角（按新坐标系左下角语义为 min corner）
    br,         # 对顶角，br = tl + size - 1
    size,       # 格数：2/3/5...
)
```

- 内部一律使用 `tl + br + size` 双角点表示，不允许只存中心点。
- `tl` 是建筑 footprint 的最小 x/y 格点。
- `br = (tl.x + size - 1, tl.y + size - 1)`。
- 重叠判断基于 `[tl.x, br.x] × [tl.y, br.y]` 的闭区间。
- `placement` 必须携带或可解析出 TL；否则模块编译失败 `MISSING_PLACEMENT`。
- 区域/预留区/policy 解析由 `tactical_map` 或 ConstructionScheduler 完成，最终都输出 TL+BR+size。

### 3. 偶数尺寸对齐

- 所有建筑 footprint 按整数格闭区间表达。
- 偶数尺寸（2×2）与奇数尺寸（3×3、5×5）的 SC2 世界中心换算由 driver 统一实现。
- 具体换算公式在 driver spike 阶段用旧项目 ADR-0011 的经验和 burnysc2 实测 fixture 锁定。
- 核心模块不得根据 size 奇偶做不同业务分支；只通过 driver 转换函数屏蔽差异。

### 4. 地图层与区域

- 区域/预留区使用整数格矩形，同样以 min-corner + max-corner 表示。
- map_layers 以格/世界查询接口暴露；约束层不关心内部存储。
- 未来虚拟建筑位置显示使用 Placement.tl，并标记 exact 或 estimated。

## 反例（明确禁止）

- 建筑只存中心点，运行时再猜 footprint。
- 单位位置取整后参与距离判定。
- 核心模块写 `if size % 2 == 0` 这类奇偶特判。
- 重叠判断用中心距离而不是 footprint 闭区间。
- placement 为 null 的 build 通过编译。

## 验收标准

1. 任意合法 Placement 都能得到确定 TL+BR+size。
2. 2×2 与 3×3 建筑在相同 TL 下 footprint 与 SC2 实际一致（driver fixture）。
3. 单位距离判定使用浮点世界距离，不受格点取整影响。
4. 区域重叠/预留冲突按闭区间判定。
5. 核心模块不存在奇偶尺寸业务分支。
