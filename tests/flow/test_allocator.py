"""flow Allocator V1：FCFS + sticky lease 语义（spec-006 接口预留的简实现）。"""
from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_all
from flow.allocator import Allocator
from tests.factories import make_gs, make_unit

CAT = load_all()


def _u(tag, type_name="MARINE", owner=Owner.SELF, x=0.0, y=0.0):
    return make_unit(tag, type_name, owner, x, y, hp=45.0, hp_max=45.0)


def _gs(units):
    return make_gs(units, seq=0, game_time=0.0, minerals=50, vespene=0,
                   supply_used=0, supply_cap=20)


def test_fill_to_target_fcfs():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 1, "target": 2, "max": 4}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.count("G1") == 2  # 只补到 target，第三个留 free 池
    assert alloc.expand("G1", "terran/marine") == [1, 2]  # FCFS：按 gs.units 顺序取前 2


def test_sticky_lease_not_stolen():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 1}})
    alloc.create_group("G2", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # 先注册先拿（FCFS）
    assert alloc.expand("G2", "terran/marine") == [2, 3]  # 后注册只从 free 拿，不抢 G1
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # sticky：重复 refresh 不重分配


def test_death_pruning_and_refill():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2)]))
    assert alloc.count("G1") == 2
    alloc.refresh(_gs([_u(2)]))  # 1 死亡 → lease 清除
    assert alloc.expand("G1", "terran/marine") == [2]
    alloc.refresh(_gs([_u(2), _u(3)]))  # 新兵进 free 池 → 补到 target
    assert alloc.expand("G1", "terran/marine") == [2, 3]


def test_only_self_units_leased():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2, owner=Owner.ENEMY)]))
    assert alloc.expand("G1", "terran/marine") == [1]  # 敌方单位不租


def test_count_and_expand_unknown_group():
    alloc = Allocator(CAT)
    assert alloc.count("nope") == 0
    assert alloc.expand("nope", "terran/marine") == []
    assert alloc.expand_all("nope") == []


def test_empty_group():
    alloc = Allocator(CAT)
    alloc.create_group("G2", {"terran/marine": {"target": 1}})
    assert alloc.count("G2") == 0
    assert alloc.expand("G2", "terran/marine") == []  # 空 group：动作展开为空 → no-op
    assert alloc.expand_all("G2") == []


def test_expand_all_across_types():
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 1}, "terran/scv": {"target": 1}})
    alloc.refresh(_gs([_u(1, "MARINE"), _u(2, "SCV")]))
    assert alloc.expand_all("G1") == [1, 2]
    assert alloc.count("G1") == 2
    assert alloc.count("G1", "terran/marine") == 1
    assert alloc.count("G1", "terran/scv") == 1


def test_sieged_tank_still_leased_and_counted():
    """形态变体归一（T3）：坦克架起后实体名变 SIEGETANKSIEGED，仍被 lease/计数为
    terran/siegetank 组成员（单侧归一：stable id → 主名，单位名归一到主名后比较）。"""
    units = [_u(1, "SIEGETANK"), _u(2, "SIEGETANK"),
             _u(3, "SIEGETANKSIEGED"), _u(4, "SIEGETANKSIEGED")]
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/siegetank": {"target": 4}})
    alloc.refresh(_gs(units))
    assert alloc.count("G1", "terran/siegetank") == 4              # 4 辆全 lease（架起态归一到主名）
    assert sorted(alloc.expand("G1", "terran/siegetank")) == [1, 2, 3, 4]


def test_burnysc2_name_is_not_authoring_vocabulary():
    """T1/D1：authoring 侧只认 stable id —— 变体名/burnysc2 名查询不再被归一（词汇只剩一套）。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/siegetank": {"target": 2}})
    alloc.refresh(_gs([_u(1, "SIEGETANK"), _u(2, "SIEGETANKSIEGED")]))
    assert alloc.count("G1", "terran/siegetank") == 2
    assert alloc.expand("G1", "SIEGETANK") == []          # 主名也不是 authoring 词汇
    assert alloc.expand("G1", "SIEGETANKSIEGED") == []    # 变体名同理
    assert alloc.count("G1", "SIEGETANK") == 0


def test_catalog_required():
    """catalog 必传（D1）：composition 是 stable id，没 catalog 无法匹配 gs 实体名 → 构造期报错。"""
    import pytest
    with pytest.raises(ValueError, match="catalog"):
        Allocator(None)


def test_unknown_stable_id_in_composition_rejected():
    """未登记的 stable id（或误写 burnysc2 名）在 create_group 就报错，不静默漏 lease。"""
    import pytest
    alloc = Allocator(CAT)
    with pytest.raises(ValueError, match="stable id"):
        alloc.create_group("G1", {"MARINE": {"target": 1}})



# ---- S3 补兵滞回（T3/D6 + H2 边界）----


def test_refill_hysteresis_three_intervals():
    """滞回三区间：[min, target) 不补 / 跌破 min 补回 target / max 硬上限截断。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 3, "target": 5, "max": 5}})
    alloc.refresh(_gs([_u(i) for i in range(1, 8)]))
    assert alloc.count("G1", "terran/marine") == 5           # 首次补到 target
    # 死 1（剩 4，在 [3,5) 区间）→ 不补：不为一个兵去抢 free 池
    alloc.refresh(_gs([_u(i) for i in range(2, 8)]))
    assert alloc.count("G1", "terran/marine") == 4
    # 再死 1（剩 3，仍 >= min）→ 仍不补
    alloc.refresh(_gs([_u(i) for i in range(3, 8)]))
    assert alloc.count("G1", "terran/marine") == 3
    # 再死 1（剩 2，跌破 min）→ 补回 target
    alloc.refresh(_gs([_u(i) for i in range(4, 9)]))
    assert alloc.count("G1", "terran/marine") == 5


