"""PLAN-V2 批 2 API 行为锁：双分支规划 + 会话合并图层 + 默认热切 + null=auto。

（与 test_map_plans.py 的分工：那边锁文件面基本行为，这边锁批 2 的新语义。）
"""
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(
        tmp_path, plans_dir=tmp_path / "plans", map_plans_dir=tmp_path / "map-plans"))


def _copy(client, pid="mine", src="layout"):
    r = client.post("/api/map-plans", json={"id": pid, "title_zh": "我的布局",
                                            "copy_from": src})
    assert r.status_code == 200, r.text
    return pid


# ---------------- 双分支预设与 payload ----------------

def test_dual_presets_replace_legacy_four(client):
    rows = client.get("/api/map-plans").json()
    locked = {r["id"] for r in rows if r["locked"]}
    assert locked == {"default", "layout"}, "双分支预设两件取代单分支四件"
    by_id = {r["id"]: r for r in rows}
    assert by_id["default"]["spawn"] == "dual" and by_id["default"]["slots"] == 0
    assert by_id["layout"]["spawn"] == "dual" and by_id["layout"]["slots"] > 0
    assert by_id["layout"]["spawns"] == ["bl", "tr"]


def test_payload_spawn_selects_branch(client):
    p_bl = client.get("/api/map-plans/layout", params={"spawn": "bl"}).json()
    p_tr = client.get("/api/map-plans/layout", params={"spawn": "tr"}).json()
    assert p_bl["spawn"] == "bl" and p_tr["spawn"] == "tr"

    def pos(payload, name):
        return next(s["build_point"] for s in payload["build_slots"] if s["name"] == name)

    assert pos(p_bl, "D1") != pos(p_tr, "D1"), "蓝/红分支各自的世界坐标"
    # 缺省 spawn = bl（不是 400 —— payload 是画布读的，页签总会给但要能兜底）
    assert client.get("/api/map-plans/layout").json()["spawn"] == "bl"


