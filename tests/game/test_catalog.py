"""game catalog 测试：稳定 ID 映射 + 查询。"""
from game.catalog import Catalog, CatalogEntry, load_terran


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
    assert e.role == "combat"
    assert "attack" in e.capabilities
    assert e.cost == {"minerals": 50, "vespene": 0, "supply": 1}
    assert e.build_time == 18
    assert e.produced_by == "terran/barracks"
    assert "terran/barracks" in e.prerequisites


def test_where_role():
    cat = load_terran()
    workers = cat.where(role="worker")
    assert len(workers) == 1
    assert workers[0].stable_id == "terran/scv"
    buildings = cat.where(role="building")
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
        "burnysc2_name": "TEST", "display_name_zh": "测试", "role": "test",
        "capabilities": ["test"], "cost": {"minerals": 1}, "build_time": 1,
        "produced_by": None, "prerequisites": [],
    })
    e = cat.by_stable_id("terran/test")
    assert e is not None
    assert e.burnysc2_name == "TEST"
    assert cat.stable_id_for("TEST") == "terran/test"