def test_max_caps_lease_size():
    """max 是硬上限：target 超过 max 时按 max 截断（编译期还会校验 target ≤ max）。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 1, "target": 5, "max": 2}})
    alloc.refresh(_gs([_u(i) for i in range(1, 8)]))
    assert alloc.count("G1", "terran/marine") == 2


def test_min_omitted_keeps_fill_to_target_behavior():
    """min 省略 → 下限 = target（跌破就补）：不给旧配置偷偷换语义。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"target": 2}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.count("G1", "terran/marine") == 2
    alloc.refresh(_gs([_u(2), _u(3)]))  # 死 1 → 立刻补回 2
    assert alloc.count("G1", "terran/marine") == 2


def test_min_zero_still_fills_empty_group():
    """H2：min=0 不能变成"永不补兵"（字面 0 会让首次都不填）→ 语义 = 只在空组时补。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 0, "target": 2, "max": 2}})
    alloc.refresh(_gs([_u(1), _u(2), _u(3)]))
    assert alloc.count("G1", "terran/marine") == 2      # 空组 → 补
    alloc.refresh(_gs([_u(2), _u(3)]))                  # 剩 1 > 0 → 不补
    assert alloc.count("G1", "terran/marine") == 1
    alloc.refresh(_gs([_u(3)]))                         # 组空了 → 再补
    assert alloc.count("G1", "terran/marine") == 1


# ---- I24 成长期/伤亡期区分（2026-08-25）----


def test_growth_phase_absorbs_past_min_to_target():
    """I24 根因回归：单兵营慢出兵（一次只多一个 free），group 不能一补到 min 就停。

    min=2 target=8：旧代码 group 补到 2 即停止吸收（滞回 floor=2），第 3 个起新兵
    全留 free 池、策略 `>= 8` 条件死锁。成长期 floor=target，兵一个一个来也一路吸到 8。
    """
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 2, "target": 8, "max": 20}})
    alive: list[int] = []
    for i in range(1, 9):
        alive.append(i)
        alloc.refresh(_gs([_u(t) for t in alive]))
        assert alloc.count("G1", "terran/marine") == i, f"第 {i} 个新兵必须入组（成长期）"


def test_hysteresis_only_after_reached_target():
    """I24：min 滞回只对「到过 target 后的伤亡」生效——补到 8、伤亡到 [2,8) 时
    free 池有同型闲兵也不抢（保留 S3 抗抖动原意）；跌破 2 才补回 8。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 2, "target": 8, "max": 20}})
    alloc.refresh(_gs([_u(i) for i in range(1, 9)]))
    assert alloc.count("G1", "terran/marine") == 8
    # 伤亡到 5（[2,8) 滞回区）→ free 池有闲兵也不补
    alloc.refresh(_gs([_u(i) for i in range(4, 9)] + [_u(20), _u(21)]))
    assert alloc.count("G1", "terran/marine") == 5
    # 跌破 min（剩 1）→ 补回 target
    alloc.refresh(_gs([_u(8)] + [_u(i) for i in range(10, 20)]))
    assert alloc.count("G1", "terran/marine") == 8


def test_refill_state_growth_vs_hysteresis_phase():
    """I24：同一 cur∈[min,target)，成长期显示「补兵中」（还在吸收），到过 target 后的
    伤亡才显示「滞回区」——observe 的状态不再把「等兵等不到」误标成「设计如此」。"""
    alloc = Allocator(CAT)
    alloc.create_group("G1", {"terran/marine": {"min": 2, "target": 4, "max": 4}})
    alloc.refresh(_gs([_u(1), _u(2)]))                 # 成长期 2/4：还在补
    snap = {c["group_id"]: c for c in alloc.snapshot()}["G1"]
    assert snap["refill_state"] == "补兵中"
    alloc.refresh(_gs([_u(i) for i in range(1, 5)]))   # 补到 target=4 → 进入伤亡期
    alloc.refresh(_gs([_u(3), _u(4)]))                 # 伤亡到 2/4（>= min）→ 滞回区
    snap = {c["group_id"]: c for c in alloc.snapshot()}["G1"]
    assert snap["refill_state"] == "滞回区"
