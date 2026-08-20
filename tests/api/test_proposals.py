"""提案存储 + 校验 + 应用（B7）—— §6 的七条审批红线。

最重要的两条：
- **P2** 校验未通过的提案不可接受，但必须可见（agent 要学、用户要诊断）；
- **P5** anchor 过期自动失效，禁止盲接受。
另外双投影预览（接受前先看未来）是 §6 里认定的杀手功能，这里验它真的算出两条不同曲线。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from game.catalog import load_terran
from game.production import QueueItem, QueueOp
from view.proposals import (
    ANCHOR_STALE_SECONDS,
    Hunk,
    ProposalStore,
    apply_hunks,
    item_to_json,
    parse_item,
)

CAT = load_terran()


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames"))
    c.post("/api/session/start", params={"autotick": "false"})
    return c


def _seq(client: TestClient) -> int:
    return client.app.state.session.seq


def _queue(client: TestClient, items: list[dict]) -> None:
    r = client.post("/api/commands/queue/submit",
                    json={"based_on_seq": _seq(client), "name": "main", "items": items})
    assert r.status_code == 200, r.text


def _propose(client: TestClient, **over) -> dict:
    body = {
        "kind": "production_queue",
        "title_zh": "先出兵",
        "rationale_zh": "队首缺气阻塞，先把无气需求的兵提前",
        "target": {"queue": "main"},
        "hunks": [{"id": "h1", "kind": "reorder", "text_zh": "把 #0 移到最后",
                   "payload": {"order": [1, 0]}}],
    }
    body.update(over)
    return client.post("/api/proposals", json=body).json()


# ---------------- hunk 应用（纯函数） ----------------

def _items(*specs) -> list[QueueItem]:
    return [parse_item(s) for s in specs]


def test_apply_hunks_insert_delete_modify_reorder():
    base = _items({"op": "train", "type": "terran/marine"},
                  {"op": "build", "type": "terran/barracks"})
    out = apply_hunks(base, [Hunk("h", "insert", "", {"index": 0,
                                                      "item": {"op": "train", "type": "terran/scv"}})])
    assert [i.type for i in out] == ["terran/scv", "terran/marine", "terran/barracks"]
    assert [i.type for i in base] == ["terran/marine", "terran/barracks"], "不改入参"

    assert [i.type for i in apply_hunks(base, [Hunk("h", "delete", "", {"index": 0})])] == [
        "terran/barracks"]
    assert [i.type for i in apply_hunks(base, [Hunk("h", "reorder", "", {"order": [1, 0]})])] == [
        "terran/barracks", "terran/marine"]
    mod = apply_hunks(base, [Hunk("h", "modify", "",
                                  {"index": 1, "item": {"op": "train", "type": "terran/medivac"}})])
    assert [i.type for i in mod] == ["terran/marine", "terran/medivac"]


def test_apply_hunks_is_sequential_so_partial_accept_keeps_index_meaning():
    """按顺序逐个应用（而不是先算总位移）：部分接受时下标语义才和用户看到的一致。"""
    base = _items({"op": "train", "type": "terran/marine"},
                  {"op": "train", "type": "terran/scv"})
    out = apply_hunks(base, [
        Hunk("a", "delete", "", {"index": 0}),
        Hunk("b", "insert", "", {"index": 0, "item": {"op": "build", "type": "terran/barracks"}}),
    ])
    assert [i.type for i in out] == ["terran/barracks", "terran/scv"]


def test_apply_hunks_rejects_out_of_range_and_bad_permutation():
    base = _items({"op": "train", "type": "terran/marine"})
    with pytest.raises(ValueError, match="越界"):
        apply_hunks(base, [Hunk("h", "delete", "", {"index": 3})])
    with pytest.raises(ValueError, match="排列"):
        apply_hunks(base, [Hunk("h", "reorder", "", {"order": [0, 1]})])
    with pytest.raises(ValueError, match="未知 kind"):
        apply_hunks(base, [Hunk("h", "frobnicate", "", {})])


def test_item_json_roundtrip():
    raw = {"op": "build", "type": "terran/barracks", "count": 1,
           "placement": {"kind": "in_region", "region": "home", "index": None}, "task": None}
    assert item_to_json(parse_item(raw)) == raw


def test_parse_item_rejects_unknown_op_and_placement():
    with pytest.raises(ValueError, match="未知队列 op"):
        parse_item({"op": "frob"})
    with pytest.raises(ValueError, match="未知 placement.kind"):
        parse_item({"op": "build", "type": "terran/barracks", "placement": {"kind": "nope"}})


# ---------------- 创建与校验 ----------------

def test_proposal_requires_a_rationale(client: TestClient):
    """没有理由的提案连提都不该提 —— 理由是审批的依据（§6 P3 的对偶）。"""
    r = client.post("/api/proposals", json={
        "kind": "production_queue", "title_zh": "x", "rationale_zh": "  ",
        "target": {"queue": "main"}, "hunks": []})
    assert r.status_code == 400 and "rationale_zh" in r.json()["detail"]


def test_invalid_proposal_is_stored_and_visible_p2(client: TestClient):
    """P2：校验不通过也要存、要可见 —— agent 要学，用户要诊断。"""
    _queue(client, [{"op": "train", "type": "terran/marine"}])
    p = _propose(client, hunks=[{"id": "h1", "kind": "delete", "text_zh": "删第 9 项",
                                 "payload": {"index": 9}}])
    assert p["validation"]["ok"] is False
    assert "越界" in p["validation"]["errors"][0]["text_zh"]
    assert any(x["id"] == p["id"] for x in client.get("/api/proposals").json())

    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 409 and "不可接受" in r.json()["detail"]


def test_unapplicable_kinds_explain_why(client: TestClient):
    """其他类型可以存、可以看，但不能 apply，并说明原因（不给假按钮）。"""
    for kind, frag in [("flow_ast", "validate"), ("params", "live_editable"),
                       ("map_plan", "map_plan patch")]:
        p = _propose(client, kind=kind)
        assert p["validation"]["ok"] is False
        assert frag in p["validation"]["errors"][0]["text_zh"], kind


# ---------------- 双投影预览（杀手功能） ----------------

def test_preview_pair_gives_two_different_futures(client: TestClient):
    """场景要选得让顺序**真的有影响**。

    沙盒开局只有指挥中心 + 农民，所以「重工厂」缺前置、「机枪兵」缺产出建筑 ——
    两种顺序都立刻卡住，两条曲线会一模一样（那说明的是场景没选好，不是预览坏了）。
    这里用「补给站 vs 重工厂」：补给站无前置且便宜，排在前面就真能造起来，
    排在被卡的重工厂后面就永远轮不到 —— 队首门控的语义在投影里也成立。
    """
    _queue(client, [
        {"op": "build", "type": "terran/factory",
         "placement": {"kind": "in_region", "region": "home"}},
        {"op": "build", "type": "terran/supplydepot",
         "placement": {"kind": "in_region", "region": "home"}},
    ])
    p = _propose(client, title_zh="先造补给站",
                 rationale_zh="重工厂缺前置会一直卡住，把补给站提前免得整队冻结")
    assert p["validation"]["ok"] is True
    assert p["preview"]["kind"] == "projection_pair"

    pair = client.get(f"/api/proposals/{p['id']}/preview").json()
    cur, prop = pair["current"], pair["proposed"]
    assert cur["based_on_seq"] == prop["based_on_seq"], "两条曲线必须同一起点，差异只来自队列"
    assert len(cur["points"]) == len(prop["points"]) > 60

    # 提案后：补给站能建起来 → 人口上限涨；当前顺序：卡在重工厂 → 上限不动
    assert prop["points"][-1]["supply_cap"] > cur["points"][-1]["supply_cap"], \
        "调换队序应该产生不同的未来，否则预览没意义"
    assert any(e["kind"] == "completed" and e["stable_id"] == "terran/supplydepot"
               for e in prop["events"]), "提案后的投影里补给站应当建成"


# ---------------- 接受 / 拒绝 ----------------

def test_accept_applies_through_the_same_command_path(client: TestClient):
    """P4：应用走与 agent 相同的命令路径，不开 UI 后门。"""
    _queue(client, [
        {"op": "build", "type": "terran/factory",
         "placement": {"kind": "in_region", "region": "home"}},
        {"op": "train", "type": "terran/marine", "count": 6},
    ])
    p = _propose(client)
    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "已接受"
    sess = client.app.state.session
    assert [i.type for i in sess.runtime.queue("main").items] == [
        "terran/marine", "terran/factory"]


def test_partial_accept_marks_partial(client: TestClient):
    _queue(client, [{"op": "train", "type": "terran/marine"}])
    p = _propose(client, hunks=[
        {"id": "h1", "kind": "insert", "text_zh": "加农民",
         "payload": {"index": 0, "item": {"op": "train", "type": "terran/scv"}}},
        {"id": "h2", "kind": "insert", "text_zh": "加医疗机",
         "payload": {"index": 0, "item": {"op": "train", "type": "terran/medivac"}}},
    ])
    r = client.post(f"/api/proposals/{p['id']}/accept", json={"hunk_ids": ["h1"]})
    assert r.status_code == 200 and r.json()["status"] == "部分接受"
    sess = client.app.state.session
    assert [i.type for i in sess.runtime.queue("main").items] == [
        "terran/scv", "terran/marine"]


def test_reject_requires_a_reason_that_flows_back(client: TestClient):
    """P3：拒绝必须附理由 —— 否则 agent 会重复推同一个提案。"""
    _queue(client, [{"op": "train", "type": "terran/marine"},
                    {"op": "train", "type": "terran/scv"}])
    p = _propose(client)
    r = client.post(f"/api/proposals/{p['id']}/reject", json={"comment_zh": ""})
    assert r.status_code == 400 and "理由" in r.json()["detail"]

    r = client.post(f"/api/proposals/{p['id']}/reject",
                    json={"comment_zh": "这波要先开矿，不急着出兵"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "已拒绝"
    assert body["decision"]["comment_zh"] == "这波要先开矿，不急着出兵"


def test_accepting_twice_is_refused(client: TestClient):
    _queue(client, [{"op": "train", "type": "terran/marine"},
                    {"op": "train", "type": "terran/scv"}])
    p = _propose(client)
    assert client.post(f"/api/proposals/{p['id']}/accept").status_code == 200
    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 409 and "已处理" in r.json()["detail"]


def test_stale_anchor_expires_and_blocks_accept_p5(client: TestClient):
    """P5：提案基于的世界已经不在了 → 自动失效，禁止盲接受。"""
    _queue(client, [{"op": "train", "type": "terran/marine"},
                    {"op": "train", "type": "terran/scv"}])
    p = _propose(client)
    sess = client.app.state.session
    for _ in range(int(ANCHOR_STALE_SECONDS) + 2):
        sess.tick()
    rows = client.get("/api/proposals").json()
    assert rows[0]["status"] == "已失效"
    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 409 and "已失效" in r.json()["detail"]


# ---------------- 存储 ----------------

def test_stopping_the_session_detaches_proposals(client: TestClient):
    """停会话后不能再基于死会话算 anchor —— 否则 P5 的失效判断永远不会触发。"""
    client.post("/api/session/stop")
    assert client.app.state.proposals.session is None
    r = client.post("/api/proposals", json={
        "kind": "production_queue", "title_zh": "x", "rationale_zh": "理由",
        "target": {"queue": "main"},
        "hunks": [{"id": "h1", "kind": "insert", "text_zh": "加",
                   "payload": {"index": 0, "item": {"op": "train", "type": "terran/marine"}}}]})
    assert r.status_code == 200
    assert r.json()["anchor"] is None, "没有会话 → 没有 anchor（而不是一个假的）"
    # 而且不能应用（没有会话可改）
    assert client.post(f"/api/proposals/{r.json()['id']}/accept").status_code == 409


def test_history_is_appended_not_overwritten(tmp_path: Path):
    """提案的价值一半在历史（提过什么、为什么被拒）→ 追加日志。"""
    path = tmp_path / "proposals.jsonl"
    store = ProposalStore(CAT, path=path)
    p = store.create({"kind": "flow_ast", "title_zh": "x", "rationale_zh": "理由",
                      "target": {}, "hunks": []})
    store.reject(p.id, "不行")
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["status"] == "待审批" and lines[1]["status"] == "已拒绝"

    reloaded = ProposalStore(CAT, path=path)
    assert reloaded.get(p.id).status == "已拒绝", "重载后取最后一条状态"
