"""api（B2）：REST 静态面 + 帧源清单 + WS 帧流。

守两件事：
1. **服务端 seek 与客户端 seek 语义逐字一致**（每 topic 取 <= 游标的最后一帧）——
   否则"复盘照抄 live"这条会在换帧源时露馅；
2. **握手先给 rev**，前端才能在渲染前判契约版本（红线 C8）。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from view.encode import envelope
from view.schema import REV, TOPICS

FIXTURES = Path("web/public/fixtures")


def _write_source(tmp: Path, name: str) -> Path:
    """造一份小帧源：静态面在 t=0，动态帧 t=1..5，另有一条 t=3 的警报。"""
    rows = [
        envelope("static/map", seq=1, game_time=0.0, wall_ms=0, payload={"map_name": "M"}),
        envelope("static/catalog", seq=2, game_time=0.0, wall_ms=0, payload={"entries": []}),
        envelope("static/schema", seq=3, game_time=0.0, wall_ms=0, payload={"rules": []}),
    ]
    seq = 3
    for t in range(1, 6):
        seq += 1
        rows.append(envelope("frame/world", seq=seq, game_time=float(t), wall_ms=0,
                             payload={"tick": t}))
        if t == 3:
            seq += 1
            rows.append(envelope("frame/alerts", seq=seq, game_time=3.0, wall_ms=0,
                                 payload={"alerts": [{"id": "a1"}]}))
    p = tmp / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _write_source(tmp_path, "demo")
    (tmp_path / "index.json").write_text(
        json.dumps([{"key": "demo", "label": "演示场景"}], ensure_ascii=False), encoding="utf-8")
    return TestClient(create_app(tmp_path))


# ---------------- REST ----------------

def test_health_reports_rev_and_topics(client: TestClient):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["rev"] == REV
    assert set(body["topics"]) == set(TOPICS)


def test_schema_available_without_any_session(client: TestClient):
    """编辑器要能在没有对局的情况下先加载词表。"""
    body = client.get("/api/schema").json()
    assert body["predicates"] and body["actions"]
    assert "composite_actions" in body["forbidden"]


def test_sources_uses_backend_labels(client: TestClient):
    rows = client.get("/api/sources").json()
    assert len(rows) == 1
    assert rows[0]["id"] == "demo"
    assert rows[0]["label"] == "演示场景"      # 标签来自后端 index.json，前端不另起名字
    assert rows[0]["from_time"] == 0.0 and rows[0]["to_time"] == 5.0
    assert "frame/world" in rows[0]["topics"]


def test_statics_returns_three_static_frames(client: TestClient):
    rows = client.get("/api/sources/demo/statics").json()
    assert [r["topic"] for r in rows] == ["static/map", "static/catalog", "static/schema"]


def test_frames_at_is_snapshot_semantics(client: TestClient):
    """每 topic 取 <= game_time 的最后一帧；静态面即使晚于游标也给。"""
    rows = client.get("/api/sources/demo/frames", params={"game_time": 3.5}).json()
    by_topic = {r["topic"]: r for r in rows}
    assert by_topic["frame/world"]["payload"]["tick"] == 3
    assert by_topic["frame/alerts"]["game_time"] == 3.0
    assert "static/map" in by_topic

    early = {r["topic"]: r for r in
             client.get("/api/sources/demo/frames", params={"game_time": 1.0}).json()}
    assert early["frame/world"]["payload"]["tick"] == 1
    assert "frame/alerts" not in early       # 3s 的警报还没发生
    assert "static/schema" in early          # 但静态面任何游标下都有效


def test_jsonl_endpoint_serves_raw_file(client: TestClient):
    lines = [x for x in client.get("/api/sources/demo/jsonl").text.split("\n") if x.strip()]
    assert len(lines) == 9
    assert json.loads(lines[0])["rev"] == REV


def test_unknown_source_is_404(client: TestClient):
    assert client.get("/api/sources/nope/statics").status_code == 404
    assert client.get("/api/sources/nope/jsonl").status_code == 404


# ---------------- WS ----------------

def _recv_until(ws, topic: str, limit: int = 60) -> dict:
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get("topic") == topic:
            return msg
    raise AssertionError(f"没收到 {topic}")


def test_ws_handshake_sends_rev_before_any_frame(client: TestClient):
    """rev 必须最先到 —— 前端要在渲染前判契约版本（红线 C8）。"""
    with client.websocket_connect("/api/frames?source=demo&rate=0") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["topic"] == "_hello"
        assert hello["rev"] == REV
        assert (hello["from"], hello["to"]) == (0.0, 5.0)


def test_ws_sends_current_snapshot_on_connect(client: TestClient):
    with client.websocket_connect("/api/frames?source=demo&rate=0&start=3") as ws:
        assert json.loads(ws.receive_text())["topic"] == "_hello"
        got = {}
        for _ in range(4):
            m = json.loads(ws.receive_text())
            got[m["topic"]] = m
        assert got["frame/world"]["payload"]["tick"] == 3


def test_ws_topic_filter(client: TestClient):
    with client.websocket_connect(
        "/api/frames?source=demo&rate=0&start=5&topics=frame/world"
    ) as ws:
        assert json.loads(ws.receive_text())["topic"] == "_hello"
        m = json.loads(ws.receive_text())
        assert m["topic"] == "frame/world" and m["payload"]["tick"] == 5


def test_ws_seek_replays_snapshot_matching_rest(client: TestClient):
    """WS 的 seek 与 REST 的 frames_at 必须给出同一份快照。"""
    rest = {r["topic"]: r["seq"] for r in
            client.get("/api/sources/demo/frames", params={"game_time": 2.0}).json()}
    with client.websocket_connect("/api/frames?source=demo&rate=0") as ws:
        _recv_until(ws, "_hello")
        # 不假设初始快照有几帧（rate=0 时游标停在 from_time，那一刻只有静态面）。
        # 直接 seek，然后收到"每个期望 topic 都出现过"为止，取每个 topic 的最后一条。
        ws.send_text(json.dumps({"op": "seek", "game_time": 2.0}))
        got: dict[str, int] = {}
        for _ in range(40):
            m = json.loads(ws.receive_text())
            if m["topic"].startswith("_"):
                continue
            got[m["topic"]] = m["seq"]
            if set(got) >= set(rest):
                break
        assert got == rest


def test_ws_play_streams_to_eof(client: TestClient):
    with client.websocket_connect("/api/frames?source=demo&rate=0") as ws:
        _recv_until(ws, "_hello")
        ws.send_text(json.dumps({"op": "play", "rate": 50.0}))
        assert _recv_until(ws, "_eof", limit=120)["game_time"] == 5.0


def test_ws_ping_pong(client: TestClient):
    with client.websocket_connect("/api/frames?source=demo&rate=0") as ws:
        _recv_until(ws, "_hello")
        ws.send_text(json.dumps({"op": "ping"}))
        assert _recv_until(ws, "_pong")


def test_ws_unknown_source_reports_error(client: TestClient):
    with client.websocket_connect("/api/frames?source=nope") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["topic"] == "_error" and "nope" in msg["detail"]


@pytest.mark.skipif(not (FIXTURES / "opening.jsonl").is_file(), reason="夹具未生成")
def test_ws_serves_real_fixtures():
    """真夹具也能跑通（形状不是测试专用的）。"""
    c = TestClient(create_app(FIXTURES))
    with c.websocket_connect("/api/frames?source=opening&rate=0") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["rev"] == REV and hello["to"] >= 60
        topics = {json.loads(ws.receive_text())["topic"] for _ in range(8)}
        assert "frame/world" in topics and "static/catalog" in topics
