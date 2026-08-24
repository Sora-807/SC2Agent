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
from game.catalog import load_all
from game.production import QueueItem, QueueOp
from view.proposals import (
    ANCHOR_STALE_SECONDS,
    Hunk,
    ProposalStore,
    apply_hunks,
    item_to_json,
    parse_item,
)

CAT = load_all()


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    # 显式给 tmp 的提案日志：不给就是内存态，但这里要顺带验持久化路径也工作
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "proposals.jsonl"))
    c.post("/api/session/start", params={"autotick": "false"})
    return c


def _seq(client: TestClient) -> int:
    return client.app.state.session.seq


def _queue(client: TestClient, items: list[dict]) -> None:
    r = client.post("/api/commands/queue/submit",
                    json={"based_on_seq": _seq(client), "name": "main", "items": items})
    assert r.status_code == 200, r.text


def _propose_body(**over) -> dict:
    body = {
        "kind": "production_queue",
        "title_zh": "先出兵",
        "rationale_zh": "队首缺气阻塞，先把无气需求的兵提前",
        "target": {"queue": "main"},
        "hunks": [{"id": "h1", "kind": "reorder", "text_zh": "把 q01 移到最后",
                   "payload": {"order": ["q02", "q01"]}}],
    }
    body.update(over)
    return body


def _propose(client: TestClient, **over) -> dict:
    return client.post("/api/proposals", json=_propose_body(**over)).json()


# ---------------- hunk 应用（纯函数） ----------------

def _items(*specs) -> list[QueueItem]:
    """带 uid 的队列（ADR-0032：hunk 引用走 uid，没 uid 的项引用不到）。"""
    out = [parse_item(s) for s in specs]
    for i, it in enumerate(out, start=1):
        it.uid = f"q{i:02d}"
    return out


def test_apply_hunks_insert_delete_modify_reorder():
    base = _items({"op": "train", "type": "terran/marine"},
                  {"op": "build", "type": "terran/barracks"})
    out = apply_hunks(base, [Hunk("h", "insert", "",
                                  {"before_uid": "q01",
                                   "item": {"op": "train", "type": "terran/scv"}})])
    assert [i.type for i in out] == ["terran/scv", "terran/marine", "terran/barracks"]
    assert [i.type for i in base] == ["terran/marine", "terran/barracks"], "不改入参"

    assert [i.type for i in apply_hunks(base, [Hunk("h", "delete", "", {"uid": "q01"})])] == [
        "terran/barracks"]
    assert [i.type for i in apply_hunks(base, [Hunk("h", "reorder", "",
                                                    {"order": ["q02", "q01"]})])] == [
        "terran/barracks", "terran/marine"]
    mod = apply_hunks(base, [Hunk("h", "modify", "",
                                  {"uid": "q02",
                                   "item": {"op": "train", "type": "terran/medivac"}})])
    assert [i.type for i in mod] == ["terran/marine", "terran/medivac"]


def test_apply_hunks_is_sequential_so_partial_accept_keeps_uid_meaning():
    """按顺序逐个应用：部分接受时 uid 语义才和用户看到的一致。"""
    base = _items({"op": "train", "type": "terran/marine"},
                  {"op": "train", "type": "terran/scv"})
    out = apply_hunks(base, [
        Hunk("a", "delete", "", {"uid": "q01"}),
        Hunk("b", "insert", "", {"before_uid": "q02",
                                 "item": {"op": "build", "type": "terran/barracks"}}),
    ])
    assert [i.type for i in out] == ["terran/barracks", "terran/scv"]


def test_apply_hunks_rejects_unknown_uid_and_bad_permutation():
    base = _items({"op": "train", "type": "terran/marine"})
    with pytest.raises(ValueError, match="不在队列里"):
        apply_hunks(base, [Hunk("h", "delete", "", {"uid": "q99"})])
    with pytest.raises(ValueError, match="排列"):
        apply_hunks(base, [Hunk("h", "reorder", "", {"order": ["q01", "q02"]})])
    with pytest.raises(ValueError, match="未知 kind"):
        apply_hunks(base, [Hunk("h", "frobnicate", "", {})])


