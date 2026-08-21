"""B3 进程分离：api 进程 + 会话子进程。

用 `driver=sim` 验**整套进程机械**（spawn / 流式帧 / 命令通道 / 崩溃识别 / 停止），
不需要 SC2。接真机时只换一个 flag（`driver=sc2`），子进程里是同一条产帧与命令路径。

守的三件事：
1. **崩了不带走 api**：弄死子进程，api 还能回答，会话是"崩溃"态且带着原因；
2. **命令在帧边界生效**（子进程语义），父进程给的是"已送达"而非"已生效"；
3. **提案的双投影在 live 会话里也要能用**（它要走子进程往返 —— GameState 在那边）。
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl"))
    yield c
    # 清理：不管测试怎么结束，别把子进程留成孤儿，也别留悬空的会话引用
    if c.app.state.session is not None and hasattr(c.app.state.session, "proc"):
        sess = c.app.state.session
        if sess.proc.poll() is None:
            sess.stop()
    c.app.state.proposals.session = None
    c.app.state.session = None


def _start(client: TestClient) -> dict:
    r = client.post("/api/session/start", params={"driver": "sim"})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_seq(client: TestClient, target: int, timeout: float = 15.0) -> dict:
    """等子进程会话推进到某个 seq（live 按墙钟节拍走）。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get("/api/session").json()
        if last["seq"] >= target:
            return last
        time.sleep(0.1)
    raise AssertionError(f"等到超时也没到 seq {target}；最后 {last}")


def test_sim_session_starts_and_streams_frames(client: TestClient):
    info = _start(client)
    assert info["driver"] == "sim" and info["state"] in ("启动中", "对局中")
    desc = _wait_seq(client, 4)
    assert desc["alive"] is True and desc["frames"] > 10
    rows = client.get("/api/sources").json()
    live = [r for r in rows if r["id"] == "live"]
    assert len(live) == 1 and live[0]["kind"] == "live"
    statics = client.get("/api/sources/live/statics").json()
    assert [f["topic"] for f in statics] == [
        "static/map", "static/catalog", "static/schema", "static/strategy"]


def test_queue_items_keeps_types_when_reconstructed_from_frames(client: TestClient):
    """字段名映射回归：帧里是 `stable_id`，`parse_item` 吃 `type`。

    漏掉映射的话 `type=None` 一路流进投影，双投影会算两条一模一样的曲线（实测踩过）。
    """
    _start(client)
    _wait_seq(client, 3)
    sess = client.app.state.session
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main",
        "items": [{"op": "train", "type": "terran/marine", "count": 2}]})
    deadline = time.time() + 15.0
    items = []
    while time.time() < deadline:
        items = client.app.state.session.queue_items("main")
        if items:
            break
        time.sleep(0.2)
    assert len(items) == 1
    assert items[0].type == "terran/marine", "type 不该丢（stable_id → type 的映射）"


def test_commands_go_through_the_subprocess_and_take_effect(client: TestClient):
    _start(client)
    _wait_seq(client, 3)
    sess = client.app.state.session
    seq = sess.seq
    r = client.post("/api/commands/queue/append", json={
        "based_on_seq": seq, "name": "main",
        "items": [{"op": "train", "type": "terran/marine", "count": 2}]})
    assert r.status_code == 200
    assert r.json()["detail"]["dispatched"] is True, "父进程给的是「已送达」而非「已生效」"

    # 等下一个帧边界，帧里应该看到这条队列。
    # 注意 poll 到"还没有 production 帧/队列"的窗口是正常的 —— 命令要等帧边界才生效。
    deadline = time.time() + 15.0
    while time.time() < deadline:
        frames = client.get("/api/sources/live/frames",
                            params={"game_time": 999999}).json()
        by = {f["topic"]: f for f in frames}
        prod = by.get("frame/production")
        if not prod or not prod["payload"]["queues"]:
            time.sleep(0.2)
            continue
        items = prod["payload"]["queues"][0]["items"]
        if items and items[0]["op"] == "train":
            assert items[0]["stable_id"] == "terran/marine"
            return
        time.sleep(0.2)
    raise AssertionError("命令没有在下一个帧边界生效")


def test_stale_command_still_rejected_live(client: TestClient):
    """R8 的门在 live 会话上同样成立（父进程持有 seq，比较语义与离线一致）。"""
    _start(client)
    _wait_seq(client, 3)
    sess = client.app.state.session
    old_seq = sess.seq
    _wait_seq(client, old_seq + 8)
    r = client.post("/api/commands/workers",
                    json={"based_on_seq": old_seq, "task": "gas", "count": 2})
    assert r.status_code == 409
    assert r.json()["detail"]["current_seq"] == sess.seq


