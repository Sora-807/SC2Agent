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
    assert len(buildings) == 3  # CC + SupplyDepot + Barracks
    stable_ids = {e.stable_id for e in buildings}
    assert "terran/commandcenter" in stable_ids
    assert "terran/supplydepot" in stable_ids
    assert "terran/barracks" in stable_ids


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