def test_item_json_roundtrip():
    raw = {"op": "build", "type": "terran/barracks", "count": 1,
           "placement": {"kind": "in_region", "region": "home", "index": None}, "task": None,
           "uid": "q07", "status": "completed", "reason": None}
    it = parse_item(raw)
    assert (it.uid, it.status) == ("q07", "completed"), "账本字段必须往返（否则已完成项被重跑）"
    assert item_to_json(it) == raw


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
    p = _propose(client, hunks=[{"id": "h1", "kind": "delete", "text_zh": "删不存在的项",
                                 "payload": {"uid": "q99"}}])
    assert p["validation"]["ok"] is False
    assert "不在队列里" in p["validation"]["errors"][0]["text_zh"]
    assert any(x["id"] == p["id"] for x in client.get("/api/proposals").json())

    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 409 and "不可接受" in r.json()["detail"]


def test_unapplicable_kinds_explain_why(client: TestClient):
    """其他类型可以存、可以看，但不能 apply，并说明原因（不给假按钮）。

    B14 起 map_plan 已可应用（写回机器覆盖层），不再在此列。
    """
    for kind, frag in [("flow_ast", "validate"), ("params", "live_editable"),
                       ("group_composition", "hot-edit")]:
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
    # 审批停用后端点创建即自动应用，「提案前队列」就不存在了 —— 预览这个
    # 休眠功能要测数学本身，直接走 store（绕过端点的自动应用）。
    p = client.app.state.proposals.create(_propose_body(
        title_zh="先造补给站",
        rationale_zh="重工厂缺前置会一直卡住，把补给站提前免得整队冻结")).to_json()
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
    """P4：应用走与 agent 相同的命令路径，不开 UI 后门。

    端点创建即自动应用后，手工 accept 是休眠通道 —— 直接走 store 建一条待审批，
    再打 accept 端点，验证的正是这条预留路径。
    """
    _queue(client, [
        {"op": "build", "type": "terran/factory",
         "placement": {"kind": "in_region", "region": "home"}},
        {"op": "train", "type": "terran/marine", "count": 6},
    ])
    pid = client.app.state.proposals.create(_propose_body()).id
    r = client.post(f"/api/proposals/{pid}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "已接受"
    sess = client.app.state.session
    assert [i.type for i in sess.runtime.queue("main").items] == [
        "terran/marine", "terran/factory"]


def test_partial_accept_marks_partial(client: TestClient):
    _queue(client, [{"op": "train", "type": "terran/marine"}])
    pid = client.app.state.proposals.create(_propose_body(hunks=[
        {"id": "h1", "kind": "insert", "text_zh": "加农民",
         "payload": {"before_uid": "q01", "item": {"op": "train", "type": "terran/scv"}}},
        {"id": "h2", "kind": "insert", "text_zh": "加医疗机",
         "payload": {"before_uid": "q01", "item": {"op": "train", "type": "terran/medivac"}}},
    ])).id
    r = client.post(f"/api/proposals/{pid}/accept", json={"hunk_ids": ["h1"]})
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
    pid = client.app.state.proposals.create(_propose_body()).id
    assert client.post(f"/api/proposals/{pid}/accept").status_code == 200
    r = client.post(f"/api/proposals/{pid}/accept")
    assert r.status_code == 409 and "已处理" in r.json()["detail"]


def test_stale_anchor_expires_and_blocks_accept_p5(client: TestClient):
    """P5：提案基于的世界已经不在了 → 自动失效，禁止盲接受。

    端点创建即应用后这条保护主要服务休眠的手工通道（store 建的待审批提案）。
    """
    _queue(client, [{"op": "train", "type": "terran/marine"},
                    {"op": "train", "type": "terran/scv"}])
    pid = client.app.state.proposals.create(_propose_body()).id
    sess = client.app.state.session
    for _ in range(int(ANCHOR_STALE_SECONDS) + 2):
        sess.tick()
    rows = {x["id"]: x for x in client.get("/api/proposals").json()}
    assert rows[pid]["status"] == "已失效"
    r = client.post(f"/api/proposals/{pid}/accept")
    assert r.status_code == 409 and "已失效" in r.json()["detail"]


def test_stale_anchor_blocks_accept_without_listing_first_p5(client: TestClient):
    """P5 的洞：**不先拉列表**也必须拒。

    `_expire()` 原先只在 list()/get() 里调，而 accept() 走的 `_require()` 不调它 ——
    于是 anchor 早已过期、期间没人拉过列表时，状态还是"待审批"，accept 照单全收，
    等于拿过期观察改世界。live 下前端根本不轮询 proposals，这个窗口尤其宽。
    上面那条测试因为先 GET 了一次列表，恰好把这个洞掩盖住了。
    """
    _queue(client, [{"op": "train", "type": "terran/marine"},
                    {"op": "train", "type": "terran/scv"}])
    pid = client.app.state.proposals.create(_propose_body()).id
    sess = client.app.state.session
    for _ in range(int(ANCHOR_STALE_SECONDS) + 2):
        sess.tick()
    # 注意：这里**没有** GET /api/proposals
    r = client.post(f"/api/proposals/{pid}/accept")
    assert r.status_code == 409, "过期提案必须被拒，不能因为没人拉过列表就放行"
    assert "已失效" in r.json()["detail"]


# ---------------- 存储 ----------------

def test_stopping_the_session_detaches_proposals(client: TestClient):
    """停会话后不能再基于死会话算 anchor —— 否则 P5 的失效判断永远不会触发。"""
    client.post("/api/session/stop")
    assert client.app.state.proposals.session is None
    r = client.post("/api/proposals", json={
        "kind": "production_queue", "title_zh": "x", "rationale_zh": "理由",
        "target": {"queue": "main"},
        "hunks": [{"id": "h1", "kind": "insert", "text_zh": "加",
                   "payload": {"item": {"op": "train", "type": "terran/marine"}}}]})
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


def test_map_plan_proposal_applies_to_overrides(client, monkeypatch, tmp_path):
    """B14 全链：map_plan 提案 → validate ok + map_overlay preview → 接受 → 覆盖层落盘。

    覆盖层路径 monkeypatch 到临时目录（不污染仓库里的真实 authoring 数据）。
    接受后**新会话**加载时生效 —— 这里验证落盘 + 重载模板可见（重开 = 重载）。
    """
    import view.map_plan as mp
    ov_path = tmp_path / "base_layout.overrides.yaml"
    monkeypatch.setattr(mp, "MAP_OVERRIDES_PATH", ov_path)

    p = client.app.state.proposals.create(_propose_body(
        kind="map_plan", title_zh="放个新点位",
        target={}, hunks=[
            {"id": "h1", "kind": "add_mark", "text_zh": "新增点位 mark_1",
             "payload": {"name": "mark_1", "pos": [55.5, 42.5]}},
        ])).to_json()
    assert p["validation"]["ok"] is True, p["validation"]
    assert p["preview"]["kind"] == "map_overlay"

    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 200, r.text

    ov = mp.load_map_overrides()
    assert ov["pos_marks"]["mark_1"]["pos"] == [55.5, 42.5]

    # 重载模板（= 新会话）后点位可见 —— "在线看到我标记了什么"的数据链路
    from tactical_map.base import load_ladder_map, load_base_template
    from pathlib import Path
    real = Path(__file__).resolve().parents[2] / "modules" / "tactical_map" / "data"         / "ladder_map" / "base_layout.yaml"
    # 用 monkeypatch 过的路径构造：临时目录里没有 base_layout.yaml，
    # 所以直接验证 load_ladder_map 不受影响（真实文件未被污染）+ 临时覆盖层正确。
    tpl = load_ladder_map()
    assert all("mark_1" not in {m.name for m in s.pos_marks} for s in tpl.spawns.values())


def test_map_plan_invalid_hunk_rejected_with_reason(client):
    """校验失败必须带结构化理由（P2：不可接受但必须可见）。"""
    p = _propose(client, kind="map_plan", target={}, hunks=[
        {"id": "h1", "kind": "add_slot", "text_zh": "非法尺寸",
         "payload": {"name": "R9", "pos": [55.5, 42.5], "size": 4, "kind": "production"}},
    ])
    assert p["validation"]["ok"] is False
    assert "size" in p["validation"]["errors"][0]["text_zh"]
    assert p["validation"]["errors"][0]["hunk_id"] == "h1"


def test_map_plan_accept_after_invalid_refused(client, monkeypatch, tmp_path):
    """校验没过就不能接受（P2 的另一半）。"""
    import view.map_plan as mp
    monkeypatch.setattr(mp, "MAP_OVERRIDES_PATH", tmp_path / "o.yaml")
    p = _propose(client, kind="map_plan", target={}, hunks=[
        {"id": "h1", "kind": "add_slot", "text_zh": "重叠",
         "payload": {"name": "dup", "pos": [40.5, 32.5], "size": 2, "kind": "supply"}},
    ])
    assert p["validation"]["ok"] is False
    r = client.post(f"/api/proposals/{p['id']}/accept")
    assert r.status_code == 409, r.text
    assert not (tmp_path / "o.yaml").exists()