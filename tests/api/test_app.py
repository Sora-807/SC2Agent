"""api（B2）：REST 静态面 + 帧源清单 + WS 帧流。

守两件事：
1. **服务端 seek 与客户端 seek 语义逐字一致**（每 topic 取 <= 游标的最后一帧）——
   否则"复盘照抄 live"这条会在换帧源时露馅；
2. **握手先给 rev**，前端才能在渲染前判契约版本（红线 C8）。
"""
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.sources import SourceInfo
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


class _BootLaterLive:
    """模拟真机时序的 live 源：connect 时一帧没有（SC2 开机 1-2 分钟），
    静态面稍后才以 **game_time=0** 落地（burnysc2 首个 on_step 的 bot.time=0）。

    between() 逐字复刻 LiveSession 的 game_time 过滤 —— 静态帧 game_time=0
    永远落在游标后面，这正是要回归锁死的 bug。
    """

    id = "live"

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._statics: list[dict] = []

    def add(self, frame: dict) -> None:
        if frame["topic"].startswith("static/"):
            self._statics.append(frame)
        self.frames.append(frame)

    def info(self) -> SourceInfo:
        times = [f["game_time"] for f in self.frames] or [0.0]
        return SourceInfo(id="live", label="boot-later", kind="live",
                          envelopes=len(self.frames), from_time=min(times),
                          to_time=max(times),
                          topics=sorted({f["topic"] for f in self.frames}),
                          snapshots=[])

    def statics(self) -> list[dict]:
        return list(self._statics)

    def latest_at(self, game_time: float, topics: set[str] | None = None) -> list[dict]:
        chosen: dict[str, dict] = {}
        for f in self.frames:
            if topics is not None and f["topic"] not in topics:
                continue
            if f["game_time"] <= game_time + 1e-9:
                chosen[f["topic"]] = f
        for f in self._statics:
            if topics is not None and f["topic"] not in topics:
                continue
            chosen.setdefault(f["topic"], f)
        return list(chosen.values())

    def between(self, after: float, until: float,
                topics: set[str] | None = None) -> list[dict]:
        return [f for f in self.frames
                if after + 1e-9 < f["game_time"] <= until + 1e-9
                and (topics is None or f["topic"] in topics)]


def test_ws_live_forwards_statics_landing_at_game_time_zero(client: TestClient):
    """真机 bug 回归（实机地图页一直「等待 static/map」而生产页有数据）：

    前端在会话出帧前 attach（真机常态），静态面以 game_time=0 落地 ——
    live 泵的游标过滤（> cursor）永远发不出它们。静态帧必须走
    「快照出现即发」通道，不受 game_time 门控。

    心跳线程持续产 world 帧：若回归（静态帧永远不来），receive 不会挂死，
    而是耗尽 `_recv_until` 的限额后**快速失败**。
    """
    stub = _BootLaterLive()
    client.app.state.session = stub
    stop = threading.Event()

    def _tick_frames() -> None:
        t = 1
        while not stop.is_set():
            t += 1
            stub.add(envelope("frame/world", seq=2 + t, game_time=float(t), wall_ms=0,
                              payload={"tick": t}))
            stop.wait(0.05)

    with client.websocket_connect("/api/frames?source=live") as ws:
        assert json.loads(ws.receive_text())["topic"] == "_hello"
        # 会话启动后首帧到达：静态面 game_time=0，动态面 game_time=1
        stub.add(envelope("static/map", seq=1, game_time=0.0, wall_ms=0,
                          payload={"map_name": "M"}))
        stub.add(envelope("static/catalog", seq=2, game_time=0.0, wall_ms=0,
                          payload={"entries": []}))
        stub.add(envelope("frame/world", seq=3, game_time=1.0, wall_ms=0,
                          payload={"tick": 1}))
        threading.Thread(target=_tick_frames, daemon=True).start()
        try:
            assert _recv_until(ws, "static/map")["payload"]["map_name"] == "M"
            assert _recv_until(ws, "static/catalog")["topic"] == "static/catalog"
            assert _recv_until(ws, "frame/world")["payload"]["tick"] == 1
        finally:
            stop.set()


def test_ws_live_without_session_does_not_fake_a_world(client: TestClient):
    """live 无会话 = 如实说「未连接」，**不**惰性建假世界（回归锁）。

    旧行为：WS source=live 一连就顺手建一个进程内 OfflineSession —— 假世界的
    出厂模板槽位 + 逐秒假帧，曾让前端把「SC2 没启动」误读成
    「时间线连上了、地图空、却有来历不明的槽位」。
    """
    with client.websocket_connect("/api/frames?source=live") as ws:
        assert json.loads(ws.receive_text())["topic"] == "_hello"
        sess = json.loads(ws.receive_text())
        assert sess["topic"] == "frame/session"
        assert sess["payload"]["state"] == "未连接"
        assert sess["payload"]["frame_source"] == "live"
        # 没有会话被创建：连接本身不应该是「启动一个假世界」的副作用
        assert client.app.state.session is None
    # REST 读面同样如实：无会话 404，而不是悄悄造一个
    r = client.get("/api/sources/live/statics")
    assert r.status_code == 404
    assert "没有活跃会话" in r.json()["detail"]


def test_ws_live_picks_up_session_started_after_attach(client: TestClient):
    """前端 attach 在先、会话启动在后（真机常态：开机 1-2 分钟）：
    同一条 WS 连接在会话出现后自动接上 —— 不需要前端重连。"""
    stub = _BootLaterLive()
    with client.websocket_connect("/api/frames?source=live") as ws:
        assert json.loads(ws.receive_text())["topic"] == "_hello"
        assert json.loads(ws.receive_text())["payload"]["state"] == "未连接"
        # 会话现在才出现（换会话对象 → pump 重置游标并补发全量快照）
        client.app.state.session = stub
        stub.add(envelope("static/map", seq=1, game_time=0.0, wall_ms=0,
                          payload={"map_name": "M"}))
        stub.add(envelope("frame/world", seq=2, game_time=1.0, wall_ms=0,
                          payload={"tick": 1}))
        assert _recv_until(ws, "static/map")["payload"]["map_name"] == "M"
        assert _recv_until(ws, "frame/world")["payload"]["tick"] == 1


@pytest.mark.skipif(not (FIXTURES / "opening.jsonl").is_file(), reason="夹具未生成")
def test_ws_serves_real_fixtures():
    """真夹具也能跑通（形状不是测试专用的）。"""
    c = TestClient(create_app(FIXTURES))
    with c.websocket_connect("/api/frames?source=opening&rate=0") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["rev"] == REV and hello["to"] >= 60
        topics = {json.loads(ws.receive_text())["topic"] for _ in range(8)}
        assert "frame/world" in topics and "static/catalog" in topics
