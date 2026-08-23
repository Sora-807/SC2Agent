"""策略文件（二十七轮用户拍板「开放写策略，免审」）。

守三件事：
1. 策略从此是**文件**（strategy+assembly 两段），与规划/地图规划同级的 authoring 载体
   —— 此前是 api.session 里两个写死的常量，agent/人都改不了（ISSUES I12「路径上够不着」）；
2. 保存过**全套编译期校验**（parse/validate：谓词签名/产槽/图可达性/环出口），
   错误带 step 定位返回 —— 免审 ≠ 免校验；
3. 会话按 id 装配：`POST /api/session/start?strategy=<id>`，default 锁定不可改。
"""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from api.app import create_app

VALID_DOC = {
    "strategy": {
        "id": "rush", "version": 1,
        "display_name_zh": "快攻", "description_zh": "攒 6 兵一波",
        "group_slots": [main_] if (main_ := "main") else [],
        "params": {"min_units": {"type": "int", "default": 6}},
        "variables": {},
        "initial_step": "wait",
        "steps": [
            {"step_id": "wait",
             "branches": [
                 {"when": {"op": ">=", "args": [
                     {"op": "group_count", "group": "main"}, {"param": "min_units"}]},
                  "do": [{"op": "exit_step", "kind": "done", "reason": "GO"}]},
                 {"do": []},
             ]},
            {"step_id": "go",
             "branches": [
                 {"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]},
             ]},
        ],
        "edges": [{"from": "wait", "to": "go", "kind": "done", "reason": "GO"}],
    },
    "assembly": {
        "id": "rush_asm",
        "groups": [{"group_id": "G1", "display_name_zh": "步兵组",
                    "composition": {"terran/marine": {"min": 4, "target": 6, "max": 8}}}],
        "strategy_instances": [
            {"instance_id": "s1", "strategy_ref": "rush",
             "bindings": {"main": "G1"}, "params": {}},
        ],
    },
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                 strategies_dir=tmp_path / "strategies"))


def test_default_seeded_locked_and_listed(client: TestClient):
    rows = client.get("/api/strategies").json()
    assert rows[0]["id"] == "default" and rows[0]["locked"] is True
    assert rows[0]["title_zh"]          # zh 名（内置策略已带 display_name_zh）


def test_create_save_roundtrip_and_compile_rejection(client: TestClient):
    assert client.post("/api/strategies", json={"id": "rush"}).status_code == 200
    # 好文档：保存过编译校验
    r = client.put("/api/strategies/rush/doc", json=VALID_DOC)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    doc = client.get("/api/strategies/rush/doc").json()
    assert doc["strategy"]["id"] == "rush" and doc["assembly"]["id"] == "rush_asm"
    # 坏文档：编译错误带定位返回（exit 的 reason 改掉 → 无匹配 edge），不落盘
    bad = yaml.safe_load(yaml.safe_dump(VALID_DOC))
    bad["strategy"]["steps"][0]["branches"][0]["do"][0]["reason"] = "NOPE"
    r2 = client.put("/api/strategies/rush/doc", json=bad)
    assert r2.status_code == 200 and r2.json()["ok"] is False
    assert any("NOPE" in e["text_zh"] for e in r2.json()["errors"])
    assert client.get("/api/strategies/rush/doc").json()["strategy"]["id"] == "rush"


def test_locked_default_refused_and_delete_guard(client: TestClient):
    r = client.put("/api/strategies/default/doc", json=VALID_DOC)
    assert r.status_code == 400 and "锁定" in r.json()["detail"]
    assert client.delete("/api/strategies/default").status_code == 400
    assert client.delete("/api/strategies/nope").status_code == 404


def test_session_assembles_custom_strategy(client: TestClient):
    """会话按 id 装配：static/strategy 的 id/中文名来自所选文件（不是内置常量）。"""
    client.post("/api/strategies", json={"id": "rush"})
    assert client.put("/api/strategies/rush/doc", json=VALID_DOC).json()["ok"]
    r = client.post("/api/session/start",
                    params={"driver": "offline", "strategy": "rush", "autotick": "false"})
    assert r.status_code == 200, r.text
    statics = client.get("/api/sources/live/statics").json()
    strat = next(s["payload"] for s in statics if s["topic"] == "static/strategy")
    assert strat["id"] == "rush" and strat["display_name_zh"] == "快攻"
    assert strat["group_names"] == {"G1": "步兵组"}
    # 不存在的策略 id：400 带原因（不是静默退回默认）
    r2 = client.post("/api/session/start",
                     params={"driver": "offline", "strategy": "nope", "autotick": "false"})
    assert r2.status_code == 400 and "nope" in r2.json()["detail"]
    client.post("/api/session/stop")


