"""地图规划文件存储（P2：规划 = 某地图 × 某方的一种布局）。

守五件事：
1. 四个锁定预设自愈存在：空白×蓝/红（无自建槽位）+ 出厂校准×蓝/红 —— 复制是唯一改动路径；
2. payload = static/map 形状 + 真地形 + 全图资源 + 预设名预留区；蓝/红坐标各自正确；
3. 保存走 hunks + 与 map_plan 提案同一套校验（重叠/重名 400 结构化理由）+ 固定建造点预留；
4. 会话装配接规划文件（进入游戏加载哪一份）+ 沙盒发真地形；
5. 文件落盘可重开（yaml 往返）。
"""
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(
        tmp_path, plans_dir=tmp_path / "plans", map_plans_dir=tmp_path / "map-plans"))


def _copy(client: TestClient, src: str = "layout-bl", pid: str = "mine") -> str:
    r = client.post("/api/map-plans", json={"id": pid, "title_zh": "我的布局",
                                            "copy_from": src})
    assert r.status_code == 200, r.text
    return pid


# ---------------- 预设（锁定 + 自愈） ----------------

def test_presets_exist_locked_and_first(client: TestClient):
    rows = client.get("/api/map-plans").json()
    locked = [r for r in rows if r["locked"]]
    assert {r["id"] for r in locked} == {"default-bl", "default-tr",
                                         "layout-bl", "layout-tr"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["default-bl"]["slots"] == 0, "空白默认 = 去掉所有自建槽位（用户拍板）"
    assert by_id["default-tr"]["slots"] == 0
    assert by_id["layout-bl"]["slots"] > 0, "出厂校准布局保留全部槽位"
    assert by_id["layout-bl"]["spawn"] == "bl" and by_id["layout-tr"]["spawn"] == "tr"
    # 锁定不可写不可删
    for pid in ("default-bl", "layout-tr"):
        r = client.put(f"/api/map-plans/{pid}", json={"hunks": []})
        assert r.status_code == 400 and "锁定" in r.json()["detail"]
        assert client.delete(f"/api/map-plans/{pid}").status_code == 400


# ---------------- payload（static/map 形状） ----------------

def test_payload_shape_and_side_coords(client: TestClient):
    p = client.get("/api/map-plans/layout-bl").json()
    assert p["map_name"] == "LadderMap" and p["spawn"] == "bl"
    names = {s["name"] for s in p["build_slots"]}
    assert {"D1", "R1", "F1"} <= names

    def pos_of(pid, name):
        pl = client.get(f"/api/map-plans/{pid}").json()
        return next(s["build_point"] for s in pl["build_slots"] if s["name"] == name)

    assert pos_of("layout-bl", "D1") != pos_of("layout-tr", "D1"), \
        "蓝/红是各自的世界坐标"

    empty = client.get("/api/map-plans/default-bl").json()
    assert empty["build_slots"] == [], "空白默认没有槽位（地形/资源/预留照常下发）"


def test_payload_carries_fullmap_terrain_and_resources(client: TestClient):
    """真机采集的数据文件直达规划页：全图地形 + 全图资源点（无战争迷雾限制）。"""
    p = client.get("/api/map-plans/default-bl").json()
    assert p["terrain"] is not None and p["terrain"]["height"]["data_b64"]
    mins = [r for r in p["resource_nodes"] if r["kind"] == "mineral"]
    assert len(mins) > 50, f"全图矿脉，实测 98，拿到 {len(mins)}"
    xs = [r["pos"][0] for r in mins]
    assert max(xs) - min(xs) > 100, "矿脉横跨全图（不是单个基地的局部数据）"


def test_payload_carries_reserved_boxes_with_names(client: TestClient):
    """预留区带预设名（蓝方主矿/二矿…红方…，气井归属基地）。"""
    p = client.get("/api/map-plans/default-bl").json()
    kinds = {r["kind"] for r in p["reserved"]}
    assert kinds == {"base", "geyser", "mineral"}
    bases = [r for r in p["reserved"] if r["kind"] == "base"]
    assert len(bases) == 12, "真机采集的 12 个基地（含 bl/tr 主基）"
    names = [r["name"] for r in p["reserved"] if r.get("name")]
    assert "蓝方主矿" in names and "红方主矿" in names
    assert any(n == "蓝方二矿" for n in names), names
    assert any(n == "蓝方主矿气井1" for n in names)
    assert len(names) == 36, "12 基地 + 24 气井全命名"


# ---------------- 保存（hunks + 校验 + 预留） ----------------

def test_save_adds_slot_and_mark(client: TestClient):
    pid = _copy(client)
    r = client.put(f"/api/map-plans/{pid}", json={"hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "R9", "pos": [70.5, 50.5], "size": 3, "kind": "production"}},
        {"id": "h2", "kind": "add_mark", "payload": {"name": "rally", "pos": [60.0, 55.0]}},
    ]})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    p = client.get(f"/api/map-plans/{pid}").json()
    assert any(s["name"] == "R9" for s in p["build_slots"])
    assert any(m["name"] == "rally" for m in p["pos_marks"])


