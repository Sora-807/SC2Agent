"""「开启游戏」两模式（2026-08-23 用户拍板收敛）：

- mode 只在 driver=sc2 有意义（offline/sim 是测试驱动）；
- 校验不静默：错 mode / 倍数越界 / normal 带 speed → 400 带原因；
- POST /api/session/speed 仿真会话即时变速（不重启）—— 用 sim 子进程验端到端
 （sim 也支持 speed 命令：缩放节拍睡眠，同一控制通道）。
"""
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl"))
    yield c
    if c.app.state.session is not None and hasattr(c.app.state.session, "proc"):
        sess = c.app.state.session
        if sess.proc.poll() is None:
            sess.stop()
    c.app.state.proposals.session = None
    c.app.state.session = None


def test_mode_validation_400s(client: TestClient):
    r = client.post("/api/session/start", params={"driver": "offline", "mode": "turbo"})
    assert r.status_code == 400 and "turbo" in r.json()["detail"]
    r = client.post("/api/session/start", params={"driver": "offline", "mode": "fast"})
    assert r.status_code == 400 and "sc2" in r.json()["detail"]
    r = client.post("/api/session/start",
                    params={"driver": "sc2", "mode": "normal", "speed": 4})
    assert r.status_code == 400 and "仿真模式" in r.json()["detail"]
    r = client.post("/api/session/start",
                    params={"driver": "sc2", "mode": "fast", "speed": 99})
    assert r.status_code == 400 and "1..64" in r.json()["detail"]


def test_speed_endpoint_on_sim_session_end_to_end(client: TestClient):
    """sim 子进程走同一条 speed 控制命令（stdin）—— 端到端验通道与 describe。"""
    r = client.post("/api/session/start", params={"driver": "sim"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 15
    while time.time() < deadline:
        if client.get("/api/session").json().get("seq", 0) > 0:
            break
        time.sleep(0.1)

    r = client.post("/api/session/speed", params={"multiplier": 4})
    assert r.status_code == 200 and r.json()["speed"] == 4
    info = client.get("/api/session").json()
    assert info["mode"] == "fast" and info["speed"] == 4

    r = client.post("/api/session/speed", params={"multiplier": 99})
    assert r.status_code == 400                       # 入参错误 = 400
    client.post("/api/session/stop")


def test_speed_endpoint_no_session_409(client: TestClient):
    r = client.post("/api/session/speed", params={"multiplier": 2})
    assert r.status_code == 409


def test_speed_endpoint_offline_session_409(client: TestClient):
    client.post("/api/session/start", params={"driver": "offline", "autotick": "false"})
    r = client.post("/api/session/speed", params={"multiplier": 2})
    assert r.status_code == 409 and "变速通道" in r.json()["detail"]