def test_copy_respects_lock_and_renames_inner_id(client: TestClient):
    client.post("/api/strategies", json={"id": "cp", "copy_from": "default"})
    doc = client.get("/api/strategies/cp/doc").json()
    assert doc["strategy"]["id"] == "cp"     # 内层 id 跟文件名走（转储里不残留旧名）
    assert client.put("/api/strategies/cp/doc", json={
        "strategy": doc["strategy"], "assembly": doc["assembly"]}).json()["ok"] is True


def test_lib_is_locked_and_not_a_strategy(client: TestClient, tmp_path: Path):
    """`_lib.yaml`（ADR-0031）：模板库不是策略 —— 清单里没有它；id 规则挡住
    `_` 前缀的建/存；但 `GET /api/strategies/_lib` 能读原文（store 会播种种子库）。"""
    rows = client.get("/api/strategies").json()
    assert all(not r["id"].startswith("_") for r in rows)
    r = client.get("/api/strategies/_lib")
    assert r.status_code == 200        # store 启动时播种了出厂种子
    assert "step_templates" in r.json()["text"]
    assert client.post("/api/strategies", json={"id": "_lib"}).status_code == 400
    assert client.put("/api/strategies/_lib/doc", json=VALID_DOC).status_code == 404


IMPORT_DOC = {
    "strategy": {
        "id": "lib_rush", "version": 1, "group_slots": ["main"],
        "params": {"min_units": {"type": "int", "default": 6}},
        "variables": {},
        "initial_step": "gather",
        # ADR-0031：imports 引用模板库 —— 键名即 step_id，绑定值可桥接策略级参数
        "imports": {"gather": {"from": "_lib", "template": "gather",
                               "params": {"min_units": {"param": "min_units"}}}},
        "steps": [{"step_id": "finish", "branches": [
            {"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]}]}],
        "edges": [{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}],
    },
    "assembly": {
        "id": "lib_rush_asm",
        "groups": [{"group_id": "G1", "composition":
                    {"terran/marine": {"min": 4, "target": 6, "max": 8}}}],
        "strategy_instances": [{"instance_id": "s1", "strategy_ref": "lib_rush",
                                "bindings": {"main": "G1"}, "params": {}}],
    },
}


def test_save_with_imports_needs_lib(tmp_path: Path):
    """带 imports 的策略在**内存态** store（无目录、无播种）里保存：编译红。"""
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl"))
    c.post("/api/strategies", json={"id": "lib_rush"})
    r = c.put("/api/strategies/lib_rush/doc", json=IMPORT_DOC).json()
    assert r["ok"] is False
    assert any("imports" in e["text_zh"] or "_lib" in e["text_zh"] for e in r["errors"])


def test_store_seeds_lib_and_imports_compile(tmp_path: Path):
    """出厂种子（modules/flow/data/_lib.yaml）在 store 建目录时播种成工作副本；
    带 imports 的策略保存通过（展开+全套校验在保存时跑）。"""
    from view.strategies import LIB_FILENAME, StrategyStore

    from api.session import DEFAULT_ASSEMBLY, DEFAULT_STRATEGY

    store = StrategyStore(tmp_path, seed=(DEFAULT_STRATEGY, DEFAULT_ASSEMBLY))
    assert (tmp_path / LIB_FILENAME).is_file(), "播种：runtime 副本缺失时从种子拷"
    assert "gather" in store.templates()
    store.create({"id": "lib_rush"})
    r = store.save_doc("lib_rush", IMPORT_DOC)
    assert r["ok"] is True, r["errors"]
    m_doc = store.doc("lib_rush")
    assert "imports" in m_doc["strategy"]      # 转储保留 imports（编辑还在源形态）
