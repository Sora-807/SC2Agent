"""simulate v2 / initial-states / export（PLAN-V2 批 3）API 行为锁。

- initial-states CRUD + 校验（catalog/工人分项/supply_cap 对账）；
- simulate 新参数：initial_state（引用/内联）、sample_interval/sample_start、
  horizon=0 静态体检（audit 合并路径）；
- export：离线会话导出 initial_state + 剩余队列（uid/status）→ simulate 复用。
"""
import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(
        tmp_path, plans_dir=tmp_path / "plans", map_plans_dir=tmp_path / "mp",
        initial_states_dir=tmp_path / "is"))


MIDGAME = {
    "title_zh": "中期双兵营",
    "minerals": 500, "gas": 150, "supply_used": 17, "supply_cap": 21,
    "workers": {"mineral": 12, "gas": 3, "building": 0, "scouting": 0, "idle": 0},
    "buildings": {"terran/commandcenter": 1, "terran/supplydepot": 1,
                  "terran/barracks": 2},
    "units": {"terran/marine": 2},
    "upgrades": [],
}


# ---------------- initial-states CRUD ----------------

def test_seed_locked_and_crud_roundtrip(client):
    rows = client.get("/api/initial-states").json()
    assert rows and rows[0]["id"] == "example-midgame" and rows[0]["locked"]
    r = client.put("/api/initial-states/mid1", json=MIDGAME)
    assert r.status_code == 200, r.text
    doc = client.get("/api/initial-states/mid1").json()
    assert doc["buildings"]["terran/barracks"] == 2
    # 校验拒：catalog 不认 / supply_cap 对不上
    bad = {**MIDGAME, "buildings": {**MIDGAME["buildings"], "terran/nope": 1}}
    r = client.put("/api/initial-states/bad1", json=bad)
    assert r.status_code == 400 and "terran/nope" in str(r.json()["detail"])
    bad2 = {**MIDGAME, "supply_cap": 99}
    r = client.put("/api/initial-states/bad2", json=bad2)
    assert r.status_code == 400 and "supply_cap" in str(r.json()["detail"])
    # 锁定件拒写拒删
    assert client.put("/api/initial-states/example-midgame", json=MIDGAME).status_code == 400
    assert client.delete("/api/initial-states/mid1").status_code == 200


# ---------------- simulate v2 参数 ----------------

QUEUE = [
    {"op": "build", "type": "terran/supplydepot", "count": 1},
    {"op": "build", "type": "terran/barracks", "count": 1},
    {"op": "train", "type": "terran/marine", "count": 4},
]