def test_save_rejects_overlap_with_structured_reason(client: TestClient):
    pid = _copy(client)   # layout-bl 有 D1 @ (40.5,32.5)
    r = client.put(f"/api/map-plans/{pid}", json={"hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "D17", "pos": [40.5, 32.5], "size": 2, "kind": "supply"}},
    ]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["ok"] is False
    assert any("重叠" in e["text_zh"] for e in detail["errors"])


def test_save_rejects_slot_on_base_or_geyser(client: TestClient):
    """固定建造点不可占用（placeable 栅格在矿/井位置是 1，预留只能靠采集数据）。"""
    pid = _copy(client, src="default-bl")
    p = client.get(f"/api/map-plans/default-bl").json()
    base = next(r for r in p["reserved"] if r["kind"] == "base"
                and abs((r["tl"][0] + r["br"][0]) / 2 - 48.5) < 3)   # bl 主基
    cx = (base["tl"][0] + base["br"][0]) / 2
    cy = (base["tl"][1] + base["br"][1]) / 2
    r = client.put(f"/api/map-plans/{pid}", json={"hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "R9", "pos": [cx, cy], "size": 3, "kind": "production"}},
    ]})
    assert r.status_code == 400
    assert any("固定建造点" in e["text_zh"] for e in r.json()["detail"]["errors"])


def test_del_touches_only_this_plan(client: TestClient):
    """单出生点模型：del 只影响本规划（没有跨分支同步）。"""
    pid = _copy(client)
    r = client.put(f"/api/map-plans/{pid}", json={"hunks": [
        {"id": "h1", "kind": "del_slot", "payload": {"name": "D16"}},
    ]})
    assert r.status_code == 200
    p = client.get(f"/api/map-plans/{pid}").json()
    assert not any(s["name"] == "D16" for s in p["build_slots"])
    # 预设不受影响
    preset = client.get("/api/map-plans/layout-bl").json()
    assert any(s["name"] == "D16" for s in preset["build_slots"])


# ---------------- 文件与生命周期 ----------------

def test_map_plan_files_survive_restart(tmp_path):
    c1 = TestClient(create_app(tmp_path, plans_dir=tmp_path / "plans",
                               map_plans_dir=tmp_path / "map-plans"))
    c1.post("/api/map-plans", json={"id": "keep", "copy_from": "default-tr"})
    c1.put("/api/map-plans/keep", json={"hunks": [
        {"id": "h1", "kind": "add_mark", "payload": {"name": "m1", "pos": [5.0, 5.0]}}]})
    c2 = TestClient(create_app(tmp_path, plans_dir=tmp_path / "plans2",
                               map_plans_dir=tmp_path / "map-plans"))
    p = c2.get("/api/map-plans/keep").json()
    assert any(m["name"] == "m1" for m in p["pos_marks"])
    assert p["spawn"] == "tr", "规划自带出生点"
    assert c2.get("/api/map-plans").json()[0]["id"] == "default-bl"


def test_copy_delete_roundtrip(client: TestClient):
    pid = _copy(client)
    assert client.delete(f"/api/map-plans/{pid}").status_code == 200
    assert client.get(f"/api/map-plans/{pid}").status_code == 404


# ---------------- 会话装配（进入游戏加载哪一份） ----------------

def test_sim_session_assembles_from_map_plan_and_emits_terrain(client: TestClient):
    """沙盒会话带 map_plan 启动：装配用该规划文件，且 statics 里有真地形。"""
    r = client.post("/api/session/start",
                    params={"driver": "sim", "autotick": "false", "map_plan": "layout-bl"})
    assert r.status_code == 200
    info = r.json()
    assert "layout-bl" in info["label"], info["label"]
    for _ in range(40):
        desc = client.get("/api/session").json()
        if desc.get("frames", 0) > 3:
            break
        time.sleep(0.25)
    statics = client.get("/api/sources/live/statics").json()
    topics = [f["topic"] for f in statics]
    assert "static/terrain" in topics
    terr = next(f for f in statics if f["topic"] == "static/terrain")["payload"]
    assert terr["height"] is not None and terr["height"]["data_b64"], "真机采集的地形"
    m = next(f for f in statics if f["topic"] == "static/map")["payload"]
    names = {s["name"] for s in m["build_slots"]}
    assert {"D1", "R1", "F1"} <= names, "装配来自出厂校准布局"
    client.post("/api/session/stop")


def test_session_rejects_unknown_map_plan(client: TestClient):
    r = client.post("/api/session/start",
                    params={"driver": "sim", "map_plan": "nope"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_slot_short_name_convention_enforced(tmp_path):
    """rev 14：槽位名必须简写（D/R/F/S+序[+]）—— 编辑与全量保存两道门都拒旧式全称。"""
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                              map_plans_dir=tmp_path / "map-plans"))
    c.post("/api/map-plans", json={"id": "conv-probe", "copy_from": "layout-bl"})
    # add_slot 旧式全称 → 结构化拒绝
    r = c.put("/api/map-plans/conv-probe", json={"hunks": [
        {"id": "h0", "kind": "add_slot",
         "payload": {"name": "depot17", "pos": [30.5, 26.5],
                     "size": 2, "kind": "supply"}}]})
    assert r.status_code == 400 and "简写约定" in str(r.json()["detail"])
    # 全量保存（agent 文件工作区路径）同拒；约定名 + 中文别名则过
    doc = c.get("/api/map-plans/conv-probe/doc").json()
    doc["build_slots"]["barracks9"] = {"pos": [60.5, 44.5], "size": 3, "kind": "production"}
    r = c.put("/api/map-plans/conv-probe/doc", json=doc)
    assert r.status_code == 400 and "简写约定" in str(r.json()["detail"])
    doc["build_slots"].pop("barracks9")
    doc["build_slots"]["R5"] = {"pos": [60.5, 44.5], "size": 3, "kind": "production",
                                "alias_zh": "兵营5"}
    r = c.put("/api/map-plans/conv-probe/doc", json=doc)
    assert r.status_code == 200, r.text
    slots = c.get("/api/map-plans/conv-probe").json()["build_slots"]
    assert slots and all(n and n[0] in "DRFS" for n in
                         (s["name"] for s in slots))
    assert any(s["name"] == "R5" and s.get("alias_zh") == "兵营5" for s in slots)
