"""planner.module_defs：内置 production module（code 注册）。

V1 用 code 函数注册模块（module_ref → params → op 序列）；YAML authoring 后续。
P6 设计 20+4 build order 时补全/调整这里（加更多模块、调参数）。
"""
from __future__ import annotations

from planner.build_order import AssignWorkers, Build, Research, Train, register_module


def _factory_chain(params: dict):
    """工厂链：factory → factorytechlab → train siegetank×tank_count。"""
    tank_count = int(params.get("tank_count", 4))
    ops = [Build("terran/factory"), Build("terran/factorytechlab")]
    ops += [Train("terran/siegetank") for _ in range(tank_count)]
    return ops


def _basic_opening(params: dict):
    """简单开局（测试用）：depot → scv×N → barracks。P6 换成真实开局。"""
    scv_count = int(params.get("scv_count", 12))
    return [Build("terran/supplydepot"),
            *[Train("terran/scv") for _ in range(scv_count)],
            Build("terran/barracks")]


def _bio_tank_opening(params: dict):
    """步坦协同开局 V3：农民优先 + 显式补给 + 科技攀升 + 二矿 + 攻防升级。

    V3（2026-08-24）：**显式插 depot** —— V2 依赖 planner supply_guard 自动插入，
    但 supply_guard 已按 PLAN-V2 D7 删除（诊断取代掩盖），模块没跟着改导致
    12/13 人口死等（真机与干跑同款：只执行 1 个 SCV 后整队冻结）。
    补给位仍取「尽可能晚」：只在即将卡人口的批次前插。

    设计原则：
    - 农民优先：每阶段间插 SCV 保持经济（scv_interleave 控制）
    - 科技攀升：兵营 → 气矿 → 工厂 → 工程站 → 攻防升级
    - 反应堆：双兵营各挂反应堆（4 训练槽，20 机枪 90s vs 单兵营 180s）
    - 二矿：首批机枪后下二矿（CC 71s 建造期间出兵，完工后收入涌注坦克）
    - 终局：20 机枪 + 4 坦克 + 攻防 1 级 → 进攻
    """
    tank_count = int(params.get("tank_count", 4))
    marine_target = int(params.get("marine_target", 20))
    n = int(params.get("scv_interleave", 3))  # 每阶段间插 SCV 数（3 = 15 训练 = 27 总）
    second_rax = bool(params.get("second_barracks", True))
    expansion = bool(params.get("expansion", True))

    depot = Build("terran/supplydepot")
    ops: list = []
    # Phase 1: 农民 + 兵营（12/13 起步：第 1 个 SCV 后即插 depot，其余照训）
    ops.append(Train("terran/scv"))
    ops.append(depot)                            # 13 → 21
    ops += [Train("terran/scv") for _ in range(n - 1)]
    ops.append(Build("terran/barracks"))
    ops += [Train("terran/scv") for _ in range(n)]

    # Phase 2: 气矿 ×2 + 第二兵营 + 分配气工（气工紧跟精炼厂，气收入按精炼厂数封顶）
    ops.append(Build("terran/refinery"))
    ops.append(AssignWorkers("gas", 3))           # 紧跟 → 精炼厂完工即有气工
    ops += [Train("terran/scv") for _ in range(2)]
    ops.append(Build("terran/refinery"))
    ops.append(AssignWorkers("gas", 3))
    if second_rax:
        ops.append(Build("terran/barracks"))   # 第二兵营（双反应堆 = 4 训练槽）

    # Phase 3: 工厂 + 反应堆 ×2 + 工程站 + 农民（消耗余矿）
    ops.append(Build("terran/factory"))
    ops.append(Build("terran/reactor"))          # 兵营 #1 反应堆
    if second_rax:
        ops.append(Build("terran/reactor"))      # 兵营 #2 反应堆
    ops.append(Build("terran/engineeringbay"))
    ops.append(depot)                            # 20/21 → 29（Phase 3 农民前）
    ops += [Train("terran/scv") for _ in range(n)]

    # Phase 4: 首批机枪 + 坦克科技 + 二矿 + 攻防升级
    ops += [Train("terran/marine") for _ in range(4)]   # 2 反应堆兵营 = 4 槽
    ops.append(Build("terran/factorytechlab"))
    if expansion:
        ops.append(Build("terran/commandcenter"))         # 二矿（71s 期间持续出兵）
    ops.append(Research("terran/infantryweapons1"))

    # Phase 5: 持续出兵 + 坦克 + 装甲升级（机枪/坦克批次前补足供给）
    ops += [Train("terran/scv") for _ in range(4)]       # 二矿完工后多出农民
    ops.append(depot)                            # 31/42 → 50
    ops += [Train("terran/marine") for _ in range(marine_target - 4)]
    ops += [depot, depot]                        # 47/50 → 66（坦克 ×4 吃 12 人口）
    ops += [Train("terran/siegetank") for _ in range(tank_count)]
    ops.append(Build("terran/armory"))
    ops.append(Research("terran/infantryarmor1"))

    return ops


# 导入即注册（planner/__init__ 导入本模块触发）
register_module("factory_chain", _factory_chain)
register_module("basic_opening", _basic_opening)
register_module("bio_tank_opening", _bio_tank_opening)