def test_simulate_with_initial_state_ref_and_inline(client):
    client.put("/api/initial-states/mid1", json=MIDGAME)
    # 引用：从中期状态起跑 —— 兵营已在场，marine 直接可训
    r = client.post("/api/plans/simulate", json={
        "initial_state": "mid1", "horizon": 60,
        "queue": [{"op": "train", "type": "terran/marine", "count": 2}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["queue_status"] and out["queue_status"][0]["status"] == "completed"
    assert out["final"]["buildings"].get("terran/barracks") == 2
    # 内联：同文档内联传，结果一致
    r2 = client.post("/api/plans/simulate", json={
        "initial_state": MIDGAME, "horizon": 60,
        "queue": [{"op": "train", "type": "terran/marine", "count": 2}]})
    assert r2.status_code == 200
    assert r2.json()["final"]["units"].get("terran/marine", 0) == 4, "初始 2 + 训成 2"
    # 未知引用 → 404 带可用清单
    r3 = client.post("/api/plans/simulate", json={
        "initial_state": "ghost", "horizon": 10, "queue": QUEUE[:1]})
    assert r3.status_code == 404 and "ghost" in r3.json()["detail"]


def test_simulate_sampling_window_and_sections(client):
    r = client.post("/api/plans/simulate", json={
        "queue": QUEUE, "horizon": 60, "sample_interval": 20, "sample_start": 20})
    out = r.json()
    ts = [s["t"] for s in out["samples"]]
    assert ts == [20, 40, 60], f"sample_start 之后每 20s 一行：{ts}"
    s0 = out["samples"][0]
    assert set(s0["workers"]) == {"mineral", "gas", "building", "scouting", "idle"}
    fin = out["final"]
    assert fin["workers"]["scouting"] == 0 and "production_detail" in fin
    assert out["queue_status"][0]["uid"] == "q01", "草稿项自动分配显示 uid"


def test_simulate_horizon_zero_static_check(client):
    """horizon=0 = 静态体检（audit 路径，D2）：不跑投影，前置/产出/人口对账。"""
    r = client.post("/api/plans/simulate", json={
        "horizon": 0,
        "queue": [{"op": "train", "type": "terran/siegetank", "count": 1}]})
    assert r.status_code == 200
    out = r.json()
    assert out["static"] is True
    kinds = {a["kind"] for a in out["alerts"]}
    assert "prereq_missing" in kinds, "坦克没工厂/挂件：前置缺口要报"
    assert out["queue_status"][0]["status"] == "pending"


# ---------------- export + from_session（离线会话） ----------------

def _start_offline(client):
    r = client.post("/api/session/start", params={"autotick": "false"})
    assert r.status_code == 200
    return r.json()


def test_export_snapshot_roundtrip(client):
    """离线会话：入队 → 推进 → 导出 → 存盘 → simulate 引用导出快照跑剩余队列。"""
    _start_offline(client)
    seq = client.get("/api/session").json()["seq"]
    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": seq, "name": "main",
        "items": [{"op": "build", "type": "terran/supplydepot", "count": 1},
                  {"op": "build", "type": "terran/barracks", "count": 1}]})
    assert r.status_code == 200, r.text
    client.post("/api/session/tick", params={"count": 5})
    out = client.get("/api/session/export").json()
    doc = out["initial_state"]
    assert doc["minerals"] > 0 and doc["buildings"].get("terran/commandcenter") == 1
    uids = [q["uid"] for q in out["queue"]]
    assert uids and all(u for u in uids), "剩余队列带 uid"
    assert all(q["status"] in ("pending", "in_progress", "skipped") for q in out["queue"])
    # 存盘复用
    out2 = client.get("/api/session/export", params={"id": "snap1"}).json()
    assert out2["saved_as"] == "snap1"
    assert any(r_["id"] == "snap1" for r_ in client.get("/api/initial-states").json())
    r = client.post("/api/plans/simulate", json={
        "initial_state": "snap1", "horizon": 90,
        "queue": [{"op": "build", "type": "terran/barracks", "count": 1}]})
    assert r.status_code == 200, r.text
    client.post("/api/session/stop")


def test_simulate_from_session_uses_live_state_and_queue(client):
    _start_offline(client)
    seq = client.get("/api/session").json()["seq"]
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": seq, "name": "main",
        "items": [{"op": "train", "type": "terran/marine", "count": 2}]})
    client.post("/api/session/tick", params={"count": 2})
    r = client.post("/api/plans/simulate", json={"from_session": True, "horizon": 60})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["queue_source"] == "session"
    # 开局只有 CC：marine 没兵营 → skipped(prereq_missing)（导出状态生效的证明）
    st = [q["status"] for q in out["queue_status"]]
    assert "skipped" in st
    client.post("/api/session/stop")