def test_save_hunks_target_one_branch_only(client):
    pid = _copy(client)
    pos = _free_pos(client, client.get("/api/map-plans/layout", params={"spawn": "tr"}).json())
    r = client.put(f"/api/map-plans/{pid}", json={"spawn": "tr", "hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "D99", "pos": pos, "size": 2, "kind": "supply"}}]})
    assert r.status_code == 200, r.text
    tr = client.get(f"/api/map-plans/{pid}", params={"spawn": "tr"}).json()
    bl = client.get(f"/api/map-plans/{pid}", params={"spawn": "bl"}).json()
    assert any(s["name"] == "D99" for s in tr["build_slots"])
    assert not any(s["name"] == "D99" for s in bl["build_slots"]), "hunks 只动指定分支"
    # 双分支保存缺 spawn = 400（改哪一侧必须说清）
    r = client.put(f"/api/map-plans/{pid}", json={"hunks": []})
    assert r.status_code == 400 and "spawn" in r.json()["detail"]


def test_doc_dual_shape_roundtrip(client):
    pid = _copy(client, src="default")
    doc = client.get(f"/api/map-plans/{pid}/doc").json()
    assert set(doc["spawns"]) == {"bl", "tr"}
    doc["spawns"]["bl"]["build_slots"]["D7"] = {
        "pos": [44.5, 30.5], "size": 2, "kind": "supply", "alias_zh": "补给7"}
    r = client.put(f"/api/map-plans/{pid}/doc", json=doc)
    assert r.status_code == 200, r.text
    back = client.get(f"/api/map-plans/{pid}/doc").json()
    assert "D7" in back["spawns"]["bl"]["build_slots"]
    assert "D7" not in back["spawns"]["tr"]["build_slots"]


# ---------------- 会话装配：合并图层 + 热切 + null=auto ----------------

def _start_sim(client, map_plan=None):
    params = {"driver": "sim", "autotick": "false"}
    if map_plan:
        params["map_plan"] = map_plan
    r = client.post("/api/session/start", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _static_map(client):
    for _ in range(40):
        client.post("/api/session/tick", params={"count": 1})
        statics = client.get("/api/sources/live/statics").json()
        m = next((f for f in statics if f["topic"] == "static/map"), None)
        if m:
            return m["payload"]
        time.sleep(0.05)
    raise AssertionError("static/map 没到")


def test_session_layer_merges_namespaced_keys(client):
    """会话图层 = 默认裸名 + 其余规划命名空间键（一份图层三种解析）。"""
    _copy(client, pid="mine", src="layout")
    _start_sim(client, map_plan="mine")
    m = _static_map(client)
    names = {s["name"] for s in m["build_slots"]}
    assert "D1" in names, "默认规划 = 裸名（自动放置消费面）"
    assert "layout/D1" in names, "其余规划 = 命名空间键（显式引用）"
    client.post("/api/session/stop")


def _free_pos(client, payload, name="D1"):
    """从既有槽位向 y+ 找第一个不压预留/既有槽的 2×2 空位（测试夹具用）。"""
    taken = [tuple(s["tl"]) for s in payload["build_slots"]] + [
        (r["tl"][0], r["tl"][1]) for r in payload["reserved"]]
    ref = next(s for s in payload["build_slots"] if s["name"] == name)
    for dy in range(2, 40, 2):
        cand = (ref["tl"][0], ref["tl"][1] + dy)
        if not any(abs(cand[0] - t[0]) < 6 and abs(cand[1] - t[1]) < 6 for t in taken):
            return [cand[0] + 0.5, cand[1] + 0.5]
    raise AssertionError("测试夹具找不到空位（预期不会发生）")


def test_map_plan_hot_swap_reemits_static_map_and_keeps_namespaced(client):
    """默认热切：帧边界换默认份 + 重发 static/map；命名空间键不受影响。"""
    _copy(client, pid="a", src="layout")
    # b = 空白副本加一个独有槽位（换默认后裸名 D8 应出现）
    _copy(client, pid="b", src="default")
    pos = _free_pos(client, client.get("/api/map-plans/layout", params={"spawn": "bl"}).json())
    r = client.put("/api/map-plans/b", json={"spawn": "bl", "hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "D8", "pos": pos, "size": 2, "kind": "supply"}}]})
    assert r.status_code == 200, r.text
    _start_sim(client, map_plan="a")

    r = client.post("/api/session/map-plan", params={"id": "b"})
    assert r.status_code == 200 and r.json()["swap"]["map_plan"] == "b"
    m = _static_map(client)
    names = {s["name"] for s in m["build_slots"]}
    assert "D8" in names, "新默认的裸名槽位进 home 区"
    assert "layout/D1" in names or "a/D1" in names, "命名空间键不随默认换丢"
    # 未知规划 → 400，会话不受影响
    assert client.post("/api/session/map-plan", params={"id": "nope"}).status_code == 400
    client.post("/api/session/stop")


def test_placement_null_is_auto_on_default_layer(client):
    """ADR-0027 修订：placement null 的 build = 自动放置（默认图层按序空位）。"""
    _copy(client, pid="auto1", src="default")
    r = client.put("/api/map-plans/auto1", json={"spawn": "bl", "hunks": [
        {"id": "h1", "kind": "add_slot",
         "payload": {"name": "D3", "pos": [42.5, 28.5], "size": 2, "kind": "supply"}}]})
    assert r.status_code == 200, r.text
    _start_sim(client, map_plan="auto1")
    seq = client.get("/api/session").json()["seq"]
    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": seq, "name": "main",
        "items": [{"op": "build", "type": "terran/supplydepot", "count": 1}]})  # 无 placement
    assert r.status_code == 200, r.text
    for _ in range(40):
        client.post("/api/session/tick", params={"count": 1})
        frames = client.get("/api/sources/live/frames",
                            params={"game_time": 999999, "topics": "frame/production"}).json()
        prod = next((f["payload"] for f in frames
                     if f["topic"] == "frame/production"), None)
        if prod and prod.get("in_flight"):
            break
        time.sleep(0.05)
    assert prod and prod["in_flight"], "null placement 应按默认图层自动放置发单（不是 dropped）"
    dropped = prod.get("dropped") or []
    assert not any("缺 placement" in d["reason"] for d in dropped)
    client.post("/api/session/stop")


def test_namespaced_placement_ref_passes_through_to_runtime(client):
    """`规划id/点位名` 引用直通（跨规划拒绝逻辑已删）—— 命名空间键在图层里解析。"""
    _copy(client, pid="refsrc", src="layout")   # 非默认
    _start_sim(client, map_plan="default")
    seq = client.get("/api/session").json()["seq"]
    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": seq, "name": "main",
        "items": [{"op": "build", "type": "terran/supplydepot", "count": 1,
                   "placement": {"kind": "exact", "mark": "refsrc/D1"}}]})
    assert r.status_code == 200, r.text   # REST 层不再拒绝（此前跨规划引用 400）
    for _ in range(40):
        client.post("/api/session/tick", params={"count": 1})
        frames = client.get("/api/sources/live/frames",
                            params={"game_time": 999999, "topics": "frame/production"}).json()
        prod = next((f["payload"] for f in frames
                     if f["topic"] == "frame/production"), None)
        if prod and prod.get("in_flight"):
            break
        time.sleep(0.05)
    assert prod and prod["in_flight"], "命名空间引用应在合并图层里解析并发出建造单"
    client.post("/api/session/stop")
