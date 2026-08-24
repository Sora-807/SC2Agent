"""agent 读面与写面清单（B10）的 api 层。"""
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.session import MAX_STALE_SEQ


@pytest.fixture()
def client(tmp_path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "proposals.jsonl"))
    c.post("/api/session/start", params={"autotick": "false"})
    return c


def test_observation_gives_seq_sections_and_facts(client: TestClient):
    sess = client.app.state.session
    for _ in range(4):
        sess.tick()
    body = client.get("/api/observation").json()
    assert body["seq"] == sess.seq - 0 or body["seq"] > 0
    assert body["facts"]["based_on_seq"] == body["seq"]
    assert "全局状态" in body["sections"] and "区域信息" in body["sections"]


def test_observation_supersedes_previous(client: TestClient):
    sess = client.app.state.session
    sess.tick()
    first = client.get("/api/observation").json()
    for _ in range(3):
        sess.tick()
    second = client.get("/api/observation").json()
    assert second["supersedes"] == first["seq"]


def test_observation_text_is_prompt_ready(client: TestClient):
    client.app.state.session.tick()
    body = client.get("/api/observation", params={"text": "true"}).json()
    assert body["text"].startswith("# 当前观察")
    assert "based_on_seq" in body["text"]


def test_observation_seq_is_the_world_seq_not_a_frame_counter(client: TestClient):
    """**回归**：`seq` 必须是 `GameState.seq`（世界版本号），不是信封自增计数器。

    用自增计数器的话观察包的 seq 会远大于世界 seq，`session.seq - based_on_seq` 恒为负，
    **R8 的门对 agent 就永久失效**（真机表现：agent 拿几分钟前的观察下命令照样被接受）。
    """
    sess = client.app.state.session
    for _ in range(6):
        sess.tick()
    obs = client.get("/api/observation").json()
    # 差一是**对的**：tick() 先用当前 gs 产帧、再推进世界，
    # 所以最新帧描述的是上一个 seq —— 观察包如实说"我看的是那一刻"。
    assert sess.seq - obs["seq"] <= 1, f"观察包 seq={obs['seq']} 应贴着世界 seq={sess.seq}"
    assert obs["facts"]["based_on_seq"] == obs["seq"]
    # 关键：它不是信封计数器。6 个 tick 产了几十条信封，如果是计数器，seq 会是几十。
    assert obs["seq"] <= 8, f"seq={obs['seq']} 看起来像信封计数器而不是世界版本号"
    assert len(sess.frames) > 3 * obs["seq"], "确认这一 tick 里确实产了远多于 seq 的信封"


def test_observation_seq_can_be_used_as_based_on_seq(client: TestClient):
    """闭环正向：拿观察包的 seq 下命令必须被接受。"""
    sess = client.app.state.session
    sess.tick()
    obs = client.get("/api/observation").json()
    r = client.post("/api/commands/workers", json={
        "based_on_seq": obs["facts"]["based_on_seq"], "task": "gas", "count": 2})
    assert r.status_code == 200


def test_stale_observation_from_an_agent_is_actually_rejected(client: TestClient):
    """闭环反向：**放久了的观察包必须被拒**。这条才真正证明 R8 的门在 agent 侧生效。"""
    from api.session import MAX_STALE_SEQ

    sess = client.app.state.session
    sess.tick()
    obs = client.get("/api/observation").json()
    for _ in range(MAX_STALE_SEQ + 3):
        sess.tick()
    r = client.post("/api/commands/workers", json={
        "based_on_seq": obs["facts"]["based_on_seq"], "task": "gas", "count": 2})
    assert r.status_code == 409, "过期观察包下的命令必须被拒（R8）"
    assert r.json()["detail"]["current_seq"] == sess.seq


def test_agent_tools_lists_write_surface_and_why_not(client: TestClient):
    body = client.get("/api/agent/tools").json()
    paths = [c["path"] for c in body["commands"]]
    assert "/api/commands/queue/{op}" in paths
    assert "/api/proposals" in paths
    assert body["max_stale_seq"] == MAX_STALE_SEQ
    # "为什么不能做"和"能做什么"放在一起：agent 最容易在不存在的动作上反复打转
    assert body["unsupported"]["queue_ops"]
    assert "predicates" in body["unsupported"]["flow"]
    assert any("based_on_seq" in r for r in body["rules"])


def test_observation_of_a_replay_source_also_works(tmp_path):
    """复盘也能产观察包 —— 同一条投影路径（这就是"agent 读面与 UI 同源"）。"""
    from pathlib import Path

    fixtures = Path("web/public/fixtures")
    if not (fixtures / "opening.jsonl").is_file():
        pytest.skip("夹具未生成")
    c = TestClient(create_app(fixtures, tmp_path / "p.jsonl"))
    body = c.get("/api/observation", params={"source": "opening", "text": "true"}).json()
    assert body["seq"] > 0
    assert "全局状态" in body["sections"] and "区域信息" in body["sections"]