def test_assign_workers_in_queue_status_and_static_check(client):
    """批 3 遗漏回归：assign_workers 项必须出现在队列执行状态表（瞬时完成），
    静态体检不得把任务名当 catalog ID 误报「catalog 不认」。"""
    r = client.post("/api/plans/simulate", json={
        "queue": [
            {"op": "build", "type": "terran/supplydepot", "count": 1},
            {"op": "assign_workers", "type": "gas", "task": "gas", "count": 3,
             "uid": "q02"},
        ], "horizon": 60})
    qs = {q["uid"]: q for q in r.json()["queue_status"]}
    assert qs["q02"]["status"] == "completed", "瞬时调度项：当帧完成"
    assert qs["q02"]["item"] == "assign_workers gas ×3"
    assert qs["q02"]["started_at"] is not None

    r2 = client.post("/api/plans/simulate", json={
        "horizon": 0,
        "queue": [
            {"op": "assign_workers", "type": "gas", "task": "gas", "count": 3,
             "uid": "q01"},
            {"op": "train", "type": "terran/marine", "count": 1, "uid": "q02"},
        ]})
    alerts = r2.json()["alerts"]
    assert not any("catalog 不认" in a["text_zh"] for a in alerts), "任务名≠类型名，别误报"
    assert any("产出建筑" in a["text_zh"] for a in alerts), "真缺口（缺兵营）照报"

    # 坏任务名在 parse 层就被拒（比静态体检更早一道门，400 带原因）
    r3 = client.post("/api/plans/simulate", json={
        "horizon": 0,
        "queue": [{"op": "assign_workers", "task": "scout", "count": 1,
                   "uid": "q01"}]})
    assert r3.status_code == 400 and "未知 worker task" in r3.text


def test_placement_approximation_modes(client):
    """放置近似（用户拍板 2026-08-24）：引用可选、报错不阻断、可一键关。"""
    # 默认开（出厂模板）：20 座补给站超 16 槽 → skipped placement_collision + 扩图建议
    r = client.post("/api/plans/simulate", json={
        "queue": [{"op": "build", "type": "terran/supplydepot", "count": 20}],
        "horizon": 200})
    out = r.json()
    assert out["placement_source"] == "出厂模板"
    st = out["queue_status"][0]
    assert st["status"] == "skipped" and st["reason"] == "placement_collision"
    assert any(a["kind"] == "placement_collision" and "扩图层" in a["text_zh"]
               for a in out["alerts"])
    # placement=false：完全不仿真槽位（旧行为，全完成）
    r2 = client.post("/api/plans/simulate", json={
        "queue": [{"op": "build", "type": "terran/supplydepot", "count": 20}],
        "horizon": 200, "placement": False})
    out2 = r2.json()
    assert out2["placement_source"] is None
    assert out2["queue_status"][0]["status"] == "completed"
    # map_plan=layout + 坏 exact 标记：静态体检报 placement_ref（error 但不阻断）
    r3 = client.post("/api/plans/simulate", json={
        "horizon": 0, "map_plan": "layout",
        "queue": [{"op": "build", "type": "terran/supplydepot", "count": 1,
                   "placement": {"kind": "exact", "mark": "Z99"}, "uid": "q01"}]})
    assert any(a["kind"] == "placement_ref" and "Z99" in a["text_zh"]
               for a in r3.json()["alerts"])
    # 好标记 D1 通过；动态干跑里坏标记摘除进「未入仿」、后续项继续仿真
    r4 = client.post("/api/plans/simulate", json={
        "horizon": 0, "map_plan": "layout",
        "queue": [{"op": "build", "type": "terran/supplydepot", "count": 1,
                   "placement": {"kind": "exact", "mark": "D1"}, "uid": "q01"}]})
    assert not any(a["kind"] == "placement_ref" for a in r4.json()["alerts"])
    r5 = client.post("/api/plans/simulate", json={
        "map_plan": "layout", "horizon": 60,
        "queue": [{"op": "build", "type": "terran/supplydepot", "count": 1,
                   "placement": {"kind": "exact", "mark": "Z99"}, "uid": "q01"},
                  {"op": "train", "type": "terran/marine", "count": 1, "uid": "q02"}]})
    out5 = r5.json()
    assert any("Z99" in s.get("reason", "") for s in out5["skipped"]), out5["skipped"]
    assert out5["queue_status"], "摘除坏标记后其余队列继续仿真"
