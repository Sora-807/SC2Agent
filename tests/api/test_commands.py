"""命令写入面 + 离线沙盒会话（B6）。

守三件事：
1. **必带 `based_on_seq`，过期就拒**（R8 / ADR-0009）—— 这一道门是 UI 与 agent 共用的；
2. **命令真的改变会话**（不是"接受了但什么都没发生"）；
3. **不静默**：不支持的 op、非法参数、非法排列一律 400 + 原因，原因取自后端闭集。
"""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.session import MAX_STALE_SEQ, OfflineSession
from game.catalog import load_all

CAT = load_all()


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path))


def _start(client: TestClient) -> dict:
    """`autotick=false`：测试自己控制时间，否则后台 tick 会和手动 tick 抢，
    `based_on_seq` 的断言就变得不可预测。"""
    return client.post("/api/session/start", params={"autotick": "false"}).json()


# ---------------- 会话 ----------------

def test_session_absent_until_started(client: TestClient):
    assert client.get("/api/session").json()["state"] == "未连接"
    info = _start(client)
    assert info["state"] == "对局中" and info["seq"] == 0
    assert info["max_stale_seq"] == MAX_STALE_SEQ


def test_session_appears_as_a_live_source(client: TestClient):
    _start(client)
    rows = client.get("/api/sources").json()
    live = [r for r in rows if r["id"] == "live"]
    assert len(live) == 1 and live[0]["kind"] == "live"
    statics = client.get("/api/sources/live/statics").json()
    assert [s["topic"] for s in statics] == [
        "static/map", "static/catalog", "static/schema", "static/strategy"]


def test_commands_need_a_session(client: TestClient):
    r = client.post("/api/commands/workers", json={"based_on_seq": 0, "task": "gas", "count": 3})
    assert r.status_code == 409
    assert "会话" in str(r.json()["detail"])


# ---------------- 新鲜度门（R8） ----------------

def test_stale_observation_is_rejected_with_current_seq(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    for _ in range(MAX_STALE_SEQ + 3):
        sess.tick()
    r = client.post("/api/commands/workers",
                    json={"based_on_seq": 0, "task": "gas", "count": 3})
    assert r.status_code == 409, "过期观察必须被拒（R8）"
    detail = r.json()["detail"]
    assert detail["based_on_seq"] == 0
    assert detail["current_seq"] == sess.seq
    assert "过期观察" in detail["reason"]


def test_fresh_observation_is_accepted(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    sess.tick()
    r = client.post("/api/commands/workers",
                    json={"based_on_seq": sess.seq, "task": "gas", "count": 3})
    assert r.status_code == 200
    assert r.json()["accepted_seq"] == sess.seq


def test_based_on_seq_is_required_not_optional(client: TestClient):
    """字段必填 —— "可选、缺了就跳过检查"等于没有这道门。"""
    _start(client)
    r = client.post("/api/commands/workers", json={"task": "gas", "count": 3})
    assert r.status_code == 422


# ---------------- 命令真的改变会话 ----------------

def test_queue_submit_then_append_then_reorder(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    seq = sess.seq

    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": seq, "name": "main",
        "items": [
            {"op": "build", "type": "terran/supplydepot",
             "placement": {"kind": "in_region", "region": "home"}},
            {"op": "train", "type": "terran/marine", "count": 4},
        ],
    })
    assert r.status_code == 200 and r.json()["detail"]["items"] == 2
    assert [i.type for i in sess.runtime.queue("main").items] == [
        "terran/supplydepot", "terran/marine"]

    client.post("/api/commands/queue/append", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "build", "type": "terran/barracks",
                   "placement": {"kind": "in_region", "region": "home"}}],
    })
    assert len(sess.runtime.queue("main").items) == 3

    r = client.post("/api/commands/queue/reorder", json={
        "based_on_seq": sess.seq, "name": "main", "order": ["q03", "q01", "q02"]})
    assert r.status_code == 200
    assert [i.type for i in sess.runtime.queue("main").items] == [
        "terran/barracks", "terran/supplydepot", "terran/marine"]


def test_queue_remove_by_uid(client: TestClient):
    """引用走 uid（ADR-0032）：下标会随执行区保留漂移，uid 是稳定锚点。"""
    _start(client)
    sess: OfflineSession = client.app.state.session
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "train", "type": "terran/marine"},
                  {"op": "train", "type": "terran/scv"}],
    })
    r = client.post("/api/commands/queue/remove",
                    json={"based_on_seq": sess.seq, "name": "main", "uid": "q01"})
    assert r.status_code == 200
    assert [i.type for i in sess.runtime.queue("main").items] == ["terran/scv"]


def test_worker_quota_is_absolute_and_visible_in_frames(client: TestClient):
    """配额是目标值（幂等）；设完能在 frame/economy 里看到。"""
    from view.adapt import economy_frame

    _start(client)
    sess: OfflineSession = client.app.state.session
    client.post("/api/commands/workers",
                json={"based_on_seq": sess.seq, "task": "gas", "count": 4})
    client.post("/api/commands/workers",
                json={"based_on_seq": sess.seq, "task": "gas", "count": 4})
    econ = economy_frame(sess.keeper.snapshot(sess.world.game_state()))
    assert {t.task: t.quota for t in econ.tasks}["gas"] == 4


# ---------------- 不静默 ----------------

def test_unsupported_queue_op_rejected_with_backend_reason(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "research", "type": "terran/marine"}],
    })
    assert r.status_code == 400
    assert "upgrade 数据目录" in r.json()["detail"], "原因应取自后端闭集，不另编文案"
    assert "暂不支持" in r.json()["detail"]


def test_unknown_queue_op_rejected(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    r = client.post("/api/commands/queue/nope",
                    json={"based_on_seq": sess.seq, "name": "main"})
    assert r.status_code == 400 and "未知队列 op" in r.json()["detail"]


def test_bad_reorder_permutation_rejected(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "train", "type": "terran/marine"}],
    })
    r = client.post("/api/commands/queue/reorder",
                    json={"based_on_seq": sess.seq, "name": "main", "order": ["q01", "q02"]})
    assert r.status_code == 400 and "排列" in r.json()["detail"]


def test_placement_exact_needs_mark(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    r = client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "build", "type": "terran/barracks",
                   "placement": {"kind": "exact"}}],
    })
    assert r.status_code in (400, 422)


# ---------------- live 帧源 ----------------

def test_live_ws_follows_new_frames(client: TestClient):
    _start(client)
    sess: OfflineSession = client.app.state.session
    for _ in range(4):
        sess.tick()
    with client.websocket_connect("/api/frames?source=live") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["topic"] == "_hello" and hello["kind"] == "live"
        topics = set()
        for _ in range(6):
            m = json.loads(ws.receive_text())
            if not m["topic"].startswith("_"):
                topics.add(m["topic"])
        assert "frame/world" in topics
