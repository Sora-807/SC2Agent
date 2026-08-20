"""game catalog 测试：稳定 ID 映射 + 查询 + 加载边界校验（Role/Cost/词表）。"""
import pytest

from game.catalog import Catalog, CatalogEntry, Cost, Role, load_terran


def test_load_terran():
    cat = load_terran()
    assert cat.by_stable_id("terran/marine") is not None
    assert cat.by_burnysc2_name("MARINE") is not None


def test_stable_id_mapping():
    cat = load_terran()
    assert cat.burnysc2_name_for("terran/marine") == "MARINE"
    assert cat.stable_id_for("MARINE") == "terran/marine"
    assert cat.burnysc2_name_for("terran/scv") == "SCV"
    assert cat.stable_id_for("SUPPLYDEPOT") == "terran/supplydepot"


def test_entry_fields():
    cat = load_terran()
    e = cat.by_stable_id("terran/marine")
    assert e.burnysc2_name == "MARINE"
    assert e.display_name_zh == "机枪兵"
    assert e.role is Role.COMBAT
    assert "attack" in e.capabilities
    assert e.cost == Cost(minerals=50, vespene=0, supply=1)
    assert e.cost.minerals == 50 and e.cost.vespene == 0 and e.cost.supply == 1
    assert e.build_time == 18
    assert e.produced_by == "terran/barracks"
    assert "terran/barracks" in e.prerequisites


def test_where_role():
    cat = load_terran()
    workers = cat.where(role="worker")  # 字符串查询也归一化到 Role
    assert len(workers) == 1
    assert workers[0].stable_id == "terran/scv"
    assert workers[0].role is Role.WORKER
    buildings = cat.where(role=Role.BUILDING)
    assert len(buildings) == 8  # CC + SupplyDepot + Barracks + Reactor + TechLab + Refinery + Factory + FactoryTechLab
    stable_ids = {e.stable_id for e in buildings}
    assert "terran/commandcenter" in stable_ids
    assert "terran/supplydepot" in stable_ids
    assert "terran/barracks" in stable_ids
    assert "terran/factory" in stable_ids
    assert "terran/factorytechlab" in stable_ids


def test_where_capability():
    cat = load_terran()
    trainers = cat.where(capability="train")
    stable_ids = {e.stable_id for e in trainers}
    assert "terran/commandcenter" in stable_ids
    assert "terran/barracks" in stable_ids


def test_unknown_returns_none():
    cat = load_terran()
    assert cat.by_stable_id("terran/ghost") is None
    assert cat.by_burnysc2_name("GHOST") is None
    assert cat.burnysc2_name_for("terran/ghost") is None
    assert cat.stable_id_for("GHOST") is None


def test_register_custom():
    cat = Catalog()
    cat.register("terran/test", {
        "burnysc2_name": "TEST", "display_name_zh": "测试", "role": "worker",
        "capabilities": ["gather"], "cost": {"minerals": 1, "vespene": 0, "supply": 0},
        "build_time": 1, "produced_by": None, "prerequisites": [],
    })
    e = cat.by_stable_id("terran/test")
    assert e is not None
    assert e.burnysc2_name == "TEST"
    assert e.role is Role.WORKER
    assert e.cost == Cost(minerals=1, vespene=0, supply=0)
    assert cat.stable_id_for("TEST") == "terran/test"


def test_addon_entry_fields():
    """挂件条目（真机锁定）：实体类型 = 父建筑专属（BARRACKSREACTOR），
    建造走通用能力名 + 订单按钮名（与实体类型名不同）。"""
    cat = load_terran()
    r = cat.by_stable_id("terran/reactor")
    assert r is not None
    assert r.burnysc2_name == "BARRACKSREACTOR"  # 游戏里产出的实体是这个类型
    assert r.build_ability == "BUILD_REACTOR"      # 建造命令是通用能力（per-parent 无实体产出）
    assert r.build_order_name == "Reactor"         # 母建筑订单按钮名（在途确认检测用）
    assert r.build_time == 36  # 反应堆真实建造时间（真机实测订单常驻 ~36 游戏秒）


def test_factory_and_factorytechlab_entries():
    """工厂生产链：factory（train）+ factorytechlab（addon，父建筑=工厂）。
    镜像 barracks/techlab 结构，只换父建筑与父专属实体名。"""
    cat = load_terran()
    f = cat.by_stable_id("terran/factory")
    assert f is not None
    assert f.burnysc2_name == "FACTORY"
    assert f.role is Role.BUILDING
    assert "train" in f.capabilities and f.size == 3
    assert f.cost == Cost(minerals=150, vespene=100, supply=0)
    assert f.build_time == 43
    assert f.produced_by is None  # SCV 建造（同 barracks/supplydepot 约定）
    assert "terran/barracks" in f.prerequisites
    ft = cat.by_stable_id("terran/factorytechlab")
    assert ft is not None
    assert ft.burnysc2_name == "FACTORYTECHLAB"  # 父专属实体名（非通用 FACTORYTECHLAB_*）
    assert "addon" in ft.capabilities and ft.size == 2
    assert ft.build_ability == "BUILD_TECHLAB"    # 通用能力（同 barracks techlab）
    assert ft.build_order_name == "Techlab"
    assert ft.produced_by == "terran/factory"