def test_killing_the_subprocess_does_not_take_down_the_api(client: TestClient):
    """S7 的一半：游戏崩了，api 还活着，会话是"崩溃"态且带着原因。"""
    _start(client)
    _wait_seq(client, 3)
    sess = client.app.state.session
    sess.proc.kill()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        desc = client.get("/api/session").json()
        if desc["state"] == "崩溃":
            assert desc["alive"] is False
            # api 自己当然还活着
            assert client.get("/api/health").json()["ok"] is True
            return
        time.sleep(0.1)
    raise AssertionError("子进程死了会话应该变「崩溃」态")


def test_stop_is_graceful(client: TestClient):
    _start(client)
    _wait_seq(client, 3)
    r = client.post("/api/session/stop")
    assert r.status_code == 200
    sess = client.app.state.session
    assert sess is None, "stop 后会话对象应被清掉"
    assert client.get("/api/session").json()["state"] == "未连接"


def test_double_projection_works_on_live_via_subprocess(client: TestClient):
    """提案的杀手功能在 live 会话上也要能用 —— 它走子进程往返（GameState 在那边）。"""
    _start(client)
    _wait_seq(client, 3)
    sess = client.app.state.session
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main", "items": [
            {"op": "build", "type": "terran/factory",
             "placement": {"kind": "in_region", "region": "home"}},
            {"op": "build", "type": "terran/supplydepot",
             "placement": {"kind": "in_region", "region": "home"}}]})
    # 目标 seq 必须**现取**：`sess` 是旧引用，它的 seq 不会自己涨 ——
    # 拿旧 seq+2 当等待目标会立即返回，然后断言跑在「命令还没生效」的窗口里（实测踩过）。
    _wait_seq(client, client.app.state.session.seq + 2)
    # 父进程的 seq 只说明「帧到了」，不等于「命令已生效」—— 命令在**下一个帧边界**生效。
    # 所以等到帧里真出现两个队列项再提提案，否则双投影算的是旧队列。
    deadline = time.time() + 15.0
    while time.time() < deadline:
        frames = client.get("/api/sources/live/frames", params={"game_time": 999999}).json()
        by = {f["topic"]: f for f in frames}
        prod = by.get("frame/production")
        if (prod and prod["payload"]["queues"]
                and len(prod["payload"]["queues"][0]["items"]) == 2):
            break
        time.sleep(0.2)
    else:
        raise AssertionError("队列命令没有在帧边界生效")
    p = client.post("/api/proposals", json={
        "kind": "production_queue", "title_zh": "先造补给站",
        "rationale_zh": "重工厂缺前置会一直卡住，把补给站提前",
        "target": {"queue": "main"},
        "hunks": [{"id": "h1", "kind": "reorder", "text_zh": "换序",
                   "payload": {"order": [1, 0]}}]}).json()
    assert p["validation"]["ok"] is True
    pair = client.get(f"/api/proposals/{p['id']}/preview").json()
    assert pair["proposed"]["points"][-1]["supply_cap"] > pair["current"]["points"][-1]["supply_cap"]

    # 接受也走子进程（apply 通过队列命令）
    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 200 and r.json()["status"] == "已接受"


def test_sc2_driver_needs_real_game(client: TestClient):
    """sc2 会话不能把 api 拖死：要么建起来，要么报错 —— 两种都算活着。

    ⚠️ 这个测试会**真的启动一个 SC2 游戏**（run_session --driver sc2 → burnysc2 run_game），
    所以默认跳过，只在 SC2_INTEGRATION=1 时跑（真机集成，不是单测）。
    历史教训：之前无条件跑，每执行一次 pytest 就后台多一个 SC2 黑屏窗口 ——
    而且 V1 停止会话只 kill 子进程、不 kill SC2（sc2_adapter 里「显式 kill SC2 待补」），
    游戏进程就成了孤儿。真机集成测试必须自己收拾：finally 里 stop 会话，
    SC2 进程要靠 game_time_limit 自止或手动 taskkill。
    """
    import importlib.util
    import os

    if importlib.util.find_spec("sc2") is None:
        pytest.skip("burnysc2 没装")
    if os.environ.get("SC2_INTEGRATION") != "1":
        pytest.skip("真机集成测试：设 SC2_INTEGRATION=1 才跑（会启动真实 SC2 游戏）")
    r = client.post("/api/session/start", params={"driver": "sc2"})
    try:
        # 要么建起来（有 SC2），要么会话报错 —— 两种都算"没把 api 拖死"
        assert r.status_code in (200, 500)
    finally:
        client.post("/api/session/stop")