def test_siegetank_entry_fields():
    """攻城坦克：双射程 + 形态变体（架起后实体变 SIEGETANKSIEGED，T3 归一化用 variants 反查主名）。
    attack_range/siege_range 为 SC2 标准占位值，T6 真机实测锁定。"""
    cat = load_terran()
    e = cat.by_stable_id("terran/siegetank")
    assert e is not None
    assert e.burnysc2_name == "SIEGETANK"
    assert e.role is Role.COMBAT
    assert "attack" in e.capabilities and "move" in e.capabilities
    assert e.cost == Cost(minerals=150, vespene=125, supply=2)
    assert e.build_time == 32
    assert e.produced_by == "terran/factory"
    assert "terran/factorytechlab" in e.prerequisites
    assert e.attack_range == 5    # 未架起地面射程（SC2 标准；T6 真机锁定）
    assert e.siege_range == 13    # 架起射程（架起门用 0.8×13=10.4）
    assert e.variants == ("SIEGETANKSIEGED",)


def test_register_rejects_negative_range():
    """射程负数 = 坏数据 → 加载当场报错（R7 fail-fast，不静默带病运行）。"""
    with pytest.raises(ValueError, match="attack_range"):
        Catalog().register("terran/test", _bad({"attack_range": -1}))
    with pytest.raises(ValueError, match="siege_range"):
        Catalog().register("terran/test", _bad({"siege_range": -0.5}))


def test_variants_normalized_to_tuple():
    """variants 从 JSON list 归一化成 tuple（不可变，hashable；T3 反查用）。"""
    cat = Catalog()
    cat.register("terran/test", _bad({"variants": ["SIEGETANKSIEGED", "OTHER"]}))
    e = cat.by_stable_id("terran/test")
    assert isinstance(e.variants, tuple)
    assert e.variants == ("SIEGETANKSIEGED", "OTHER")


def test_normalize_burnysc2_name():
    """变体归一反向索引（T3）：变体名 → 主名；主名/未知名原样返回（宽容，不报错）。"""
    cat = load_terran()
    assert cat.normalize_burnysc2_name("SIEGETANKSIEGED") == "SIEGETANK"  # 变体→主名
    assert cat.normalize_burnysc2_name("SIEGETANK") == "SIEGETANK"          # 主名透传
    assert cat.normalize_burnysc2_name("MARINE") == "MARINE"                # 非变体透传
    assert cat.normalize_burnysc2_name("UNKNOWN") == "UNKNOWN"             # 未知名透传


def test_register_rejects_addon_without_build_ability():
    """addon 挂件缺通用建造能力名 = 无法下发的坏数据 → 加载当场报错。"""
    with pytest.raises(ValueError, match="build_ability"):
        Catalog().register("terran/test", _bad({"capabilities": ["addon"], "size": 2}))


# ---- 加载边界校验（非法数据当场报错，R7 降级告警不静默）----


def _bad(data_patch: dict) -> dict:
    data = {
        "burnysc2_name": "TEST", "role": "worker", "capabilities": ["gather"],
        "cost": {"minerals": 1, "vespene": 0, "supply": 0}, "build_time": 1,
    }
    data.update(data_patch)
    return data


def test_register_rejects_bad_stable_id():
    with pytest.raises(ValueError, match="stable_id"):
        Catalog().register("marine", _bad({}))            # 缺 race/name 两段
    with pytest.raises(ValueError, match="stable_id"):
        Catalog().register("warp/marine", _bad({}))       # 未知族前缀


def test_register_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        Catalog().register("terran/test", _bad({"role": "flyer"}))


def test_register_rejects_unknown_capability():
    with pytest.raises(ValueError, match="capability"):
        Catalog().register("terran/test", _bad({"capabilities": ["gather", "fly"]}))


def test_register_rejects_incomplete_cost():
    with pytest.raises(ValueError, match="cost"):
        Catalog().register("terran/test", _bad({"cost": {"minerals": 1}}))  # 缺 vespene/supply
    with pytest.raises(ValueError, match="cost"):
        Catalog().register("terran/test", _bad({"cost": "free"}))


def test_register_rejects_missing_build_time():
    data = _bad({})
    del data["build_time"]
    with pytest.raises(ValueError, match="build_time"):
        Catalog().register("terran/test", data)


def test_register_rejects_duplicate_stable_id_and_name():
    cat = Catalog()
    cat.register("terran/test", _bad({}))
    with pytest.raises(ValueError, match="重复"):
        cat.register("terran/test", _bad({"burnysc2_name": "TEST2"}))
    with pytest.raises(ValueError, match="占用"):
        cat.register("terran/other", _bad({}))  # burnysc2_name "TEST" 已被占

